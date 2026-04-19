"""Speech-to-text via Cactus Whisper (batch mode, serialized).

Root cause of the earlier "first call works, rest return empty" bug: we were
running each transcription on `asyncio.to_thread` with a 5 s `wait_for`.
When `wait_for` timed out, it cancelled the asyncio task but the underlying
Python thread kept running inside the Cactus FFI (C code can't be cancelled
from Python). The *next* segment spun up a fresh thread; now two threads
were hitting the same non-thread-safe `cactus_model_t` whisper handle and
the handle wedged. First call succeeded by luck (4.5 s < 5 s); from there
everything was racing.

Fix:
  1. ONE dedicated single-worker executor thread for all whisper calls —
     concurrent access is impossible regardless of asyncio cancellation.
  2. No hard timeout — if a segment is slow, the next queues behind it.
  3. Batch `cactus_transcribe` only.

Options JSON is `{"language":"en","task":"transcribe"}`. Omitting the
language hint makes Whisper hallucinate a dots-only "no speech" decode on
short clips (confirmed by Relay's VoiceEngine.swift:51).

Every action is logged — pcm-size, first 16 bytes hex, raw FFI response
preview, executor queue depth, duration — so the post-mortem log shows
exactly what went through the pipe.
"""
from __future__ import annotations

import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TYPE_CHECKING

from voice_agent.events import AgentEvent, EventBus, EventType

if TYPE_CHECKING:
    from voice_agent.config import Settings

log = logging.getLogger(__name__)

_OPTIONS_JSON = '{"language":"en","task":"transcribe"}'


@dataclass
class Transcript:
    text: str
    confidence: float | None = None
    language: str | None = None
    raw: str = ""
    raw_stream: str = ""
    raw_batch: str = ""
    duration_ms: float = 0.0
    queue_depth: int = 0


