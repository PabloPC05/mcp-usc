"""Context-bound Moodle actions that affect only the authenticated student.

The generic student action endpoint deliberately does not execute these
functions.  This module resolves the course/module and the current user's
completion state before issuing one narrowly shaped mutation.  Moodle owns
the authenticated user in both calls; no user id is accepted by the mutation
API.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any, TypeAlias

Invoke: TypeAlias = Callable[[str, Mapping[str, Any]], Awaitable[Any]]


class ActivityActionError(RuntimeError):
    """Moodle returned a payload that cannot be used as safe context."""


def _positive_id(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} debe ser un entero positivo")
    return value


def _mapping(value: Any, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ActivityActionError(f"Moodle devolvió {context} con un formato inesperado.")
    return value


def _integer(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise ActivityActionError(f"Moodle no devolvió {name} como entero.")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ActivityActionError(f"Moodle no devolvió {name} como entero.") from exc


def _boolean(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    raise ActivityActionError(f"Moodle no devolvió {name} como booleano.")


def _warnings(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, Mapping):
        return []
    warnings = payload.get("warnings", [])
    if warnings is None:
        return []
    if not isinstance(warnings, list):
        raise ActivityActionError("Moodle devolvió avisos con un formato inesperado.")
    result: list[dict[str, Any]] = []
    for item in warnings[:100]:
        if isinstance(item, Mapping):
            result.append(
                {
                    "code": str(item.get("warningcode") or item.get("code") or "warning")[:100],
                    "message": str(item.get("message") or "")[:2_000],
                    "content_is_untrusted": True,
                }
            )
    return result


def _mutation_result(payload: Any, action: str, **context: Any) -> dict[str, Any]:
    data = _mapping(payload, f"el resultado de {action}")
    status = data.get("status")
    if not isinstance(status, bool):
        raise ActivityActionError(f"Moodle no confirmó el resultado de {action}.")
    warnings = _warnings(data)
    return {
        "ok": status and not warnings,
        "action": action,
        **context,
        "warnings": warnings,
        "result": "acknowledged" if status else "rejected",
        "partial_mutation": bool(warnings),
    }


def _denied(action: str, code: str, reason: str, **context: Any) -> dict[str, Any]:
    return {
        "allowed": False,
        "action": action,
        "code": code,
        "reason": reason,
        "requires_confirmation": False,
        **context,
    }


async def _identity(invoke: Invoke) -> tuple[int, str]:
    data = _mapping(await invoke("core_webservice_get_site_info", {}), "la identidad")
    user_id = _positive_id(_integer(data.get("userid"), "userid"), "userid")
    name = str(data.get("fullname") or data.get("username") or "")[:500]
    return user_id, name


async def _module_context(
    invoke: Invoke, *, course_id: int, cmid: int
) -> tuple[Mapping[str, Any], dict[str, Any]]:
    payload = _mapping(
        await invoke("core_course_get_course_module", {"cmid": cmid}),
        "el módulo del curso",
    )
    module = payload.get("cm")
    if not isinstance(module, Mapping):
        # Some Moodle versions return the module directly in fixtures and
        # custom webservice wrappers.  Accept it only when it is unambiguous.
        module = payload
    module_id = _integer(module.get("id", cmid), "cm.id")
    module_course = _integer(module.get("course"), "cm.course")
    if module_id != cmid or module_course != course_id:
        raise ActivityActionError("El módulo no pertenece al curso solicitado.")
    context = {
        "course_id": course_id,
        "cmid": cmid,
        "module_id": module_id,
        "module_course_id": module_course,
        "module_name": str(module.get("name") or "")[:500],
        "module_type": str(module.get("modname") or module.get("modulename") or "")[:100],
        "completion_mode": module.get("completion"),
        "warnings": _warnings(payload),
        "content_is_untrusted": True,
    }
    return module, context


def _status_items(payload: Any) -> list[Mapping[str, Any]]:
    data = _mapping(payload, "el estado de finalización")
    raw = data.get("statuses")
    if not isinstance(raw, list):
        raise ActivityActionError("Moodle no devolvió statuses como lista.")
    return [item for item in raw if isinstance(item, Mapping)]


def _status_for(items: list[Mapping[str, Any]], cmid: int) -> Mapping[str, Any]:
    matches = [item for item in items if _integer(item.get("cmid"), "status.cmid") == cmid]
    if len(matches) != 1:
        raise ActivityActionError("Moodle no identificó un estado único para el módulo.")
    return matches[0]


async def preview_update_activity_completion_status_manually(
    invoke: Invoke, *, course_id: int, cmid: int, completed: bool
) -> dict[str, Any]:
    """Preview changing manual completion for one own course module."""

    course_id = _positive_id(course_id, "course_id")
    cmid = _positive_id(cmid, "cmid")
    if not isinstance(completed, bool):
        raise ValueError("completed debe ser booleano")
    user_id, full_name = await _identity(invoke)
    module, context = await _module_context(invoke, course_id=course_id, cmid=cmid)
    if module.get("completion") not in (1, "1", "manual"):
        return _denied(
            "update_activity_completion_status_manually",
            "activity_completion_not_manual",
            "El módulo no declara finalización manual disponible.",
            authenticated_user_id=user_id,
            authenticated_user_full_name=full_name,
            **context,
        )
    status_payload = await invoke(
        "core_completion_get_activities_completion_status",
        {"courseid": course_id, "userid": user_id},
    )
    status = _status_for(_status_items(status_payload), cmid)
    current_state = status.get("state", status.get("completionstate"))
    if current_state is None:
        raise ActivityActionError("Moodle no devolvió el estado actual del módulo.")
    current_state = _integer(current_state, "status.state")
    if current_state not in {0, 1, 2, 3}:
        raise ActivityActionError("Moodle devolvió un estado de finalización no válido.")
    return {
        "allowed": True,
        "action": "update_activity_completion_status_manually",
        "requires_confirmation": True,
        "authenticated_user_id": user_id,
        "authenticated_user_full_name": full_name,
        "course_id": course_id,
        "cmid": cmid,
        "module_name": context["module_name"],
        "module_type": context["module_type"],
        "current_state": current_state,
        "completed": completed,
        "affected_scope": "authenticated_user_activity_only",
        "warnings": context["warnings"] + _warnings(status_payload),
        "content_is_untrusted": True,
    }


async def update_activity_completion_status_manually(
    invoke: Invoke,
    *,
    cmid: int,
    completed: bool,
) -> dict[str, Any]:
    """Execute exactly one manual activity-completion mutation."""

    cmid = _positive_id(cmid, "cmid")
    if not isinstance(completed, bool):
        raise ValueError("completed debe ser booleano")
    payload = await invoke(
        "core_completion_update_activity_completion_status_manually",
        {"cmid": cmid, "completed": completed},
    )
    return _mutation_result(
        payload,
        "update_activity_completion_status_manually",
        cmid=cmid,
        completed=completed,
    )


def _self_completion_criterion(status: Mapping[str, Any]) -> Mapping[str, Any] | None:
    raw = status.get("completions")
    if not isinstance(raw, list):
        raise ActivityActionError("Moodle no devolvió los criterios de finalización.")
    matches = [
        item
        for item in raw
        if isinstance(item, Mapping) and _integer(item.get("type"), "criterion.type") == 1
    ]
    if len(matches) > 1:
        raise ActivityActionError("Moodle devolvió varios criterios de auto-finalización.")
    return matches[0] if matches else None


async def preview_mark_course_self_completed(
    invoke: Invoke, *, course_id: int
) -> dict[str, Any]:
    """Preview self-completing a course when Moodle explicitly permits it."""

    course_id = _positive_id(course_id, "course_id")
    user_id, full_name = await _identity(invoke)
    payload = await invoke(
        "core_completion_get_course_completion_status",
        {"courseid": course_id, "userid": user_id},
    )
    envelope = _mapping(payload, "el estado de finalización del curso")
    status = _mapping(
        envelope.get("completionstatus"),
        "completionstatus del curso",
    )
    criterion = _self_completion_criterion(status)
    course_completed = _boolean(status.get("completed"), "course.completed")
    criterion_completed = (
        None if criterion is None else _boolean(criterion.get("complete"), "criterion.complete")
    )
    context = {
        "course_id": course_id,
        "authenticated_user_id": user_id,
        "authenticated_user_full_name": full_name,
        "course_completed": course_completed,
        "self_completion_criterion": (
            None
            if criterion is None
            else {
                "type": 1,
                "title": str(criterion.get("title") or "")[:500],
                "complete": criterion_completed,
            }
        ),
        "affected_scope": "authenticated_user_course_only",
        "warnings": _warnings(envelope),
        "content_is_untrusted": True,
    }
    if criterion is None:
        return _denied(
            "mark_course_self_completed",
            "self_completion_not_allowed",
            "Moodle no declara un criterio de auto-finalización para este curso.",
            **context,
        )
    if criterion_completed:
        return _denied(
            "mark_course_self_completed",
            "course_already_completed",
            "El criterio de auto-finalización ya está completado para la cuenta actual.",
            **context,
        )
    return {
        "allowed": True,
        "action": "mark_course_self_completed",
        "requires_confirmation": True,
        **context,
    }


async def mark_course_self_completed(invoke: Invoke, *, course_id: int) -> dict[str, Any]:
    """Execute exactly one course self-completion mutation."""

    course_id = _positive_id(course_id, "course_id")
    payload = await invoke("core_completion_mark_course_self_completed", {"courseid": course_id})
    return _mutation_result(payload, "mark_course_self_completed", course_id=course_id)
