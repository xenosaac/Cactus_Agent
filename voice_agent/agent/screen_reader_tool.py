"""Read-only visible-screen inspection for native app content."""
from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

from voice_agent.agent.system_prompts import VISIBLE_SCREEN_READER_SYSTEM
from voice_agent.agent.tool_router import ToolResult

if TYPE_CHECKING:
    from voice_agent.agent.gemini_client import GeminiClient
    from voice_agent.config import Settings

log = logging.getLogger(__name__)


class VisibleScreenReaderTool:
    name: str = "read_visible_screen"
    description: str = (
        "Read or summarize text/messages currently visible on the Mac screen. "
        "Use this after opening a native app when the user asks to check, read, "
        "list, or summarize visible content. Does not click, type, or navigate."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "What visible content to read or summarize.",
            },
            "max_items": {
                "type": "integer",
                "description": "Maximum visible messages/items to list.",
                "minimum": 1,
                "maximum": 25,
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

    async def call(self, args: dict[str, Any]) -> ToolResult:
        task = str(args.get("task") or "").strip()
        if not task:
            return ToolResult(
                ok=False,
                content="read_visible_screen needs a task",
                error="missing_task",
            )
        max_items = self._max_items(args.get("max_items"))
        png_path = await asyncio.to_thread(self._screenshot)
        try:
            image_bytes = await asyncio.to_thread(Path(png_path).read_bytes)
            if self._gemini is not None:
                return await self._read_with_gemini(task, max_items, image_bytes)
            return await self._read_with_local_vision(task, max_items, png_path)
        finally:
            self._cleanup(png_path)

    async def _read_with_gemini(
        self,
        task: str,
        max_items: int,
        image_bytes: bytes,
    ) -> ToolResult:
        assert self._gemini is not None
        prompt = self._prompt(task, max_items)
        try:
            text = await self._gemini.describe_image(
                prompt=prompt,
                image_bytes=image_bytes,
                system_instruction=VISIBLE_SCREEN_READER_SYSTEM,
                max_tokens=700,
                temperature=1.0,
                top_p=0.95,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("read_visible_screen Gemini failed: %s", exc)
            return ToolResult(
                ok=False,
                content=f"Screen read failed: {exc}",
                error=type(exc).__name__,
            )
        if not text:
            return ToolResult(
                ok=False,
                content="Screen read returned no text",
                error="empty_screen_read",
            )
        return ToolResult(ok=True, content=text)

    async def _read_with_local_vision(
        self,
        task: str,
        max_items: int,
        png_path: str,
    ) -> ToolResult:
        history: list[dict[str, Any]] = [
            {"role": "system", "content": VISIBLE_SCREEN_READER_SYSTEM},
            {
                "role": "user",
                "content": self._prompt(task, max_items),
                "images": [png_path],
            },
        ]
        options = json.dumps({
            "max_tokens": 700,
            "temperature": 0.1,
            "top_p": 0.9,
            "stop_sequences": ["<turn|>"],
        })
        try:
            result_json = await asyncio.to_thread(
                self._complete,
                self._handle,
                json.dumps(history),
                options,
                None,
                None,
                None,
            )
            result = json.loads(result_json)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                ok=False,
                content=f"Local screen read raised: {exc}",
                error=type(exc).__name__,
            )
        if not result.get("success"):
            return ToolResult(
                ok=False,
                content=f"Local screen read failed: {result.get('error')}",
                error="vision_model_failure",
            )
        text = str(result.get("response") or "").strip()
        if not text:
            return ToolResult(
                ok=False,
                content="Local screen read returned no text",
                error="empty_screen_read",
            )
        return ToolResult(ok=True, content=text)

    @staticmethod
    def _prompt(task: str, max_items: int) -> str:
        return (
            f"Task: {task}\n"
            f"Extract up to {max_items} visible messages/items if this is a list or chat. "
            "Keep the answer short and useful for speech. If the app is loading, not logged in, "
            "or the requested content is not visible, say that directly."
        )

    @staticmethod
    def _max_items(raw: Any) -> int:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = 10
        return max(1, min(value, 25))

    def _screenshot(self) -> str:
        import mss

        out = tempfile.mktemp(suffix=".png")
        with mss.mss() as sct:
            sct.shot(mon=1, output=out)
        return out

    def _cleanup(self, path: str) -> None:
        with suppress(Exception):
            Path(path).unlink(missing_ok=True)
