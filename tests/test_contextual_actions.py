from __future__ import annotations

from collections import deque
from typing import Any

import pytest

from mcp_usc.contextual_actions import (
    ContextualActionError,
    cancel_choice_response,
    create_forum_discussion,
    create_personal_calendar_event,
    delete_personal_calendar_event,
    preview_cancel_choice_response,
    preview_create_forum_discussion,
    preview_create_personal_calendar_event,
    preview_delete_personal_calendar_event,
    preview_reply_forum_post,
    preview_submit_choice_response,
    reply_forum_post,
    submit_choice_response,
)


class FakeInvoke:
    def __init__(self, responses: dict[str, list[Any]]) -> None:
        self.responses = {name: deque(values) for name, values in responses.items()}
        self.calls: list[tuple[str, dict[str, Any], str | None]] = []

    async def __call__(self, function: str, params: dict[str, Any]) -> Any:
        self.calls.append((function, dict(params), getattr(params, "client_request_id", None)))
        if function not in self.responses or not self.responses[function]:
            raise AssertionError(f"Llamada inesperada a {function}")
        response = self.responses[function].popleft()
        if isinstance(response, Exception):
            raise response
        return response


def _identity(user_id: int = 7) -> dict[str, Any]:
    return {"userid": user_id, "fullname": "Alumna USC"}


def _calendar_access(allowed: bool = True) -> dict[str, Any]:
    return {
        "canmanageentries": False,
        "canmanageownentries": allowed,
        "canmanagegroupentries": False,
        "warnings": [],
    }


def _forum() -> dict[str, Any]:
    return {
        "id": 11,
        "course": 5,
        "cmid": 91,
        "name": "Foro xeral",
        "type": "general",
    }


def _choice(*, allow_update: bool = True, allow_multiple: bool = False) -> dict[str, Any]:
    return {
        "choices": [
            {
                "id": 21,
                "course": 5,
                "name": "Escolle horario",
                "allowupdate": allow_update,
                "allowmultiple": allow_multiple,
                "timeopen": 100,
                "timeclose": 1_000,
            }
        ],
        "warnings": [],
    }


def _options(*, selected: int | None = None) -> dict[str, Any]:
    return {
        "options": [
            {
                "id": 31,
                "text": "Mañá",
                "checked": int(selected == 31),
                "disabled": 0,
            },
            {
                "id": 32,
                "text": "Tarde",
                "checked": int(selected == 32),
                "disabled": 0,
            },
        ],
        "warnings": [],
    }


@pytest.mark.asyncio
async def test_calendar_create_preview_is_read_only_and_execution_is_one_write() -> None:
    preview_invoke = FakeInvoke(
        {
            "core_webservice_get_site_info": [_identity()],
            "core_calendar_get_calendar_access_information": [_calendar_access()],
            "core_calendar_get_allowed_event_types": [
                {"allowedeventtypes": ["user"], "warnings": []}
            ],
        }
    )

    preview = await preview_create_personal_calendar_event(
        preview_invoke,
        name="Cita titoría",
        description="Levar dúbidas",
        timestart=1_800_000_000,
        duration=1_800,
        repeats=2,
    )

    assert preview["allowed"] is True
    assert preview["event_type"] == "user"
    assert preview["owner_user_id"] == 7
    assert preview["repeat_scope"] == "series"
    assert preview["additional_repeats_requested"] == 2
    assert [call[0] for call in preview_invoke.calls] == [
        "core_webservice_get_site_info",
        "core_calendar_get_calendar_access_information",
        "core_calendar_get_allowed_event_types",
    ]

    execute_invoke = FakeInvoke(
        {
            "core_calendar_create_calendar_events": [
                {
                    "events": [
                        {"id": 101, "userid": 7},
                        {"id": 102, "userid": 7},
                        {"id": 103, "userid": 7},
                    ],
                    "warnings": [],
                }
            ]
        }
    )
    result = await create_personal_calendar_event(
        execute_invoke,
        name=preview["name"],
        description=preview["description"],
        timestart=preview["timestart"],
        duration=preview["duration"],
        repeats=preview["additional_repeats_requested"],
        expected_owner_user_id=preview["owner_user_id"],
        client_request_id="calendar-create-1",
    )

    assert result["ok"] is True
    assert result["event_ids"] == [101, 102, 103]
    assert len(execute_invoke.calls) == 1
    function, params, request_id = execute_invoke.calls[0]
    assert function == "core_calendar_create_calendar_events"
    assert request_id == "calendar-create-1"
    assert params == {
        "events": [
            {
                "name": "Cita titoría",
                "description": "Levar dúbidas",
                "format": 2,
                "courseid": 0,
                "groupid": 0,
                "repeats": 2,
                "eventtype": "user",
                "timestart": 1_800_000_000,
                "timeduration": 1_800,
                "visible": 1,
                "sequence": 1,
            }
        ]
    }


