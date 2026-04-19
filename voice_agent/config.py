"""Runtime configuration. Loaded once at startup, immutable after."""
from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Mode(StrEnum):
    LOCAL = "local"
    HYBRID = "hybrid"


# Project root = parent of voice_agent/
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_WEIGHTS_ROOT = _PROJECT_ROOT / "cactus" / "weights"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="VA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    # Runtime
    mode: Mode = Mode.HYBRID

    # Cactus weights (default paths match `cactus download` layout)
    gemma4_weights: Path = _WEIGHTS_ROOT / "gemma-4-E4B-it"
    whisper_weights: Path = _WEIGHTS_ROOT / "whisper-small"
    vad_weights: Path = _WEIGHTS_ROOT / "silero-vad"

    # Optional small fallback planner
    use_small_planner_fallback: bool = False
    functiongemma_weights: Path = _WEIGHTS_ROOT / "functiongemma-270m-it"

    # Cloud (Hybrid mode only)
    gemini_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "gemini_api_key", "GEMINI_API_KEY", "VA_GEMINI_API_KEY"
        ),
    )
    gemini_model: str = "gemini-3-pro-preview"
    gemini_timeout_s: float = 30.0
    gemini_vision_model: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "gemini_vision_model", "GEMINI_VISION_MODEL", "VA_GEMINI_VISION_MODEL"
        ),
    )
    gemini_vision_timeout_s: float = 45.0
    gemini_vision_thinking_level: str = "low"
    browser_model: str = "gemini-2.5-flash"
    browser_temperature: float = 0.2
    browser_cdp_url: str | None = None
    browser_pid: int | None = None
    browser_user_data_dir: Path | None = None
    browser_profile_directory: str = "Default"
    browser_executable_path: Path | None = None
    browser_channel: str | None = None
    browser_keep_alive: bool = False

    # Voice layer
    wake_phrases: tuple[str, ...] = ("hey cactus", "okay cactus", "cactus,")
    sample_rate_hz: int = 16_000
    vad_silence_ms: int = 800
    vad_min_speech_ms: int = 250
    vad_frame_ms: int = 30
    vad_max_speech_ms: int = 12_000
    vad_threshold: float = 0.65
    vad_min_segment_rms: float = 0.030
    whisper_confirm_threshold: float = 0.75
    whisper_min_chunk_bytes: int = 480  # 30ms @ 16kHz int16 mono
    # 20s: Cactus Whisper-Small on CPU takes 4–8 s per ~5 s utterance.
    # First-call warmup sometimes adds 2–3 s. 5 s was too tight (first call
    # finished at 4.5 s, everything after timed out).
    whisper_timeout_s: float = 20.0

    # Agent
    max_agent_steps: int = 10
    tool_timeout_s: float = 90.0
    planner_max_tokens: int = 96
    planner_temperature: float = 0.2
    planner_top_p: float = 0.9
    vision_max_steps: int = 6
    vision_settle_ms: int = 350

    # MCP / OAuth
    google_oauth_credentials: Path = _PROJECT_ROOT / "credentials.json"
    google_oauth_token: Path = _PROJECT_ROOT / "token.json"

    # Demo assets
    demo_portal_url: str = "file:///tmp/demo-portal.html"

    # Voice debugging (records user speech when enabled)
    voice_debug: bool = False
    voice_debug_dir: Path = _PROJECT_ROOT / "logs" / "voice_debug"

    # HUD timing (visuals owned by claude design; these are behavior only)
    hud_visible_seconds_after_done: float = 3.0

    def require_hybrid_keys(self) -> None:
        if self.mode == Mode.HYBRID and not self.gemini_api_key:
            raise RuntimeError(
                "Hybrid mode requires GEMINI_API_KEY. "
                "Set it in .env or export it, or run with VA_MODE=local."
            )


def load_settings() -> Settings:
    s = Settings()
    s.require_hybrid_keys()
    return s
