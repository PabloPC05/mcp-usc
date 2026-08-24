from __future__ import annotations

import re

from mcp_usc.manifest import build_manifest


async def test_manifest_is_deterministic_complete_and_secret_free() -> None:
    first = await build_manifest()
    second = await build_manifest()

    assert first == second
    assert first["version"] == "0.11.0"
    assert first["counts"] == {"tools": 91, "resources": 4, "prompts": 4}
    assert re.fullmatch(r"[0-9a-f]{64}", first["contract_sha256"])
    assert first["network_contacted"] is False
    assert first["secrets_exposed"] is False
    assert {item["uri"] for item in first["resources"]} == {
        "usc://about",
        "usc://compatibility",
        "usc://safety",
        "usc://workflows",
    }
    assert "MoodleSession" not in repr(first)
