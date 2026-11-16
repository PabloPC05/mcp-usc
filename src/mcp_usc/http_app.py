"""Streamable HTTP entrypoint that runs anywhere.

The same ASGI app serves a local machine, a home server behind a reverse proxy or an
MCP gateway, a container on any host, or a serverless platform such as Vercel:

- ``mcp-usc serve --transport http`` runs it with uvicorn;
- ``uvicorn --factory mcp_usc.http_app:create_app`` runs it under any ASGI server;
- ``api/index.py`` adapts it to Vercel's Python runtime.

Every request to ``/mcp`` needs ``Authorization: Bearer <token>``. The deployment is
read-only unless ``MCP_USC_ALLOW_WRITES`` is enabled on a persistent (non-serverless)
process: write confirmations are one-use tokens kept in process memory, which a
serverless platform cannot guarantee across instances.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from . import __version__
from .request_credentials import bind_request_credentials
from .resource_references import STATE_SECRET_ENV, shared_state_secret_configured
from .server import INSTRUCTIONS, READ_ONLY
from .server import mcp as local_mcp

ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]
AuthVerifier = tuple[str, str]

MCP_PATH = "/mcp"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
_MIN_AUTH_TOKEN_LENGTH = 32
_MAX_BEARER_TOKEN_LENGTH = 1_024
_MAX_MOODLE_CREDENTIAL_LENGTH = 4_096
_MOODLE_TOKEN_HEADER = "x-usc-moodle-token"
_MOODLE_SESSION_HEADER = "x-usc-moodle-session"
_SENSITIVE_REQUEST_HEADERS = {
    _MOODLE_TOKEN_HEADER.encode("ascii"),
    _MOODLE_SESSION_HEADER.encode("ascii"),
}
_READ_ONLY_NOTE = (
    " Este despliegue HTTP es de solo lectura: no publica herramientas preview ni de escritura;"
    " para mensajes, entregas o cuestionarios usa el servidor local."
)

logger = logging.getLogger(__name__)


class HttpConfigError(ValueError):
    """The HTTP deployment is misconfigured; it refuses to start instead of guessing."""


@dataclass(frozen=True, slots=True)
class HttpOptions:
    verifier: AuthVerifier | None
    allow_writes: bool
    serverless: bool


def _env_flag(name: str) -> bool:
    value = os.getenv(name, "").strip().casefold()
    if value in {"", "0", "false", "no", "off"}:
        return False
    if value in {"1", "true", "yes", "on"}:
        return True
    raise HttpConfigError(f"{name} debe ser true o false")


def running_serverless() -> bool:
    """True on platforms that may serve consecutive requests from different processes."""

    return bool(os.getenv("VERCEL")) or _env_flag("MCP_USC_SERVERLESS")


def load_auth_verifier() -> AuthVerifier | None:
    """Read the bearer verifier: MCP_AUTH_TOKEN, then MCP_AUTH_TOKEN_FILE, then the SHA-256."""

    token = os.getenv("MCP_AUTH_TOKEN", "").strip()
    token_file = os.getenv("MCP_AUTH_TOKEN_FILE", "").strip()
    if not token and token_file:
        try:
            token = Path(token_file).expanduser().read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise HttpConfigError("No se pudo leer MCP_AUTH_TOKEN_FILE") from exc
    if token:
        if len(token) < _MIN_AUTH_TOKEN_LENGTH:
            raise HttpConfigError(
                f"El token MCP debe tener al menos {_MIN_AUTH_TOKEN_LENGTH} caracteres"
            )
        return ("plain", token)

    digest = os.getenv("MCP_AUTH_TOKEN_SHA256", "").strip().casefold()
    if digest:
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise HttpConfigError("MCP_AUTH_TOKEN_SHA256 debe ser un SHA-256 hexadecimal")
        return ("sha256", digest)
    return None


def load_http_options() -> HttpOptions:
    serverless = running_serverless()
    allow_writes = _env_flag("MCP_USC_ALLOW_WRITES")
    if allow_writes and serverless:
        raise HttpConfigError(
            "MCP_USC_ALLOW_WRITES no se admite en plataformas serverless: las confirmaciones "
            "de un solo uso viven en la memoria de un único proceso."
        )
    return HttpOptions(
        verifier=load_auth_verifier(),
        allow_writes=allow_writes,
        serverless=serverless,
    )


def _header(scope: Scope, name: str) -> str:
    expected = name.casefold().encode("ascii")
    for key, value in scope.get("headers", []):
        if key.lower() == expected:
            return value.decode("latin-1")
    return ""


def _has_valid_bearer(scope: Scope, verifier: AuthVerifier) -> bool:
    scheme, separator, candidate = _header(scope, "authorization").partition(" ")
    candidate = candidate.strip()
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
    forwarded_scope = dict(scope)
    forwarded_scope["headers"] = [
        (key, value)
        for key, value in scope.get("headers", [])
        if key.lower() not in _SENSITIVE_REQUEST_HEADERS
    ]
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


def build_http_server(*, allow_writes: bool) -> FastMCP:
    """Build a dedicated FastMCP for HTTP that publishes only the allowed tools.

    Read-only deployments drop previews and writes from ``tools/list`` instead of
    advertising tools that could never complete, which also keeps the model's context
    smaller. The local stdio server object is left untouched.
    """

    server = FastMCP(
        local_mcp.name,
        instructions=INSTRUCTIONS if allow_writes else INSTRUCTIONS + _READ_ONLY_NOTE,
        json_response=True,
        # Stateless requests survive restarts and any number of instances.
        stateless_http=True,
        streamable_http_path=MCP_PATH,
        # The bearer token below is the access boundary; hosts and proxies vary.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    for tool in local_mcp._tool_manager.list_tools():  # noqa: SLF001
        if allow_writes or tool.annotations == READ_ONLY:
            server._tool_manager._tools[tool.name] = tool  # noqa: SLF001
    server._resource_manager = local_mcp._resource_manager  # noqa: SLF001
    server._prompt_manager = local_mcp._prompt_manager  # noqa: SLF001
    return server


class ProtectedMCPApp:
    """ASGI boundary for health checks, bearer auth and request-only USC secrets."""

    def __init__(self, mcp_app: ASGIApp, options: HttpOptions) -> None:
        self._mcp_app = mcp_app
        self._options = options

    def _health(self) -> dict[str, object]:
        return {
            "name": "mcp-usc",
            "version": __version__,
            "status": "ok",
            "mcp_ready": self._options.verifier is not None,
            "transport": "streamable-http",
            "endpoint": MCP_PATH,
            "mode": "read-write" if self._options.allow_writes else "read-only",
        }

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
            await _send_json(scope, receive, send, self._health(), status_code=200)
            return

        if path not in {MCP_PATH, f"{MCP_PATH}/"}:
            await _send_json(scope, receive, send, {"error": "not_found"}, status_code=404)
            return

        verifier = self._options.verifier
        if verifier is None:
            await _send_json(
                scope,
                receive,
                send,
                {
                    "error": "mcp_not_configured",
                    "message": (
                        "Configura MCP_AUTH_TOKEN, MCP_AUTH_TOKEN_FILE o MCP_AUTH_TOKEN_SHA256."
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
            forwarded_scope["path"] = MCP_PATH
            forwarded_scope["raw_path"] = MCP_PATH.encode("ascii")

        with bind_request_credentials(
            moodle_token=moodle_token,
            moodle_session=moodle_session,
        ):
            await self._mcp_app(forwarded_scope, receive, send)


def create_app(options: HttpOptions | None = None) -> ProtectedMCPApp:
    """Build the protected ASGI app from the environment (or explicit options)."""

    resolved = options or load_http_options()
    if resolved.verifier is None:
        logger.warning("mcp-usc HTTP: no hay token configurado; /mcp responderá 503.")
    if resolved.serverless and not shared_state_secret_configured():
        logger.warning(
            "mcp-usc HTTP: sin %s, read_course_resource solo acepta referencias emitidas "
            "por la misma instancia serverless.",
            STATE_SECRET_ENV,
        )
    server = build_http_server(allow_writes=resolved.allow_writes)
    return ProtectedMCPApp(server.streamable_http_app(), resolved)


def run_http(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    """Serve the HTTP app with uvicorn in a single process."""

    import uvicorn

    from .server import _configure_safe_http_logging

    app = create_app()
    _configure_safe_http_logging()
    # One worker only: write confirmations live in this process's memory.
    uvicorn.run(app, host=host, port=port, server_header=False, log_level="info")


__all__ = [
    "MCP_PATH",
    "HttpConfigError",
    "HttpOptions",
    "ProtectedMCPApp",
    "build_http_server",
    "create_app",
    "load_http_options",
    "run_http",
]
