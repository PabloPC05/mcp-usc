from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Any


def _payload_digest(action: str, payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"action": action, "payload": payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


@dataclass(frozen=True, slots=True)
class _PendingAction:
    action: str
    digest: str = field(repr=False)
    expires_at: float


class ActionConfirmationStore:
    """Short-lived, in-memory confirmations bound to one exact action payload."""

    def __init__(self, ttl_seconds: int = 300) -> None:
        if ttl_seconds < 30 or ttl_seconds > 900:
            raise ValueError("ttl_seconds debe estar entre 30 y 900")
        self.ttl_seconds = ttl_seconds
        self._pending: dict[str, _PendingAction] = {}

    def issue(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not action or len(action) > 100:
            raise ValueError("action no es válida")
        now = time.monotonic()
        self._purge(now)
        token = secrets.token_urlsafe(18)
        self._pending[token] = _PendingAction(
            action=action,
            digest=_payload_digest(action, payload),
            expires_at=now + self.ttl_seconds,
        )
        return {
            "confirmation_token": token,
            "expires_in_seconds": self.ttl_seconds,
            "requires_confirmation": True,
        }

    def consume(self, token: str, action: str, payload: dict[str, Any]) -> None:
        if not isinstance(token, str) or not token:
            raise ValueError("Se requiere un token de confirmación")
        now = time.monotonic()
        self._purge(now)
        pending = self._pending.pop(token, None)
        digest = _payload_digest(action, payload)
        if (
            pending is None
            or pending.expires_at < now
            or pending.action != action
            or not hmac.compare_digest(pending.digest, digest)
        ):
            raise ValueError(
                "Token de confirmación inválido, caducado o ligado a otros parámetros. "
                "Solicita una nueva vista previa."
            )

    def clear(self) -> None:
        self._pending.clear()

    def _purge(self, now: float) -> None:
        expired = [token for token, item in self._pending.items() if item.expires_at < now]
        for token in expired:
            self._pending.pop(token, None)


ACTION_CONFIRMATIONS = ActionConfirmationStore()
