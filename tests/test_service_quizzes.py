from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from mcp_usc.campus import CampusCapabilityUnavailable
from mcp_usc.confirmations import ACTION_CONFIRMATIONS
from mcp_usc.service import UscService


class FakeQuizGateway:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Mapping[str, Any]]] = []
        self.user_id = 5

    async def status(self) -> dict[str, Any]:
        return {"authenticated": True, "user_id": self.user_id}

    async def invoke(self, function: str, arguments: Mapping[str, Any]) -> Any:
        self.calls.append((function, arguments))
        if function == "mod_quiz_start_attempt":
            return {"attempt": {"id": 91, "quiz": 7, "state": "inprogress"}}
        if function == "mod_quiz_save_attempt":
            return {"status": True, "warnings": []}
        if function == "mod_quiz_process_attempt":
            return {"state": "finished", "warnings": []}
        if function == "mod_quiz_get_attempt_data":
            return {"attempt": {"id": 91, "quiz": 7, "state": "inprogress"}, "questions": []}
        raise AssertionError(function)


@pytest.fixture(autouse=True)
def clear_confirmations() -> None:
    ACTION_CONFIRMATIONS.clear()


def _service_with(gateway: FakeQuizGateway) -> UscService:
    service = object.__new__(UscService)
    service.settings = None
    service._campus = lambda: gateway
    return service


async def test_quiz_start_preview_never_calls_gateway_and_exact_confirmation_starts() -> None:
    gateway = FakeQuizGateway()
    service = _service_with(gateway)

    preview = await service.preview_start_quiz(7, None, False)

    assert preview["started"] is False
    assert gateway.calls == []

    result = await service.start_quiz(
        7,
        None,
        False,
        preview["confirmation_token"],
    )

    assert result["started"] is True
    assert gateway.calls[0][0] == "mod_quiz_start_attempt"


async def test_quiz_start_confirmation_rejects_parameter_change() -> None:
    gateway = FakeQuizGateway()
    service = _service_with(gateway)
    preview = await service.preview_start_quiz(7, None, False)

    with pytest.raises(ValueError, match="Token de confirmación inválido"):
        await service.start_quiz(8, None, False, preview["confirmation_token"])

    assert gateway.calls == []


async def test_quiz_confirmation_is_bound_to_authenticated_account() -> None:
    gateway = FakeQuizGateway()
    service = _service_with(gateway)
    preview = await service.preview_start_quiz(7, None, False)
    gateway.user_id = 6

    with pytest.raises(ValueError, match="Token de confirmación inválido"):
        await service.start_quiz(7, None, False, preview["confirmation_token"])

    assert gateway.calls == []


async def test_save_answers_requires_preview_of_exact_values() -> None:
    gateway = FakeQuizGateway()
    service = _service_with(gateway)
    responses = {"q7:1_sequencecheck": "2", "q7:1_answer": "1"}

    preview = await service.preview_save_quiz_answers(91, responses, None)

    assert preview["responses"] == responses
    assert gateway.calls == []
    result = await service.save_quiz_answers(
        91,
        responses,
        None,
        preview["confirmation_token"],
    )

    assert result["saved"] is True
    assert gateway.calls[0][0] == "mod_quiz_save_attempt"


async def test_finish_quiz_is_a_separate_confirmed_write() -> None:
    gateway = FakeQuizGateway()
    service = _service_with(gateway)
    preview = await service.preview_finish_quiz(91, None, False, None)

    assert gateway.calls == []
    result = await service.finish_quiz(
        91,
        None,
        False,
        None,
        preview["confirmation_token"],
    )

    assert result["finished"] is True
    assert gateway.calls[0][0] == "mod_quiz_process_attempt"


async def test_quiz_attempt_inspection_requires_confirmation_before_stateful_read() -> None:
    gateway = FakeQuizGateway()
    service = _service_with(gateway)

    with pytest.raises(CampusCapabilityUnavailable, match="preview_inspect_quiz_attempt"):
        await service.get_quiz_attempt_page(91, 0, None)

    preview = await service.inspect_quiz_attempt(91, 0, False, None, None)
    assert preview["preview"] is True
    assert gateway.calls == []

    result = await service.inspect_quiz_attempt(
        91,
        0,
        False,
        None,
        preview["confirmation_token"],
    )
    assert result["stateful_inspection_confirmed"] is True
    assert gateway.calls[0][0] == "mod_quiz_get_attempt_data"
