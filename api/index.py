from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import parse_qs

from starlette.types import Receive, Scope, Send

_SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from mcp_usc.vercel_app import app as _mcp_app  # noqa: E402

_ROUTE_QUERY_KEY = "__mcp_usc_route"
_ROUTE_PATHS = {
    "root": "/",
    "healthz": "/healthz",
    "mcp": "/mcp",
}


def _external_path(scope: Scope) -> str | None:
    raw_query = scope.get("query_string", b"")
    query = parse_qs(raw_query.decode("utf-8", errors="ignore"))
    route_name = query.get(_ROUTE_QUERY_KEY, [""])[0]
    return _ROUTE_PATHS.get(route_name)


async def app(scope: Scope, receive: Receive, send: Send) -> None:
    forwarded_scope = scope
    if scope["type"] == "http" and (external_path := _external_path(scope)) is not None:
        forwarded_scope = dict(scope)
        forwarded_scope["path"] = external_path
        forwarded_scope["raw_path"] = external_path.encode("ascii")
        forwarded_scope["query_string"] = b""
    await _mcp_app(forwarded_scope, receive, send)


__all__ = ["app"]
