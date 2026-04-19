"""Wake-command assembly for finalized STT transcripts.

The voice loop receives segment-level transcripts, not guaranteed complete
commands. This module owns the small state machine that decides whether a
transcript should arm the agent, be ignored, or dispatch exactly one command.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from voice_agent.voice.wake_filter import WakeFilter

log = logging.getLogger(__name__)

DecisionKind = Literal["ignore", "armed", "dispatch"]


@dataclass(frozen=True)
class CommandDecision:
    kind: DecisionKind
    command: str = ""
    reason: str = ""
    wake_detected: bool = False
    armed_after: bool = False


class CommandAssembler:
    """Turn noisy STT segments into one complete command per wake phrase."""

    def __init__(
        self,
        wake_filter: WakeFilter,
        min_command_chars: int = 4,
    ) -> None:
        self._wake_filter = wake_filter
        self._min_command_chars = min_command_chars
        self._armed = False

    @property
    def armed(self) -> bool:
        return self._armed

    def arm(self, reason: str = "external_wake") -> CommandDecision:
        self._armed = True
        return CommandDecision(
            kind="armed",
            reason=reason,
            wake_detected=True,
            armed_after=True,
        )

    def reset(self) -> None:
        self._armed = False

    def process(self, text: str, *, forced: bool = False) -> CommandDecision:
        """Classify one final STT transcript.

        Forced VAD cuts and obvious trailing fragments are treated as
        incomplete. If a wake phrase was detected, the assembler stays armed
        and waits for a later clean transcript instead of dispatching the
        fragment.
        """
        clean = " ".join((text or "").strip().split())
        if not clean:
            return CommandDecision(
                kind="ignore",
                reason="empty_transcript",
                armed_after=self._armed,
            )

        matched, command = self._strip_repeated_wake(clean)
        if self._armed:
            candidate = command if matched else clean
            return self._candidate_decision(
                candidate,
                forced=forced,
                wake_detected=matched,
                armed_reason="armed_incomplete",
            )

        if not matched:
            return CommandDecision(
                kind="ignore",
                reason="ambient_no_wake",
                armed_after=False,
            )

        return self._candidate_decision(
            command,
            forced=forced,
            wake_detected=True,
            armed_reason="wake_without_complete_command",
        )

    def process_direct(self, text: str, *, forced: bool = False) -> CommandDecision:
        """Classify a push-to-talk transcript.

        Push-to-talk is already explicit user intent, so a wake phrase is not
        required. If the user still says one out of habit, strip it.
        """
        clean = " ".join((text or "").strip().split())
        if not clean:
            self._armed = False
            return CommandDecision(kind="ignore", reason="empty_transcript")

        matched, command = self._strip_repeated_wake(clean)
        candidate = command if matched else clean
        if self._looks_incomplete(candidate, forced=forced):
            self._armed = False
            return CommandDecision(
                kind="ignore",
                command=candidate.strip(),
                reason="push_to_talk_incomplete",
                wake_detected=matched,
                armed_after=False,
            )

        self._armed = False
        return CommandDecision(
            kind="dispatch",
            command=candidate.strip(),
            reason="push_to_talk_complete",
            wake_detected=matched,
            armed_after=False,
        )

    def _candidate_decision(
        self,
        command: str,
        *,
        forced: bool,
        wake_detected: bool,
        armed_reason: str,
    ) -> CommandDecision:
        command = command.strip()
        if self._looks_incomplete(command, forced=forced):
            self._armed = True
            return CommandDecision(
                kind="armed",
                command=command,
                reason=armed_reason,
                wake_detected=wake_detected,
                armed_after=True,
            )

        self._armed = False
        return CommandDecision(
            kind="dispatch",
            command=command,
            reason="complete_command",
            wake_detected=wake_detected,
            armed_after=False,
        )

    def _strip_repeated_wake(self, text: str) -> tuple[bool, str]:
        matched_any = False
        command = text
        for _ in range(8):
            matched, stripped = self._wake_filter.match(command)
            if not matched:
                break
            matched_any = True
            command = stripped.strip()
            if not command:
                break
        return matched_any, command

    def _looks_incomplete(self, command: str, *, forced: bool) -> bool:
        if forced:
            return True
        if len(command.strip()) < self._min_command_chars:
            return True
        return command.rstrip().endswith(("-", "–", "—"))
