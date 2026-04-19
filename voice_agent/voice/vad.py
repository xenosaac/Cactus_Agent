"""Always-live voice activity detection via sounddevice + Cactus Silero VAD.

Heavily instrumented: at startup we log the actual input device the OS
picked (so we can spot a "default mic is HDMI / AirPods off" problem), and
during capture we emit a per-second audio-level rollup (RMS, peak, frame
count, queue depth). The rollup also goes on the EventBus as AUDIO_LEVEL so
the UI can render a live meter — if that bar moves the mic is healthy even
when Whisper returns empty.
"""
from __future__ import annotations

import asyncio
import json
import logging
import struct
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from voice_agent.events import AgentEvent, EventBus, EventType

log = logging.getLogger(__name__)


@dataclass
class SpeechSegment:
    pcm_int16: bytes
    start_sample: int
    end_sample: int
    sample_rate: int
    segment_id: int
    duration_ms: float
    rms: float
    peak: float
    forced: bool
    queue_depth: int
    ring_bytes_before: int
    ring_bytes_after: int
    first16: str


def _rms_peak_int16(pcm_bytes: bytes) -> tuple[float, float]:
    """Return (rms, peak) both normalized to [0, 1.0]. Zero-safe."""
    if not pcm_bytes:
        return 0.0, 0.0
    n = len(pcm_bytes) // 2
    if n == 0:
        return 0.0, 0.0
    samples = struct.unpack(f"<{n}h", pcm_bytes[: n * 2])
    peak_raw = max(abs(s) for s in samples)
    # mean-square in pure python — acceptable for ~480-sample chunks.
    mean_sq = sum(s * s for s in samples) / n
    rms_raw = mean_sq ** 0.5
    return rms_raw / 32768.0, peak_raw / 32768.0


