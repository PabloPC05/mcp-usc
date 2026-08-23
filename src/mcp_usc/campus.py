from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import re
import time
import uuid
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup, Tag

from .credentials import CredentialStore
from .security import html_to_text, validate_usc_url
from .session_forms import FormResponse, MoodleSessionForms
from .settings import Settings

SESSION_CREDENTIAL_NAME = "moodle-session"
_USER_AGENT = "mcp-usc/0.1 (+https://github.com/PabloPC05/mcp-usc)"
_ANNOUNCEMENT_TERMS = (
    "announcement",
    "anuncio",
    "aviso",
    "news",
    "noticia",
    "nova",
    "novedad",
)
_SESSKEY_PATTERNS = (
    re.compile(r'["\']sesskey["\']\s*:\s*["\']([A-Za-z0-9_-]{5,128})["\']'),
    re.compile(r"sesskey=([A-Za-z0-9_-]{5,128})"),
)
_USER_ID_PATTERNS = (re.compile(r'["\']user(?:id|Id)["\']\s*:\s*["\']?(\d+)'),)
_NO_HTML_FALLBACK = object()


class CampusError(RuntimeError):
    """Base error for failures talking to the private Moodle campus."""


class AuthenticationRequired(CampusError):
    """The configured Moodle credential is absent, expired, or invalid."""


class CampusProtocolError(CampusError):
    """Moodle returned an unexpected or unsupported response."""


class CampusCapabilityUnavailable(CampusProtocolError):
    """Moodle explicitly reported that an external function is unavailable."""


