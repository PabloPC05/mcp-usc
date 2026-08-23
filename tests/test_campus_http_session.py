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
async def test_session_form_adapter_posts_fresh_assignment_form_and_follows_safe_redirect() -> None:
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
    respx.get("https://cv.usc.es/mod/assign/view.php", params={"id": 17}).mock(
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
    assert b"sesskey=abc123" in posted.calls[0].request.content
    assert b"onlinetext_editor%5Bformat%5D=2" in posted.calls[0].request.content
    assert posted.calls[0].request.headers["cookie"] == "MoodleSession=session-secret"
    assert "abc123" not in str(result)


@respx.mock
async def test_session_course_contents_falls_back_to_http_html_when_ajax_is_unavailable() -> None:
    store = MemoryCredentialStore("session-secret")
    respx.get("https://cv.usc.es/my/").mock(
        return_value=httpx.Response(200, text=_dashboard_html())
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
    course_html = """
    <li class="section course-section" data-id="70" data-section="1">
      <h3 class="sectionname">Tema 1</h3>
      <li class="activity" data-moduleid="11">
        <a href="/mod/resource/view.php?id=11"><span class="instancename">Guía</span></a>
        <div class="activity-description">Material docente</div>
        <a href="/pluginfile.php/70/mod_resource/content/1/guia.pdf">guia.pdf</a>
      </li>
    </li>
    """
    respx.get("https://cv.usc.es/course/view.php", params={"id": 7}).mock(
        return_value=httpx.Response(200, text=course_html)
    )
    gateway = HttpSessionMoodleGateway(_settings(), store)  # type: ignore[arg-type]

    result = await gateway.invoke("core_course_get_contents", {"courseid": 7, "options": []})

    assert result[0]["name"] == "Tema 1"
    assert result[0]["modules"][0]["id"] == 11
    assert result[0]["modules"][0]["contents"][0]["filename"] == "guia.pdf"
    assert result[0]["modules"][0]["id_is_course_module"] is True


@respx.mock
async def test_session_resource_page_follows_only_safe_redirect_to_pluginfile() -> None:
    store = MemoryCredentialStore("session-secret")
    respx.get("https://cv.usc.es/mod/resource/view.php", params={"id": 11}).mock(
        return_value=httpx.Response(
            303,
            headers={"location": "/pluginfile.php/70/mod_resource/content/1/guia.pdf"},
        )
    )
    respx.get("https://cv.usc.es/pluginfile.php/70/mod_resource/content/1/guia.pdf").mock(
        return_value=httpx.Response(
            200,
            content=b"pdf-content",
            headers={"content-type": "application/pdf"},
        )
    )

    content, media_type, source_url = await HttpSessionMoodleGateway(  # type: ignore[arg-type]
        _settings(), store
    ).fetch_file("https://cv.usc.es/mod/resource/view.php?id=11", 1024)

    assert content == b"pdf-content"
    assert media_type == "application/pdf"
    assert source_url.endswith("/guia.pdf")
