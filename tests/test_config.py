from __future__ import annotations

import pytest

from voice_agent.config import Mode, Settings


def test_local_mode_does_not_require_key() -> None:
    s = Settings(mode=Mode.LOCAL, gemini_api_key=None)
    s.require_hybrid_keys()  # should not raise


def test_hybrid_mode_requires_key() -> None:
    s = Settings(mode=Mode.HYBRID, gemini_api_key=None)
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        s.require_hybrid_keys()


def test_hybrid_mode_accepts_key() -> None:
    s = Settings(mode=Mode.HYBRID, gemini_api_key="test_key_123")
    s.require_hybrid_keys()  # should not raise


def test_wake_phrases_default() -> None:
    s = Settings(mode=Mode.LOCAL)
    assert "hey cactus" in s.wake_phrases


def test_voice_debug_defaults_off() -> None:
    s = Settings(mode=Mode.LOCAL)
    assert s.voice_debug is False
    assert s.vad_max_speech_ms == 12_000
    assert s.vad_threshold == 0.65
    assert s.vad_min_segment_rms == 0.030
    assert s.voice_debug_dir.name == "voice_debug"


def test_browser_model_defaults_to_stable_flash() -> None:
    s = Settings(mode=Mode.LOCAL)
    assert s.gemini_model == "gemini-3-pro-preview"
    assert s.gemini_vision_model is None
    assert s.browser_model == "gemini-2.5-flash"
