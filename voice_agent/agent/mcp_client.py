"""MCP subprocess pool. Spawns N MCP servers as stdio subprocesses,
enumerates their tools, and exposes each tool as a ToolAdapter."""
from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

from voice_agent.agent.tool_router import ToolAdapter, ToolResult

log = logging.getLogger(__name__)


@dataclass
class MCPServerSpec:
    name: str
    command: str
    args: list[str]
    env: dict[str, str] = field(default_factory=dict)


class MCPToolAdapter:
    """Represents ONE tool exposed by an MCP server."""

    def __init__(
        self,
        session: Any,  # mcp.ClientSession — duck typed to avoid import in this scope
        tool: Any,     # mcp.types.Tool
        server_name: str,
    ) -> None:
        self._session = session
        self._underlying_name = tool.name
        self.name = f"{server_name}.{tool.name}"
        self.description = (tool.description or "").strip()
        self.schema = tool.inputSchema or {"type": "object", "properties": {}}

    async def call(self, args: dict[str, Any]) -> ToolResult:
        import time as _time
        _t0 = _time.perf_counter()
        log.info(
            "mcp call tool=%s args_preview=%s",
            self.name, {k: str(v)[:40] for k, v in args.items()},
        )
        from mcp import types as mcp_types

        try:
            result = await self._session.call_tool(
                name=self._underlying_name, arguments=args
            )
        except Exception as exc:  # noqa: BLE001
            log.exception(
                "mcp call FAILED tool=%s after %.1fms: %s",
                self.name, (_time.perf_counter() - _t0) * 1000, exc,
            )
            return ToolResult(
                ok=False,
                content=f"MCP call error: {exc}",
                error=type(exc).__name__,
            )
        log.info(
            "mcp call DONE tool=%s duration_ms=%.1f",
            self.name, (_time.perf_counter() - _t0) * 1000,
        )

        text_parts: list[str] = []
        for c in getattr(result, "content", None) or []:
            if isinstance(c, mcp_types.TextContent):
                text_parts.append(c.text)

        # MCP Python SDK v1.27 uses camelCase (isError, structuredContent).
        # Probe both forms in case future versions switch to snake_case.
        structured = (
            getattr(result, "structuredContent", None)
            or getattr(result, "structured_content", None)
        )
        is_error = bool(
            getattr(result, "isError", None)
            or getattr(result, "is_error", None)
        )
        content = "\n".join(text_parts) if text_parts else "(empty)"
        return ToolResult(
            ok=not is_error,
            content=content,
            structured=structured,
            error=None if not is_error else content,
        )


class MCPClientPool:
    """Context manager that spawns all MCP servers and yields their adapters."""

    def __init__(self, specs: list[MCPServerSpec]) -> None:
        self._specs = specs
        self._stack = AsyncExitStack()
        self._adapters: list[ToolAdapter] = []

    async def __aenter__(self) -> list[ToolAdapter]:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        for spec in self._specs:
            log.info(
                "MCP spawn name=%s cmd=%s args=%s env_keys=%s",
                spec.name, spec.command, list(spec.args),
                list((spec.env or {}).keys()),
            )
            params = StdioServerParameters(
                command=spec.command,
                args=list(spec.args),
                env=(spec.env or None),
            )
            try:
                read, write = await self._stack.enter_async_context(
                    stdio_client(params)
                )
                session = await self._stack.enter_async_context(
                    ClientSession(read, write)
                )
                await session.initialize()
                tools_resp = await session.list_tools()
            except Exception as exc:  # noqa: BLE001
                log.exception(
                    "MCP server %s failed to initialize: %s", spec.name, exc
                )
                continue

            for tool in tools_resp.tools:
                self._adapters.append(
                    MCPToolAdapter(session, tool, spec.name)
                )
            log.info(
                "MCP %s: %d tools registered", spec.name, len(tools_resp.tools)
            )
        return list(self._adapters)

    async def __aexit__(self, *exc: object) -> None:
        await self._stack.aclose()
