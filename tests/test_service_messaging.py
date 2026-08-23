from __future__ import annotations

from typing import Any

import pytest

from mcp_usc import service as service_module
from mcp_usc.service import UscService


class FakeMessageGateway:
    def __init__(self, result: dict[str, Any] | None = None) -> None:
        self.calls: list[tuple[int, str]] = []
        self.result = result or {"msgid": 99}
        self.user_id = 5

    async def status(self) -> dict[str, Any]:
        return {"authenticated": True, "user_id": self.user_id}

    async def send_message(self, recipient_user_id: int, text: str) -> dict[str, Any]:
        self.calls.append((recipient_user_id, text))
        return self.result


@pytest.fixture(autouse=True)
def clear_confirmation_tokens() -> None:
    service_module._MESSAGE_CONFIRMATIONS.clear()
    service_module._MESSAGE_CONTACTS.clear()


def _service_with(gateway: FakeMessageGateway) -> UscService:
    service = object.__new__(UscService)
    service.settings = None
    service._campus = lambda: gateway
    return service


def _remember_contact(user_id: int, name: str = "Profesor USC", now: float | None = None) -> None:
    base = service_module.time.monotonic() if now is None else now
    service_module._MESSAGE_CONTACTS[user_id] = (base + 600, 5, name)


async def test_message_preview_performs_no_gateway_call_and_confirmation_sends_once() -> None:
    gateway = FakeMessageGateway()
    service = _service_with(gateway)
    _remember_contact(42)

    preview = await service.send_message(42, "  Hola  ", confirmation_token=None)

    assert preview["sent"] is False
    assert preview["requires_confirmation"] is True
    assert preview["recipient_full_name"] == "Profesor USC"
    assert preview["text"] == "Hola"
    assert preview["expires_in_seconds"] == 300
    assert gateway.calls == []

    result = await service.send_message(
        42,
        "Hola",
        confirmation_token=preview["confirmation_token"],
    )

    assert result == {
        "sent": True,
        "recipient_user_id": 42,
        "recipient_full_name": "Profesor USC",
        "message_id": 99,
        "server_error": "",
    }
    assert gateway.calls == [(42, "Hola")]

    with pytest.raises(ValueError, match="Token de confirmación inválido"):
        await service.send_message(
            42,
            "Hola",
            confirmation_token=preview["confirmation_token"],
        )
    assert gateway.calls == [(42, "Hola")]


async def test_confirmation_token_is_bound_to_recipient_and_exact_text() -> None:
    gateway = FakeMessageGateway()
    service = _service_with(gateway)
    _remember_contact(42)
    _remember_contact(43)
    preview = await service.send_message(42, "Texto aprobado", confirmation_token=None)

    with pytest.raises(ValueError, match="Token de confirmación inválido"):
        await service.send_message(
            43,
            "Texto aprobado",
            confirmation_token=preview["confirmation_token"],
        )

    assert gateway.calls == []


async def test_expired_confirmation_token_does_not_send(monkeypatch) -> None:
    gateway = FakeMessageGateway()
    service = _service_with(gateway)
    now = 100.0
    monkeypatch.setattr(service_module.time, "monotonic", lambda: now)
    _remember_contact(42, now=now)
    preview = await service.send_message(42, "Texto aprobado", confirmation_token=None)

    now = 401.0
    with pytest.raises(ValueError, match="Token de confirmación inválido"):
        await service.send_message(
            42,
            "Texto aprobado",
            confirmation_token=preview["confirmation_token"],
        )

    assert gateway.calls == []


async def test_preview_requires_a_recently_searched_contact() -> None:
    gateway = FakeMessageGateway()
    service = _service_with(gateway)

    with pytest.raises(ValueError, match="búsqueda reciente"):
        await service.send_message(42, "Hola", confirmation_token=None)

    assert gateway.calls == []


async def test_send_accepts_message_id_from_http_gateway() -> None:
    gateway = FakeMessageGateway({"message_id": 101})
    service = _service_with(gateway)
    _remember_contact(42)
    preview = await service.send_message(42, "Hola", confirmation_token=None)

    result = await service.send_message(
        42,
        "Hola",
        confirmation_token=preview["confirmation_token"],
    )

    assert result["message_id"] == 101


async def test_message_confirmation_is_bound_to_authenticated_account() -> None:
    gateway = FakeMessageGateway()
    service = _service_with(gateway)
    _remember_contact(42)
    preview = await service.send_message(42, "Hola", confirmation_token=None)
    gateway.user_id = 6

    with pytest.raises(ValueError, match="cuenta de Moodle cambió"):
        await service.send_message(
            42,
            "Hola",
            confirmation_token=preview["confirmation_token"],
        )

    assert gateway.calls == []
