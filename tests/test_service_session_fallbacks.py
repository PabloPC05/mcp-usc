from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from mcp_usc.campus import (
    CampusCapabilityUnavailable,
    CampusMutationOutcomeUnknown,
    CampusProtocolError,
)
from mcp_usc.confirmations import ACTION_CONFIRMATIONS
from mcp_usc.local_files import inspect_upload_files
from mcp_usc.service import UscService, _capture_confirmed_uploads
from mcp_usc.settings import Settings


class FakeSessionForms:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.unknown_save = False

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
        if self.unknown_save:
            raise CampusMutationOutcomeUnknown("unknown")
        return {"request_sent": True, "outcome": "unknown"}

    async def prepare_assignment_submit(self, course_module_id: int) -> dict[str, Any]:
        self.calls.append(("prepare_assignment_submit", (course_module_id,)))
        return {"supported": True}

    async def submit_assignment(
        self,
        course_module_id: int,
        values: Mapping[str, Any] | None,
        *,
        confirmed: bool,
    ) -> dict[str, Any]:
        self.calls.append(("submit_assignment", (course_module_id, dict(values or {}), confirmed)))
        return {"request_sent": True, "outcome": "unknown"}

    async def inspect_assignment_delete(self, course_module_id: int) -> dict[str, Any]:
        self.calls.append(("inspect_assignment_delete", (course_module_id,)))
        return {"delete_action_detected": True}

    async def delete_assignment(
        self, course_module_id: int, *, confirmed: bool
    ) -> dict[str, Any]:
        self.calls.append(("delete_assignment", (course_module_id, confirmed)))
        return {"request_sent": True, "outcome": "unknown"}

    async def replace_assignment_files(
        self, course_module_id: int, uploads: list[Any], *, confirmed: bool
    ) -> dict[str, Any]:
        self.calls.append(("replace_assignment_files", (course_module_id, uploads, confirmed)))
        return {"request_sent": True, "outcome": "unknown"}

    async def delete_assignment_files(
        self, course_module_id: int, *, confirmed: bool
    ) -> dict[str, Any]:
        self.calls.append(("delete_assignment_files", (course_module_id, confirmed)))
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
        if function == "core_courseformat_get_state":
            return {
                "cm": [
                    {"id": 17, "module": "assign", "name": "Tarea", "uservisible": True},
                    {"id": 18, "module": "quiz", "name": "Quiz"},
                ]
            }
        if self.ambiguous:
            raise CampusProtocolError(f"respuesta ambigua de {function}")
        raise CampusCapabilityUnavailable(f"{function} no está disponible")

    def session_forms(self) -> FakeSessionForms | None:
        return self.forms if self.forms_enabled else None

    async def list_courses(self, include_archived: bool = False) -> list[dict[str, Any]]:
        return [{"id": 7, "fullname": "Curso"}]


@pytest.fixture(autouse=True)
def clear_confirmations() -> None:
    ACTION_CONFIRMATIONS.clear()


def _service_with(gateway: FakeSessionGateway) -> UscService:
    service = object.__new__(UscService)
    service.settings = None
    service._campus = lambda: gateway
    return service


async def test_online_submission_preview_is_pure_and_issues_confirmation() -> None:
    gateway = FakeSessionGateway()
    service = _service_with(gateway)

    preview = await service.preview_save_online_submission(None, "Texto", 17)

    assert preview["allowed"] is True
    assert preview["status"]["unchecked_until_execution"] is True
    assert gateway.forms.calls == []
    assert gateway.invoke_calls == []


async def test_online_submission_effect_uses_confirmed_http_form() -> None:
    gateway = FakeSessionGateway()
    service = _service_with(gateway)
    preview = await service.preview_save_online_submission(None, "Texto", 17)

    result = await service.save_online_submission(
        None, "Texto", preview["confirmation_token"], 17
    )

    assert result["request_sent"] is True
    assert gateway.forms.calls == [
        ("save_assignment", (17, {"onlinetext_editor[text]": "Texto"}, True))
    ]
    assert gateway.invoke_calls == []


