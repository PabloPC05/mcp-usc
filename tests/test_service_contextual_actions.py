from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import pytest

from mcp_usc.campus import CampusProtocolError
from mcp_usc.confirmations import ACTION_CONFIRMATIONS
from mcp_usc.service import UscService

MUTATIONS = frozenset(
    {
        "core_calendar_create_calendar_events",
        "core_calendar_delete_calendar_events",
        "mod_forum_add_discussion",
        "mod_forum_add_discussion_post",
        "mod_choice_submit_choice_response",
        "mod_choice_delete_choice_responses",
    }
)


@dataclass(frozen=True, slots=True)
class ActionCase:
    name: str
    preview_method: str
    execute_method: str
    arguments: dict[str, Any]
    changed_arguments: dict[str, Any]
    mutation: str


CASES = (
    ActionCase(
        name="create_calendar",
        preview_method="preview_create_personal_calendar_event",
        execute_method="create_personal_calendar_event",
        arguments={
            "name": "Tutoría",
            "timestart": 2_000_000_000,
            "description": "Resolver dudas",
            "duration": 1_800,
            "repeats": 0,
        },
        changed_arguments={
            "name": "Tutoría cambiada",
            "timestart": 2_000_000_000,
            "description": "Resolver dudas",
            "duration": 1_800,
            "repeats": 0,
        },
        mutation="core_calendar_create_calendar_events",
    ),
    ActionCase(
        name="delete_calendar",
        preview_method="preview_delete_personal_calendar_event",
        execute_method="delete_personal_calendar_event",
        arguments={"event_id": 55, "scope": "single"},
        changed_arguments={"event_id": 56, "scope": "single"},
        mutation="core_calendar_delete_calendar_events",
    ),
    ActionCase(
        name="create_discussion",
        preview_method="preview_create_forum_discussion",
        execute_method="create_forum_discussion",
        arguments={
            "course_id": 5,
            "forum_id": 11,
            "subject": "Duda sobre el tema",
            "message": "¿Podéis aclararlo?",
            "group_id": 0,
        },
        changed_arguments={
            "course_id": 5,
            "forum_id": 11,
            "subject": "Otro asunto",
            "message": "¿Podéis aclararlo?",
            "group_id": 0,
        },
        mutation="mod_forum_add_discussion",
    ),
    ActionCase(
        name="reply_post",
        preview_method="preview_reply_forum_post",
        execute_method="reply_forum_post",
        arguments={
            "course_id": 5,
            "forum_id": 11,
            "parent_post_id": 41,
            "message": "Mi respuesta",
            "subject": None,
            "group_id": 0,
        },
        changed_arguments={
            "course_id": 5,
            "forum_id": 11,
            "parent_post_id": 41,
            "message": "Respuesta modificada",
            "subject": None,
            "group_id": 0,
        },
        mutation="mod_forum_add_discussion_post",
    ),
    ActionCase(
        name="submit_choice",
        preview_method="preview_submit_choice_response",
        execute_method="submit_choice_response",
        arguments={"course_id": 5, "choice_id": 21, "option_texts": ["Mañana"]},
        changed_arguments={"course_id": 5, "choice_id": 21, "option_texts": ["Tarde"]},
        mutation="mod_choice_submit_choice_response",
    ),
    ActionCase(
        name="cancel_choice",
        preview_method="preview_cancel_choice_response",
        execute_method="cancel_choice_response",
        arguments={"course_id": 5, "choice_id": 21},
        changed_arguments={"course_id": 6, "choice_id": 21},
        mutation="mod_choice_delete_choice_responses",
    ),
)


