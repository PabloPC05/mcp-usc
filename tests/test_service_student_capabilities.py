from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from unittest.mock import AsyncMock

import pytest

from mcp_usc import server
from mcp_usc.campus import CampusError
from mcp_usc.confirmations import ACTION_CONFIRMATIONS
from mcp_usc.service import UscService


class FakeStudentGateway:
    def __init__(
        self,
        *,
        user_id: int = 5,
        available: set[str] | None = None,
        result: Any = None,
    ) -> None:
        self.user_id = user_id
        self.available = available
        self.result = {"html": "<b>respuesta</b>"} if result is None else result
        self.status_calls = 0
        self.available_calls = 0
        self.required: list[set[str]] = []
        self.invocations: list[tuple[str, dict[str, Any]]] = []

    async def status(self) -> dict[str, Any]:
        self.status_calls += 1
        return {"authenticated": True, "user_id": self.user_id}

    async def available_functions(self) -> set[str] | None:
        self.available_calls += 1
        return self.available

    async def require_functions(self, functions: set[str]) -> None:
        self.required.append(functions)

    async def invoke(self, function: str, arguments: Mapping[str, Any]) -> Any:
        self.invocations.append((function, dict(arguments)))
        return self.result


@pytest.fixture(autouse=True)
def clear_action_confirmations() -> None:
    ACTION_CONFIRMATIONS.clear()


def _service_with(gateway: FakeStudentGateway) -> UscService:
    service = object.__new__(UscService)
    service._campus = lambda: gateway
    return service


async def test_list_capabilities_reports_and_filters_known_token_functions() -> None:
    gateway = FakeStudentGateway(available={"core_user_get_user_preferences"})
    service = _service_with(gateway)

    result = await service.list_student_capabilities("account", "read", available_only=True)

    assert result["count"] == 1
    assert result["items"][0]["function"] == "core_user_get_user_preferences"
    assert result["availability_known"] is True
    assert result["available_only_applied"] is True
    assert gateway.status_calls == 1
    assert gateway.available_calls == 1
    assert gateway.invocations == []


async def test_list_capabilities_does_not_hide_items_when_availability_is_unknown() -> None:
    gateway = FakeStudentGateway(available=None)

    result = await _service_with(gateway).list_student_capabilities(
        "attempts", "action", available_only=True
    )

    assert result["count"] == 1
    assert result["availability_known"] is False
    assert result["available_only_applied"] is False
    assert result["items"][0]["available_for_configured_token"] is None


async def test_student_read_binds_identity_checks_capability_and_sanitises_result() -> None:
    gateway = FakeStudentGateway(result={"name": "<b>Ana</b><script>bad()</script>"})
    service = _service_with(gateway)

    result = await service.call_student_read(
        "core_user_get_user_preferences", {"userid": 5, "name": "lang"}
    )

    assert gateway.required == [{"core_user_get_user_preferences"}]
    assert gateway.invocations == [
        ("core_user_get_user_preferences", {"userid": 5, "name": "lang"})
    ]
    assert result == {
        "function": "core_user_get_user_preferences",
        "category": "account",
        "result": {"name": "Ana"},
        "content_is_untrusted": True,
    }


@pytest.mark.parametrize(
    ("function", "arguments"),
    [
        ("core_user_update_user_preferences", {}),
        ("not_in_the_allowlist", {}),
        ("core_user_get_user_preferences", {"userid": 999}),
        ("core_user_get_user_preferences", {"nested": {"token": "secret"}}),
    ],
)
async def test_invalid_student_read_never_invokes_moodle(
    function: str, arguments: dict[str, Any]
) -> None:
    gateway = FakeStudentGateway()

    with pytest.raises(ValueError):
        await _service_with(gateway).call_student_read(function, arguments)

    assert gateway.invocations == []


