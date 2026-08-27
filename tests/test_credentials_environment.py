from __future__ import annotations

import keyring

from mcp_usc.credentials import CredentialStore


def test_moodle_session_environment_secret_precedes_keyring(monkeypatch) -> None:
    monkeypatch.setenv("USC_MOODLE_SESSION", "A" * 32)

    def fail_if_called(service_name: str, name: str) -> str | None:
        raise AssertionError(f"keyring should not be read for {service_name}/{name}")

    monkeypatch.setattr(keyring, "get_password", fail_if_called)

    assert CredentialStore().get("moodle-session") == "A" * 32


def test_empty_moodle_session_environment_falls_back_to_keyring(monkeypatch) -> None:
    monkeypatch.setenv("USC_MOODLE_SESSION", "   ")
    monkeypatch.setattr(keyring, "get_password", lambda service_name, name: "stored")

    assert CredentialStore().get("moodle-session") == "stored"
