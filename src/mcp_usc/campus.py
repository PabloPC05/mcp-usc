from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, Tag

from .credentials import CredentialStore
from .security import html_to_text, validate_usc_url
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


class CampusError(RuntimeError):
    """Base error for failures talking to the private Moodle campus."""


class AuthenticationRequired(CampusError):
    """The configured Moodle credential is absent, expired, or invalid."""


class CampusProtocolError(CampusError):
    """Moodle returned an unexpected or unsupported response."""


class CampusGateway(ABC):
    """Small Moodle contract used by the service layer."""

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
        return CampusProtocolError(
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

    async def _info(self) -> dict[str, Any]:
        if self._site_info is None:
            payload = await self._call("core_webservice_get_site_info")
            if not isinstance(payload, Mapping) or not payload.get("userid"):
                raise CampusProtocolError("Moodle no devolvió la identidad del usuario.")
            self._site_info = dict(payload)
        return self._site_info

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
        except CampusProtocolError as exc:
            if "no está disponible" not in str(exc):
                raise
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

    def _session_cookie(self) -> str:
        cookie = self.store.get(SESSION_CREDENTIAL_NAME)
        if not cookie:
            raise AuthenticationRequired(
                "No hay una sesión del Campus Virtual. Ejecuta `mcp-usc login`."
            )
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
