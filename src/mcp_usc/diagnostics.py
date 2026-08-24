from __future__ import annotations

import importlib.util
import platform
import sys
from pathlib import Path
from typing import Protocol

from . import __version__
from .credentials import CredentialStore, CredentialStoreError
from .session_auth import SESSION_CREDENTIAL_NAME
from .settings import Settings


class ReadableCredentialStore(Protocol):
    def get(self, name: str) -> str | None: ...


def _upload_status(path: Path | None) -> dict[str, object]:
    if path is None:
        return {"configured": False, "exists": False, "is_directory": False, "usable": False}
    exists = path.exists()
    is_directory = path.is_dir() if exists else False
    return {
        "configured": True,
        "exists": exists,
        "is_directory": is_directory,
        "usable": exists and is_directory,
    }


def build_diagnostic(
    settings: Settings | None = None,
    credential_store: ReadableCredentialStore | None = None,
    *,
    browser_auth_available: bool | None = None,
) -> dict[str, object]:
    """Inspect local readiness without contacting Moodle or revealing credentials."""

    try:
        resolved = settings or Settings.from_env()
    except (OSError, ValueError) as exc:
        return {
            "name": "mcp-usc",
            "version": __version__,
            "status": "configuration_error",
            "error": str(exc),
            "campus_contacted": False,
            "secrets_exposed": False,
        }

    store = credential_store or CredentialStore()
    session_stored: bool | None
    session_store_readable = True
    try:
        session_stored = bool(store.get(SESSION_CREDENTIAL_NAME))
    except CredentialStoreError:
        session_stored = None
        session_store_readable = False

    if browser_auth_available is None:
        browser_auth_available = importlib.util.find_spec("playwright") is not None

    token_configured = bool(resolved.moodle_token)
    private_access_configured = token_configured or session_stored is True
    python_supported = sys.version_info >= (3, 11)
    status = "ready" if private_access_configured else "public_only"
    if not python_supported:
        status = "unsupported_python"

    return {
        "name": "mcp-usc",
        "version": __version__,
        "status": status,
        "python": {
            "version": platform.python_version(),
            "supported": python_supported,
            "minimum": "3.11",
        },
        "authentication": {
            "token_configured": token_configured,
            "session_cookie_stored": session_stored,
            "credential_store_readable": session_store_readable,
            "private_access_configured": private_access_configured,
            "credentials_validated_online": False,
        },
        "features": {
            "public_usc_queries_available": True,
            "additional_exam_sources": len(resolved.exam_sources),
            "assignment_uploads": _upload_status(resolved.upload_root),
            "interactive_browser_login_available": browser_auth_available,
        },
        "next_steps": (
            ["Ejecuta `mcp-usc status` para validar la autenticación por HTTP."]
            if private_access_configured
            else [
                "Importa una MoodleSession con `mcp-usc import-session` o configura "
                "un token REST legítimo.",
                "Las consultas públicas de grados y exámenes ya están disponibles.",
            ]
        ),
        "campus_contacted": False,
        "secrets_exposed": False,
    }
