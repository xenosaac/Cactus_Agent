from __future__ import annotations

from typing import Any

import pytest

from voice_agent.agent.browser_use_adapter import BrowserUseAdapter
from voice_agent.events import EventBus


class _FailedHistory:
    def final_result(self) -> str | None:
        return None

    def has_errors(self) -> bool:
        return True

    def errors(self) -> list[str | None]:
        return ["('', 502)\nStacktrace: provider failed"]

    def is_done(self) -> bool:
        return False


class _SuccessfulHistory:
    def final_result(self) -> str:
        return "Found flights"

    def has_errors(self) -> bool:
        return False

    def errors(self) -> list[str | None]:
        return []

    def is_done(self) -> bool:
        return True


@pytest.mark.asyncio
async def test_browser_use_failed_history_returns_tool_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import browser_use

    class _Agent:
        def __init__(self, **kwargs: Any) -> None:
            pass

        async def run(self, **kwargs: Any) -> _FailedHistory:
            return _FailedHistory()

    monkeypatch.setattr(browser_use, "Agent", _Agent)
    adapter = BrowserUseAdapter(llm=object(), browser=object(), bus=EventBus())

    result = await adapter.call({"task": "search for flights"})

    assert not result.ok
    assert result.error == "browser_use_failed"
    assert "502" in result.content
    assert result.structured == {
        "errors": ["('', 502)\nStacktrace: provider failed"]
    }


@pytest.mark.asyncio
async def test_browser_use_successful_history_returns_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import browser_use

    class _Agent:
        def __init__(self, **kwargs: Any) -> None:
            pass

        async def run(self, **kwargs: Any) -> _SuccessfulHistory:
            return _SuccessfulHistory()

    monkeypatch.setattr(browser_use, "Agent", _Agent)
    adapter = BrowserUseAdapter(llm=object(), browser=object(), bus=EventBus())

    result = await adapter.call({"task": "search for flights"})

    assert result.ok
    assert result.content == "Found flights"
