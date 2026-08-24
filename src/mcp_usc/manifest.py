from __future__ import annotations

import hashlib
import json
from typing import Any

from . import __version__


def _model_dump(item: Any) -> dict[str, Any]:
    return item.model_dump(mode="json", by_alias=True, exclude_none=True)


async def build_manifest(server: Any | None = None) -> dict[str, object]:
    """Build a deterministic, secret-free manifest of the local MCP surface."""

    if server is None:
        from .server import mcp

        server = mcp

    tools = [_model_dump(item) for item in await server.list_tools()]
    resources = [_model_dump(item) for item in await server.list_resources()]
    prompts = [_model_dump(item) for item in await server.list_prompts()]
    contract = {
        "tools": tools,
        "resources": resources,
        "prompts": prompts,
    }
    canonical = json.dumps(
        contract,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "schema_version": 1,
        "name": "mcp-usc",
        "version": __version__,
        "transport": "stdio",
        "counts": {name: len(items) for name, items in contract.items()},
        "contract_sha256": hashlib.sha256(canonical).hexdigest(),
        **contract,
        "network_contacted": False,
        "secrets_exposed": False,
    }