class FakeContextualGateway:
    def __init__(self) -> None:
        self.user_id = 7
        self.full_name = "Alumna USC"
        self.calendar_can_manage = True
        self.event_owner_id = 7
        self.event_name = "Recordatorio"
        self.forum_name = "Foro general"
        self.forum_can_start = True
        self.forum_can_reply = True
        self.parent_subject = "Pregunta"
        self.choice_option_offset = 0
        self.choice_option_disabled = False
        self.choice_has_selection = True
        self.mutation_error = False
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.required: list[set[str]] = []
        self.status_calls = 0

    @property
    def mutation_calls(self) -> list[tuple[str, dict[str, Any]]]:
        return [call for call in self.calls if call[0] in MUTATIONS]

    @property
    def read_calls(self) -> list[tuple[str, dict[str, Any]]]:
        return [call for call in self.calls if call[0] not in MUTATIONS]

    async def status(self) -> dict[str, Any]:
        self.status_calls += 1
        return {"authenticated": True, "user_id": self.user_id}

    async def require_functions(self, functions: set[str]) -> None:
        self.required.append(functions)

    async def invoke(self, function: str, arguments: Mapping[str, Any]) -> Any:
        params = dict(arguments)
        self.calls.append((function, params))
        if function in MUTATIONS:
            if self.mutation_error:
                raise CampusProtocolError("La conexión terminó después de enviar la petición")
            return self._mutation_response(function)
        return self._read_response(function, params)

    def _read_response(self, function: str, params: dict[str, Any]) -> Any:
        if function == "core_webservice_get_site_info":
            return {"userid": self.user_id, "fullname": self.full_name}
        if function == "core_calendar_get_calendar_access_information":
            return {"canmanageownentries": self.calendar_can_manage, "warnings": []}
        if function == "core_calendar_get_allowed_event_types":
            return {"allowedeventtypes": ["user"], "warnings": []}
        if function == "core_calendar_get_calendar_event_by_id":
            return {
                "event": {
                    "id": params["eventid"],
                    "name": self.event_name,
                    "userid": self.event_owner_id,
                    "eventtype": "user",
                    "courseid": 0,
                    "groupid": 0,
                    "repeatid": 0,
                },
                "warnings": [],
            }
        if function == "mod_forum_get_forums_by_courses":
            course_id = params["courseids"][0]
            return [
                {
                    "id": 11,
                    "course": course_id,
                    "cmid": 91,
                    "name": self.forum_name,
                    "type": "general",
                }
            ]
        if function == "mod_forum_get_forum_access_information":
            return {
                "canstartdiscussion": self.forum_can_start,
                "canreplypost": self.forum_can_reply,
                "warnings": [],
            }
        if function == "core_group_get_activity_groupmode":
            return {"groupmode": 0, "warnings": []}
        if function == "core_group_get_activity_allowed_groups":
            return {"groups": [], "canaccessallgroups": False, "warnings": []}
        if function == "mod_forum_can_add_discussion":
            return {"status": self.forum_can_start, "warnings": []}
        if function == "mod_forum_get_discussion_post":
            return {
                "post": {
                    "id": params["postid"],
                    "discussionid": 61,
                    "subject": self.parent_subject,
                    "replysubject": f"Re: {self.parent_subject}",
                    "capabilities": {"reply": self.forum_can_reply},
                },
                "warnings": [],
            }
        if function == "mod_forum_get_forum_discussions":
            return {
                "discussions": [
                    {
                        "discussion": 61,
                        "forum": params["forumid"],
                        "course": 5,
                        "name": "Pregunta inicial",
                        "groupid": -1,
                    }
                ],
                "warnings": [],
            }
        if function == "mod_choice_get_choices_by_courses":
            course_id = params["courseids"][0]
            return {
                "choices": [
                    {
                        "id": 21,
                        "course": course_id,
                        "name": "Escoge horario",
                        "allowupdate": True,
                        "allowmultiple": False,
                        "timeopen": 0,
                        "timeclose": 0,
                    }
                ],
                "warnings": [],
            }
        if function == "mod_choice_get_choice_options":
            offset = self.choice_option_offset
            return {
                "options": [
                    {
                        "id": 31 + offset,
                        "text": "Mañana",
                        "checked": self.choice_has_selection,
                        "disabled": self.choice_option_disabled,
                    },
                    {
                        "id": 32 + offset,
                        "text": "Tarde",
                        "checked": False,
                        "disabled": False,
                    },
                ],
                "warnings": [],
            }
        raise AssertionError(f"Lectura inesperada: {function} {params}")

    def _mutation_response(self, function: str) -> Any:
        if function == "core_calendar_create_calendar_events":
            return {"events": [{"id": 101, "userid": self.user_id}], "warnings": []}
        if function == "core_calendar_delete_calendar_events":
            return None
        if function == "mod_forum_add_discussion":
            return {"discussionid": 77, "warnings": []}
        if function == "mod_forum_add_discussion_post":
            return {"postid": 99, "warnings": []}
        if function == "mod_choice_submit_choice_response":
            return {"answers": [{"id": 70}], "warnings": []}
        if function == "mod_choice_delete_choice_responses":
            return {"status": True, "warnings": []}
        raise AssertionError(function)

    def change_context(self, name: str) -> None:
        if name == "create_calendar":
            self.full_name = "Alumna USC — nombre actualizado"
        elif name == "delete_calendar":
            self.event_name = "Recordatorio actualizado"
        elif name == "create_discussion":
            self.forum_name = "Foro renombrado"
        elif name == "reply_post":
            self.parent_subject = "Pregunta editada"
        elif name in {"submit_choice", "cancel_choice"}:
            self.choice_option_offset = 100
        else:  # pragma: no cover - invariant of CASES
            raise AssertionError(name)

    def deny(self, name: str) -> None:
        if name == "create_calendar":
            self.calendar_can_manage = False
        elif name == "delete_calendar":
            self.event_owner_id = 999
        elif name == "create_discussion":
            self.forum_can_start = False
        elif name == "reply_post":
            self.forum_can_reply = False
        elif name == "submit_choice":
            self.choice_option_disabled = True
            self.choice_has_selection = False
        elif name == "cancel_choice":
            self.choice_has_selection = False
        else:  # pragma: no cover - invariant of CASES
            raise AssertionError(name)


