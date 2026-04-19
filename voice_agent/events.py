"""Event contract shared between backend and frontend. FROZEN at hour 4.

After freeze, only additive changes are allowed:
  - new EventType enum values (append-only)
  - new optional fields on AgentEvent

No renames, no removals, no type narrowing. Breaks the HUD otherwise.
"""
from __future__ import annotations

import itertools
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal

log = logging.getLogger(__name__)


class EventType(str, Enum):
    WAKE_DETECTED = "wake_detected"
    PTT_START = "push_to_talk_start"
    PTT_END = "push_to_talk_end"
    VOICE_READY = "voice_ready"
    STT_PARTIAL = "stt_partial"
    STT_FINAL = "stt_final"
    # Additional transcription lifecycle events so the UI can show progress
    # even when Whisper returns an empty string.
    AUDIO_LEVEL = "audio_level"  # periodic mic RMS/peak rollup
    WHISPER_START = "whisper_start"  # transcription just began
    STT_EMPTY = "stt_empty"  # Whisper returned empty on a non-silent segment
    AGENT_START = "agent_start"
    # Confirmation gate — UI shows a preview of the next tool call and waits
    # for the user to say "yes" / "do it" / "go ahead" (or "no" to cancel).
    CONFIRMATION_REQUIRED = "confirmation_required"
    AGENT_CANCELLED = "agent_cancelled"
    UNDO_REQUESTED = "undo_requested"
    STEP_START = "step_start"
    STEP_DONE = "step_done"
    AGENT_DONE = "agent_done"
    AGENT_ERROR = "agent_error"
    MODE_CHANGE = "mode_change"


_event_id_gen = itertools.count(1)


@dataclass(frozen=True)
class AgentEvent:
    type: EventType
    event_id: int = field(default_factory=lambda: next(_event_id_gen))
    timestamp: float = field(default_factory=time.time)

    # Optional payload fields populated per EventType (see table in PRD § 3.2)
    task: str | None = None
    step_index: int | None = None
    tool_name: str | None = None
    summary: str | None = None
    success: bool | None = None
    error: str | None = None
    mode: Literal["local", "hybrid"] | None = None
    partial_text: str | None = None
    final_text: str | None = None
    # Mic audio level — populated for AUDIO_LEVEL events. rms/peak in [0, 1.0].
    rms: float | None = None
    peak: float | None = None

    def humanize(self) -> str:
        core = self.type.value
        if self.error:
            return f"{core}: ERROR {self.error}"
        if self.summary:
            return f"{core}: {self.summary}"
        if self.final_text:
            return f"{core}: {self.final_text[:60]}"
        if self.partial_text:
            return f"{core}: ~{self.partial_text[:60]}"
        return core


Subscriber = Callable[[AgentEvent], Awaitable[None]]


def _event_summary(e: "AgentEvent") -> str:
    """One-line human-readable summary for logs. Truncates long strings."""
    bits: list[str] = []
    if e.task:
        bits.append(f"task={e.task[:60]!r}")
    if e.step_index is not None:
        bits.append(f"step={e.step_index}")
    if e.tool_name:
        bits.append(f"tool={e.tool_name}")
    if e.summary:
        bits.append(f"sum={e.summary[:80]!r}")
    if e.partial_text:
        bits.append(f"partial={e.partial_text[:40]!r}")
    if e.final_text:
        bits.append(f"final={e.final_text[:80]!r}")
    if e.error:
        bits.append(f"err={e.error[:80]!r}")
    if e.rms is not None:
        bits.append(f"rms={e.rms:.3f}")
    if e.peak is not None:
        bits.append(f"peak={e.peak:.3f}")
    return " ".join(bits)


class EventBus:
    """Fan-out, in-process, asyncio-backed. One-way backend -> frontend."""

    def __init__(self) -> None:
        self._subs: list[Subscriber] = []
        self._count = 0

    def subscribe(self, cb: Subscriber) -> Callable[[], None]:
        """Register a callback. Returns an unsubscribe function."""
        self._subs.append(cb)
        log.debug("bus subscribe: subs=%d", len(self._subs))

        def _unsub() -> None:
            if cb in self._subs:
                self._subs.remove(cb)
                log.debug("bus unsubscribe: subs=%d", len(self._subs))

        return _unsub

    async def publish(self, event: AgentEvent) -> None:
        """Deliver to every subscriber. Subscriber exceptions are logged
        but never prevent delivery to other subscribers."""
        self._count += 1
        # One-line summary of the event — truncate verbose fields.
        summary = _event_summary(event)
        log.info(
            "bus publish #%d type=%s subs=%d %s",
            event.event_id, event.type.value, len(self._subs), summary,
        )
        # Snapshot list so subscriber-triggered unsub doesn't mutate mid-iter.
        for cb in list(self._subs):
            try:
                await cb(event)
            except Exception as exc:  # noqa: BLE001
                log.exception("EventBus subscriber raised: %s", exc)

    def event_count(self) -> int:
        return self._count


# Process-wide singleton. Both backend and frontend import this.
bus: EventBus = EventBus()
