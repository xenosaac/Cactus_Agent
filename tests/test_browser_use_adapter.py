from __future__ import annotations

from pathlib import Path
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


class _FilePointerHistory:
    def final_result(self) -> str:
        return "The details are in the attached results.md file."

    def has_errors(self) -> bool:
        return False

    def errors(self) -> list[str | None]:
        return []

    def is_done(self) -> bool:
        return True

    def extracted_content(self) -> list[str]:
        return []


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


@pytest.mark.asyncio
async def test_browser_use_success_reads_results_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import browser_use

    data_dir = tmp_path / "browseruse_agent_data"
    data_dir.mkdir()
    (data_dir / "results.md").write_text(
        "# Cheapest Flight\n\nAirline: Frontier\nPrice: $124\n",
        encoding="utf-8",
    )

    class _Agent:
        def __init__(self, **kwargs: Any) -> None:
            self.file_system_path = str(tmp_path)

        async def run(self, **kwargs: Any) -> _FilePointerHistory:
            return _FilePointerHistory()

    monkeypatch.setattr(browser_use, "Agent", _Agent)
    adapter = BrowserUseAdapter(llm=object(), browser=object(), bus=EventBus())

    result = await adapter.call({"task": "search for flights"})

    assert result.ok
    assert result.content == "Cheapest Flight\n\nAirline: Frontier\nPrice: $124"
