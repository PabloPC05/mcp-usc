"""Fail-closed previews and single-call Moodle student actions.

This module owns no HTTP client. Callers inject an async ``invoke`` callable
implemented by either Moodle REST or the same-origin AJAX transport. Preview
functions call only functions registered by Moodle as reads; execution
functions make exactly one write call and deliberately implement no retries.
"""

from __future__ import annotations

import html
import re
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, TypeAlias

from .security import html_to_text

Invoke: TypeAlias = Callable[[str, Mapping[str, Any]], Awaitable[Any]]

MAX_CALENDAR_NAME_CHARS = 255
MAX_DESCRIPTION_BYTES = 64 * 1024
MAX_EVENT_REPEATS = 52
MAX_EVENT_DURATION = 366 * 24 * 60 * 60
MAX_FORUM_SUBJECT_CHARS = 255
MAX_FORUM_MESSAGE_BYTES = 1024 * 1024
MAX_FORUM_DISCUSSION_PAGES = 20
FORUM_DISCUSSIONS_PER_PAGE = 100
MAX_CHOICE_OPTIONS = 20

_REQUEST_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")


class ContextualActionError(RuntimeError):
    """Moodle returned data which cannot be interpreted safely."""


class ContextualPermissionError(PermissionError):
    """A preview established that Moodle does not permit the action."""


class InvocationParams(dict[str, Any]):
    """Official Moodle parameters carrying local-only correlation metadata."""

    __slots__ = ("client_request_id",)

    def __init__(self, values: Mapping[str, Any], client_request_id: str) -> None:
        super().__init__(values)
        self.client_request_id = client_request_id


def _positive_id(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} debe ser un entero positivo")
    return value


def _integer(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise ContextualActionError(f"Moodle no devolvió {name} como entero.")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ContextualActionError(f"Moodle no devolvió {name} como entero.") from exc


def _non_negative_int(value: int, name: str, *, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} debe ser un entero no negativo")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} no puede superar {maximum}")
    return value


def _plain_text(value: str, name: str, *, max_chars: int, max_bytes: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} debe ser texto")
    text = value.strip()
    if not text:
        raise ValueError(f"{name} no puede estar vacío")
    if "\x00" in text or len(text) > max_chars or len(text.encode("utf-8")) > max_bytes:
        raise ValueError(f"{name} supera el límite permitido")
    return text


def _optional_text(value: str, name: str, *, max_bytes: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} debe ser texto")
    text = value.strip()
    if "\x00" in text or len(text.encode("utf-8")) > max_bytes:
        raise ValueError(f"{name} supera el límite permitido")
    return text


def _remote_text(value: Any, *, limit: int = 2_000) -> str:
    return html_to_text(str(value or ""), limit=limit)


