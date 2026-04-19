"""Browser Use Agent wrapped as ONE tool.

The outer orchestrator treats web navigation as a single tool call; internally
Browser Use runs its own multi-step loop. We bridge Browser Use step hooks into
our EventBus so the HUD sees sub-step progress ("Opening page", "Clicking link",
"Reading result").
"""
from __future__ import annotations

import logging
from typing import Any

from voice_agent.agent.tool_router import ToolResult
from voice_agent.events import AgentEvent, EventBus, EventType

log = logging.getLogger(__name__)


class BrowserUseAdapter:
    """Browser Use ToolAdapter. Accepts a Browser Use-compatible LLM object."""

    name: str = "web_navigate"
    description: str = (
        "Use for ANY web task: open URL, read a page, fill a form, click "
        "buttons, navigate a website. Input: `task` (natural-language goal). "
        "This tool drives a real browser locally; no data leaves the machine."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "Natural-language goal for the web subtask.",
            },
        },
        "required": ["task"],
    }

    def __init__(
        self,
        llm: object,       # BaseChatModel-compatible
        browser: object,   # browser_use.Browser
        bus: EventBus,
    ) -> None:
        self._llm = llm
        self._browser = browser
        self._bus = bus

    async def call(self, args: dict[str, Any]) -> ToolResult:
        # Lazy import so agent tests that don't exercise BU can run without it.
        try:
            from browser_use import Agent
        except Exception as exc:  # noqa: BLE001
            log.exception("browser_use import failed: %s", exc)
            return ToolResult(
                ok=False,
                content=f"browser_use unavailable: {exc}",
                error="browser_use_missing",
            )

        task = args.get("task", "")
        if not task:
            return ToolResult(
                ok=False,
                content="web_navigate requires a `task` argument",
                error="missing_task",
            )

        # browser-use v0.3.3 Agent.run signature (verified via inspect):
        #   async def run(
        #       self,
        #       max_steps: int = 100,
        #       on_step_start: Callable[[Agent], Awaitable[None]] | None = None,
        #       on_step_end:   Callable[[Agent], Awaitable[None]] | None = None,
        #   ) -> AgentHistoryList
        # Hooks receive the Agent instance itself.
        step_idx = {"i": 0}

        async def _on_step_start(agent: object) -> None:
            step_idx["i"] += 1
            # Best-effort summary from the agent's latest state.
            summary_text = "browser step"
            try:
                state = getattr(agent, "state", None)
                history = getattr(state, "history", None) or []
                if history:
                    last = history[-1]
                    for attr in ("state_description", "action_description", "result"):
                        v = getattr(last, attr, None)
                        if isinstance(v, str) and v:
                            summary_text = v
                            break
            except Exception:  # noqa: BLE001
                pass
            await self._bus.publish(
                AgentEvent(
                    type=EventType.STEP_START,
                    step_index=step_idx["i"],
                    tool_name="web_navigate",
                    summary=summary_text[:120],
                )
            )

        async def _on_step_end(agent: object) -> None:
            success = True
            summary_text = "step complete"
            try:
                state = getattr(agent, "state", None)
                history = getattr(state, "history", None) or []
                if history:
                    last = history[-1]
                    err = getattr(last, "error", None)
                    if err:
                        success = False
                        summary_text = str(err)[:120]
                    else:
                        for attr in ("result", "extracted_content", "summary"):
                            v = getattr(last, attr, None)
                            if isinstance(v, str) and v:
                                summary_text = v[:120]
                                break
            except Exception:  # noqa: BLE001
                pass
            await self._bus.publish(
                AgentEvent(
                    type=EventType.STEP_DONE,
                    step_index=step_idx["i"],
                    tool_name="web_navigate",
                    success=success,
                    summary=summary_text[:120],
                )
            )

        try:
            agent = Agent(
                task=task,
                llm=self._llm,
                browser=self._browser,
                use_vision=False,
                enable_memory=False,
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                ok=False,
                content=f"Could not construct Browser Use agent: {exc}",
                error="browser_use_construct",
            )

        try:
            result = await agent.run(
                on_step_start=_on_step_start,
                on_step_end=_on_step_end,
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                ok=False,
                content=f"Browser Use failed: {exc}",
                error=type(exc).__name__,
            )

        # AgentHistoryList exposes final_result(), is_done(), has_errors(), errors().
        summary: str = ""
        try:
            fr = getattr(result, "final_result", None)
            summary = fr() if callable(fr) else (fr or "")
        except Exception:  # noqa: BLE001
            summary = ""
        has_errors = False
        errors: list[str | None] = []
        try:
            he = getattr(result, "has_errors", None)
            has_errors = bool(he() if callable(he) else he)
        except Exception:  # noqa: BLE001
            has_errors = False
        try:
            err_attr = getattr(result, "errors", None)
            raw_errors = err_attr() if callable(err_attr) else (err_attr or [])
            errors = [str(e) if e is not None else None for e in raw_errors]
        except Exception:  # noqa: BLE001
            errors = []
        try:
            is_done = getattr(result, "is_done", None)
            done = bool(is_done() if callable(is_done) else is_done)
        except Exception:  # noqa: BLE001
            done = False

        if has_errors and not summary:
            error_text = next((e for e in errors if e), "browser agent failed")
            return ToolResult(
                ok=False,
                content=f"Browser Use failed: {error_text[:500]}",
                error="browser_use_failed",
                structured={"errors": [e for e in errors if e][:5]},
            )
        if not done and not summary:
            return ToolResult(
                ok=False,
                content="Browser Use stopped without completing the task",
                error="browser_use_incomplete",
            )
        if not summary:
            summary = (
                getattr(result, "final_output", None)
                or getattr(result, "summary", None)
                or str(result)
            )
        return ToolResult(ok=True, content=str(summary)[:500])