@pytest.mark.asyncio
async def test_calendar_create_denied_by_access_never_calls_write() -> None:
    invoke = FakeInvoke(
        {
            "core_webservice_get_site_info": [_identity()],
            "core_calendar_get_calendar_access_information": [_calendar_access(False)],
            "core_calendar_get_allowed_event_types": [
                {"allowedeventtypes": ["user"], "warnings": []}
            ],
        }
    )

    result = await preview_create_personal_calendar_event(
        invoke, name="Evento", timestart=1_800_000_000
    )

    assert result["allowed"] is False
    assert result["code"] == "personal_events_not_allowed"
    assert all("create_calendar_events" not in call[0] for call in invoke.calls)


@pytest.mark.asyncio
async def test_calendar_delete_checks_owner_and_series_scope_then_writes_once() -> None:
    preview_invoke = FakeInvoke(
        {
            "core_webservice_get_site_info": [_identity()],
            "core_calendar_get_calendar_event_by_id": [
                {
                    "event": {
                        "id": 55,
                        "name": "Recordatorio semanal",
                        "userid": 7,
                        "eventtype": "user",
                        "courseid": 0,
                        "groupid": 0,
                        "repeatid": 50,
                    },
                    "warnings": [],
                }
            ],
            "core_calendar_get_calendar_access_information": [_calendar_access()],
        }
    )

    preview = await preview_delete_personal_calendar_event(
        preview_invoke, event_id=55, scope="series"
    )

    assert preview["allowed"] is True
    assert preview["affected_scope"] == "all_occurrences_in_series"

    execute_invoke = FakeInvoke({"core_calendar_delete_calendar_events": [None]})
    result = await delete_personal_calendar_event(
        execute_invoke, event_id=55, scope="series", client_request_id="delete-series"
    )

    assert result["ok"] is True
    assert execute_invoke.calls == [
        (
            "core_calendar_delete_calendar_events",
            {"events": [{"eventid": 55, "repeat": True}]},
            "delete-series",
        )
    ]


@pytest.mark.asyncio
async def test_calendar_delete_wrong_owner_fails_closed() -> None:
    invoke = FakeInvoke(
        {
            "core_webservice_get_site_info": [_identity(7)],
            "core_calendar_get_calendar_event_by_id": [
                {
                    "event": {
                        "id": 55,
                        "name": "Alleo",
                        "userid": 8,
                        "eventtype": "user",
                        "courseid": 0,
                        "groupid": 0,
                        "repeatid": 0,
                    },
                    "warnings": [],
                }
            ],
            "core_calendar_get_calendar_access_information": [_calendar_access()],
        }
    )

    preview = await preview_delete_personal_calendar_event(invoke, event_id=55)

    assert preview["allowed"] is False
    assert preview["code"] == "not_event_owner"
    assert all(call[0] != "core_calendar_delete_calendar_events" for call in invoke.calls)