def _mapping(value: Any, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ContextualActionError(f"Moodle devolvió {context} con un formato inesperado.")
    return value


def _list(value: Any, context: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        raise ContextualActionError(f"Moodle no devolvió {context} como lista.")
    return [item for item in value if isinstance(item, Mapping)]


def _warnings(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, Mapping):
        return []
    raw = payload.get("warnings", [])
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ContextualActionError("Moodle devolvió avisos con un formato inesperado.")
    result: list[dict[str, Any]] = []
    for warning in raw[:100]:
        if not isinstance(warning, Mapping):
            continue
        result.append(
            {
                "code": _remote_text(
                    warning.get("warningcode") or warning.get("code") or "warning", limit=100
                ),
                "message": _remote_text(warning.get("message"), limit=2_000),
                "item_id": warning.get("itemid"),
                "content_is_untrusted": True,
            }
        )
    return result


def _client_request_id(value: str | None) -> str:
    request_id = value or uuid.uuid4().hex
    if not isinstance(request_id, str) or not _REQUEST_ID.fullmatch(request_id):
        raise ValueError(
            "client_request_id debe tener entre 1 y 128 caracteres alfanuméricos, "
            "punto, guion, guion bajo o dos puntos"
        )
    return request_id


def _mutation_params(values: Mapping[str, Any], request_id: str) -> InvocationParams:
    return InvocationParams(values, request_id)


def _denied(action: str, code: str, reason: str, **context: Any) -> dict[str, Any]:
    return {
        "allowed": False,
        "action": action,
        "code": code,
        "reason": reason,
        "requires_confirmation": False,
        **context,
    }


def _identity(payload: Any) -> tuple[int, str]:
    data = _mapping(payload, "la identidad del usuario")
    user_id = _positive_id(_integer(data.get("userid"), "userid"), "userid")
    full_name = _remote_text(data.get("fullname") or data.get("username"), limit=500)
    return user_id, full_name


def _calendar_values(
    name: str,
    timestart: int,
    description: str,
    duration: int,
    repeats: int,
) -> tuple[str, int, str, int, int]:
    name = _plain_text(
        name,
        "name",
        max_chars=MAX_CALENDAR_NAME_CHARS,
        max_bytes=MAX_CALENDAR_NAME_CHARS * 4,
    )
    timestart = _positive_id(timestart, "timestart")
    description = _optional_text(description, "description", max_bytes=MAX_DESCRIPTION_BYTES)
    duration = _non_negative_int(duration, "duration", maximum=MAX_EVENT_DURATION)
    repeats = _non_negative_int(repeats, "repeats", maximum=MAX_EVENT_REPEATS)
    return name, timestart, description, duration, repeats


async def preview_create_personal_calendar_event(
    invoke: Invoke,
    *,
    name: str,
    timestart: int,
    description: str = "",
    duration: int = 0,
    repeats: int = 0,
) -> dict[str, Any]:
    """Preview one personal user event using only official Moodle reads."""

    name, timestart, description, duration, repeats = _calendar_values(
        name, timestart, description, duration, repeats
    )
    user_id, full_name = _identity(await invoke("core_webservice_get_site_info", {}))
    access = _mapping(
        await invoke("core_calendar_get_calendar_access_information", {"courseid": 0}),
        "los permisos del calendario",
    )
    allowed_types = _mapping(
        await invoke("core_calendar_get_allowed_event_types", {"courseid": 0}),
        "los tipos de evento permitidos",
    )
    types = allowed_types.get("allowedeventtypes")
    if not isinstance(types, list) or any(not isinstance(item, str) for item in types):
        raise ContextualActionError("Moodle no devolvió los tipos de evento permitidos.")
    if access.get("canmanageownentries") is not True or "user" not in types:
        return _denied(
            "create_personal_calendar_event",
            "personal_events_not_allowed",
            "Moodle no permite crear eventos personales al usuario actual.",
            owner_user_id=user_id,
            owner_full_name=full_name,
        )
    return {
        "allowed": True,
        "action": "create_personal_calendar_event",
        "requires_confirmation": True,
        "event_type": "user",
        "owner_user_id": user_id,
        "owner_full_name": full_name,
        "name": name,
        "description": description,
        "timestart": timestart,
        "duration": duration,
        "repeat_scope": "single" if repeats == 0 else "series",
        "additional_repeats_requested": repeats,
        "course_id": 0,
        "group_id": 0,
        "warnings": _warnings(access) + _warnings(allowed_types),
        "content_is_untrusted": True,
    }


async def create_personal_calendar_event(
    invoke: Invoke,
    *,
    name: str,
    timestart: int,
    description: str = "",
    duration: int = 0,
    repeats: int = 0,
    expected_owner_user_id: int | None = None,
    client_request_id: str | None = None,
) -> dict[str, Any]:
    """Execute exactly one ``core_calendar_create_calendar_events`` call."""

    name, timestart, description, duration, repeats = _calendar_values(
        name, timestart, description, duration, repeats
    )
    if expected_owner_user_id is not None:
        expected_owner_user_id = _positive_id(expected_owner_user_id, "expected_owner_user_id")
    request_id = _client_request_id(client_request_id)
    payload = await invoke(
        "core_calendar_create_calendar_events",
        _mutation_params(
            {
                "events": [
                    {
                        "name": name,
                        "description": description,
                        "format": 2,
                        "courseid": 0,
                        "groupid": 0,
                        "repeats": repeats,
                        "eventtype": "user",
                        "timestart": timestart,
                        "timeduration": duration,
                        "visible": 1,
                        "sequence": 1,
                    }
                ]
            },
            request_id,
        ),
    )
    data = _mapping(payload, "la creación del evento")
    events = _list(data.get("events"), "los eventos creados")
    warnings = _warnings(data)
    event_ids = [_integer(event.get("id"), "event.id") for event in events]
    owners = {_integer(event.get("userid"), "event.userid") for event in events}
    owner_matches = expected_owner_user_id is None or owners == {expected_owner_user_id}
    return {
        "ok": bool(events) and not warnings and owner_matches,
        "action": "create_personal_calendar_event",
        "client_request_id": request_id,
        "event_ids": event_ids,
        "repeat_scope": "single" if repeats == 0 else "series",
        "owner_verified": owner_matches,
        "warnings": warnings,
        "partial_mutation": bool(events) and (bool(warnings) or not owner_matches),
    }


def _delete_scope(scope: str) -> str:
    if scope not in {"single", "series"}:
        raise ValueError("scope debe ser 'single' o 'series'")
    return scope


async def preview_delete_personal_calendar_event(
    invoke: Invoke, *, event_id: int, scope: str = "single"
) -> dict[str, Any]:
    """Verify ownership, event type, permissions, and repetition scope."""

    event_id = _positive_id(event_id, "event_id")
    scope = _delete_scope(scope)
    user_id, full_name = _identity(await invoke("core_webservice_get_site_info", {}))
    event_payload = _mapping(
        await invoke("core_calendar_get_calendar_event_by_id", {"eventid": event_id}),
        "el evento del calendario",
    )
    event = _mapping(event_payload.get("event"), "el evento del calendario")
    access = _mapping(
        await invoke("core_calendar_get_calendar_access_information", {"courseid": 0}),
        "los permisos del calendario",
    )
    owner_id = _integer(event.get("userid"), "event.userid")
    event_type = str(event.get("eventtype") or "")
    course_id = _integer(event.get("courseid", 0), "event.courseid")
    group_id = _integer(event.get("groupid", 0), "event.groupid")
    repeat_id = _integer(event.get("repeatid", 0) or 0, "event.repeatid")
    context = {
        "event_id": event_id,
        "event_name": _remote_text(event.get("name"), limit=MAX_CALENDAR_NAME_CHARS),
        "owner_user_id": owner_id,
        "current_user_id": user_id,
        "current_user_full_name": full_name,
        "repeat_id": repeat_id or None,
        "repeat_scope": scope,
        "warnings": _warnings(event_payload) + _warnings(access),
        "content_is_untrusted": True,
    }
    if event_type != "user" or course_id != 0 or group_id != 0:
        return _denied(
            "delete_personal_calendar_event",
            "not_a_personal_event",
            "El evento no es un evento personal aislado del usuario.",
            **context,
        )
    if owner_id != user_id:
        return _denied(
            "delete_personal_calendar_event",
            "not_event_owner",
            "El evento pertenece a otro usuario.",
            **context,
        )
    if access.get("canmanageownentries") is not True:
        return _denied(
            "delete_personal_calendar_event",
            "cannot_manage_own_events",
            "Moodle no permite eliminar eventos personales al usuario actual.",
            **context,
        )
    if scope == "series" and repeat_id <= 0:
        return _denied(
            "delete_personal_calendar_event",
            "event_is_not_repeated",
            "No se puede confirmar un borrado de serie porque el evento no es repetido.",
            **context,
        )
    return {
        "allowed": True,
        "action": "delete_personal_calendar_event",
        "requires_confirmation": True,
        "affected_scope": "all_occurrences_in_series"
        if scope == "series"
        else "this_occurrence_only",
        **context,
    }


async def delete_personal_calendar_event(
    invoke: Invoke,
    *,
    event_id: int,
    scope: str = "single",
    client_request_id: str | None = None,
) -> dict[str, Any]:
    """Execute exactly one ``core_calendar_delete_calendar_events`` call."""

    event_id = _positive_id(event_id, "event_id")
    scope = _delete_scope(scope)
    request_id = _client_request_id(client_request_id)
    payload = await invoke(
        "core_calendar_delete_calendar_events",
        _mutation_params(
            {"events": [{"eventid": event_id, "repeat": scope == "series"}]}, request_id
        ),
    )
    return {
        "ok": True,
        "action": "delete_personal_calendar_event",
        "event_id": event_id,
        "repeat_scope": scope,
        "client_request_id": request_id,
        "result": None if payload is None else "acknowledged",
    }


def _forum_values(subject: str, message: str) -> tuple[str, str]:
    subject = _plain_text(
        subject,
        "subject",
        max_chars=MAX_FORUM_SUBJECT_CHARS,
        max_bytes=MAX_FORUM_SUBJECT_CHARS * 4,
    )
    message = _plain_text(
        message,
        "message",
        max_chars=MAX_FORUM_MESSAGE_BYTES,
        max_bytes=MAX_FORUM_MESSAGE_BYTES,
    )
    return subject, message


def _forum_items(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, Mapping):
        payload = payload.get("forums")
    return _list(payload, "los foros")


def _find_forum(payload: Any, forum_id: int, course_id: int) -> Mapping[str, Any]:
    matches = [
        item
        for item in _forum_items(payload)
        if _integer(item.get("id"), "forum.id") == forum_id
        and _integer(item.get("course"), "forum.course") == course_id
    ]
    if len(matches) != 1:
        raise ContextualActionError("No se pudo resolver el foro dentro del curso indicado.")
    return matches[0]


async def _forum_audience(
    invoke: Invoke,
    *,
    cmid: int,
    course_id: int,
    requested_group_id: int,
) -> tuple[dict[str, Any] | None, int, str | None]:
    if isinstance(requested_group_id, bool) or not isinstance(requested_group_id, int):
        raise ValueError("group_id debe ser un entero")
    if requested_group_id < -1:
        raise ValueError("group_id debe ser -1, 0 o un identificador positivo")
    groupmode_payload = _mapping(
        await invoke("core_group_get_activity_groupmode", {"cmid": cmid}),
        "el modo de grupos del foro",
    )
    groupmode = _integer(groupmode_payload.get("groupmode"), "groupmode")
    groups_payload = _mapping(
        await invoke("core_group_get_activity_allowed_groups", {"cmid": cmid, "userid": 0}),
        "los grupos permitidos del foro",
    )
    groups = _list(groups_payload.get("groups"), "los grupos permitidos")
    by_id = {_integer(group.get("id"), "group.id"): group for group in groups}
    if groupmode == 0:
        if requested_group_id > 0:
            return None, requested_group_id, "El foro no usa grupos; no se puede elegir uno."
        return {"scope": "course", "course_id": course_id}, 0, None
    if requested_group_id == -1:
        if groups_payload.get("canaccessallgroups") is not True:
            return None, requested_group_id, "El usuario no puede publicar para todos los grupos."
        return {"scope": "course", "course_id": course_id}, -1, None
    if requested_group_id > 0:
        group = by_id.get(requested_group_id)
        if group is None:
            return (
                None,
                requested_group_id,
                "El grupo indicado no está permitido para este usuario.",
            )
        return (
            {
                "scope": "group",
                "course_id": course_id,
                "group_id": requested_group_id,
                "group_name": _remote_text(group.get("name"), limit=500),
            },
            requested_group_id,
            None,
        )
    if len(by_id) != 1:
        return (
            None,
            0,
            "Moodle no identifica de forma unívoca el grupo activo; "
            "indica group_id explícitamente.",
        )
    group_id, group = next(iter(by_id.items()))
    return (
        {
            "scope": "group",
            "course_id": course_id,
            "group_id": group_id,
            "group_name": _remote_text(group.get("name"), limit=500),
        },
        group_id,
        None,
    )


async def preview_create_forum_discussion(
    invoke: Invoke,
    *,
    course_id: int,
    forum_id: int,
    subject: str,
    message: str,
    group_id: int = 0,
) -> dict[str, Any]:
    """Resolve forum, course, group audience, and discussion permissions."""

    course_id = _positive_id(course_id, "course_id")
    forum_id = _positive_id(forum_id, "forum_id")
    subject, message = _forum_values(subject, message)
    forum = _find_forum(
        await invoke("mod_forum_get_forums_by_courses", {"courseids": [course_id]}),
        forum_id,
        course_id,
    )
    cmid = _positive_id(_integer(forum.get("cmid"), "forum.cmid"), "forum.cmid")
    access = _mapping(
        await invoke("mod_forum_get_forum_access_information", {"forumid": forum_id}),
        "los permisos del foro",
    )
    audience, resolved_group_id, audience_error = await _forum_audience(
        invoke, cmid=cmid, course_id=course_id, requested_group_id=group_id
    )
    if audience_error:
        return _denied(
            "create_forum_discussion",
            "ambiguous_or_forbidden_audience",
            audience_error,
            course_id=course_id,
            forum_id=forum_id,
            forum_name=_remote_text(forum.get("name"), limit=500),
        )
    can_add = _mapping(
        await invoke(
            "mod_forum_can_add_discussion",
            {"forumid": forum_id, "groupid": resolved_group_id},
        ),
        "el permiso para crear discusiones",
    )
    if access.get("canstartdiscussion") is not True or can_add.get("status") is not True:
        return _denied(
            "create_forum_discussion",
            "cannot_start_discussion",
            "Moodle no permite iniciar una discusión en este foro y audiencia.",
            course_id=course_id,
            forum_id=forum_id,
            forum_name=_remote_text(forum.get("name"), limit=500),
            audience=audience,
        )
    return {
        "allowed": True,
        "action": "create_forum_discussion",
        "requires_confirmation": True,
        "course_id": course_id,
        "forum_id": forum_id,
        "forum_name": _remote_text(forum.get("name"), limit=500),
        "forum_type": _remote_text(forum.get("type"), limit=100),
        "subject": subject,
        "message": message,
        "audience": audience,
        "resolved_group_id": resolved_group_id,
        "attachments": [],
        "private_reply": False,
        "may_notify_subscribers": True,
        "warnings": _warnings(access) + _warnings(can_add),
        "content_is_untrusted": True,
    }


def _plain_to_forum_html(value: str) -> str:
    return "<br>".join(html.escape(line, quote=False) for line in value.splitlines())


async def create_forum_discussion(
    invoke: Invoke,
    *,
    forum_id: int,
    subject: str,
    message: str,
    group_id: int,
    client_request_id: str | None = None,
) -> dict[str, Any]:
    """Execute one attachment-free ``mod_forum_add_discussion`` call."""

    forum_id = _positive_id(forum_id, "forum_id")
    if isinstance(group_id, bool) or not isinstance(group_id, int) or group_id < -1:
        raise ValueError("group_id debe ser -1, 0 o un identificador positivo")
    subject, message = _forum_values(subject, message)
    request_id = _client_request_id(client_request_id)
    payload = await invoke(
        "mod_forum_add_discussion",
        _mutation_params(
            {
                "forumid": forum_id,
                "subject": subject,
                "message": _plain_to_forum_html(message),
                "groupid": group_id,
                "options": [
                    {"name": "discussionsubscribe", "value": 0},
                    {"name": "discussionpinned", "value": 0},
                    {"name": "inlineattachmentsid", "value": 0},
                    {"name": "attachmentsid", "value": 0},
                ],
            },
            request_id,
        ),
    )
    data = _mapping(payload, "la creación de la discusión")
    discussion_id = _integer(data.get("discussionid", 0) or 0, "discussionid")
    warnings = _warnings(data)
    return {
        "ok": discussion_id > 0 and not warnings,
        "action": "create_forum_discussion",
        "forum_id": forum_id,
        "discussion_id": discussion_id or None,
        "client_request_id": request_id,
        "warnings": warnings,
        "partial_mutation": discussion_id > 0 and bool(warnings),
    }


def _discussion_items(payload: Any) -> list[Mapping[str, Any]]:
    data = _mapping(payload, "las discusiones del foro")
    return _list(data.get("discussions"), "las discusiones del foro")


async def _find_visible_discussion(
    invoke: Invoke, *, forum_id: int, discussion_id: int, group_id: int
) -> tuple[Mapping[str, Any], list[dict[str, Any]]]:
    warnings: list[dict[str, Any]] = []
    for page in range(MAX_FORUM_DISCUSSION_PAGES):
        payload = await invoke(
            "mod_forum_get_forum_discussions",
            {
                "forumid": forum_id,
                "sortorder": -1,
                "page": page,
                "perpage": FORUM_DISCUSSIONS_PER_PAGE,
                "groupid": group_id,
            },
        )
        items = _discussion_items(payload)
        warnings.extend(_warnings(payload))
        matches = [
            item
            for item in items
            if _integer(item.get("discussion") or item.get("id"), "discussion.id") == discussion_id
        ]
        if len(matches) == 1:
            return matches[0], warnings
        if len(matches) > 1:
            break
        if len(items) < FORUM_DISCUSSIONS_PER_PAGE:
            break
    raise ContextualActionError(
        "No se pudo vincular el mensaje con una discusión visible dentro del límite paginado."
    )


async def preview_reply_forum_post(
    invoke: Invoke,
    *,
    course_id: int,
    forum_id: int,
    parent_post_id: int,
    message: str,
    subject: str | None = None,
    group_id: int = 0,
) -> dict[str, Any]:
    """Resolve a public, attachment-free reply and its visible audience."""

    course_id = _positive_id(course_id, "course_id")
    forum_id = _positive_id(forum_id, "forum_id")
    parent_post_id = _positive_id(parent_post_id, "parent_post_id")
    message = _plain_text(
        message,
        "message",
        max_chars=MAX_FORUM_MESSAGE_BYTES,
        max_bytes=MAX_FORUM_MESSAGE_BYTES,
    )
    forum = _find_forum(
        await invoke("mod_forum_get_forums_by_courses", {"courseids": [course_id]}),
        forum_id,
        course_id,
    )
    access = _mapping(
        await invoke("mod_forum_get_forum_access_information", {"forumid": forum_id}),
        "los permisos del foro",
    )
    post_payload = _mapping(
        await invoke("mod_forum_get_discussion_post", {"postid": parent_post_id}),
        "el mensaje del foro",
    )
    post = _mapping(post_payload.get("post"), "el mensaje del foro")
    capabilities = _mapping(post.get("capabilities"), "los permisos del mensaje")
    discussion_id = _positive_id(
        _integer(post.get("discussionid"), "post.discussionid"), "post.discussionid"
    )
    cmid = _positive_id(_integer(forum.get("cmid"), "forum.cmid"), "forum.cmid")
    audience, resolved_group_id, audience_error = await _forum_audience(
        invoke,
        cmid=cmid,
        course_id=course_id,
        requested_group_id=group_id,
    )
    if audience_error:
        return _denied(
            "reply_forum_post",
            "ambiguous_or_forbidden_audience",
            audience_error,
            course_id=course_id,
            forum_id=forum_id,
            discussion_id=discussion_id,
            parent_post_id=parent_post_id,
        )
    discussion, discussion_warnings = await _find_visible_discussion(
        invoke,
        forum_id=forum_id,
        discussion_id=discussion_id,
        group_id=resolved_group_id,
    )
    discussion_forum_id = _integer(discussion.get("forum") or forum_id, "discussion.forum")
    course_from_discussion = _integer(discussion.get("course") or course_id, "discussion.course")
    if discussion_forum_id != forum_id or course_from_discussion != course_id:
        raise ContextualActionError("La discusión no pertenece al foro y curso indicados.")
    raw_group_id = _integer(discussion.get("groupid", -1), "discussion.groupid")
    group_matches = (
        raw_group_id in {-1, 0} if resolved_group_id == 0 else raw_group_id == resolved_group_id
    )
    if not group_matches:
        return _denied(
            "reply_forum_post",
            "discussion_group_mismatch",
            "La discusión no pertenece al grupo o audiencia confirmados.",
            course_id=course_id,
            forum_id=forum_id,
            discussion_id=discussion_id,
            parent_post_id=parent_post_id,
        )
    resolved_subject = subject
    if resolved_subject is None:
        resolved_subject = str(post.get("replysubject") or post.get("subject") or "")
    resolved_subject, _ = _forum_values(resolved_subject, message)
    if access.get("canreplypost") is not True or capabilities.get("reply") is not True:
        return _denied(
            "reply_forum_post",
            "cannot_reply",
            "Moodle no permite responder a este mensaje.",
            course_id=course_id,
            forum_id=forum_id,
            discussion_id=discussion_id,
            parent_post_id=parent_post_id,
            audience=audience,
        )
    return {
        "allowed": True,
        "action": "reply_forum_post",
        "requires_confirmation": True,
        "course_id": course_id,
        "forum_id": forum_id,
        "forum_name": _remote_text(forum.get("name"), limit=500),
        "discussion_id": discussion_id,
        "discussion_subject": _remote_text(
            discussion.get("name") or discussion.get("subject"), limit=MAX_FORUM_SUBJECT_CHARS
        ),
        "parent_post_id": parent_post_id,
        "parent_subject": _remote_text(post.get("subject"), limit=MAX_FORUM_SUBJECT_CHARS),
        "subject": resolved_subject,
        "message": message,
        "audience": audience,
        "resolved_group_id": resolved_group_id,
        "attachments": [],
        "private_reply": False,
        "may_notify_subscribers": True,
        "warnings": _warnings(access) + _warnings(post_payload) + discussion_warnings,
        "content_is_untrusted": True,
    }


async def reply_forum_post(
    invoke: Invoke,
    *,
    parent_post_id: int,
    subject: str,
    message: str,
    client_request_id: str | None = None,
) -> dict[str, Any]:
    """Execute one public, attachment-free ``mod_forum_add_discussion_post`` call."""

    parent_post_id = _positive_id(parent_post_id, "parent_post_id")
    subject, message = _forum_values(subject, message)
    request_id = _client_request_id(client_request_id)
    payload = await invoke(
        "mod_forum_add_discussion_post",
        _mutation_params(
            {
                "postid": parent_post_id,
                "subject": subject,
                "message": message,
                "options": [
                    {"name": "discussionsubscribe", "value": 0},
                    {"name": "private", "value": 0},
                    {"name": "inlineattachmentsid", "value": 0},
                    {"name": "attachmentsid", "value": 0},
                    {"name": "topreferredformat", "value": 0},
                ],
                "messageformat": 2,
            },
            request_id,
        ),
    )
    data = _mapping(payload, "la respuesta del foro")
    post_id = _integer(data.get("postid", 0) or 0, "postid")
    warnings = _warnings(data)
    return {
        "ok": post_id > 0 and not warnings,
        "action": "reply_forum_post",
        "parent_post_id": parent_post_id,
        "post_id": post_id or None,
        "client_request_id": request_id,
        "warnings": warnings,
        "partial_mutation": post_id > 0 and bool(warnings),
    }


def _choice_items(payload: Any) -> list[Mapping[str, Any]]:
    data = _mapping(payload, "las actividades Choice")
    return _list(data.get("choices"), "las actividades Choice")


async def _choice_context(
    invoke: Invoke, *, course_id: int, choice_id: int
) -> tuple[Mapping[str, Any], list[Mapping[str, Any]], list[dict[str, Any]]]:
    payload = await invoke("mod_choice_get_choices_by_courses", {"courseids": [course_id]})
    matches = [
        item
        for item in _choice_items(payload)
        if _integer(item.get("id"), "choice.id") == choice_id
        and _integer(item.get("course"), "choice.course") == course_id
    ]
    if len(matches) != 1:
        raise ContextualActionError("No se pudo resolver Choice dentro del curso indicado.")
    choice = matches[0]
    for key in ("allowupdate", "allowmultiple", "timeopen", "timeclose"):
        if key not in choice:
            raise ContextualActionError(
                "Moodle no devolvió los permisos y fechas necesarios de Choice."
            )
    option_payload = _mapping(
        await invoke("mod_choice_get_choice_options", {"choiceid": choice_id}),
        "las opciones de Choice",
    )
    options = _list(option_payload.get("options"), "las opciones de Choice")
    return choice, options, _warnings(payload) + _warnings(option_payload)


def _choice_time_denial(choice: Mapping[str, Any], now: int) -> tuple[str, str] | None:
    time_open = _integer(choice.get("timeopen", 0) or 0, "choice.timeopen")
    time_close = _integer(choice.get("timeclose", 0) or 0, "choice.timeclose")
    if time_open > 0 and now < time_open:
        return "choice_not_open", "Choice todavía no está abierto."
    if time_close > 0 and now > time_close:
        return "choice_closed", "Choice ya está cerrado."
    return None


def _normalise_option_texts(option_texts: Sequence[str]) -> list[str]:
    if isinstance(option_texts, (str, bytes, bytearray)):
        raise ValueError("option_texts debe ser una lista de textos")
    texts = [
        _plain_text(value, "option_text", max_chars=2_000, max_bytes=8_000)
        for value in option_texts
    ]
    if not 1 <= len(texts) <= MAX_CHOICE_OPTIONS:
        raise ValueError(f"option_texts debe contener entre 1 y {MAX_CHOICE_OPTIONS} opciones")
    if len(set(texts)) != len(texts):
        raise ValueError("option_texts no puede contener duplicados")
    return texts


def _resolved_options(
    options: Sequence[Mapping[str, Any]], requested_texts: Sequence[str]
) -> list[dict[str, Any]]:
    by_text: dict[str, list[Mapping[str, Any]]] = {}
    for option in options:
        text = _remote_text(option.get("text"), limit=2_000)
        by_text.setdefault(text, []).append(option)
    result: list[dict[str, Any]] = []
    for requested in requested_texts:
        matches = by_text.get(requested, [])
        if len(matches) != 1:
            raise ContextualActionError(
                f"La opción exacta {requested!r} no existe o es ambigua en Moodle."
            )
        option = matches[0]
        result.append(
            {
                "option_id": _positive_id(
                    _integer(option.get("id"), "choice.option.id"), "choice.option.id"
                ),
                "text": requested,
                "selected": option.get("checked") in (1, True, "1"),
                "disabled": option.get("disabled") in (1, True, "1"),
                "content_is_untrusted": True,
            }
        )
    return result


async def preview_submit_choice_response(
    invoke: Invoke,
    *,
    course_id: int,
    choice_id: int,
    option_texts: Sequence[str],
    now: int | None = None,
) -> dict[str, Any]:
    """Resolve case-sensitive displayed option text to Moodle option IDs."""

    course_id = _positive_id(course_id, "course_id")
    choice_id = _positive_id(choice_id, "choice_id")
    requested = _normalise_option_texts(option_texts)
    now = int(time.time()) if now is None else _non_negative_int(now, "now")
    choice, options, warnings = await _choice_context(
        invoke, course_id=course_id, choice_id=choice_id
    )
    denial = _choice_time_denial(choice, now)
    if denial:
        return _denied(
            "submit_choice_response",
            denial[0],
            denial[1],
            course_id=course_id,
            choice_id=choice_id,
            choice_name=_remote_text(choice.get("name"), limit=500),
        )
    resolved = _resolved_options(options, requested)
    if any(option["disabled"] for option in resolved):
        return _denied(
            "submit_choice_response",
            "choice_option_disabled",
            "Al menos una opción no está disponible para seleccionar.",
            course_id=course_id,
            choice_id=choice_id,
            options=resolved,
        )
    allow_multiple = choice.get("allowmultiple") is True or choice.get("allowmultiple") == 1
    allow_update = choice.get("allowupdate") is True or choice.get("allowupdate") == 1
    selected = [option for option in options if option.get("checked") in (1, True, "1")]
    if len(resolved) > 1 and not allow_multiple:
        return _denied(
            "submit_choice_response",
            "multiple_answers_not_allowed",
            "Choice permite seleccionar una sola opción.",
            course_id=course_id,
            choice_id=choice_id,
            options=resolved,
        )
    if selected and not allow_update:
        return _denied(
            "submit_choice_response",
            "choice_update_not_allowed",
            "Ya existe una respuesta y Moodle no permite cambiarla.",
            course_id=course_id,
            choice_id=choice_id,
            options=resolved,
        )
    return {
        "allowed": True,
        "action": "submit_choice_response",
        "requires_confirmation": True,
        "course_id": course_id,
        "choice_id": choice_id,
        "choice_name": _remote_text(choice.get("name"), limit=500),
        "options": resolved,
        "option_ids": [option["option_id"] for option in resolved],
        "replaces_existing_response": bool(selected),
        "allow_multiple": allow_multiple,
        "allow_update": allow_update,
        "warnings": warnings,
        "content_is_untrusted": True,
    }


async def submit_choice_response(
    invoke: Invoke,
    *,
    choice_id: int,
    option_ids: Sequence[int],
    client_request_id: str | None = None,
) -> dict[str, Any]:
    """Execute exactly one ``mod_choice_submit_choice_response`` call."""

    choice_id = _positive_id(choice_id, "choice_id")
    if isinstance(option_ids, (str, bytes, bytearray)):
        raise ValueError("option_ids debe ser una lista de identificadores")
    ids = [_positive_id(value, "option_id") for value in option_ids]
    if not 1 <= len(ids) <= MAX_CHOICE_OPTIONS or len(set(ids)) != len(ids):
        raise ValueError("option_ids debe contener identificadores únicos y no vacíos")
    request_id = _client_request_id(client_request_id)
    payload = await invoke(
        "mod_choice_submit_choice_response",
        _mutation_params({"choiceid": choice_id, "responses": ids}, request_id),
    )
    data = _mapping(payload, "la respuesta de Choice")
    answers = _list(data.get("answers"), "las respuestas guardadas")
    warnings = _warnings(data)
    return {
        "ok": bool(answers) and not warnings,
        "action": "submit_choice_response",
        "choice_id": choice_id,
        "option_ids": ids,
        "answer_ids": [_integer(answer.get("id"), "answer.id") for answer in answers],
        "client_request_id": request_id,
        "warnings": warnings,
        "partial_mutation": bool(answers) and bool(warnings),
    }


async def preview_cancel_choice_response(
    invoke: Invoke,
    *,
    course_id: int,
    choice_id: int,
    now: int | None = None,
) -> dict[str, Any]:
    """Preview deleting all responses belonging to the current user only."""

    course_id = _positive_id(course_id, "course_id")
    choice_id = _positive_id(choice_id, "choice_id")
    now = int(time.time()) if now is None else _non_negative_int(now, "now")
    choice, options, warnings = await _choice_context(
        invoke, course_id=course_id, choice_id=choice_id
    )
    denial = _choice_time_denial(choice, now)
    if denial:
        return _denied(
            "cancel_choice_response",
            denial[0],
            denial[1],
            course_id=course_id,
            choice_id=choice_id,
        )
    allow_update = choice.get("allowupdate") is True or choice.get("allowupdate") == 1
    selected = [option for option in options if option.get("checked") in (1, True, "1")]
    if not allow_update:
        return _denied(
            "cancel_choice_response",
            "choice_update_not_allowed",
            "Moodle no permite retirar la respuesta de este Choice.",
            course_id=course_id,
            choice_id=choice_id,
        )
    if not selected:
        return _denied(
            "cancel_choice_response",
            "no_current_response",
            "El usuario actual no tiene una respuesta que cancelar.",
            course_id=course_id,
            choice_id=choice_id,
        )
    resolved = [
        {
            "option_id": _positive_id(
                _integer(option.get("id"), "choice.option.id"), "choice.option.id"
            ),
            "text": _remote_text(option.get("text"), limit=2_000),
            "content_is_untrusted": True,
        }
        for option in selected
    ]
    return {
        "allowed": True,
        "action": "cancel_choice_response",
        "requires_confirmation": True,
        "course_id": course_id,
        "choice_id": choice_id,
        "choice_name": _remote_text(choice.get("name"), limit=500),
        "selected_options": resolved,
        "affected_scope": "all_current_user_responses",
        "never_deletes_other_users_responses": True,
        "warnings": warnings,
        "content_is_untrusted": True,
    }


async def cancel_choice_response(
    invoke: Invoke,
    *,
    choice_id: int,
    client_request_id: str | None = None,
) -> dict[str, Any]:
    """Delete only the current user's answers with one empty-response call."""

    choice_id = _positive_id(choice_id, "choice_id")
    request_id = _client_request_id(client_request_id)
    payload = await invoke(
        "mod_choice_delete_choice_responses",
        _mutation_params({"choiceid": choice_id, "responses": []}, request_id),
    )
    data = _mapping(payload, "la cancelación de Choice")
    warnings = _warnings(data)
    return {
        "ok": data.get("status") is True and not warnings,
        "action": "cancel_choice_response",
        "choice_id": choice_id,
        "affected_scope": "all_current_user_responses",
        "client_request_id": request_id,
        "warnings": warnings,
    }
