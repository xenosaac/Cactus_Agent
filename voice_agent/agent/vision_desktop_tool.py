"""Native-app control via screenshot -> vision model -> pyautogui."""
from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

from voice_agent.agent.system_prompts import VISION_DESKTOP_SYSTEM
from voice_agent.agent.tool_router import ToolResult

if TYPE_CHECKING:
    from voice_agent.agent.gemini_client import GeminiClient
    from voice_agent.config import Settings

log = logging.getLogger(__name__)


class VisionDesktopTool:
    name: str = "desktop_native_app"
    description: str = (
        "Control a native macOS app (FaceTime, Messages, Notes, etc.) when no "
        "MCP server exists for that app. Higher latency than other tools — "
        "prefer them when applicable. Input: `task` (natural-language goal)."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "Natural-language goal for a native-app operation.",
            },
        },
        "required": ["task"],
    }

    def __init__(
        self,
        settings: Settings,
        planner_handle: int,
        gemini: GeminiClient | None = None,
        complete: Callable[..., str] | None = None,
    ) -> None:
        if complete is None:
            from cactus.python.src.cactus import cactus_complete

            complete = cactus_complete
        self._complete = complete
        self._settings = settings
        self._handle = planner_handle
        self._gemini = gemini
        self._max_steps = settings.vision_max_steps
        self._settle_ms = settings.vision_settle_ms

    async def call(self, args: dict[str, Any]) -> ToolResult:
        task = str(args.get("task", "")).strip()
        if not task:
            return ToolResult(
                ok=False, content="desktop_native_app needs a task", error="missing_task"
            )

        history: list[dict[str, Any]] = [
            {"role": "system", "content": VISION_DESKTOP_SYSTEM},
            {"role": "user", "content": f"Task: {task}"},
        ]
        local_options = json.dumps({
            "max_tokens": 128,
            "temperature": 0.1,
            "top_p": 0.9,
            "stop_sequences": ["<turn|>"],
        })
        action_history: list[str] = []

        for step in range(1, self._max_steps + 1):
            png_path = await asyncio.to_thread(self._screenshot)

            try:
                raw = await self._next_action(
                    task=task,
                    step=step,
                    png_path=png_path,
                    local_history=history,
                    local_options=local_options,
                    action_history=action_history,
                )
            except Exception as exc:  # noqa: BLE001
                self._cleanup(png_path)
                return ToolResult(
                    ok=False,
                    content=f"Vision step {step} raised: {exc}",
                    error=type(exc).__name__,
                )

            self._cleanup(png_path)

            try:
                action = self._parse_action(raw)
            except ValueError:
                return ToolResult(
                    ok=False,
                    content=f"Malformed vision action at step {step}: {raw!r}",
                    error="malformed_action",
                )

            history.append({"role": "assistant", "content": raw})
            action_history.append(json.dumps(action))

            kind = action.get("action")
            if kind == "done":
                return ToolResult(ok=True, content=action.get("summary", "done"))
            try:
                await self._execute(action)
            except Exception as exc:  # noqa: BLE001
                return ToolResult(
                    ok=False,
                    content=f"pyautogui execute failed: {exc}",
                    error=type(exc).__name__,
                )

            await asyncio.sleep(self._settle_ms / 1000.0)

        return ToolResult(
            ok=False,
            content=f"Max vision steps ({self._max_steps}) exceeded",
            error="max_steps",
        )

    # ── internals ─────────────────────────────────────────────────────────────

    async def _next_action(
        self,
        *,
        task: str,
        step: int,
        png_path: str,
        local_history: list[dict[str, Any]],
        local_options: str,
        action_history: list[str],
    ) -> str:
        if self._gemini is not None:
            image_bytes = await asyncio.to_thread(Path(png_path).read_bytes)
            prompt = self._gemini_action_prompt(task, step, action_history)
            return await self._gemini.describe_image(
                prompt=prompt,
                image_bytes=image_bytes,
                system_instruction=VISION_DESKTOP_SYSTEM,
                max_tokens=192,
                temperature=1.0,
                top_p=0.95,
                timeout_s=self._settings.gemini_vision_timeout_s,
                response_mime_type="application/json",
                thinking_level=self._settings.gemini_vision_thinking_level,
            )

        history_step = local_history + [
            {"role": "user", "content": "Current screen:", "images": [png_path]},
        ]
        result_json = await asyncio.to_thread(
            self._complete,
            self._handle,
            json.dumps(history_step),
            local_options,
            None,
            None,
            None,
        )
        result = json.loads(result_json)
        if not result.get("success"):
            raise RuntimeError(f"vision model failed: {result.get('error')}")
        return str(result.get("response") or "").strip()

    @staticmethod
    def _gemini_action_prompt(
        task: str,
        step: int,
        action_history: list[str],
    ) -> str:
        previous = "\n".join(action_history[-6:]) or "(none)"
        return (
            f"Task: {task}\n"
            f"Step: {step}\n"
            f"Previous actions:\n{previous}\n\n"
            "Look at the current screenshot and choose the single next UI action. "
            "If the task names an icon button or app control, click the visual center "
            "of that icon/button. Only return done when the requested state is visible "
            "or the task is already complete."
        )

    @staticmethod
    def _parse_action(raw: str) -> dict[str, Any]:
        text = raw.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        try:
            action = json.loads(text)
        except json.JSONDecodeError as exc:
            start = text.find("{")
            end = text.rfind("}")
            if start == -1 or end == -1 or end <= start:
                raise ValueError("no JSON object in vision response") from exc
            action = json.loads(text[start : end + 1])
        if not isinstance(action, dict):
            raise ValueError("vision response is not a JSON object")
        return action

    def _screenshot(self) -> str:
        import mss

        out = tempfile.mktemp(suffix=".png")
        with mss.mss() as sct:
            sct.shot(mon=1, output=out)
        return out

    async def _execute(self, action: dict[str, Any]) -> None:
        import pyautogui
        kind = action.get("action")
        if kind == "click":
            await asyncio.to_thread(pyautogui.click, action["x"], action["y"])
        elif kind == "type":
            await asyncio.to_thread(
                pyautogui.typewrite, action.get("text", ""), 0.02
            )
        elif kind == "key":
            key = action.get("key", "")
            if "+" in key:
                parts = key.split("+")
                await asyncio.to_thread(pyautogui.hotkey, *parts)
            else:
                await asyncio.to_thread(pyautogui.press, key)
        elif kind == "scroll":
            await asyncio.to_thread(pyautogui.scroll, int(action.get("dy", 0)))
        elif kind == "wait":
            await asyncio.sleep(float(action.get("seconds", 1.0)))
        else:
            raise ValueError(f"Unknown action kind: {kind}")

    def _cleanup(self, path: str) -> None:
        with suppress(Exception):
            Path(path).unlink(missing_ok=True)
