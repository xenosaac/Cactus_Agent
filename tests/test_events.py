from __future__ import annotations

import asyncio

import pytest

from voice_agent.events import AgentEvent, EventBus, EventType


@pytest.mark.asyncio
async def test_publish_delivers_to_all_subscribers() -> None:
    bus = EventBus()
    received_a: list[AgentEvent] = []
    received_b: list[AgentEvent] = []

    async def cb_a(e: AgentEvent) -> None:
        received_a.append(e)

    async def cb_b(e: AgentEvent) -> None:
        received_b.append(e)

    bus.subscribe(cb_a)
    bus.subscribe(cb_b)

    await bus.publish(AgentEvent(type=EventType.WAKE_DETECTED))
    await bus.publish(AgentEvent(type=EventType.AGENT_DONE, summary="ok"))

    assert len(received_a) == 2
    assert len(received_b) == 2
    assert bus.event_count() == 2


@pytest.mark.asyncio
async def test_subscriber_exception_isolates() -> None:
    bus = EventBus()
    seen: list[int] = []

    async def bad(_: AgentEvent) -> None:
        raise RuntimeError("intentional")

    async def good(e: AgentEvent) -> None:
        seen.append(e.event_id)

    bus.subscribe(bad)
    bus.subscribe(good)
    await bus.publish(AgentEvent(type=EventType.WAKE_DETECTED))
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_unsubscribe_stops_delivery() -> None:
    bus = EventBus()
    received: list[AgentEvent] = []

    async def cb(e: AgentEvent) -> None:
        received.append(e)

    unsub = bus.subscribe(cb)
    await bus.publish(AgentEvent(type=EventType.WAKE_DETECTED))
    unsub()
    await bus.publish(AgentEvent(type=EventType.WAKE_DETECTED))
    assert len(received) == 1


def test_agent_event_humanize_basic() -> None:
    e = AgentEvent(type=EventType.AGENT_DONE, summary="sent email")
    s = e.humanize()
    assert "agent_done" in s
    assert "sent email" in s

    err = AgentEvent(type=EventType.AGENT_ERROR, error="boom")
    assert "ERROR" in err.humanize()
