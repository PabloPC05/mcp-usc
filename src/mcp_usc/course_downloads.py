"""Download a course's files into a local folder, safely and incrementally.

Two transports, one result:

- with a Moodle web-service token, files come from their direct ``pluginfile`` URLs and
  nothing is recorded in Moodle;
- with a browser session (every USC student), Moodle only reveals a file when its
  activity is opened, which records the view exactly like clicking it on the web, so
  callers must ask the user first.

Files land in ``<root>/<course>/<section>/<file>``. A small manifest per course keeps
track of what was already downloaded, so repeating a download only fetches new items.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .campus import CampusError
from .collaboration import list_downloadable_resources
from .domain import MADRID
from .session_course_state import fetch_session_course_states, session_course_resources

MANIFEST_NAME = ".mcp-usc-descargas.json"
MAX_FILE_BYTES = 100 * 1024 * 1024
_SESSION_DOWNLOADABLE = frozenset({"resource", "folder"})
_UNSAFE = re.compile(r'[\x00-\x1f\x7f<>:"/\\|?*]')
_COURSE_SUFFIX = re.compile(r"\s*\[(?:G\d{7}[A-Z]?|\d{4}/\d{4})\]", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class DownloadItem:
    """One file (token transport) or one activity to open (session transport)."""

    key: str
    module_id: int
    module_type: str
    name: str
    section_name: str
    url: str | None = None
    file_name: str | None = None
    subfolder: str = ""
    size: int | None = None

    def public_dict(self) -> dict[str, Any]:
        return {
            "module_id": self.module_id,
            "module_type": self.module_type,
            "name": self.name,
            "section_name": self.section_name,
            "file_name": self.file_name,
            "size": self.size,
            "opens_activity": self.url is None,
            "content_is_untrusted": True,
        }


def safe_component(value: str, *, fallback: str, limit: int = 120) -> str:
    """A single, portable path component (no separators, no reserved characters)."""

    text = unicodedata.normalize("NFC", str(value or ""))
    text = _UNSAFE.sub("_", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    if text.upper() in {"CON", "PRN", "AUX", "NUL"} or not text:
        text = fallback
    return text[:limit].rstrip(" .") or fallback


def course_folder_name(course_name: str, course_id: int) -> str:
    """«Intelixencia Artificial [G4012328] [2026/2027]» → «Intelixencia Artificial»."""

    return safe_component(_COURSE_SUFFIX.sub("", course_name), fallback=f"curso-{course_id}")


async def _all_pages(fetch: Callable[[int], Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    offset = 0
    for _ in range(100):
        page = await fetch(offset)
        items.extend(page.get("items", []))
        if not page.get("has_more"):
            break
        offset = int(page.get("next_offset") or offset + len(page.get("items", [])))
    return items


async def list_download_items(
    gateway: Any, course_id: int, *, session: bool
) -> tuple[list[DownloadItem], dict[str, int]]:
    """Everything downloadable in a course, plus a count of what cannot be downloaded."""

    skipped: dict[str, int] = {}
    items: list[DownloadItem] = []
    if session:
        states = await fetch_session_course_states(gateway, [course_id])

        async def fetch(offset: int) -> dict[str, Any]:
            return session_course_resources(states, section_id=None, offset=offset, limit=100)

        for module in await _all_pages(fetch):
            module_type = str(module.get("module_type") or "")
            if module_type not in _SESSION_DOWNLOADABLE:
                skipped[module_type or "otro"] = skipped.get(module_type or "otro", 0) + 1
                continue
            items.append(
                DownloadItem(
                    key=str(module["module_id"]),
                    module_id=int(module["module_id"]),
                    module_type=module_type,
                    name=str(module.get("module_name") or ""),
                    section_name=str(module.get("section_name") or ""),
                )
            )
        return items, skipped

    async def fetch_rest(offset: int) -> dict[str, Any]:
        return await list_downloadable_resources(
            gateway.invoke, course_id=course_id, offset=offset, limit=50
        )

    for file in await _all_pages(fetch_rest):
        if file.get("is_external") or not file.get("url"):
            skipped["enlace"] = skipped.get("enlace", 0) + 1
            continue
        path = str(file.get("file_path") or "/").strip("/")
        items.append(
            DownloadItem(
                key=f"{file['module_id']}:{path}/{file.get('file_name')}",
                module_id=int(file["module_id"]),
                module_type=str(file.get("module_type") or ""),
                name=str(file.get("module_name") or file.get("file_name") or ""),
                section_name=str(file.get("section_name") or ""),
                url=str(file["url"]),
                file_name=str(file.get("file_name") or ""),
                subfolder=path,
                size=int(file.get("file_size") or 0) or None,
            )
        )
    return items, skipped


def _load_manifest(folder: Path) -> dict[str, Any]:
    try:
        data = json.loads((folder / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": 1, "items": {}}
    if not isinstance(data, dict) or not isinstance(data.get("items"), dict):
        return {"version": 1, "items": {}}
    return data


def _save_manifest(folder: Path, manifest: dict[str, Any]) -> None:
    temporary = folder / f"{MANIFEST_NAME}.tmp"
    temporary.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, folder / MANIFEST_NAME)


def _unique_path(directory: Path, file_name: str, taken: set[Path]) -> Path:
    stem, dot, suffix = file_name.rpartition(".")
    if not dot:
        stem, suffix = file_name, ""
    candidate = directory / file_name
    counter = 2
    while candidate in taken or candidate.exists():
        numbered = f"{stem} ({counter})"
        candidate = directory / (f"{numbered}.{suffix}" if suffix else numbered)
        counter += 1
    return candidate


def _inside(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


async def download_items(
    gateway: Any,
    items: Sequence[DownloadItem],
    folder: Path,
    *,
    force: bool = False,
    progress: Callable[[DownloadItem, str], None] | None = None,
) -> dict[str, Any]:
    """Download ``items`` into ``folder`` and report what happened to each one."""

    folder.mkdir(parents=True, exist_ok=True)
    manifest = _load_manifest(folder)
    known: dict[str, Any] = manifest["items"]
    sections: dict[str, str] = {}
    taken: set[Path] = set()
    downloaded: list[dict[str, Any]] = []
    already: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for item in items:
        entry = known.get(item.key)
        if entry and not force and (folder / str(entry.get("path", ""))).is_file():
            already.append({**item.public_dict(), "path": entry["path"]})
            if progress:
                progress(item, "ya estaba")
            continue
        section = sections.setdefault(
            item.section_name,
            f"{len(sections) + 1:02d} "
            + safe_component(item.section_name, fallback="Sen sección", limit=80),
        )
        directory = folder / section
        for part in [segment for segment in item.subfolder.split("/") if segment]:
            directory = directory / safe_component(part, fallback="carpeta", limit=80)
        try:
            if item.url is not None:
                content, _, _ = await gateway.fetch_file(item.url, MAX_FILE_BYTES)
                file_name = item.file_name or item.name
            else:
                content, _, _, file_name = await gateway.open_activity_file(
                    item.module_id, item.module_type, MAX_FILE_BYTES
                )
        except (CampusError, ValueError) as exc:
            failed.append({**item.public_dict(), "error": str(exc)[:300]})
            if progress:
                progress(item, "error")
            continue
        directory.mkdir(parents=True, exist_ok=True)
        target = _unique_path(directory, safe_component(file_name, fallback="archivo"), taken)
        if not _inside(folder, target):
            failed.append({**item.public_dict(), "error": "Ruta de destino no permitida"})
            continue
        temporary = target.with_name(f".{target.name}.descargando")
        temporary.write_bytes(content)
        os.replace(temporary, target)
        taken.add(target)
        relative = target.relative_to(folder).as_posix()
        known[item.key] = {
            "path": relative,
            "size": len(content),
            "downloaded_at": datetime.now(MADRID).isoformat(timespec="seconds"),
        }
        _save_manifest(folder, manifest)
        downloaded.append({**item.public_dict(), "path": relative, "bytes": len(content)})
        if progress:
            progress(item, "descargado")
    return {
        "folder": str(folder),
        "downloaded": downloaded,
        "already_downloaded": already,
        "failed": failed,
        "content_is_untrusted": True,
    }


__all__ = [
    "MANIFEST_NAME",
    "DownloadItem",
    "course_folder_name",
    "download_items",
    "list_download_items",
    "safe_component",
]
