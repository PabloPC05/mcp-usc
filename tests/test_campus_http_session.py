from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from mcp_usc.campus import (
    SESSION_CREDENTIAL_NAME,
    AuthenticationRequired,
    CampusCapabilityUnavailable,
    CampusError,
    CampusMutationOutcomeUnknown,
    CampusProtocolError,
    HttpSessionMoodleGateway,
    _activities_from_sections,
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


def test_assignment_html_fallback_never_relabels_cmid_as_instance_id() -> None:
    sections = {
        7: [
            {
                "modules": [
                    {"id": 11, "modname": "assign", "name": "Tarea"},
                    {"id": 12, "modname": "quiz", "name": "Parcial"},
                ]
            }
        ]
    }

    assignment = _activities_from_sections(sections, "assign")[0]
    quiz = _activities_from_sections(sections, "quiz")[0]

    assert assignment["id"] is None
    assert assignment["cmid"] == 11
    assert assignment["id_is_course_module"] is False
    assert quiz["id"] == 12
    assert quiz["id_is_course_module"] is True


def _settings() -> Settings:
    return Settings(
        moodle_url="https://cv.usc.es",
        moodle_token=None,
        browser_channel="chromium",
        browser_profile_dir=Path("unused"),
        exam_sources=(),
    )


def _session_context_html() -> str:
    return """
    <html><head><title>Campus USC</title></head><body>
      <div class="usermenu"><span class="usertext">Ada</span></div>
      <script>window.M = {"sesskey":"abc123","userid":5};</script>
    </body></html>
    """


async def test_session_rejects_rest_only_contextual_actions_before_any_http() -> None:
    gateway = HttpSessionMoodleGateway(  # type: ignore[arg-type]
        _settings(), MemoryCredentialStore("session-secret")
    )

    with pytest.raises(CampusCapabilityUnavailable, match="token REST"):
        await gateway.require_functions(
            {
                "core_calendar_get_calendar_access_information",
                "core_calendar_create_calendar_events",
                "core_question_update_flag",
            }
        )


@respx.mock
async def test_session_status_uses_non_stateful_preferences_context() -> None:
    store = MemoryCredentialStore("session-secret")
    route = respx.get("https://cv.usc.es/user/preferences.php").mock(
        return_value=httpx.Response(200, text=_session_context_html())
    )
    dashboard = respx.get("https://cv.usc.es/my/").mock(
        return_value=httpx.Response(200, text="must not be requested")
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
    assert dashboard.call_count == 0
    assert "session-secret" not in str(result)
    assert "abc123" not in str(result)


@respx.mock
async def test_session_ajax_fetches_sesskey_from_preferences_then_posts_json() -> None:
    store = MemoryCredentialStore("session-secret")
    preferences = respx.get("https://cv.usc.es/user/preferences.php").mock(
        return_value=httpx.Response(200, text=_session_context_html())
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
    assert preferences.call_count == 1
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
async def test_session_ajax_rejects_oversized_json_before_parsing() -> None:
    store = MemoryCredentialStore("session-secret")
    respx.get("https://cv.usc.es/user/preferences.php").mock(
        return_value=httpx.Response(200, text=_session_context_html())
    )
    respx.post("https://cv.usc.es/lib/ajax/service.php").mock(
        return_value=httpx.Response(200, content=b"x" * (5 * 1024 * 1024 + 1))
    )

    with pytest.raises(CampusProtocolError, match="límite de bytes"):
        await HttpSessionMoodleGateway(_settings(), store).list_courses()  # type: ignore[arg-type]


@respx.mock
async def test_session_redirect_to_login_becomes_authentication_error() -> None:
    store = MemoryCredentialStore("expired-secret")
    respx.get("https://cv.usc.es/user/preferences.php").mock(
        return_value=httpx.Response(302, headers={"location": "/login/index.php"})
    )

    with pytest.raises(AuthenticationRequired, match="ha caducado"):
        await HttpSessionMoodleGateway(_settings(), store).status()  # type: ignore[arg-type]


@respx.mock
async def test_rotated_moodle_cookie_is_saved_back_to_keyring() -> None:
    store = MemoryCredentialStore("old-session")
    respx.get("https://cv.usc.es/user/preferences.php").mock(
        return_value=httpx.Response(
            200,
            text=_session_context_html(),
            headers={"set-cookie": "MoodleSession=new-session; Secure; HttpOnly; Path=/"},
        )
    )

    await HttpSessionMoodleGateway(_settings(), store).status()  # type: ignore[arg-type]

    assert store.set_calls == [(SESSION_CREDENTIAL_NAME, "new-session")]


@respx.mock
async def test_message_contract_uses_plain_text_and_only_mocked_http() -> None:
    store = MemoryCredentialStore("session-secret")
    respx.get("https://cv.usc.es/user/preferences.php").mock(
        return_value=httpx.Response(200, text=_session_context_html())
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


@respx.mock
async def test_session_file_download_converts_webservice_url_and_uses_cookie() -> None:
    store = MemoryCredentialStore("session-secret")
    route = respx.get("https://cv.usc.es/pluginfile.php/7/mod_resource/content/1/tema.txt").mock(
        return_value=httpx.Response(200, content=b"apuntes", headers={"content-type": "text/plain"})
    )

    content, media_type, source_url = await HttpSessionMoodleGateway(  # type: ignore[arg-type]
        _settings(), store
    ).fetch_file(
        "https://cv.usc.es/webservice/pluginfile.php/7/mod_resource/content/1/tema.txt",
        1024,
    )

    assert route.calls[0].request.headers["cookie"] == "MoodleSession=session-secret"
    assert content == b"apuntes"
    assert media_type == "text/plain"
    assert source_url == "https://cv.usc.es/pluginfile.php/7/mod_resource/content/1/tema.txt"


@respx.mock
async def test_session_form_adapter_never_follows_redirect_after_post() -> None:
    store = MemoryCredentialStore("session-secret")
    form_html = """
    <form method="post" action="/mod/assign/view.php">
      <input type="hidden" name="id" value="17">
      <input type="hidden" name="action" value="savesubmission">
      <input type="hidden" name="sesskey" value="abc123">
      <input type="hidden" name="onlinetext_editor[format]" value="1">
      <textarea name="onlinetext_editor[text]">Borrador</textarea>
    </form>
    """
    respx.get(
        "https://cv.usc.es/mod/assign/view.php",
        params={"id": 17, "action": "editsubmission"},
    ).mock(return_value=httpx.Response(200, text=form_html))
    posted = respx.post("https://cv.usc.es/mod/assign/view.php").mock(
        return_value=httpx.Response(303, headers={"location": "/mod/assign/view.php?id=17"})
    )
    redirected = respx.get("https://cv.usc.es/mod/assign/view.php", params={"id": 17}).mock(
        return_value=httpx.Response(200, text="<p>Entrega actualizada</p>")
    )
    gateway = HttpSessionMoodleGateway(_settings(), store)  # type: ignore[arg-type]

    result = await gateway.session_forms().save_assignment(
        17,
        {"onlinetext_editor[text]": "Respuesta"},
        confirmed=True,
    )

    assert result["request_sent"] is True
    assert result["outcome"] == "unknown"
    assert posted.call_count == 1
    assert redirected.call_count == 0
    assert result["http_status"] == 303
    assert b"sesskey=abc123" in posted.calls[0].request.content
    assert b"onlinetext_editor%5Bformat%5D=2" in posted.calls[0].request.content
    assert posted.calls[0].request.headers["cookie"] == "MoodleSession=session-secret"
    assert "abc123" not in str(result)


@respx.mock
async def test_auth_redirect_does_not_persist_deleted_cookie() -> None:
    store = MemoryCredentialStore("expired-secret")
    route = respx.get("https://cv.usc.es/user/preferences.php").mock(
        return_value=httpx.Response(
            302,
            headers={
                "location": "https://login.microsoftonline.com/common/oauth2/authorize",
                "set-cookie": "MoodleSession=deleted; Secure; HttpOnly; Path=/",
            },
        )
    )

    with pytest.raises(AuthenticationRequired):
        await HttpSessionMoodleGateway(_settings(), store).status()  # type: ignore[arg-type]

    assert route.call_count == 1
    assert store.set_calls == []


@pytest.mark.parametrize(
    "url",
    [
        "https://cv.usc.es/mod/assign/../../admin/index.php",
        "https://cv.usc.es/mod/assign/%2e%2e/%2e%2e/admin/index.php",
    ],
)
@respx.mock
async def test_form_url_rejects_dot_segments_before_http(url: str) -> None:
    route = respx.get("https://cv.usc.es/admin/index.php").mock(
        return_value=httpx.Response(200, text="must not run")
    )
    gateway = HttpSessionMoodleGateway(  # type: ignore[arg-type]
        _settings(), MemoryCredentialStore("session-secret")
    )

    with pytest.raises(CampusProtocolError):
        await gateway._form_get(url, {})

    assert route.call_count == 0


@respx.mock
async def test_pluginfile_rejects_dot_segments_before_http() -> None:
    route = respx.get("https://cv.usc.es/admin/index.php").mock(
        return_value=httpx.Response(200, text="must not run")
    )
    gateway = HttpSessionMoodleGateway(  # type: ignore[arg-type]
        _settings(), MemoryCredentialStore("session-secret")
    )

    with pytest.raises(CampusCapabilityUnavailable):
        await gateway.fetch_file(
            "https://cv.usc.es/pluginfile.php/%2e%2e/%2e%2e/admin/index.php", 1024
        )

    assert route.call_count == 0


@respx.mock
async def test_form_post_timeout_is_unknown_without_secret_cause_or_retry() -> None:
    route = respx.post("https://cv.usc.es/mod/assign/view.php").mock(
        side_effect=httpx.ReadTimeout("secret", request=httpx.Request("POST", "https://x"))
    )
    gateway = HttpSessionMoodleGateway(  # type: ignore[arg-type]
        _settings(), MemoryCredentialStore("session-secret")
    )

    with pytest.raises(CampusMutationOutcomeUnknown) as caught:
        await gateway._form_post(
            "https://cv.usc.es/mod/assign/view.php",
            {"sesskey": "abc123", "action": "savesubmission"},
        )

    assert route.call_count == 1
    assert caught.value.__cause__ is None
    assert caught.value.request_may_have_been_sent is True
    assert caught.value.do_not_retry is True


@respx.mock
async def test_form_post_500_is_unknown_and_not_retried() -> None:
    route = respx.post("https://cv.usc.es/mod/assign/view.php").mock(
        return_value=httpx.Response(500, text="failed after processing")
    )
    gateway = HttpSessionMoodleGateway(  # type: ignore[arg-type]
        _settings(), MemoryCredentialStore("session-secret")
    )

    with pytest.raises(CampusMutationOutcomeUnknown) as caught:
        await gateway._form_post(
            "https://cv.usc.es/mod/assign/view.php",
            {"sesskey": "abc123", "action": "savesubmission"},
        )

    assert route.call_count == 1
    assert caught.value.do_not_retry is True


@respx.mock
async def test_form_auth_redirect_is_not_followed() -> None:
    route = respx.get("https://cv.usc.es/mod/assign/view.php").mock(
        return_value=httpx.Response(302, headers={"location": "/login/index.php"})
    )
    login = respx.get("https://cv.usc.es/login/index.php").mock(
        return_value=httpx.Response(200, text="must not run")
    )
    gateway = HttpSessionMoodleGateway(  # type: ignore[arg-type]
        _settings(), MemoryCredentialStore("session-secret")
    )

    with pytest.raises(AuthenticationRequired):
        await gateway._form_get("https://cv.usc.es/mod/assign/view.php", {"id": 17})

    assert route.call_count == 1
    assert login.call_count == 0


@respx.mock
async def test_session_get_network_error_has_no_cookie_bearing_cause() -> None:
    respx.get("https://cv.usc.es/user/preferences.php").mock(
        side_effect=httpx.ConnectError("failed")
    )
    gateway = HttpSessionMoodleGateway(  # type: ignore[arg-type]
        _settings(), MemoryCredentialStore("session-secret")
    )

    with pytest.raises(CampusError) as caught:
        await gateway.status()

    assert caught.value.__cause__ is None


@respx.mock
async def test_session_course_contents_fails_closed_without_html_get() -> None:
    store = MemoryCredentialStore("session-secret")
    respx.get("https://cv.usc.es/user/preferences.php").mock(
        return_value=httpx.Response(200, text=_session_context_html())
    )
    respx.post(
        "https://cv.usc.es/lib/ajax/service.php",
        params={"sesskey": "abc123", "info": "core_course_get_contents"},
    ).mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "error": True,
                    "exception": {
                        "errorcode": "wsfunctionnotavailable",
                        "message": "Function not available",
                    },
                }
            ],
        )
    )
    course_page = respx.get("https://cv.usc.es/course/view.php", params={"id": 7}).mock(
        return_value=httpx.Response(200, text="must not be requested")
    )
    gateway = HttpSessionMoodleGateway(_settings(), store)  # type: ignore[arg-type]

    with pytest.raises(CampusCapabilityUnavailable):
        await gateway.invoke("core_course_get_contents", {"courseid": 7, "options": []})

    assert course_page.call_count == 0


@respx.mock
async def test_session_resource_view_page_fails_closed_without_http_get() -> None:
    store = MemoryCredentialStore("session-secret")
    resource_page = respx.get("https://cv.usc.es/mod/resource/view.php", params={"id": 11}).mock(
        return_value=httpx.Response(
            303,
            headers={"location": "/pluginfile.php/70/mod_resource/content/1/guia.pdf"},
        )
    )
    plugin_file = respx.get(
        "https://cv.usc.es/pluginfile.php/70/mod_resource/content/1/guia.pdf"
    ).mock(
        return_value=httpx.Response(
            200,
            content=b"pdf-content",
            headers={"content-type": "application/pdf"},
        )
    )

    with pytest.raises(CampusCapabilityUnavailable):
        await HttpSessionMoodleGateway(  # type: ignore[arg-type]
            _settings(), store
        ).fetch_file("https://cv.usc.es/mod/resource/view.php?id=11", 1024)

    assert resource_page.call_count == 0
    assert plugin_file.call_count == 0
