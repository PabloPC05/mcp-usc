import pytest
from keyring.errors import KeyringError, PasswordDeleteError

from mcp_usc import credentials
from mcp_usc.credentials import CredentialStore, CredentialStoreError


def test_credential_store_delegates_to_keyring_with_service_name(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_get(service: str, name: str) -> str:
        calls.append(("get", service, name))
        return "session-value"

    def fake_set(service: str, name: str, value: str) -> None:
        calls.append(("set", service, name, value))

    def fake_delete(service: str, name: str) -> None:
        calls.append(("delete", service, name))

    monkeypatch.setattr(credentials.keyring, "get_password", fake_get)
    monkeypatch.setattr(credentials.keyring, "set_password", fake_set)
    monkeypatch.setattr(credentials.keyring, "delete_password", fake_delete)

    store = CredentialStore(service_name="mcp-usc-test")
    assert store.get("MoodleSession") == "session-value"
    store.set("MoodleSession", "session-value")
    store.delete("MoodleSession")

    assert calls == [
        ("get", "mcp-usc-test", "MoodleSession"),
        ("set", "mcp-usc-test", "MoodleSession", "session-value"),
        ("delete", "mcp-usc-test", "MoodleSession"),
    ]


def test_credential_store_rejects_empty_values(monkeypatch) -> None:
    def unexpected_set(*_args) -> None:
        raise AssertionError("keyring must not receive an empty credential")

    monkeypatch.setattr(credentials.keyring, "set_password", unexpected_set)

    with pytest.raises(ValueError, match="vacía"):
        CredentialStore().set("MoodleSession", "")


@pytest.mark.parametrize("operation", ["get", "set", "delete"])
def test_credential_store_wraps_backend_failures(monkeypatch, operation: str) -> None:
    def fail(*_args):
        raise KeyringError("backend detail that should not escape")

    monkeypatch.setattr(credentials.keyring, f"{operation}_password", fail)
    store = CredentialStore()

    with pytest.raises(CredentialStoreError) as caught:
        if operation == "get":
            store.get("MoodleSession")
        elif operation == "set":
            store.set("MoodleSession", "secret")
        else:
            store.delete("MoodleSession")

    assert "backend detail" not in str(caught.value)


def test_delete_is_idempotent_when_credential_does_not_exist(monkeypatch) -> None:
    def missing(*_args) -> None:
        raise PasswordDeleteError("missing")

    monkeypatch.setattr(credentials.keyring, "delete_password", missing)

    CredentialStore().delete("MoodleSession")
