from __future__ import annotations

import hashlib
import json
import mimetypes
import secrets
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .assignments import (
    delete_submission_files as moodle_delete_submission_files,
)
from .assignments import get_submission_status as moodle_get_submission_status
from .assignments import list_assignments as moodle_list_assignments
from .assignments import remove_entire_submission as moodle_remove_entire_submission
from .assignments import reopen_submission as moodle_reopen_submission
from .assignments import replace_submission_files as moodle_replace_submission_files
from .assignments import save_submission as moodle_save_submission
from .assignments import submission_plugin_safety
from .assignments import submit_for_grading as moodle_submit_for_grading
from .campus import (
    AuthenticationRequired,
    CampusCapabilityUnavailable,
    CampusError,
    CampusMutationOutcomeUnknown,
    CampusProtocolError,
    create_campus_gateway,
)
from .collaboration import (
    list_conversation_messages as moodle_list_conversation_messages,
)
from .collaboration import (
    list_course_contents as moodle_list_course_contents,
)
from .collaboration import (
    list_discussion_posts as moodle_list_discussion_posts,
)
from .collaboration import (
    list_downloadable_resources as moodle_list_downloadable_resources,
)
from .collaboration import (
    list_forum_discussions as moodle_list_forum_discussions,
)
from .collaboration import list_forums as moodle_list_forums
from .collaboration import list_messages as moodle_list_messages
from .confirmations import ACTION_CONFIRMATIONS
from .contextual_actions import (
    ContextualActionError,
)
from .contextual_actions import (
    cancel_choice_response as moodle_cancel_choice_response,
)
from .contextual_actions import (
    create_forum_discussion as moodle_create_forum_discussion,
)
from .contextual_actions import (
    create_personal_calendar_event as moodle_create_personal_calendar_event,
)
from .contextual_actions import (
    delete_personal_calendar_event as moodle_delete_personal_calendar_event,
)
from .contextual_actions import (
    preview_cancel_choice_response as moodle_preview_cancel_choice_response,
)
from .contextual_actions import (
    preview_create_forum_discussion as moodle_preview_create_forum_discussion,
)
from .contextual_actions import (
    preview_create_personal_calendar_event as moodle_preview_create_personal_calendar_event,
)
from .contextual_actions import (
    preview_delete_personal_calendar_event as moodle_preview_delete_personal_calendar_event,
)
from .contextual_actions import (
    preview_reply_forum_post as moodle_preview_reply_forum_post,
)
from .contextual_actions import (
    preview_submit_choice_response as moodle_preview_submit_choice_response,
)
from .contextual_actions import reply_forum_post as moodle_reply_forum_post
from .contextual_actions import submit_choice_response as moodle_submit_choice_response
from .domain import MADRID, normalise_announcement, normalise_course, normalise_event
from .exam_catalog import extract_academic_year, extract_subject_code, normalise_academic_year
from .local_files import inspect_upload_files
from .official_exams import (
    discover_official_exam_subjects,
    fetch_official_exam_dates,
    list_official_exam_degrees,
)
from .public_web import search_exam_sources
from .quizzes import SECURITY_NOTE as QUIZ_SECURITY_NOTE
from .quizzes import MoodleQuizClient
from .resource_text import extract_resource_text
from .security import html_to_text
from .session_forms import FormUpload
from .settings import Settings
from .student_capabilities import (
    GENERIC_ACTIONS,
    bind_account,
    capability_catalog,
    get_capability,
    sanitise_result,
    validate_arguments,
)

_MESSAGE_CONFIRMATION_TTL = 300
_MESSAGE_CONFIRMATIONS: dict[str, tuple[float, int, int, str]] = {}
_MESSAGE_CONTACT_TTL = 600
_MESSAGE_CONTACTS: dict[int, tuple[float, int, str]] = {}
_RESOURCE_TTL = 600
_RESOURCE_REFERENCES: dict[str, tuple[float, dict[str, Any]]] = {}


def _message_digest(recipient_user_id: int, text: str) -> str:
    value = f"{recipient_user_id}\0{text}".encode()
    return hashlib.sha256(value).hexdigest()


def _register_resource(resource: dict[str, Any]) -> None:
    url = resource.pop("url", None)
    if not url:
        return
    now = time.monotonic()
    expired = [token for token, item in _RESOURCE_REFERENCES.items() if item[0] < now]
    for token in expired:
        _RESOURCE_REFERENCES.pop(token, None)
    token = secrets.token_urlsafe(18)
    reference = {
        "url": url,
        "filename": str(resource.get("file_name") or resource.get("name") or "resource"),
        "media_type": str(resource.get("mime_type") or ""),
        "declared_size": int(resource.get("file_size") or 0),
    }
    _RESOURCE_REFERENCES[token] = (now + _RESOURCE_TTL, reference)
    resource["resource_token"] = token
    resource["source_url"] = url


def _register_result_resources(result: dict[str, Any]) -> dict[str, Any]:
    for item in result.get("items", []):
        if not isinstance(item, dict):
            continue
        files = item.get("files")
        if isinstance(files, list):
            for file in files:
                if isinstance(file, dict):
                    _register_resource(file)
            if not files and item.get("module_type") in {
                "book",
                "folder",
                "page",
                "resource",
                "url",
            }:
                _register_resource(item)
        else:
            _register_resource(item)
    return result


def _session_forms_or_raise(gateway: Any, error: CampusProtocolError):
    forms = gateway.session_forms()
    if forms is None:
        raise error
    return forms


async def _gateway_user_id(gateway: Any) -> int:
    identity = await gateway.status()
    try:
        user_id = int(identity.get("user_id") or 0)
    except (AttributeError, TypeError, ValueError) as exc:
        raise CampusProtocolError("Moodle no devolvió una identidad válida.") from exc
    if user_id <= 0:
        raise CampusProtocolError("Moodle no devolvió una identidad válida.")
    return user_id


async def _require_functions(gateway: Any, *functions: str) -> None:
    checker = getattr(gateway, "require_functions", None)
    if checker is not None:
        await checker(set(functions))


def _require_rest_for_assignment(gateway: Any) -> None:
    form_factory = getattr(gateway, "session_forms", None)
    if form_factory is not None and form_factory() is not None:
        raise CampusCapabilityUnavailable(
            "Las páginas HTML de tareas registran vistas y pueden cambiar la finalización. "
            "Esta operación exige un token REST de mínimo privilegio."
        )


def _session_form_client(gateway: Any):
    form_factory = getattr(gateway, "session_forms", None)
    return form_factory() if form_factory is not None else None


def _public_uploads(inspected: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "relative_path": item["relative_path"],
            "filename": item["filename"],
            "size": item["size"],
            "sha256": item["sha256"],
        }
        for item in inspected
    ]


def _capture_confirmed_uploads(
    inspected: list[dict[str, Any]], max_total_bytes: int
) -> list[FormUpload]:
    """Read once and bind the exact bytes to the already-confirmed metadata."""

    captured: list[FormUpload] = []
    total = 0
    for item in inspected:
        path = Path(item["path"])
        try:
            with path.open("rb") as handle:
                content = handle.read(max_total_bytes + 1)
        except OSError as exc:
            raise ValueError("Un archivo confirmado ya no se puede leer") from exc
        total += len(content)
        if total > max_total_bytes:
            raise ValueError("Los archivos superan el limite de subida configurado")
        if len(content) != item["size"] or hashlib.sha256(content).hexdigest() != item["sha256"]:
            raise ValueError(
                "Un archivo cambio despues de la confirmacion; no se envio ningun contenido"
            )
        media_type = mimetypes.guess_type(item["filename"])[0] or "application/octet-stream"
        captured.append(FormUpload(item["filename"], content, media_type))
    return captured


async def _session_assignment_listing(
    gateway: Any, course_ids: list[int] | None
) -> dict[str, Any]:
    courses = await gateway.list_courses(include_archived=True)
    by_id = {
        int(course["id"]): course
        for course in courses
        if isinstance(course, dict) and str(course.get("id") or "").isdigit()
    }
    ids = course_ids or list(by_id)
    unique_ids: list[int] = []
    for raw_id in ids:
        if isinstance(raw_id, bool) or not isinstance(raw_id, int) or raw_id <= 0:
            raise ValueError("course_id debe ser un entero positivo")
        if raw_id not in unique_ids:
            unique_ids.append(raw_id)
    if len(unique_ids) > 100:
        raise ValueError("No se pueden consultar mas de 100 cursos a la vez")
    assignments: list[dict[str, Any]] = []
    for course_id in unique_ids:
        raw_state = await gateway.invoke("core_courseformat_get_state", {"courseid": course_id})
        try:
            state = json.loads(raw_state) if isinstance(raw_state, str) else raw_state
        except json.JSONDecodeError as exc:
            raise CampusProtocolError("Moodle devolvio un estado de curso no valido") from exc
        if not isinstance(state, dict) or not isinstance(state.get("cm"), list):
            raise CampusProtocolError("Moodle devolvio un estado de curso inesperado")
        course = by_id.get(course_id, {})
        for raw_module in state["cm"][:2_000]:
            if not isinstance(raw_module, dict) or raw_module.get("module") != "assign":
                continue
            try:
                cmid = int(raw_module.get("id") or 0)
            except (TypeError, ValueError):
                continue
            if cmid <= 0:
                continue
            assignments.append(
                {
                    "id": None,
                    "assignment_id": None,
                    "cmid": cmid,
                    "course_module_id": cmid,
                    "name": html_to_text(str(raw_module.get("name") or ""), limit=1_000),
                    "course_id": course_id,
                    "course_name": html_to_text(
                        str(course.get("fullname") or course.get("shortname") or ""), limit=1_000
                    ),
                    "visible": bool(raw_module.get("uservisible", True)),
                    "instance_id_available": False,
                    "transport": "moodle_ajax_course_state",
                    "content_is_untrusted": True,
                }
            )
    return {
        "assignments": assignments,
        "warnings": [
            {
                "code": "cmid_only",
                "message": (
                    "La sesion HTTP devuelve course_module_id (CMID), no el identificador "
                    "interno de mod_assign."
                ),
            }
        ],
    }


def _identity_bound_payload(payload: dict[str, Any], user_id: int) -> dict[str, Any]:
    return {"authenticated_user_id": user_id, "parameters": payload}


async def _issue_action_confirmation(
    gateway: Any, action: str, payload: dict[str, Any]
) -> dict[str, Any]:
    user_id = await _gateway_user_id(gateway)
    confirmation = ACTION_CONFIRMATIONS.issue(
        action,
        _identity_bound_payload(payload, user_id),
    )
    confirmation["authenticated_user_id"] = user_id
    return confirmation


async def _consume_action_confirmation(
    gateway: Any,
    token: str,
    action: str,
    payload: dict[str, Any],
) -> int:
    user_id = await _gateway_user_id(gateway)
    ACTION_CONFIRMATIONS.consume(
        token,
        action,
        _identity_bound_payload(payload, user_id),
    )
    return user_id