async def test_action_preview_has_full_consequences_and_never_invokes_mutator() -> None:
    gateway = FakeStudentGateway()
    service = _service_with(gateway)
    arguments = {"preferences": [{"type": "drawer-open", "value": "1"}]}

    preview = await service.student_action(
        "core_user_update_user_preferences", arguments, confirmation_token=None
    )

    assert preview["preview"] is True
    assert preview["function"] == "core_user_update_user_preferences"
    assert preview["category"] == "account"
    assert preview["destructive"] is False
    assert preview["consequence"]
    assert preview["arguments"] == arguments
    assert preview["authenticated_user_id"] == 5
    assert preview["requires_confirmation"] is True
    assert gateway.required == [{"core_user_update_user_preferences"}]
    assert gateway.invocations == []


async def test_destructive_action_is_catalogued_but_generic_execution_is_refused() -> None:
    gateway = FakeStudentGateway()

    with pytest.raises(ValueError, match="no puede ejecutarse.*interfaz genérica"):
        await _service_with(gateway).student_action(
            "mod_forum_delete_post", {"postid": 17}, confirmation_token=None
        )

    assert gateway.status_calls == 0
    assert gateway.required == []
    assert gateway.invocations == []


async def test_exact_confirmation_executes_once_and_sanitises_remote_result() -> None:
    gateway = FakeStudentGateway(result={"message": "<p>guardado</p>"})
    service = _service_with(gateway)
    arguments = {"preferences": [{"type": "drawer-open", "value": "1"}]}
    preview = await service.student_action(
        "core_user_update_user_preferences", arguments, confirmation_token=None
    )

    result = await service.student_action(
        "core_user_update_user_preferences",
        arguments,
        confirmation_token=preview["confirmation_token"],
    )

    assert result == {
        "request_sent": True,
        "outcome": "reported_by_moodle",
        "function": "core_user_update_user_preferences",
        "result": {"message": "guardado"},
        "content_is_untrusted": True,
    }
    assert gateway.invocations == [("core_user_update_user_preferences", arguments)]

    with pytest.raises(ValueError, match="Token de confirmación inválido"):
        await service.student_action(
            "core_user_update_user_preferences",
            arguments,
            confirmation_token=preview["confirmation_token"],
        )
    assert len(gateway.invocations) == 1


@pytest.mark.parametrize(
    ("confirmed_function", "confirmed_arguments"),
    [
        (
            "core_course_set_favourite_courses",
            {"preferences": [{"type": "drawer-open", "value": "1"}]},
        ),
        (
            "core_user_update_user_preferences",
            {"preferences": [{"type": "drawer-open", "value": "0"}]},
        ),
    ],
)
async def test_confirmation_is_exact_and_a_mismatch_is_consumed_without_invocation(
    confirmed_function: str, confirmed_arguments: dict[str, Any]
) -> None:
    gateway = FakeStudentGateway()
    service = _service_with(gateway)
    original_arguments = {"preferences": [{"type": "drawer-open", "value": "1"}]}
    preview = await service.student_action(
        "core_user_update_user_preferences", original_arguments, confirmation_token=None
    )
    token = preview["confirmation_token"]

    with pytest.raises(ValueError, match="Token de confirmación inválido"):
        await service.student_action(confirmed_function, confirmed_arguments, token)
    with pytest.raises(ValueError, match="Token de confirmación inválido"):
        await service.student_action("core_user_update_user_preferences", original_arguments, token)

    assert gateway.invocations == []


async def test_confirmation_is_bound_to_the_account_that_previewed_it() -> None:
    gateway = FakeStudentGateway(user_id=5)
    service = _service_with(gateway)
    preview = await service.student_action(
        "core_user_update_user_preferences", {}, confirmation_token=None
    )

    gateway.user_id = 6
    with pytest.raises(ValueError, match="Token de confirmación inválido"):
        await service.student_action(
            "core_user_update_user_preferences", {}, preview["confirmation_token"]
        )

    assert gateway.invocations == []


