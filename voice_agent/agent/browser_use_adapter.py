"""Browser Use Agent wrapped as ONE tool.

The outer orchestrator treats web navigation as a single tool call; internally
Browser Use runs its own multi-step loop. We bridge Browser Use step hooks into
our EventBus so the HUD sees sub-step progress ("Opening page", "Clicking link",
"Reading result").
"""
from __future__ import annotations

import json
import logging
import re
from json import JSONDecodeError
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from voice_agent.agent.tool_router import ToolResult
from voice_agent.events import AgentEvent, EventBus, EventType

log = logging.getLogger(__name__)

_ARTIFACT_POINTER_RE = re.compile(
    r"\b(attached|saved|review|results\.md|todo\.md)\b",
    re.IGNORECASE,
)
_ARTIFACT_PATH_RE = re.compile(r"(/(?:[^ \n\r\t]+/)*(?:results|todo)\.md)")
_CDP_INTERNAL_PAGE_PREFIXES = ("chrome://omnibox-popup",)
_CDP_UNUSABLE_PAGE_PREFIXES = (
    "chrome://",
    "chrome-extension://",
    "devtools://",
)


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
            from browser_use import Agent, BrowserSession
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

        browser_kwargs: dict[str, object]
        if isinstance(self._browser, BrowserSession):
            try:
                await self._prepare_browser_session(self._browser)
            except Exception as exc:  # noqa: BLE001
                return ToolResult(
                    ok=False,
                    content=f"Could not prepare Browser Use session: {exc}",
                    error="browser_use_session",
                )
            browser_kwargs = {"browser_session": self._browser}
        else:
            browser_kwargs = {"browser": self._browser}

        try:
            agent = Agent(
                task=task,
                llm=self._llm,
                **browser_kwargs,
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

        nonempty_errors = [e for e in errors if e]
        if (has_errors or nonempty_errors) and not summary:
            error_text = next((e for e in errors if e), "browser agent failed")
            return ToolResult(
                ok=False,
                content=f"Browser Use failed: {error_text[:500]}",
                error="browser_use_failed",
                structured={"errors": nonempty_errors[:5]},
            )
        if not done and not summary:
            if nonempty_errors:
                return ToolResult(
                    ok=False,
                    content=(
                        "Browser Use stopped without completing the task: "
                        f"{nonempty_errors[0][:500]}"
                    ),
                    error="browser_use_incomplete",
                    structured={"errors": nonempty_errors[:5]},
                )
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

    async def _prepare_browser_session(self, browser_session: object) -> None:
        """Normalize reusable browser sessions before Browser Use copies them."""
        cdp_url = getattr(browser_session, "cdp_url", None)
        if isinstance(cdp_url, str) and cdp_url:
            self._prepare_cdp_targets(cdp_url)

        profile = getattr(browser_session, "browser_profile", None)
        keep_alive = getattr(profile, "keep_alive", None)
        browser_pid = getattr(browser_session, "browser_pid", None)
        external_browser = bool(cdp_url or browser_pid)
        initialized = bool(getattr(browser_session, "initialized", False))
        if (external_browser or keep_alive is True) and not initialized:
            start = getattr(browser_session, "start", None)
            if callable(start):
                await start()
                initialized = True
        if initialized:
            await self._select_usable_browser_page(browser_session)

    def _prepare_cdp_targets(self, cdp_url: str) -> None:
        """Close Chrome UI-only CDP targets and ensure a normal page exists."""
        targets = self._read_cdp_targets(cdp_url)
        if targets is None:
            return

        for target in targets:
            url = str(target.get("url") or "")
            target_id = str(target.get("id") or "")
            target_type = str(target.get("type") or "")
            if (
                target_id
                and target_type == "page"
                and url.startswith(_CDP_INTERNAL_PAGE_PREFIXES)
            ):
                self._cdp_request(cdp_url, f"/json/close/{target_id}")

        targets = self._read_cdp_targets(cdp_url) or []
        if not any(self._target_is_usable_page(target) for target in targets):
            encoded_url = quote("about:blank", safe="")
            self._cdp_request(cdp_url, f"/json/new?{encoded_url}", method="PUT")

    def _read_cdp_targets(self, cdp_url: str) -> list[dict[str, Any]] | None:
        raw = self._cdp_request(cdp_url, "/json/list")
        if raw is None:
            return None
        try:
            data = raw.decode("utf-8")
            parsed = json.loads(data)
        except (UnicodeDecodeError, JSONDecodeError):
            return None
        if not isinstance(parsed, list):
            return None
        return [item for item in parsed if isinstance(item, dict)]

    def _cdp_request(
        self, cdp_url: str, path: str, *, method: str = "GET"
    ) -> bytes | None:
        url = f"{cdp_url.rstrip('/')}{path}"
        request = Request(url, method=method)
        try:
            with urlopen(request, timeout=2.0) as response:  # noqa: S310
                return response.read()
        except URLError as exc:
            if method == "PUT":
                return self._cdp_request(cdp_url, path, method="GET")
            log.debug("CDP preflight request failed url=%s error=%s", url, exc)
            return None
        except TimeoutError:
            log.debug("CDP preflight request timed out url=%s", url)
            return None

    def _target_is_usable_page(self, target: dict[str, Any]) -> bool:
        if str(target.get("type") or "") != "page":
            return False
        return self._is_usable_page_url(str(target.get("url") or ""))

    async def _select_usable_browser_page(self, browser_session: object) -> None:
        context = getattr(browser_session, "browser_context", None)
        pages = list(getattr(context, "pages", []) or [])
        for page in pages:
            if self._is_usable_page_url(str(getattr(page, "url", "") or "")):
                browser_session.agent_current_page = page
                browser_session.human_current_page = page
                bring_to_front = getattr(page, "bring_to_front", None)
                if callable(bring_to_front):
                    await bring_to_front()
                return

        new_page = getattr(context, "new_page", None)
        if callable(new_page):
            page = await new_page()
            goto = getattr(page, "goto", None)
            if callable(goto):
                await goto("about:blank")
            browser_session.agent_current_page = page
            browser_session.human_current_page = page

    def _is_usable_page_url(self, url: str) -> bool:
        if not url:
            return False
        if url.startswith("about:blank"):
            return True
        return not url.startswith(_CDP_UNUSABLE_PAGE_PREFIXES)

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