class StreamingWhisper:
    """Name kept for call-site compatibility; implementation is batch-mode
    on a single serialized worker thread."""

    def __init__(
        self,
        whisper_handle: int,
        settings: "Settings",
        bus: EventBus | None = None,
    ) -> None:
        from cactus.python.src.cactus import (
            cactus_stream_transcribe_process,
            cactus_stream_transcribe_start,
            cactus_stream_transcribe_stop,
            cactus_transcribe,
        )
        self._transcribe = cactus_transcribe
        self._stream_start = cactus_stream_transcribe_start
        self._stream_process = cactus_stream_transcribe_process
        self._stream_stop = cactus_stream_transcribe_stop
        self._handle = whisper_handle
        self._settings = settings
        self._bus = bus
        # One worker, lifetime = the StreamingWhisper instance.
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="whisper",
        )
        self._pending = 0

    async def transcribe(
        self,
        pcm_int16: bytes,
        *,
        emit_events: bool = True,
    ) -> Transcript:
        import time as _time
        _t0 = _time.perf_counter()
        self._pending += 1
        queue_depth = self._pending
        first16 = pcm_int16[:16].hex()
        log.info(
            "whisper.transcribe START bytes=%d queue_depth=%d emit_events=%s "
            "first16=%s",
            len(pcm_int16), queue_depth, emit_events, first16,
        )
        if emit_events and self._bus is not None:
            try:
                await self._bus.publish(AgentEvent(type=EventType.WHISPER_START))
            except Exception:  # noqa: BLE001
                pass

        loop = asyncio.get_running_loop()
        # Primary path: streaming. On this Cactus build the batch
        # `cactus_transcribe` deterministically returns "" with high
        # confidence (the "no speech" hallucination Relay documented in
        # VoiceEngine.swift:60-64). Streaming actually produces text.
        text = ""
        raw_stream = ""
        raw_batch = ""
        try:
            raw_stream = await loop.run_in_executor(
                self._executor, self._run_streaming, pcm_int16
            )
            text = self._extract_text(raw_stream).strip()
        except Exception as exc:  # noqa: BLE001
            log.warning("whisper streaming raised: %s", exc)

        # Fallback: batch transcribe. Usually empty on this build, but kept
        # so we have a last-chance shot if streaming somehow fails.
        if not text:
            try:
                raw_batch = await loop.run_in_executor(
                    self._executor, self._run_batch, pcm_int16
                )
                text = self._extract_text(raw_batch).strip()
            except Exception as exc:  # noqa: BLE001
                log.warning("whisper batch raised: %s", exc)

        self._pending -= 1
        duration_ms = (_time.perf_counter() - _t0) * 1000
        log.info(
            "whisper.transcribe END duration_ms=%.1f text=%r",
            duration_ms, text[:100],
        )
        if not text and emit_events and self._bus is not None:
            try:
                await self._bus.publish(AgentEvent(
                    type=EventType.STT_EMPTY,
                    summary="didn't catch that",
                ))
            except Exception:  # noqa: BLE001
                pass
        return Transcript(
            text=text,
            raw=raw_stream or raw_batch,
            raw_stream=raw_stream,
            raw_batch=raw_batch,
            duration_ms=duration_ms,
            queue_depth=queue_depth,
        )

    # ── native calls (run on the dedicated worker thread) ────────────────
    def _run_streaming(self, pcm_int16: bytes) -> str:
        """Stream the full PCM through `cactus_stream_transcribe_*` as a
        single chunk and return the final combined transcript.

        Earlier we split into 500 ms sub-chunks; for an 11 s clip that meant
        23 sequential FFI calls and ~21 s wall-clock. One big chunk gets the
        decoder to process everything in one pass and is much faster. We
        don't need mid-stream partials — the orchestrator only consumes the
        final transcript via `stt_final`."""
        import time as _time
        t0 = _time.perf_counter()
        log.info(
            "whisper stream START pcm_bytes=%d options=%s",
            len(pcm_int16), _OPTIONS_JSON,
        )
        stream = self._stream_start(self._handle, _OPTIONS_JSON)
        try:
            if pcm_int16:
                self._stream_process(stream, list(pcm_int16))
            raw = self._stream_stop(stream)
        except Exception:
            try:
                self._stream_stop(stream)
            except Exception:  # noqa: BLE001
                pass
            raise
        raw_str = str(raw) if raw is not None else ""
        log.info(
            "whisper stream END raw_chars=%d duration_ms=%.1f preview=%r",
            len(raw_str), (_time.perf_counter() - t0) * 1000,
            raw_str[:300].replace("\n", " "),
        )
        return raw_str

    def _run_batch(self, pcm_int16: bytes) -> str:
        # NOTE: deliberately no `cactus_reset` — on this Cactus build it
        # destroys the Whisper encoder state and every subsequent transcribe
        # returns empty. The single-worker executor is the concurrency guard.
        import time as _time
        t0 = _time.perf_counter()
        log.info(
            "whisper ffi call pcm_bytes=%d first16=%s options=%s",
            len(pcm_int16), pcm_int16[:16].hex(), _OPTIONS_JSON,
        )
        raw = self._transcribe(
            self._handle,
            None,            # audio_path → use pcm
            None,            # prompt → default
            _OPTIONS_JSON,
            None,            # no streaming callback
            list(pcm_int16),
        )
        raw_str = str(raw) if raw is not None else ""
        log.info(
            "whisper ffi return raw_chars=%d duration_ms=%.1f preview=%r",
            len(raw_str), (_time.perf_counter() - t0) * 1000,
            raw_str[:300].replace("\n", " "),
        )
        return raw_str

    # ── response parsing ─────────────────────────────────────────────────
    @staticmethod
    def _extract_text(raw: object) -> str:
        """Cactus returns either a plain string or a JSON blob
        (`{response|text|confirmed: ...}`). Handle both."""
        if raw is None:
            return ""
        s = raw if isinstance(raw, str) else str(raw)
        try:
            parsed = json.loads(s)
        except (json.JSONDecodeError, TypeError):
            return s
        if isinstance(parsed, dict):
            return (
                parsed.get("response")
                or parsed.get("text")
                or parsed.get("confirmed")
                or parsed.get("confirmed_local")
                or ""
            )
        return s
