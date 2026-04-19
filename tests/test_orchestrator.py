from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from voice_agent.agent.orchestrator import AgentOrchestrator
from voice_agent.agent.tool_router import ToolResult, ToolRouter
from voice_agent.config import Mode, Settings
from voice_agent.events import AgentEvent, EventBus, EventType


class _FakeTool:
    name = "probe"
    description = "test tool"
    schema: dict[str, Any] = {"type": "object", "properties": {}}
    calls: list[dict[str, Any]] = []

    async def call(self, args: dict[str, Any]) -> ToolResult:
        type(self).calls.append(args)
        return ToolResult(ok=True, content=f"ran with {args}")


class _BrowserTool:
    name = "web_navigate"
    description = "test browser tool"
    schema: dict[str, Any] = {"type": "object", "properties": {}}

    async def call(self, args: dict[str, Any]) -> ToolResult:
        return ToolResult(
            ok=True,
            content="Cheapest Flight\n\nAirline: Frontier\nPrice: $124",
        )


class _OneShotPlanner:
    """Returns one tool_call then a final answer."""

    def __init__(self, tool_name: str = "probe", final: str = "all done") -> None:
        self._tool_name = tool_name
        self._final = final
        self._turn_phase = 0
        self.reset_calls = 0

    def reset_turn(self) -> None:
        self.reset_calls += 1

    async def ainvoke(self, messages, output_format=None, **kwargs):
        self._turn_phase += 1
        if self._turn_phase == 1:
            tc = SimpleNamespace(name=self._tool_name, arguments={"x": 1})
            return SimpleNamespace(completion="", tool_calls=[tc])
        return SimpleNamespace(completion=self._final, tool_calls=[])


async def _fake_say(*args, **kwargs) -> None:
    return None


@pytest.mark.asyncio
async def test_tool_path_executes_without_confirmation() -> None:
    """Planner -> STEP_START -> STEP_DONE -> AGENT_DONE."""
    _FakeTool.calls = []
    bus = EventBus()
    received: list[AgentEvent] = []

    async def cb(e: AgentEvent) -> None:
        received.append(e)

    bus.subscribe(cb)

    router = ToolRouter([_FakeTool()], timeout_s=2.0)
    settings = Settings(mode=Mode.LOCAL, max_agent_steps=5)
    planner = _OneShotPlanner()
    orch = AgentOrchestrator(settings, planner, router, bus)

    with patch("voice_agent.agent.orchestrator.say", new=_fake_say):
        await orch.run_turn("probe it")

    types = [e.type for e in received]
    assert types == [
        EventType.AGENT_START,
        EventType.STEP_START,
        EventType.STEP_DONE,
        EventType.AGENT_DONE,
    ]
    assert planner.reset_calls == 1
    assert _FakeTool.calls == [{"x": 1}]
    assert not orch.awaiting_confirm()


@pytest.mark.asyncio
async def test_legacy_cancel_intent_does_not_block_tool_execution() -> None:
    """Legacy cancel intents are ignored because confirmation is disabled."""
    _FakeTool.calls = []
    bus = EventBus()
    received: list[AgentEvent] = []

    async def cb(e: AgentEvent) -> None:
        received.append(e)

    bus.subscribe(cb)

    router = ToolRouter([_FakeTool()], timeout_s=2.0)
    settings = Settings(mode=Mode.LOCAL, max_agent_steps=5)
    planner = _OneShotPlanner()
    orch = AgentOrchestrator(settings, planner, router, bus)
    orch.deliver_intent("cancel")

    with patch("voice_agent.agent.orchestrator.say", new=_fake_say):
        await orch.run_turn("probe it")

    types = [e.type for e in received]
    assert types == [
        EventType.AGENT_START,
        EventType.STEP_START,
        EventType.STEP_DONE,
        EventType.AGENT_DONE,
    ]
    assert _FakeTool.calls == [{"x": 1}]


@pytest.mark.asyncio
async def test_vague_browser_saved_reply_is_replaced_with_tool_content() -> None:
    bus = EventBus()
    received: list[AgentEvent] = []

    async def cb(e: AgentEvent) -> None:
        received.append(e)

    bus.subscribe(cb)

    router = ToolRouter([_BrowserTool()], timeout_s=2.0)
    settings = Settings(mode=Mode.LOCAL, max_agent_steps=5)
    planner = _OneShotPlanner(
        tool_name="web_navigate",
        final="The details have been saved for you to review.",
    )
    orch = AgentOrchestrator(settings, planner, router, bus)

    with patch("voice_agent.agent.orchestrator.say", new=_fake_say):
        await orch.run_turn("find flights")

    done = [e for e in received if e.type == EventType.AGENT_DONE][0]
    assert done.final_text == "Cheapest Flight\n\nAirline: Frontier\nPrice: $124"
    assert "saved" not in (done.final_text or "").lower()
    assert not orch.awaiting_confirm()


@pytest.mark.asyncio
async def test_no_intent_needed_for_tool_execution() -> None:
    """The turn should complete without waiting for a confirmation intent."""
    _FakeTool.calls = []
    bus = EventBus()
    received: list[AgentEvent] = []

    async def cb(e: AgentEvent) -> None:
        received.append(e)

    bus.subscribe(cb)

    router = ToolRouter([_FakeTool()], timeout_s=2.0)
    settings = Settings(mode=Mode.LOCAL, max_agent_steps=5)
    planner = _OneShotPlanner()
    orch = AgentOrchestrator(settings, planner, router, bus)

    with patch("voice_agent.agent.orchestrator.say", new=_fake_say):
        await asyncio.wait_for(orch.run_turn("probe it"), timeout=2.0)

    types = [e.type for e in received]
    assert types == [
        EventType.AGENT_START,
        EventType.STEP_START,
        EventType.STEP_DONE,
        EventType.AGENT_DONE,
    ]
    assert _FakeTool.calls == [{"x": 1}]
