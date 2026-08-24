from __future__ import annotations

import json
from urllib.parse import parse_qs

import httpx
import pytest

from mcp_usc.demo_audit import (
    DEMO_BASE_URL,
    DemoConfigurationError,
    DemoRestClient,
    _parser,
    main,
    run_demo_audit,
)

TOKEN = "a" * 32
REST_URL = f"{DEMO_BASE_URL}/webservice/rest/server.php"
TOKEN_URL = f"{DEMO_BASE_URL}/login/token.php"


def _form(request: httpx.Request) -> dict[str, list[str]]:
    return parse_qs(request.content.decode(), keep_blank_values=True)


def _response_for(function: str, available: set[str]) -> object:
    if function == "core_webservice_get_site_info":
        return {
            "userid": 5,
            "release": "5.2 (Build: 20260801)",
            "functions": [{"name": name} for name in sorted(available)],
        }
    if function == "core_enrol_get_users_courses":
        return [{"id": 7, "fullname": "Must not be included in the report"}]
    if function == "core_user_get_users_by_field":
        return [{"id": 5, "email": "must-not-leak@example.test"}]
    if function == "core_calendar_get_calendar_access_information":
        return {"canmanageownentries": True, "warnings": []}
    if function == "core_calendar_get_allowed_event_types":
        return {"allowedeventtypes": ["user"], "warnings": []}
    if function == "mod_assign_get_assignments":
        return {"courses": [{"id": 7, "assignments": [{"id": 11}]}], "warnings": []}
    if function == "mod_assign_get_submission_status":
        return {"lastattempt": {"submission": {"status": "new"}}, "warnings": []}
    if function == "mod_quiz_get_quizzes_by_courses":
        return {"quizzes": [{"id": 13}], "warnings": []}
    if function == "mod_quiz_get_user_attempts":
        return {"attempts": [], "warnings": []}
    return {"warnings": []}


async def test_acquire_uses_post_body_and_never_retains_password_or_cookies() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert str(request.url) == TOKEN_URL
        form = _form(request)
        assert form["username"] == ["student"]
        assert form["password"] == ["public-demo-password"]
        assert form["service"] == ["moodle_mobile_app"]
        return httpx.Response(
            200,
            json={"token": TOKEN, "privatetoken": "must-not-be-retained"},
            headers={"set-cookie": "MoodleSession=must-not-be-retained"},
        )

    client = await DemoRestClient.acquire(
        username="student",
        password="public-demo-password",
        transport=httpx.MockTransport(handler),
    )
    representation = repr(client)
    assert TOKEN not in representation
    assert "public-demo-password" not in representation
    assert "must-not-be-retained" not in representation
    assert "cookie" not in requests[0].headers
    assert requests[0].method == "POST"


@pytest.mark.parametrize(
    "base_url",
    [
        "https://cv.usc.es",
        "https://school.moodledemo.net.evil.example",
        "http://school.moodledemo.net",
        "https://school.moodledemo.net/path",
    ],
)
def test_demo_allowlist_is_exact_and_separate_from_usc(base_url: str) -> None:
    with pytest.raises(DemoConfigurationError):
        DemoRestClient(token=TOKEN, base_url=base_url)


async def test_transport_refuses_message_and_forum_writes_before_http() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={})

    client = DemoRestClient(token=TOKEN, transport=httpx.MockTransport(handler))
    for function in (
        "core_message_send_instant_messages",
        "mod_forum_add_discussion",
        "mod_forum_add_discussion_post",
        "mod_chat_send_chat_message",
    ):
        with pytest.raises(DemoConfigurationError):
            await client.call(function, {})
    assert calls == 0


