"""Browser Use Agent wrapped as ONE tool.

The outer orchestrator treats web navigation as a single tool call; internally
Browser Use runs its own multi-step loop. We bridge Browser Use step hooks into
our EventBus so the HUD sees sub-step progress ("Opening page", "Clicking link",
"Reading result").
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from voice_agent.agent.tool_router import ToolResult
from voice_agent.events import AgentEvent, EventBus, EventType

log = logging.getLogger(__name__)

_ARTIFACT_POINTER_RE = re.compile(
    r"\b(attached|saved|review|results\.md|todo\.md)\b",
    re.IGNORECASE,
)
_ARTIFACT_PATH_RE = re.compile(r"(/(?:[^ \n\r\t]+/)*(?:results|todo)\.md)")


class BrowserUseAdapter:
    """Browser Use ToolAdapter. Accepts a Browser Use-compatible LLM object."""

    name: str = "web_navigate"
    description: str = (
        "Use for ANY web task: open URL, read a page, fill a form, click "
        "buttons, navigate a website. Input: `task` (natural-language goal). "
        "This tool drives a real browser locally."
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
        content = self._display_content(agent=agent, result=result, summary=str(summary))
        return ToolResult(ok=True, content=content[:2000])

    def _display_content(self, *, agent: object, result: object, summary: str) -> str:
        """Return the user-facing Browser Use result, not just file pointers."""
        details = self._collect_artifact_text(agent=agent, result=result, summary=summary)
        if not details:
            return summary
        if summary and not _ARTIFACT_POINTER_RE.search(summary):
            return self._join_blocks([summary, details])
        return details

    def _collect_artifact_text(
        self, *, agent: object, result: object, summary: str
    ) -> str:
        blocks: list[str] = []
        blocks.extend(self._read_result_files(agent=agent, summary=summary))
        blocks.extend(self._extracted_content(result))
        return self._join_blocks(blocks)

    def _read_result_files(self, *, agent: object, summary: str) -> list[str]:
        texts: list[str] = []

        fs = getattr(agent, "file_system", None)
        get_file = getattr(fs, "get_file", None)
        if callable(get_file):
            for filename in ("results.md", "todo.md"):
                try:
                    file_obj = get_file(filename)
                    read = getattr(file_obj, "read", None)
                    text = read() if callable(read) else ""
                except Exception:  # noqa: BLE001
                    text = ""
                if isinstance(text, str) and text.strip():
                    texts.append(text)

        paths = self._candidate_artifact_paths(agent=agent, summary=summary)
        for path in paths:
            try:
                text = path.read_text(encoding="utf-8")
            except Exception:  # noqa: BLE001
                continue
            if text.strip():
                texts.append(text)

        return texts

    def _candidate_artifact_paths(
        self, *, agent: object, summary: str
    ) -> list[Path]:
        paths: list[Path] = []
        seen: set[Path] = set()

        def add(path: Path) -> None:
            try:
                resolved = path.expanduser().resolve()
            except Exception:  # noqa: BLE001
                resolved = path.expanduser()
            if resolved not in seen:
                seen.add(resolved)
                paths.append(resolved)

        for match in _ARTIFACT_PATH_RE.finditer(summary or ""):
            add(Path(match.group(1)))

        roots: list[Path] = []
        for obj in (agent, getattr(agent, "state", None), getattr(agent, "file_system", None)):
            if obj is None:
                continue
            for attr in ("file_system_path", "base_dir", "data_dir"):
                raw = getattr(obj, attr, None)
                if raw:
                    roots.append(Path(raw))

        for root in roots:
            for filename in ("results.md", "todo.md"):
                add(root / "browseruse_agent_data" / filename)
                add(root / filename)

        return paths

    def _extracted_content(self, result: object) -> list[str]:
        try:
            extracted = getattr(result, "extracted_content", None)
            raw = extracted() if callable(extracted) else (extracted or [])
        except Exception:  # noqa: BLE001
            return []

        if isinstance(raw, str):
            items = [raw]
        else:
            try:
                items = list(raw)
            except TypeError:
                items = []
        return [item for item in items if isinstance(item, str) and item.strip()]

    @staticmethod
    def _join_blocks(blocks: list[str]) -> str:
        out: list[str] = []
        seen: set[str] = set()
        for block in blocks:
            clean = BrowserUseAdapter._clean_text(block)
            if not clean or clean in seen:
                continue
            seen.add(clean)
            out.append(clean)
        return "\n\n".join(out)

    @staticmethod
    def _clean_text(text: str) -> str:
        lines: list[str] = []
        previous_blank = False
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if line.startswith("#"):
                line = line.lstrip("#").strip()
            if not line:
                if not previous_blank and lines:
                    lines.append("")
                previous_blank = True
                continue
            lines.append(line)
            previous_blank = False
        return "\n".join(lines).strip()
