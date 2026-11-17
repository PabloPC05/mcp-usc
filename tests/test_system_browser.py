"""Login through your own Chromium browser (DevTools), against a pretend browser."""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_usc import campus, system_browser
from mcp_usc.settings import Settings

FAKE = str(Path(__file__).parent / "data" / "fake_chromium.py")


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        moodle_url="https://cv.usc.es",
        moodle_token=None,
        browser_channel="auto",
        browser_profile_dir=tmp_path / "profile",
        exam_sources=(),
        request_timeout_seconds=5,
    )


@pytest.fixture(autouse=True)
def _only_good_is_a_session(monkeypatch):
    async def authenticated(settings, cookie):
        return cookie == "good"

    monkeypatch.setattr(campus, "_cookie_is_authenticated", authenticated)


async def test_the_session_is_read_once_moodle_accepts_it(tmp_path):
    cookie = await system_browser.capture_session(
        _settings(tmp_path), headless=False, timeout=30, browser=("fake", FAKE)
    )
    assert cookie == "good"
    profile = tmp_path / "profile" / "sistema-fake"
    assert profile.is_dir()


async def test_silent_renewal_gives_up_when_nobody_logs_in(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_STAY_GUEST", "1")
    cookie = await system_browser.capture_session(
        _settings(tmp_path), headless=True, timeout=30, give_up_after=2, browser=("fake", FAKE)
    )
    assert cookie == ""


async def test_no_browser_means_another_way(tmp_path, monkeypatch):
    monkeypatch.setattr(system_browser, "find_browser", lambda: None)
    with pytest.raises(campus.LoginBrowserUnavailable):
        await system_browser.capture_session(_settings(tmp_path), headless=True, timeout=5)


def test_an_explicit_browser_wins(tmp_path, monkeypatch):
    browser = tmp_path / "my-chrome"
    browser.write_text("")
    monkeypatch.setenv("USC_LOGIN_BROWSER", str(browser))
    assert system_browser.find_browser() == ("custom", str(browser))
    monkeypatch.setenv("USC_LOGIN_BROWSER", str(tmp_path / "missing"))
    assert system_browser.find_browser() is None


async def test_login_prefers_your_browser_over_playwright(tmp_path, monkeypatch):
    used = []

    async def yours(settings, **options):
        used.append(("system", options["headless"]))
        return "good"

    async def playwright(settings, **options):
        used.append(("playwright", options["headless"]))
        return "good"

    stored = []

    class _Store:
        def set(self, name, value):
            stored.append(value)

    monkeypatch.setattr(system_browser, "find_browser", lambda: ("chrome", "/x"))
    monkeypatch.setattr(system_browser, "capture_session", yours)
    monkeypatch.setattr(campus, "_capture_session", playwright)
    monkeypatch.setattr(campus, "CredentialStore", _Store)
    assert await campus.renew_session_silently(_settings(tmp_path)) is True
    monkeypatch.setattr(system_browser, "find_browser", lambda: None)
    await campus.interactive_login(_settings(tmp_path), timeout=60)
    assert used == [("system", True), ("playwright", False)] and stored == ["good", "good"]
