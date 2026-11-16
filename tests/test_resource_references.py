from __future__ import annotations

import pytest

from mcp_usc import resource_references
from mcp_usc.resource_references import (
    RESOURCE_REFERENCE_TTL_SECONDS,
    issue_resource_reference,
    resolve_resource_reference,
)

_REFERENCE = {
    "url": "https://cv.usc.es/webservice/pluginfile.php/1/mod_resource/content/1/tema.pdf",
    "filename": "tema.pdf",
    "media_type": "application/pdf",
    "declared_size": 1234,
}


def test_reference_round_trips_without_process_state() -> None:
    token = issue_resource_reference(_REFERENCE, now=1_000)

    assert resolve_resource_reference(token, now=1_001) == _REFERENCE


def test_tampered_payload_or_signature_is_rejected() -> None:
    token = issue_resource_reference(_REFERENCE, now=1_000)
    body, signature = token.split(".")
    forged_body = issue_resource_reference(
        {**_REFERENCE, "url": "https://cv.usc.es/pluginfile.php/9/other.pdf"}, now=1_000
    ).split(".")[0]

    for candidate in (f"{forged_body}.{signature}", f"{body}.{signature[:-2]}AA", "inventado", ""):
        with pytest.raises(ValueError, match="no procede de una lista reciente"):
            resolve_resource_reference(candidate, now=1_001)


def test_reference_expires() -> None:
    token = issue_resource_reference(_REFERENCE, now=1_000)

    with pytest.raises(ValueError, match="caducado"):
        resolve_resource_reference(token, now=1_001 + RESOURCE_REFERENCE_TTL_SECONDS)


def test_shared_secret_lets_other_processes_verify(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_USC_STATE_SECRET", "s" * 40)
    token = issue_resource_reference(_REFERENCE, now=1_000)

    # A different process has a different random key but the same configured secret.
    monkeypatch.setattr(resource_references, "_PROCESS_KEY", b"\x01" * 32)
    assert resolve_resource_reference(token, now=1_001)["url"] == _REFERENCE["url"]

    monkeypatch.setenv("MCP_USC_STATE_SECRET", "t" * 40)
    with pytest.raises(ValueError):
        resolve_resource_reference(token, now=1_001)


def test_short_secret_falls_back_to_the_process_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_USC_STATE_SECRET", "short")
    token = issue_resource_reference(_REFERENCE, now=1_000)
    monkeypatch.setattr(resource_references, "_PROCESS_KEY", b"\x02" * 32)

    with pytest.raises(ValueError):
        resolve_resource_reference(token, now=1_001)
