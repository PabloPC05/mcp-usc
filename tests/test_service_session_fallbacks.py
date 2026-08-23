from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from mcp_usc.campus import CampusCapabilityUnavailable, CampusProtocolError
from mcp_usc.confirmations import ACTION_CONFIRMATIONS
from mcp_usc.service import UscService


class FakeSessionForms:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def inspect_assignment(self, course_module_id: int) -> dict[str, Any]:
        self.calls.append(("inspect_assignment", (course_module_id,)))
        return {
            "forms": [{"action_path": "/mod/assign/view.php"}],
            "save_supported": True,
        }

    async def save_assignment(
        self,
        course_module_id: int,
        values: Mapping[str, Any],
        *,
        confirmed: bool,
    ) -> dict[str, Any]:
        self.calls.append(("save_assignment", (course_module_id, dict(values), confirmed)))
        return {"request_sent": True, "outcome": "unknown"}

    async def inspect_quiz_start(self, course_module_id: int) -> dict[str, Any]:
        self.calls.append(("inspect_quiz_start", (course_module_id,)))
        return {"forms": [{"action_path": "/mod/quiz/startattempt.php"}]}

    async def start_quiz(
        self,
        course_module_id: int,
        values: Mapping[str, Any] | None,
        *,
        confirmed: bool,
    ) -> dict[str, Any]:
        self.calls.append(("start_quiz", (course_module_id, dict(values or {}), confirmed)))
        return {"request_sent": True, "outcome": "unknown"}

    async def finish_quiz(self, attempt_id: int, *, confirmed: bool) -> dict[str, Any]:
        self.calls.append(("finish_quiz", (attempt_id, confirmed)))
        return {"request_sent": True, "outcome": "unknown"}

    async def inspect_quiz_finish(self, attempt_id: int) -> dict[str, Any]:
        self.calls.append(("inspect_quiz_finish", (attempt_id,)))
        return {"forms": [{"action_path": "/mod/quiz/processattempt.php"}]}


class FakeSessionGateway:
    def __init__(self) -> None:
        self.forms = FakeSessionForms()
        self.invoke_calls: list[str] = []
        self.ambiguous = False
        self.forms_enabled = True

    async def status(self) -> dict[str, Any]:
        return {"authenticated": True, "user_id": 5}

    async def invoke(self, function: str, arguments: Mapping[str, Any]) -> Any:
        self.invoke_calls.append(function)
        if self.ambiguous:
            raise CampusProtocolError(f"respuesta ambigua de {function}")
        raise CampusCapabilityUnavailable(f"{function} no está disponible")

    def session_forms(self) -> FakeSessionForms | None:
        return self.forms if self.forms_enabled else None


@pytest.fixture(autouse=True)
def clear_confirmations() -> None:
    ACTION_CONFIRMATIONS.clear()


def _service_with(gateway: FakeSessionGateway) -> UscService:
    service = object.__new__(UscService)
    service.settings = None
    service._campus = lambda: gateway
    return service


async def test_online_submission_falls_back_to_fresh_http_form() -> None:
    gateway = FakeSessionGateway()
    service = _service_with(gateway)
    preview = await service.preview_save_online_submission(None, "Texto", 17)

    result = await service.save_online_submission(
        None,
        "Texto",
        preview["confirmation_token"],
        17,
    )

    assert result["request_sent"] is True
    assert gateway.forms.calls[-1] == (
        "save_assignment",
        (17, {"onlinetext_editor[text]": "Texto"}, True),
    )


async def test_html_assignment_uses_cmid_without_inventing_assignment_id() -> None:
    gateway = FakeSessionGateway()
    service = _service_with(gateway)
    preview = await service.preview_save_online_submission(None, "Texto", 17)

    result = await service.save_online_submission(
        None,
        "Texto",
        preview["confirmation_token"],
        17,
    )

    assert result["request_sent"] is True
    assert gateway.invoke_calls == []


async def test_session_assignment_rejects_internal_id_cmid_pair_before_inspection() -> None:
    gateway = FakeSessionGateway()
    service = _service_with(gateway)

    with pytest.raises(ValueError, match="assignment_id=null"):
        await service.preview_save_online_submission(8, "Texto", 17)

    assert gateway.forms.calls == []
    assert gateway.invoke_calls == []


async def test_session_quiz_preview_requires_course_module_id() -> None:
    gateway = FakeSessionGateway()
    service = _service_with(gateway)

    with pytest.raises(ValueError, match="course_module_id"):
        await service.preview_start_quiz(None, None, False)


async def test_quiz_start_falls_back_to_http_form_with_exact_confirmation() -> None:
    gateway = FakeSessionGateway()
    service = _service_with(gateway)
    preview = await service.preview_start_quiz(None, {"quizpassword": "clave"}, False, 23)

    result = await service.start_quiz(
        None,
        {"quizpassword": "clave"},
        False,
        preview["confirmation_token"],
        23,
    )

    assert result["request_sent"] is True
    assert gateway.forms.calls[-1] == (
        "start_quiz",
        (23, {"quizpassword": "clave"}, True),
    )


async def test_ambiguous_write_result_never_falls_back_to_a_second_write() -> None:
    gateway = FakeSessionGateway()
    service = _service_with(gateway)
    preview = await service.preview_save_online_submission(None, "Texto", 17)
    gateway.ambiguous = True
    gateway.forms_enabled = False

    with pytest.raises(CampusProtocolError):
        await service.save_online_submission(
            None,
            "Texto",
            preview["confirmation_token"],
            17,
        )

    assert all(call[0] != "save_assignment" for call in gateway.forms.calls)


async def test_session_quiz_rejects_internal_id_cmid_pair_before_inspection() -> None:
    gateway = FakeSessionGateway()
    service = _service_with(gateway)

    with pytest.raises(ValueError, match="quiz_id=null"):
        await service.preview_start_quiz(7, None, False, 99)

    assert gateway.forms.calls == []


async def test_quiz_finish_form_fallback_refuses_to_discard_responses() -> None:
    gateway = FakeSessionGateway()
    service = _service_with(gateway)
    payload = {
        "attempt_id": 41,
        "responses": {"q1:1_answer": "2"},
        "time_up": False,
        "preflight_data": {},
    }
    preview = await service.preview_finish_quiz(
        41,
        payload["responses"],
        False,
        None,
    )

    with pytest.raises(CampusProtocolError, match="No se finalizó"):
        await service.finish_quiz(
            41,
            payload["responses"],
            False,
            None,
            preview["confirmation_token"],
        )

    assert all(call[0] != "finish_quiz" for call in gateway.forms.calls)
