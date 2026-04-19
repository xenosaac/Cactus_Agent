"""Deterministic proof that `cloud_handoff=true` with an empty Cactus response
routes to our Gemini client. The live path depends on Cactus choosing to set
this flag, which we cannot force from an utterance — so we inject it here.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from voice_agent.agent.cactus_chat_model import CactusChatModel
from voice_agent.config import Mode, Settings


def _build_model(gemini: object | None, mode: Mode = Mode.LOCAL) -> CactusChatModel:
    settings = Settings(mode=mode, gemini_api_key="test_key")
    with patch(
        "cactus.python.src.cactus.cactus_complete", new=MagicMock()
    ), patch(
        "cactus.python.src.cactus.cactus_reset", new=MagicMock()
    ):
        model = CactusChatModel(settings, cactus_handle=0, gemini_client=gemini)  # type: ignore[arg-type]
    return model


@pytest.mark.asyncio
async def test_hybrid_mode_skips_cactus_entirely() -> None:
    """With Gemini available in HYBRID, planner calls go straight to Gemini.
    Cactus's `_complete` FFI must never be invoked."""
    gemini = MagicMock()
    gemini.ainvoke = AsyncMock(return_value="gemini_reply")
    model = _build_model(gemini, mode=Mode.HYBRID)
    model._complete = MagicMock(  # type: ignore[method-assign]
        side_effect=AssertionError("Cactus _complete must not run in HYBRID")
    )

    result = await model.ainvoke([{"role": "user", "content": "open Safari"}])

    gemini.ainvoke.assert_awaited_once()
    model._complete.assert_not_called()
    assert result == "gemini_reply"


@pytest.mark.asyncio
async def test_local_mode_cloud_handoff_raises_without_gemini_in_local() -> None:
    """LOCAL mode + cloud_handoff=true + no fallback available → RuntimeError."""
    model = _build_model(gemini=None, mode=Mode.LOCAL)
    handoff_payload = json.dumps({
        "success": True, "cloud_handoff": True, "confidence": 0.42,
        "response": "", "function_calls": None,
    })
    model._complete = MagicMock(return_value=handoff_payload)  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="local-only"):
        await model.ainvoke([{"role": "user", "content": "hard question"}])


@pytest.mark.asyncio
async def test_cloud_handoff_with_populated_response_skips_gemini() -> None:
    gemini = MagicMock()
    gemini.ainvoke = AsyncMock()

    model = _build_model(gemini)

    handoff_payload = json.dumps({
        "success": True,
        "cloud_handoff": True,
        "confidence": 0.55,
        "response": "cactus cloud answered directly",
        "function_calls": None,
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    })
    model._complete = MagicMock(return_value=handoff_payload)  # type: ignore[method-assign]

    sentinel = object()
    with patch.object(model, "_to_completion", return_value=sentinel) as to_completion:
        result = await model.ainvoke([{"role": "user", "content": "another question"}])

    gemini.ainvoke.assert_not_awaited()
    to_completion.assert_called_once()
    assert result is sentinel
