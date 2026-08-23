from __future__ import annotations

import pytest

from mcp_usc import confirmations
from mcp_usc.confirmations import ActionConfirmationStore


def test_confirmation_is_bound_to_exact_action_and_payload() -> None:
    store = ActionConfirmationStore()
    issued = store.issue("submit_assignment", {"assignment_id": 7, "text": "respuesta"})

    with pytest.raises(ValueError, match="Token de confirmación inválido"):
        store.consume(
            issued["confirmation_token"],
            "submit_assignment",
            {"assignment_id": 8, "text": "respuesta"},
        )


def test_confirmation_is_single_use() -> None:
    store = ActionConfirmationStore()
    payload = {"quiz_id": 3}
    issued = store.issue("start_quiz", payload)

    store.consume(issued["confirmation_token"], "start_quiz", payload)

    with pytest.raises(ValueError, match="Token de confirmación inválido"):
        store.consume(issued["confirmation_token"], "start_quiz", payload)


def test_expired_confirmation_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    now = 100.0
    monkeypatch.setattr(confirmations.time, "monotonic", lambda: now)
    store = ActionConfirmationStore(ttl_seconds=30)
    issued = store.issue("finish_quiz", {"attempt_id": 9})

    now = 131.0
    with pytest.raises(ValueError, match="Token de confirmación inválido"):
        store.consume(issued["confirmation_token"], "finish_quiz", {"attempt_id": 9})
