from __future__ import annotations

from pathlib import Path

from voice_agent.agent.browser_session_factory import create_browser_session
from voice_agent.config import Mode, Settings


def test_browser_session_uses_cdp_and_chrome_profile(tmp_path: Path) -> None:
    chrome_path = tmp_path / "Google Chrome"
    settings = Settings(
        mode=Mode.LOCAL,
        browser_cdp_url="http://127.0.0.1:9222",
        browser_user_data_dir=tmp_path,
        browser_profile_directory="Profile 1",
        browser_executable_path=chrome_path,
        browser_keep_alive=True,
    )

    session = create_browser_session(settings)

    assert session.cdp_url == "http://127.0.0.1:9222"
    profile = session.browser_profile
    assert profile.user_data_dir == tmp_path.resolve()
    assert profile.profile_directory == "Profile 1"
    assert profile.executable_path == chrome_path.resolve()
    assert profile.keep_alive is True


def test_browser_session_leaves_cdp_keep_alive_unset_by_default() -> None:
    settings = Settings(
        mode=Mode.LOCAL,
        browser_cdp_url="http://127.0.0.1:9222",
    )

    session = create_browser_session(settings)

    assert session.cdp_url == "http://127.0.0.1:9222"
    assert session.browser_profile.keep_alive is None


def test_browser_session_defaults_to_managed_profile() -> None:
    session = create_browser_session(Settings(mode=Mode.LOCAL))

    assert session.cdp_url is None
    assert session.browser_pid is None
    profile = session.browser_profile
    assert profile.profile_directory == "Default"
    assert profile.headless is False
    assert profile.keep_alive is None
