from __future__ import annotations

import hashlib
import hmac
import os
from collections.abc import Awaitable, Callable

from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from .request_credentials import bind_request_credentials
from .server import mcp

ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]
AuthVerifier = tuple[str, str]
_MIN_AUTH_TOKEN_LENGTH = 32
_MAX_BEARER_TOKEN_LENGTH = 1_024
_MAX_MOODLE_CREDENTIAL_LENGTH = 4_096
_MCP_PATH = "/mcp"
_MOODLE_TOKEN_HEADER = "x-usc-moodle-token"
_MOODLE_SESSION_HEADER = "x-usc-moodle-session"
_SENSITIVE_REQUEST_HEADERS = {
    _MOODLE_TOKEN_HEADER.encode("ascii"),
    _MOODLE_SESSION_HEADER.encode("ascii"),
}


def _configured_auth_verifier() -> AuthVerifier | None:
    token = os.getenv("MCP_AUTH_TOKEN", "").strip()
    if len(token) >= _MIN_AUTH_TOKEN_LENGTH:
        return ("plain", token)

    token_digest = os.getenv("MCP_AUTH_TOKEN_SHA256", "").strip().casefold()
    if len(token_digest) == 64 and all(character in "0123456789abcdef" for character in token_digest):
        return ("sha256", token_digest)
    return None


def _header(scope: Scope, name: str) -> str:
    expected = name.casefold().encode("ascii")
    for key, value in scope.get("headers", []):
        if key.lower() == expected:
            return value.decode("latin-1")
    return ""


def _has_valid_bearer(scope: Scope, verifier: AuthVerifier) -> bool:
    scheme, separator, candidate = _header(scope, "authorization").partition(" ")
    if (
        not separator
        or scheme.casefold() != "bearer"
        or not candidate
        or len(candidate) > _MAX_BEARER_TOKEN_LENGTH
    ):
        return False

    verifier_kind, expected = verifier
    if verifier_kind == "plain":
        return hmac.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))

    candidate_digest = hashlib.sha256(candidate.encode("utf-8")).hexdigest()
    return hmac.compare_digest(candidate_digest, expected)


def _request_credential(scope: Scope, header_name: str) -> str | None:
    value = _header(scope, header_name).strip()
    if not value:
        return None
    if len(value) > _MAX_MOODLE_CREDENTIAL_LENGTH:
        raise ValueError(f"{header_name} supera el límite permitido")
    return value


def _without_sensitive_request_headers(scope: Scope) -> Scope:
    filtered_headers = [
        (key, value)
        for key, value in scope.get("headers", [])
        if key.lower() not in _SENSITIVE_REQUEST_HEADERS
    ]
    forwarded_scope = dict(scope)
    forwarded_scope["headers"] = filtered_headers
    return forwarded_scope


async def _send_json(
    scope: Scope,
    receive: Receive,
    send: Send,
    payload: dict[str, object],
    *,
    status_code: int,
    headers: dict[str, str] | None = None,
) -> None:
    response_headers = {"Cache-Control": "no-store"}
    if headers:
        response_headers.update(headers)
    response = JSONResponse(payload, status_code=status_code, headers=response_headers)
    await response(scope, receive, send)


def _disable_non_read_only_tools() -> None:
    """Fail closed for tools that are not explicitly annotated as pure reads.

    The local confirmation stores are intentionally process-bound. A serverless
    invocation may run on another instance, so preview/execute workflows cannot
    preserve the project's one-use confirmation guarantee without durable state.
    """

    for tool in mcp._tool_manager.list_tools():  # noqa: SLF001
        annotations = tool.annotations
        if annotations is not None and getattr(annotations, "readOnlyHint", None) is True:
            continue

        tool_name = tool.name

        async def blocked_remote_tool(
            *args: object,
            _tool_name: str = tool_name,
            **kwargs: object,
        ) -> None:
            del args, kwargs
            raise RuntimeError(
                f"{_tool_name} no está disponible en el despliegue serverless de solo lectura. "
                "Ejecuta esta operación con el servidor local para conservar confirmaciones "
                "de un solo uso y estado de sesión fiable."
            )

        tool.fn = blocked_remote_tool
        tool.is_async = True


def _build_mcp_app() -> ASGIApp:
    # Vercel creates and reuses function instances independently. Stateless HTTP
    # avoids binding an MCP session to any one instance, while JSON responses
    # avoid long-lived SSE streams for ordinary request/response calls.
    mcp.settings.stateless_http = True
    mcp.settings.json_response = True
    mcp.settings.streamable_http_path = _MCP_PATH

    # FastMCP enables localhost-only Host validation because the original stdio
    # server uses its default 127.0.0.1 host. Vercel already terminates and routes
    # HTTPS; the private bearer token below is the remote access boundary.
    mcp.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=False
    )

    _disable_non_read_only_tools()
    return mcp.streamable_http_app()


class ProtectedMCPApp:
    """ASGI boundary for health checks, bearer auth and request-only USC secrets."""

    def __init__(self, mcp_app: ASGIApp) -> None:
        self._mcp_app = mcp_app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self._mcp_app(scope, receive, send)
            return

        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return

        path = str(scope.get("path") or "")
        if path in {"/", "/healthz"}:
            await _send_json(
                scope,
                receive,
                send,
                {
                    "name": "mcp-usc",
                    "status": "ok",
                    "mcp_ready": _configured_auth_verifier() is not None,
                    "transport": "streamable-http",
                    "endpoint": _MCP_PATH,
                    "mode": "remote-read-only",
                    "moodle_credentials": "request-headers-or-environment",
                },
                status_code=200,
            )
            return

        if path not in {_MCP_PATH, f"{_MCP_PATH}/"}:
            await _send_json(
                scope,
                receive,
                send,
                {"error": "not_found"},
                status_code=404,
            )
            return

        verifier = _configured_auth_verifier()
        if verifier is None:
            await _send_json(
                scope,
                receive,
                send,
                {
                    "error": "mcp_not_configured",
                    "message": (
                        "Configura MCP_AUTH_TOKEN o MCP_AUTH_TOKEN_SHA256 "
                        "con un verificador válido."
                    ),
                },
                status_code=503,
            )
            return

        if not _has_valid_bearer(scope, verifier):
            await _send_json(
                scope,
                receive,
                send,
                {"error": "unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            return

        try:
            moodle_token = _request_credential(scope, _MOODLE_TOKEN_HEADER)
            moodle_session = _request_credential(scope, _MOODLE_SESSION_HEADER)
        except ValueError as exc:
            await _send_json(
                scope,
                receive,
                send,
                {"error": "invalid_credential_header", "message": str(exc)},
                status_code=400,
            )
            return

        forwarded_scope = _without_sensitive_request_headers(scope)
        if path.endswith("/"):
            forwarded_scope["path"] = _MCP_PATH
            forwarded_scope["raw_path"] = _MCP_PATH.encode("ascii")

        with bind_request_credentials(
            moodle_token=moodle_token,
            moodle_session=moodle_session,
        ):
            await self._mcp_app(forwarded_scope, receive, send)


app = ProtectedMCPApp(_build_mcp_app())
