"""Opt-in voice pipeline diagnostics.

When VA_VOICE_DEBUG=1 is set, each emitted VAD segment can be persisted as a
WAV file plus a JSONL metadata row. This is intentionally off by default
because it records user speech.
"""
from __future__ import annotations

import json
import logging
import wave
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from voice_agent.voice.command_assembler import CommandDecision
from voice_agent.voice.stt import Transcript
from voice_agent.voice.vad import SpeechSegment

log = logging.getLogger(__name__)


class VoiceDebugRecorder:
    def __init__(self, enabled: bool, output_dir: Path) -> None:
        self.enabled = enabled
        self.output_dir = output_dir
        self._jsonl_path = output_dir / "segments.jsonl"
        if self.enabled:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            log.info("voice debug capture enabled dir=%s", self.output_dir)

    def record(
        self,
        segment: SpeechSegment,
        transcript: Transcript,
        decision: CommandDecision | None = None,
        *,
        mode: str = "full",
    ) -> None:
        if not self.enabled:
            return

        wav_name = f"segment_{segment.segment_id:05d}.wav"
        wav_path = self.output_dir / wav_name
        self._write_wav(wav_path, segment)

        payload: dict[str, Any] = {
            "mode": mode,
            "segment_id": segment.segment_id,
            "wav": wav_name,
            "sample_rate": segment.sample_rate,
            "pcm_bytes": len(segment.pcm_int16),
            "start_sample": segment.start_sample,
            "end_sample": segment.end_sample,
            "duration_ms": segment.duration_ms,
            "rms": segment.rms,
            "peak": segment.peak,
            "forced": segment.forced,
            "queue_depth": segment.queue_depth,
            "ring_bytes_before": segment.ring_bytes_before,
            "ring_bytes_after": segment.ring_bytes_after,
            "first16": segment.first16,
            "whisper_text": transcript.text,
            "whisper_raw": transcript.raw,
            "whisper_raw_stream": transcript.raw_stream,
            "whisper_raw_batch": transcript.raw_batch,
            "whisper_duration_ms": transcript.duration_ms,
            "whisper_queue_depth": transcript.queue_depth,
            "dispatch_decision": self._decision_payload(decision),
        }
        with self._jsonl_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")

    @staticmethod
    def _write_wav(path: Path, segment: SpeechSegment) -> None:
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(segment.sample_rate)
            wf.writeframes(segment.pcm_int16)

    @staticmethod
    def _decision_payload(decision: CommandDecision | None) -> dict[str, Any] | None:
        if decision is None:
            return None
        if is_dataclass(decision):
            return asdict(decision)
        return {"repr": repr(decision)}
