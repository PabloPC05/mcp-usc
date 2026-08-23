from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from mcp_usc.campus import (
    SESSION_CREDENTIAL_NAME,
    AuthenticationRequired,
    HttpSessionMoodleGateway,
)
from mcp_usc.settings import Settings


class MemoryCredentialStore:
    def __init__(self, value: str | None) -> None:
        self.value = value
        self.get_calls: list[str] = []
        self.set_calls: list[tuple[str, str]] = []

    def get(self, name: str) -> str | None:
        self.get_calls.append(name)
        return self.value

    def set(self, name: str, value: str) -> None:
        self.set_calls.append((name, value))
        self.value = value


def _settings() -> Settings:
    return Settings(
        moodle_url="https://cv.usc.es",
        moodle_token=None,
        browser_channel="chromium",
        browser_profile_dir=Path("unused"),
        exam_sources=(),
    )


def _dashboard_html() -> str:
    return """
    <html><head><title>Campus USC</title></head><body>
      <div class="usermenu"><span class="usertext">Ada</span></div>
      <script>window.M = {"sesskey":"abc123","userid":5};</script>
    </body></html>
    """


@respx.mock
async def test_session_status_uses_keyring_cookie_over_http_without_exposing_it() -> None:
    store = MemoryCredentialStore("session-secret")
    route = respx.get("https://cv.usc.es/my/").mock(
        return_value=httpx.Response(200, text=_dashboard_html())
    )

    result = await HttpSessionMoodleGateway(_settings(), store).status()  # type: ignore[arg-type]

    assert result == {
        "authenticated": True,
        "method": "moodle_http_session",
        "site_name": "Campus USC",
        "user_name": "Ada",
        "user_id": 5,
    }
    assert store.get_calls == [SESSION_CREDENTIAL_NAME]
    assert route.calls[0].request.headers["cookie"] == "MoodleSession=session-secret"
    assert "session-secret" not in str(result)
    assert "abc123" not in str(result)


@respx.mock
async def test_session_ajax_fetches_sesskey_from_dashboard_then_posts_json() -> None:
    store = MemoryCredentialStore("session-secret")
    dashboard = respx.get("https://cv.usc.es/my/").mock(
        return_value=httpx.Response(200, text=_dashboard_html())
    )
    ajax = respx.post(
        "https://cv.usc.es/lib/ajax/service.php",
        params={
            "sesskey": "abc123",
            "info": "core_course_get_enrolled_courses_by_timeline_classification",
        },
    ).mock(
        return_value=httpx.Response(
            200,
            json=[{"error": False, "data": {"courses": [{"id": 7, "fullname": "Álxebra"}]}}],
        )
    )

    courses = await HttpSessionMoodleGateway(  # type: ignore[arg-type]
        _settings(), store
    ).list_courses()

    assert courses == [{"id": 7, "fullname": "Álxebra"}]
    assert dashboard.call_count == 1
    assert ajax.call_count == 1
    request = ajax.calls[0].request
    assert request.headers["cookie"] == "MoodleSession=session-secret"
    assert request.headers["x-requested-with"] == "XMLHttpRequest"
    assert request.url.params["sesskey"] == "abc123"
    assert request.url.params["info"] == (
        "core_course_get_enrolled_courses_by_timeline_classification"
    )
    assert request.read().decode() == (
        '[{"index":0,"methodname":'
        '"core_course_get_enrolled_courses_by_timeline_classification",'
        '"args":{"classification":"inprogress","limit":0,"offset":0,'
        '"sort":"fullname","customfieldname":"","customfieldvalue":""}}]'
    )


@respx.mock
async def test_session_redirect_to_login_becomes_authentication_error() -> None:
    store = MemoryCredentialStore("expired-secret")
    respx.get("https://cv.usc.es/my/").mock(
        return_value=httpx.Response(302, headers={"location": "/login/index.php"})
    )

    with pytest.raises(AuthenticationRequired, match="ha caducado"):
        await HttpSessionMoodleGateway(_settings(), store).status()  # type: ignore[arg-type]


@respx.mock
async def test_rotated_moodle_cookie_is_saved_back_to_keyring() -> None:
    store = MemoryCredentialStore("old-session")
    respx.get("https://cv.usc.es/my/").mock(
        return_value=httpx.Response(
            200,
            text=_dashboard_html(),
            headers={"set-cookie": "MoodleSession=new-session; Secure; HttpOnly; Path=/"},
        )
    )

    await HttpSessionMoodleGateway(_settings(), store).status()  # type: ignore[arg-type]

    assert store.set_calls == [(SESSION_CREDENTIAL_NAME, "new-session")]


@respx.mock
async def test_message_contract_uses_plain_text_and_only_mocked_http() -> None:
    store = MemoryCredentialStore("session-secret")
    respx.get("https://cv.usc.es/my/").mock(
        return_value=httpx.Response(200, text=_dashboard_html())
    )
    ajax = respx.post(
        "https://cv.usc.es/lib/ajax/service.php",
        params={"sesskey": "abc123", "info": "core_message_send_instant_messages"},
    ).mock(
        return_value=httpx.Response(
            200,
            json=[{"error": False, "data": [{"msgid": 99}]}],
        )
    )

    result = await HttpSessionMoodleGateway(  # type: ignore[arg-type]
        _settings(), store
    ).send_message(42, "Hola <b>literal</b>")

    payload = json.loads(ajax.calls[0].request.content)
    message = payload[0]["args"]["messages"][0]
    assert message["touserid"] == 42
    assert message["text"] == "Hola <b>literal</b>"
    assert message["textformat"] == 2
    assert result["message_id"] == 99
