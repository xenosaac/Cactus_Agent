from __future__ import annotations

from voice_agent.voice.command_assembler import CommandAssembler
from voice_agent.voice.wake_filter import WakeFilter


def _assembler() -> CommandAssembler:
    return CommandAssembler(WakeFilter(("hey cactus", "okay cactus", "cactus,")))


def test_forced_partial_wake_does_not_dispatch_then_clean_command_dispatches() -> None:
    asm = _assembler()

    first = asm.process("Hey cactus! Hey cactus! Turn on the c-", forced=True)
    assert first.kind == "armed"
    assert first.wake_detected is True
    assert first.armed_after is True

    second = asm.process("Turn on the calculator!", forced=False)
    assert second.kind == "dispatch"
    assert second.command == "Turn on the calculator!"
    assert second.armed_after is False


def test_wake_phrase_repetitions_are_stripped() -> None:
    decision = _assembler().process(
        "Hey cactus! Okay cactus, cactus, open Safari",
        forced=False,
    )
    assert decision.kind == "dispatch"
    assert decision.command == "open Safari"


def test_ambient_speech_without_wake_is_ignored() -> None:
    decision = _assembler().process("turn on the calculator", forced=False)
    assert decision.kind == "ignore"
    assert decision.reason == "ambient_no_wake"


def test_after_dispatch_future_non_wake_utterance_is_ignored() -> None:
    asm = _assembler()
    first = asm.process("Hey cactus, open Calculator", forced=False)
    assert first.kind == "dispatch"

    second = asm.process("open Safari", forced=False)
    assert second.kind == "ignore"
    assert second.reason == "ambient_no_wake"


def test_external_wake_arms_next_complete_transcript_once() -> None:
    asm = _assembler()
    armed = asm.arm()
    assert armed.kind == "armed"

    command = asm.process("open Calculator", forced=False)
    assert command.kind == "dispatch"
    assert command.command == "open Calculator"

    ambient = asm.process("open Safari", forced=False)
    assert ambient.kind == "ignore"


def test_push_to_talk_dispatches_without_wake_and_strips_habitual_wake() -> None:
    decision = _assembler().process_direct(
        "Hey cactus, open Calculator",
        forced=False,
    )

    assert decision.kind == "dispatch"
    assert decision.command == "open Calculator"
    assert decision.wake_detected is True


def test_push_to_talk_incomplete_does_not_stay_armed() -> None:
    asm = _assembler()
    first = asm.process_direct("turn on the c-", forced=False)
    assert first.kind == "ignore"
    assert first.reason == "push_to_talk_incomplete"
    assert asm.armed is False

    second = asm.process("open Safari", forced=False)
    assert second.kind == "ignore"