class CampusGateway(ABC):
    """Small Moodle contract used by the service layer."""

    @abstractmethod
    async def invoke(self, function: str, arguments: Mapping[str, Any] | None = None) -> Any:
        """Invoke an allowed Moodle external function over the configured HTTP transport."""
        ...

    async def fetch_file(self, url: str, max_bytes: int) -> tuple[bytes, str, str]:
        """Fetch one Moodle file and return bytes, media type, and a secret-free source URL."""
        raise NotImplementedError

    def session_forms(self) -> MoodleSessionForms | None:
        """Return conservative same-session HTML form fallbacks when available."""
        return None

    async def require_functions(self, functions: set[str]) -> None:
        """Fail before preview when a transport knows required functions are unavailable."""
        return None

    async def invoke_course_module(self, function: str, arguments: Mapping[str, Any]) -> Any:
        """Use a course-module ID only on transports that explicitly support it."""
        raise CampusProtocolError(
            "Esta operación requiere el identificador interno de la actividad Moodle."
        )

    @abstractmethod
    async def status(self) -> dict[str, Any]: ...

    @abstractmethod
    async def list_courses(self, include_archived: bool = False) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def action_events(self, start: int, end: int, limit: int) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def event_by_id(self, event_id: int) -> dict[str, Any]: ...

    @abstractmethod
    async def announcements(
        self, courses: Sequence[Mapping[str, Any]], limit: int
    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def search_message_contacts(self, query: str, limit: int) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def send_message(self, recipient_user_id: int, text: str) -> dict[str, Any]: ...


def _form_value(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if value is None:
        return ""
    return str(value)


def _moodle_file_url(url: str, base_url: str, *, webservice: bool) -> str:
    parsed = urlparse(url)
    base = urlparse(base_url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != base.hostname
        or parsed.port not in (None, 443)
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise ValueError("El recurso no pertenece al Campus Virtual configurado")
    path = parsed.path
    for prefix in ("/webservice/pluginfile.php", "/pluginfile.php"):
        if path == prefix or path.startswith(f"{prefix}/"):
            suffix = path[len(prefix) :]
            target = "/webservice/pluginfile.php" if webservice else "/pluginfile.php"
            return urlunparse(("https", base.netloc, f"{target}{suffix}", "", "", ""))
    raise ValueError("La URL no corresponde a un archivo Moodle permitido")


def _session_resource_url(url: str, base_url: str) -> str:
    try:
        return _moodle_file_url(url, base_url, webservice=False)
    except ValueError:
        pass
    parsed = urlparse(url)
    base = urlparse(base_url)
    allowed_query = {"chapterid", "forcedownload", "id"}
    query = parse_qs(parsed.query, keep_blank_values=True)
    module_path = re.fullmatch(r"/mod/(book|folder|page|resource|url)/view\.php", parsed.path)
    if (
        parsed.scheme != "https"
        or parsed.hostname != base.hostname
        or parsed.port not in (None, 443)
        or parsed.username
        or parsed.password
        or parsed.fragment
        or not module_path
        or set(query) - allowed_query
    ):
        raise ValueError("La URL no corresponde a un recurso Moodle permitido")
    return parsed.geturl()


async def _read_limited_response(response: httpx.Response, max_bytes: int) -> bytes:
    if not 1 <= max_bytes <= 100 * 1024 * 1024:
        raise ValueError("max_bytes debe estar entre 1 y 104857600")
    declared = response.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > max_bytes:
        raise CampusProtocolError("El recurso supera el límite de descarga configurado.")
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > max_bytes:
            raise CampusProtocolError("El recurso supera el límite de descarga configurado.")
        chunks.append(chunk)
    return b"".join(chunks)


def _flatten_form(arguments: Mapping[str, Any], prefix: str = "") -> dict[str, str]:
    """Flatten nested Moodle arguments using its bracketed form notation."""

    flattened: dict[str, str] = {}

    def visit(name: str, value: Any) -> None:
        if isinstance(value, Mapping):
            for child_key, child in value.items():
                visit(f"{name}[{child_key}]", child)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            for index, item in enumerate(value):
                visit(f"{name}[{index}]", item)
        else:
            flattened[name] = _form_value(value)

    for key, value in arguments.items():
        visit(f"{prefix}[{key}]" if prefix else str(key), value)
    return flattened


def _validate_limit(limit: int, maximum: int = 200) -> None:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= maximum:
        raise ValueError(f"limit debe estar entre 1 y {maximum}")


def _validate_message(recipient_user_id: int, text: str) -> str:
    if (
        isinstance(recipient_user_id, bool)
        or not isinstance(recipient_user_id, int)
        or recipient_user_id <= 0
    ):
        raise ValueError("recipient_user_id debe ser un entero positivo")
    if not isinstance(text, str):
        raise ValueError("text debe ser una cadena")
    message = text.strip()
    if not message:
        raise ValueError("El mensaje no puede estar vacío")
    if len(message.encode("utf-8")) > 4096:
        raise ValueError("El mensaje supera el límite de 4096 bytes de Moodle")
    return message


def _validate_contact_search(query: str, limit: int) -> str:
    _validate_limit(limit, 100)
    if not isinstance(query, str):
        raise ValueError("query debe ser una cadena")
    search = query.strip()
    if not search:
        raise ValueError("query no puede estar vacío")
    if len(search) > 200:
        raise ValueError("query no puede superar 200 caracteres")
    return search


def _moodle_error(payload: Mapping[str, Any], function: str) -> CampusError:
    exception = payload.get("exception")
    nested = exception if isinstance(exception, Mapping) else payload
    code = str(nested.get("errorcode") or payload.get("errorcode") or "").lower()
    exception_name = str(nested.get("exception") or exception or "").lower()
    message = str(nested.get("message") or payload.get("message") or "").lower()
    if code in {
        "invalidlogin",
        "invalidsesskey",
        "invalidtoken",
        "notloggedin",
        "requireloginerror",
        "servicerequireslogin",
    } or any(marker in exception_name for marker in ("invalidtoken", "require_login", "session")):
        return AuthenticationRequired(
            "La credencial del Campus Virtual no es válida o ha caducado. Ejecuta de nuevo "
            "`mcp-usc login` o configura un token válido."
        )
    unavailable = (
        code in {"wsfunctionnotavailable", "servicedonotexist", "servicenotavailable"}
        or "external_functions" in message
        or ("function" in message and ("not available" in message or "does not exist" in message))
    )
    if unavailable:
        return CampusCapabilityUnavailable(
            f"La función de Moodle {function} no está disponible para esta cuenta o servidor."
        )
    safe_code = re.sub(r"[^a-z0-9_-]", "", code)[:80]
    suffix = f" ({safe_code})" if safe_code else ""
    return CampusProtocolError(f"Moodle rechazó la operación {function}{suffix}.")


def _extract_list(payload: Any, *keys: str) -> list[dict[str, Any]]:
    value = payload
    if isinstance(payload, Mapping):
        for key in keys:
            candidate = payload.get(key)
            if isinstance(candidate, list):
                value = candidate
                break
    if not isinstance(value, list):
        raise CampusProtocolError("Moodle devolvió una lista con un formato inesperado.")
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _course_is_current(course: Mapping[str, Any]) -> bool:
    if course.get("hidden") is True:
        return False
    end = course.get("enddate")
    try:
        return not end or int(end) >= int(time.time())
    except (TypeError, ValueError):
        return True


def _normalise_contacts(payload: Any, limit: int) -> list[dict[str, Any]]:
    candidates: list[Any]
    if isinstance(payload, Mapping):
        candidates = []
        for key in ("contacts", "noncontacts", "users"):
            value = payload.get(key)
            if isinstance(value, list):
                candidates.extend(value)
    elif isinstance(payload, list):
        candidates = payload
    else:
        raise CampusProtocolError("Moodle devolvió contactos con un formato inesperado.")

    contacts: list[dict[str, Any]] = []
    seen: set[int] = set()
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        try:
            user_id = int(candidate.get("id") or candidate.get("userid"))
        except (TypeError, ValueError):
            continue
        if user_id <= 0 or user_id in seen:
            continue
        seen.add(user_id)
        contacts.append(
            {
                "id": user_id,
                "fullname": html_to_text(
                    str(candidate.get("fullname") or candidate.get("name") or ""), limit=500
                ),
                "is_contact": bool(candidate.get("iscontact", False)),
            }
        )
        if len(contacts) >= limit:
            break
    return contacts


def _sent_result(payload: Any, recipient_user_id: int) -> dict[str, Any]:
    results = _extract_list(payload)
    if not results:
        raise CampusProtocolError("Moodle no confirmó el envío del mensaje.")
    result = results[0]
    try:
        message_id = int(result.get("msgid", -1))
    except (TypeError, ValueError):
        message_id = -1
    if message_id <= 0:
        raise CampusProtocolError("Moodle no pudo entregar el mensaje al destinatario indicado.")
    response: dict[str, Any] = {
        "sent": True,
        "recipient_user_id": recipient_user_id,
        "msgid": message_id,
        "message_id": message_id,
    }
    if result.get("timecreated") is not None:
        response["created"] = result["timecreated"]
    return response


class RestMoodleGateway(CampusGateway):
    def __init__(self, settings: Settings) -> None:
        if not settings.moodle_token:
            raise AuthenticationRequired("No hay un token de Moodle configurado.")
        self.settings = settings
        self.base_url = validate_usc_url(settings.moodle_url, campus=True).rstrip("/")
        self._endpoint = validate_usc_url(
            urljoin(f"{self.base_url}/", "webservice/rest/server.php"), campus=True
        )
        self._site_info: dict[str, Any] | None = None

    async def _call(self, function: str, arguments: Mapping[str, Any] | None = None) -> Any:
        token = self.settings.moodle_token
        if not token:
            raise AuthenticationRequired("No hay un token de Moodle configurado.")
        form = {
            "wstoken": token,
            "wsfunction": function,
            "moodlewsrestformat": "json",
            **_flatten_form(arguments or {}),
        }
        try:
            async with httpx.AsyncClient(
                timeout=self.settings.request_timeout_seconds,
                follow_redirects=False,
                headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
            ) as client:
                response = await client.post(self._endpoint, data=form)
        except httpx.HTTPError as exc:
            raise CampusError("No se pudo conectar por HTTP con el Campus Virtual.") from exc
        if response.is_redirect:
            raise AuthenticationRequired("Moodle redirigió la petición REST; revisa el token.")
        if response.status_code in {401, 403}:
            raise AuthenticationRequired("Moodle rechazó el token configurado.")
        if response.status_code >= 400:
            raise CampusError(f"El Campus Virtual devolvió un error HTTP {response.status_code}.")
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise CampusProtocolError("Moodle devolvió una respuesta REST no válida.") from exc
        if isinstance(payload, Mapping) and (payload.get("exception") or payload.get("errorcode")):
            raise _moodle_error(payload, function)
        return payload

    async def invoke(self, function: str, arguments: Mapping[str, Any] | None = None) -> Any:
        if function == "core_files_upload":
            return await self._upload_draft_file(arguments or {})
        return await self._call(function, arguments)

    async def _upload_draft_file(self, arguments: Mapping[str, Any]) -> Any:
        token = self.settings.moodle_token
        if not token:
            raise AuthenticationRequired("No hay un token de Moodle configurado.")
        if arguments.get("component") != "user" or arguments.get("filearea") != "draft":
            raise ValueError("Solo se permiten subidas al borrador del usuario actual")
        try:
            item_id = int(arguments.get("itemid") or 0)
            content = base64.b64decode(str(arguments.get("filecontent") or ""), validate=True)
        except (TypeError, ValueError) as exc:
            raise ValueError("La subida de borrador contiene datos no válidos") from exc
        if item_id <= 0:
            raise ValueError("itemid debe ser positivo")
        if len(content) > self.settings.max_upload_bytes:
            raise ValueError("El archivo supera el límite de subida configurado")
        filename = str(arguments.get("filename") or "")
        filepath = str(arguments.get("filepath") or "/")
        if not filename or any(character in filename for character in "/\\\0"):
            raise ValueError("filename no es válido")
        endpoint = validate_usc_url(
            urljoin(f"{self.base_url}/", "webservice/upload.php"), campus=True
        )
        try:
            async with httpx.AsyncClient(
                timeout=self.settings.request_timeout_seconds,
                follow_redirects=False,
                headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
            ) as client:
                response = await client.post(
                    endpoint,
                    data={"token": token, "itemid": str(item_id), "filepath": filepath},
                    files={"file_1": (filename, content, "application/octet-stream")},
                )
        except httpx.HTTPError as exc:
            raise CampusError("No se pudo subir el archivo de borrador por HTTP.") from exc
        if response.is_redirect or response.status_code in {401, 403}:
            raise AuthenticationRequired("Moodle rechazó la subida con el token configurado.")
        if response.status_code >= 400:
            raise CampusError(f"El Campus Virtual devolvió un error HTTP {response.status_code}.")
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise CampusProtocolError("Moodle devolvió una subida REST no válida.") from exc
        if isinstance(payload, Mapping) and (payload.get("exception") or payload.get("errorcode")):
            raise _moodle_error(payload, "webservice/upload.php")
        return payload

    async def fetch_file(self, url: str, max_bytes: int) -> tuple[bytes, str, str]:
        token = self.settings.moodle_token
        if not token:
            raise AuthenticationRequired("No hay un token de Moodle configurado.")
        safe_url = _moodle_file_url(url, self.base_url, webservice=True)
        try:
            async with (
                httpx.AsyncClient(
                    timeout=self.settings.request_timeout_seconds,
                    follow_redirects=False,
                    headers={"User-Agent": _USER_AGENT},
                ) as client,
                client.stream("GET", safe_url, params={"token": token}) as response,
            ):
                if response.status_code in {401, 403}:
                    raise AuthenticationRequired("Moodle rechazó el acceso al recurso.")
                if response.is_redirect or response.status_code >= 400:
                    raise CampusError(
                        f"El Campus Virtual devolvió un error HTTP {response.status_code}."
                    )
                content = await _read_limited_response(response, max_bytes)
                media_type = response.headers.get("content-type", "").split(";", 1)[0]
        except CampusError:
            raise
        except httpx.HTTPError as exc:
            raise CampusError("No se pudo descargar el recurso del Campus Virtual.") from exc
        return content, media_type, safe_url

    async def _info(self) -> dict[str, Any]:
        if self._site_info is None:
            payload = await self._call("core_webservice_get_site_info")
            if not isinstance(payload, Mapping) or not payload.get("userid"):
                raise CampusProtocolError("Moodle no devolvió la identidad del usuario.")
            self._site_info = dict(payload)
        return self._site_info

    async def require_functions(self, functions: set[str]) -> None:
        info = await self._info()
        raw_functions = info.get("functions")
        if not isinstance(raw_functions, list):
            raise CampusProtocolError("Moodle no informó las funciones habilitadas para el token.")
        available = {
            str(item.get("name") or "") for item in raw_functions if isinstance(item, Mapping)
        }
        required = set(functions)
        if "core_files_upload" in required:
            required.remove("core_files_upload")
            if not bool(info.get("uploadfiles", False)):
                raise CampusCapabilityUnavailable(
                    "El servicio del token de Moodle no permite subir archivos."
                )
        missing = sorted(required - available)
        if missing:
            raise CampusCapabilityUnavailable(
                "El token de Moodle no habilita estas funciones requeridas: " + ", ".join(missing)
            )

    async def status(self) -> dict[str, Any]:
        info = await self._info()
        return {
            "authenticated": True,
            "method": "moodle_webservice",
            "site_name": html_to_text(str(info.get("sitename") or ""), limit=500),
            "user_name": html_to_text(str(info.get("fullname") or ""), limit=500),
            "user_id": int(info["userid"]),
        }

    async def list_courses(self, include_archived: bool = False) -> list[dict[str, Any]]:
        classification = "all" if include_archived else "inprogress"
        try:
            payload = await self._call(
                "core_course_get_enrolled_courses_by_timeline_classification",
                {
                    "classification": classification,
                    "limit": 0,
                    "offset": 0,
                    "sort": "fullname",
                    "customfieldname": "",
                    "customfieldvalue": "",
                },
            )
            return _extract_list(payload, "courses")
        except CampusCapabilityUnavailable:
            pass
        info = await self._info()
        courses = _extract_list(
            await self._call("core_enrol_get_users_courses", {"userid": int(info["userid"])})
        )
        if include_archived:
            return courses
        return [course for course in courses if _course_is_current(course)]

    async def action_events(self, start: int, end: int, limit: int) -> list[dict[str, Any]]:
        _validate_limit(limit)
        if start < 0 or end <= start:
            raise ValueError("El intervalo de eventos no es válido")
        payload = await self._call(
            "core_calendar_get_action_events_by_timesort",
            {
                "timesortfrom": start,
                "timesortto": end,
                "aftereventid": 0,
                "limitnum": limit,
            },
        )
        return _extract_list(payload, "events")[:limit]

    async def event_by_id(self, event_id: int) -> dict[str, Any]:
        if isinstance(event_id, bool) or not isinstance(event_id, int) or event_id <= 0:
            raise ValueError("event_id debe ser positivo")
        payload = await self._call("core_calendar_get_calendar_event_by_id", {"eventid": event_id})
        if isinstance(payload, Mapping) and isinstance(payload.get("event"), Mapping):
            return dict(payload["event"])
        if isinstance(payload, Mapping):
            return dict(payload)
        raise CampusProtocolError("Moodle devolvió un evento con un formato inesperado.")

    async def announcements(
        self, courses: Sequence[Mapping[str, Any]], limit: int
    ) -> list[dict[str, Any]]:
        _validate_limit(limit, 100)
        course_ids = [int(course["id"]) for course in courses if int(course.get("id", 0)) > 0]
        if not course_ids:
            return []
        forums = _extract_list(
            await self._call("mod_forum_get_forums_by_courses", {"courseids": course_ids}),
            "forums",
        )
        course_names = {
            int(course["id"]): str(course.get("fullname") or course.get("full_name") or "")
            for course in courses
            if int(course.get("id", 0)) > 0
        }
        selected = [forum for forum in forums if _is_announcement_forum(forum)]
        announcements: list[dict[str, Any]] = []
        for forum in selected:
            if len(announcements) >= limit:
                break
            forum_id = int(forum.get("id", 0))
            if forum_id <= 0:
                continue
            payload = await self._call(
                "mod_forum_get_forum_discussions",
                {
                    "forumid": forum_id,
                    "sortorder": -1,
                    "page": 0,
                    "perpage": limit - len(announcements),
                },
            )
            discussions = _extract_list(payload, "discussions")
            for discussion in discussions:
                item = dict(discussion)
                course_id = int(forum.get("course", forum.get("courseid", 0)) or 0)
                item.update(
                    {
                        "course_id": course_id,
                        "course_name": course_names.get(course_id, ""),
                        "forum_name": str(forum.get("name") or ""),
                    }
                )
                announcements.append(item)
        return sorted(
            announcements,
            key=lambda item: int(item.get("timemodified") or item.get("modified") or 0),
            reverse=True,
        )[:limit]

    async def search_message_contacts(self, query: str, limit: int) -> list[dict[str, Any]]:
        search = _validate_contact_search(query, limit)
        payload = await self._call(
            "core_message_search_contacts",
            {"searchtext": search, "onlymycourses": True},
        )
        return _normalise_contacts(payload, limit)

    async def send_message(self, recipient_user_id: int, text: str) -> dict[str, Any]:
        message = _validate_message(recipient_user_id, text)
        payload = await self._call(
            "core_message_send_instant_messages",
            {
                "messages": [
                    {
                        "touserid": recipient_user_id,
                        "text": message,
                        "textformat": 2,
                        "clientmsgid": uuid.uuid4().hex,
                    }
                ]
            },
        )
        return _sent_result(payload, recipient_user_id)


@dataclass(frozen=True, slots=True)
class _SessionContext:
    sesskey: str = field(repr=False)
    user_id: int
    user_name: str
    site_name: str


class HttpSessionMoodleGateway(CampusGateway):
    """Moodle client that reuses one OS-keyring session cookie over plain HTTP."""

    def __init__(self, settings: Settings, credential_store: CredentialStore | None = None) -> None:
        self.settings = settings
        self.base_url = validate_usc_url(settings.moodle_url, campus=True).rstrip("/")
        self.store = credential_store or CredentialStore()
        self._context: _SessionContext | None = None
        self._cookie_value: str | None = None

    def _session_cookie(self) -> str:
        cookie = self._cookie_value or self.store.get(SESSION_CREDENTIAL_NAME)
        if not cookie:
            raise AuthenticationRequired(
                "No hay una sesión del Campus Virtual. Ejecuta `mcp-usc login`."
            )
        self._cookie_value = cookie
        return cookie

    def _url(self, path: str, query: Mapping[str, Any] | None = None) -> str:
        url = validate_usc_url(urljoin(f"{self.base_url}/", path.lstrip("/")), campus=True)
        if query:
            url = f"{url}?{urlencode(query)}"
        return url

    def _client(self, cookie: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=False,
            cookies={"MoodleSession": cookie},
            headers={
                "User-Agent": _USER_AGENT,
                "Accept": "text/html,application/json;q=0.9,*/*;q=0.1",
            },
        )

    def _remember_rotated_cookie(self, response: httpx.Response, previous: str) -> None:
        try:
            current = response.cookies.get("MoodleSession")
        except httpx.CookieConflict:
            current = None
        if current and current != previous:
            self.store.set(SESSION_CREDENTIAL_NAME, current)
            self._cookie_value = current

    @staticmethod
    def _ensure_authenticated_response(response: httpx.Response) -> None:
        location = response.headers.get("location", "")
        path = urlparse(location).path.lower()
        if response.is_redirect and ("/login/" in path or "login.microsoftonline" in location):
            raise AuthenticationRequired(
                "La sesión del Campus Virtual ha caducado. Ejecuta `mcp-usc login`."
            )
        if response.status_code in {401, 403}:
            raise AuthenticationRequired(
                "La sesión del Campus Virtual ha caducado. Ejecuta `mcp-usc login`."
            )
        if response.is_redirect:
            raise CampusProtocolError("Moodle devolvió una redirección HTTP inesperada.")
        if response.status_code >= 400:
            raise CampusError(f"El Campus Virtual devolvió un error HTTP {response.status_code}.")

    async def _get(self, path: str, query: Mapping[str, Any] | None = None) -> httpx.Response:
        cookie = self._session_cookie()
        try:
            async with self._client(cookie) as client:
                response = await client.get(self._url(path, query))
        except httpx.HTTPError as exc:
            raise CampusError("No se pudo conectar por HTTP con el Campus Virtual.") from exc
        self._remember_rotated_cookie(response, cookie)
        self._ensure_authenticated_response(response)
        return response

    async def _session_context(self) -> _SessionContext:
        if self._context is not None:
            return self._context
        response = await self._get("my/")
        body = response.text
        soup = BeautifulSoup(body, "html.parser")
        if _looks_like_login(soup, response.url):
            raise AuthenticationRequired(
                "La sesión del Campus Virtual ha caducado. Ejecuta `mcp-usc login`."
            )
        sesskey = _extract_sesskey(soup, body)
        if not sesskey:
            raise CampusProtocolError("No se encontró la clave de sesión en el panel de Moodle.")
        user_id = _extract_user_id(soup, body)
        if user_id <= 0:
            raise CampusProtocolError("No se encontró el identificador del usuario en Moodle.")
        user_name = _first_text(
            soup,
            ".usermenu .usertext",
            "[data-region='user-menu'] .usertext",
            ".logininfo a",
        )
        site_name = _first_text(soup, ".navbar-brand", ".site-name", "title")
        self._context = _SessionContext(sesskey, user_id, user_name, site_name)
        return self._context

    async def _ajax(self, function: str, arguments: Mapping[str, Any]) -> Any:
        context = await self._session_context()
        cookie = self._session_cookie()
        request_payload = [{"index": 0, "methodname": function, "args": dict(arguments)}]
        try:
            async with self._client(cookie) as client:
                response = await client.post(
                    self._url(
                        "lib/ajax/service.php", {"sesskey": context.sesskey, "info": function}
                    ),
                    json=request_payload,
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                        "X-Requested-With": "XMLHttpRequest",
                    },
                )
        except httpx.HTTPError as exc:
            raise CampusError("No se pudo conectar por HTTP con el Campus Virtual.") from exc
        self._remember_rotated_cookie(response, cookie)
        self._ensure_authenticated_response(response)
        try:
            envelope = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise CampusProtocolError("Moodle devolvió una respuesta AJAX no válida.") from exc
        if not isinstance(envelope, list) or len(envelope) != 1:
            raise CampusProtocolError("Moodle devolvió una respuesta AJAX inesperada.")
        item = envelope[0]
        if not isinstance(item, Mapping):
            raise CampusProtocolError("Moodle devolvió una respuesta AJAX inesperada.")
        if item.get("error"):
            raise _moodle_error(item, function)
        if "data" not in item:
            raise CampusProtocolError("Moodle no incluyó datos en la respuesta AJAX.")
        return item["data"]

    async def invoke(self, function: str, arguments: Mapping[str, Any] | None = None) -> Any:
        values = arguments or {}
        try:
            return await self._ajax(function, values)
        except CampusCapabilityUnavailable as exc:
            fallback = await self._html_external(function, values)
            if fallback is _NO_HTML_FALLBACK:
                raise exc
            return fallback

    async def invoke_course_module(self, function: str, arguments: Mapping[str, Any]) -> Any:
        fallback = await self._html_external(function, arguments)
        if fallback is _NO_HTML_FALLBACK:
            raise CampusProtocolError(
                f"No hay una lectura HTML segura por course_module_id para {function}."
            )
        return fallback

    async def _html_external(self, function: str, arguments: Mapping[str, Any]) -> Any:
        if function == "core_course_get_contents":
            course_id = int(arguments.get("courseid") or 0)
            if course_id <= 0:
                raise ValueError("courseid debe ser positivo")
            page = await self._get("course/view.php", {"id": course_id})
            sections = _course_sections_from_html(page.text, str(page.url), course_id)
            section_id = _section_filter(arguments.get("options"))
            if section_id is not None:
                sections = [section for section in sections if section.get("id") == section_id]
            return sections
        if function in {
            "mod_forum_get_forums_by_courses",
            "mod_assign_get_assignments",
            "mod_quiz_get_quizzes_by_courses",
        }:
            course_ids = _positive_ids(arguments.get("courseids"), "courseids")
            if not course_ids:
                course_ids = [
                    int(course["id"])
                    for course in await self.list_courses(include_archived=False)
                    if int(course.get("id") or 0) > 0
                ]
            course_sections: dict[int, list[dict[str, Any]]] = {}
            for course_id in course_ids:
                page = await self._get("course/view.php", {"id": course_id})
                course_sections[course_id] = _course_sections_from_html(
                    page.text, str(page.url), course_id
                )
            if function == "mod_forum_get_forums_by_courses":
                return _activities_from_sections(course_sections, "forum")
            if function == "mod_quiz_get_quizzes_by_courses":
                return {"quizzes": _activities_from_sections(course_sections, "quiz")}
            courses = []
            for course_id, sections in course_sections.items():
                courses.append(
                    {
                        "id": course_id,
                        "fullname": "",
                        "assignments": _activities_from_sections({course_id: sections}, "assign"),
                    }
                )
            return {"courses": courses, "warnings": []}
        if function == "mod_forum_get_forum_discussions":
            forum_cmid = int(arguments.get("forumid") or 0)
            if forum_cmid <= 0:
                raise ValueError("forumid debe ser positivo")
            page = await self._get("mod/forum/view.php", {"id": forum_cmid})
            discussions = _forum_discussions_from_html(page.text, str(page.url), forum_cmid)
            page_number = max(0, int(arguments.get("page") or 0))
            per_page = max(1, min(100, int(arguments.get("perpage") or 20)))
            start = page_number * per_page
            return {"discussions": discussions[start : start + per_page], "warnings": []}
        if function == "mod_forum_get_discussion_posts":
            discussion_id = int(arguments.get("discussionid") or 0)
            if discussion_id <= 0:
                raise ValueError("discussionid debe ser positivo")
            page = await self._get("mod/forum/discuss.php", {"d": discussion_id})
            posts = _forum_posts_from_html(page.text, str(page.url), discussion_id)
            direction = str(arguments.get("sortdirection") or "ASC").upper()
            posts.sort(key=lambda item: int(item.get("created") or 0), reverse=direction == "DESC")
            return {"posts": posts, "warnings": []}
        if function == "mod_quiz_get_user_attempts":
            quiz_cmid = int(arguments.get("quizid") or 0)
            if quiz_cmid <= 0:
                raise ValueError("quizid debe ser positivo")
            page = await self._get("mod/quiz/view.php", {"id": quiz_cmid})
            attempts = _quiz_attempts_from_html(page.text, str(page.url), quiz_cmid)
            status = str(arguments.get("status") or "all")
            if status == "finished":
                attempts = [attempt for attempt in attempts if attempt.get("state") == "finished"]
            elif status == "unfinished":
                attempts = [attempt for attempt in attempts if attempt.get("state") != "finished"]
            return {"attempts": attempts, "warnings": []}
        return _NO_HTML_FALLBACK

    async def fetch_file(self, url: str, max_bytes: int) -> tuple[bytes, str, str]:
        safe_url = _session_resource_url(url, self.base_url)
        cookie = self._session_cookie()
        content: bytes | None = None
        media_type = ""
        final_url = safe_url
        try:
            async with self._client(cookie) as client:
                target = safe_url
                for _ in range(6):
                    request = client.build_request("GET", target)
                    response = await client.send(request, stream=True)
                    self._remember_rotated_cookie(response, cookie)
                    if response.is_redirect:
                        location = response.headers.get("location", "")
                        await response.aclose()
                        if not location:
                            break
                        target = _session_resource_url(
                            urljoin(str(response.url), location), self.base_url
                        )
                        continue
                    self._ensure_authenticated_response(response)
                    content = await _read_limited_response(response, max_bytes)
                    media_type = response.headers.get("content-type", "").split(";", 1)[0]
                    final_url = urlunparse((*urlparse(str(response.url))[:3], "", "", ""))
                    await response.aclose()
                    break
                else:
                    raise CampusProtocolError("Moodle devolvió demasiadas redirecciones.")
        except CampusError:
            raise
        except httpx.HTTPError as exc:
            raise CampusError("No se pudo descargar el recurso del Campus Virtual.") from exc
        if content is None:
            raise CampusProtocolError("Moodle no devolvió el contenido del recurso.")
        return content, media_type, final_url

    def _safe_form_url(self, url: str, query: Mapping[str, Any] | None = None) -> str:
        resolved = urljoin(f"{self.base_url}/", url)
        parsed = urlparse(resolved)
        base = urlparse(self.base_url)
        allowed_prefixes = ("/mod/assign/", "/mod/quiz/", "/course/", "/my/")
        if (
            parsed.scheme != "https"
            or parsed.hostname != base.hostname
            or parsed.port not in (None, 443)
            or parsed.username
            or parsed.password
            or not parsed.path.startswith(allowed_prefixes)
        ):
            raise CampusProtocolError("El formulario Moodle apunta a un destino no permitido.")
        if query:
            parsed = parsed._replace(query=urlencode(query), fragment="")
        return parsed.geturl()

    async def _form_request(
        self,
        method: str,
        url: str,
        *,
        query: Mapping[str, Any] | None = None,
        data: Mapping[str, str] | None = None,
        files: Mapping[str, Any] | None = None,
    ) -> FormResponse:
        target = self._safe_form_url(url, query)
        cookie = self._session_cookie()
        request_method = method.upper()
        request_data = data
        request_files = files
        try:
            async with self._client(cookie) as client:
                for _ in range(4):
                    response = await client.request(
                        request_method,
                        target,
                        data=request_data,
                        files=request_files,
                    )
                    self._remember_rotated_cookie(response, cookie)
                    if not response.is_redirect:
                        break
                    location = response.headers.get("location", "")
                    if not location:
                        break
                    target = self._safe_form_url(urljoin(str(response.url), location))
                    request_method = "GET"
                    request_data = None
                    request_files = None
                else:
                    raise CampusProtocolError("Moodle devolvió demasiadas redirecciones.")
        except CampusError:
            raise
        except httpx.HTTPError as exc:
            raise CampusError("No se pudo completar el formulario HTTP de Moodle.") from exc
        self._ensure_authenticated_response(response)
        if len(response.content) > 5 * 1024 * 1024:
            raise CampusProtocolError("La respuesta del formulario Moodle es demasiado grande.")
        return FormResponse(str(response.url), response.text, response.status_code)

    async def _form_get(self, url: str, query: Mapping[str, Any]) -> FormResponse:
        return await self._form_request("GET", url, query=query)

    async def _form_post(self, url: str, data: Mapping[str, str]) -> FormResponse:
        return await self._form_request("POST", url, data=data)

    async def _form_post_multipart(
        self,
        url: str,
        data: Mapping[str, str],
        files: Mapping[str, Any],
    ) -> FormResponse:
        safe_files: dict[str, tuple[str, bytes, str]] = {}
        for name, value in files.items():
            if not isinstance(name, str) or not isinstance(value, bytes):
                raise ValueError("Los adjuntos multipart deben proporcionarse como bytes")
            if len(value) > self.settings.max_upload_bytes:
                raise ValueError("Un adjunto supera el límite de subida configurado")
            safe_files[name] = ("attachment", value, "application/octet-stream")
        return await self._form_request("POST", url, data=data, files=safe_files)

    def session_forms(self) -> MoodleSessionForms:
        return MoodleSessionForms(
            self.base_url,
            self._form_get,
            self._form_post,
            self._form_post_multipart,
        )

    async def require_functions(self, functions: set[str]) -> None:
        unavailable = sorted(
            function
            for function in functions
            if function.startswith("core_files_") or function.startswith("mod_assign_")
        )
        if unavailable:
            raise CampusCapabilityUnavailable(
                "La sesión HTTP no expone de forma general estas funciones REST: "
                + ", ".join(unavailable)
            )

    async def status(self) -> dict[str, Any]:
        context = await self._session_context()
        return {
            "authenticated": True,
            "method": "moodle_http_session",
            "site_name": context.site_name,
            "user_name": context.user_name,
            "user_id": context.user_id,
        }

    async def list_courses(self, include_archived: bool = False) -> list[dict[str, Any]]:
        payload = await self._ajax(
            "core_course_get_enrolled_courses_by_timeline_classification",
            {
                "classification": "all" if include_archived else "inprogress",
                "limit": 0,
                "offset": 0,
                "sort": "fullname",
                "customfieldname": "",
                "customfieldvalue": "",
            },
        )
        return _extract_list(payload, "courses")

    async def action_events(self, start: int, end: int, limit: int) -> list[dict[str, Any]]:
        _validate_limit(limit)
        if start < 0 or end <= start:
            raise ValueError("El intervalo de eventos no es válido")
        payload = await self._ajax(
            "core_calendar_get_action_events_by_timesort",
            {
                "timesortfrom": start,
                "timesortto": end,
                "aftereventid": 0,
                "limitnum": limit,
            },
        )
        return _extract_list(payload, "events")[:limit]

    async def event_by_id(self, event_id: int) -> dict[str, Any]:
        if isinstance(event_id, bool) or not isinstance(event_id, int) or event_id <= 0:
            raise ValueError("event_id debe ser positivo")
        payload = await self._ajax("core_calendar_get_calendar_event_by_id", {"eventid": event_id})
        if isinstance(payload, Mapping) and isinstance(payload.get("event"), Mapping):
            return dict(payload["event"])
        if isinstance(payload, Mapping):
            return dict(payload)
        raise CampusProtocolError("Moodle devolvió un evento con un formato inesperado.")

    async def announcements(
        self, courses: Sequence[Mapping[str, Any]], limit: int
    ) -> list[dict[str, Any]]:
        _validate_limit(limit, 100)
        await self._session_context()
        found: list[dict[str, Any]] = []
        seen_discussions: set[int] = set()
        for course in courses:
            if len(found) >= limit:
                break
            try:
                course_id = int(course.get("id", 0))
            except (TypeError, ValueError):
                continue
            if course_id <= 0:
                continue
            course_name = str(course.get("fullname") or course.get("full_name") or "")
            course_page = await self._get("course/view.php", {"id": course_id})
            forums = _announcement_forum_links(course_page.text, str(course_page.url))
            for forum_name, forum_url in forums:
                if len(found) >= limit:
                    break
                forum_page = await self._get_absolute(forum_url)
                discussions = _discussion_links(forum_page.text, str(forum_page.url))
                for discussion_id, title, discussion_url, summary in discussions:
                    if len(found) >= limit or discussion_id in seen_discussions:
                        continue
                    seen_discussions.add(discussion_id)
                    item = {
                        "id": discussion_id,
                        "discussion": discussion_id,
                        "name": title,
                        "course_id": course_id,
                        "course_name": course_name,
                        "forum_name": forum_name,
                        "url": discussion_url,
                        **summary,
                    }
                    try:
                        detail_page = await self._get_absolute(discussion_url)
                        item.update(_discussion_detail(detail_page.text))
                    except AuthenticationRequired:
                        raise
                    except CampusError:
                        # The listing still provides a useful, attributable announcement.
                        pass
                    found.append(item)
        return sorted(
            found,
            key=lambda item: int(item.get("timemodified") or item.get("created") or 0),
            reverse=True,
        )[:limit]

    async def _get_absolute(self, url: str) -> httpx.Response:
        safe_url = validate_usc_url(url, campus=True)
        if urlparse(safe_url).netloc != urlparse(self.base_url).netloc:
            raise CampusProtocolError("Moodle incluyó un enlace fuera del Campus Virtual esperado.")
        cookie = self._session_cookie()
        try:
            async with self._client(cookie) as client:
                response = await client.get(safe_url)
        except httpx.HTTPError as exc:
            raise CampusError("No se pudo conectar por HTTP con el Campus Virtual.") from exc
        self._remember_rotated_cookie(response, cookie)
        self._ensure_authenticated_response(response)
        return response

    async def search_message_contacts(self, query: str, limit: int) -> list[dict[str, Any]]:
        search = _validate_contact_search(query, limit)
        context = await self._session_context()
        payload = await self._ajax(
            "core_message_message_search_users",
            {
                "userid": context.user_id,
                "search": search,
                "limitfrom": 0,
                "limitnum": limit,
            },
        )
        return _normalise_contacts(payload, limit)

    async def send_message(self, recipient_user_id: int, text: str) -> dict[str, Any]:
        message = _validate_message(recipient_user_id, text)
        payload = await self._ajax(
            "core_message_send_instant_messages",
            {
                "messages": [
                    {
                        "touserid": recipient_user_id,
                        "text": message,
                        "textformat": 2,
                        "clientmsgid": uuid.uuid4().hex,
                    }
                ]
            },
        )
        return _sent_result(payload, recipient_user_id)


def _first_text(soup: BeautifulSoup, *selectors: str) -> str:
    for selector in selectors:
        element = soup.select_one(selector)
        if element:
            text = html_to_text(element.get_text(" ", strip=True), limit=500)
            if text:
                return text
    return ""


def _looks_like_login(soup: BeautifulSoup, url: httpx.URL | str) -> bool:
    path = urlparse(str(url)).path.lower()
    return bool(
        "/login/" in path
        or soup.select_one("form#login, form[action*='/login/index.php']")
        or (soup.select_one("input[name='username']") and soup.select_one("input[name='password']"))
    )


def _extract_sesskey(soup: BeautifulSoup, body: str) -> str:
    element = soup.select_one("input[name='sesskey'][value]")
    if element:
        value = str(element.get("value") or "")
        if re.fullmatch(r"[A-Za-z0-9_-]{5,128}", value):
            return value
    for pattern in _SESSKEY_PATTERNS:
        match = pattern.search(body)
        if match:
            return match.group(1)
    return ""


def _extract_user_id(soup: BeautifulSoup, body: str) -> int:
    for pattern in _USER_ID_PATTERNS:
        match = pattern.search(body)
        if match and int(match.group(1)) > 0:
            return int(match.group(1))
    for selector, attribute in (
        ("[data-initial-user-id]", "data-initial-user-id"),
        ("[data-userid]", "data-userid"),
    ):
        element = soup.select_one(selector)
        if element:
            try:
                user_id = int(element.get(attribute) or 0)
            except (TypeError, ValueError):
                continue
            if user_id > 0:
                return user_id
    for link in soup.select("a[href*='/user/profile.php']"):
        values = parse_qs(urlparse(str(link.get("href") or "")).query).get("id", [])
        if values and values[0].isdigit() and int(values[0]) > 0:
            return int(values[0])
    return 0


def _is_announcement_forum(forum: Mapping[str, Any]) -> bool:
    forum_type = str(forum.get("type") or "").casefold()
    name = html_to_text(str(forum.get("name") or ""), limit=500).casefold()
    return forum_type == "news" or any(term in name for term in _ANNOUNCEMENT_TERMS)


def _announcement_forum_links(html: str, base_url: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    links: list[tuple[str, str]] = []
    seen: set[str] = set()
    for anchor in soup.select("a[href*='/mod/forum/view.php']"):
        href = str(anchor.get("href") or "")
        label = html_to_text(anchor.get_text(" ", strip=True), limit=500)
        parent = anchor.find_parent(["li", "div", "tr"])
        context = html_to_text(parent.get_text(" ", strip=True), limit=1_000) if parent else label
        if not any(term in f"{label} {context}".casefold() for term in _ANNOUNCEMENT_TERMS):
            continue
        try:
            url = validate_usc_url(urljoin(base_url, href), campus=True)
        except ValueError:
            continue
        if url not in seen:
            seen.add(url)
            links.append((label or "Avisos", url))
    return links


def _timestamp_from_container(container: Tag) -> int | None:
    for element in container.select("[data-timestamp], time[datetime]"):
        raw = element.get("data-timestamp")
        if raw is not None:
            try:
                return int(str(raw))
            except ValueError:
                pass
        iso = element.get("datetime")
        if iso:
            try:
                return int(datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp())
            except ValueError:
                pass
    return None


def _discussion_links(html: str, base_url: str) -> list[tuple[int, str, str, dict[str, Any]]]:
    soup = BeautifulSoup(html, "html.parser")
    discussions: list[tuple[int, str, str, dict[str, Any]]] = []
    seen: set[int] = set()
    for anchor in soup.select("a[href*='/mod/forum/discuss.php']"):
        href = str(anchor.get("href") or "")
        values = parse_qs(urlparse(href).query).get("d", [])
        if not values or not values[0].isdigit():
            continue
        discussion_id = int(values[0])
        if discussion_id <= 0 or discussion_id in seen:
            continue
        title = html_to_text(anchor.get_text(" ", strip=True), limit=1_000)
        if not title:
            continue
        try:
            url = validate_usc_url(urljoin(base_url, href), campus=True)
        except ValueError:
            continue
        container = anchor.find_parent(["tr", "li", "article", "div"])
        summary: dict[str, Any] = {}
        if isinstance(container, Tag):
            author = _first_text_from_tag(
                container, ".author", "[data-region='author-name']", ".userfullname"
            )
            if author:
                summary["author"] = author
            modified = _timestamp_from_container(container)
            if modified:
                summary["timemodified"] = modified
        seen.add(discussion_id)
        discussions.append((discussion_id, title, url, summary))
    return discussions


def _first_text_from_tag(container: Tag, *selectors: str) -> str:
    for selector in selectors:
        element = container.select_one(selector)
        if element:
            text = html_to_text(element.get_text(" ", strip=True), limit=500)
            if text:
                return text
    return ""


def _discussion_detail(html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    post = soup.select_one(".forumpost, [data-post-id], article")
    if not isinstance(post, Tag):
        return {}
    result: dict[str, Any] = {}
    subject = _first_text_from_tag(post, ".subject", "h3", "h4")
    author = _first_text_from_tag(post, ".author", "[data-region='author-name']", ".userfullname")
    message = _first_text_from_tag(
        post,
        ".posting.fullpost",
        ".content .posting",
        ".post-content-container",
        "[data-region='post-content']",
    )
    created = _timestamp_from_container(post)
    if subject:
        result["name"] = subject
    if author:
        result["author"] = author
    if message:
        result["message"] = message
    if created:
        result["created"] = created
        result.setdefault("timemodified", created)
    return result


def _positive_ids(value: Any, name: str) -> list[int]:
    if value in (None, []):
        return []
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError(f"{name} debe ser una lista de identificadores")
    ids: list[int] = []
    for raw in value:
        if isinstance(raw, bool):
            raise ValueError(f"{name} contiene un identificador no válido")
        try:
            item = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} contiene un identificador no válido") from exc
        if item <= 0:
            raise ValueError(f"{name} contiene un identificador no válido")
        if item not in ids:
            ids.append(item)
    if len(ids) > 100:
        raise ValueError(f"{name} no puede superar 100 elementos")
    return ids


def _section_filter(value: Any) -> int | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return None
    for option in value:
        if not isinstance(option, Mapping) or option.get("name") != "sectionid":
            continue
        try:
            section_id = int(option.get("value") or 0)
        except (TypeError, ValueError):
            continue
        return section_id if section_id > 0 else None
    return None


def _safe_campus_link(base_url: str, href: str) -> str | None:
    try:
        return validate_usc_url(urljoin(base_url, href), campus=True)
    except ValueError:
        return None


def _module_from_html(container: Tag, base_url: str, course_id: int) -> dict[str, Any] | None:
    anchor = container.select_one("a[href*='/mod/'][href*='/view.php']")
    if not isinstance(anchor, Tag):
        return None
    url = _safe_campus_link(base_url, str(anchor.get("href") or ""))
    if not url:
        return None
    parsed = urlparse(url)
    match = re.search(r"/mod/([a-z0-9_]+)/view\.php", parsed.path, re.IGNORECASE)
    values = parse_qs(parsed.query).get("id", [])
    if not match or not values or not values[0].isdigit():
        return None
    module_id = int(values[0])
    name_element = container.select_one(
        ".activityname, .instancename, [data-region='activityname'], .activity-title"
    )
    name = html_to_text(
        name_element.get_text(" ", strip=True)
        if name_element
        else anchor.get_text(" ", strip=True),
        limit=1_000,
    )
    description_element = container.select_one(
        ".contentafterlink, .activity-description, .description, "
        "[data-region='activity-information']"
    )
    description = (
        html_to_text(description_element.get_text(" ", strip=True), limit=12_000)
        if description_element
        else ""
    )
    contents: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for file_anchor in container.select("a[href*='/pluginfile.php/']")[:100]:
        file_url = _safe_campus_link(base_url, str(file_anchor.get("href") or ""))
        if not file_url or file_url in seen_urls:
            continue
        seen_urls.add(file_url)
        filename = html_to_text(file_anchor.get_text(" ", strip=True), limit=1_000)
        if not filename:
            filename = urlparse(file_url).path.rsplit("/", 1)[-1]
        media_type, _ = mimetypes.guess_type(filename)
        contents.append(
            {
                "type": "file",
                "filename": filename,
                "filepath": "/",
                "filesize": 0,
                "mimetype": media_type or "application/octet-stream",
                "fileurl": file_url,
            }
        )
    classes = {str(value).casefold() for value in (container.get("class") or [])}
    return {
        "id": module_id,
        "instance": 0,
        "course": course_id,
        "modname": match.group(1).casefold(),
        "name": name,
        "description": description,
        "url": url,
        "visible": "hidden" not in classes,
        "uservisible": "hidden" not in classes,
        "contents": contents,
        "id_is_course_module": True,
    }


def _course_sections_from_html(html: str, base_url: str, course_id: int) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    candidates = soup.select("li.section, .course-section, [data-sectionid]")
    if not candidates:
        body = soup.body
        candidates = [body] if isinstance(body, Tag) else []
    sections: list[dict[str, Any]] = []
    seen_sections: set[int] = set()
    seen_modules: set[int] = set()
    for index, section in enumerate(candidates):
        if not isinstance(section, Tag):
            continue
        raw_id = section.get("data-id") or section.get("data-sectionid") or section.get("id")
        match = re.search(r"(\d+)", str(raw_id or ""))
        section_id = int(match.group(1)) if match else index + 1
        if section_id in seen_sections:
            continue
        seen_sections.add(section_id)
        raw_number = section.get("data-number") or section.get("data-section") or index
        try:
            section_number = int(raw_number)
        except (TypeError, ValueError):
            section_number = index
        title = section.select_one(
            ".sectionname, .section-title, [data-region='section-title'], h3"
        )
        modules: list[dict[str, Any]] = []
        module_candidates = section.select("li.activity, .activity-item, [data-moduleid]")
        for container in module_candidates:
            if not isinstance(container, Tag):
                continue
            module = _module_from_html(container, base_url, course_id)
            if not module or int(module["id"]) in seen_modules:
                continue
            seen_modules.add(int(module["id"]))
            modules.append(module)
        sections.append(
            {
                "id": section_id,
                "section": section_number,
                "name": html_to_text(
                    title.get_text(" ", strip=True) if title else f"Sección {section_number}",
                    limit=500,
                ),
                "modules": modules,
            }
        )
    return sections


def _activities_from_sections(
    course_sections: Mapping[int, Sequence[Mapping[str, Any]]], module_type: str
) -> list[dict[str, Any]]:
    activities: list[dict[str, Any]] = []
    for course_id, sections in course_sections.items():
        for section in sections:
            modules = section.get("modules")
            if not isinstance(modules, list):
                continue
            for module in modules:
                if not isinstance(module, Mapping) or module.get("modname") != module_type:
                    continue
                module_id = int(module.get("id") or 0)
                activities.append(
                    {
                        "id": None if module_type == "assign" else module_id,
                        "cmid": module_id,
                        "coursemodule": module_id,
                        "course": course_id,
                        "name": module.get("name") or "",
                        "intro": module.get("description") or "",
                        "type": "html_fallback" if module_type == "forum" else module_type,
                        "instance_id_available": False,
                        "id_is_course_module": module_type != "assign",
                    }
                )
    return activities


def _forum_discussions_from_html(html: str, base_url: str, forum_cmid: int) -> list[dict[str, Any]]:
    discussions: list[dict[str, Any]] = []
    for discussion_id, title, url, summary in _discussion_links(html, base_url):
        discussions.append(
            {
                "id": discussion_id,
                "discussion": discussion_id,
                "forum": forum_cmid,
                "name": title,
                "discussionurl": url,
                **summary,
            }
        )
    return discussions


def _forum_posts_from_html(html: str, base_url: str, discussion_id: int) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    posts: list[dict[str, Any]] = []
    seen: set[int] = set()
    for index, post in enumerate(soup.select(".forumpost, [data-post-id]")):
        if not isinstance(post, Tag):
            continue
        raw_id = post.get("data-post-id") or post.get("id")
        match = re.search(r"(\d+)", str(raw_id or ""))
        post_id = int(match.group(1)) if match else index + 1
        if post_id in seen:
            continue
        seen.add(post_id)
        attachments = []
        for anchor in post.select("a[href*='/pluginfile.php/']")[:100]:
            file_url = _safe_campus_link(base_url, str(anchor.get("href") or ""))
            if not file_url:
                continue
            filename = html_to_text(anchor.get_text(" ", strip=True), limit=1_000)
            media_type, _ = mimetypes.guess_type(filename)
            attachments.append(
                {
                    "filename": filename,
                    "mimetype": media_type or "application/octet-stream",
                    "fileurl": file_url,
                }
            )
        posts.append(
            {
                "id": post_id,
                "discussion": discussion_id,
                "subject": _first_text_from_tag(post, ".subject", "h3", "h4"),
                "message": _first_text_from_tag(
                    post,
                    ".posting.fullpost",
                    ".content .posting",
                    ".post-content-container",
                    "[data-region='post-content']",
                ),
                "userfullname": _first_text_from_tag(
                    post, ".author", "[data-region='author-name']", ".userfullname"
                ),
                "created": _timestamp_from_container(post),
                "attachments": attachments,
            }
        )
    return posts


def _quiz_attempts_from_html(html: str, base_url: str, quiz_cmid: int) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    attempts: list[dict[str, Any]] = []
    seen: set[int] = set()
    selector = "a[href*='/mod/quiz/attempt.php'], a[href*='/mod/quiz/review.php']"
    for anchor in soup.select(selector)[:200]:
        url = _safe_campus_link(base_url, str(anchor.get("href") or ""))
        if not url:
            continue
        values = parse_qs(urlparse(url).query).get("attempt", [])
        if not values or not values[0].isdigit():
            continue
        attempt_id = int(values[0])
        if attempt_id in seen:
            continue
        seen.add(attempt_id)
        container = anchor.find_parent(["tr", "li", "div"])
        state = "inprogress" if "/attempt.php" in urlparse(url).path else "finished"
        modified = _timestamp_from_container(container) if isinstance(container, Tag) else None
        attempts.append(
            {
                "id": attempt_id,
                "quiz": quiz_cmid,
                "attempt": len(attempts) + 1,
                "state": state,
                "timemodified": modified or 0,
                "id_is_course_module": True,
            }
        )
    return attempts


def create_campus_gateway(settings: Settings | None = None) -> CampusGateway:
    configured = settings or Settings.from_env()
    validate_usc_url(configured.moodle_url, campus=True)
    if configured.moodle_token:
        return RestMoodleGateway(configured)
    return HttpSessionMoodleGateway(configured)


async def _cookie_is_authenticated(settings: Settings, cookie: str) -> bool:
    dashboard = validate_usc_url(urljoin(f"{settings.moodle_url.rstrip('/')}/", "my/"), campus=True)
    try:
        async with httpx.AsyncClient(
            timeout=settings.request_timeout_seconds,
            follow_redirects=False,
            cookies={"MoodleSession": cookie},
            headers={"User-Agent": _USER_AGENT, "Accept": "text/html"},
        ) as client:
            response = await client.get(dashboard)
    except httpx.HTTPError:
        return False
    if response.is_redirect or response.status_code >= 400:
        return False
    soup = BeautifulSoup(response.text, "html.parser")
    return not _looks_like_login(soup, response.url) and bool(_extract_sesskey(soup, response.text))


async def interactive_login(settings: Settings, timeout: int = 900) -> dict[str, Any]:
    """Open a visible, ephemeral browser solely to bootstrap MoodleSession."""

    if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 30:
        raise ValueError("timeout debe ser un entero de al menos 30 segundos")
    base_url = validate_usc_url(settings.moodle_url, campus=True).rstrip("/")
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise CampusError(
            "El login interactivo requiere el extra `browser-auth`: "
            "ejecuta `uv sync --extra browser-auth`."
        ) from exc

    deadline = time.monotonic() + timeout
    session_cookie = ""
    try:
        async with async_playwright() as playwright:
            channel = None if settings.browser_channel == "chromium" else settings.browser_channel
            browser = await playwright.chromium.launch(headless=False, channel=channel)
            try:
                context = await browser.new_context()
                page = await context.new_page()
                await page.goto(
                    validate_usc_url(urljoin(f"{base_url}/", "login/index.php"), campus=True),
                    wait_until="domcontentloaded",
                    timeout=min(timeout * 1_000, 120_000),
                )
                while time.monotonic() < deadline:
                    for cookie in await context.cookies([base_url]):
                        if cookie.get("name") != "MoodleSession" or not cookie.get("value"):
                            continue
                        candidate = str(cookie["value"])
                        if await _cookie_is_authenticated(settings, candidate):
                            session_cookie = candidate
                            break
                    if session_cookie:
                        break
                    await asyncio.sleep(1)
            finally:
                await browser.close()
    except TimeoutError:
        raise
    except Exception as exc:
        raise CampusError("No se pudo completar el login interactivo del Campus Virtual.") from exc

    if not session_cookie:
        raise TimeoutError(
            "No se completó el acceso al Campus Virtual dentro del tiempo disponible."
        )
    CredentialStore().set(SESSION_CREDENTIAL_NAME, session_cookie)
    return {"authenticated": True, "method": "moodle_http_session"}
