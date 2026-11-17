"""The login browser: remembered profile, silent renewal and handing over to a person."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from mcp_usc import campus
from mcp_usc.settings import Settings


def _settings(tmp_path: Path, channel: str = "auto") -> Settings:
    return Settings(
        moodle_url="https://cv.usc.es",
        moodle_token=None,
        browser_channel=channel,
        browser_profile_dir=tmp_path / "profile",
        exam_sources=(),
    )


class _Engine:
    def __init__(self, works: set[str | None]) -> None:
        self.works = works
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def launch_persistent_context(self, folder: str, **options: Any) -> str:
        self.calls.append((folder, options))
        if options.get("channel") not in self.works:
            raise RuntimeError("not installed")
        Path(folder).mkdir(parents=True, exist_ok=True)
        return f"context:{Path(folder).name}"


class _Playwright:
    def __init__(self, chromium: set[str | None], webkit: bool = False) -> None:
        self.chromium = _Engine(chromium)
        self.webkit = _Engine({None} if webkit else set())
        self.firefox = _Engine(set())


async def test_login_profile_is_remembered_per_browser(tmp_path):
    playwright = _Playwright({None})  # no Chrome or Edge: Playwright's own Chromium

    context = await campus._open_login_context(playwright, _settings(tmp_path), headless=True)

    assert context == "context:chromium"
    tried = [Path(folder).name for folder, _ in playwright.chromium.calls]
    assert tried == ["chrome", "msedge", "chromium"]
    assert all(options["headless"] is True for _, options in playwright.chromium.calls)
    profile = tmp_path / "profile" / "chromium"
    assert profile.is_dir()
    if os.name != "nt":  # Windows has no POSIX modes
        assert (profile.stat().st_mode & 0o777) == 0o700
    assert not (tmp_path / "profile" / "chrome").exists()


async def test_login_profile_falls_back_to_webkit_then_gives_up(tmp_path):
    with_webkit = _Playwright(set(), webkit=True)
    context = await campus._open_login_context(with_webkit, _settings(tmp_path), headless=False)
    assert context == "context:webkit"

    with pytest.raises(campus.LoginBrowserUnavailable):
        await campus._open_login_context(_Playwright(set()), _settings(tmp_path), headless=False)


class _Locator:
    def __init__(self, visible: bool) -> None:
        self.visible = visible

    @property
    def first(self) -> _Locator:
        return self

    async def is_visible(self, timeout: int = 0) -> bool:
        return self.visible


class _Page:
    def __init__(self, url: str, selectors: set[str]) -> None:
        self.url = url
        self.selectors = selectors

    def locator(self, selector: str) -> _Locator:
        return _Locator(selector in self.selectors)


class _Context:
    def __init__(self, *pages: _Page) -> None:
        self.pages = list(pages)


async def test_microsoft_asking_for_the_account_needs_a_person():
    asking = _Context(_Page("https://login.microsoftonline.com/x", {"input[name='loginfmt']"}))
    passing = _Context(_Page("https://login.microsoftonline.com/x", set()))
    on_campus = _Context(_Page("https://cv.usc.es/my/", {"input[type='password']"}))

    assert await campus._waits_for_person(asking, "cv.usc.es") is True
    assert await campus._waits_for_person(passing, "cv.usc.es") is False
    assert await campus._waits_for_person(on_campus, "cv.usc.es") is False


async def test_silent_renewal_stores_only_an_authenticated_session(tmp_path, monkeypatch):
    stored: list[tuple[str, str]] = []

    class _Store:
        def set(self, name: str, value: str) -> None:
            stored.append((name, value))

    monkeypatch.setattr(campus, "CredentialStore", _Store)
    seen: list[dict[str, Any]] = []

    async def gives_up(settings, **options):
        seen.append(options)
        return ""

    monkeypatch.setattr(campus, "_capture_session", gives_up)
    assert await campus.renew_session_silently(_settings(tmp_path)) is False
    assert stored == []
    assert seen == [{"headless": True, "timeout": 45, "give_up_when_asked": True}]

    async def renewed(settings, **options):
        return "fresh-cookie"

    monkeypatch.setattr(campus, "_capture_session", renewed)
    assert await campus.renew_session_silently(_settings(tmp_path)) is True
    assert stored == [(campus.SESSION_CREDENTIAL_NAME, "fresh-cookie")]


async def test_window_login_waits_for_the_person(tmp_path, monkeypatch):
    seen: list[dict[str, Any]] = []

    async def capture(settings, **options):
        seen.append(options)
        return ""

    monkeypatch.setattr(campus, "_capture_session", capture)
    with pytest.raises(TimeoutError):
        await campus.interactive_login(_settings(tmp_path), timeout=60)
    assert seen == [{"headless": False, "timeout": 60, "give_up_when_asked": False}]
