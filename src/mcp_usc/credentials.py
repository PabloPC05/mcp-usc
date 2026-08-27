from __future__ import annotations

import os
from dataclasses import dataclass

import keyring
from keyring.errors import KeyringError

from .request_credentials import current_moodle_session

SERVICE_NAME = "mcp-usc"
_ENVIRONMENT_CREDENTIALS = {
    "moodle-session": "USC_MOODLE_SESSION",
}


class CredentialStoreError(RuntimeError):
    pass


@dataclass(slots=True)
class CredentialStore:
    """Small adapter over request, environment and OS credential stores."""

    service_name: str = SERVICE_NAME

    def get(self, name: str) -> str | None:
        if name == "moodle-session" and (request_value := current_moodle_session()):
            return request_value
        environment_name = _ENVIRONMENT_CREDENTIALS.get(name)
        if environment_name:
            environment_value = os.getenv(environment_name, "").strip()
            if environment_value:
                return environment_value
        try:
            return keyring.get_password(self.service_name, name)
        except KeyringError as exc:
            raise CredentialStoreError(
                "No se pudo leer el almacén seguro de credenciales del sistema."
            ) from exc

    def set(self, name: str, value: str) -> None:
        if not value:
            raise ValueError("No se puede guardar una credencial vacía")
        try:
            keyring.set_password(self.service_name, name, value)
        except KeyringError as exc:
            raise CredentialStoreError(
                "No se pudo escribir en el almacén seguro de credenciales del sistema."
            ) from exc

    def delete(self, name: str) -> None:
        try:
            keyring.delete_password(self.service_name, name)
        except keyring.errors.PasswordDeleteError:
            return
        except KeyringError as exc:
            raise CredentialStoreError(
                "No se pudo borrar la credencial del almacén seguro del sistema."
            ) from exc