@pytest.mark.asyncio
async def test_forum_discussion_resolves_group_audience_and_has_no_attachments() -> None:
    preview_invoke = FakeInvoke(
        {
            "mod_forum_get_forums_by_courses": [[_forum()]],
            "mod_forum_get_forum_access_information": [
                {"canstartdiscussion": True, "warnings": []}
            ],
            "core_group_get_activity_groupmode": [{"groupmode": 1, "warnings": []}],
            "core_group_get_activity_allowed_groups": [
                {
                    "groups": [{"id": 3, "name": "Grupo A"}],
                    "canaccessallgroups": False,
                    "warnings": [],
                }
            ],
            "mod_forum_can_add_discussion": [
                {"status": True, "cancreateattachment": True, "warnings": []}
            ],
        }
    )

    preview = await preview_create_forum_discussion(
        preview_invoke,
        course_id=5,
        forum_id=11,
        subject="Dúbida <tema>",
        message="Liña 1\nLiña <2>",
        group_id=0,
    )

    assert preview["allowed"] is True
    assert preview["resolved_group_id"] == 3
    assert preview["audience"] == {
        "scope": "group",
        "course_id": 5,
        "group_id": 3,
        "group_name": "Grupo A",
    }
    assert preview["attachments"] == []

    execute_invoke = FakeInvoke(
        {"mod_forum_add_discussion": [{"discussionid": 77, "warnings": []}]}
    )
    result = await create_forum_discussion(
        execute_invoke,
        forum_id=11,
        subject=preview["subject"],
        message=preview["message"],
        group_id=preview["resolved_group_id"],
        client_request_id="forum-new",
    )

    assert result["ok"] is True
    assert len(execute_invoke.calls) == 1
    _, params, request_id = execute_invoke.calls[0]
    assert request_id == "forum-new"
    assert params["message"] == "Liña 1<br>Liña &lt;2&gt;"
    assert params["options"] == [
        {"name": "discussionsubscribe", "value": 0},
        {"name": "discussionpinned", "value": 0},
        {"name": "inlineattachmentsid", "value": 0},
        {"name": "attachmentsid", "value": 0},
    ]


@pytest.mark.asyncio
async def test_forum_discussion_fails_closed_when_active_group_is_ambiguous() -> None:
    invoke = FakeInvoke(
        {
            "mod_forum_get_forums_by_courses": [[_forum()]],
            "mod_forum_get_forum_access_information": [
                {"canstartdiscussion": True, "warnings": []}
            ],
            "core_group_get_activity_groupmode": [{"groupmode": 1, "warnings": []}],
            "core_group_get_activity_allowed_groups": [
                {
                    "groups": [{"id": 3, "name": "A"}, {"id": 4, "name": "B"}],
                    "canaccessallgroups": False,
                    "warnings": [],
                }
            ],
        }
    )

    preview = await preview_create_forum_discussion(
        invoke,
        course_id=5,
        forum_id=11,
        subject="Tema",
        message="Corpo",
        group_id=0,
    )

    assert preview["allowed"] is False
    assert preview["code"] == "ambiguous_or_forbidden_audience"
    assert all(call[0] != "mod_forum_can_add_discussion" for call in invoke.calls)
    assert all(call[0] != "mod_forum_add_discussion" for call in invoke.calls)