@pytest.fixture(autouse=True)
def clear_confirmations() -> None:
    ACTION_CONFIRMATIONS.clear()


def _service_with(gateway: FakeContextualGateway) -> UscService:
    service = object.__new__(UscService)
    service._campus = lambda: gateway
    return service


async def _preview(service: UscService, case: ActionCase) -> dict[str, Any]:
    return await getattr(service, case.preview_method)(**case.arguments)


async def _execute(
    service: UscService,
    case: ActionCase,
    token: str,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return await getattr(service, case.execute_method)(
        **(arguments or case.arguments),
        confirmation_token=token,
    )


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
async def test_preview_invokes_only_context_reads(case: ActionCase) -> None:
    gateway = FakeContextualGateway()

    preview = await _preview(_service_with(gateway), case)

    assert preview["allowed"] is True
    assert preview["requires_confirmation"] is True
    assert preview["confirmation_token"]
    assert gateway.read_calls
    assert gateway.mutation_calls == []
    assert gateway.required
    assert case.mutation in gateway.required[0]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
async def test_execution_after_exact_token_performs_one_mutation(case: ActionCase) -> None:
    gateway = FakeContextualGateway()
    service = _service_with(gateway)
    preview = await _preview(service, case)
    gateway.calls.clear()

    result = await _execute(service, case, preview["confirmation_token"])

    assert result["ok"] is True
    assert [function for function, _ in gateway.mutation_calls] == [case.mutation]
    assert gateway.calls[-1][0] == case.mutation


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
async def test_changed_parameters_invalidate_token_before_mutation(case: ActionCase) -> None:
    gateway = FakeContextualGateway()
    service = _service_with(gateway)
    preview = await _preview(service, case)
    gateway.calls.clear()

    with pytest.raises(ValueError, match="Token de confirmación inválido"):
        await _execute(
            service,
            case,
            preview["confirmation_token"],
            arguments=case.changed_arguments,
        )

    assert gateway.mutation_calls == []


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
async def test_changed_remote_context_invalidates_token_before_mutation(case: ActionCase) -> None:
    gateway = FakeContextualGateway()
    service = _service_with(gateway)
    preview = await _preview(service, case)
    gateway.calls.clear()
    gateway.change_context(case.name)

    with pytest.raises(ValueError, match="Token de confirmación inválido"):
        await _execute(service, case, preview["confirmation_token"])

    assert gateway.mutation_calls == []


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
async def test_denied_preview_never_emits_token_or_invokes_mutation(case: ActionCase) -> None:
    gateway = FakeContextualGateway()
    gateway.deny(case.name)

    preview = await _preview(_service_with(gateway), case)

    assert preview["allowed"] is False
    assert preview["requires_confirmation"] is False
    assert "confirmation_token" not in preview
    assert gateway.mutation_calls == []
    assert gateway.status_calls == 0


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
async def test_error_after_single_mutation_is_unknown_and_never_retried(case: ActionCase) -> None:
    gateway = FakeContextualGateway()
    service = _service_with(gateway)
    preview = await _preview(service, case)
    gateway.calls.clear()
    gateway.mutation_error = True

    result = await _execute(service, case, preview["confirmation_token"])

    assert result["request_may_have_been_sent"] is True
    assert result["outcome"] == "unknown"
    assert result["do_not_retry"] is True
    assert [function for function, _ in gateway.mutation_calls] == [case.mutation]
