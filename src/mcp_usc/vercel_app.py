from __future__ import annotations

import hmac
import os
from collections.abc import Awaitable, Callable

from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from .server import mcp

ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]
_MIN_AUTH_TOKEN_LENGTH = 32
_MCP_PATH = "/mcp"


def _configured_auth_token() -> str | None:
    token = os.getenv("MCP_AUTH_TOKEN", "").strip()
    if len(token) < _MIN_AUTH_TOKEN_LENGTH:
        return None
    return token


def _header(scope: Scope, name: str) -> str:
    expected = name.casefold().encode("ascii")
    for key, value in scope.get("headers", []):
        if key.lower() == expected:
            return value.decode("latin-1")
    return ""


def _has_valid_bearer(scope: Scope, expected_token: str) -> bool:
    scheme, separator, candidate = _header(scope, "authorization").partition(" ")
    return bool(
        separator
        and scheme.casefold() == "bearer"
        and candidate
        and hmac.compare_digest(candidate.encode("utf-8"), expected_token.encode("utf-8"))
    )


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
    """Small ASGI boundary for health checks and pre-shared bearer authentication."""

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
                    "mcp_ready": _configured_auth_token() is not None,
                    "transport": "streamable-http",
                    "endpoint": _MCP_PATH,
                    "mode": "remote-read-only",
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

        expected_token = _configured_auth_token()
        if expected_token is None:
            await _send_json(
                scope,
                receive,
                send,
                {
                    "error": "mcp_not_configured",
                    "message": "Configura MCP_AUTH_TOKEN con al menos 32 caracteres.",
                },
                status_code=503,
            )
            return

        if not _has_valid_bearer(scope, expected_token):
            await _send_json(
                scope,
                receive,
                send,
                {"error": "unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            return

        forwarded_scope = scope
        if path.endswith("/"):
            forwarded_scope = dict(scope)
            forwarded_scope["path"] = _MCP_PATH
            forwarded_scope["raw_path"] = _MCP_PATH.encode("ascii")
        await self._mcp_app(forwarded_scope, receive, send)


app = ProtectedMCPApp(_build_mcp_app())
