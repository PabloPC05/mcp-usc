from __future__ import annotations

from typing import Any

import pytest

from mcp_usc.activity_actions import (
    ActivityActionError,
    mark_course_self_completed,
    preview_mark_course_self_completed,
    preview_update_activity_completion_status_manually,
    update_activity_completion_status_manually,
)


class FakeInvoke:
    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, function: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((function, dict(arguments)))
        response = self.responses[function]
        return response() if callable(response) else response


def _module(completion: int = 1) -> dict[str, Any]:
    return {
        "cm": {
            "id": 31,
            "course": 7,
            "name": "Lectura inicial",
            "modname": "page",
            "completion": completion,
        },
        "warnings": [],
    }


def _activity_invoke(*, completion: int = 1, state: int = 0) -> FakeInvoke:
    return FakeInvoke(
        {
            "core_webservice_get_site_info": {"userid": 9, "fullname": "Alumna USC"},
            "core_course_get_course_module": _module(completion),
            "core_completion_get_activities_completion_status": {
                "statuses": [{"cmid": 31, "state": state}],
                "warnings": [],
            },
            "core_completion_update_activity_completion_status_manually": {
                "status": True,
                "warnings": [],
            },
        }
    )


@pytest.mark.asyncio
async def test_manual_completion_preview_binds_course_module_and_user_state() -> None:
    invoke = _activity_invoke(state=0)

    result = await preview_update_activity_completion_status_manually(
        invoke, course_id=7, cmid=31, completed=True
    )

    assert result["allowed"] is True
    assert result["current_state"] == 0
    assert result["affected_scope"] == "authenticated_user_activity_only"
    assert [function for function, _ in invoke.calls] == [
        "core_webservice_get_site_info",
        "core_course_get_course_module",
        "core_completion_get_activities_completion_status",
    ]


@pytest.mark.asyncio
async def test_manual_completion_rejects_automatic_modules_without_mutation() -> None:
    invoke = _activity_invoke(completion=2)

    result = await preview_update_activity_completion_status_manually(
        invoke, course_id=7, cmid=31, completed=True
    )

    assert result["allowed"] is False
    assert result["code"] == "activity_completion_not_manual"
    assert all(
        function != "core_completion_update_activity_completion_status_manually"
        for function, _ in invoke.calls
    )


@pytest.mark.asyncio
async def test_manual_completion_rejects_module_from_another_course() -> None:
    invoke = _activity_invoke()
    invoke.responses["core_course_get_course_module"] = {
        **_module(),
        "cm": {**_module()["cm"], "course": 8},
    }

    with pytest.raises(ActivityActionError, match="no pertenece al curso"):
        await preview_update_activity_completion_status_manually(
            invoke, course_id=7, cmid=31, completed=True
        )


@pytest.mark.asyncio
async def test_manual_completion_fails_closed_without_current_state() -> None:
    invoke = _activity_invoke()
    invoke.responses["core_completion_get_activities_completion_status"] = {
        "statuses": [{"cmid": 31}],
        "warnings": [],
    }

    with pytest.raises(ActivityActionError, match="estado actual"):
        await preview_update_activity_completion_status_manually(
            invoke, course_id=7, cmid=31, completed=True
        )


@pytest.mark.asyncio
async def test_manual_completion_execution_makes_exactly_one_write() -> None:
    invoke = _activity_invoke()

    result = await update_activity_completion_status_manually(
        invoke, cmid=31, completed=False
    )

    assert result["ok"] is True
    assert invoke.calls == [
        (
            "core_completion_update_activity_completion_status_manually",
            {"cmid": 31, "completed": False},
        )
    ]


@pytest.mark.asyncio
async def test_manual_completion_does_not_report_a_rejected_write_as_success() -> None:
    invoke = _activity_invoke()
    invoke.responses["core_completion_update_activity_completion_status_manually"] = {
        "status": False,
        "warnings": [],
    }

    result = await update_activity_completion_status_manually(
        invoke, cmid=31, completed=True
    )

    assert result["ok"] is False
    assert result["result"] == "rejected"


def _course_invoke(*, allowed: bool = True, completed: bool = False) -> FakeInvoke:
    status: dict[str, Any] = {
        "completionstatus": {
            "completed": completed,
            "aggregation": 1,
            "completions": (
                [
                    {
                        "type": 1,
                        "title": "Auto-finalización",
                        "status": "Sí" if completed else "No",
                        "complete": completed,
                        "timecompleted": 0,
                        "details": {
                            "type": "Auto-finalización",
                            "criteria": "El estudiante completa el curso",
                            "requirement": "Marcar como completado",
                            "status": "Sí" if completed else "No",
                        },
                    }
                ]
                if allowed
                else []
            ),
        },
        "warnings": [],
    }
    return FakeInvoke(
        {
            "core_webservice_get_site_info": {"userid": 9, "fullname": "Alumna USC"},
            "core_completion_get_course_completion_status": status,
            "core_completion_mark_course_self_completed": {"status": True, "warnings": []},
        }
    )


@pytest.mark.asyncio
async def test_course_self_completion_requires_explicit_moodle_permission() -> None:
    invoke = _course_invoke(allowed=False)

    result = await preview_mark_course_self_completed(invoke, course_id=7)

    assert result["allowed"] is False
    assert result["code"] == "self_completion_not_allowed"
    assert all(
        function != "core_completion_mark_course_self_completed"
        for function, _ in invoke.calls
    )


@pytest.mark.asyncio
async def test_course_self_completion_preview_and_exact_write() -> None:
    invoke = _course_invoke()

    preview = await preview_mark_course_self_completed(invoke, course_id=7)
    result = await mark_course_self_completed(invoke, course_id=7)

    assert preview["allowed"] is True
    assert result["ok"] is True
    assert [function for function, _ in invoke.calls] == [
        "core_webservice_get_site_info",
        "core_completion_get_course_completion_status",
        "core_completion_mark_course_self_completed",
    ]


@pytest.mark.asyncio
async def test_course_self_completion_does_not_offer_already_completed_course() -> None:
    invoke = _course_invoke(completed=True)

    result = await preview_mark_course_self_completed(invoke, course_id=7)

    assert result["allowed"] is False
    assert result["code"] == "course_already_completed"


@pytest.mark.asyncio
async def test_course_self_completion_fails_closed_on_non_boolean_state() -> None:
    invoke = _course_invoke()
    invoke.responses["core_completion_get_course_completion_status"]["completionstatus"][
        "completions"
    ][0]["complete"] = "false"

    with pytest.raises(ActivityActionError, match="booleano"):
        await preview_mark_course_self_completed(invoke, course_id=7)
