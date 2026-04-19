from __future__ import annotations

import asyncio
import json
import wave

from voice_agent.voice.command_assembler import CommandDecision
from voice_agent.voice.debug_recorder import VoiceDebugRecorder
from voice_agent.voice.stt import Transcript
from voice_agent.voice.vad import SpeechSegment, VADListener


def _segment() -> SpeechSegment:
    return SpeechSegment(
        pcm_int16=b"\x00\x00\x01\x00" * 80,
        start_sample=0,
        end_sample=160,
        sample_rate=16_000,
        segment_id=7,
        duration_ms=10.0,
        rms=0.01,
        peak=0.1,
        forced=True,
        queue_depth=3,
        ring_bytes_before=320,
        ring_bytes_after=0,
        first16="00000100",
    )


def test_vad_flush_clears_queue_and_ring() -> None:
    vad = VADListener.__new__(VADListener)
    vad._queue = asyncio.Queue()
    vad._queue.put_nowait(b"abcd")
    vad._queue.put_nowait(b"ef")
    vad._ring = bytearray(b"ring")

    result = vad.flush("test")

    assert result == {
        "reason": "test",
        "dropped_frames": 2,
        "dropped_bytes": 6,
        "ring_bytes": 4,
    }
    assert vad._queue.empty()
    assert vad._ring == bytearray()


def test_vad_energy_gate_uses_configurable_rms_floor() -> None:
    vad = VADListener.__new__(VADListener)
    vad._min_segment_rms = 0.030

    assert vad._segment_energy_ok(0.031) is True
    assert vad._segment_energy_ok(0.027) is False


def test_debug_recorder_writes_wav_and_jsonl(tmp_path) -> None:
    recorder = VoiceDebugRecorder(enabled=True, output_dir=tmp_path)
    transcript = Transcript(
        text="Turn on the calculator",
        raw='{"success":true,"confirmed":"Turn on the calculator"}',
        duration_ms=12.5,
        queue_depth=1,
    )
    decision = CommandDecision(
        kind="dispatch",
        command="Turn on the calculator",
        reason="complete_command",
    )

    recorder.record(_segment(), transcript, decision, mode="voice_debug_only")

    wav_path = tmp_path / "segment_00007.wav"
    with wave.open(str(wav_path), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getframerate() == 16_000
        assert wf.getnframes() == 160

    rows = (tmp_path / "segments.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1
    payload = json.loads(rows[0])
    assert payload["segment_id"] == 7
    assert payload["forced"] is True
    assert payload["whisper_text"] == "Turn on the calculator"
    assert payload["dispatch_decision"]["kind"] == "dispatch"
