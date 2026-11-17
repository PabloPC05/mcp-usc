"""Without an OS keyring (a server, a VM over SSH) the session lives in a private file."""

from __future__ import annotations

import os
import stat

import httpx
import pytest
import respx
from keyring.errors import NoKeyringError

from mcp_usc import credentials
from mcp_usc.credentials import CredentialStore
from mcp_usc.session_auth import SESSION_CREDENTIAL_NAME, end_campus_session
from mcp_usc.settings import Settings


@pytest.fixture
def no_keyring(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise NoKeyringError("no backend")

    monkeypatch.setattr(credentials.keyring, "get_password", fail)
    monkeypatch.setattr(credentials.keyring, "set_password", fail)
    monkeypatch.setattr(credentials.keyring, "delete_password", fail)
    monkeypatch.setenv("USC_CREDENTIALS_FILE", str(tmp_path / "conf" / "credenciales.json"))
    monkeypatch.delenv("USC_MOODLE_SESSION", raising=False)
    return tmp_path / "conf" / "credenciales.json"


def test_private_file_when_there_is_no_keyring(no_keyring):
    store = CredentialStore()
    assert store.get(SESSION_CREDENTIAL_NAME) is None
    store.set(SESSION_CREDENTIAL_NAME, "abc123")
    assert store.get(SESSION_CREDENTIAL_NAME) == "abc123"
    if os.name != "nt":
        assert stat.S_IMODE(no_keyring.stat().st_mode) == 0o600
        assert stat.S_IMODE(no_keyring.parent.stat().st_mode) == 0o700
    store.delete(SESSION_CREDENTIAL_NAME)
    assert store.get(SESSION_CREDENTIAL_NAME) is None and not no_keyring.exists()


class _Store:
    def __init__(self, value: str | None) -> None:
        self.value = value

    def get(self, name: str) -> str | None:
        return self.value

    def delete(self, name: str) -> None:
        self.value = None


def _settings() -> Settings:
    from pathlib import Path

    return Settings(
        moodle_url="https://cv.usc.es",
        moodle_token=None,
        browser_channel="auto",
        browser_profile_dir=Path("/tmp/none"),
        exam_sources=(),
    )


@respx.mock
async def test_logging_out_ends_the_session_on_the_campus_too(monkeypatch):
    monkeypatch.delenv("USC_MOODLE_SESSION", raising=False)
    respx.get("https://cv.usc.es/user/preferences.php").mock(
        return_value=httpx.Response(200, text='<input name="sesskey" value="abcdef123">')
    )
    logout = respx.get("https://cv.usc.es/login/logout.php").mock(
        return_value=httpx.Response(303, headers={"location": "https://cv.usc.es/"})
    )
    store = _Store("cookie-value")

    result = await end_campus_session(_settings(), credential_store=store)

    assert logout.called and logout.calls[0].request.url.params["sesskey"] == "abcdef123"
    assert result["remote_logout"] is True and result["local_session_removed"] is True
    assert store.value is None


@respx.mock
async def test_an_already_expired_session_is_just_forgotten(monkeypatch):
    monkeypatch.delenv("USC_MOODLE_SESSION", raising=False)
    respx.get("https://cv.usc.es/user/preferences.php").mock(
        return_value=httpx.Response(303, headers={"location": "https://cv.usc.es/login/"})
    )
    logout = respx.get("https://cv.usc.es/login/logout.php")
    store = _Store("old")

    result = await end_campus_session(_settings(), credential_store=store)

    assert not logout.called and result["remote_logout"] is False and store.value is None
