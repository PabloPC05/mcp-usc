"""Safe local import and removal of a Moodle browser session.

The cookie is accepted only through an in-process secret prompt, validated against the
configured USC Moodle origin, and persisted in the operating-system credential store.
Neither the cookie nor Moodle's sesskey are returned to callers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from .credentials import CredentialStore
from .security import html_to_text, validate_usc_url
from .settings import Settings

SESSION_CREDENTIAL_NAME = "moodle-session"
_COOKIE_VALUE = re.compile(r"[A-Za-z0-9,_-]{16,512}\Z")
_SESSKEY_PATTERNS = (
    re.compile(r'["\']sesskey["\']\s*:\s*["\']([A-Za-z0-9_-]{5,128})["\']'),
    re.compile(r"sesskey=([A-Za-z0-9_-]{5,128})"),
)
_USER_ID_PATTERNS = (
    re.compile(r'["\']userid["\']\s*:\s*["\']?(\d+)["\']?'),
    re.compile(r'["\']userId["\']\s*:\s*["\']?(\d+)["\']?'),
)
_MAX_VALIDATION_HTML_BYTES = 5 * 1024 * 1024


class SessionImportError(RuntimeError):
    """The supplied browser session could not be safely validated or stored."""


@dataclass(frozen=True, slots=True)
class ImportedSession:
    user_id: int
    user_name: str
    site_name: str

    def as_dict(self) -> dict[str, object]:
        return {
            "authenticated": True,
            "method": "moodle_http_session",
            "user_id": self.user_id,
            "user_name": self.user_name,
            "site_name": self.site_name,
            "cookie_stored_in_os_keyring": True,
        }


def _validated_cookie(raw_cookie: str) -> str:
    if not isinstance(raw_cookie, str):
        raise SessionImportError("MoodleSession debe ser texto.")
    cookie = raw_cookie.strip()
    if not _COOKIE_VALUE.fullmatch(cookie):
        raise SessionImportError(
            "MoodleSession no tiene un formato válido. Copia únicamente el valor de la cookie, "
            "sin el nombre, comillas, espacios ni punto y coma."
        )
    return cookie


def _looks_like_login(soup: BeautifulSoup, url: str) -> bool:
    path = urlparse(url).path.casefold()
    return bool(
        "/login/" in path
        or soup.select_one("form#login, form[action*='/login/index.php']")
        or (soup.select_one("input[name='username']") and soup.select_one("input[name='password']"))
    )


def _sesskey(soup: BeautifulSoup, body: str) -> str:
    field = soup.select_one("input[name='sesskey'][value]")
    if field:
        value = str(field.get("value") or "")
        if re.fullmatch(r"[A-Za-z0-9_-]{5,128}", value):
            return value
    for pattern in _SESSKEY_PATTERNS:
        match = pattern.search(body)
        if match:
            return match.group(1)
    return ""


def _user_id(soup: BeautifulSoup, body: str) -> int:
    for pattern in _USER_ID_PATTERNS:
        match = pattern.search(body)
        if match and int(match.group(1)) > 0:
            return int(match.group(1))
    for element in soup.select("[data-userid], [data-initial-user-id]"):
        raw_value = element.get("data-userid") or element.get("data-initial-user-id")
        try:
            value = int(str(raw_value))
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return 0


def _first_text(soup: BeautifulSoup, *selectors: str) -> str:
    for selector in selectors:
        element = soup.select_one(selector)
        if element:
            value = html_to_text(element.get_text(" ", strip=True), limit=500)
            if value:
                return value
    return ""


async def import_session_cookie(
    settings: Settings,
    raw_cookie: str,
    *,
    credential_store: CredentialStore | None = None,
) -> dict[str, object]:
    """Validate one MoodleSession over HTTP and store it without returning secrets."""

    cookie = _validated_cookie(raw_cookie)
    base_url = validate_usc_url(settings.moodle_url, campus=True).rstrip("/")
    validation_url = validate_usc_url(
        urljoin(f"{base_url}/", "user/preferences.php"), campus=True
    )
    try:
        async with httpx.AsyncClient(
            timeout=settings.request_timeout_seconds,
            follow_redirects=False,
            cookies={"MoodleSession": cookie},
            headers={"Accept": "text/html", "User-Agent": "mcp-usc/0.4"},
        ) as client:
            response = await client.get(validation_url)
    except httpx.HTTPError:
        raise SessionImportError(
            "No se pudo validar la sesión con el Campus Virtual. No se guardó la cookie."
        ) from None

    location = response.headers.get("location", "")
    if response.is_redirect:
        destination = urlparse(location).hostname or ""
        if "/login/" in urlparse(location).path.casefold() or destination.casefold().endswith(
            "microsoftonline.com"
        ):
            raise SessionImportError(
                "La cookie no corresponde a una sesión autenticada o ya ha caducado."
            )
        raise SessionImportError(
            "Moodle devolvió una redirección inesperada. No se guardó la cookie."
        )
    if response.status_code in {401, 403}:
        raise SessionImportError("La cookie no está autorizada o ya ha caducado.")
    if response.status_code >= 400:
        raise SessionImportError(
            f"El Campus Virtual devolvió HTTP {response.status_code}. No se guardó la cookie."
        )
    if len(response.content) > _MAX_VALIDATION_HTML_BYTES:
        raise SessionImportError("La respuesta de validación de Moodle es demasiado grande.")

    soup = BeautifulSoup(response.text, "html.parser")
    if _looks_like_login(soup, str(response.url)):
        raise SessionImportError("La cookie no corresponde a una sesión autenticada.")
    if not _sesskey(soup, response.text):
        raise SessionImportError("Moodle no expuso un sesskey válido; no se guardó la cookie.")
    user_id = _user_id(soup, response.text)
    if user_id <= 0:
        raise SessionImportError("Moodle no expuso una identidad válida; no se guardó la cookie.")

    try:
        rotated_cookie = response.cookies.get("MoodleSession")
    except httpx.CookieConflict:
        rotated_cookie = None
    cookie_to_store = _validated_cookie(rotated_cookie) if rotated_cookie else cookie
    store = credential_store or CredentialStore()
    try:
        store.set(SESSION_CREDENTIAL_NAME, cookie_to_store)
    except Exception:
        raise SessionImportError(
            "La sesión es válida, pero no pudo guardarse en el almacén seguro del sistema."
        ) from None
    imported = ImportedSession(
        user_id=user_id,
        user_name=_first_text(
            soup,
            ".usermenu .usertext",
            "[data-region='user-menu'] .usertext",
            ".logininfo a",
        ),
        site_name=_first_text(soup, ".navbar-brand", ".site-name", "title"),
    )
    return imported.as_dict()


def forget_session_cookie(*, credential_store: CredentialStore | None = None) -> dict[str, object]:
    """Delete only the local credential; do not make a remote logout request."""

    store = credential_store or CredentialStore()
    try:
        store.delete(SESSION_CREDENTIAL_NAME)
    except Exception:
        raise SessionImportError(
            "No se pudo eliminar MoodleSession del almacén seguro del sistema."
        ) from None
    return {
        "authenticated": False,
        "local_session_removed": True,
        "remote_session_unchanged": True,
    }
