"""Pure AJAX course-module listings for a MoodleSession transport.

``core_courseformat_get_state`` exposes the course navigation model without
opening ``course/view.php`` or an activity page.  It provides course-module
IDs (CMIDs), not plugin instance IDs or downloadable file URLs.  This module
keeps that distinction explicit and never follows the advertised links.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import parse_qs, urlparse

from .campus import CampusProtocolError
from .quizzes import SECURITY_NOTE as QUIZ_SECURITY_NOTE
from .security import UnsafeUrlError, html_to_text, validate_usc_url

_MAX_COURSES = 100
_MAX_MODULES = 2_000
_MAX_SECTIONS = 500
_MODULE_TYPE = re.compile(r"[a-z][a-z0-9_]{0,99}")
_RESOURCE_MODULES = frozenset({"book", "folder", "page", "resource", "url"})


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError(f"{name} debe ser un entero positivo")
    if isinstance(value, str) and not value.isdecimal():
        raise ValueError(f"{name} debe ser un entero positivo") from None
    result = int(value)
    if result <= 0:
        raise ValueError(f"{name} debe ser un entero positivo")
    return result


def _optional_positive_int(value: Any) -> int | None:
    if value in (None, "", 0, "0") or isinstance(value, bool):
        return None
    if not isinstance(value, (int, str)) or (
        isinstance(value, str) and not value.isdecimal()
    ):
        return None
    result = int(value)
    return result if result > 0 else None


def _pagination(offset: int, limit: int) -> tuple[int, int]:
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("offset debe ser un entero no negativo")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit debe estar entre 1 y 100")
    return offset, limit


def _page(items: Sequence[dict[str, Any]], offset: int, limit: int) -> dict[str, Any]:
    page = list(items[offset : offset + limit])
    has_more = offset + len(page) < len(items)
    return {
        "items": page,
        "offset": offset,
        "limit": limit,
        "returned": len(page),
        "total_count": len(items),
        "has_more": has_more,
        "next_offset": offset + len(page) if has_more else None,
        "server_side_pagination": False,
        "content_is_untrusted": True,
    }


def _activity_url(value: Any, module_type: str, cmid: int) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        candidate = validate_usc_url(value, campus=True)
    except UnsafeUrlError:
        return None
    parsed = urlparse(candidate)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.path != f"/mod/{module_type}/view.php"
        or set(query) != {"id"}
        or len(query["id"]) != 1
        or query["id"][0] != str(cmid)
        or parsed.fragment
    ):
        return None
    return candidate


def _decode_state(value: Any) -> Mapping[str, Any]:
    try:
        state = json.loads(value) if isinstance(value, str) else value
    except json.JSONDecodeError as exc:
        raise CampusProtocolError("Moodle devolvio un estado de curso no valido") from exc
    if not isinstance(state, Mapping) or not isinstance(state.get("cm"), list):
        raise CampusProtocolError("Moodle devolvio un estado de curso inesperado")
    sections = state.get("section", [])
    if not isinstance(sections, list):
        raise CampusProtocolError("Moodle devolvio secciones de curso inesperadas")
    if len(state["cm"]) > _MAX_MODULES or len(sections) > _MAX_SECTIONS:
        raise CampusProtocolError("El estado de curso supera los limites permitidos")
    return state


async def fetch_session_course_states(
    gateway: Any, course_ids: Sequence[int] | None
) -> list[dict[str, Any]]:
    """Fetch bounded states only for courses advertised to the current account."""

    courses = await gateway.list_courses(include_archived=True)
    by_id: dict[int, Mapping[str, Any]] = {}
    for course in courses:
        if not isinstance(course, Mapping):
            continue
        course_id = _optional_positive_int(course.get("id"))
        if course_id is not None:
            by_id[course_id] = course
    requested = list(course_ids or by_id)
    unique_ids: list[int] = []
    for value in requested:
        course_id = _positive_int(value, "course_id")
        if course_id not in by_id:
            raise ValueError("course_id no pertenece a los cursos anunciados para esta cuenta")
        if course_id not in unique_ids:
            unique_ids.append(course_id)
    if len(unique_ids) > _MAX_COURSES:
        raise ValueError(f"No se pueden consultar mas de {_MAX_COURSES} cursos a la vez")

    snapshots: list[dict[str, Any]] = []
    for course_id in unique_ids:
        raw_state = await gateway.invoke(
            "core_courseformat_get_state", {"courseid": course_id}
        )
        state = _decode_state(raw_state)
        sections: dict[int, dict[str, Any]] = {}
        for raw_section in state.get("section", []):
            if not isinstance(raw_section, Mapping):
                raise CampusProtocolError("Moodle devolvio una seccion de curso no valida")
            section_id = _positive_int(raw_section.get("id"), "section_id")
            if section_id in sections:
                raise CampusProtocolError("Moodle duplico una seccion en el estado de curso")
            sections[section_id] = {
                "section_id": section_id,
                "section_number": _optional_positive_int(
                    raw_section.get("section") or raw_section.get("number")
                )
                or 0,
                "section_name": html_to_text(
                    str(
                        raw_section.get("title")
                        or raw_section.get("rawtitle")
                        or ""
                    ),
                    limit=500,
                ),
                "visible": bool(raw_section.get("visible", True)),
            }
        modules: list[dict[str, Any]] = []
        seen_modules: set[int] = set()
        for raw_module in state["cm"]:
            if not isinstance(raw_module, Mapping):
                raise CampusProtocolError("Moodle devolvio un modulo de curso no valido")
            module_type = str(
                raw_module.get("module") or raw_module.get("modname") or ""
            ).casefold()
            if not _MODULE_TYPE.fullmatch(module_type):
                raise CampusProtocolError("Moodle devolvio un tipo de modulo no valido")
            cmid = _positive_int(raw_module.get("id"), "course_module_id")
            if cmid in seen_modules:
                raise CampusProtocolError("Moodle duplico un modulo en el estado de curso")
            seen_modules.add(cmid)
            section_id = _optional_positive_int(raw_module.get("sectionid"))
            section = sections.get(section_id or -1, {})
            visible = bool(raw_module.get("visible", True)) and bool(
                raw_module.get("uservisible", True)
            )
            visible = visible and bool(raw_module.get("accessvisible", True))
            visible = visible and bool(section.get("visible", True))
            modules.append(
                {
                    "course_id": course_id,
                    "course_name": html_to_text(
                        str(
                            by_id[course_id].get("fullname")
                            or by_id[course_id].get("shortname")
                            or ""
                        ),
                        limit=1_000,
                    ),
                    "course_module_id": cmid,
                    "module_type": module_type,
                    "module_type_label": html_to_text(
                        str(raw_module.get("modname") or ""), limit=200
                    ),
                    "name": html_to_text(
                        str(raw_module.get("name") or ""), limit=1_000
                    ),
                    "section_id": section_id,
                    "section_number": section.get("section_number")
                    if section
                    else (_optional_positive_int(raw_module.get("sectionnumber")) or 0),
                    "section_name": section.get("section_name", ""),
                    "visible": visible,
                    "activity_url": (
                        _activity_url(raw_module.get("url"), module_type, cmid)
                        if visible
                        else None
                    ),
                    "content_is_untrusted": True,
                }
            )
        snapshots.append(
            {
                "course_id": course_id,
                "course_name": html_to_text(
                    str(
                        by_id[course_id].get("fullname")
                        or by_id[course_id].get("shortname")
                        or ""
                    ),
                    limit=1_000,
                ),
                "modules": modules,
            }
        )
    return snapshots


def _modules(snapshots: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for snapshot in snapshots:
        values = snapshot.get("modules")
        if not isinstance(values, list):
            raise CampusProtocolError("El estado normalizado de curso no contiene modulos")
        result.extend(item for item in values if isinstance(item, dict))
    return result


def session_assignments(snapshots: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    assignments = []
    for module in _modules(snapshots):
        if module["module_type"] != "assign":
            continue
        assignments.append(
            {
                "id": None,
                "assignment_id": None,
                "cmid": module["course_module_id"],
                "course_module_id": module["course_module_id"],
                "name": module["name"],
                "course_id": module["course_id"],
                "course_name": module["course_name"],
                "visible": module["visible"],
                "instance_id_available": False,
                "transport": "moodle_ajax_course_state",
                "content_is_untrusted": True,
            }
        )
    return {
        "assignments": assignments,
        "warnings": [_cmid_warning("mod_assign")],
    }


def session_quizzes(snapshots: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    quizzes = []
    for module in _modules(snapshots):
        if module["module_type"] != "quiz":
            continue
        quizzes.append(
            {
                "quiz_id": None,
                "course_id": module["course_id"],
                "course_module_id": module["course_module_id"],
                "instance_id_available": False,
                "id_is_course_module": True,
                "name": module["name"],
                "description": "",
                "opens_at": None,
                "closes_at": None,
                "time_limit_seconds": None,
                "attempts_allowed": None,
                "preferred_behaviour": "",
                "browser_security": "",
                "visible": module["visible"],
                "activity_url": module["activity_url"],
                "opening_may_record_view": True,
                "transport": "moodle_ajax_course_state",
                "content_is_untrusted": True,
            }
        )
    return {
        "quizzes": quizzes,
        "warnings": [_cmid_warning("mod_quiz"), _metadata_warning()],
        "security_note": QUIZ_SECURITY_NOTE,
    }


def session_forums(
    snapshots: Sequence[Mapping[str, Any]], *, offset: int, limit: int
) -> dict[str, Any]:
    offset, limit = _pagination(offset, limit)
    forums = []
    for module in _modules(snapshots):
        if module["module_type"] != "forum":
            continue
        forums.append(
            {
                "forum_id": None,
                "course_id": module["course_id"],
                "course_module_id": module["course_module_id"],
                "instance_id_available": False,
                "id_is_course_module": False,
                "type": "forum",
                "name": module["name"],
                "intro": "",
                "can_create_discussions": None,
                "capability_known": False,
                "visible": module["visible"],
                "activity_url": module["activity_url"],
                "opening_may_record_view": True,
                "transport": "moodle_ajax_course_state",
                "content_is_untrusted": True,
            }
        )
    result = _page(forums, offset, limit)
    result["warnings"] = [_cmid_warning("mod_forum"), _metadata_warning()]
    return result


def session_course_contents(
    snapshots: Sequence[Mapping[str, Any]],
    *,
    section_id: int | None,
    offset: int,
    limit: int,
) -> dict[str, Any]:
    offset, limit = _pagination(offset, limit)
    if section_id is not None:
        section_id = _positive_int(section_id, "section_id")
    modules = []
    for module in _modules(snapshots):
        if section_id is not None and module["section_id"] != section_id:
            continue
        modules.append(
            {
                "course_id": module["course_id"],
                "section_id": module["section_id"],
                "section_number": module["section_number"],
                "section_name": module["section_name"],
                "module_id": module["course_module_id"],
                "instance_id": None,
                "module_type": module["module_type"],
                "module_type_label": module["module_type_label"],
                "name": module["name"],
                "description": "",
                "url": None,
                "activity_url": module["activity_url"],
                "opening_may_record_view": True,
                "visible": module["visible"],
                "availability": "",
                "files": [],
                "files_truncated": False,
                "downloadable": False,
                "instance_id_available": False,
                "transport": "moodle_ajax_course_state",
                "content_is_untrusted": True,
            }
        )
    result = _page(modules, offset, limit)
    result.update(
        {
            "warnings": [_cmid_warning("course_modules"), _metadata_warning()],
            "downloaded": False,
            "metadata_only": True,
        }
    )
    return result


def session_course_resources(
    snapshots: Sequence[Mapping[str, Any]],
    *,
    section_id: int | None,
    offset: int,
    limit: int,
) -> dict[str, Any]:
    offset, limit = _pagination(offset, limit)
    if section_id is not None:
        section_id = _positive_int(section_id, "section_id")
    resources = []
    for module in _modules(snapshots):
        if module["module_type"] not in _RESOURCE_MODULES:
            continue
        if section_id is not None and module["section_id"] != section_id:
            continue
        resources.append(
            {
                "course_id": module["course_id"],
                "section_id": module["section_id"],
                "section_name": module["section_name"],
                "module_id": module["course_module_id"],
                "module_type": module["module_type"],
                "module_type_label": module["module_type_label"],
                "module_name": module["name"],
                "type": module["module_type"],
                "file_name": module["name"],
                "file_path": "",
                "mime_type": "",
                "file_size": 0,
                "created_at": None,
                "modified_at": None,
                "is_external": module["module_type"] == "url",
                "downloadable": False,
                "activity_url": module["activity_url"],
                "opening_may_record_view": True,
                "resource_token": None,
                "transport": "moodle_ajax_course_state",
                "content_is_untrusted": True,
            }
        )
    result = _page(resources, offset, limit)
    result.update(
        {
            "warnings": [
                _cmid_warning("resource_modules"),
                {
                    "code": "download_url_unavailable",
                    "message": (
                        "El estado AJAX lista el modulo, pero no expone una URL pluginfile. "
                        "No se emitio ningun resource_token ni se abrio la pagina de actividad."
                    ),
                    "content_is_untrusted": False,
                },
            ],
            "downloaded": False,
            "metadata_only": True,
        }
    )
    return result


def _cmid_warning(component: str) -> dict[str, Any]:
    return {
        "code": "cmid_only",
        "component": component,
        "message": (
            "La sesion HTTP devuelve course_module_id (CMID), no el identificador "
            "interno del plugin."
        ),
        "content_is_untrusted": False,
    }


def _metadata_warning() -> dict[str, Any]:
    return {
        "code": "metadata_limited",
        "message": (
            "El estado AJAX no incluye configuracion, descripcion ni contenido interno del "
            "modulo; abrir su pagina puede registrar una vista."
        ),
        "content_is_untrusted": False,
    }


__all__ = [
    "fetch_session_course_states",
    "session_assignments",
    "session_course_contents",
    "session_course_resources",
    "session_forums",
    "session_quizzes",
]