async def _contextual_confirmation(
    gateway: Any,
    action: str,
    request: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    if context.get("allowed") is not True:
        context["requires_confirmation"] = False
        return context
    confirmation = await _issue_action_confirmation(
        gateway,
        action,
        {"request": request, "context": context},
    )
    return {**context, **confirmation}


async def _consume_contextual_confirmation(
    gateway: Any,
    token: str,
    action: str,
    request: dict[str, Any],
    context: dict[str, Any],
) -> None:
    if context.get("allowed") is not True:
        raise PermissionError(str(context.get("reason") or "Moodle ya no permite esta acción"))
    await _consume_action_confirmation(
        gateway,
        token,
        action,
        {"request": request, "context": context},
    )


def _unknown_contextual_result(action: str) -> dict[str, Any]:
    return {
        "request_may_have_been_sent": True,
        "outcome": "unknown",
        "do_not_retry": True,
        "action": action,
        "warning": (
            "No se pudo verificar el resultado. Moodle puede haber aplicado la acción; "
            "comprueba su estado mediante una lectura antes de tomar otra decisión."
        ),
    }


async def _session_form_mutation(action: str, operation: Any) -> dict[str, Any]:
    try:
        return await operation
    except CampusMutationOutcomeUnknown:
        return {
            "request_may_have_been_sent": True,
            "outcome": "unknown",
            "do_not_retry": True,
            "action": action,
            "warning": (
                "Moodle puede haber aplicado la operacion. No la repitas; inspecciona el "
                "estado mediante el flujo confirmado antes de decidir."
            ),
        }


class UscService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings.from_env()

    def _campus(self):
        return create_campus_gateway(self.settings)

    async def auth_status(self) -> dict[str, Any]:
        return await self._campus().status()

    async def list_student_capabilities(
        self,
        category: str | None = None,
        access: str | None = None,
        available_only: bool = False,
    ) -> dict[str, Any]:
        gateway = self._campus()
        await gateway.status()
        available = await gateway.available_functions()
        items = capability_catalog(
            category=category,
            access=access,
            available_functions=available,
        )
        if available_only and available is not None:
            items = [item for item in items if item["available_for_configured_token"]]
        return {
            "items": items,
            "count": len(items),
            "availability_known": available is not None,
            "available_only_applied": available_only and available is not None,
            "note": (
                "La disponibilidad real depende de los permisos, plugins y contexto Moodle. "
                "Las acciones siempre exigen preview y confirmación."
            ),
        }

    async def call_student_read(
        self, function: str, arguments: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        capability = get_capability(function, "read")
        gateway = self._campus()
        user_id = await _gateway_user_id(gateway)
        clean_arguments = bind_account(validate_arguments(arguments), user_id, function=function)
        await _require_functions(gateway, function)
        result = sanitise_result(await gateway.invoke(function, clean_arguments))
        return {
            "function": function,
            "category": capability.category,
            "result": result,
            "content_is_untrusted": True,
        }

    async def student_action(
        self,
        function: str,
        arguments: dict[str, Any] | None,
        confirmation_token: str | None,
    ) -> dict[str, Any]:
        capability = get_capability(function, "action")
        if function not in GENERIC_ACTIONS:
            raise ValueError(
                "Esta acción no puede ejecutarse por la interfaz genérica: necesita una "
                "previsualización específica que resuelva el objeto, propietario, curso, "
                "audiencia y alcance exactos."
            )
        gateway = self._campus()
        user_id = await _gateway_user_id(gateway)
        clean_arguments = bind_account(validate_arguments(arguments), user_id, function=function)
        payload = {
            "function": function,
            "arguments": clean_arguments,
        }
        if confirmation_token is None:
            await _require_functions(gateway, function)
            confirmation = await _issue_action_confirmation(
                gateway,
                "student_api_action",
                payload,
            )
            return {
                "preview": True,
                "function": function,
                "category": capability.category,
                "description": capability.description,
                "consequence": capability.consequence,
                "destructive": capability.destructive,
                "arguments": clean_arguments,
                **confirmation,
            }
        await _consume_action_confirmation(
            gateway,
            confirmation_token,
            "student_api_action",
            payload,
        )
        try:
            result = sanitise_result(await gateway.invoke(function, clean_arguments))
        except (AuthenticationRequired, CampusCapabilityUnavailable):
            raise
        except (CampusError, ValueError):
            return {
                "request_may_have_been_sent": True,
                "outcome": "unknown",
                "do_not_retry": True,
                "function": function,
                "warning": (
                    "No se pudo verificar el resultado. Moodle puede haber aplicado la acción; "
                    "comprueba el estado mediante una lectura antes de tomar otra decisión."
                ),
            }
        return {
            "request_sent": True,
            "outcome": "reported_by_moodle",
            "function": function,
            "result": result,
            "content_is_untrusted": True,
        }

    async def get_my_profile(self) -> dict[str, Any]:
        gateway = self._campus()
        user_id = await _gateway_user_id(gateway)
        return await self.call_student_read(
            "core_user_get_users_by_field",
            {"field": "id", "values": [str(user_id)]},
        )

    async def get_my_preferences(self, name: str | None = None) -> dict[str, Any]:
        gateway = self._campus()
        user_id = await _gateway_user_id(gateway)
        arguments: dict[str, Any] = {"userid": user_id}
        if name:
            if len(name) > 100:
                raise ValueError("name es demasiado largo")
            arguments["name"] = name
        return await self.call_student_read("core_user_get_user_preferences", arguments)

    async def list_course_participants(
        self, course_id: int, offset: int, limit: int
    ) -> dict[str, Any]:
        if course_id <= 0:
            raise ValueError("course_id debe ser positivo")
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError("offset/limit no son válidos")
        return await self.call_student_read(
            "core_enrol_get_enrolled_users",
            {
                "courseid": course_id,
                "options": [
                    {"name": "limitfrom", "value": offset},
                    {"name": "limitnumber", "value": limit},
                    {"name": "sortby", "value": "fullname"},
                    {"name": "sortdirection", "value": "ASC"},
                ],
            },
        )

    async def list_my_groups(self, course_id: int) -> dict[str, Any]:
        if course_id <= 0:
            raise ValueError("course_id debe ser positivo")
        gateway = self._campus()
        user_id = await _gateway_user_id(gateway)
        return await self.call_student_read(
            "core_group_get_course_user_groups",
            {"courseid": course_id, "userid": user_id, "groupingid": 0},
        )

    async def get_my_grades(self, course_id: int | None = None) -> dict[str, Any]:
        gateway = self._campus()
        user_id = await _gateway_user_id(gateway)
        if course_id is None:
            return await self.call_student_read(
                "gradereport_overview_get_course_grades",
                {"userid": user_id},
            )
        if course_id <= 0:
            raise ValueError("course_id debe ser positivo")
        return await self.call_student_read(
            "gradereport_user_get_grade_items",
            {"courseid": course_id, "userid": user_id, "groupid": 0},
        )

    async def get_my_completion(self, course_id: int) -> dict[str, Any]:
        if course_id <= 0:
            raise ValueError("course_id debe ser positivo")
        gateway = self._campus()
        user_id = await _gateway_user_id(gateway)
        functions = {
            "core_completion_get_activities_completion_status",
            "core_completion_get_course_completion_status",
        }
        await _require_functions(gateway, *functions)
        activity_status = await gateway.invoke(
            "core_completion_get_activities_completion_status",
            {"courseid": course_id, "userid": user_id},
        )
        course_status = await gateway.invoke(
            "core_completion_get_course_completion_status",
            {"courseid": course_id, "userid": user_id},
        )
        return {
            "course_id": course_id,
            "activities": sanitise_result(activity_status),
            "course": sanitise_result(course_status),
            "content_is_untrusted": True,
        }

    async def list_notifications(
        self, status: str = "unread", offset: int = 0, limit: int = 20
    ) -> dict[str, Any]:
        if status not in {"unread", "read", "all"}:
            raise ValueError("status debe ser unread, read o all")
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError("offset/limit no son válidos")
        gateway = self._campus()
        user_id = await _gateway_user_id(gateway)
        arguments: dict[str, Any] = {
            "useridto": user_id,
            "newestfirst": True,
            "limit": limit,
            "offset": offset,
        }
        if status != "all":
            arguments["status"] = status
        return await self.call_student_read("message_popup_get_popup_notifications", arguments)

    async def list_calendar_events(
        self,
        start: int,
        end: int,
        course_ids: list[int] | None = None,
        include_user_events: bool = True,
        include_site_events: bool = True,
    ) -> dict[str, Any]:
        if start < 0 or end <= start:
            raise ValueError("El intervalo del calendario no es válido")
        ids = course_ids or []
        if len(ids) > 100 or any(isinstance(value, bool) or value <= 0 for value in ids):
            raise ValueError("course_ids contiene identificadores no válidos")
        return await self.call_student_read(
            "core_calendar_get_calendar_events",
            {
                "events": {
                    "eventids": [],
                    "courseids": ids,
                    "groupids": [],
                    "categoryids": [],
                },
                "options": {
                    "userevents": include_user_events,
                    "siteevents": include_site_events,
                    "timestart": start,
                    "timeend": end,
                    "ignorehidden": False,
                },
            },
        )

    async def list_my_badges(
        self, course_id: int = 0, page: int = 0, per_page: int = 50
    ) -> dict[str, Any]:
        if course_id < 0 or page < 0 or not 1 <= per_page <= 100:
            raise ValueError("course_id/page/per_page no son válidos")
        gateway = self._campus()
        user_id = await _gateway_user_id(gateway)
        return await self.call_student_read(
            "core_badges_get_user_badges",
            {
                "userid": user_id,
                "courseid": course_id,
                "page": page,
                "perpage": per_page,
                "search": "",
                "onlypublic": False,
            },
        )

    async def get_private_files_info(self) -> dict[str, Any]:
        return await self.call_student_read("core_user_get_private_files_info", {})

    async def get_quiz_attempt_review(self, attempt_id: int, page: int = -1) -> dict[str, Any]:
        if attempt_id <= 0 or page < -1:
            raise ValueError("attempt_id/page no son válidos")
        return await self.call_student_read(
            "mod_quiz_get_attempt_review",
            {"attemptid": attempt_id, "page": page},
        )

    async def get_quiz_best_grade(self, quiz_id: int) -> dict[str, Any]:
        if quiz_id <= 0:
            raise ValueError("quiz_id debe ser positivo")
        return await self.call_student_read(
            "mod_quiz_get_user_best_grade",
            {"quizid": quiz_id, "userid": 0},
        )

    async def preview_create_personal_calendar_event(
        self,
        name: str,
        timestart: int,
        description: str = "",
        duration: int = 0,
        repeats: int = 0,
    ) -> dict[str, Any]:
        gateway = self._campus()
        await _require_functions(
            gateway,
            "core_webservice_get_site_info",
            "core_calendar_get_calendar_access_information",
            "core_calendar_get_allowed_event_types",
            "core_calendar_create_calendar_events",
        )
        request = {
            "name": name,
            "timestart": timestart,
            "description": description,
            "duration": duration,
            "repeats": repeats,
        }
        context = await moodle_preview_create_personal_calendar_event(gateway.invoke, **request)
        return await _contextual_confirmation(
            gateway, "contextual.create_personal_calendar_event", request, context
        )

    async def create_personal_calendar_event(
        self,
        name: str,
        timestart: int,
        confirmation_token: str,
        description: str = "",
        duration: int = 0,
        repeats: int = 0,
    ) -> dict[str, Any]:
        gateway = self._campus()
        request = {
            "name": name,
            "timestart": timestart,
            "description": description,
            "duration": duration,
            "repeats": repeats,
        }
        context = await moodle_preview_create_personal_calendar_event(gateway.invoke, **request)
        await _consume_contextual_confirmation(
            gateway,
            confirmation_token,
            "contextual.create_personal_calendar_event",
            request,
            context,
        )
        try:
            return await moodle_create_personal_calendar_event(
                gateway.invoke,
                **request,
                expected_owner_user_id=int(context["owner_user_id"]),
                client_request_id=f"mcp-{secrets.token_hex(12)}",
            )
        except (AuthenticationRequired, CampusCapabilityUnavailable):
            raise
        except (CampusError, ContextualActionError):
            return _unknown_contextual_result("create_personal_calendar_event")

    async def preview_delete_personal_calendar_event(
        self, event_id: int, scope: str = "single"
    ) -> dict[str, Any]:
        gateway = self._campus()
        await _require_functions(
            gateway,
            "core_webservice_get_site_info",
            "core_calendar_get_calendar_event_by_id",
            "core_calendar_get_calendar_access_information",
            "core_calendar_delete_calendar_events",
        )
        request = {"event_id": event_id, "scope": scope}
        context = await moodle_preview_delete_personal_calendar_event(gateway.invoke, **request)
        return await _contextual_confirmation(
            gateway, "contextual.delete_personal_calendar_event", request, context
        )

    async def delete_personal_calendar_event(
        self, event_id: int, scope: str, confirmation_token: str
    ) -> dict[str, Any]:
        gateway = self._campus()
        request = {"event_id": event_id, "scope": scope}
        context = await moodle_preview_delete_personal_calendar_event(gateway.invoke, **request)
        await _consume_contextual_confirmation(
            gateway,
            confirmation_token,
            "contextual.delete_personal_calendar_event",
            request,
            context,
        )
        try:
            return await moodle_delete_personal_calendar_event(
                gateway.invoke,
                **request,
                client_request_id=f"mcp-{secrets.token_hex(12)}",
            )
        except (AuthenticationRequired, CampusCapabilityUnavailable):
            raise
        except (CampusError, ContextualActionError):
            return _unknown_contextual_result("delete_personal_calendar_event")

    async def preview_create_forum_discussion(
        self,
        course_id: int,
        forum_id: int,
        subject: str,
        message: str,
        group_id: int = 0,
    ) -> dict[str, Any]:
        gateway = self._campus()
        await _require_functions(
            gateway,
            "mod_forum_get_forums_by_courses",
            "mod_forum_get_forum_access_information",
            "core_group_get_activity_groupmode",
            "core_group_get_activity_allowed_groups",
            "mod_forum_can_add_discussion",
            "mod_forum_add_discussion",
        )
        request = {
            "course_id": course_id,
            "forum_id": forum_id,
            "subject": subject,
            "message": message,
            "group_id": group_id,
        }
        context = await moodle_preview_create_forum_discussion(gateway.invoke, **request)
        return await _contextual_confirmation(
            gateway, "contextual.create_forum_discussion", request, context
        )

    async def create_forum_discussion(
        self,
        course_id: int,
        forum_id: int,
        subject: str,
        message: str,
        group_id: int,
        confirmation_token: str,
    ) -> dict[str, Any]:
        gateway = self._campus()
        request = {
            "course_id": course_id,
            "forum_id": forum_id,
            "subject": subject,
            "message": message,
            "group_id": group_id,
        }
        context = await moodle_preview_create_forum_discussion(gateway.invoke, **request)
        await _consume_contextual_confirmation(
            gateway,
            confirmation_token,
            "contextual.create_forum_discussion",
            request,
            context,
        )
        try:
            return await moodle_create_forum_discussion(
                gateway.invoke,
                forum_id=forum_id,
                subject=subject,
                message=message,
                group_id=int(context["resolved_group_id"]),
                client_request_id=f"mcp-{secrets.token_hex(12)}",
            )
        except (AuthenticationRequired, CampusCapabilityUnavailable):
            raise
        except (CampusError, ContextualActionError):
            return _unknown_contextual_result("create_forum_discussion")

    async def preview_reply_forum_post(
        self,
        course_id: int,
        forum_id: int,
        parent_post_id: int,
        message: str,
        subject: str | None = None,
        group_id: int = 0,
    ) -> dict[str, Any]:
        gateway = self._campus()
        await _require_functions(
            gateway,
            "mod_forum_get_forums_by_courses",
            "mod_forum_get_forum_access_information",
            "mod_forum_get_discussion_post",
            "mod_forum_get_forum_discussions",
            "core_group_get_activity_groupmode",
            "core_group_get_activity_allowed_groups",
            "mod_forum_add_discussion_post",
        )
        request = {
            "course_id": course_id,
            "forum_id": forum_id,
            "parent_post_id": parent_post_id,
            "message": message,
            "subject": subject,
            "group_id": group_id,
        }
        context = await moodle_preview_reply_forum_post(gateway.invoke, **request)
        return await _contextual_confirmation(
            gateway, "contextual.reply_forum_post", request, context
        )

    async def reply_forum_post(
        self,
        course_id: int,
        forum_id: int,
        parent_post_id: int,
        message: str,
        confirmation_token: str,
        subject: str | None = None,
        group_id: int = 0,
    ) -> dict[str, Any]:
        gateway = self._campus()
        request = {
            "course_id": course_id,
            "forum_id": forum_id,
            "parent_post_id": parent_post_id,
            "message": message,
            "subject": subject,
            "group_id": group_id,
        }
        context = await moodle_preview_reply_forum_post(gateway.invoke, **request)
        await _consume_contextual_confirmation(
            gateway,
            confirmation_token,
            "contextual.reply_forum_post",
            request,
            context,
        )
        try:
            return await moodle_reply_forum_post(
                gateway.invoke,
                parent_post_id=parent_post_id,
                subject=str(context["subject"]),
                message=message,
                client_request_id=f"mcp-{secrets.token_hex(12)}",
            )
        except (AuthenticationRequired, CampusCapabilityUnavailable):
            raise
        except (CampusError, ContextualActionError):
            return _unknown_contextual_result("reply_forum_post")

    async def preview_submit_choice_response(
        self,
        course_id: int,
        choice_id: int,
        option_texts: list[str],
    ) -> dict[str, Any]:
        gateway = self._campus()
        await _require_functions(
            gateway,
            "mod_choice_get_choices_by_courses",
            "mod_choice_get_choice_options",
            "mod_choice_submit_choice_response",
        )
        request = {
            "course_id": course_id,
            "choice_id": choice_id,
            "option_texts": option_texts,
        }
        context = await moodle_preview_submit_choice_response(gateway.invoke, **request)
        return await _contextual_confirmation(
            gateway, "contextual.submit_choice_response", request, context
        )

    async def submit_choice_response(
        self,
        course_id: int,
        choice_id: int,
        option_texts: list[str],
        confirmation_token: str,
    ) -> dict[str, Any]:
        gateway = self._campus()
        request = {
            "course_id": course_id,
            "choice_id": choice_id,
            "option_texts": option_texts,
        }
        context = await moodle_preview_submit_choice_response(gateway.invoke, **request)
        await _consume_contextual_confirmation(
            gateway,
            confirmation_token,
            "contextual.submit_choice_response",
            request,
            context,
        )
        try:
            return await moodle_submit_choice_response(
                gateway.invoke,
                choice_id=choice_id,
                option_ids=[int(value) for value in context["option_ids"]],
                client_request_id=f"mcp-{secrets.token_hex(12)}",
            )
        except (AuthenticationRequired, CampusCapabilityUnavailable):
            raise
        except (CampusError, ContextualActionError):
            return _unknown_contextual_result("submit_choice_response")

    async def preview_cancel_choice_response(
        self, course_id: int, choice_id: int
    ) -> dict[str, Any]:
        gateway = self._campus()
        await _require_functions(
            gateway,
            "mod_choice_get_choices_by_courses",
            "mod_choice_get_choice_options",
            "mod_choice_delete_choice_responses",
        )
        request = {"course_id": course_id, "choice_id": choice_id}
        context = await moodle_preview_cancel_choice_response(gateway.invoke, **request)
        return await _contextual_confirmation(
            gateway, "contextual.cancel_choice_response", request, context
        )

    async def cancel_choice_response(
        self, course_id: int, choice_id: int, confirmation_token: str
    ) -> dict[str, Any]:
        gateway = self._campus()
        request = {"course_id": course_id, "choice_id": choice_id}
        context = await moodle_preview_cancel_choice_response(gateway.invoke, **request)
        await _consume_contextual_confirmation(
            gateway,
            confirmation_token,
            "contextual.cancel_choice_response",
            request,
            context,
        )
        try:
            return await moodle_cancel_choice_response(
                gateway.invoke,
                choice_id=choice_id,
                client_request_id=f"mcp-{secrets.token_hex(12)}",
            )
        except (AuthenticationRequired, CampusCapabilityUnavailable):
            raise
        except (CampusError, ContextualActionError):
            return _unknown_contextual_result("cancel_choice_response")

    async def list_courses(self, include_archived: bool = False) -> list[dict[str, Any]]:
        courses = await self._campus().list_courses(include_archived)
        return [normalise_course(course) for course in courses]

    async def list_events(
        self,
        *,
        days: int,
        include_overdue: bool,
        course_ids: list[int] | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        if days < 1 or days > 366:
            raise ValueError("days debe estar entre 1 y 366")
        if limit < 1 or limit > 200:
            raise ValueError("limit debe estar entre 1 y 200")
        now = datetime.now(MADRID)
        start = 0 if include_overdue else int(now.timestamp())
        end = int((now + timedelta(days=days)).timestamp())
        events = await self._campus().action_events(start, end, limit)
        wanted = set(course_ids or [])
        normalised = [normalise_event(event) for event in events]
        if wanted:
            normalised = [event for event in normalised if event.get("course_id") in wanted]
        return normalised

    async def get_work_item(self, event_id: int) -> dict[str, Any]:
        if event_id <= 0:
            raise ValueError("event_id debe ser positivo")
        event = await self._campus().event_by_id(event_id)
        return normalise_event(event)

    async def list_announcements(
        self, course_ids: list[int] | None, since_days: int, limit: int
    ) -> list[dict[str, Any]]:
        if since_days < 1 or since_days > 366:
            raise ValueError("since_days debe estar entre 1 y 366")
        if limit < 1 or limit > 100:
            raise ValueError("limit debe estar entre 1 y 100")
        gateway = self._campus()
        courses = await gateway.list_courses(include_archived=False)
        wanted = set(course_ids or [])
        if wanted:
            courses = [course for course in courses if int(course["id"]) in wanted]
        raw = await gateway.announcements(courses, limit)
        course_names = {int(course["id"]): course.get("fullname", "") for course in courses}
        normalised = [
            normalise_announcement(
                item,
                course_id=int(item.get("course_id", 0)),
                course_name=item.get("course_name")
                or course_names.get(int(item.get("course_id", 0)), ""),
                forum_name=item.get("forum_name", ""),
            )
            for item in raw
        ]
        cutoff = time.time() - since_days * 86400
        filtered = []
        for item, original in zip(normalised, raw, strict=False):
            modified = original.get("timemodified") or original.get("modified")
            modified = modified or original.get("created")
            if modified in (None, "") or int(modified) >= cutoff:
                filtered.append(item)
        return filtered[:limit]

    async def search_exams(
        self, query: str, source_urls: list[str] | None, max_documents: int
    ) -> dict[str, object]:
        sources = tuple(source_urls) if source_urls else self.settings.exam_sources
        return await search_exam_sources(
            sources,
            query=query,
            max_documents=max_documents,
            timeout=self.settings.request_timeout_seconds,
        )

    def list_official_exam_degrees(self) -> dict[str, Any]:
        degrees = list_official_exam_degrees()
        return {
            "degrees": degrees,
            "count": len(degrees),
            "note": (
                "Crosswalk institucional cerrado de las dos ediciones del doble grado. "
                "Las asignaturas se descubren dinámicamente para cada curso académico."
            ),
        }

    async def list_official_exam_subjects(
        self, academic_year: str | None, degree_keys: list[str] | None
    ) -> dict[str, Any]:
        if academic_year is None:
            return {
                "status": "academic_year_required",
                "subjects": [],
                "degrees": list_official_exam_degrees(),
                "note": "Indica un curso académico exacto con formato 2025/2026.",
            }
        return await discover_official_exam_subjects(
            academic_year,
            degree_keys,
            timeout=self.settings.request_timeout_seconds,
        )

    async def get_official_exam_dates(
        self,
        subject_codes: list[str],
        academic_year: str,
        degree_keys: list[str] | None = None,
    ) -> dict[str, Any]:
        return await fetch_official_exam_dates(
            subject_codes,
            academic_year,
            degree_keys,
            timeout=self.settings.request_timeout_seconds,
        )

    async def get_my_official_exam_schedule(
        self, academic_year: str, degree_keys: list[str] | None = None
    ) -> dict[str, Any]:
        academic_year = normalise_academic_year(academic_year)
        courses = [
            normalise_course(course)
            for course in await self._campus().list_courses(include_archived=True)
        ]
        selected: list[dict[str, Any]] = []
        seen_codes: list[str] = []
        for course in courses:
            code = extract_subject_code(course.get("short_name"), course.get("full_name"))
            course_year = extract_academic_year(course.get("short_name"), course.get("full_name"))
            if code is None or (course_year is not None and course_year != academic_year):
                continue
            selected.append(
                {
                    "course_id": course["id"],
                    "subject_code": code,
                    "course_name": course["full_name"],
                    "course_academic_year": course_year,
                    "dashboard_hidden": course["dashboard_hidden"],
                    "content_is_untrusted": True,
                }
            )
            if code not in seen_codes:
                seen_codes.append(code)

        if not seen_codes:
            return {
                "academic_year": academic_year,
                "matched_moodle_courses": [],
                "subjects": [],
                "sources_checked": [],
                "note": (
                    "No se encontraron códigos G de ese curso académico entre los cursos "
                    "visibles u ocultos del tablero. No se consultó ninguna fuente pública."
                ),
            }
        result = await self.get_official_exam_dates(seen_codes, academic_year, degree_keys)
        result["matched_moodle_courses"] = selected
        return result

    async def search_message_contacts(self, query: str, limit: int) -> list[dict[str, Any]]:
        query = query.strip()
        if len(query) < 2:
            raise ValueError("query debe contener al menos 2 caracteres")
        if limit < 1 or limit > 50:
            raise ValueError("limit debe estar entre 1 y 50")
        gateway = self._campus()
        authenticated_user_id = await _gateway_user_id(gateway)
        result = await gateway.search_message_contacts(query, limit)
        if isinstance(result, list):
            contacts = result
        else:
            contacts = [
                *result.get("contacts", []),
                *result.get("noncontacts", []),
            ]
        normalised: list[dict[str, Any]] = []
        for contact in contacts[:limit]:
            user_id = contact.get("id") or contact.get("userid")
            if not user_id:
                continue
            user_id = int(user_id)
            full_name = html_to_text(contact.get("fullname") or contact.get("full_name"))
            normalised.append(
                {
                    "user_id": user_id,
                    "full_name": full_name,
                    "is_contact": bool(contact.get("iscontact") or contact.get("is_contact")),
                    "content_is_untrusted": True,
                }
            )
            _MESSAGE_CONTACTS[user_id] = (
                time.monotonic() + _MESSAGE_CONTACT_TTL,
                authenticated_user_id,
                full_name,
            )
        return normalised

    async def list_conversations(
        self,
        offset: int,
        limit: int,
        conversation_type: int | None,
        favourites: bool | None,
    ) -> dict[str, Any]:
        del offset, limit, conversation_type, favourites
        raise CampusCapabilityUnavailable(
            "Moodle puede crear y marcar como favorita una conversación consigo mismo al listar "
            "conversaciones. Usa list_messages, que emplea una lectura sin ese efecto lateral."
        )

    async def list_messages(
        self,
        direction: str,
        message_type: str,
        read_status: str,
        offset: int,
        limit: int,
        newest: bool,
    ) -> dict[str, Any]:
        gateway = self._campus()
        identity = await gateway.status()
        return await moodle_list_messages(
            gateway.invoke,
            user_id=int(identity["user_id"]),
            direction=direction,
            message_type=message_type,
            read_status=read_status,
            offset=offset,
            limit=limit,
            newest=newest,
        )

    async def list_conversation_messages(
        self,
        conversation_id: int,
        offset: int,
        limit: int,
        newest: bool,
        time_from: int,
    ) -> dict[str, Any]:
        gateway = self._campus()
        identity = await gateway.status()
        return await moodle_list_conversation_messages(
            gateway.invoke,
            user_id=int(identity["user_id"]),
            conversation_id=conversation_id,
            offset=offset,
            limit=limit,
            newest=newest,
            time_from=time_from,
        )

    async def list_forums(
        self, course_ids: list[int] | None, offset: int, limit: int
    ) -> dict[str, Any]:
        return await moodle_list_forums(
            self._campus().invoke,
            course_ids=course_ids or (),
            offset=offset,
            limit=limit,
        )

    async def list_forum_discussions(
        self, forum_id: int, page: int, per_page: int
    ) -> dict[str, Any]:
        return await moodle_list_forum_discussions(
            self._campus().invoke,
            forum_id=forum_id,
            page=page,
            per_page=per_page,
        )

    async def list_discussion_posts(
        self, discussion_id: int, offset: int, limit: int
    ) -> dict[str, Any]:
        del discussion_id, offset, limit
        raise CampusCapabilityUnavailable(
            "Moodle puede marcar publicaciones como leídas al obtener una discusión. Usa "
            "preview_inspect_discussion_posts y después inspect_discussion_posts."
        )

    async def _inspect_discussion_posts(
        self, discussion_id: int, offset: int, limit: int
    ) -> dict[str, Any]:
        return await moodle_list_discussion_posts(
            self._campus().invoke,
            discussion_id=discussion_id,
            offset=offset,
            limit=limit,
        )

    async def inspect_discussion_posts(
        self,
        discussion_id: int,
        offset: int,
        limit: int,
        confirmation_token: str | None,
    ) -> dict[str, Any]:
        if discussion_id <= 0:
            raise ValueError("discussion_id debe ser positivo")
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError("offset/limit no son válidos")
        payload = {
            "discussion_id": discussion_id,
            "offset": offset,
            "limit": limit,
        }
        gateway = self._campus()
        if confirmation_token is None:
            await _require_functions(gateway, "mod_forum_get_discussion_posts")
            confirmation = await _issue_action_confirmation(
                gateway,
                "forum.inspect_posts_stateful",
                payload,
            )
            return {
                "preview": True,
                **payload,
                "warning": (
                    "Moodle puede marcar como leídas las publicaciones devueltas y registrar "
                    "seguimiento. Esta vista previa no abre la discusión."
                ),
                **confirmation,
            }
        await _consume_action_confirmation(
            gateway,
            confirmation_token,
            "forum.inspect_posts_stateful",
            payload,
        )
        try:
            result = await moodle_list_discussion_posts(
                gateway.invoke,
                discussion_id=discussion_id,
                offset=offset,
                limit=limit,
            )
        except (AuthenticationRequired, CampusCapabilityUnavailable):
            raise
        except CampusError:
            return {
                "request_may_have_been_sent": True,
                "outcome": "unknown",
                "do_not_retry": True,
                "discussion_id": discussion_id,
                "warning": (
                    "Moodle puede haber actualizado el estado de lectura. Comprueba la "
                    "discusión antes de repetir la operación."
                ),
            }
        result["stateful_inspection_confirmed"] = True
        return result

    async def list_course_contents(
        self,
        course_id: int,
        section_id: int | None,
        offset: int,
        limit: int,
    ) -> dict[str, Any]:
        result = await moodle_list_course_contents(
            self._campus().invoke,
            course_id=course_id,
            section_id=section_id,
            offset=offset,
            limit=limit,
        )
        return _register_result_resources(result)

    async def list_course_resources(
        self,
        course_id: int,
        section_id: int | None,
        offset: int,
        limit: int,
    ) -> dict[str, Any]:
        result = await moodle_list_downloadable_resources(
            self._campus().invoke,
            course_id=course_id,
            section_id=section_id,
            offset=offset,
            limit=limit,
        )
        return _register_result_resources(result)

    async def read_course_resource(
        self,
        resource_token: str,
        max_bytes: int,
        max_chars: int,
        max_pdf_pages: int,
    ) -> dict[str, Any]:
        if not isinstance(resource_token, str) or not resource_token:
            raise ValueError("resource_token es obligatorio")
        now = time.monotonic()
        cached = _RESOURCE_REFERENCES.get(resource_token)
        if not cached or cached[0] < now:
            _RESOURCE_REFERENCES.pop(resource_token, None)
            raise ValueError(
                "El recurso no procede de una lista reciente o su referencia ha caducado."
            )
        reference = cached[1]
        if not 1 <= max_bytes <= 50 * 1024 * 1024:
            raise ValueError("max_bytes debe estar entre 1 y 52428800")
        gateway = self._campus()
        content, media_type, source_url = await gateway.fetch_file(str(reference["url"]), max_bytes)
        extracted = extract_resource_text(
            content,
            media_type=media_type or str(reference["media_type"]),
            filename=str(reference["filename"]),
            max_chars=max_chars,
            max_pdf_pages=max_pdf_pages,
        )
        return {
            "file_name": reference["filename"],
            "media_type": media_type or reference["media_type"],
            "downloaded_bytes": len(content),
            "source_url": source_url,
            **extracted,
        }

    async def list_assignments(self, course_ids: list[int] | None) -> dict[str, Any]:
        gateway = self._campus()
        if _session_form_client(gateway) is not None:
            return await _session_assignment_listing(gateway, course_ids)
        await _require_functions(gateway, "mod_assign_get_assignments")
        return await moodle_list_assignments(gateway.invoke, course_ids)

    async def get_submission_status(
        self, assignment_id: int | None, course_module_id: int | None = None
    ) -> dict[str, Any]:
        gateway = self._campus()
        session_factory = getattr(gateway, "session_forms", None)
        session_forms = session_factory() if session_factory else None
        if session_forms is not None and assignment_id is not None:
            raise ValueError(
                "En modo sesión usa assignment_id=null y el course_module_id mostrado por Moodle."
            )
        if assignment_id is None:
            if course_module_id is None:
                raise ValueError("Se requiere assignment_id o course_module_id")
            forms = gateway.session_forms()
            if forms is None:
                raise CampusProtocolError("El token REST requiere assignment_id.")
            raise CampusCapabilityUnavailable(
                "La pagina de la tarea puede registrar vista/completion. Usa primero "
                "preview_inspect_submission_status y luego inspect_submission_status."
            )
        try:
            return await moodle_get_submission_status(gateway.invoke, assignment_id)
        except CampusCapabilityUnavailable as exc:
            if course_module_id is None:
                raise CampusProtocolError(
                    "Esta sesión necesita course_module_id para inspeccionar la entrega por HTTP."
                ) from exc
            inspection = await _session_forms_or_raise(gateway, exc).inspect_assignment(
                course_module_id
            )
            return {
                "assignment_id": assignment_id,
                "course_module_id": course_module_id,
                "transport": "moodle_http_form",
                "editable": bool(inspection.get("save_supported")),
                "inspection": inspection,
                "content_is_untrusted": True,
            }

    async def inspect_submission_status(
        self,
        course_module_id: int,
        confirmation_token: str | None,
    ) -> dict[str, Any]:
        gateway = self._campus()
        forms = _session_form_client(gateway)
        if forms is None:
            raise CampusCapabilityUnavailable(
                "Esta inspeccion confirmada solo se usa con una sesion HTTP; con REST usa "
                "get_submission_status."
            )
        payload = {"course_module_id": course_module_id}
        if confirmation_token is None:
            confirmation = await _issue_action_confirmation(
                gateway, "assignment.inspect_status_stateful", payload
            )
            return {
                "preview": True,
                "course_module_id": course_module_id,
                "stateful_read": True,
                "warning": (
                    "La vista previa no abre la tarea. La inspeccion confirmada abrira una vez "
                    "la pagina de edicion y Moodle puede registrar vista o completion."
                ),
                **confirmation,
            }
        await _consume_action_confirmation(
            gateway,
            confirmation_token,
            "assignment.inspect_status_stateful",
            payload,
        )
        inspection_result = await _session_form_mutation(
            "assignment.inspect_status_stateful",
            forms.inspect_assignment(course_module_id),
        )
        if inspection_result.get("outcome") == "unknown":
            return inspection_result
        inspection = inspection_result
        return {
            "assignment_id": None,
            "course_module_id": course_module_id,
            "transport": "moodle_http_form",
            "editable": bool(inspection.get("save_supported")),
            "inspection": inspection,
            "stateful_inspection_confirmed": True,
            "content_is_untrusted": True,
        }

    async def preview_save_online_submission(
        self,
        assignment_id: int | None,
        online_text: str,
        course_module_id: int | None = None,
    ) -> dict[str, Any]:
        gateway = self._campus()
        session_factory = getattr(gateway, "session_forms", None)
        session_forms = session_factory() if session_factory else None
        if session_forms is not None and assignment_id is not None:
            raise ValueError(
                "En modo sesión usa assignment_id=null y el course_module_id mostrado por Moodle."
            )
        if session_forms is not None:
            if course_module_id is None:
                raise ValueError("Se requiere assignment_id o course_module_id")
            status = {
                "editable": True,
                "transport": "moodle_http_form",
                "unchecked_until_execution": True,
            }
        else:
            if assignment_id is None:
                raise ValueError("El token REST requiere assignment_id")
            status = await moodle_get_submission_status(gateway.invoke, assignment_id)
        if not status["editable"]:
            return {
                "allowed": False,
                "requires_confirmation": False,
                "assignment_id": assignment_id,
                "status": status,
                "warning": "Moodle indica que esta entrega no es editable.",
            }
        plugin_safety = submission_plugin_safety(status, {"onlinetext"})
        if not plugin_safety["safe"] and status.get("transport") != "moodle_http_form":
            return {
                "allowed": False,
                "requires_confirmation": False,
                "assignment_id": assignment_id,
                "status": status,
                **plugin_safety,
            }
        if status.get("transport") != "moodle_http_form":
            await _require_functions(gateway, "mod_assign_save_submission")
        payload = {
            "assignment_id": assignment_id,
            "online_text": online_text,
            "course_module_id": course_module_id,
        }
        confirmation = await _issue_action_confirmation(gateway, "assignment.save_text", payload)
        return {
            "allowed": True,
            "saved": False,
            "assignment_id": assignment_id,
            "course_module_id": course_module_id,
            "online_text": online_text,
            "text_bytes": len(online_text.encode("utf-8")),
            "status": status,
            "warning": (
                "Esta operación reemplazará el texto online actual por el texto exacto mostrado."
            ),
            **confirmation,
        }

    async def save_online_submission(
        self,
        assignment_id: int | None,
        online_text: str,
        confirmation_token: str,
        course_module_id: int | None = None,
    ) -> dict[str, Any]:
        payload = {
            "assignment_id": assignment_id,
            "online_text": online_text,
            "course_module_id": course_module_id,
        }
        gateway = self._campus()
        session_factory = getattr(gateway, "session_forms", None)
        session_forms = session_factory() if session_factory else None
        if session_forms is not None and assignment_id is not None:
            raise ValueError(
                "En modo sesión usa assignment_id=null y el course_module_id mostrado por Moodle."
            )
        await _consume_action_confirmation(
            gateway,
            confirmation_token,
            "assignment.save_text",
            payload,
        )
        form_factory = getattr(gateway, "session_forms", None)
        form_client = form_factory() if form_factory else None
        if form_client is not None and course_module_id is not None:
            return await _session_form_mutation(
                "assignment.save_text",
                form_client.save_assignment(
                    course_module_id,
                    {"onlinetext_editor[text]": online_text},
                    confirmed=True,
                ),
            )
        if assignment_id is None:
            if course_module_id is None:
                raise ValueError("Se requiere course_module_id para guardar por formulario HTTP")
            forms = gateway.session_forms()
            if forms is None:
                raise CampusProtocolError("El token REST requiere assignment_id.")
            return await forms.save_assignment(
                course_module_id,
                {"onlinetext_editor[text]": online_text},
                confirmed=True,
            )
        try:
            return await moodle_save_submission(
                gateway.invoke,
                assignment_id,
                online_text=online_text,
                client_request_id=f"mcp-{secrets.token_hex(12)}",
            )
        except CampusCapabilityUnavailable as exc:
            if course_module_id is None:
                raise
            return await _session_forms_or_raise(gateway, exc).save_assignment(
                course_module_id,
                {"onlinetext_editor[text]": online_text},
                confirmed=True,
            )

    async def preview_replace_submission_files(
        self,
        assignment_id: int | None,
        file_paths: list[str],
        course_module_id: int | None = None,
    ) -> dict[str, Any]:
        inspected = inspect_upload_files(self.settings, file_paths)
        public_files = _public_uploads(inspected)
        gateway = self._campus()
        session_forms = _session_form_client(gateway)
        if session_forms is not None:
            if assignment_id is not None:
                raise ValueError(
                    "En modo sesion usa assignment_id=null y course_module_id."
                )
            if course_module_id is None:
                raise ValueError("Se requiere course_module_id en modo sesion")
            payload = {
                "assignment_id": None,
                "course_module_id": course_module_id,
                "files": public_files,
            }
            confirmation = await _issue_action_confirmation(
                gateway, "assignment.replace_files", payload
            )
            return {
                "allowed": True,
                "replaced": False,
                "assignment_id": None,
                "course_module_id": course_module_id,
                "files": public_files,
                "total_bytes": sum(item["size"] for item in public_files),
                "status": {
                    "transport": "moodle_http_form",
                    "unchecked_until_execution": True,
                },
                "warning": (
                    "Tras confirmar, Moodle abrira un borrador temporal, eliminara sus archivos, "
                    "subira los confirmados y guardara la entrega. Un fallo intermedio puede "
                    "dejar el borrador parcial; nunca se reintenta automaticamente."
                ),
                **confirmation,
            }
        if assignment_id is None:
            raise ValueError("El token REST requiere assignment_id")
        try:
            status = await moodle_get_submission_status(gateway.invoke, assignment_id)
        except CampusCapabilityUnavailable as exc:
            if course_module_id is None:
                raise
            inspection = await _session_forms_or_raise(gateway, exc).inspect_assignment(
                course_module_id
            )
            return {
                "allowed": False,
                "requires_confirmation": False,
                "assignment_id": assignment_id,
                "files": [
                    {
                        "relative_path": item["relative_path"],
                        "filename": item["filename"],
                        "size": item["size"],
                        "sha256": item["sha256"],
                    }
                    for item in inspected
                ],
                "inspection": inspection,
                "warning": (
                    "El formulario usa el gestor JavaScript de borradores de Moodle. Para "
                    "subir archivos configura un token REST autorizado."
                ),
            }
        if not status["editable"]:
            return {
                "allowed": False,
                "requires_confirmation": False,
                "assignment_id": assignment_id,
                "files": public_files,
                "status": status,
                "warning": "Moodle indica que esta entrega no es editable.",
            }
        plugin_safety = submission_plugin_safety(status, {"file"})
        if not plugin_safety["safe"]:
            return {
                "allowed": False,
                "requires_confirmation": False,
                "assignment_id": assignment_id,
                "files": public_files,
                "status": status,
                **plugin_safety,
            }
        await _require_functions(
            gateway,
            "core_files_get_unused_draft_itemid",
            "core_files_upload",
            "mod_assign_save_submission",
        )
        payload = {"assignment_id": assignment_id, "files": public_files}
        confirmation = await _issue_action_confirmation(
            gateway, "assignment.replace_files", payload
        )
        return {
            "allowed": True,
            "replaced": False,
            "assignment_id": assignment_id,
            "files": public_files,
            "total_bytes": sum(item["size"] for item in public_files),
            "status": status,
            "warning": (
                "Esta operación reemplazará el conjunto completo de archivos de la entrega."
            ),
            **confirmation,
        }

    async def replace_submission_files(
        self,
        assignment_id: int | None,
        file_paths: list[str],
        confirmation_token: str,
        course_module_id: int | None = None,
    ) -> dict[str, Any]:
        inspected = inspect_upload_files(self.settings, file_paths)
        public_files = _public_uploads(inspected)
        gateway = self._campus()
        form_client = _session_form_client(gateway)
        if form_client is not None:
            if assignment_id is not None:
                raise ValueError("En modo sesion usa assignment_id=null")
            if course_module_id is None:
                raise ValueError("Se requiere course_module_id en modo sesion")
            payload = {
                "assignment_id": None,
                "course_module_id": course_module_id,
                "files": public_files,
            }
        else:
            if assignment_id is None:
                raise ValueError("El token REST requiere assignment_id")
            payload = {"assignment_id": assignment_id, "files": public_files}
        await _consume_action_confirmation(
            gateway,
            confirmation_token,
            "assignment.replace_files",
            payload,
        )
        if form_client is not None:
            uploads = _capture_confirmed_uploads(inspected, self.settings.max_upload_bytes)
            return await _session_form_mutation(
                "assignment.replace_files",
                form_client.replace_assignment_files(
                    course_module_id,
                    uploads,
                    confirmed=True,
                ),
            )
        if self.settings.upload_root is None:
            raise ValueError("USC_UPLOAD_ROOT no está configurado")
        return await moodle_replace_submission_files(
            gateway.invoke,
            assignment_id,
            [item["path"] for item in inspected],
            allowed_root=self.settings.upload_root,
            max_file_bytes=self.settings.max_upload_bytes,
            max_total_bytes=self.settings.max_upload_bytes,
            client_request_id=f"mcp-{secrets.token_hex(12)}",
        )

    async def preview_delete_submission_files(
        self, assignment_id: int | None, course_module_id: int | None = None
    ) -> dict[str, Any]:
        gateway = self._campus()
        session_forms = _session_form_client(gateway)
        if session_forms is not None:
            if assignment_id is not None:
                raise ValueError("En modo sesion usa assignment_id=null")
            if course_module_id is None:
                raise ValueError("Se requiere course_module_id en modo sesion")
            payload = {"assignment_id": None, "course_module_id": course_module_id}
            confirmation = await _issue_action_confirmation(
                gateway, "assignment.delete_files", payload
            )
            return {
                "allowed": True,
                "deleted": False,
                "assignment_id": None,
                "course_module_id": course_module_id,
                "status": {
                    "transport": "moodle_http_form",
                    "unchecked_until_execution": True,
                },
                "warning": (
                    "Tras confirmar se eliminaran todos los archivos del borrador y se guardara "
                    "la entrega conservando otros campos. Un fallo puede dejar el borrador parcial."
                ),
                **confirmation,
            }
        if assignment_id is None:
            raise ValueError("El token REST requiere assignment_id")
        try:
            status = await moodle_get_submission_status(gateway.invoke, assignment_id)
        except CampusCapabilityUnavailable as exc:
            if course_module_id is None:
                raise
            inspection = await _session_forms_or_raise(gateway, exc).inspect_assignment(
                course_module_id
            )
            return {
                "allowed": False,
                "requires_confirmation": False,
                "assignment_id": assignment_id,
                "inspection": inspection,
                "warning": (
                    "Eliminar solo los archivos requiere la API REST; el formulario HTTP no "
                    "permite manipular con seguridad el filemanager JavaScript."
                ),
            }
        if not status["editable"]:
            return {
                "allowed": False,
                "requires_confirmation": False,
                "assignment_id": assignment_id,
                "status": status,
                "warning": "Moodle indica que los archivos no son editables.",
            }
        plugin_safety = submission_plugin_safety(status, {"file"})
        if not plugin_safety["safe"]:
            return {
                "allowed": False,
                "requires_confirmation": False,
                "assignment_id": assignment_id,
                "status": status,
                **plugin_safety,
            }
        await _require_functions(
            gateway,
            "core_files_get_unused_draft_itemid",
            "mod_assign_save_submission",
        )
        payload = {"assignment_id": assignment_id}
        confirmation = await _issue_action_confirmation(gateway, "assignment.delete_files", payload)
        return {
            "allowed": True,
            "deleted": False,
            "assignment_id": assignment_id,
            "status": status,
            "warning": "Se eliminarán todos los archivos, pero no el texto online.",
            **confirmation,
        }

    async def delete_submission_files(
        self,
        assignment_id: int | None,
        confirmation_token: str,
        course_module_id: int | None = None,
    ) -> dict[str, Any]:
        gateway = self._campus()
        form_client = _session_form_client(gateway)
        if form_client is not None:
            if assignment_id is not None:
                raise ValueError("En modo sesion usa assignment_id=null")
            if course_module_id is None:
                raise ValueError("Se requiere course_module_id en modo sesion")
            payload = {"assignment_id": None, "course_module_id": course_module_id}
        else:
            if assignment_id is None:
                raise ValueError("El token REST requiere assignment_id")
            payload = {"assignment_id": assignment_id}
        await _consume_action_confirmation(
            gateway,
            confirmation_token,
            "assignment.delete_files",
            payload,
        )
        if form_client is not None:
            return await _session_form_mutation(
                "assignment.delete_files",
                form_client.delete_assignment_files(
                    course_module_id,
                    confirmed=True,
                ),
            )
        return await moodle_delete_submission_files(
            gateway.invoke,
            assignment_id,
            client_request_id=f"mcp-{secrets.token_hex(12)}",
        )

    async def preview_submit_assignment(
        self,
        assignment_id: int | None,
        accept_submission_statement: bool,
        course_module_id: int | None = None,
    ) -> dict[str, Any]:
        gateway = self._campus()
        session_factory = getattr(gateway, "session_forms", None)
        session_forms = session_factory() if session_factory else None
        if session_forms is not None and assignment_id is not None:
            raise ValueError(
                "En modo sesión usa assignment_id=null y el course_module_id mostrado por Moodle."
            )
        if session_forms is not None:
            if course_module_id is None:
                raise ValueError("Se requiere assignment_id o course_module_id")
            status = {
                "can_submit": True,
                "transport": "moodle_http_form",
                "unchecked_until_execution": True,
            }
        else:
            if assignment_id is None:
                raise ValueError("El token REST requiere assignment_id")
            status = await moodle_get_submission_status(gateway.invoke, assignment_id)
        if not status["can_submit"]:
            return {
                "allowed": False,
                "requires_confirmation": False,
                "assignment_id": assignment_id,
                "status": status,
                "warning": "Moodle no permite enviar ahora esta entrega para calificación.",
            }
        if status.get("transport") != "moodle_http_form":
            await _require_functions(gateway, "mod_assign_submit_for_grading")
        payload = {
            "assignment_id": assignment_id,
            "accept_submission_statement": accept_submission_statement,
            "course_module_id": course_module_id,
        }
        confirmation = await _issue_action_confirmation(gateway, "assignment.submit", payload)
        return {
            "allowed": True,
            "submitted": False,
            "assignment_id": assignment_id,
            "course_module_id": course_module_id,
            "accept_submission_statement": accept_submission_statement,
            "status": status,
            "warning": (
                "Enviar para calificación puede bloquear futuras modificaciones. Confirma la "
                "declaración y revisa antes todos los archivos y el texto."
            ),
            **confirmation,
        }

    async def submit_assignment(
        self,
        assignment_id: int | None,
        accept_submission_statement: bool,
        confirmation_token: str,
        course_module_id: int | None = None,
    ) -> dict[str, Any]:
        payload = {
            "assignment_id": assignment_id,
            "accept_submission_statement": accept_submission_statement,
            "course_module_id": course_module_id,
        }
        gateway = self._campus()
        session_factory = getattr(gateway, "session_forms", None)
        session_forms = session_factory() if session_factory else None
        if session_forms is not None and assignment_id is not None:
            raise ValueError(
                "En modo sesión usa assignment_id=null y el course_module_id mostrado por Moodle."
            )
        await _consume_action_confirmation(
            gateway,
            confirmation_token,
            "assignment.submit",
            payload,
        )
        form_factory = getattr(gateway, "session_forms", None)
        form_client = form_factory() if form_factory else None
        if form_client is not None and course_module_id is not None:
            return await _session_form_mutation(
                "assignment.submit",
                form_client.submit_assignment(
                    course_module_id,
                    {"submissionstatement": accept_submission_statement},
                    confirmed=True,
                ),
            )
        if assignment_id is None:
            if course_module_id is None:
                raise ValueError("Se requiere course_module_id para enviar por formulario HTTP")
            forms = gateway.session_forms()
            if forms is None:
                raise CampusProtocolError("El token REST requiere assignment_id.")
            return await forms.submit_assignment(
                course_module_id,
                {"submissionstatement": accept_submission_statement},
                confirmed=True,
            )
        try:
            return await moodle_submit_for_grading(
                gateway.invoke,
                assignment_id,
                accept_submission_statement=accept_submission_statement,
                client_request_id=f"mcp-{secrets.token_hex(12)}",
            )
        except CampusCapabilityUnavailable as exc:
            if course_module_id is None:
                raise
            return await _session_forms_or_raise(gateway, exc).submit_assignment(
                course_module_id,
                {"submissionstatement": accept_submission_statement},
                confirmed=True,
            )

    async def preview_remove_submission(
        self, assignment_id: int | None, course_module_id: int | None = None
    ) -> dict[str, Any]:
        gateway = self._campus()
        session_factory = getattr(gateway, "session_forms", None)
        session_forms = session_factory() if session_factory else None
        if session_forms is not None and assignment_id is not None:
            raise ValueError(
                "En modo sesión usa assignment_id=null y el course_module_id mostrado por Moodle."
            )
        if session_forms is not None:
            if course_module_id is None:
                raise ValueError("Se requiere assignment_id o course_module_id")
            status = {
                "editable": True,
                "transport": "moodle_http_form",
                "unchecked_until_execution": True,
            }
        else:
            if assignment_id is None:
                raise ValueError("El token REST requiere assignment_id")
            status = await moodle_get_submission_status(gateway.invoke, assignment_id)
        if not status["editable"]:
            return {
                "allowed": False,
                "requires_confirmation": False,
                "assignment_id": assignment_id,
                "status": status,
                "warning": "Moodle indica que la entrega no se puede eliminar.",
            }
        if status.get("transport") != "moodle_http_form":
            await _require_functions(gateway, "mod_assign_remove_submission")
        payload = {
            "assignment_id": assignment_id,
            "course_module_id": course_module_id,
        }
        confirmation = await _issue_action_confirmation(gateway, "assignment.remove", payload)
        return {
            "allowed": True,
            "removed": False,
            "assignment_id": assignment_id,
            "course_module_id": course_module_id,
            "status": status,
            "warning": (
                "Se intentará eliminar toda la entrega. Es destructivo y depende de Moodle 4.5+."
            ),
            **confirmation,
        }

    async def remove_submission(
        self,
        assignment_id: int | None,
        confirmation_token: str,
        course_module_id: int | None = None,
    ) -> dict[str, Any]:
        payload = {
            "assignment_id": assignment_id,
            "course_module_id": course_module_id,
        }
        gateway = self._campus()
        session_factory = getattr(gateway, "session_forms", None)
        session_forms = session_factory() if session_factory else None
        if session_forms is not None and assignment_id is not None:
            raise ValueError(
                "En modo sesión usa assignment_id=null y el course_module_id mostrado por Moodle."
            )
        user_id = await _consume_action_confirmation(
            gateway, confirmation_token, "assignment.remove", payload
        )
        form_factory = getattr(gateway, "session_forms", None)
        form_client = form_factory() if form_factory else None
        if form_client is not None and course_module_id is not None:
            return await _session_form_mutation(
                "assignment.remove",
                form_client.delete_assignment(course_module_id, confirmed=True),
            )
        if assignment_id is None:
            if course_module_id is None:
                raise ValueError("Se requiere course_module_id para eliminar por formulario HTTP")
            forms = gateway.session_forms()
            if forms is None:
                raise CampusProtocolError("El token REST requiere assignment_id.")
            return await forms.delete_assignment(course_module_id, confirmed=True)
        try:
            return await moodle_remove_entire_submission(
                gateway.invoke,
                assignment_id,
                user_id,
                client_request_id=f"mcp-{secrets.token_hex(12)}",
            )
        except CampusCapabilityUnavailable as exc:
            if course_module_id is None:
                raise
            return await _session_forms_or_raise(gateway, exc).delete_assignment(
                course_module_id,
                confirmed=True,
            )

    async def check_submission_reopen(
        self, assignment_id: int | None, course_module_id: int | None = None
    ) -> dict[str, Any]:
        gateway = self._campus()
        if _session_form_client(gateway) is not None:
            if assignment_id is not None:
                raise ValueError("En modo sesion usa assignment_id=null")
            if course_module_id is None:
                raise ValueError("Se requiere course_module_id en modo sesion")
            return {
                "ok": False,
                "supported": False,
                "requires_stateful_inspection": True,
                "mutated": False,
                "assignment_id": None,
                "course_module_id": course_module_id,
                "transport": "moodle_http_form",
                "warning": (
                    "Usa preview_inspect_submission_status e inspect_submission_status para "
                    "comprobar si Moodle expone de nuevo la edicion."
                ),
            }
        if assignment_id is None:
            if course_module_id is None:
                raise ValueError("Se requiere assignment_id o course_module_id")
            forms = gateway.session_forms()
            if forms is None:
                raise CampusProtocolError("El token REST requiere assignment_id.")
            return {
                "ok": False,
                "supported": False,
                "requires_stateful_inspection": True,
                "mutated": False,
                "assignment_id": None,
                "course_module_id": course_module_id,
                "transport": "moodle_http_form",
                "warning": (
                    "Comprobar reapertura abriria la tarea. Usa la inspeccion stateful confirmada "
                    "para ver si el formulario de edicion esta disponible."
                ),
            }
        try:
            return await moodle_reopen_submission(
                gateway.invoke,
                assignment_id,
                client_request_id=f"mcp-{secrets.token_hex(12)}",
            )
        except CampusCapabilityUnavailable as exc:
            if course_module_id is None:
                raise
            inspection = await _session_forms_or_raise(gateway, exc).inspect_assignment(
                course_module_id
            )
            return {
                "ok": bool(inspection.get("save_supported")),
                "already_editable": bool(inspection.get("save_supported")),
                "mutated": False,
                "assignment_id": assignment_id,
                "course_module_id": course_module_id,
                "transport": "moodle_http_form",
                "inspection": inspection,
            }

    async def send_message(
        self, recipient_user_id: int, text: str, *, confirmation_token: str | None
    ) -> dict[str, Any]:
        clean_text = text.strip()
        if recipient_user_id <= 0:
            raise ValueError("recipient_user_id debe ser positivo")
        if not clean_text:
            raise ValueError("El mensaje no puede estar vacío")
        if len(clean_text.encode("utf-8")) > 4_096:
            raise ValueError("El mensaje no puede superar 4096 bytes en UTF-8")
        now = time.monotonic()
        contact = _MESSAGE_CONTACTS.get(recipient_user_id)
        if not contact or contact[0] < now:
            _MESSAGE_CONTACTS.pop(recipient_user_id, None)
            raise ValueError(
                "El destinatario no procede de una búsqueda reciente. "
                "Usa search_message_contacts y verifica su nombre antes de previsualizar."
            )
        gateway = self._campus()
        authenticated_user_id = await _gateway_user_id(gateway)
        contact_user_id, recipient_full_name = contact[1], contact[2]
        if contact_user_id != authenticated_user_id:
            _MESSAGE_CONTACTS.pop(recipient_user_id, None)
            raise ValueError(
                "La cuenta de Moodle cambió desde la búsqueda del contacto. "
                "Repite search_message_contacts con la cuenta actual."
            )
        expired = [token for token, entry in _MESSAGE_CONFIRMATIONS.items() if entry[0] < now]
        for token in expired:
            _MESSAGE_CONFIRMATIONS.pop(token, None)
        digest = _message_digest(recipient_user_id, clean_text)
        if not confirmation_token:
            await _require_functions(gateway, "core_message_send_instant_messages")
            confirmation_token = secrets.token_urlsafe(12)
            _MESSAGE_CONFIRMATIONS[confirmation_token] = (
                now + _MESSAGE_CONFIRMATION_TTL,
                authenticated_user_id,
                recipient_user_id,
                digest,
            )
            return {
                "sent": False,
                "requires_confirmation": True,
                "recipient_user_id": recipient_user_id,
                "recipient_full_name": recipient_full_name,
                "authenticated_user_id": authenticated_user_id,
                "text": clean_text,
                "confirmation_token": confirmation_token,
                "expires_in_seconds": _MESSAGE_CONFIRMATION_TTL,
                "warning": (
                    "Vista previa: pide al usuario que confirme explícitamente el destinatario "
                    "y este texto exacto. Después vuelve a llamar con el token; Moodle podría "
                    "generar notificaciones externas según la configuración del destinatario."
                ),
            }
        expected = _MESSAGE_CONFIRMATIONS.pop(confirmation_token, None)
        if expected:
            expires_at, expected_user, expected_recipient, expected_digest = expected
        else:
            expires_at, expected_user, expected_recipient, expected_digest = 0, 0, 0, ""
        if (
            expires_at < now
            or expected_user != authenticated_user_id
            or expected_recipient != recipient_user_id
            or expected_digest != digest
        ):
            raise ValueError(
                "Token de confirmación inválido, caducado o no ligado a este destinatario/texto. "
                "Solicita una nueva vista previa."
            )
        result = await gateway.send_message(recipient_user_id, clean_text)
        return {
            "sent": True,
            "recipient_user_id": recipient_user_id,
            "recipient_full_name": recipient_full_name,
            "message_id": (
                result.get("msgid") or result.get("messageid") or result.get("message_id")
            ),
            "server_error": html_to_text(result.get("errormessage")),
        }

    def _quizzes(self) -> MoodleQuizClient:
        gateway = self._campus()
        return MoodleQuizClient(gateway.invoke)

    async def list_quizzes(self, course_ids: list[int] | None) -> dict[str, Any]:
        return await self._quizzes().list_quizzes(course_ids)

    async def list_quiz_attempts(
        self,
        quiz_id: int | None,
        status: str,
        include_previews: bool,
        course_module_id: int | None = None,
    ) -> dict[str, Any]:
        if quiz_id is None:
            if course_module_id is None:
                raise ValueError("Se requiere quiz_id o course_module_id")
            gateway = self._campus()
            result = await MoodleQuizClient(gateway.invoke_course_module).list_attempts(
                course_module_id,
                status=status,
                include_previews=include_previews,
            )
            for attempt in result.get("attempts", []):
                if isinstance(attempt, dict):
                    attempt["quiz_id"] = None
                    attempt["course_module_id"] = course_module_id
            result["quiz_id"] = None
            result["course_module_id"] = course_module_id
            return result
        return await self._quizzes().list_attempts(
            quiz_id,
            status=status,
            include_previews=include_previews,
        )

    async def get_quiz_attempt_page(
        self,
        attempt_id: int,
        page: int,
        preflight_data: dict[str, Any] | None,
    ) -> dict[str, Any]:
        del attempt_id, page, preflight_data
        raise CampusCapabilityUnavailable(
            "Consultar una página de intento puede procesar automáticamente un timeout y cambiar "
            "su estado. Usa preview_inspect_quiz_attempt y después inspect_quiz_attempt."
        )

    async def _inspect_quiz_attempt_page(
        self,
        gateway: Any,
        attempt_id: int,
        page: int,
        preflight_data: dict[str, Any] | None,
    ) -> dict[str, Any]:
        try:
            return await MoodleQuizClient(gateway.invoke).get_attempt_page(
                attempt_id,
                page,
                preflight_data=preflight_data,
            )
        except CampusCapabilityUnavailable as exc:
            return await _session_forms_or_raise(gateway, exc).inspect_quiz_page(
                attempt_id,
                page,
            )

    async def get_quiz_attempt_summary(
        self, attempt_id: int, preflight_data: dict[str, Any] | None
    ) -> dict[str, Any]:
        del attempt_id, preflight_data
        raise CampusCapabilityUnavailable(
            "Consultar el resumen puede procesar automáticamente un timeout y cambiar el intento. "
            "Usa preview_inspect_quiz_attempt con summary=true y después inspect_quiz_attempt."
        )

    async def _inspect_quiz_attempt_summary(
        self,
        gateway: Any,
        attempt_id: int,
        preflight_data: dict[str, Any] | None,
    ) -> dict[str, Any]:
        try:
            return await MoodleQuizClient(gateway.invoke).get_attempt_summary(
                attempt_id,
                preflight_data=preflight_data,
            )
        except CampusCapabilityUnavailable as exc:
            return await _session_forms_or_raise(gateway, exc).inspect_quiz_finish(attempt_id)

    async def inspect_quiz_attempt(
        self,
        attempt_id: int,
        page: int,
        summary: bool,
        preflight_data: dict[str, Any] | None,
        confirmation_token: str | None,
    ) -> dict[str, Any]:
        if attempt_id <= 0 or page < 0:
            raise ValueError("attempt_id/page no son válidos")
        payload = {
            "attempt_id": attempt_id,
            "page": page,
            "summary": summary,
            "preflight_data": dict(preflight_data or {}),
        }
        gateway = self._campus()
        if confirmation_token is None:
            function = "mod_quiz_get_attempt_summary" if summary else "mod_quiz_get_attempt_data"
            form_factory = getattr(gateway, "session_forms", None)
            if not (form_factory and form_factory() is not None):
                await _require_functions(gateway, function)
            confirmation = await _issue_action_confirmation(
                gateway,
                "quiz.inspect_stateful",
                payload,
            )
            return {
                "preview": True,
                **payload,
                "warning": (
                    "Moodle puede procesar el vencimiento del tiempo al abrir esta vista y pasar "
                    "el intento a overdue, finished o abandoned. Esta previsualización no abre "
                    "el intento; la segunda llamada sí puede cambiar su estado."
                ),
                **confirmation,
            }
        await _consume_action_confirmation(
            gateway,
            confirmation_token,
            "quiz.inspect_stateful",
            payload,
        )
        try:
            if summary:
                result = await self._inspect_quiz_attempt_summary(
                    gateway, attempt_id, preflight_data
                )
            else:
                result = await self._inspect_quiz_attempt_page(
                    gateway,
                    attempt_id,
                    page,
                    preflight_data,
                )
        except (AuthenticationRequired, CampusCapabilityUnavailable):
            raise
        except CampusError:
            return {
                "request_may_have_been_sent": True,
                "outcome": "unknown",
                "do_not_retry": True,
                "attempt_id": attempt_id,
                "warning": (
                    "No se pudo comprobar si Moodle procesó el timeout. Revisa el intento "
                    "manualmente antes de repetir cualquier acción."
                ),
            }
        result["stateful_inspection_confirmed"] = True
        return result

    async def preview_start_quiz(
        self,
        quiz_id: int | None,
        preflight_data: dict[str, Any] | None,
        force_new: bool,
        course_module_id: int | None = None,
    ) -> dict[str, Any]:
        gateway = self._campus()
        form_factory = getattr(gateway, "session_forms", None)
        form_client = form_factory() if form_factory else None
        if form_client is not None:
            if quiz_id is not None:
                raise ValueError(
                    "En modo sesión usa quiz_id=null y el course_module_id mostrado por Moodle."
                )
            if course_module_id is None:
                raise ValueError(
                    "La sesión HTTP necesita course_module_id para localizar el cuestionario."
                )
        else:
            if quiz_id is None:
                raise ValueError("El token REST requiere quiz_id")
            await _require_functions(gateway, "mod_quiz_start_attempt")
        payload = {
            "quiz_id": quiz_id,
            "preflight_data": dict(preflight_data or {}),
            "force_new": force_new,
            "course_module_id": course_module_id,
        }
        confirmation = await _issue_action_confirmation(gateway, "quiz.start", payload)
        return {
            "started": False,
            "quiz_id": quiz_id,
            "course_module_id": course_module_id,
            "force_new": force_new,
            "preflight_fields": sorted(payload["preflight_data"]),
            "form_inspection": None,
            "warning": (
                "Iniciar un intento puede activar inmediatamente su temporizador. En modo "
                "sesión, la vista previa tampoco abre la página del cuestionario: esa apertura "
                "confirmada puede registrar una vista o procesar un intento caducado. Confirma "
                "en un mensaje nuevo que deseas continuar."
            ),
            "security_note": QUIZ_SECURITY_NOTE,
            **confirmation,
        }

    async def start_quiz(
        self,
        quiz_id: int | None,
        preflight_data: dict[str, Any] | None,
        force_new: bool,
        confirmation_token: str,
        course_module_id: int | None = None,
    ) -> dict[str, Any]:
        payload = {
            "quiz_id": quiz_id,
            "preflight_data": dict(preflight_data or {}),
            "force_new": force_new,
            "course_module_id": course_module_id,
        }
        gateway = self._campus()
        form_factory = getattr(gateway, "session_forms", None)
        form_client = form_factory() if form_factory else None
        if form_client is not None:
            if quiz_id is not None:
                raise ValueError(
                    "En modo sesión usa quiz_id=null y el course_module_id mostrado por Moodle."
                )
            if course_module_id is None:
                raise ValueError("Se requiere course_module_id para iniciar por formulario HTTP")
        elif quiz_id is None:
            raise ValueError("El token REST requiere quiz_id")
        await _consume_action_confirmation(gateway, confirmation_token, "quiz.start", payload)
        if form_client is not None:
            return await _session_form_mutation(
                "quiz.start",
                form_client.start_quiz(
                    course_module_id,
                    preflight_data,
                    confirmed=True,
                ),
            )
        try:
            return await MoodleQuizClient(gateway.invoke).start_attempt(
                quiz_id,
                confirmed=True,
                preflight_data=preflight_data,
                force_new=force_new,
            )
        except CampusCapabilityUnavailable as exc:
            if course_module_id is None:
                raise
            return await _session_forms_or_raise(gateway, exc).start_quiz(
                course_module_id,
                preflight_data,
                confirmed=True,
            )

    async def preview_save_quiz_answers(
        self,
        attempt_id: int,
        responses: dict[str, Any],
        preflight_data: dict[str, Any] | None,
        page: int = 0,
    ) -> dict[str, Any]:
        gateway = self._campus()
        form_inspection = None
        form_factory = getattr(gateway, "session_forms", None)
        form_client = form_factory() if form_factory else None
        if form_client is None:
            await _require_functions(gateway, "mod_quiz_save_attempt")
        payload = {
            "attempt_id": attempt_id,
            "responses": dict(responses),
            "preflight_data": dict(preflight_data or {}),
            "page": page,
        }
        confirmation = await _issue_action_confirmation(gateway, "quiz.save", payload)
        return {
            "saved": False,
            "attempt_id": attempt_id,
            "responses": payload["responses"],
            "preflight_fields": sorted(payload["preflight_data"]),
            "form_inspection": form_inspection,
            "warning": (
                "Guardar modifica el intento. En modo sesión la vista previa no abre la página "
                "porque Moodle podría procesar un timeout; valida antes los campos mediante el "
                "flujo confirmado inspect_quiz_attempt."
            ),
            "security_note": QUIZ_SECURITY_NOTE,
            **confirmation,
        }

    async def save_quiz_answers(
        self,
        attempt_id: int,
        responses: dict[str, Any],
        preflight_data: dict[str, Any] | None,
        confirmation_token: str,
        page: int = 0,
    ) -> dict[str, Any]:
        payload = {
            "attempt_id": attempt_id,
            "responses": dict(responses),
            "preflight_data": dict(preflight_data or {}),
            "page": page,
        }
        gateway = self._campus()
        await _consume_action_confirmation(gateway, confirmation_token, "quiz.save", payload)
        form_factory = getattr(gateway, "session_forms", None)
        form_client = form_factory() if form_factory else None
        if form_client is not None:
            return await _session_form_mutation(
                "quiz.save",
                form_client.save_quiz_page(
                    attempt_id,
                    page,
                    responses,
                    confirmed=True,
                ),
            )
        try:
            return await MoodleQuizClient(gateway.invoke).save_answers(
                attempt_id,
                responses,
                confirmed=True,
                preflight_data=preflight_data,
            )
        except CampusCapabilityUnavailable as exc:
            return await _session_forms_or_raise(gateway, exc).save_quiz_page(
                attempt_id,
                page,
                responses,
                confirmed=True,
            )

    async def preview_finish_quiz(
        self,
        attempt_id: int,
        responses: dict[str, Any] | None,
        time_up: bool,
        preflight_data: dict[str, Any] | None,
    ) -> dict[str, Any]:
        gateway = self._campus()
        form_inspection = None
        form_factory = getattr(gateway, "session_forms", None)
        form_client = form_factory() if form_factory else None
        if form_client is None:
            await _require_functions(gateway, "mod_quiz_process_attempt")
        payload = {
            "attempt_id": attempt_id,
            "responses": dict(responses or {}),
            "time_up": time_up,
            "preflight_data": dict(preflight_data or {}),
        }
        confirmation = await _issue_action_confirmation(gateway, "quiz.finish", payload)
        return {
            "finished": False,
            "attempt_id": attempt_id,
            "responses": payload["responses"],
            "time_up": time_up,
            "preflight_fields": sorted(payload["preflight_data"]),
            "form_inspection": form_inspection,
            "warning": (
                "Finalizar suele ser irreversible. Confirma en un mensaje nuevo el intento y "
                "las respuestas exactas antes de enviarlo."
            ),
            "security_note": QUIZ_SECURITY_NOTE,
            **confirmation,
        }

    async def finish_quiz(
        self,
        attempt_id: int,
        responses: dict[str, Any] | None,
        time_up: bool,
        preflight_data: dict[str, Any] | None,
        confirmation_token: str,
    ) -> dict[str, Any]:
        payload = {
            "attempt_id": attempt_id,
            "responses": dict(responses or {}),
            "time_up": time_up,
            "preflight_data": dict(preflight_data or {}),
        }
        gateway = self._campus()
        await _consume_action_confirmation(gateway, confirmation_token, "quiz.finish", payload)
        form_factory = getattr(gateway, "session_forms", None)
        form_client = form_factory() if form_factory else None
        if form_client is not None:
            if responses or preflight_data or time_up:
                raise CampusProtocolError(
                    "El formulario HTTP no puede finalizar conservando responses, "
                    "preflight_data o time_up. No se finalizó el intento: guarda primero las "
                    "respuestas mediante su flujo confirmado."
                )
            return await _session_form_mutation(
                "quiz.finish",
                form_client.finish_quiz(attempt_id, confirmed=True),
            )
        try:
            return await MoodleQuizClient(gateway.invoke).finish_attempt(
                attempt_id,
                responses,
                confirmed=True,
                time_up=time_up,
                preflight_data=preflight_data,
            )
        except CampusCapabilityUnavailable as exc:
            if responses or preflight_data or time_up:
                raise CampusProtocolError(
                    "Moodle no expone la API para finalizar con respuestas, preflight_data "
                    "o time_up. No se finalizó el intento: guarda primero las respuestas "
                    "mediante su flujo confirmado y después finaliza sin esos parámetros."
                ) from exc
            return await _session_forms_or_raise(gateway, exc).finish_quiz(
                attempt_id,
                confirmed=True,
            )
