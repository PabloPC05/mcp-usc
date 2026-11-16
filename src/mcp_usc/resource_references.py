"""Signed, self-contained references to Moodle files returned by a listing.

Listings hand the model an opaque ``resource_token`` instead of asking it to send
raw URLs back, so ``read_course_resource`` only downloads files this server listed.
The token carries its own payload and an HMAC, so it needs no process-local state:
every process that shares ``MCP_USC_STATE_SECRET`` (several serverless instances, a
restarted server) can verify it. Without that variable each process uses a random
key, which keeps references valid only inside the process that issued them.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any

RESOURCE_REFERENCE_TTL_SECONDS = 600
STATE_SECRET_ENV = "MCP_USC_STATE_SECRET"
_MIN_STATE_SECRET_LENGTH = 32
_MAX_TOKEN_LENGTH = 8_192
_VERSION = 1
_PROCESS_KEY = secrets.token_bytes(32)
_INVALID_REFERENCE = "El recurso no procede de una lista reciente o su referencia ha caducado."


def shared_state_secret_configured() -> bool:
    return len(os.getenv(STATE_SECRET_ENV, "").strip()) >= _MIN_STATE_SECRET_LENGTH


def _key() -> bytes:
    configured = os.getenv(STATE_SECRET_ENV, "").strip()
    if len(configured) >= _MIN_STATE_SECRET_LENGTH:
        return hashlib.sha256(
            b"mcp-usc/resource-reference/v1\0" + configured.encode("utf-8")
        ).digest()
    return _PROCESS_KEY


def _encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _signature(body: str) -> str:
    return _encode(hmac.new(_key(), body.encode("ascii"), hashlib.sha256).digest())


def issue_resource_reference(reference: dict[str, Any], *, now: float | None = None) -> str:
    issued_at = time.time() if now is None else now
    payload = {
        "v": _VERSION,
        "exp": int(issued_at) + RESOURCE_REFERENCE_TTL_SECONDS,
        "url": str(reference["url"]),
        "filename": str(reference.get("filename") or "resource"),
        "media_type": str(reference.get("media_type") or ""),
        "declared_size": int(reference.get("declared_size") or 0),
    }
    body = _encode(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
            "utf-8"
        )
    )
    return f"{body}.{_signature(body)}"


def resolve_resource_reference(token: str, *, now: float | None = None) -> dict[str, Any]:
    if not isinstance(token, str) or len(token) > _MAX_TOKEN_LENGTH or token.count(".") != 1:
        raise ValueError(_INVALID_REFERENCE)
    body, signature = token.split(".")
    if not body or not hmac.compare_digest(signature, _signature(body)):
        raise ValueError(_INVALID_REFERENCE)
    try:
        payload = json.loads(_decode(body))
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise ValueError(_INVALID_REFERENCE) from exc
    current = time.time() if now is None else now
    if (
        not isinstance(payload, dict)
        or payload.get("v") != _VERSION
        or not isinstance(payload.get("exp"), int)
        or payload["exp"] < current
        or not isinstance(payload.get("url"), str)
    ):
        raise ValueError(_INVALID_REFERENCE)
    return {
        "url": payload["url"],
        "filename": str(payload.get("filename") or "resource"),
        "media_type": str(payload.get("media_type") or ""),
        "declared_size": int(payload.get("declared_size") or 0),
    }
