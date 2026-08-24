from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from mcp_usc.session_auth import (
    SESSION_CREDENTIAL_NAME,
    SessionImportError,
    forget_session_cookie,
    import_session_cookie,
)
from mcp_usc.settings import Settings


class MemoryStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def set(self, name: str, value: str) -> None:
        self.values[name] = value

    def delete(self, name: str) -> None:
        self.values.pop(name, None)


class FailingStore(MemoryStore):
    def set(self, name: str, value: str) -> None:
        raise RuntimeError(f"keyring rejected {value}")


def _settings() -> Settings:
    return Settings(
        moodle_url="https://cv.usc.es",
        moodle_token=None,
        browser_channel="chromium",
        browser_profile_dir=Path("unused"),
        exam_sources=(),
    )


def _authenticated_html() -> str:
    return """
    <html><head><title>Campus Virtual</title></head><body>
      <div class="usermenu"><span class="usertext">Pablo</span></div>
      <script>window.M = {"sesskey":"freshKey123","userid":42};</script>
    </body></html>
    """


@respx.mock
async def test_import_validates_then_stores_cookie_without_returning_secrets() -> None:
    cookie = "abcdef0123456789abcdef0123456789"
    route = respx.get("https://cv.usc.es/user/preferences.php").mock(
        return_value=httpx.Response(200, text=_authenticated_html())
    )
    store = MemoryStore()

    result = await import_session_cookie(_settings(), cookie, credential_store=store)

    assert result == {
        "authenticated": True,
        "method": "moodle_http_session",
        "user_id": 42,
        "user_name": "Pablo",
        "site_name": "Campus Virtual",
        "cookie_stored_in_os_keyring": True,
    }
    assert store.values == {SESSION_CREDENTIAL_NAME: cookie}
    assert route.calls[0].request.headers["cookie"] == f"MoodleSession={cookie}"
    assert cookie not in str(result)
    assert "freshKey123" not in str(result)


@respx.mock
async def test_import_rejects_login_redirect_without_storing_cookie() -> None:
    cookie = "abcdef0123456789abcdef0123456789"
    respx.get("https://cv.usc.es/user/preferences.php").mock(
        return_value=httpx.Response(303, headers={"location": "/login/index.php"})
    )
    store = MemoryStore()

    with pytest.raises(SessionImportError, match="caducado") as caught:
        await import_session_cookie(_settings(), cookie, credential_store=store)

    assert not store.values
    assert cookie not in str(caught.value)


@respx.mock
async def test_import_rejects_malformed_cookie_before_network() -> None:
    store = MemoryStore()

    with pytest.raises(SessionImportError, match="formato"):
        await import_session_cookie(
            _settings(), "value; MoodleSession=attacker", credential_store=store
        )

    assert not respx.calls
    assert not store.values


@respx.mock
async def test_import_persists_server_rotated_cookie() -> None:
    original = "abcdef0123456789abcdef0123456789"
    rotated = "9876543210abcdef9876543210abcdef"
    respx.get("https://cv.usc.es/user/preferences.php").mock(
        return_value=httpx.Response(
            200,
            text=_authenticated_html(),
            headers={"set-cookie": f"MoodleSession={rotated}; Secure; HttpOnly; Path=/"},
        )
    )
    store = MemoryStore()

    result = await import_session_cookie(_settings(), original, credential_store=store)

    assert store.values == {SESSION_CREDENTIAL_NAME: rotated}
    assert original not in str(result)
    assert rotated not in str(result)


@respx.mock
async def test_keyring_failure_redacts_cookie_and_has_no_exception_cause() -> None:
    cookie = "abcdef0123456789abcdef0123456789"
    respx.get("https://cv.usc.es/user/preferences.php").mock(
        return_value=httpx.Response(200, text=_authenticated_html())
    )

    with pytest.raises(SessionImportError, match="almacén seguro") as caught:
        await import_session_cookie(_settings(), cookie, credential_store=FailingStore())

    assert cookie not in str(caught.value)
    assert "freshKey123" not in str(caught.value)
    assert caught.value.__cause__ is None


def test_forget_session_only_removes_local_credential() -> None:
    store = MemoryStore()
    store.values[SESSION_CREDENTIAL_NAME] = "abcdef0123456789abcdef0123456789"

    result = forget_session_cookie(credential_store=store)

    assert SESSION_CREDENTIAL_NAME not in store.values
    assert result == {
        "authenticated": False,
        "local_session_removed": True,
        "remote_session_unchanged": True,
    }
