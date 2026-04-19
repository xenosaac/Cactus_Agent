"""Central dispatch for all agent tools.

Owns: tool manifest (single source of truth), timeout enforcement,
retry-once policy, error normalization to ToolResult.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

log = logging.getLogger(__name__)


_NO_RETRY_TOOLS = frozenset({
    # These tools call screenshot/vision work inside threads. asyncio timeout
    # cancellation cannot stop that underlying thread, so retrying can create
    # overlapping vision calls and a longer apparent hang.
    "desktop_native_app",
    "read_visible_screen",
})


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    content: str
    structured: dict[str, Any] | None = None
    error: str | None = None

    def to_tool_message_content(self) -> str:
        """Serialize for feeding back into the planner as a tool message."""
        if self.structured is not None:
            return json.dumps(
                {"ok": self.ok, "data": self.structured, "error": self.error}
            )
        return self.content


@runtime_checkable
class ToolAdapter(Protocol):
    name: str
    description: str
    schema: dict[str, Any]

    async def call(self, args: dict[str, Any]) -> ToolResult: ...


class ToolRouter:
    def __init__(
        self, tools: list[ToolAdapter], timeout_s: float = 30.0
    ) -> None:
        self._tools: dict[str, ToolAdapter] = {}
        for t in tools:
            if t.name in self._tools:
                raise ValueError(f"Duplicate tool name: {t.name}")
            self._tools[t.name] = t
        self._timeout = timeout_s

    @property
    def tools(self) -> list[ToolAdapter]:
        return list(self._tools.values())

    def manifest(self) -> list[dict[str, Any]]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "parameters": t.schema,
            }
            for t in self._tools.values()
        ]

    def manifest_json(self) -> str:
        return json.dumps(self.manifest())

    async def dispatch(
        self, tool_name: str, args: dict[str, Any]
    ) -> ToolResult:
        tool = self._tools.get(tool_name)
        if tool is None:
            return ToolResult(
                ok=False,
                content=f"Unknown tool: {tool_name}",
                error="unknown_tool",
            )

        attempts = 1 if tool_name in _NO_RETRY_TOOLS else 2
        for attempt in range(1, attempts + 1):
            try:
                return await asyncio.wait_for(
                    tool.call(args), timeout=self._timeout
                )
            except TimeoutError:
                log.warning("tool=%s timed out (attempt %d)", tool_name, attempt)
                if attempt == attempts:
                    return ToolResult(
                        ok=False,
                        content=f"Tool {tool_name} timed out",
                        error="timeout",
                    )
            except Exception as exc:  # noqa: BLE001
                log.exception(
                    "tool=%s failed (attempt %d): %s", tool_name, attempt, exc
                )
                if attempt == attempts:
                    return ToolResult(
                        ok=False,
                        content=f"Tool {tool_name} failed: {exc}",
                        error=type(exc).__name__,
                    )
                await asyncio.sleep(0.5 * attempt)

        # Unreachable
        return ToolResult(
            ok=False, content="Unreachable retry loop", error="internal"
        )
