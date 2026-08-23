from __future__ import annotations

import hashlib
import secrets
import time
from datetime import datetime, timedelta
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
from .campus import CampusCapabilityUnavailable, CampusProtocolError, create_campus_gateway
from .collaboration import (
    list_conversation_messages as moodle_list_conversation_messages,
)
from .collaboration import (
    list_conversations as moodle_list_conversations,
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
from .confirmations import ACTION_CONFIRMATIONS
from .domain import MADRID, normalise_announcement, normalise_course, normalise_event
from .local_files import inspect_upload_files
from .public_web import search_exam_sources
from .quizzes import SECURITY_NOTE as QUIZ_SECURITY_NOTE
from .quizzes import MoodleQuizClient
from .resource_text import extract_resource_text
from .security import html_to_text
from .settings import Settings

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


class UscService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings.from_env()

    def _campus(self):
        return create_campus_gateway(self.settings)

    async def auth_status(self) -> dict[str, Any]:
        return await self._campus().status()

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
        gateway = self._campus()
        identity = await gateway.status()
        return await moodle_list_conversations(
            gateway.invoke,
            user_id=int(identity["user_id"]),
            offset=offset,
            limit=limit,
            conversation_type=conversation_type,
            favourites=favourites,
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
        return await moodle_list_discussion_posts(
            self._campus().invoke,
            discussion_id=discussion_id,
            offset=offset,
            limit=limit,
        )

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
        return await moodle_list_assignments(self._campus().invoke, course_ids)

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
            inspection = await forms.inspect_assignment(course_module_id)
            return {
                "assignment_id": None,
                "course_module_id": course_module_id,
                "transport": "moodle_http_form",
                "editable": bool(inspection.get("save_supported")),
                "inspection": inspection,
                "content_is_untrusted": True,
            }
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
        if assignment_id is None:
            if course_module_id is None:
                raise ValueError("Se requiere assignment_id o course_module_id")
            forms = gateway.session_forms()
            if forms is None:
                raise CampusProtocolError("El token REST requiere assignment_id.")
            inspection = await forms.inspect_assignment(course_module_id)
            status = {
                "editable": bool(inspection.get("save_supported")),
                "transport": "moodle_http_form",
                "inspection": inspection,
            }
        else:
            try:
                status = await moodle_get_submission_status(gateway.invoke, assignment_id)
            except CampusCapabilityUnavailable as exc:
                if course_module_id is None:
                    raise CampusProtocolError(
                        "Esta sesión necesita course_module_id para usar el formulario HTTP "
                        "alternativo."
                    ) from exc
                inspection = await _session_forms_or_raise(gateway, exc).inspect_assignment(
                    course_module_id
                )
                status = {
                    "editable": bool(inspection.get("save_supported")),
                    "transport": "moodle_http_form",
                    "inspection": inspection,
                }
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
            return await form_client.save_assignment(
                course_module_id,
                {"onlinetext_editor[text]": online_text},
                confirmed=True,
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
        assignment_id: int,
        file_paths: list[str],
        course_module_id: int | None = None,
    ) -> dict[str, Any]:
        inspected = inspect_upload_files(self.settings, file_paths)
        gateway = self._campus()
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
        public_files = [
            {
                "relative_path": item["relative_path"],
                "filename": item["filename"],
                "size": item["size"],
                "sha256": item["sha256"],
            }
            for item in inspected
        ]
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
        assignment_id: int,
        file_paths: list[str],
        confirmation_token: str,
    ) -> dict[str, Any]:
        inspected = inspect_upload_files(self.settings, file_paths)
        public_files = [
            {
                "relative_path": item["relative_path"],
                "filename": item["filename"],
                "size": item["size"],
                "sha256": item["sha256"],
            }
            for item in inspected
        ]
        payload = {"assignment_id": assignment_id, "files": public_files}
        gateway = self._campus()
        await _consume_action_confirmation(
            gateway,
            confirmation_token,
            "assignment.replace_files",
            payload,
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
        self, assignment_id: int, course_module_id: int | None = None
    ) -> dict[str, Any]:
        gateway = self._campus()
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
        self, assignment_id: int, confirmation_token: str
    ) -> dict[str, Any]:
        payload = {"assignment_id": assignment_id}
        gateway = self._campus()
        await _consume_action_confirmation(
            gateway,
            confirmation_token,
            "assignment.delete_files",
            payload,
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
        if assignment_id is None:
            if course_module_id is None:
                raise ValueError("Se requiere assignment_id o course_module_id")
            forms = gateway.session_forms()
            if forms is None:
                raise CampusProtocolError("El token REST requiere assignment_id.")
            inspection = await forms.prepare_assignment_submit(course_module_id)
            status = {
                "can_submit": bool(inspection.get("supported")),
                "transport": "moodle_http_form",
                "inspection": inspection,
            }
        else:
            try:
                status = await moodle_get_submission_status(gateway.invoke, assignment_id)
            except CampusCapabilityUnavailable as exc:
                if course_module_id is None:
                    raise CampusProtocolError(
                        "Esta sesión necesita course_module_id para preparar el formulario HTTP."
                    ) from exc
                inspection = await _session_forms_or_raise(gateway, exc).prepare_assignment_submit(
                    course_module_id
                )
                status = {
                    "can_submit": bool(inspection.get("supported")),
                    "transport": "moodle_http_form",
                    "inspection": inspection,
                }
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
            return await form_client.submit_assignment(
                course_module_id,
                {"submissionstatement": accept_submission_statement},
                confirmed=True,
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
        if assignment_id is None:
            if course_module_id is None:
                raise ValueError("Se requiere assignment_id o course_module_id")
            forms = gateway.session_forms()
            if forms is None:
                raise CampusProtocolError("El token REST requiere assignment_id.")
            inspection = await forms.inspect_assignment_delete(course_module_id)
            status = {
                "editable": bool(inspection.get("delete_action_detected")),
                "transport": "moodle_http_form",
                "inspection": inspection,
            }
        else:
            try:
                status = await moodle_get_submission_status(gateway.invoke, assignment_id)
            except CampusCapabilityUnavailable as exc:
                if course_module_id is None:
                    raise CampusProtocolError(
                        "Esta sesión necesita course_module_id para localizar el borrado HTTP."
                    ) from exc
                inspection = await _session_forms_or_raise(gateway, exc).inspect_assignment_delete(
                    course_module_id
                )
                status = {
                    "editable": bool(inspection.get("delete_action_detected")),
                    "transport": "moodle_http_form",
                    "inspection": inspection,
                }
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
            return await form_client.delete_assignment(course_module_id, confirmed=True)
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
        if assignment_id is None:
            if course_module_id is None:
                raise ValueError("Se requiere assignment_id o course_module_id")
            forms = gateway.session_forms()
            if forms is None:
                raise CampusProtocolError("El token REST requiere assignment_id.")
            inspection = await forms.inspect_assignment(course_module_id)
            return {
                "ok": bool(inspection.get("save_supported")),
                "already_editable": bool(inspection.get("save_supported")),
                "mutated": False,
                "assignment_id": None,
                "course_module_id": course_module_id,
                "transport": "moodle_http_form",
                "inspection": inspection,
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
        gateway = self._campus()
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
        gateway = self._campus()
        try:
            return await MoodleQuizClient(gateway.invoke).get_attempt_summary(
                attempt_id,
                preflight_data=preflight_data,
            )
        except CampusCapabilityUnavailable as exc:
            return await _session_forms_or_raise(gateway, exc).inspect_quiz_finish(attempt_id)

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
        form_inspection = None
        if form_client is not None:
            if quiz_id is not None:
                raise ValueError(
                    "En modo sesión usa quiz_id=null y el course_module_id mostrado por Moodle."
                )
            if course_module_id is None:
                raise ValueError(
                    "La sesión HTTP necesita course_module_id para localizar el cuestionario."
                )
            form_inspection = await form_client.inspect_quiz_start(course_module_id)
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
            "form_inspection": form_inspection,
            "warning": (
                "Iniciar un intento puede activar inmediatamente su temporizador. Confirma en "
                "un mensaje nuevo que deseas comenzar este cuestionario."
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
            return await form_client.start_quiz(
                course_module_id,
                preflight_data,
                confirmed=True,
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
        if form_client is not None:
            form_inspection = await form_client.inspect_quiz_page(attempt_id, page)
        else:
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
            "warning": "Guardar modifica el intento en curso; verifica los campos y valores.",
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
            return await form_client.save_quiz_page(
                attempt_id,
                page,
                responses,
                confirmed=True,
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
        if form_client is not None:
            form_inspection = await form_client.inspect_quiz_finish(attempt_id)
        else:
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
            return await form_client.finish_quiz(attempt_id, confirmed=True)
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