async def test_read_only_audit_reports_shapes_and_skips_all_external_writes() -> None:
    available = {
        "core_webservice_get_site_info",
        "core_enrol_get_users_courses",
        "core_user_get_users_by_field",
        "core_calendar_get_calendar_access_information",
        "core_calendar_get_allowed_event_types",
        "mod_assign_get_assignments",
        "mod_assign_get_submission_status",
        "mod_quiz_get_quizzes_by_courses",
        "mod_quiz_get_user_attempts",
        "core_calendar_create_calendar_events",
        "core_calendar_delete_calendar_events",
        "core_message_send_instant_messages",
        "core_message_get_conversations",
        "mod_forum_add_discussion",
    }
    requests: list[httpx.Request] = []
    called_functions: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert str(request.url) == REST_URL
        assert "wstoken" not in request.url.query.decode()
        assert "cookie" not in request.headers
        form = _form(request)
        assert form["wstoken"] == [TOKEN]
        function = form["wsfunction"][0]
        called_functions.append(function)
        return httpx.Response(200, json=_response_for(function, available))

    client = DemoRestClient(token=TOKEN, transport=httpx.MockTransport(handler))
    report = await run_demo_audit(client, max_courses=1, max_activity_samples=1)
    encoded = json.dumps(report)

    assert report["summary"]["overall"] == "pass"
    assert report["policy"]["messages_email_chat_forum_posts_sent"] is False
    assert report["policy"]["reversible_personal_write_enabled"] is False
    assert "core_calendar_create_calendar_events" not in called_functions
    assert "core_calendar_delete_calendar_events" not in called_functions
    assert "core_message_send_instant_messages" not in called_functions
    assert "core_message_get_conversations" not in called_functions
    assert "mod_forum_add_discussion" not in called_functions
    assert TOKEN not in encoded
    assert "must-not-leak@example.test" not in encoded
    assert all(request.method == "POST" for request in requests)
    round_trip = next(
        probe for probe in report["probes"] if probe["id"] == "write.personal_calendar_round_trip"
    )
    assert round_trip["status"] == "skip"


async def test_explicit_reversible_write_creates_and_deletes_once() -> None:
    available = {
        "core_webservice_get_site_info",
        "core_enrol_get_users_courses",
        "core_calendar_get_calendar_access_information",
        "core_calendar_get_allowed_event_types",
        "core_calendar_create_calendar_events",
        "core_calendar_delete_calendar_events",
        "core_calendar_get_calendar_events",
    }
    called_functions: list[str] = []
    state: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        form = _form(request)
        function = form["wsfunction"][0]
        called_functions.append(function)
        if function == "core_calendar_create_calendar_events":
            assert form["events[0][eventtype]"] == ["user"]
            assert form["events[0][courseid]"] == ["0"]
            state["event"] = {
                "id": 91,
                "userid": 5,
                "name": form["events[0][name]"][0],
                "eventtype": "user",
            }
            return httpx.Response(200, json={"events": [{"id": 91, "userid": 5}]})
        if function == "core_calendar_get_calendar_events":
            assert form["events[eventids][0]"] == ["91"]
            events = [state["event"]] if "event" in state else []
            return httpx.Response(200, json={"events": events, "warnings": []})
        if function == "core_calendar_delete_calendar_events":
            assert form["events[0][eventid]"] == ["91"]
            assert form["events[0][repeat]"] == ["0"]
            state.pop("event", None)
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=_response_for(function, available))

    client = DemoRestClient(token=TOKEN, transport=httpx.MockTransport(handler))
    report = await run_demo_audit(
        client,
        allow_reversible_write=True,
        max_courses=1,
        max_activity_samples=1,
    )
    round_trip = next(
        probe for probe in report["probes"] if probe["id"] == "write.personal_calendar_round_trip"
    )
    assert round_trip == {
        "id": "write.personal_calendar_round_trip",
        "category": "reversible_write",
        "status": "pass",
        "metrics": {
            "created": 1,
            "deleted": 1,
            "external_state_remaining": False,
            "read_back_checks": 2,
        },
    }
    assert called_functions.count("core_calendar_create_calendar_events") == 1
    assert called_functions.count("core_calendar_delete_calendar_events") == 1
    assert called_functions.count("core_calendar_get_calendar_events") == 2


