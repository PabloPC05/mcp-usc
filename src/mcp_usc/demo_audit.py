"""Opt-in HTTP REST audit for Moodle's public, hourly-reset school demo.

This module is deliberately separate from the USC gateways and their destination
allowlist.  It never accepts an arbitrary host, never persists credentials, does
not retain cookies, and sends no messages, email, chats, or forum content.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
import uuid
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urljoin, urlparse

import httpx

from .student_capabilities import CAPABILITIES

DEMO_BASE_URL = "https://school.moodledemo.net"
DEMO_SERVICE = "moodle_mobile_app"
_USER_AGENT = "mcp-usc-demo-audit/1"
_MAX_RESPONSE_BYTES = 5 * 1024 * 1024
_TOKEN = re.compile(r"[^\s\x00-\x1f\x7f]{16,512}\Z")
_FUNCTION = re.compile(r"[a-z][a-z0-9_]{2,127}\Z")

# These reads supplement the generic student capability catalogue with operations
# already implemented by the dedicated assignment, quiz and collaboration modules.
_ADDITIONAL_SAFE_READS = frozenset(
    {
        "core_webservice_get_site_info",
        "mod_assign_get_assignments",
        "mod_assign_get_submission_status",
        "mod_forum_get_forums_by_courses",
        "mod_quiz_get_quizzes_by_courses",
        "mod_quiz_get_user_attempts",
    }
)
_CATALOG_READS = frozenset(
    capability.function for capability in CAPABILITIES.values() if capability.access == "read"
)
_SAFE_READS = _CATALOG_READS | _ADDITIONAL_SAFE_READS
_REVERSIBLE_MUTATIONS = frozenset(
    {"core_calendar_create_calendar_events", "core_calendar_delete_calendar_events"}
)
_CALENDAR_READBACK = "core_calendar_get_calendar_events"
_PROHIBITED_WRITES = frozenset(
    {
        "core_message_send_instant_messages",
        "mod_chat_send_chat_message",
        "mod_forum_add_discussion",
        "mod_forum_add_discussion_post",
        "mod_forum_update_discussion_post",
    }
)
_CONTEXT_SKIP_CODES = frozenset(
    {
        "accessdenied",
        "completionnotenabled",
        "invalidrecord",
        "nocourses",
        "nocriteriaset",
        "nopermissions",
        "notingroup",
        "notavailable",
    }
)
_ACTIVITY_DISCOVERY = (
    "mod_assign_get_assignments",
    "mod_book_get_books_by_courses",
    "mod_chat_get_chats_by_courses",
    "mod_choice_get_choices_by_courses",
    "mod_data_get_databases_by_courses",
    "mod_feedback_get_feedbacks_by_courses",
    "mod_folder_get_folders_by_courses",
    "mod_forum_get_forums_by_courses",
    "mod_glossary_get_glossaries_by_courses",
    "mod_h5pactivity_get_h5pactivities_by_courses",
    "mod_imscp_get_imscps_by_courses",
    "mod_label_get_labels_by_courses",
    "mod_lesson_get_lessons_by_courses",
    "mod_lti_get_ltis_by_courses",
    "mod_page_get_pages_by_courses",
    "mod_quiz_get_quizzes_by_courses",
    "mod_resource_get_resources_by_courses",
    "mod_scorm_get_scorms_by_courses",
    "mod_survey_get_surveys_by_courses",
    "mod_url_get_urls_by_courses",
    "mod_wiki_get_wikis_by_courses",
    "mod_workshop_get_workshops_by_courses",
)
_COUNTABLE_KEYS = frozenset(
    {
        "assignments",
        "badges",
        "books",
        "choices",
        "conversations",
        "courses",
        "databases",
        "events",
        "feedbacks",
        "folders",
        "forums",
        "glossaries",
        "groups",
        "h5pactivities",
        "imscps",
        "labels",
        "lessons",
        "ltis",
        "notifications",
        "pages",
        "plans",
        "preferences",
        "quizzes",
        "resources",
        "scorms",
        "surveys",
        "urls",
        "users",
        "warnings",
        "wikis",
        "workshops",
    }
)


class DemoAuditError(RuntimeError):
    pass


class DemoConfigurationError(DemoAuditError):
    pass


class DemoTransportError(DemoAuditError):
    pass


class DemoProtocolError(DemoAuditError):
    pass


class DemoRemoteError(DemoAuditError):
    def __init__(self, code: str) -> None:
        self.code = re.sub(r"[^a-zA-Z0-9_.-]", "", code)[:100] or "moodle_error"
        super().__init__(self.code)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _form_value(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if value is None:
        return ""
    return str(value)


def _flatten_form(arguments: Mapping[str, Any]) -> dict[str, str]:
    flattened: dict[str, str] = {}

    def visit(name: str, value: Any, depth: int) -> None:
        if depth > 8:
            raise ValueError("Los argumentos Moodle superan la profundidad segura")
        if isinstance(value, Mapping):
            for key, child in value.items():
                if not isinstance(key, str) or not key:
                    raise ValueError("Las claves de argumentos Moodle deben ser texto")
                visit(f"{name}[{key}]", child, depth + 1)
            return
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            for index, child in enumerate(value):
                visit(f"{name}[{index}]", child, depth + 1)
            return
        flattened[name] = _form_value(value)

    for key, value in arguments.items():
        if not isinstance(key, str) or not key:
            raise ValueError("Las claves de argumentos Moodle deben ser texto")
        visit(key, value, 0)
    if len(flattened) > 2_000:
        raise ValueError("Demasiados argumentos para la auditoría Moodle")
    return flattened


def _validate_demo_base_url(base_url: str) -> str:
    candidate = base_url.rstrip("/")
    parsed = urlparse(candidate)
    if (
        candidate != DEMO_BASE_URL
        or parsed.scheme != "https"
        or parsed.hostname != "school.moodledemo.net"
        or parsed.port not in (None, 443)
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise DemoConfigurationError("La auditoría solo admite https://school.moodledemo.net")
    return candidate


def _demo_endpoint(base_url: str, path: Literal["token", "rest"]) -> str:
    base = _validate_demo_base_url(base_url)
    relative = "login/token.php" if path == "token" else "webservice/rest/server.php"
    endpoint = urljoin(f"{base}/", relative)
    parsed = urlparse(endpoint)
    if parsed.hostname != "school.moodledemo.net" or parsed.scheme != "https":  # pragma: no cover
        raise DemoConfigurationError("El endpoint de demo salió de la allowlist")
    return endpoint


async def _read_limited(response: httpx.Response, maximum: int) -> bytes:
    declared = response.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > maximum:
        raise DemoProtocolError("La respuesta de demo supera el límite permitido")
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > maximum:
            raise DemoProtocolError("La respuesta de demo supera el límite permitido")
        chunks.append(chunk)
    return b"".join(chunks)


@dataclass(slots=True)
class DemoRestClient:
    token: str = field(repr=False)
    base_url: str = DEMO_BASE_URL
    timeout: float = 20.0
    transport: httpx.AsyncBaseTransport | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self.base_url = _validate_demo_base_url(self.base_url)
        if not _TOKEN.fullmatch(self.token):
            raise DemoConfigurationError("El token efímero de demo no tiene un formato válido")
        if not 1 <= self.timeout <= 120:
            raise DemoConfigurationError("timeout debe estar entre 1 y 120 segundos")

    @classmethod
    async def acquire(
        cls,
        *,
        username: str,
        password: str,
        base_url: str = DEMO_BASE_URL,
        timeout: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> DemoRestClient:
        if not username or not password or len(username) > 200 or len(password) > 500:
            raise DemoConfigurationError("Se requieren credenciales efímeras de demo válidas")
        payload = await cls._post_json(
            _demo_endpoint(base_url, "token"),
            {
                "username": username,
                "password": password,
                "service": DEMO_SERVICE,
            },
            timeout=timeout,
            transport=transport,
        )
        if not isinstance(payload, Mapping) or not isinstance(payload.get("token"), str):
            code = (
                str(payload.get("errorcode") or "token_unavailable")
                if isinstance(payload, Mapping)
                else "token_unavailable"
            )
            raise DemoRemoteError(code)
        return cls(
            token=str(payload["token"]),
            base_url=base_url,
            timeout=timeout,
            transport=transport,
        )

    @staticmethod
    async def _post_json(
        endpoint: str,
        form: Mapping[str, str],
        *,
        timeout: float,
        transport: httpx.AsyncBaseTransport | None,
    ) -> Any:
        try:
            async with (
                httpx.AsyncClient(
                    timeout=timeout,
                    follow_redirects=False,
                    transport=transport,
                    headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
                ) as client,
                client.stream("POST", endpoint, data=form) as response,
            ):
                if response.is_redirect:
                    raise DemoProtocolError("La demo redirigió una petición REST")
                if response.status_code >= 400:
                    raise DemoTransportError(f"La demo respondió HTTP {response.status_code}")
                content = await _read_limited(response, _MAX_RESPONSE_BYTES)
        except DemoAuditError:
            raise
        except httpx.HTTPError:
            # Never retain an httpx Request carrying credentials or a token as a cause.
            raise DemoTransportError("No se pudo conectar con la demo oficial") from None
        try:
            return json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            raise DemoProtocolError("La demo devolvió JSON no válido") from None

    async def call(
        self,
        function: str,
        arguments: Mapping[str, Any] | None = None,
        *,
        allow_reversible_mutation: bool = False,
    ) -> Any:
        if not _FUNCTION.fullmatch(function):
            raise ValueError("Nombre de función Moodle no válido")
        if function in _PROHIBITED_WRITES:
            raise DemoConfigurationError("La política de demo prohíbe esta escritura")
        if function in _REVERSIBLE_MUTATIONS:
            if not allow_reversible_mutation:
                raise DemoConfigurationError("La mutación reversible no fue habilitada")
        elif function not in _SAFE_READS:
            raise DemoConfigurationError("Función fuera de la allowlist de auditoría")
        form = {
            **_flatten_form(arguments or {}),
            "wstoken": self.token,
            "wsfunction": function,
            "moodlewsrestformat": "json",
        }
        payload = await self._post_json(
            _demo_endpoint(self.base_url, "rest"),
            form,
            timeout=self.timeout,
            transport=self.transport,
        )
        if isinstance(payload, Mapping) and (payload.get("exception") or payload.get("errorcode")):
            raise DemoRemoteError(str(payload.get("errorcode") or "moodle_exception"))
        return payload


def _probe_result(
    probe_id: str,
    category: str,
    status: Literal["pass", "fail", "skip"],
    *,
    function: str | None = None,
    reason: str | None = None,
    metrics: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": probe_id,
        "category": category,
        "status": status,
    }
    if function:
        result["function"] = function
    if reason:
        result["reason"] = reason
    if metrics:
        result["metrics"] = dict(metrics)
    return result


def _payload_metrics(payload: Any) -> dict[str, Any]:
    if isinstance(payload, list):
        return {"payload_type": "array", "items": len(payload)}
    if not isinstance(payload, Mapping):
        return {"payload_type": type(payload).__name__}
    counts = {
        str(key): len(value)
        for key, value in payload.items()
        if str(key) in _COUNTABLE_KEYS and isinstance(value, list)
    }
    metrics: dict[str, Any] = {"payload_type": "object"}
    if counts:
        metrics["collection_counts"] = dict(sorted(counts.items()))
    return metrics


async def _execute_probe(
    client: DemoRestClient,
    available: set[str],
    *,
    probe_id: str,
    category: str,
    function: str,
    arguments: Mapping[str, Any],
) -> tuple[dict[str, Any], Any | None]:
    if function not in available:
        return (
            _probe_result(
                probe_id,
                category,
                "skip",
                function=function,
                reason="function_not_advertised_for_demo_token",
            ),
            None,
        )
    try:
        payload = await client.call(function, arguments)
    except DemoRemoteError as exc:
        status: Literal["fail", "skip"] = "skip" if exc.code in _CONTEXT_SKIP_CODES else "fail"
        return (
            _probe_result(
                probe_id,
                category,
                status,
                function=function,
                reason=f"moodle_error:{exc.code}",
            ),
            None,
        )
    except DemoAuditError as exc:
        return (
            _probe_result(
                probe_id,
                category,
                "fail",
                function=function,
                reason=type(exc).__name__,
            ),
            None,
        )
    return (
        _probe_result(
            probe_id,
            category,
            "pass",
            function=function,
            metrics=_payload_metrics(payload),
        ),
        payload,
    )


def _positive_ids(values: Any, key: str, maximum: int) -> list[int]:
    if not isinstance(values, list):
        return []
    result: list[int] = []
    for item in values:
        if not isinstance(item, Mapping):
            continue
        try:
            value = int(item.get(key) or 0)
        except (TypeError, ValueError):
            continue
        if value > 0 and value not in result:
            result.append(value)
        if len(result) >= maximum:
            break
    return result


def _assignment_ids(payload: Any, maximum: int) -> list[int]:
    if not isinstance(payload, Mapping) or not isinstance(payload.get("courses"), list):
        return []
    values: list[Mapping[str, Any]] = []
    for course in payload["courses"]:
        if isinstance(course, Mapping) and isinstance(course.get("assignments"), list):
            values.extend(item for item in course["assignments"] if isinstance(item, Mapping))
    return _positive_ids(values, "id", maximum)


def _quiz_ids(payload: Any, maximum: int) -> list[int]:
    if not isinstance(payload, Mapping):
        return []
    return _positive_ids(payload.get("quizzes"), "id", maximum)


def _catalog_probe_results(available: set[str]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[str]] = defaultdict(list)
    for capability in CAPABILITIES.values():
        grouped[(capability.access, capability.category)].append(capability.function)
    results: list[dict[str, Any]] = []
    for (access, category), functions in sorted(grouped.items()):
        advertised = sorted(set(functions).intersection(available))
        missing = sorted(set(functions) - available)
        if access == "read":
            status: Literal["pass", "skip"] = "pass" if advertised else "skip"
            reason = None if advertised else "no_catalogued_read_function_advertised"
        else:
            status = "skip"
            reason = "external_write_not_executed_by_demo_policy"
        results.append(
            _probe_result(
                f"catalog.{access}.{category}",
                "catalog",
                status,
                reason=reason,
                metrics={
                    "catalogued": len(set(functions)),
                    "advertised": len(advertised),
                    "advertised_functions": advertised,
                    "not_advertised": len(missing),
                },
            )
        )
    return results


async def _personal_calendar_round_trip(
    client: DemoRestClient,
    available: set[str],
    user_id: int,
    enabled: bool,
) -> dict[str, Any]:
    probe_id = "write.personal_calendar_round_trip"
    if not enabled:
        return _probe_result(
            probe_id,
            "reversible_write",
            "skip",
            reason="requires_--allow-reversible-write",
        )
    if not (_REVERSIBLE_MUTATIONS | {_CALENDAR_READBACK}).issubset(available):
        return _probe_result(
            probe_id,
            "reversible_write",
            "skip",
            reason="create_delete_or_readback_function_not_advertised",
        )
    try:
        access = await client.call("core_calendar_get_calendar_access_information", {"courseid": 0})
        allowed = await client.call("core_calendar_get_allowed_event_types", {"courseid": 0})
    except DemoAuditError as exc:
        return _probe_result(
            probe_id,
            "reversible_write",
            "fail",
            reason=type(exc).__name__,
        )
    event_types = allowed.get("allowedeventtypes") if isinstance(allowed, Mapping) else None
    if not isinstance(access, Mapping) or access.get("canmanageownentries") is not True:
        return _probe_result(
            probe_id, "reversible_write", "skip", reason="own_calendar_events_not_allowed"
        )
    if not isinstance(event_types, list) or "user" not in event_types:
        return _probe_result(
            probe_id, "reversible_write", "skip", reason="user_event_type_not_allowed"
        )

    event_name = f"mcp-usc demo audit {uuid.uuid4().hex[:12]}"
    try:
        created = await client.call(
            "core_calendar_create_calendar_events",
            {
                "events": [
                    {
                        "name": event_name,
                        "description": "Disposable HTTP audit event; delete immediately.",
                        "format": 2,
                        "courseid": 0,
                        "groupid": 0,
                        "repeats": 0,
                        "eventtype": "user",
                        "timestart": int(time.time()) + 86_400,
                        "timeduration": 0,
                        "visible": 1,
                        "sequence": 1,
                    }
                ]
            },
            allow_reversible_mutation=True,
        )
    except DemoAuditError as exc:
        return _probe_result(
            probe_id,
            "reversible_write",
            "fail",
            reason=type(exc).__name__,
            metrics={
                "outcome": "unknown",
                "external_state_remaining": "unknown",
                "do_not_retry": True,
                "hourly_reset_is_final_cleanup": True,
            },
        )

    raw_events = created.get("events") if isinstance(created, Mapping) else None
    warnings = created.get("warnings") if isinstance(created, Mapping) else None
    if not isinstance(raw_events, list) or len(raw_events) != 1:
        return _probe_result(
            probe_id,
            "reversible_write",
            "fail",
            reason="DemoProtocolError",
            metrics={
                "outcome": "unknown",
                "external_state_remaining": "unknown",
                "do_not_retry": True,
                "hourly_reset_is_final_cleanup": True,
            },
        )
    created_event = raw_events[0]
    if not isinstance(created_event, Mapping):
        return _probe_result(
            probe_id,
            "reversible_write",
            "fail",
            reason="DemoProtocolError",
            metrics={
                "outcome": "unknown",
                "external_state_remaining": "unknown",
                "do_not_retry": True,
                "hourly_reset_is_final_cleanup": True,
            },
        )
    try:
        event_id = int(created_event.get("id") or 0)
    except (TypeError, ValueError):
        event_id = 0
    if event_id <= 0:
        return _probe_result(
            probe_id,
            "reversible_write",
            "fail",
            reason="DemoProtocolError",
            metrics={
                "outcome": "unknown",
                "external_state_remaining": "unknown",
                "do_not_retry": True,
                "hourly_reset_is_final_cleanup": True,
            },
        )

    async def read_back() -> Mapping[str, Any] | None:
        payload = await client.call(
            _CALENDAR_READBACK,
            {
                "events": {
                    "eventids": [event_id],
                    "courseids": [],
                    "groupids": [],
                    "categoryids": [],
                },
                "options": {
                    "userevents": False,
                    "siteevents": False,
                    "timestart": 0,
                    "timeend": 1,
                    "ignorehidden": True,
                },
            },
        )
        if not isinstance(payload, Mapping):
            raise DemoProtocolError("La lectura de comprobaciÃ³n no devolviÃ³ un objeto")
        events = payload.get("events")
        read_warnings = payload.get("warnings")
        if not isinstance(events, list) or read_warnings not in (None, []):
            raise DemoProtocolError("La lectura de comprobaciÃ³n es ambigua")
        matching: list[Mapping[str, Any]] = []
        for item in events:
            if not isinstance(item, Mapping):
                raise DemoProtocolError("La lectura contiene un evento invÃ¡lido")
            try:
                item_id = int(item.get("id") or 0)
            except (TypeError, ValueError):
                raise DemoProtocolError("La lectura contiene un ID invÃ¡lido") from None
            if item_id != event_id:
                raise DemoProtocolError("La lectura devolviÃ³ un evento no solicitado")
            matching.append(item)
        if len(matching) > 1:
            raise DemoProtocolError("La lectura devolviÃ³ el evento duplicado")
        return matching[0] if matching else None

    try:
        observed = await read_back()
    except DemoAuditError as exc:
        return _probe_result(
            probe_id,
            "reversible_write",
            "fail",
            reason=type(exc).__name__,
            metrics={
                "created": 1,
                "deleted": 0,
                "external_state_remaining": "unknown",
                "do_not_retry": True,
                "hourly_reset_is_final_cleanup": True,
            },
        )
    try:
        observed_owner = int(observed.get("userid") or 0) if observed else 0
    except (TypeError, ValueError):
        observed_owner = 0
    verified_owned_marker = (
        observed is not None
        and observed_owner == user_id
        and observed.get("name") == event_name
        and observed.get("eventtype") == "user"
    )
    if not verified_owned_marker:
        return _probe_result(
            probe_id,
            "reversible_write",
            "fail",
            reason="DemoProtocolError",
            metrics={
                "created": 1,
                "deleted": 0,
                "external_state_remaining": "unknown",
                "do_not_retry": True,
                "hourly_reset_is_final_cleanup": True,
            },
        )

    try:
        deleted_response = await client.call(
            "core_calendar_delete_calendar_events",
            {"events": [{"eventid": event_id, "repeat": False}]},
            allow_reversible_mutation=True,
        )
    except DemoAuditError as exc:
        return _probe_result(
            probe_id,
            "reversible_write",
            "fail",
            reason=type(exc).__name__,
            metrics={
                "created": 1,
                "deleted": 0,
                "external_state_remaining": "unknown",
                "do_not_retry": True,
                "hourly_reset_is_final_cleanup": True,
            },
        )
    try:
        remaining = await read_back()
    except DemoAuditError as exc:
        return _probe_result(
            probe_id,
            "reversible_write",
            "fail",
            reason=type(exc).__name__,
            metrics={
                "created": 1,
                "deleted": 1,
                "external_state_remaining": "unknown",
                "do_not_retry": True,
                "hourly_reset_is_final_cleanup": True,
            },
        )
    cleanup_proven = remaining is None
    response_is_valid = warnings in (None, []) and deleted_response in (None, [])
    if not cleanup_proven or not response_is_valid:
        return _probe_result(
            probe_id,
            "reversible_write",
            "fail",
            reason="DemoProtocolError",
            metrics={
                "created": 1,
                "deleted": 1,
                "external_state_remaining": not cleanup_proven,
                "read_back_checks": 2,
            },
        )
    return _probe_result(
        probe_id,
        "reversible_write",
        "pass",
        metrics={
            "created": 1,
            "deleted": 1,
            "external_state_remaining": False,
            "read_back_checks": 2,
        },
    )


async def run_demo_audit(
    client: DemoRestClient,
    *,
    allow_reversible_write: bool = False,
    max_courses: int = 3,
    max_activity_samples: int = 3,
) -> dict[str, Any]:
    if not 1 <= max_courses <= 10:
        raise ValueError("max_courses debe estar entre 1 y 10")
    if not 1 <= max_activity_samples <= 10:
        raise ValueError("max_activity_samples debe estar entre 1 y 10")
    started = _utc_now()
    probes: list[dict[str, Any]] = []
    try:
        site_info = await client.call("core_webservice_get_site_info", {})
        if not isinstance(site_info, Mapping):
            raise DemoProtocolError("core_webservice_get_site_info no devolvió un objeto")
        user_id = int(site_info.get("userid") or 0)
        functions = site_info.get("functions")
        if user_id <= 0 or not isinstance(functions, list):
            raise DemoProtocolError("La identidad o lista de funciones de demo es inválida")
        available = {
            str(item.get("name"))
            for item in functions
            if isinstance(item, Mapping) and _FUNCTION.fullmatch(str(item.get("name") or ""))
        }
        release = re.sub(r"[^0-9A-Za-z .+_-]", "", str(site_info.get("release") or ""))[:80]
        probes.append(
            _probe_result(
                "identity.site_info",
                "identity",
                "pass",
                function="core_webservice_get_site_info",
                metrics={"advertised_functions": len(available), "release": release or None},
            )
        )
    except (DemoAuditError, TypeError, ValueError) as exc:
        probes.append(
            _probe_result(
                "identity.site_info",
                "identity",
                "fail",
                function="core_webservice_get_site_info",
                reason=type(exc).__name__,
            )
        )
        return _final_report(started, probes, allow_reversible_write, site=None)

    probes.extend(_catalog_probe_results(available))

    base_specs: tuple[tuple[str, str, str, Mapping[str, Any]], ...] = (
        (
            "account.profile",
            "account",
            "core_user_get_users_by_field",
            {"field": "id", "values": [str(user_id)]},
        ),
        ("account.preferences", "account", "core_user_get_user_preferences", {"userid": user_id}),
        ("account.private_files", "account", "core_user_get_private_files_info", {}),
        ("account.badges", "account", "core_badges_get_user_badges", {"userid": user_id}),
        ("account.ai_policy", "account", "core_ai_get_policy_status", {"userid": user_id}),
        (
            "calendar.access",
            "calendar",
            "core_calendar_get_calendar_access_information",
            {"courseid": 0},
        ),
        (
            "calendar.allowed_types",
            "calendar",
            "core_calendar_get_allowed_event_types",
            {"courseid": 0},
        ),
        (
            "calendar.upcoming",
            "calendar",
            "core_calendar_get_calendar_upcoming_view",
            {"courseid": 0, "categoryid": 0},
        ),
        (
            "messages.unread_conversations",
            "messages",
            "core_message_get_unread_conversations_count",
            {"useridto": user_id},
        ),
        (
            "messages.unread_notifications",
            "messages",
            "core_message_get_unread_notification_count",
            {"useridto": user_id},
        ),
        (
            "messages.preferences",
            "messages",
            "core_message_get_user_notification_preferences",
            {"userid": user_id},
        ),
        (
            "messages.notification_stream",
            "messages",
            "core_message_get_messages",
            {
                "useridto": user_id,
                "useridfrom": 0,
                "type": "notifications",
                "read": 2,
                "newestfirst": True,
                "limitfrom": 0,
                "limitnum": 5,
            },
        ),
        (
            "competencies.user_plans",
            "competencies",
            "core_competency_list_user_plans",
            {"userid": user_id},
        ),
        ("search.areas", "search", "core_search_get_search_areas_list", {}),
        ("courses.dashboard_blocks", "courses", "core_block_get_dashboard_blocks", {}),
    )
    for probe_id, category, function, arguments in base_specs:
        result, _ = await _execute_probe(
            client,
            available,
            probe_id=probe_id,
            category=category,
            function=function,
            arguments=arguments,
        )
        probes.append(result)

    course_result, courses_payload = await _execute_probe(
        client,
        available,
        probe_id="courses.enrolled",
        category="courses",
        function="core_enrol_get_users_courses",
        arguments={"userid": user_id},
    )
    probes.append(course_result)
    course_ids = _positive_ids(courses_payload, "id", max_courses)

    if course_ids:
        for slot, course_id in enumerate(course_ids, start=1):
            course_specs = (
                ("contents", "core_course_get_contents", {"courseid": course_id}),
                (
                    "participants",
                    "core_enrol_get_enrolled_users",
                    {
                        "courseid": course_id,
                        "options": [
                            {"name": "limitfrom", "value": 0},
                            {"name": "limitnumber", "value": 5},
                        ],
                    },
                ),
                (
                    "groups",
                    "core_group_get_course_user_groups",
                    {"courseid": course_id, "userid": user_id, "groupingid": 0},
                ),
                (
                    "grade_items",
                    "gradereport_user_get_grade_items",
                    {"courseid": course_id, "userid": user_id},
                ),
                (
                    "activity_completion",
                    "core_completion_get_activities_completion_status",
                    {"courseid": course_id, "userid": user_id},
                ),
                (
                    "course_completion",
                    "core_completion_get_course_completion_status",
                    {"courseid": course_id, "userid": user_id},
                ),
            )
            for suffix, function, arguments in course_specs:
                result, _ = await _execute_probe(
                    client,
                    available,
                    probe_id=f"course_slot_{slot}.{suffix}",
                    category="course_context",
                    function=function,
                    arguments=arguments,
                )
                probes.append(result)
    else:
        probes.append(
            _probe_result(
                "course_context",
                "course_context",
                "skip",
                reason="no_enrolled_course_available",
            )
        )

    assignment_payload: Any = None
    quiz_payload: Any = None
    for function in _ACTIVITY_DISCOVERY:
        if not course_ids:
            probes.append(
                _probe_result(
                    f"activities.{function}",
                    "activities",
                    "skip",
                    function=function,
                    reason="no_enrolled_course_available",
                )
            )
            continue
        arguments: dict[str, Any] = {"courseids": course_ids}
        if function == "mod_assign_get_assignments":
            arguments.update({"capabilities": [], "includenotenrolledcourses": False})
        result, payload = await _execute_probe(
            client,
            available,
            probe_id=f"activities.{function}",
            category="activities",
            function=function,
            arguments=arguments,
        )
        probes.append(result)
        if function == "mod_assign_get_assignments":
            assignment_payload = payload
        elif function == "mod_quiz_get_quizzes_by_courses":
            quiz_payload = payload

    for slot, assignment_id in enumerate(
        _assignment_ids(assignment_payload, max_activity_samples), start=1
    ):
        result, _ = await _execute_probe(
            client,
            available,
            probe_id=f"assignments.sample_{slot}.submission_status",
            category="assignments",
            function="mod_assign_get_submission_status",
            arguments={"assignid": assignment_id, "userid": 0, "groupid": 0},
        )
        probes.append(result)
    for slot, quiz_id in enumerate(_quiz_ids(quiz_payload, max_activity_samples), start=1):
        result, _ = await _execute_probe(
            client,
            available,
            probe_id=f"quizzes.sample_{slot}.attempts",
            category="quizzes",
            function="mod_quiz_get_user_attempts",
            arguments={
                "quizid": quiz_id,
                "userid": 0,
                "status": "all",
                "includepreviews": False,
            },
        )
        probes.append(result)

    for function in sorted(_PROHIBITED_WRITES):
        probes.append(
            _probe_result(
                f"policy.blocked.{function}",
                "policy",
                "skip",
                function=function,
                reason="prohibited_external_communication",
                metrics={"advertised": function in available},
            )
        )
    probes.append(
        _probe_result(
            "policy.blocked.core_message_get_conversations",
            "policy",
            "skip",
            function="core_message_get_conversations",
            reason="ambiguous_self_conversation_bootstrap_side_effect",
            metrics={"advertised": "core_message_get_conversations" in available},
        )
    )
    probes.append(
        await _personal_calendar_round_trip(client, available, user_id, allow_reversible_write)
    )
    site = {"release": release or None, "advertised_functions": len(available)}
    return _final_report(started, probes, allow_reversible_write, site=site)


def _final_report(
    started: str,
    probes: list[dict[str, Any]],
    allow_reversible_write: bool,
    *,
    site: Mapping[str, Any] | None,
) -> dict[str, Any]:
    counts = {
        status: sum(item["status"] == status for item in probes)
        for status in ("pass", "fail", "skip")
    }
    return {
        "schema_version": 1,
        "target": DEMO_BASE_URL,
        "target_kind": "official_hourly_reset_moodle_demo",
        "started_at": started,
        "finished_at": _utc_now(),
        "site": dict(site) if site else None,
        "policy": {
            "opt_in_required": True,
            "credentials_persisted": False,
            "cookies_persisted": False,
            "messages_email_chat_forum_posts_sent": False,
            "reversible_personal_write_enabled": allow_reversible_write,
            "production_usc_allowlist_modified": False,
        },
        "summary": {"overall": "fail" if counts["fail"] else "pass", **counts},
        "probes": probes,
    }


def _configuration_failure(reason: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "target": DEMO_BASE_URL,
        "target_kind": "official_hourly_reset_moodle_demo",
        "site": None,
        "policy": {
            "opt_in_required": True,
            "credentials_persisted": False,
            "cookies_persisted": False,
            "messages_email_chat_forum_posts_sent": False,
            "production_usc_allowlist_modified": False,
        },
        "summary": {"overall": "fail", "pass": 0, "fail": 1, "skip": 0},
        "probes": [_probe_result("configuration", "configuration", "fail", reason=reason)],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=("Auditoría REST opt-in contra school.moodledemo.net; no usa configuración USC")
    )
    parser.add_argument(
        "--confirm-demo",
        action="store_true",
        help="Confirma explícitamente el destino oficial reseteable",
    )
    parser.add_argument("--username", help="Usuario público de la demo")
    parser.add_argument(
        "--allow-reversible-write",
        action="store_true",
        help="Crea y elimina inmediatamente un evento personal desechable",
    )
    parser.add_argument("--max-courses", type=int, default=3)
    parser.add_argument("--max-activity-samples", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=20.0)
    return parser


async def _run_from_args(args: argparse.Namespace) -> dict[str, Any]:
    if not args.confirm_demo:
        raise DemoConfigurationError("Falta --confirm-demo")
    token = os.getenv("MOODLE_DEMO_TOKEN")
    username = args.username or os.getenv("MOODLE_DEMO_USERNAME")
    password = os.getenv("MOODLE_DEMO_PASSWORD")
    if token:
        client = DemoRestClient(token=token, timeout=args.timeout)
    elif username and password:
        client = await DemoRestClient.acquire(
            username=username,
            password=password,
            timeout=args.timeout,
        )
    else:
        raise DemoConfigurationError(
            "Define MOODLE_DEMO_TOKEN o MOODLE_DEMO_USERNAME y MOODLE_DEMO_PASSWORD"
        )
    return await run_demo_audit(
        client,
        allow_reversible_write=bool(args.allow_reversible_write),
        max_courses=args.max_courses,
        max_activity_samples=args.max_activity_samples,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = asyncio.run(_run_from_args(args))
    except DemoRemoteError as exc:
        report = _configuration_failure(f"moodle_error:{exc.code}")
    except (DemoAuditError, ValueError) as exc:
        report = _configuration_failure(type(exc).__name__)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if report["summary"]["overall"] == "fail" else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