async def test_ambiguous_failure_after_confirmation_is_never_retried() -> None:
    class FailingGateway(FakeStudentGateway):
        async def invoke(self, function: str, arguments: Mapping[str, Any]) -> Any:
            self.invocations.append((function, dict(arguments)))
            raise CampusError("timeout after dispatch")

    gateway = FailingGateway()
    service = _service_with(gateway)
    function = "core_user_update_user_preferences"
    arguments = {"preferences": [{"type": "drawer-open", "value": "1"}]}
    preview = await service.student_action(function, arguments, confirmation_token=None)

    result = await service.student_action(
        function,
        arguments,
        confirmation_token=preview["confirmation_token"],
    )

    assert result["outcome"] == "unknown"
    assert result["request_may_have_been_sent"] is True
    assert result["do_not_retry"] is True
    assert len(gateway.invocations) == 1
    with pytest.raises(ValueError, match="Token de confirmación inválido"):
        await service.student_action(function, arguments, preview["confirmation_token"])
    assert len(gateway.invocations) == 1


async def test_preview_rejects_another_user_and_secrets_before_capability_probe() -> None:
    gateway = FakeStudentGateway()
    service = _service_with(gateway)

    with pytest.raises(ValueError, match="identidad autenticada"):
        await service.student_action(
            "core_user_update_user_preferences", {"userid": 6}, confirmation_token=None
        )
    with pytest.raises(ValueError, match="sensible"):
        await service.student_action(
            "core_user_update_user_preferences", {"cookie": "secret"}, confirmation_token=None
        )

    assert gateway.required == []
    assert gateway.invocations == []


@pytest.mark.parametrize(
    ("method", "args", "function", "arguments"),
    [
        (
            "get_my_profile",
            (),
            "core_user_get_users_by_field",
            {"field": "id", "values": ["5"]},
        ),
        (
            "get_my_preferences",
            ("lang",),
            "core_user_get_user_preferences",
            {"userid": 5, "name": "lang"},
        ),
        (
            "list_course_participants",
            (11, 3, 25),
            "core_enrol_get_enrolled_users",
            {
                "courseid": 11,
                "options": [
                    {"name": "limitfrom", "value": 3},
                    {"name": "limitnumber", "value": 25},
                    {"name": "sortby", "value": "fullname"},
                    {"name": "sortdirection", "value": "ASC"},
                ],
            },
        ),
        (
            "list_my_groups",
            (11,),
            "core_group_get_course_user_groups",
            {"courseid": 11, "userid": 5, "groupingid": 0},
        ),
        (
            "get_my_grades",
            (),
            "gradereport_overview_get_course_grades",
            {"userid": 5},
        ),
        (
            "get_my_grades",
            (11,),
            "gradereport_user_get_grade_items",
            {"courseid": 11, "userid": 5, "groupid": 0},
        ),
        (
            "list_notifications",
            ("all", 4, 20),
            "message_popup_get_popup_notifications",
            {"useridto": 5, "newestfirst": True, "limit": 20, "offset": 4},
        ),
        (
            "list_my_badges",
            (11, 2, 25),
            "core_badges_get_user_badges",
            {
                "userid": 5,
                "courseid": 11,
                "page": 2,
                "perpage": 25,
                "search": "",
                "onlypublic": False,
            },
        ),
        ("get_private_files_info", (), "core_user_get_private_files_info", {}),
    ],
)
async def test_convenience_read_wrappers_use_identity_bound_moodle_parameters(
    method: str,
    args: tuple[Any, ...],
    function: str,
    arguments: dict[str, Any],
) -> None:
    gateway = FakeStudentGateway()
    service = _service_with(gateway)

    await getattr(service, method)(*args)

    assert gateway.invocations == [(function, arguments)]
    assert gateway.required == [{function}]


