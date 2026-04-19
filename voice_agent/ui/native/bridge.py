"""Bridge the asyncio-backed EventBus to Qt signals.

Qt widgets live on the main thread; the backend lives on a daemon thread.
Signals emitted from another thread are delivered on the receiver's thread via
`Qt.QueuedConnection` — that's what makes this safe.

The bridge also exposes `wake(text=None)`, `confirm()`, `cancel()` helpers so
the Qt window can drop events into the same `intent_queue` the HTTP
`/wake /confirm /cancel` endpoints use. One path of truth.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from PySide6.QtCore import QObject, Signal

from voice_agent.events import AgentEvent, EventBus, EventType
from voice_agent.events import bus as default_bus

log = logging.getLogger(__name__)


class EventBridge(QObject):
    # Emitted on any AgentEvent. Payload is the raw dataclass (serialization
    # is the slot's problem — the reducer takes AgentEvent directly).
    agent_event = Signal(object)

    def __init__(
        self,
        bus: EventBus,
        intent_queue: "asyncio.Queue[str]",
        backend_loop: asyncio.AbstractEventLoop,
    ) -> None:
        super().__init__()
        self._bus = bus
        self._intent_queue = intent_queue
        self._loop = backend_loop
        self._unsub: "Any" = bus.subscribe(self._on_event)
        log.info("EventBridge attached to bus (subs=1)")

    async def _on_event(self, event: AgentEvent) -> None:
        # Runs on the asyncio loop thread. Signal.emit crosses threads safely
        # when the connection is auto/queued — Qt auto-picks queued for
        # cross-thread emissions.
        self.agent_event.emit(event)

    def detach(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None
            log.info("EventBridge detached")

    # ── intent helpers — mirror server.py /wake /confirm /cancel ──────────
    def wake(self, text: str | None = None) -> None:
        """Fire a wake. If `text` is provided, it's routed through the
        `utter:<text>` shape that `_intent_consumer` already handles."""
        asyncio.run_coroutine_threadsafe(self._push_wake(text), self._loop)

    async def _push_wake(self, text: str | None) -> None:
        await self._bus.publish(AgentEvent(
            type=EventType.WAKE_DETECTED,
            summary="typed_wake" if text else None,
        ))
        if text:
            await self._bus.publish(
                AgentEvent(type=EventType.STT_FINAL, final_text=text)
            )
            await self._intent_queue.put(f"utter:{text}")

    def push_to_talk_start(self) -> None:
        asyncio.run_coroutine_threadsafe(
            self._bus.publish(AgentEvent(type=EventType.PTT_START)),
            self._loop,
        )

    def push_to_talk_end(self) -> None:
        asyncio.run_coroutine_threadsafe(
            self._bus.publish(AgentEvent(type=EventType.PTT_END)),
            self._loop,
        )

    def confirm(self) -> None:
        asyncio.run_coroutine_threadsafe(
            self._intent_queue.put("confirm"), self._loop
        )

    def cancel(self) -> None:
        asyncio.run_coroutine_threadsafe(
            self._intent_queue.put("cancel"), self._loop
        )


def make_bridge(
    intent_queue: "asyncio.Queue[str]",
    backend_loop: asyncio.AbstractEventLoop,
    bus: EventBus | None = None,
) -> EventBridge:
    return EventBridge(bus or default_bus, intent_queue, backend_loop)


__all__ = ["EventBridge", "make_bridge"]
