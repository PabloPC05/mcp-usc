from __future__ import annotations

import keyring

from mcp_usc.credentials import CredentialStore
from mcp_usc.request_credentials import (
    bind_request_credentials,
    current_moodle_session,
    current_moodle_token,
)
from mcp_usc.settings import Settings


def test_request_credentials_are_scoped_and_precede_persistent_sources(monkeypatch) -> None:
    monkeypatch.setenv("USC_MOODLE_TOKEN", "environment-token")
    monkeypatch.setenv("USC_MOODLE_SESSION", "environment-session")

    def fail_if_called(service_name: str, name: str) -> str | None:
        raise AssertionError(f"keyring should not be read for {service_name}/{name}")

    monkeypatch.setattr(keyring, "get_password", fail_if_called)

    assert current_moodle_token() is None
    assert current_moodle_session() is None

    with bind_request_credentials(
        moodle_token="request-token",
        moodle_session="request-session",
    ):
        assert Settings.from_env().moodle_token == "request-token"
        assert CredentialStore().get("moodle-session") == "request-session"

    assert current_moodle_token() is None
    assert current_moodle_session() is None
    assert Settings.from_env().moodle_token == "environment-token"
    assert CredentialStore().get("moodle-session") == "environment-session"


def test_nested_request_credentials_restore_outer_values() -> None:
    with bind_request_credentials(moodle_token="outer", moodle_session="outer-session"):
        with bind_request_credentials(moodle_token="inner", moodle_session=None):
            assert current_moodle_token() == "inner"
            assert current_moodle_session() is None

        assert current_moodle_token() == "outer"
        assert current_moodle_session() == "outer-session"
