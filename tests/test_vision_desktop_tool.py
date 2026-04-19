from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from voice_agent.agent.vision_desktop_tool import VisionDesktopTool


class _FakeGemini:
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    async def describe_image(self, **kwargs: Any) -> str:
        self.calls.append(kwargs)
        return self.responses.pop(0)


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        vision_max_steps=3,
        vision_settle_ms=0,
        gemini_vision_timeout_s=45.0,
        gemini_vision_thinking_level="low",
    )


@pytest.mark.asyncio
async def test_desktop_native_app_uses_gemini_for_actions(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gemini = _FakeGemini([
        json.dumps({"action": "click", "x": 42, "y": 99, "reason": "target icon"}),
        json.dumps({"action": "done", "summary": "opened the visible panel"}),
    ])
    tool = VisionDesktopTool(
        _settings(),
        planner_handle=123,
        gemini=gemini,  # type: ignore[arg-type]
        complete=lambda *args: "{}",
    )
    shots = 0

    def _shot() -> str:
        nonlocal shots
        shots += 1
        path = tmp_path / f"screen-{shots}.png"
        path.write_bytes(b"fake-png")
        return str(path)

    executed: list[dict[str, Any]] = []

    async def _execute(action: dict[str, Any]) -> None:
        executed.append(action)

    monkeypatch.setattr(tool, "_screenshot", _shot)
    monkeypatch.setattr(tool, "_execute", _execute)

    result = await tool.call({"task": "click the Discord inbox icon"})

    assert result.ok
    assert result.content == "opened the visible panel"
    assert len(gemini.calls) == 2
    assert gemini.calls[0]["response_mime_type"] == "application/json"
    assert gemini.calls[0]["thinking_level"] == "low"
    assert executed == [{"action": "click", "x": 42, "y": 99, "reason": "target icon"}]


def test_parse_action_accepts_markdown_fenced_json() -> None:
    raw = '```json\n{"action": "done", "summary": "ok"}\n```'
    assert VisionDesktopTool._parse_action(raw) == {
        "action": "done",
        "summary": "ok",
    }


@pytest.mark.asyncio
async def test_desktop_native_app_local_fallback_without_gemini(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = tmp_path / "screen.png"
    image.write_bytes(b"fake-png")
    local_calls = 0

    def _local_complete(*args: Any) -> str:
        nonlocal local_calls
        local_calls += 1
        return json.dumps({
            "success": True,
            "response": json.dumps({"action": "done", "summary": "local done"}),
        })

    tool = VisionDesktopTool(
        _settings(),
        planner_handle=123,
        gemini=None,
        complete=_local_complete,
    )
    monkeypatch.setattr(tool, "_screenshot", lambda: str(image))
    monkeypatch.setattr(tool, "_cleanup", lambda path: None)

    result = await tool.call({"task": "inspect app"})

    assert result.ok
    assert result.content == "local done"
    assert local_calls == 1