async def test_session_assignment_rejects_transport_before_inspection() -> None:
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

    assert gateway.forms.calls == []

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


async def test_session_assignment_timeout_returns_unknown_without_fallback() -> None:
    gateway = FakeSessionGateway()
    service = _service_with(gateway)
    gateway.forms.unknown_save = True
    preview = await service.preview_save_online_submission(None, "Texto", 17)

    result = await service.save_online_submission(
        None, "Texto", preview["confirmation_token"], 17
    )

    assert result["outcome"] == "unknown"
    assert result["do_not_retry"] is True
    assert len(gateway.forms.calls) == 1
    assert gateway.invoke_calls == []


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


async def test_assignment_stateful_inspection_requires_fresh_confirmation() -> None:
    gateway = FakeSessionGateway()
    service = _service_with(gateway)

    preview = await service.inspect_submission_status(17, None)

    assert preview["stateful_read"] is True
    assert gateway.forms.calls == []

    result = await service.inspect_submission_status(17, preview["confirmation_token"])

    assert result["stateful_inspection_confirmed"] is True
    assert gateway.forms.calls == [("inspect_assignment", (17,))]


async def test_session_list_assignments_uses_ajax_course_state_and_exposes_only_cmid() -> None:
    gateway = FakeSessionGateway()
    service = _service_with(gateway)

    result = await service.list_assignments(None)

    assert result["assignments"] == [
        {
            "id": None,
            "assignment_id": None,
            "cmid": 17,
            "course_module_id": 17,
            "name": "Tarea",
            "course_id": 7,
            "course_name": "Curso",
            "visible": True,
            "instance_id_available": False,
            "transport": "moodle_ajax_course_state",
            "content_is_untrusted": True,
        }
    ]
    assert gateway.invoke_calls == ["core_courseformat_get_state"]
    assert gateway.forms.calls == []


async def test_submit_and_remove_previews_do_not_open_assignment_pages() -> None:
    gateway = FakeSessionGateway()
    service = _service_with(gateway)

    submit = await service.preview_submit_assignment(None, True, 17)
    remove = await service.preview_remove_submission(None, 17)

    assert gateway.forms.calls == []
    await service.submit_assignment(None, True, submit["confirmation_token"], 17)
    await service.remove_submission(None, remove["confirmation_token"], 17)
    assert gateway.forms.calls == [
        ("submit_assignment", (17, {"submissionstatement": True}, True)),
        ("delete_assignment", (17, True)),
    ]


def _upload_settings(root: Path) -> Settings:
    return Settings(
        moodle_url="https://cv.usc.es",
        moodle_token=None,
        browser_channel="chromium",
        browser_profile_dir=root / "profile",
        exam_sources=(),
        upload_root=root,
        max_upload_bytes=1024,
    )


async def test_session_file_replace_passes_exact_confirmed_bytes(tmp_path: Path) -> None:
    source = tmp_path / "answer.txt"
    source.write_bytes(b"confirmed")
    gateway = FakeSessionGateway()
    service = _service_with(gateway)
    service.settings = _upload_settings(tmp_path)

    preview = await service.preview_replace_submission_files(None, ["answer.txt"], 17)
    result = await service.replace_submission_files(
        None,
        ["answer.txt"],
        preview["confirmation_token"],
        17,
    )

    assert result["request_sent"] is True
    call = gateway.forms.calls[-1]
    assert call[0] == "replace_assignment_files"
    upload = call[1][1][0]
    assert upload.filename == "answer.txt"
    assert upload.content == b"confirmed"


def test_capture_rejects_file_changed_after_confirmation(tmp_path: Path) -> None:
    source = tmp_path / "answer.txt"
    source.write_bytes(b"original")
    settings = _upload_settings(tmp_path)
    inspected = inspect_upload_files(settings, ["answer.txt"])
    source.write_bytes(b"modified")

    with pytest.raises(ValueError, match="cambio despues de la confirmacion"):
        _capture_confirmed_uploads(inspected, settings.max_upload_bytes)