@pytest.mark.asyncio
async def test_forum_reply_resolves_context_permission_and_executes_one_plain_post() -> None:
    preview_invoke = FakeInvoke(
        {
            "mod_forum_get_forums_by_courses": [[_forum()]],
            "mod_forum_get_forum_access_information": [{"canreplypost": True, "warnings": []}],
            "mod_forum_get_discussion_post": [
                {
                    "post": {
                        "id": 41,
                        "discussionid": 61,
                        "subject": "Pregunta",
                        "replysubject": "Re: Pregunta",
                        "capabilities": {"reply": True},
                    },
                    "warnings": [],
                }
            ],
            "mod_forum_get_forum_discussions": [
                {
                    "discussions": [
                        {
                            "discussion": 61,
                            "forum": 11,
                            "course": 5,
                            "name": "Pregunta inicial",
                            "groupid": -1,
                        }
                    ],
                    "warnings": [],
                }
            ],
            "core_group_get_activity_groupmode": [{"groupmode": 0, "warnings": []}],
            "core_group_get_activity_allowed_groups": [
                {"groups": [], "canaccessallgroups": False, "warnings": []}
            ],
        }
    )

    preview = await preview_reply_forum_post(
        preview_invoke,
        course_id=5,
        forum_id=11,
        parent_post_id=41,
        message="A miña resposta",
    )

    assert preview["allowed"] is True
    assert preview["subject"] == "Re: Pregunta"
    assert preview["discussion_subject"] == "Pregunta inicial"
    assert preview["audience"] == {"scope": "course", "course_id": 5}
    discussion_call = next(
        call for call in preview_invoke.calls if call[0] == "mod_forum_get_forum_discussions"
    )
    assert discussion_call[1] == {
        "forumid": 11,
        "sortorder": -1,
        "page": 0,
        "perpage": 100,
        "groupid": 0,
    }

    execute_invoke = FakeInvoke({"mod_forum_add_discussion_post": [{"postid": 99, "warnings": []}]})
    result = await reply_forum_post(
        execute_invoke,
        parent_post_id=41,
        subject=preview["subject"],
        message=preview["message"],
        client_request_id="forum-reply",
    )

    assert result["ok"] is True
    assert len(execute_invoke.calls) == 1
    _, params, request_id = execute_invoke.calls[0]
    assert request_id == "forum-reply"
    assert params["messageformat"] == 2
    assert {option["name"] for option in params["options"]} == {
        "discussionsubscribe",
        "private",
        "inlineattachmentsid",
        "attachmentsid",
        "topreferredformat",
    }
    assert all(option["value"] == 0 for option in params["options"])


@pytest.mark.asyncio
async def test_forum_reply_filters_the_exact_allowed_group() -> None:
    invoke = FakeInvoke(
        {
            "mod_forum_get_forums_by_courses": [[_forum()]],
            "mod_forum_get_forum_access_information": [{"canreplypost": True, "warnings": []}],
            "mod_forum_get_discussion_post": [
                {
                    "post": {
                        "discussionid": 61,
                        "subject": "Pregunta",
                        "capabilities": {"reply": True},
                    },
                    "warnings": [],
                }
            ],
            "core_group_get_activity_groupmode": [{"groupmode": 1, "warnings": []}],
            "core_group_get_activity_allowed_groups": [
                {
                    "groups": [{"id": 3, "name": "Grupo A"}],
                    "canaccessallgroups": False,
                    "warnings": [],
                }
            ],
            "mod_forum_get_forum_discussions": [
                {
                    "discussions": [
                        {
                            "discussion": 61,
                            "forum": 11,
                            "course": 5,
                            "name": "Pregunta inicial",
                            "groupid": 3,
                        }
                    ],
                    "warnings": [],
                }
            ],
        }
    )

    preview = await preview_reply_forum_post(
        invoke,
        course_id=5,
        forum_id=11,
        parent_post_id=41,
        message="Resposta",
        group_id=3,
    )

    assert preview["allowed"] is True
    assert preview["resolved_group_id"] == 3
    assert preview["audience"]["group_id"] == 3
    discussion_call = next(
        call for call in invoke.calls if call[0] == "mod_forum_get_forum_discussions"
    )
    assert discussion_call[1]["groupid"] == 3


