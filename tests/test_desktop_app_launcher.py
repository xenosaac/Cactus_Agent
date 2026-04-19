from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from voice_agent.agent.desktop_app_launcher import DesktopAppLauncher


def _fake_proc(returncode: int, stderr: bytes = b"") -> AsyncMock:
    proc = AsyncMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(b"", stderr))
    proc.kill = AsyncMock()
    return proc


@pytest.mark.asyncio
async def test_open_success() -> None:
    tool = DesktopAppLauncher()
    with patch(
        "voice_agent.agent.desktop_app_launcher.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=_fake_proc(0)),
    ):
        result = await tool.call({"app": "Safari"})
    assert result.ok
    assert "Safari" in result.content


@pytest.mark.asyncio
async def test_open_missing_arg() -> None:
    tool = DesktopAppLauncher()
    result = await tool.call({})
    assert not result.ok
    assert result.error == "bad_args"


@pytest.mark.asyncio
async def test_open_failure_surfaces_stderr() -> None:
    tool = DesktopAppLauncher()
    with patch(
        "voice_agent.agent.desktop_app_launcher.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=_fake_proc(1, stderr=b"No such app: WidgetFoo")),
    ):
        result = await tool.call({"app": "WidgetFoo"})
    assert not result.ok
    assert "WidgetFoo" in result.content
    assert result.error == "open_failed"
