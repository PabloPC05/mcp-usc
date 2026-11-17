from __future__ import annotations

import contextlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import keyring
from keyring.errors import KeyringError, NoKeyringError
from platformdirs import user_config_path

from .request_credentials import current_moodle_session

SERVICE_NAME = "mcp-usc"
_ENVIRONMENT_CREDENTIALS = {
    "moodle-session": "USC_MOODLE_SESSION",
}


class CredentialStoreError(RuntimeError):
    pass


def fallback_path() -> Path:
    """Where credentials go when the system has no keyring (a server, a VM over SSH)."""

    override = os.getenv("USC_CREDENTIALS_FILE", "").strip()
    if override:
        return Path(override).expanduser()
    return Path(user_config_path("mcp-usc", appauthor=False)) / "credenciales.json"


def _read_fallback() -> dict[str, str]:
    try:
        data = json.loads(fallback_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(key): str(value) for key, value in data.items()} if isinstance(data, dict) else {}


def _write_fallback(values: dict[str, str]) -> None:
    path = fallback_path()
    if not values:
        with contextlib.suppress(FileNotFoundError):
            path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        path.parent.chmod(0o700)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".credenciales-")
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(handle, 0o600)  # only you can read your Campus session
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(values, stream)
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise


@dataclass(slots=True)
class CredentialStore:
    """Small adapter over request, environment and OS credential stores.

    Without a usable OS keyring (headless Linux, a VM reached by SSH) values live in a
    private file (0600, folder 0700) under the user's config directory instead.
    """

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
            value = keyring.get_password(self.service_name, name)
        except NoKeyringError:
            return _read_fallback().get(name)
        except KeyringError as exc:
            raise CredentialStoreError(
                "No se pudo leer el almacén seguro de credenciales del sistema."
            ) from exc
        return value if value is not None else _read_fallback().get(name)

    def set(self, name: str, value: str) -> None:
        if not value:
            raise ValueError("No se puede guardar una credencial vacía")
        try:
            keyring.set_password(self.service_name, name, value)
        except NoKeyringError:
            values = _read_fallback()
            values[name] = value
            try:
                _write_fallback(values)
            except OSError as exc:
                raise CredentialStoreError(
                    "No hay llavero del sistema y no se pudo guardar el archivo privado."
                ) from exc
        except KeyringError as exc:
            raise CredentialStoreError(
                "No se pudo escribir en el almacén seguro de credenciales del sistema."
            ) from exc

    def delete(self, name: str) -> None:
        values = _read_fallback()
        if name in values:
            values.pop(name)
            _write_fallback(values)
        try:
            keyring.delete_password(self.service_name, name)
        except (keyring.errors.PasswordDeleteError, NoKeyringError):
            return
        except KeyringError as exc:
            raise CredentialStoreError(
                "No se pudo borrar la credencial del almacén seguro del sistema."
            ) from exc
