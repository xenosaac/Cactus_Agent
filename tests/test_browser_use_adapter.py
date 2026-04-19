from __future__ import annotations

import json
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


class _IncompleteWithErrorsHistory:
    def final_result(self) -> str | None:
        return None

    def has_errors(self) -> bool:
        return False

    def errors(self) -> list[str | None]:
        return [
            "BrowserType.connect_over_cdp: Protocol error "
            "(Browser.setDownloadBehavior): Browser context management is not supported."
        ]

    def is_done(self) -> bool:
        return False


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
async def test_browser_use_incomplete_history_with_errors_returns_tool_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import browser_use

    class _Agent:
        def __init__(self, **kwargs: Any) -> None:
            pass

        async def run(self, **kwargs: Any) -> _IncompleteWithErrorsHistory:
            return _IncompleteWithErrorsHistory()

    monkeypatch.setattr(browser_use, "Agent", _Agent)
    adapter = BrowserUseAdapter(llm=object(), browser=object(), bus=EventBus())

    result = await adapter.call({"task": "search for flights"})

    assert not result.ok
    assert result.error == "browser_use_failed"
    assert "Browser.setDownloadBehavior" in result.content
    assert result.structured == {
        "errors": [
            "BrowserType.connect_over_cdp: Protocol error "
            "(Browser.setDownloadBehavior): Browser context management is not supported."
        ]
    }


@pytest.mark.asyncio
async def test_browser_use_agent_receives_browser_session_kwarg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import browser_use
    from browser_use import BrowserSession

    seen_kwargs: dict[str, Any] = {}

    class _Agent:
        def __init__(self, **kwargs: Any) -> None:
            seen_kwargs.update(kwargs)

        async def run(self, **kwargs: Any) -> _SuccessfulHistory:
            return _SuccessfulHistory()

    monkeypatch.setattr(browser_use, "Agent", _Agent)
    browser_session = BrowserSession()
    adapter = BrowserUseAdapter(
        llm=object(),
        browser=browser_session,
        bus=EventBus(),
    )

    result = await adapter.call({"task": "search for flights"})

    assert result.ok
    assert seen_kwargs["browser_session"] is browser_session
    assert "browser" not in seen_kwargs


def test_browser_use_cdp_preflight_closes_internal_targets_and_opens_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    list_calls = {"count": 0}

    class _Response:
        def __init__(self, payload: object) -> None:
            self._payload = payload

        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(self._payload).encode("utf-8")

    def _fake_urlopen(request: Any, timeout: float) -> _Response:
        assert timeout == 2.0
        url = request.full_url
        method = request.get_method()
        calls.append((method, url))
        if url.endswith("/json/list"):
            list_calls["count"] += 1
            if list_calls["count"] == 1:
                return _Response(
                    [
                        {
                            "id": "internal-1",
                            "type": "page",
                            "url": "chrome://omnibox-popup.top-chrome/",
                        }
                    ]
                )
            return _Response([])
        return _Response({})

    monkeypatch.setattr(
        "voice_agent.agent.browser_use_adapter.urlopen",
        _fake_urlopen,
    )
    adapter = BrowserUseAdapter(llm=object(), browser=object(), bus=EventBus())

    adapter._prepare_cdp_targets("http://127.0.0.1:9222")

    assert ("GET", "http://127.0.0.1:9222/json/close/internal-1") in calls
    assert ("PUT", "http://127.0.0.1:9222/json/new?about%3Ablank") in calls


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