async def test_completion_wrapper_reads_both_scoped_reports_and_sanitises_them() -> None:
    gateway = FakeStudentGateway(result={"progress": "<b>ok</b>"})

    result = await _service_with(gateway).get_my_completion(11)

    expected_functions = {
        "core_completion_get_activities_completion_status",
        "core_completion_get_course_completion_status",
    }
    assert gateway.required == [expected_functions]
    assert gateway.invocations == [
        (
            "core_completion_get_activities_completion_status",
            {"courseid": 11, "userid": 5},
        ),
        (
            "core_completion_get_course_completion_status",
            {"courseid": 11, "userid": 5},
        ),
    ]
    assert result["activities"] == {"progress": "ok"}
    assert result["course"] == {"progress": "ok"}
    assert result["content_is_untrusted"] is True


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("get_my_preferences", ("x" * 101,)),
        ("list_course_participants", (0, 0, 20)),
        ("list_course_participants", (1, -1, 20)),
        ("list_course_participants", (1, 0, 101)),
        ("list_my_groups", (0,)),
        ("get_my_grades", (0,)),
        ("get_my_completion", (0,)),
        ("list_notifications", ("invalid", 0, 20)),
        ("list_notifications", ("all", -1, 20)),
        ("list_my_badges", (-1, 0, 50)),
        ("list_my_badges", (0, -1, 50)),
        ("list_my_badges", (0, 0, 101)),
    ],
)
async def test_convenience_wrappers_validate_before_invoking_moodle(
    method: str, args: tuple[Any, ...]
) -> None:
    gateway = FakeStudentGateway()

    with pytest.raises(ValueError):
        await getattr(_service_with(gateway), method)(*args)

    assert gateway.invocations == []


async def test_server_generic_tools_are_thin_service_wrappers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = type("FakeService", (), {})()
    fake.list_student_capabilities = AsyncMock(return_value={"kind": "catalog"})
    fake.call_student_read = AsyncMock(return_value={"kind": "read"})
    fake.student_action = AsyncMock(side_effect=[{"kind": "preview"}, {"kind": "write"}])
    monkeypatch.setattr(server, "_service", lambda: fake)

    assert await server.list_student_capabilities("account", "read", True) == {"kind": "catalog"}
    assert await server.call_student_read("core_user_get_user_preferences", {"name": "lang"}) == {
        "kind": "read"
    }
    assert await server.preview_student_action("core_user_update_user_preferences", {}) == {
        "kind": "preview"
    }
    assert await server.execute_student_action(
        "core_user_update_user_preferences", {}, "nonce"
    ) == {"kind": "write"}

    fake.list_student_capabilities.assert_awaited_once_with("account", "read", True)
    fake.call_student_read.assert_awaited_once_with(
        "core_user_get_user_preferences", {"name": "lang"}
    )
    assert fake.student_action.await_args_list[0].args == (
        "core_user_update_user_preferences",
        {},
    )
    assert fake.student_action.await_args_list[0].kwargs == {"confirmation_token": None}
    assert fake.student_action.await_args_list[1].args == (
        "core_user_update_user_preferences",
        {},
        "nonce",
    )


@pytest.mark.parametrize(
    ("tool_name", "service_name", "args"),
    [
        ("get_my_profile", "get_my_profile", ()),
        ("get_my_preferences", "get_my_preferences", ("lang",)),
        ("list_course_participants", "list_course_participants", (4, 2, 10)),
        ("list_my_groups", "list_my_groups", (4,)),
        ("get_my_grades", "get_my_grades", (4,)),
        ("get_my_completion", "get_my_completion", (4,)),
        ("list_notifications", "list_notifications", ("unread", 2, 10)),
        ("list_my_badges", "list_my_badges", (4, 1, 10)),
        ("get_private_files_info", "get_private_files_info", ()),
    ],
)
async def test_server_convenience_tools_forward_exact_parameters(
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
    service_name: str,
    args: tuple[Any, ...],
) -> None:
    method = AsyncMock(return_value={"ok": True})
    fake = type("FakeService", (), {service_name: method})()
    monkeypatch.setattr(server, "_service", lambda: fake)

    assert await getattr(server, tool_name)(*args) == {"ok": True}
    method.assert_awaited_once_with(*args)
