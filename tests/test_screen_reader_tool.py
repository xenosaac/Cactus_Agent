from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from voice_agent.agent.screen_reader_tool import VisibleScreenReaderTool


class _FakeGemini:
    def __init__(self, text: str | None = "1. Alice: hello") -> None:
        self.text = text
        self.calls: list[dict[str, Any]] = []

    async def describe_image(self, **kwargs: Any) -> str:
        self.calls.append(kwargs)
        if self.text is None:
            raise RuntimeError("cloud down")
        return self.text


@pytest.mark.asyncio
async def test_read_visible_screen_uses_gemini_for_one_shot_read(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = tmp_path / "screen.png"
    image.write_bytes(b"fake-png")
    gemini = _FakeGemini("1. Alice: visible message")
    local_calls = 0

    def _local_complete(*args: Any) -> str:
        nonlocal local_calls
        local_calls += 1
        return json.dumps({"success": True, "response": "local"})

    tool = VisibleScreenReaderTool(
        SimpleNamespace(),
        planner_handle=123,
        gemini=gemini,  # type: ignore[arg-type]
        complete=_local_complete,
    )
    monkeypatch.setattr(tool, "_screenshot", lambda: str(image))

    result = await tool.call({"task": "check my top 10 messages", "max_items": 10})

    assert result.ok
    assert result.content == "1. Alice: visible message"
    assert local_calls == 0
    assert gemini.calls[0]["image_bytes"] == b"fake-png"
    assert "up to 10 visible" in gemini.calls[0]["prompt"]


@pytest.mark.asyncio
async def test_read_visible_screen_returns_gemini_error_without_local_retry(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = tmp_path / "screen.png"
    image.write_bytes(b"fake-png")
    gemini = _FakeGemini(None)
    local_calls = 0

    def _local_complete(*args: Any) -> str:
        nonlocal local_calls
        local_calls += 1
        return json.dumps({"success": True, "response": "local"})

    tool = VisibleScreenReaderTool(
        SimpleNamespace(),
        planner_handle=123,
        gemini=gemini,  # type: ignore[arg-type]
        complete=_local_complete,
    )
    monkeypatch.setattr(tool, "_screenshot", lambda: str(image))

    result = await tool.call({"task": "read Discord"})

    assert not result.ok
    assert result.error == "RuntimeError"
    assert local_calls == 0


@pytest.mark.asyncio
async def test_read_visible_screen_local_fallback_when_no_gemini(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = tmp_path / "screen.png"
    image.write_bytes(b"fake-png")

    def _local_complete(*args: Any) -> str:
        return json.dumps({"success": True, "response": "visible local text"})

    tool = VisibleScreenReaderTool(
        SimpleNamespace(),
        planner_handle=123,
        gemini=None,
        complete=_local_complete,
    )
    monkeypatch.setattr(tool, "_screenshot", lambda: str(image))

    result = await tool.call({"task": "read visible app content"})

    assert result.ok
    assert result.content == "visible local text"


@pytest.mark.asyncio
async def test_read_visible_screen_requires_task() -> None:
    tool = VisibleScreenReaderTool(
        SimpleNamespace(),
        planner_handle=123,
        gemini=None,
        complete=lambda *args: "{}",
    )

    result = await tool.call({})

    assert not result.ok
    assert result.error == "missing_task"
