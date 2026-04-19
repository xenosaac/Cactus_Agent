from __future__ import annotations

import asyncio

import pytest

from voice_agent.events import AgentEvent, EventBus, EventType
from voice_agent.main import voice_loop
from voice_agent.voice.stt import Transcript
from voice_agent.voice.vad import SpeechSegment
from voice_agent.voice.wake_filter import WakeFilter


def _segment() -> SpeechSegment:
    return SpeechSegment(
        pcm_int16=b"\x00\x00" * 160,
        start_sample=0,
        end_sample=160,
        sample_rate=16_000,
        segment_id=1,
        duration_ms=10.0,
        rms=0.0,
        peak=0.0,
        forced=False,
        queue_depth=0,
        ring_bytes_before=320,
        ring_bytes_after=0,
        first16="00000000",
    )


class FakeVAD:
    async def segments(self):
        yield _segment()

    async def push_to_talk_segments(self):
        yield _segment()


class FakeWhisper:
    def __init__(self, text: str = "yes") -> None:
        self.text = text
        self.emit_events: list[bool] = []

    async def transcribe(
        self,
        _pcm_int16: bytes,
        *,
        emit_events: bool = True,
    ) -> Transcript:
        self.emit_events.append(emit_events)
        return Transcript(text=self.text)


class FakeOrchestrator:
    def __init__(self, awaiting: bool = True) -> None:
        self.awaiting = awaiting
        self.intents: list[str] = []
        self.turns: list[str] = []

    def awaiting_confirm(self) -> bool:
        return self.awaiting

    def deliver_intent(self, intent: str) -> None:
        self.intents.append(intent)

    async def run_turn(self, command: str) -> None:
        self.turns.append(command)


def _recording_bus(events: list[AgentEvent]) -> EventBus:
    test_bus = EventBus()

    async def _record(event: AgentEvent) -> None:
        events.append(event)

    test_bus.subscribe(_record)
    return test_bus


@pytest.mark.asyncio
async def test_voice_loop_routes_confirmation_without_wake(monkeypatch) -> None:
    orchestrator = FakeOrchestrator()
    whisper = FakeWhisper("yes")
    events: list[AgentEvent] = []
    test_bus = _recording_bus(events)
    monkeypatch.setattr("voice_agent.main.bus", test_bus)

    await voice_loop(
        whisper,
        WakeFilter(("hey cactus",)),
        orchestrator,
        FakeVAD(),
    )

    assert orchestrator.intents == ["confirm"]
    assert orchestrator.turns == []
    assert whisper.emit_events == [True]
    assert EventType.STT_FINAL in [event.type for event in events]


@pytest.mark.asyncio
async def test_voice_loop_keeps_idle_ambient_transcripts_internal(monkeypatch) -> None:
    orchestrator = FakeOrchestrator(awaiting=False)
    whisper = FakeWhisper("turn on discord")
    events: list[AgentEvent] = []
    test_bus = _recording_bus(events)
    monkeypatch.setattr("voice_agent.main.bus", test_bus)

    await voice_loop(
        whisper,
        WakeFilter(("hey cactus",)),
        orchestrator,
        FakeVAD(),
    )

    assert whisper.emit_events == [False]
    assert orchestrator.intents == []
    assert orchestrator.turns == []
    assert EventType.STT_FINAL not in [event.type for event in events]
    assert EventType.WAKE_DETECTED not in [event.type for event in events]


@pytest.mark.asyncio
async def test_voice_loop_push_to_talk_dispatches_without_wake(monkeypatch) -> None:
    orchestrator = FakeOrchestrator(awaiting=False)
    whisper = FakeWhisper("open calculator")
    events: list[AgentEvent] = []
    test_bus = _recording_bus(events)
    monkeypatch.setattr("voice_agent.main.bus", test_bus)

    await voice_loop(
        whisper,
        WakeFilter(("hey cactus",)),
        orchestrator,
        FakeVAD(),
        push_to_talk_only=True,
    )
    await asyncio.sleep(0)

    assert whisper.emit_events == [True]
    assert orchestrator.turns == ["open calculator"]
    assert EventType.STT_FINAL in [event.type for event in events]
    assert EventType.WAKE_DETECTED not in [event.type for event in events]