async def test_unexpected_created_event_list_is_not_deleted_blindly() -> None:
    available = {
        "core_webservice_get_site_info",
        "core_enrol_get_users_courses",
        "core_calendar_get_calendar_access_information",
        "core_calendar_get_allowed_event_types",
        "core_calendar_create_calendar_events",
        "core_calendar_delete_calendar_events",
        "core_calendar_get_calendar_events",
    }
    called_functions: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        form = _form(request)
        function = form["wsfunction"][0]
        called_functions.append(function)
        if function == "core_calendar_create_calendar_events":
            return httpx.Response(
                200,
                json={
                    "events": [
                        {"id": event_id, "userid": 5} for event_id in range(91, 97)
                    ]
                },
            )
        return httpx.Response(200, json=_response_for(function, available))

    report = await run_demo_audit(
        DemoRestClient(token=TOKEN, transport=httpx.MockTransport(handler)),
        allow_reversible_write=True,
        max_courses=1,
        max_activity_samples=1,
    )
    round_trip = next(
        probe for probe in report["probes"] if probe["id"] == "write.personal_calendar_round_trip"
    )
    assert round_trip["status"] == "fail"
    assert round_trip["metrics"]["external_state_remaining"] == "unknown"
    assert "core_calendar_delete_calendar_events" not in called_functions
    assert "core_calendar_get_calendar_events" not in called_functions


async def test_delete_response_is_not_enough_when_readback_still_finds_event() -> None:
    available = {
        "core_webservice_get_site_info",
        "core_enrol_get_users_courses",
        "core_calendar_get_calendar_access_information",
        "core_calendar_get_allowed_event_types",
        "core_calendar_create_calendar_events",
        "core_calendar_delete_calendar_events",
        "core_calendar_get_calendar_events",
    }
    event: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        form = _form(request)
        function = form["wsfunction"][0]
        if function == "core_calendar_create_calendar_events":
            event.update(
                id=91,
                userid=5,
                name=form["events[0][name]"][0],
                eventtype="user",
            )
            return httpx.Response(200, json={"events": [{"id": 91, "userid": 5}]})
        if function == "core_calendar_get_calendar_events":
            return httpx.Response(200, json={"events": [event], "warnings": []})
        if function == "core_calendar_delete_calendar_events":
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=_response_for(function, available))

    report = await run_demo_audit(
        DemoRestClient(token=TOKEN, transport=httpx.MockTransport(handler)),
        allow_reversible_write=True,
        max_courses=1,
        max_activity_samples=1,
    )
    round_trip = next(
        probe for probe in report["probes"] if probe["id"] == "write.personal_calendar_round_trip"
    )
    assert round_trip["status"] == "fail"
    assert round_trip["metrics"]["external_state_remaining"] is True


async def test_event_with_wrong_owner_is_never_deleted() -> None:
    available = {
        "core_webservice_get_site_info",
        "core_enrol_get_users_courses",
        "core_calendar_get_calendar_access_information",
        "core_calendar_get_allowed_event_types",
        "core_calendar_create_calendar_events",
        "core_calendar_delete_calendar_events",
        "core_calendar_get_calendar_events",
    }
    called_functions: list[str] = []
    event: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        form = _form(request)
        function = form["wsfunction"][0]
        called_functions.append(function)
        if function == "core_calendar_create_calendar_events":
            event.update(
                id=91,
                userid=999,
                name=form["events[0][name]"][0],
                eventtype="user",
            )
            return httpx.Response(200, json={"events": [{"id": 91, "userid": 999}]})
        if function == "core_calendar_get_calendar_events":
            return httpx.Response(200, json={"events": [event], "warnings": []})
        return httpx.Response(200, json=_response_for(function, available))

    report = await run_demo_audit(
        DemoRestClient(token=TOKEN, transport=httpx.MockTransport(handler)),
        allow_reversible_write=True,
        max_courses=1,
        max_activity_samples=1,
    )
    round_trip = next(
        probe for probe in report["probes"] if probe["id"] == "write.personal_calendar_round_trip"
    )
    assert round_trip["status"] == "fail"
    assert round_trip["metrics"]["external_state_remaining"] == "unknown"
    assert "core_calendar_delete_calendar_events" not in called_functions


def test_cli_requires_explicit_demo_confirmation_and_outputs_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main([]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["summary"]["overall"] == "fail"
    assert report["probes"][0]["reason"] == "DemoConfigurationError"


def test_cli_does_not_accept_secrets_in_process_arguments() -> None:
    help_text = _parser().format_help()

    assert "--token" not in help_text
    assert "--password" not in help_text