@pytest.mark.asyncio
async def test_choice_preview_resolves_exact_text_and_execution_writes_once() -> None:
    preview_invoke = FakeInvoke(
        {
            "mod_choice_get_choices_by_courses": [_choice(allow_multiple=True)],
            "mod_choice_get_choice_options": [_options()],
        }
    )

    preview = await preview_submit_choice_response(
        preview_invoke,
        course_id=5,
        choice_id=21,
        option_texts=["Mañá", "Tarde"],
        now=500,
    )

    assert preview["allowed"] is True
    assert preview["option_ids"] == [31, 32]
    assert [option["text"] for option in preview["options"]] == ["Mañá", "Tarde"]
    assert [call[0] for call in preview_invoke.calls] == [
        "mod_choice_get_choices_by_courses",
        "mod_choice_get_choice_options",
    ]

    execute_invoke = FakeInvoke(
        {
            "mod_choice_submit_choice_response": [
                {
                    "answers": [
                        {"id": 70, "optionid": 31},
                        {"id": 71, "optionid": 32},
                    ],
                    "warnings": [],
                }
            ]
        }
    )
    result = await submit_choice_response(
        execute_invoke,
        choice_id=21,
        option_ids=preview["option_ids"],
        client_request_id="choice-submit",
    )

    assert result["ok"] is True
    assert result["answer_ids"] == [70, 71]
    assert execute_invoke.calls == [
        (
            "mod_choice_submit_choice_response",
            {"choiceid": 21, "responses": [31, 32]},
            "choice-submit",
        )
    ]


@pytest.mark.asyncio
async def test_choice_text_matching_is_case_sensitive_and_fails_before_write() -> None:
    invoke = FakeInvoke(
        {
            "mod_choice_get_choices_by_courses": [_choice()],
            "mod_choice_get_choice_options": [_options()],
        }
    )

    with pytest.raises(ContextualActionError, match="no existe o es ambigua"):
        await preview_submit_choice_response(
            invoke,
            course_id=5,
            choice_id=21,
            option_texts=["mañá"],
            now=500,
        )

    assert all(call[0] != "mod_choice_submit_choice_response" for call in invoke.calls)


@pytest.mark.asyncio
async def test_choice_cancel_previews_exact_selection_and_deletes_only_current_user() -> None:
    preview_invoke = FakeInvoke(
        {
            "mod_choice_get_choices_by_courses": [_choice(allow_update=True)],
            "mod_choice_get_choice_options": [_options(selected=32)],
        }
    )

    preview = await preview_cancel_choice_response(
        preview_invoke, course_id=5, choice_id=21, now=500
    )

    assert preview["allowed"] is True
    assert preview["selected_options"] == [
        {"option_id": 32, "text": "Tarde", "content_is_untrusted": True}
    ]
    assert preview["affected_scope"] == "all_current_user_responses"

    execute_invoke = FakeInvoke(
        {"mod_choice_delete_choice_responses": [{"status": True, "warnings": []}]}
    )
    result = await cancel_choice_response(
        execute_invoke, choice_id=21, client_request_id="choice-cancel"
    )

    assert result["ok"] is True
    assert execute_invoke.calls == [
        (
            "mod_choice_delete_choice_responses",
            {"choiceid": 21, "responses": []},
            "choice-cancel",
        )
    ]


@pytest.mark.asyncio
async def test_choice_cancel_fails_closed_when_updates_are_forbidden() -> None:
    invoke = FakeInvoke(
        {
            "mod_choice_get_choices_by_courses": [_choice(allow_update=False)],
            "mod_choice_get_choice_options": [_options(selected=31)],
        }
    )

    preview = await preview_cancel_choice_response(invoke, course_id=5, choice_id=21, now=500)

    assert preview["allowed"] is False
    assert preview["code"] == "choice_update_not_allowed"
    assert all(call[0] != "mod_choice_delete_choice_responses" for call in invoke.calls)


@pytest.mark.asyncio
async def test_executor_never_retries_an_ambiguous_write_failure() -> None:
    invoke = FakeInvoke({"mod_choice_submit_choice_response": [TimeoutError("resultado ambiguo")]})

    with pytest.raises(TimeoutError, match="ambiguo"):
        await submit_choice_response(
            invoke,
            choice_id=21,
            option_ids=[31],
            client_request_id="no-retry",
        )

    assert len(invoke.calls) == 1