class VADListener:
    """Captures mic audio continuously; yields bounded speech regions.

    Caller model:
        async with VADListener(vad_handle, ..., bus=bus) as vad:
            async for seg in vad.segments():
                ... consume seg ...
    """

    def __init__(
        self,
        vad_handle: int,
        sample_rate: int = 16_000,
        frame_ms: int = 30,
        silence_threshold_ms: int = 800,
        min_speech_ms: int = 250,
        max_speech_ms: int = 12_000,  # cap segment length — Whisper CPU is slow
        threshold: float = 0.65,
        min_segment_rms: float = 0.025,
        push_to_talk_capture: bool = False,
        ring_buffer_seconds: float = 12.0,
        bus: EventBus | None = None,
    ) -> None:
        # Lazy-import so tests can run without Cactus libs.
        from cactus.python.src.cactus import cactus_vad
        self._vad = cactus_vad
        self._handle = vad_handle
        self._sr = sample_rate
        self._frame_samples = sample_rate * frame_ms // 1000
        self._silence_ms = silence_threshold_ms
        self._min_speech_ms = min_speech_ms
        self._max_speech_ms = max_speech_ms
        self._threshold = threshold
        self._min_segment_rms = min_segment_rms
        self._push_to_talk_capture = push_to_talk_capture
        self._ring_cap_bytes = int(ring_buffer_seconds * sample_rate * 2)  # int16
        self._ring = bytearray()
        self._stream: Any = None  # sounddevice.RawInputStream; untyped lib
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=200)
        self._running = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._bus = bus
        # Rolling audio-level stats (callback thread → stats thread via atomics).
        self._last_rollup_t = time.perf_counter()
        self._rollup_sum_sq = 0.0
        self._rollup_samples = 0
        self._rollup_peak = 0.0
        self._rollup_frame_count = 0
        # Total counters (for startup smoke).
        self._total_callbacks = 0
        self._total_bytes = 0
        self._segment_id = 0
        # Push-to-talk capture state. Normal app voice uses this path so the
        # mic is ignored unless Space is held.
        self._ptt_active = False
        self._ptt_buffer = bytearray()
        self._ptt_start_sample = 0
        self._ptt_ready: asyncio.Queue[SpeechSegment] = asyncio.Queue()

    async def __aenter__(self) -> "VADListener":
        import sounddevice as sd
        self._loop = asyncio.get_running_loop()

        # Dump the actual input device so we can catch a wrong-mic default.
        try:
            default_in_idx = sd.default.device[0]
            info = sd.query_devices(default_in_idx)
            log.info(
                "sounddevice input device: idx=%s name=%r channels_in=%s "
                "default_sr=%s chosen_sr=%s frame_samples=%s",
                default_in_idx, info.get("name"),
                info.get("max_input_channels"),
                info.get("default_samplerate"),
                self._sr, self._frame_samples,
            )
            if (info.get("max_input_channels") or 0) < 1:
                log.error(
                    "DEFAULT INPUT DEVICE HAS 0 CHANNELS — mic is dead. "
                    "Check System Settings → Sound → Input, or pick a "
                    "device with `sd.default.device = <idx>`."
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("couldn't query sounddevice defaults: %s", exc)

        def _cb(indata: Any, frames: int, time_info: Any, status: Any) -> None:
            if status:
                log.warning("VAD mic status: %s", status)
            data = bytes(indata)
            self._total_callbacks += 1
            self._total_bytes += len(data)

            if self._push_to_talk_capture and not self._ptt_active:
                # Logical mute: while Space is up we do not retain frames,
                # run audio levels, or feed any downstream queue.
                return

            # Cheap per-callback stats; full rollup computed on a timer below.
            rms_norm, peak_norm = _rms_peak_int16(data)
            self._rollup_sum_sq += (rms_norm * rms_norm) * (len(data) // 2)
            self._rollup_samples += len(data) // 2
            if peak_norm > self._rollup_peak:
                self._rollup_peak = peak_norm
            self._rollup_frame_count += 1
            # Enqueue frame. sounddevice callback runs on portaudio thread;
            # post to loop safely.
            try:
                self._queue.put_nowait(data)
            except asyncio.QueueFull:
                # Drop-oldest semantics (never block audio thread).
                try:
                    self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass

        self._stream = sd.RawInputStream(
            samplerate=self._sr,
            blocksize=self._frame_samples,
            dtype="int16",
            channels=1,
            callback=_cb,
        )
        self._stream.start()
        self._running = True
        log.info(
            "VAD mic stream started sr=%d frame_ms=%d silence_ms=%d "
            "min_speech_ms=%d threshold=%.2f min_segment_rms=%.4f ptt_capture=%s",
            self._sr, self._frame_samples * 1000 // self._sr,
            self._silence_ms, self._min_speech_ms,
            self._threshold, self._min_segment_rms, self._push_to_talk_capture,
        )
        # Start the per-second audio-level rollup task.
        assert self._loop is not None
        self._rollup_task = self._loop.create_task(self._run_rollup())
        return self

    async def __aexit__(self, *exc: object) -> None:
        self._running = False
        try:
            if hasattr(self, "_rollup_task") and not self._rollup_task.done():
                self._rollup_task.cancel()
        except Exception:  # noqa: BLE001
            pass
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as e:  # noqa: BLE001
                log.warning("VAD stream close error: %s", e)
            self._stream = None

    def flush(self, reason: str) -> dict[str, int | str]:
        """Drop queued frames and buffered audio.

        VAD opens the mic before Cactus/MCP warmup so macOS permissions are
        prompted early. That means stale startup speech can sit in the queue
        for several seconds. Flush once the runtime is ready so the first
        command starts from fresh audio.
        """
        dropped_frames = 0
        dropped_bytes = 0
        while True:
            try:
                frame = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            dropped_frames += 1
            dropped_bytes += len(frame)
        ring_bytes = len(self._ring)
        self._ring.clear()
        log.info(
            "vad flush reason=%s dropped_frames=%d dropped_bytes=%d ring_bytes=%d",
            reason, dropped_frames, dropped_bytes, ring_bytes,
        )
        return {
            "reason": reason,
            "dropped_frames": dropped_frames,
            "dropped_bytes": dropped_bytes,
            "ring_bytes": ring_bytes,
        }

    def _segment_energy_ok(self, rms: float) -> bool:
        return rms >= self._min_segment_rms

    def _reset_rollup(self) -> None:
        self._rollup_sum_sq = 0.0
        self._rollup_samples = 0
        self._rollup_peak = 0.0
        self._rollup_frame_count = 0

    def start_push_to_talk(self, reason: str = "push_to_talk") -> dict[str, int | str]:
        """Begin collecting raw mic frames for a push-to-talk utterance."""
        dropped = self.flush(f"{reason}_start")
        self._ptt_buffer.clear()
        self._ptt_start_sample = self._total_bytes // 2
        self._reset_rollup()
        self._ptt_active = True
        log.info(
            "ptt start reason=%s start_sample=%d",
            reason, self._ptt_start_sample,
        )
        return dropped

    def stop_push_to_talk(self, reason: str = "push_to_talk") -> SpeechSegment | None:
        """Stop collecting and enqueue the captured utterance for STT."""
        if not self._ptt_active:
            log.info("ptt stop ignored reason=%s active=false", reason)
            return None

        self._ptt_active = False
        drained_frames = 0
        drained_bytes = 0
        while True:
            try:
                frame = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            self._ptt_buffer.extend(frame)
            drained_frames += 1
            drained_bytes += len(frame)

        pcm = bytes(self._ptt_buffer)
        self._ptt_buffer.clear()
        self._reset_rollup()
        start_sample = self._ptt_start_sample
        end_sample = start_sample + len(pcm) // 2
        dur_ms = (len(pcm) // 2) * 1000 / self._sr if pcm else 0.0
        seg_rms, seg_peak = _rms_peak_int16(pcm)
        first16 = pcm[:16].hex()

        if dur_ms < self._min_speech_ms:
            log.info(
                "ptt rejected too_short reason=%s dur_ms=%.0f min_ms=%d "
                "drained_frames=%d drained_bytes=%d",
                reason, dur_ms, self._min_speech_ms,
                drained_frames, drained_bytes,
            )
            self._publish_stt_empty("hold Space and speak")
            return None

        if not self._segment_energy_ok(seg_rms):
            log.info(
                "ptt rejected low_energy reason=%s dur_ms=%.0f rms=%.4f "
                "peak=%.4f min_rms=%.4f drained_frames=%d drained_bytes=%d",
                reason, dur_ms, seg_rms, seg_peak, self._min_segment_rms,
                drained_frames, drained_bytes,
            )
            self._publish_stt_empty("too quiet")
            return None

        self._segment_id += 1
        segment = SpeechSegment(
            pcm_int16=pcm,
            start_sample=start_sample,
            end_sample=end_sample,
            sample_rate=self._sr,
            segment_id=self._segment_id,
            duration_ms=dur_ms,
            rms=seg_rms,
            peak=seg_peak,
            forced=False,
            queue_depth=self._queue.qsize(),
            ring_bytes_before=0,
            ring_bytes_after=0,
            first16=first16,
        )
        self._ptt_ready.put_nowait(segment)
        log.info(
            "ptt segment queued id=%d dur_ms=%.0f bytes=%d rms=%.4f peak=%.4f "
            "drained_frames=%d drained_bytes=%d first16=%s",
            segment.segment_id, segment.duration_ms, len(pcm),
            seg_rms, seg_peak, drained_frames, drained_bytes, first16,
        )
        return segment

    def _publish_stt_empty(self, summary: str) -> None:
        if self._bus is None or self._loop is None:
            return
        self._loop.create_task(self._bus.publish(AgentEvent(
            type=EventType.STT_EMPTY,
            summary=summary,
        )))

    async def push_to_talk_segments(self) -> AsyncIterator[SpeechSegment]:
        """Yield utterances cut by Space-down/Space-up boundaries."""
        while self._running:
            try:
                segment = self._ptt_ready.get_nowait()
            except asyncio.QueueEmpty:
                pass
            else:
                yield segment
                continue

            try:
                frame = await asyncio.wait_for(self._queue.get(), timeout=0.05)
            except asyncio.TimeoutError:
                continue
            if self._ptt_active:
                self._ptt_buffer.extend(frame)

    # ── per-second audio-level rollup ─────────────────────────────────────
    async def _run_rollup(self) -> None:
        """Every ~1 s, snapshot the callback-accumulated stats, log them,
        publish an AUDIO_LEVEL event, and reset the accumulator."""
        while self._running:
            try:
                await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                return
            now = time.perf_counter()
            dt = now - self._last_rollup_t
            self._last_rollup_t = now
            if self._rollup_samples == 0:
                rms = 0.0
            else:
                rms = (self._rollup_sum_sq / self._rollup_samples) ** 0.5
            peak = self._rollup_peak
            frame_count = self._rollup_frame_count
            # Reset.
            self._rollup_sum_sq = 0.0
            self._rollup_samples = 0
            self._rollup_peak = 0.0
            self._rollup_frame_count = 0
            log.info(
                "audio_level rms=%.4f peak=%.4f frames=%d interval_s=%.2f "
                "qdepth=%d total_cb=%d total_bytes=%d",
                rms, peak, frame_count, dt,
                self._queue.qsize(),
                self._total_callbacks, self._total_bytes,
            )
            if self._bus is not None:
                try:
                    await self._bus.publish(AgentEvent(
                        type=EventType.AUDIO_LEVEL,
                        rms=rms, peak=peak,
                    ))
                except Exception as exc:  # noqa: BLE001
                    log.warning("audio_level publish failed: %s", exc)

    async def segments(self) -> AsyncIterator[SpeechSegment]:
        options_json = json.dumps({
            "threshold": self._threshold,
            "min_speech_duration_ms": self._min_speech_ms,
            "min_silence_duration_ms": self._silence_ms,
            "speech_pad_ms": 200,
        })
        vad_interval_bytes = (self._sr // 5) * 2
        since_last_vad = 0
        invoke_count = 0

        while self._running:
            try:
                frame = await asyncio.wait_for(self._queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            self._ring.extend(frame)
            since_last_vad += len(frame)

            if len(self._ring) > self._ring_cap_bytes:
                excess = len(self._ring) - self._ring_cap_bytes
                del self._ring[:excess]

            if since_last_vad < vad_interval_bytes:
                continue
            since_last_vad = 0

            invoke_count += 1
            vad_t0 = time.perf_counter()
            try:
                vad_json = await asyncio.to_thread(
                    self._vad, self._handle, None, options_json, list(self._ring)
                )
                vad = json.loads(vad_json)
            except Exception as exc:  # noqa: BLE001
                log.exception("cactus_vad failed: %s", exc)
                continue
            log.debug(
                "vad invoke #%d ring_bytes=%d duration_ms=%.1f raw=%s",
                invoke_count, len(self._ring),
                (time.perf_counter() - vad_t0) * 1000,
                (vad_json[:120] + "…") if len(vad_json) > 120 else vad_json,
            )

            segs: list[dict] = vad.get("segments", []) or []
            completed = [s for s in segs if s["end"] * 2 < len(self._ring)]
            forced = False

            # Force-emit a segment when the current one is dragging on past
            # the configured max — Cactus Whisper-Small on CPU is slow
            # enough that >6 s clips feel stuck.
            if not completed and segs:
                active = segs[-1]
                active_len_ms = (active["end"] - active["start"]) * 1000 // self._sr
                if active_len_ms >= self._max_speech_ms:
                    cut_end_samples = (
                        active["start"] + self._max_speech_ms * self._sr // 1000
                    )
                    completed = [{"start": active["start"], "end": cut_end_samples}]
                    forced = True
                    log.info(
                        "vad force-emit long-speech segment max_ms=%d",
                        self._max_speech_ms,
                    )

            if not completed:
                continue

            last = completed[-1]
            start_byte = last["start"] * 2
            end_byte = min(last["end"] * 2, len(self._ring))
            ring_bytes_before = len(self._ring)
            pcm = bytes(self._ring[start_byte:end_byte])
            del self._ring[:end_byte]

            seg_rms, seg_peak = _rms_peak_int16(pcm)
            first16 = pcm[:16].hex()
            dur_ms = (last["end"] - last["start"]) * 1000 / self._sr
            if not self._segment_energy_ok(seg_rms):
                log.info(
                    "vad segment rejected low_energy start=%d end=%d dur_ms=%.0f "
                    "bytes=%d rms=%.4f peak=%.4f min_rms=%.4f forced=%s qdepth=%d "
                    "first16=%s",
                    last["start"], last["end"], dur_ms, len(pcm),
                    seg_rms, seg_peak, self._min_segment_rms, forced,
                    self._queue.qsize(), first16,
                )
                continue
            self._segment_id += 1
            log.info(
                "vad segment detected id=%d start=%d end=%d dur_ms=%.0f bytes=%d "
                "rms=%.4f peak=%.4f forced=%s qdepth=%d first16=%s",
                self._segment_id, last["start"], last["end"], dur_ms, len(pcm),
                seg_rms, seg_peak, forced, self._queue.qsize(), first16,
            )
            yield SpeechSegment(
                pcm_int16=pcm,
                start_sample=last["start"],
                end_sample=last["end"],
                sample_rate=self._sr,
                segment_id=self._segment_id,
                duration_ms=dur_ms,
                rms=seg_rms,
                peak=seg_peak,
                forced=forced,
                queue_depth=self._queue.qsize(),
                ring_bytes_before=ring_bytes_before,
                ring_bytes_after=len(self._ring),
                first16=first16,
            )
