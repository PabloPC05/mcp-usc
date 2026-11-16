"""The host-agnostic HTTP app: auth boundary, tool exposure and Vercel adapter."""

from __future__ import annotations

import hashlib
import importlib
import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from mcp_usc import http_app
from mcp_usc.http_app import (
    HttpConfigError,
    HttpOptions,
    create_app,
    load_auth_verifier,
    load_http_options,
)
from mcp_usc.request_credentials import current_moodle_session, current_moodle_token
from mcp_usc.server import mcp as local_mcp

TOKEN = "t" * 40
_JSON_RPC_HEADERS = {
    "accept": "application/json, text/event-stream",
    "content-type": "application/json",
}
_HTTP_ENV = (
    "MCP_AUTH_TOKEN",
    "MCP_AUTH_TOKEN_FILE",
    "MCP_AUTH_TOKEN_SHA256",
    "MCP_USC_ALLOW_WRITES",
    "MCP_USC_SERVERLESS",
    "VERCEL",
)


@pytest.fixture(autouse=True)
def _clean_http_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _HTTP_ENV:
        monkeypatch.delenv(name, raising=False)


def _options(*, allow_writes: bool = False) -> HttpOptions:
    return HttpOptions(verifier=("plain", TOKEN), allow_writes=allow_writes, serverless=False)


def _rpc(client: TestClient, method: str, params: dict | None = None, **headers: str):
    return client.post(
        "/mcp",
        headers={**_JSON_RPC_HEADERS, "authorization": f"Bearer {TOKEN}", **headers},
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
    )


def _tool_names(client: TestClient) -> set[str]:
    response = _rpc(client, "tools/list")
    assert response.status_code == 200
    return {tool["name"] for tool in response.json()["result"]["tools"]}


def test_health_is_public_and_reveals_no_configuration_secret() -> None:
    with TestClient(create_app(_options())) as client:
        health = client.get("/healthz").json()

    assert health["status"] == "ok"
    assert health["mcp_ready"] is True
    assert health["mode"] == "read-only"
    assert TOKEN not in str(health)


