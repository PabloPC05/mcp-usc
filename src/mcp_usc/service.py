from __future__ import annotations

import hashlib
import secrets
import time
from datetime import datetime, timedelta
from typing import Any

from .campus import create_campus_gateway
from .domain import MADRID, normalise_announcement, normalise_course, normalise_event
from .public_web import search_exam_sources
from .security import html_to_text
from .settings import Settings

_MESSAGE_CONFIRMATION_TTL = 300
_MESSAGE_CONFIRMATIONS: dict[str, tuple[float, int, str]] = {}
_MESSAGE_CONTACT_TTL = 600
_MESSAGE_CONTACTS: dict[int, tuple[float, str]] = {}


def _message_digest(recipient_user_id: int, text: str) -> str:
    value = f"{recipient_user_id}\0{text}".encode()
    return hashlib.sha256(value).hexdigest()


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
        result = await self._campus().search_message_contacts(query, limit)
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
                full_name,
            )
        return normalised

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
        recipient_full_name = contact[1]
        expired = [token for token, entry in _MESSAGE_CONFIRMATIONS.items() if entry[0] < now]
        for token in expired:
            _MESSAGE_CONFIRMATIONS.pop(token, None)
        digest = _message_digest(recipient_user_id, clean_text)
        if not confirmation_token:
            confirmation_token = secrets.token_urlsafe(12)
            _MESSAGE_CONFIRMATIONS[confirmation_token] = (
                now + _MESSAGE_CONFIRMATION_TTL,
                recipient_user_id,
                digest,
            )
            return {
                "sent": False,
                "requires_confirmation": True,
                "recipient_user_id": recipient_user_id,
                "recipient_full_name": recipient_full_name,
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
            expires_at, expected_recipient, expected_digest = expected
        else:
            expires_at, expected_recipient, expected_digest = 0, 0, ""
        if expires_at < now or expected_recipient != recipient_user_id or expected_digest != digest:
            raise ValueError(
                "Token de confirmación inválido, caducado o no ligado a este destinatario/texto. "
                "Solicita una nueva vista previa."
            )
        result = await self._campus().send_message(recipient_user_id, clean_text)
        return {
            "sent": True,
            "recipient_user_id": recipient_user_id,
            "recipient_full_name": recipient_full_name,
            "message_id": (
                result.get("msgid") or result.get("messageid") or result.get("message_id")
            ),
            "server_error": html_to_text(result.get("errormessage")),
        }
