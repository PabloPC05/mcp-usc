"""Everyday terminal commands: the same service the MCP uses, printed for a person.

`mcp-usc pendientes`, `horario`, `examenes`, `cursos`, `materiales` and `descargar`.
Every command accepts --json for scripts. Nothing here writes to Moodle; `descargar`
asks before opening activities, because with a session Moodle records each opening.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .academic_profile import resolve_academic_context
from .domain import normalise_course
from .exam_catalog import extract_academic_year, extract_subject_code
from .service import UscService

_WEEKDAYS = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")
_ROMAN = frozenset({"i", "ii", "iii", "iv", "v", "vi"})
_STOPWORDS = frozenset(
    {"a", "de", "del", "da", "do", "das", "dos", "e", "en", "y", "la", "el", "o"}
)
_ACTIVITY = {
    "expository": "teoría",
    "laboratory": "laboratorio",
    "seminar": "seminario",
    "tutorship": "tutoría",
    "unspecified": "",
}
_COURSE_SUFFIX = re.compile(r"\s*\[(?:G\d{7}[A-Z]?|\d{4}/\d{4})\]", re.IGNORECASE)


class CliError(RuntimeError):
    """A message for the person at the terminal; exits with status 1."""


def _fold(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def short_course(name: str) -> str:
    return _COURSE_SUFFIX.sub("", name or "").strip()


def _when(value: str | None) -> str:
    if not value:
        return "sin fecha"
    moment = datetime.fromisoformat(value)
    return f"{_WEEKDAYS[moment.weekday()]} {moment:%d/%m %H:%M}"


def match_course(courses: list[dict[str, Any]], query: str, academic_year: str) -> dict[str, Any]:
    """Pick one course by id, G code or words of its name (current year first)."""

    text = query.strip()
    if text.isdigit():
        found = [course for course in courses if course["id"] == int(text)]
    elif (code := extract_subject_code(text)) is not None and code == text.upper():
        found = [
            course
            for course in courses
            if extract_subject_code(course.get("short_name"), course.get("full_name")) == code
        ]
    else:
        found = _match_by_name(courses, text)
    current = [
        course
        for course in found
        if extract_academic_year(course.get("short_name"), course.get("full_name"))
        in {None, academic_year}
    ]
    candidates = current or found
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise CliError(f"No encontré ninguna asignatura que coincida con «{query}».")
    options = "\n".join(f"  {course['id']:>7}  {course['full_name']}" for course in candidates)
    raise CliError(f"«{query}» coincide con varias asignaturas; indica el número:\n{options}")


def _name_tokens(course: dict[str, Any]) -> list[str]:
    name = _fold(short_course(str(course.get("full_name") or "")))
    return [token for token in re.split(r"[^0-9a-z]+", name) if token]


def _match_by_name(courses: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    """Whole words or initials first («ia», «soi», «asr»), then word prefixes, then text."""

    words = [word for word in re.split(r"[^0-9a-z]+", _fold(query)) if word]
    if not words:
        return []

    def initials(course: dict[str, Any]) -> str:
        # Roman numerals and digits count whole: «Sistemas Operativos II» is SOII, not SOI.
        return "".join(
            token if token in _ROMAN or token.isdigit() else token[0]
            for token in _name_tokens(course)
            if token not in _STOPWORDS
        )

    exact = [
        course
        for course in courses
        if all(word in _name_tokens(course) for word in words)
        or (len(words) == 1 and initials(course) == words[0])
    ]
    if exact:
        return exact
    prefix = [
        course
        for course in courses
        if all(any(token.startswith(word) for token in _name_tokens(course)) for word in words)
    ]
    if prefix:
        return prefix
    return [
        course
        for course in courses
        if all(word in " ".join(_name_tokens(course)) for word in words)
    ]


async def _courses(service: UscService) -> list[dict[str, Any]]:
    raw = await service._campus().list_courses(include_archived=True)  # noqa: SLF001
    return [normalise_course(course) for course in raw]


def _academic_year() -> str:
    return resolve_academic_context(None)[0]


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


async def pendientes(service: UscService, args: argparse.Namespace) -> None:
    items = await service.list_events(
        days=args.dias, include_overdue=args.atrasadas, course_ids=None, limit=50
    )
    if args.json:
        _print_json(items)
        return
    if not items:
        print(f"Nada pendiente en los próximos {args.dias} días.")
        return
    for item in items:
        print(f"{_when(item.get('sort_at')):>15}  {short_course(item.get('course_name') or '')}")
        print(f"{'':>15}  {item.get('name')}")


async def horario(service: UscService, args: argparse.Namespace) -> None:
    result = await service.get_my_subjects_timetable(
        date_in_week=args.semana, group_codes=args.grupos or None
    )
    if args.json:
        _print_json(result)
        return
    pending = [item for item in result["subjects"] if item["status"] != "included"]
    current = None
    for session in result["sessions"]:
        day = date.fromisoformat(str(session["date"]))
        if day != current:
            current = day
            print(f"\n{_WEEKDAYS[day.weekday()].capitalize()} {day:%d/%m}")
        kinds = str(session.get("activity_type") or "").split("+")
        kind = "+".join(filter(None, (_ACTIVITY.get(value, value) for value in kinds)))
        group = str(session.get("group_code") or "").lstrip("/")
        detail = ", ".join(filter(None, (kind, group)))
        print(
            f"  {session.get('start_time')}-{session.get('end_time')}  "
            f"{session.get('subject_name')}  ({detail})  {session.get('room') or ''}"
        )
    if not result["sessions"]:
        print("No hay clases publicadas esa semana para tus asignaturas.")
    for item in pending:
        print(f"\n· {item['subject_code']}: {item['status']}")


async def examenes(service: UscService, args: argparse.Namespace) -> None:
    result = await service.get_my_official_exam_schedule(args.curso_academico or _academic_year())
    if args.json:
        _print_json(result)
        return
    names = {
        course["subject_code"]: short_course(course["course_name"])
        for course in result.get("matched_moodle_courses", [])
    }
    rows = []
    for subject in result.get("subjects", []):
        calls = subject.get("calls") or []
        name = names.get(subject["subject_code"], subject["subject_code"])
        if not calls:
            rows.append(("9999", f"{name}: sin fechas ({subject.get('status')})"))
            continue
        dates = "; ".join(
            f"{_when(call.get('starts_at'))} {', '.join(call.get('rooms') or [])}".strip()
            for call in calls
        )
        rows.append((str(calls[0].get("starts_at")), f"{name}: {dates}"))
    for _, line in sorted(rows):
        print(line)
    if not rows:
        print("No encontré asignaturas de este curso académico en tu Campus.")


async def cursos(service: UscService, args: argparse.Namespace) -> None:
    courses = await _courses(service)
    year = _academic_year()
    if not args.todos:
        courses = [
            course
            for course in courses
            if extract_academic_year(course.get("short_name"), course.get("full_name"))
            in {None, year}
        ]
    if args.json:
        _print_json(courses)
        return
    for course in courses:
        print(f"{course['id']:>7}  {course['full_name']}")


async def materiales(service: UscService, args: argparse.Namespace) -> None:
    course = match_course(await _courses(service), args.asignatura, _academic_year())
    plan = await service.course_download_plan(course["id"])
    if args.json:
        _print_json([item.public_dict() for item in plan["items"]])
        return
    print(f"{course['full_name']}: {len(plan['items'])} archivos y carpetas\n")
    section = None
    for item in plan["items"]:
        if item.section_name != section:
            section = item.section_name
            print(section or "(sin sección)")
        kind = "carpeta" if item.module_type == "folder" else "archivo"
        print(f"  {item.module_id:>8}  {kind:8} {item.name}")
    if plan["skipped"]:
        others = ", ".join(f"{count} {kind}" for kind, count in sorted(plan["skipped"].items()))
        print(f"\nNo descargables (enlaces, páginas…): {others}")


async def descargar(
    service: UscService,
    args: argparse.Namespace,
    *,
    ask: Callable[[str], str] = input,
) -> None:
    course = match_course(await _courses(service), args.asignatura, _academic_year())
    plan = await service.course_download_plan(course["id"], args.solo or None)
    folder = Path(args.carpeta).expanduser() if args.carpeta else plan["folder"]
    total = len(plan["items"])
    print(f"{course['full_name']}: {total} archivos y carpetas → {folder}")
    if plan["records_views"] and not args.si:
        print(
            "Con tu sesión de alumno, Moodle solo entrega cada archivo al abrirlo: quedará "
            "registrado que abriste cada material nuevo, igual que al hacer clic en la web."
        )
        if not sys.stdin.isatty():
            raise CliError("Confirma con --si para descargar sin preguntar.")
        if ask("¿Descargar? [s/N] ").strip().casefold() not in {"s", "si", "sí", "y", "yes"}:
            print("No se ha descargado nada.")
            return

    def progress(item: Any, outcome: str) -> None:
        if not args.json and outcome != "ya estaba":
            print(f"  {outcome:>10}  {item.name}")

    result = await service.download_course_files_now(
        plan, folder=folder, force=args.forzar, progress=progress
    )
    if args.json:
        _print_json(result)
        return
    print(
        f"\nDescargados {len(result['downloaded'])}, ya estaban "
        f"{len(result['already_downloaded'])}, fallidos {len(result['failed'])}. "
        f"Carpeta: {result['folder']}"
    )
    for failure in result["failed"]:
        print(f"  ✗ {failure['name']}: {failure['error']}")


def add_parsers(subparsers: Any) -> None:
    def command(name: str, help_text: str) -> argparse.ArgumentParser:
        parser = subparsers.add_parser(name, help=help_text)
        parser.add_argument("--json", action="store_true", help="Salida en JSON")
        return parser

    parser = command("pendientes", "Entregas y cuestionarios próximos")
    parser.add_argument("--dias", type=int, default=14, help="Días hacia delante (por defecto 14)")
    parser.add_argument(
        "--atrasadas", action="store_true", help="Incluye lo atrasado de este curso académico"
    )
    parser = command("horario", "Tu horario de la semana, solo de tus asignaturas")
    parser.add_argument("--semana", help="Cualquier fecha de la semana (AAAA-MM-DD)")
    parser.add_argument(
        "--grupos", nargs="+", help="Solo estos grupos (p. ej. CLE_01 CLIL_03 CLIL_05)"
    )
    parser = command("examenes", "Fechas oficiales de examen de tus asignaturas")
    parser.add_argument("--curso-academico", help="Por defecto, el actual (p. ej. 2026/2027)")
    parser = command("cursos", "Tus asignaturas de este curso académico")
    parser.add_argument("--todos", action="store_true", help="Incluye cursos anteriores")
    parser = command("materiales", "Archivos y carpetas de una asignatura")
    parser.add_argument("asignatura", help="Número, código G o parte del nombre (p. ej. «ia»)")
    parser = command("descargar", "Descarga los archivos de una asignatura a una carpeta")
    parser.add_argument("asignatura", help="Número, código G o parte del nombre (p. ej. «redes»)")
    parser.add_argument("--carpeta", help="Carpeta de destino de esta asignatura")
    parser.add_argument("--solo", type=int, nargs="+", help="Solo estos números de material")
    parser.add_argument("--forzar", action="store_true", help="Vuelve a bajar lo ya descargado")
    parser.add_argument("--si", action="store_true", help="No preguntes antes de descargar")


COMMANDS = {
    "pendientes": pendientes,
    "horario": horario,
    "examenes": examenes,
    "cursos": cursos,
    "materiales": materiales,
    "descargar": descargar,
}