@pytest.mark.parametrize(
    "authorization",
    ["", "Bearer", "Bearer wrong-token", f"Basic {TOKEN}", f"Bearer {'x' * 2000}"],
)
def test_mcp_requires_the_exact_bearer(authorization: str) -> None:
    with TestClient(create_app(_options())) as client:
        response = client.post(
            "/mcp",
            headers={**_JSON_RPC_HEADERS, "authorization": authorization},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_unconfigured_server_fails_closed() -> None:
    options = HttpOptions(verifier=None, allow_writes=False, serverless=False)
    with TestClient(create_app(options)) as client:
        assert client.get("/healthz").json()["mcp_ready"] is False
        assert _rpc(client, "tools/list").status_code == 503


def test_unknown_paths_are_not_found() -> None:
    with TestClient(create_app(_options())) as client:
        assert client.get("/mcp-admin").status_code == 404


def test_read_only_deployment_lists_only_pure_reads() -> None:
    with TestClient(create_app(_options())) as client:
        names = _tool_names(client)

    assert "list_pending_work" in names
    assert "read_course_resource" in names
    assert not any(name.startswith("preview_") for name in names)
    assert {"send_message", "submit_assignment", "start_quiz"}.isdisjoint(names)
    # The local stdio server keeps its complete surface.
    local_names = {tool.name for tool in local_mcp._tool_manager.list_tools()}  # noqa: SLF001
    assert {"send_message", "preview_message"} <= local_names


def test_read_only_deployment_rejects_a_hidden_write_call() -> None:
    with TestClient(create_app(_options())) as client:
        response = _rpc(
            client,
            "tools/call",
            {
                "name": "send_message",
                "arguments": {"recipient_user_id": 1, "text": "hola", "confirmation_token": "x"},
            },
        )

    body = response.json()
    assert body.get("error") or body["result"]["isError"] is True


def test_persistent_deployment_can_opt_into_confirmed_writes() -> None:
    with TestClient(create_app(_options(allow_writes=True))) as client:
        names = _tool_names(client)
        health = client.get("/healthz").json()

    assert {"preview_message", "send_message"} <= names
    assert health["mode"] == "read-write"


def test_moodle_credential_headers_are_request_scoped_and_stripped(monkeypatch) -> None:
    seen: dict[str, object] = {}

    async def inner(scope, receive, send) -> None:
        if scope["type"] == "lifespan":
            return
        seen["token"] = current_moodle_token()
        seen["session"] = current_moodle_session()
        seen["headers"] = {key.decode() for key, _ in scope["headers"]}
        from starlette.responses import JSONResponse

        await JSONResponse({})(scope, receive, send)

    app = http_app.ProtectedMCPApp(inner, _options())
    client = TestClient(app)
    response = client.post(
        "/mcp/",
        headers={
            "authorization": f"Bearer {TOKEN}",
            "x-usc-moodle-token": "moodle-token",
            "x-usc-moodle-session": "moodle-session",
        },
    )

    assert response.status_code == 200
    assert seen["token"] == "moodle-token"
    assert seen["session"] == "moodle-session"
    assert {"x-usc-moodle-token", "x-usc-moodle-session"}.isdisjoint(seen["headers"])
    assert current_moodle_token() is None


def test_oversized_moodle_credential_header_is_rejected() -> None:
    with TestClient(create_app(_options())) as client:
        response = _rpc(client, "tools/list", **{"x-usc-moodle-token": "a" * 5000})

    assert response.status_code == 400


def test_verifier_sources_and_priority(monkeypatch, tmp_path: Path) -> None:
    assert load_auth_verifier() is None

    digest = hashlib.sha256(TOKEN.encode()).hexdigest()
    monkeypatch.setenv("MCP_AUTH_TOKEN_SHA256", digest.upper())
    assert load_auth_verifier() == ("sha256", digest)

    token_file = tmp_path / "token"
    token_file.write_text(f"{TOKEN}\n", encoding="utf-8")
    monkeypatch.setenv("MCP_AUTH_TOKEN_FILE", str(token_file))
    assert load_auth_verifier() == ("plain", TOKEN)

    monkeypatch.setenv("MCP_AUTH_TOKEN", "p" * 32)
    assert load_auth_verifier() == ("plain", "p" * 32)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("MCP_AUTH_TOKEN", "too-short"),
        ("MCP_AUTH_TOKEN_SHA256", "not-a-digest"),
        ("MCP_AUTH_TOKEN_FILE", "/nonexistent/mcp-usc-token"),
        ("MCP_USC_ALLOW_WRITES", "perhaps"),
    ],
)
def test_misconfiguration_refuses_to_start(monkeypatch, name: str, value: str) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(HttpConfigError):
        load_http_options()


def test_serverless_platforms_cannot_enable_writes(monkeypatch) -> None:
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("MCP_USC_ALLOW_WRITES", "true")

    with pytest.raises(HttpConfigError, match="serverless"):
        load_http_options()


def test_hashed_verifier_accepts_the_original_token(monkeypatch) -> None:
    monkeypatch.setenv("MCP_AUTH_TOKEN_SHA256", hashlib.sha256(TOKEN.encode()).hexdigest())

    with TestClient(create_app()) as client:
        assert _tool_names(client)


def test_vercel_adapter_restores_rewritten_routes(monkeypatch) -> None:
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("MCP_AUTH_TOKEN", TOKEN)
    repository = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(repository))
    sys.modules.pop("api.index", None)
    adapter = importlib.import_module("api.index")

    with TestClient(adapter.app) as client:
        health = client.get("/api/index?__mcp_usc_route=healthz").json()
        listed = client.post(
            "/api/index?__mcp_usc_route=mcp",
            headers={**_JSON_RPC_HEADERS, "authorization": f"Bearer {TOKEN}"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        )

    assert health["mode"] == "read-only"
    names = {tool["name"] for tool in listed.json()["result"]["tools"]}
    assert "send_message" not in names
