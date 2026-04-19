from __future__ import annotations

import asyncio
from typing import Any

import pytest

from voice_agent.agent.tool_router import ToolResult, ToolRouter


class _Happy:
    name = "happy"
    description = "always works"
    schema: dict[str, Any] = {"type": "object", "properties": {}}

    async def call(self, args: dict[str, Any]) -> ToolResult:
        return ToolResult(ok=True, content="ok", structured={"echo": args})


class _Flaky:
    """Fails on attempt 1, succeeds on attempt 2."""
    name = "flaky"
    description = "fails once"
    schema: dict[str, Any] = {"type": "object", "properties": {}}
    calls = 0

    async def call(self, args: dict[str, Any]) -> ToolResult:
        type(self).calls += 1
        if type(self).calls == 1:
            raise RuntimeError("first try fails")
        return ToolResult(ok=True, content="recovered")


class _Slow:
    name = "slow"
    description = "hangs"
    schema: dict[str, Any] = {"type": "object", "properties": {}}

    async def call(self, args: dict[str, Any]) -> ToolResult:
        await asyncio.sleep(10)
        return ToolResult(ok=True, content="never")


class _SlowDesktop:
    name = "desktop_native_app"
    description = "non-cancellable vision work"
    schema: dict[str, Any] = {"type": "object", "properties": {}}
    calls = 0

    async def call(self, args: dict[str, Any]) -> ToolResult:
        type(self).calls += 1
        await asyncio.sleep(10)
        return ToolResult(ok=True, content="never")


@pytest.mark.asyncio
async def test_manifest_lists_all_tools() -> None:
    router = ToolRouter([_Happy()], timeout_s=2.0)
    m = router.manifest()
    assert len(m) == 1
    assert m[0]["name"] == "happy"


@pytest.mark.asyncio
async def test_dispatch_happy_path() -> None:
    router = ToolRouter([_Happy()], timeout_s=2.0)
    r = await router.dispatch("happy", {"x": 1})
    assert r.ok
    assert r.structured == {"echo": {"x": 1}}


@pytest.mark.asyncio
async def test_unknown_tool_returns_error() -> None:
    router = ToolRouter([_Happy()], timeout_s=2.0)
    r = await router.dispatch("nope", {})
    assert not r.ok
    assert r.error == "unknown_tool"


@pytest.mark.asyncio
async def test_duplicate_tool_names_rejected() -> None:
    with pytest.raises(ValueError):
        ToolRouter([_Happy(), _Happy()])


@pytest.mark.asyncio
async def test_retry_once_and_succeeds() -> None:
    _Flaky.calls = 0
    router = ToolRouter([_Flaky()], timeout_s=2.0)
    r = await router.dispatch("flaky", {})
    assert r.ok
    assert _Flaky.calls == 2


@pytest.mark.asyncio
async def test_timeout_returns_error() -> None:
    router = ToolRouter([_Slow()], timeout_s=0.05)
    r = await router.dispatch("slow", {})
    assert not r.ok
    assert r.error == "timeout"


@pytest.mark.asyncio
async def test_screen_tools_do_not_retry_after_timeout() -> None:
    _SlowDesktop.calls = 0
    router = ToolRouter([_SlowDesktop()], timeout_s=0.05)
    r = await router.dispatch("desktop_native_app", {})
    assert not r.ok
    assert r.error == "timeout"
    assert _SlowDesktop.calls == 1


def test_tool_result_serialization() -> None:
    tr = ToolResult(ok=True, content="x", structured={"a": 1})
    msg = tr.to_tool_message_content()
    assert '"ok": true' in msg
    assert '"a": 1' in msg

    tr2 = ToolResult(ok=True, content="plain")
    assert tr2.to_tool_message_content() == "plain"
