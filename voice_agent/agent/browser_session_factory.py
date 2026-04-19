"""Browser Use session construction from app settings."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from voice_agent.config import Settings

log = logging.getLogger(__name__)


def create_browser_session(settings: Settings) -> object:
    """Create a Browser Use session with optional Chrome profile/CDP settings."""
    from browser_use import BrowserProfile, BrowserSession

    profile_kwargs: dict[str, Any] = {
        "headless": False,
        "profile_directory": settings.browser_profile_directory,
        "keep_alive": settings.browser_keep_alive,
    }
    if settings.browser_user_data_dir is not None:
        profile_kwargs["user_data_dir"] = _expand(settings.browser_user_data_dir)
    if settings.browser_executable_path is not None:
        profile_kwargs["executable_path"] = _expand(settings.browser_executable_path)
    if settings.browser_channel:
        profile_kwargs["channel"] = settings.browser_channel

    session_kwargs: dict[str, Any] = {
        "browser_profile": BrowserProfile(**profile_kwargs)
    }
    if settings.browser_cdp_url:
        session_kwargs["cdp_url"] = settings.browser_cdp_url
    if settings.browser_pid is not None:
        session_kwargs["browser_pid"] = settings.browser_pid

    _log_browser_mode(settings)
    return BrowserSession(**session_kwargs)


def _expand(path: Path) -> Path:
    return path.expanduser().resolve()


def _log_browser_mode(settings: Settings) -> None:
    if settings.browser_cdp_url:
        log.info("Browser Use will connect over CDP: %s", settings.browser_cdp_url)
        return
    if settings.browser_pid is not None:
        log.info("Browser Use will connect to browser pid=%s", settings.browser_pid)
        return
    if settings.browser_user_data_dir:
        log.info(
            "Browser Use will launch Chrome profile dir=%s profile=%s",
            settings.browser_user_data_dir,
            settings.browser_profile_directory,
        )
        return
    if settings.browser_executable_path or settings.browser_channel:
        log.info(
            "Browser Use will launch browser executable=%s channel=%s",
            settings.browser_executable_path,
            settings.browser_channel,
        )
        return
    log.info("Browser Use will use its default managed Chromium profile")
