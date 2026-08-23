from __future__ import annotations

import asyncio
import base64
import re
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from .security import html_to_text

AssignmentInvoke = Callable[[str, Mapping[str, Any]], Awaitable[Any]]

DEFAULT_MAX_FILE_BYTES = 25 * 1024 * 1024
DEFAULT_MAX_TOTAL_BYTES = 100 * 1024 * 1024
MAX_FILES_PER_OPERATION = 20
MAX_ONLINE_TEXT_BYTES = 1 * 1024 * 1024

_REQUEST_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
_SENSITIVE_KEYS = frozenset({"access_token", "apikey", "password", "sesskey", "token", "wstoken"})


class AssignmentOperationError(RuntimeError):
    """Moodle returned a response that cannot be safely interpreted."""


class InvocationParams(dict[str, Any]):
    """Official Moodle parameters plus local correlation metadata.

    ``client_request_id`` is an attribute rather than a mapping key, so an HTTP
    adapter can log or deduplicate it without accidentally sending an unknown
    parameter to Moodle's strict external-function validator.
    """

    __slots__ = ("client_request_id",)

    def __init__(self, values: Mapping[str, Any], client_request_id: str) -> None:
        super().__init__(values)
        self.client_request_id = client_request_id


def _positive_id(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} debe ser un entero positivo")
    return value


def _client_request_id(value: str | None) -> str:
    request_id = value or uuid.uuid4().hex
    if not isinstance(request_id, str) or not _REQUEST_ID.fullmatch(request_id):
        raise ValueError(
            "client_request_id debe tener entre 1 y 128 caracteres alfanuméricos, "
            "punto, guion, guion bajo o dos puntos"
        )
    return request_id


def _child_request_id(parent: str, operation: str) -> str:
    suffix = re.sub(r"[^A-Za-z0-9._:-]", "-", operation)[:40]
    available = 128 - len(suffix) - 1
    return f"{parent[:available]}:{suffix}"


def _mutation_params(values: Mapping[str, Any], request_id: str) -> InvocationParams:
    return InvocationParams(values, request_id)


def _warnings(payload: Any) -> list[dict[str, Any]]:
    raw: Any = payload
    if isinstance(payload, Mapping):
        raw = payload.get("warnings", [])
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise AssignmentOperationError("Moodle devolvió avisos con un formato inesperado.")
    warnings: list[dict[str, Any]] = []
    for warning in raw:
        if not isinstance(warning, Mapping):
            continue
        warnings.append(
            {
                "code": str(warning.get("warningcode") or warning.get("code") or "warning")[:100],
                "message": html_to_text(str(warning.get("message") or ""), limit=2_000),
                "item_id": warning.get("itemid"),
                "content_is_untrusted": True,
            }
        )
    return warnings


def _sanitise_remote(value: Any, depth: int = 0) -> Any:
    if depth > 5:
        return None
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, child in list(value.items())[:200]:
            key = str(raw_key)[:200]
            if key.casefold() in _SENSITIVE_KEYS:
                continue
            result[key] = _sanitise_remote(child, depth + 1)
        result.setdefault("content_is_untrusted", True)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_sanitise_remote(child, depth + 1) for child in list(value)[:500]]
    if isinstance(value, str):
        return html_to_text(value, limit=12_000)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return html_to_text(str(value), limit=2_000)


def _warning_result(
    *, action: str, assignment_id: int, request_id: str, payload: Any
) -> dict[str, Any]:
    warnings = _warnings(payload)
    result: dict[str, Any] = {
        "ok": not warnings,
        "action": action,
        "assignment_id": assignment_id,
        "client_request_id": request_id,
        "warnings": warnings,
    }
    if warnings:
        result.update(
            {
                "code": warnings[0]["code"],
                "reason": warnings[0]["message"] or "Moodle no permitió completar la operación.",
            }
        )
    return result


def _flat_assignments(payload: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not isinstance(payload, Mapping) or not isinstance(payload.get("courses"), list):
        raise AssignmentOperationError("Moodle devolvió las tareas con un formato inesperado.")
    assignments: list[dict[str, Any]] = []
    for raw_course in payload["courses"]:
        if not isinstance(raw_course, Mapping):
            continue
        course_id = raw_course.get("id")
        course_name = raw_course.get("fullname") or raw_course.get("shortname") or ""
        raw_assignments = raw_course.get("assignments", [])
        if not isinstance(raw_assignments, list):
            continue
        for raw_assignment in raw_assignments:
            if not isinstance(raw_assignment, Mapping):
                continue
            item = _sanitise_remote(raw_assignment)
            item["course_id"] = course_id
            item["course_name"] = html_to_text(str(course_name), limit=1_000)
            item["content_is_untrusted"] = True
            assignments.append(item)
    return assignments, _warnings(payload)


async def list_assignments(
    invoke: AssignmentInvoke, course_ids: Sequence[int] | None = None
) -> dict[str, Any]:
    """List visible Moodle assignments, flattened but otherwise lossless."""

    ids: list[int] = []
    for course_id in course_ids or ():
        validated = _positive_id(course_id, "course_id")
        if validated not in ids:
            ids.append(validated)
    if len(ids) > 100:
        raise ValueError("No se pueden consultar más de 100 cursos a la vez")
    payload = await invoke(
        "mod_assign_get_assignments",
        {
            "courseids": ids,
            "capabilities": [],
            "includenotenrolledcourses": False,
        },
    )
    assignments, warnings = _flat_assignments(payload)
    return {"assignments": assignments, "warnings": warnings}


def _submission_from_attempt(attempt: Mapping[str, Any]) -> dict[str, Any] | None:
    for key in ("submission", "teamsubmission"):
        submission = attempt.get(key)
        if isinstance(submission, Mapping):
            return dict(submission)
    return None


def _normalise_status(assignment_id: int, payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise AssignmentOperationError("Moodle devolvió el estado con un formato inesperado.")
    attempt_value = payload.get("lastattempt")
    attempt = attempt_value if isinstance(attempt_value, Mapping) else {}
    submission = _submission_from_attempt(attempt)
    return {
        "assignment_id": assignment_id,
        "submission_status": submission.get("status") if submission else None,
        "attempt_number": submission.get("attemptnumber") if submission else None,
        "editable": bool(attempt.get("canedit", False)) and not bool(attempt.get("locked", False)),
        "can_submit": bool(attempt.get("cansubmit", False))
        and not bool(attempt.get("locked", False)),
        "submissions_enabled": bool(attempt.get("submissionsenabled", False)),
        "locked": bool(attempt.get("locked", False)),
        "graded": bool(attempt.get("graded", False)),
        "submission": _sanitise_remote(submission),
        "feedback": _sanitise_remote(payload["feedback"])
        if isinstance(payload.get("feedback"), Mapping)
        else None,
        "previous_attempts": _sanitise_remote(list(payload.get("previousattempts") or [])),
        "warnings": _warnings(payload),
        "content_is_untrusted": True,
    }


async def _raw_submission_status(invoke: AssignmentInvoke, assignment_id: int) -> Any:
    return await invoke(
        "mod_assign_get_submission_status",
        {"assignid": assignment_id, "userid": 0, "groupid": 0},
    )


async def get_submission_status(invoke: AssignmentInvoke, assignment_id: int) -> dict[str, Any]:
    assignment_id = _positive_id(assignment_id, "assignment_id")
    return _normalise_status(assignment_id, await _raw_submission_status(invoke, assignment_id))


def _not_editable_result(
    assignment_id: int, request_id: str, status: Mapping[str, Any], action: str
) -> dict[str, Any]:
    if status.get("locked"):
        code = "submission_locked"
        reason = "Moodle indica que la entrega está bloqueada."
    elif not status.get("submissions_enabled"):
        code = "submissions_disabled"
        reason = "Moodle indica que las entregas no están habilitadas."
    else:
        code = "submission_not_editable"
        reason = "Moodle no permite al alumno editar o eliminar esta entrega."
    return {
        "ok": False,
        "action": action,
        "assignment_id": assignment_id,
        "client_request_id": request_id,
        "code": code,
        "reason": reason,
        "status": dict(status),
        "warnings": [],
    }


def submission_plugin_safety(
    status: Mapping[str, Any], requested_plugins: set[str]
) -> dict[str, Any]:
    """Refuse save_submission unless every enabled plugin is represented safely."""

    submission = status.get("submission")
    raw_plugins = submission.get("plugins") if isinstance(submission, Mapping) else None
    if not isinstance(raw_plugins, list) or not raw_plugins:
        return {
            "safe": False,
            "code": "submission_plugins_unknown",
            "reason": (
                "Moodle no informó qué complementos de entrega están activos; no es seguro "
                "guardar solo una parte de la entrega."
            ),
            "enabled_plugins": [],
        }
    enabled = {
        str(plugin.get("type") or "").strip().casefold()
        for plugin in raw_plugins
        if isinstance(plugin, Mapping) and plugin.get("type")
    }
    if enabled != requested_plugins:
        return {
            "safe": False,
            "code": "unsupported_submission_plugins",
            "reason": (
                "La entrega activa complementos que esta operación no puede conservar de "
                "forma atómica. Usa el formulario HTTP completo o modifica cada complemento "
                "en una única operación compatible."
            ),
            "enabled_plugins": sorted(enabled),
        }
    return {"safe": True, "enabled_plugins": sorted(enabled)}


def _unsafe_plugins_result(
    assignment_id: int,
    request_id: str,
    status: Mapping[str, Any],
    action: str,
    requested_plugins: set[str],
) -> dict[str, Any] | None:
    safety = submission_plugin_safety(status, requested_plugins)
    if safety["safe"]:
        return None
    return {
        "ok": False,
        "action": action,
        "assignment_id": assignment_id,
        "client_request_id": request_id,
        "status": dict(status),
        "warnings": [],
        **safety,
    }


async def _editable_status(invoke: AssignmentInvoke, assignment_id: int) -> dict[str, Any]:
    return _normalise_status(assignment_id, await _raw_submission_status(invoke, assignment_id))


async def prepare_submission_draft(
    invoke: AssignmentInvoke, *, client_request_id: str | None = None
) -> dict[str, Any]:
    """Reserve a draft area owned by the authenticated Moodle user."""

    request_id = _client_request_id(client_request_id)
    payload = await invoke("core_files_get_unused_draft_itemid", _mutation_params({}, request_id))
    if not isinstance(payload, Mapping):
        raise AssignmentOperationError("Moodle no devolvió un área de borrador válida.")
    item_id = _positive_id(int(payload.get("itemid", 0)), "draft_item_id")
    context_id = _positive_id(int(payload.get("contextid", 0)), "context_id")
    user_id = _positive_id(int(payload.get("userid", 0)), "user_id")
    warnings = _warnings(payload)
    return {
        "ok": not warnings,
        "client_request_id": request_id,
        "draft_item_id": item_id,
        "context_id": context_id,
        "user_id": user_id,
        "component": "user",
        "file_area": "draft",
        "warnings": warnings,
    }


def _normalise_draft_path(value: str) -> str:
    if not isinstance(value, str) or "\x00" in value or "\\" in value:
        raise ValueError("draft_path no es válido")
    stripped = value.strip("/")
    raw_parts = stripped.split("/") if stripped else []
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise ValueError("draft_path no puede contener segmentos vacíos, '.' o '..'")
    path = PurePosixPath("/", *raw_parts)
    for part in path.parts[1:]:
        if any(ord(character) < 32 or character == ":" for character in part):
            raise ValueError("draft_path contiene caracteres no permitidos")
    normalised = path.as_posix()
    return normalised if normalised.endswith("/") else f"{normalised}/"


def _validated_local_file(
    file_path: str | Path, allowed_root: str | Path, max_bytes: int
) -> tuple[Path, int]:
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError("max_bytes debe ser un entero positivo")
    try:
        root = Path(allowed_root).expanduser().resolve(strict=True)
    except OSError as exc:
        raise ValueError("allowed_root no existe o no se puede resolver") from exc
    if not root.is_dir():
        raise ValueError("allowed_root debe ser un directorio")
    supplied = Path(file_path).expanduser()
    candidate = supplied if supplied.is_absolute() else root / supplied
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ValueError("El archivo no existe o no se puede resolver") from exc
    if not resolved.is_relative_to(root):
        raise ValueError("El archivo debe estar dentro de allowed_root")
    if not resolved.is_file():
        raise ValueError("La ruta indicada no es un archivo regular")
    if candidate.is_symlink():
        raise ValueError("No se permiten enlaces simbólicos como archivos de entrega")
    filename = resolved.name
    if (
        filename in {"", ".", ".."}
        or any(ord(character) < 32 or character in "/\\:" for character in filename)
        or len(filename.encode("utf-8")) > 255
    ):
        raise ValueError("El nombre del archivo no es válido para Moodle")
    size = resolved.stat().st_size
    if size > max_bytes:
        raise ValueError(f"El archivo supera el límite de {max_bytes} bytes")
    return resolved, size


def _draft_ids(draft: Mapping[str, Any]) -> tuple[int, int, int]:
    try:
        item_id = int(draft.get("draft_item_id") or draft.get("itemid") or 0)
        context_id = int(draft.get("context_id") or draft.get("contextid") or 0)
        user_id = int(draft.get("user_id") or draft.get("userid") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("El área de borrador no contiene identificadores válidos") from exc
    return (
        _positive_id(item_id, "draft_item_id"),
        _positive_id(context_id, "context_id"),
        _positive_id(user_id, "user_id"),
    )


async def upload_draft_file(
    invoke: AssignmentInvoke,
    draft: Mapping[str, Any],
    file_path: str | Path,
    *,
    allowed_root: str | Path,
    draft_path: str = "/",
    max_bytes: int = DEFAULT_MAX_FILE_BYTES,
    client_request_id: str | None = None,
) -> dict[str, Any]:
    """Upload one validated local file into the current user's draft area."""

    request_id = _client_request_id(client_request_id)
    item_id, context_id, user_id = _draft_ids(draft)
    path, size = _validated_local_file(file_path, allowed_root, max_bytes)
    moodle_path = _normalise_draft_path(draft_path)
    try:
        content = await asyncio.to_thread(path.read_bytes)
    except OSError as exc:
        raise ValueError("No se pudo leer el archivo de entrega") from exc
    if len(content) > max_bytes:
        raise ValueError(f"El archivo supera el límite de {max_bytes} bytes")
    payload = await invoke(
        "core_files_upload",
        _mutation_params(
            {
                "contextid": context_id,
                "component": "user",
                "filearea": "draft",
                "itemid": item_id,
                "filepath": moodle_path,
                "filename": path.name,
                "filecontent": base64.b64encode(content).decode("ascii"),
                "contextlevel": "user",
                "instanceid": user_id,
            },
            request_id,
        ),
    )
    if isinstance(payload, list) and len(payload) == 1:
        payload = payload[0]
    if not isinstance(payload, Mapping):
        raise AssignmentOperationError("Moodle no confirmó la subida del archivo de borrador.")
    warnings = _warnings(payload)
    upload_error = html_to_text(str(payload.get("error") or ""), limit=2_000)
    if upload_error:
        warnings.append(
            {
                "item": "file",
                "item_id": item_id,
                "code": html_to_text(str(payload.get("errortype") or "upload_failed"), limit=200),
                "message": upload_error,
                "content_is_untrusted": True,
            }
        )
    return {
        "ok": not warnings,
        "client_request_id": request_id,
        "draft_item_id": int(payload.get("itemid") or item_id),
        "filename": str(payload.get("filename") or path.name),
        "draft_path": str(payload.get("filepath") or moodle_path),
        "size": size,
        "warnings": warnings,
    }


def _draft_file_spec(value: str | Mapping[str, Any]) -> dict[str, str]:
    if isinstance(value, Mapping):
        filename = str(value.get("filename") or "")
        filepath = _normalise_draft_path(str(value.get("filepath") or "/"))
    elif isinstance(value, str):
        if "\x00" in value or "\\" in value:
            raise ValueError("La ruta de borrador no es válida")
        raw_parts = value.strip("/").split("/")
        if any(part in {"", ".", ".."} for part in raw_parts):
            raise ValueError("La ruta de borrador no es válida")
        path = PurePosixPath(value)
        filename = path.name
        parent = path.parent.as_posix()
        filepath = _normalise_draft_path("/" if parent == "." else parent)
    else:
        raise ValueError("Cada archivo debe ser una ruta o un mapping")
    if (
        not filename
        or filename in {".", ".."}
        or any(ord(character) < 32 or character in "/\\:" for character in filename)
    ):
        raise ValueError("El nombre del archivo de borrador no es válido")
    return {"filepath": filepath, "filename": filename}


async def delete_draft_files(
    invoke: AssignmentInvoke,
    draft_item_id: int,
    files: Sequence[str | Mapping[str, Any]],
    *,
    client_request_id: str | None = None,
) -> dict[str, Any]:
    """Delete selected files only from the authenticated user's draft area."""

    request_id = _client_request_id(client_request_id)
    draft_item_id = _positive_id(draft_item_id, "draft_item_id")
    if isinstance(files, (str, bytes)) or not files:
        raise ValueError("files debe contener al menos un archivo")
    if len(files) > MAX_FILES_PER_OPERATION:
        raise ValueError(f"No se pueden borrar más de {MAX_FILES_PER_OPERATION} archivos")
    specs = [_draft_file_spec(file) for file in files]
    payload = await invoke(
        "core_files_delete_draft_files",
        _mutation_params({"draftitemid": draft_item_id, "files": specs}, request_id),
    )
    if not isinstance(payload, Mapping):
        raise AssignmentOperationError("Moodle no confirmó el borrado del borrador.")
    warnings = _warnings(payload)
    return {
        "ok": not warnings,
        "client_request_id": request_id,
        "draft_item_id": draft_item_id,
        "deleted": specs if not warnings else [],
        "parent_paths": list(payload.get("parentpaths") or []),
        "warnings": warnings,
    }


def _online_text(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("online_text debe ser una cadena")
    if "\x00" in value:
        raise ValueError("online_text contiene un carácter nulo")
    if len(value.encode("utf-8")) > MAX_ONLINE_TEXT_BYTES:
        raise ValueError(f"online_text supera el límite local de {MAX_ONLINE_TEXT_BYTES} bytes")
    return value


async def _save_submission_unchecked(
    invoke: AssignmentInvoke,
    assignment_id: int,
    plugindata: Mapping[str, Any],
    request_id: str,
) -> dict[str, Any]:
    payload = await invoke(
        "mod_assign_save_submission",
        _mutation_params(
            {"assignmentid": assignment_id, "plugindata": dict(plugindata)}, request_id
        ),
    )
    return _warning_result(
        action="save_submission",
        assignment_id=assignment_id,
        request_id=request_id,
        payload=payload,
    )


async def save_submission(
    invoke: AssignmentInvoke,
    assignment_id: int,
    *,
    online_text: str | None = None,
    draft_item_id: int | None = None,
    client_request_id: str | None = None,
) -> dict[str, Any]:
    """Save editable online text and/or the complete draft file set."""

    assignment_id = _positive_id(assignment_id, "assignment_id")
    request_id = _client_request_id(client_request_id)
    if online_text is None and draft_item_id is None:
        raise ValueError("Debes proporcionar online_text, draft_item_id o ambos")
    if draft_item_id is not None:
        draft_item_id = _positive_id(draft_item_id, "draft_item_id")
    plugindata: dict[str, Any] = {}
    if online_text is not None:
        plugindata["onlinetext_editor"] = {
            "text": _online_text(online_text),
            "format": 2,
            "itemid": draft_item_id or 0,
        }
    if draft_item_id is not None:
        plugindata["files_filemanager"] = draft_item_id
    status = await _editable_status(invoke, assignment_id)
    if not status["editable"]:
        return _not_editable_result(assignment_id, request_id, status, "save_submission")
    requested_plugins = set()
    if online_text is not None:
        requested_plugins.add("onlinetext")
    if draft_item_id is not None:
        requested_plugins.add("file")
    unsafe = _unsafe_plugins_result(
        assignment_id,
        request_id,
        status,
        "save_submission",
        requested_plugins,
    )
    if unsafe:
        return unsafe
    return await _save_submission_unchecked(invoke, assignment_id, plugindata, request_id)


async def submit_for_grading(
    invoke: AssignmentInvoke,
    assignment_id: int,
    *,
    accept_submission_statement: bool,
    client_request_id: str | None = None,
) -> dict[str, Any]:
    """Finalize a draft after Moodle reports that the current user may submit."""

    assignment_id = _positive_id(assignment_id, "assignment_id")
    if not isinstance(accept_submission_statement, bool):
        raise ValueError("accept_submission_statement debe ser booleano")
    request_id = _client_request_id(client_request_id)
    status = await _editable_status(invoke, assignment_id)
    if not status["can_submit"]:
        result = _not_editable_result(assignment_id, request_id, status, "submit_for_grading")
        result["code"] = (
            "already_submitted"
            if status.get("submission_status") == "submitted"
            else "cannot_submit"
        )
        result["reason"] = "Moodle no permite enviar esta entrega para calificación."
        return result
    payload = await invoke(
        "mod_assign_submit_for_grading",
        _mutation_params(
            {
                "assignmentid": assignment_id,
                "acceptsubmissionstatement": accept_submission_statement,
            },
            request_id,
        ),
    )
    return _warning_result(
        action="submit_for_grading",
        assignment_id=assignment_id,
        request_id=request_id,
        payload=payload,
    )


def _validated_file_batch(
    files: Sequence[str | Path],
    allowed_root: str | Path,
    max_file_bytes: int,
    max_total_bytes: int,
) -> list[tuple[Path, int]]:
    if isinstance(files, (str, bytes)) or not files:
        raise ValueError("files debe contener al menos un archivo")
    if len(files) > MAX_FILES_PER_OPERATION:
        raise ValueError(f"No se pueden subir más de {MAX_FILES_PER_OPERATION} archivos")
    if (
        isinstance(max_total_bytes, bool)
        or not isinstance(max_total_bytes, int)
        or max_total_bytes <= 0
    ):
        raise ValueError("max_total_bytes debe ser un entero positivo")
    validated = [_validated_local_file(file, allowed_root, max_file_bytes) for file in files]
    names = [path.name.casefold() for path, _ in validated]
    if len(names) != len(set(names)):
        raise ValueError("No se permiten nombres de archivo duplicados en la misma entrega")
    if sum(size for _, size in validated) > max_total_bytes:
        raise ValueError(f"Los archivos superan el límite total de {max_total_bytes} bytes")
    return validated


async def replace_submission_files(
    invoke: AssignmentInvoke,
    assignment_id: int,
    files: Sequence[str | Path],
    *,
    allowed_root: str | Path,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
    client_request_id: str | None = None,
) -> dict[str, Any]:
    """Replace the complete submitted-file set, never individual files in place."""

    assignment_id = _positive_id(assignment_id, "assignment_id")
    request_id = _client_request_id(client_request_id)
    validated = _validated_file_batch(files, allowed_root, max_file_bytes, max_total_bytes)
    status = await _editable_status(invoke, assignment_id)
    if not status["editable"]:
        return _not_editable_result(assignment_id, request_id, status, "replace_submission_files")
    unsafe = _unsafe_plugins_result(
        assignment_id,
        request_id,
        status,
        "replace_submission_files",
        {"file"},
    )
    if unsafe:
        return unsafe
    draft = await prepare_submission_draft(
        invoke, client_request_id=_child_request_id(request_id, "draft")
    )
    if not draft["ok"]:
        return {
            "ok": False,
            "action": "replace_submission_files",
            "assignment_id": assignment_id,
            "client_request_id": request_id,
            "code": "draft_failed",
            "reason": "Moodle no pudo preparar el área de borrador.",
            "warnings": draft["warnings"],
        }
    uploaded: list[dict[str, Any]] = []
    for index, (path, _) in enumerate(validated):
        upload = await upload_draft_file(
            invoke,
            draft,
            path,
            allowed_root=allowed_root,
            max_bytes=max_file_bytes,
            client_request_id=_child_request_id(request_id, f"upload-{index}"),
        )
        if not upload["ok"]:
            return {
                "ok": False,
                "action": "replace_submission_files",
                "assignment_id": assignment_id,
                "client_request_id": request_id,
                "code": "upload_failed",
                "reason": "Moodle no pudo guardar todos los archivos en el borrador.",
                "draft_item_id": draft["draft_item_id"],
                "uploaded": uploaded,
                "warnings": upload["warnings"],
            }
        uploaded.append(upload)
    saved = await _save_submission_unchecked(
        invoke,
        assignment_id,
        {"files_filemanager": draft["draft_item_id"]},
        _child_request_id(request_id, "save"),
    )
    return {
        **saved,
        "action": "replace_submission_files",
        "client_request_id": request_id,
        "draft_item_id": draft["draft_item_id"],
        "uploaded": uploaded,
    }


async def delete_submission_files(
    invoke: AssignmentInvoke,
    assignment_id: int,
    *,
    client_request_id: str | None = None,
) -> dict[str, Any]:
    """Delete all files while leaving other enabled submission plugins untouched."""

    assignment_id = _positive_id(assignment_id, "assignment_id")
    request_id = _client_request_id(client_request_id)
    status = await _editable_status(invoke, assignment_id)
    if not status["editable"]:
        return _not_editable_result(assignment_id, request_id, status, "delete_submission_files")
    unsafe = _unsafe_plugins_result(
        assignment_id,
        request_id,
        status,
        "delete_submission_files",
        {"file"},
    )
    if unsafe:
        return unsafe
    draft = await prepare_submission_draft(
        invoke, client_request_id=_child_request_id(request_id, "empty-draft")
    )
    if not draft["ok"]:
        return {
            "ok": False,
            "action": "delete_submission_files",
            "assignment_id": assignment_id,
            "client_request_id": request_id,
            "code": "draft_failed",
            "reason": "Moodle no pudo preparar un borrador vacío.",
            "warnings": draft["warnings"],
        }
    saved = await _save_submission_unchecked(
        invoke,
        assignment_id,
        {"files_filemanager": draft["draft_item_id"]},
        _child_request_id(request_id, "save-empty-files"),
    )
    return {
        **saved,
        "action": "delete_submission_files",
        "client_request_id": request_id,
        "deleted_all_files": bool(saved["ok"]),
        "draft_item_id": draft["draft_item_id"],
    }


async def remove_entire_submission(
    invoke: AssignmentInvoke,
    assignment_id: int,
    user_id: int,
    *,
    client_request_id: str | None = None,
) -> dict[str, Any]:
    """Use Moodle 4.5+'s destructive removal only when `canedit` is true."""

    assignment_id = _positive_id(assignment_id, "assignment_id")
    user_id = _positive_id(user_id, "user_id")
    request_id = _client_request_id(client_request_id)
    status = await _editable_status(invoke, assignment_id)
    if not status["editable"]:
        return _not_editable_result(assignment_id, request_id, status, "remove_entire_submission")
    payload = await invoke(
        "mod_assign_remove_submission",
        _mutation_params({"userid": user_id, "assignid": assignment_id}, request_id),
    )
    if not isinstance(payload, Mapping):
        raise AssignmentOperationError("Moodle no confirmó la eliminación de la entrega.")
    warnings = _warnings(payload)
    removed = bool(payload.get("status")) and not warnings
    return {
        "ok": removed,
        "action": "remove_entire_submission",
        "assignment_id": assignment_id,
        "client_request_id": request_id,
        "removed": removed,
        "code": None if removed else "remove_not_allowed",
        "reason": None
        if removed
        else "Moodle no permitió eliminar la entrega o no había contenido eliminable.",
        "warnings": warnings,
    }


async def reopen_submission(
    invoke: AssignmentInvoke,
    assignment_id: int,
    *,
    client_request_id: str | None = None,
) -> dict[str, Any]:
    """Report whether a student attempt is already editable; never impersonate a grader."""

    assignment_id = _positive_id(assignment_id, "assignment_id")
    request_id = _client_request_id(client_request_id)
    status = await _editable_status(invoke, assignment_id)
    if status["editable"]:
        return {
            "ok": True,
            "action": "reopen_submission",
            "assignment_id": assignment_id,
            "client_request_id": request_id,
            "already_editable": True,
            "mutated": False,
            "warnings": [],
        }
    return {
        "ok": False,
        "action": "reopen_submission",
        "assignment_id": assignment_id,
        "client_request_id": request_id,
        "mutated": False,
        "code": "reopen_requires_teacher",
        "reason": (
            "La API estándar solo permite al profesorado revertir una entrega a borrador. "
            "Eliminarla mediante Moodle 4.5+ destruiría su contenido y no es una reapertura."
        ),
        "status": status,
        "warnings": [],
    }
