from __future__ import annotations

import json
import re
import unicodedata
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal
from urllib.parse import unquote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, Tag

from .exam_catalog import normalise_subject_code
from .public_http_cache import (
    DEFAULT_PUBLIC_HTTP_CACHE,
    PublicHttpCache,
    PublicHttpCacheMetadata,
    PublicHttpError,
    PublicHttpResponse,
    SafePublicHttpFetcher,
)
from .security import UnsafeUrlError, validate_usc_url

_USER_AGENT = "mcp-usc/0.7 (+https://github.com/PabloPC05/mcp-usc)"
_ACADEMIC_YEAR = re.compile(r"^20(?P<start>\d{2})/20(?P<end>\d{2})$")
_PLAN_CLASS = re.compile(r"^is-type-(?P<plan_id>\d+)$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_ENCODED_SEPARATOR = re.compile(r"%(?:2f|5c)", re.IGNORECASE)
_AJAX_PATH = re.compile(r"^/(?:gl|es|en)/course/\d+/schedules-exams-calendar/\d+/?$")
_CALENDAR_PAGE_PATH = re.compile(
    r"^(?:/(?:gl|es)/centro/[^/]+/calendarios/convocatorias/?|"
    r"/en/center/[^/]+/calendars/(?:calls-proposals|exams)/?)$"
)
_SUBJECT_PAGE_PATH = re.compile(r"^/(?:gl/estudos|es/estudios|en/studies)/.+/20\d{6}/[^/]+/?$")


class ExamCalendarError(RuntimeError):
    """A public USC calendar could not be fetched or interpreted safely."""


class ExamCalendarParseError(ExamCalendarError):
    """The official page did not match the expected, fail-closed structure."""


class ExamCalendarSchemaChangedError(ExamCalendarParseError):
    """The official response changed shape and was rejected before caching."""


@dataclass(frozen=True, slots=True)
class ExamCall:
    semester: str
    opportunity: str
    date_time: str
    rooms: tuple[str, ...]
    groups: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExamSubject:
    plan_id: int
    name: str
    calls: tuple[ExamCall, ...]


@dataclass(frozen=True, slots=True)
class ExamCalendar:
    academic_year: str | None
    endpoint_url: str
    subjects: tuple[ExamSubject, ...]
    cache_metadata: tuple[PublicHttpCacheMetadata, ...] = ()


@dataclass(frozen=True, slots=True)
class SubjectExamSlot:
    date_time: str
    rooms: tuple[str, ...]
    groups: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SubjectSheet:
    source_url: str
    title: str
    academic_year: str | None
    semester: str | None
    exam_slots: tuple[SubjectExamSlot, ...]
    # USC subject sheets do not always print the academic code. It stays absent rather
    # than being guessed from a same-named entry in another plan.
    code: str | None = None
    cache_metadata: tuple[PublicHttpCacheMetadata, ...] = ()


def _clean_text(value: str) -> str:
    return " ".join(value.split())


def _normalise_label(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", _clean_text(value).casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char)).strip(" :")


def _validate_academic_year(value: str) -> str:
    match = _ACADEMIC_YEAR.fullmatch(value)
    if not match or int(match.group("end")) != int(match.group("start")) + 1:
        raise ValueError("academic_year debe tener el formato consecutivo 2025/2026")
    return value


def _safe_path(url: str) -> str:
    validated = validate_usc_url(url)
    parsed = urlparse(validated)
    raw_path = parsed.path
    decoded_path = unquote(raw_path)
    if (
        parsed.query
        or parsed.fragment
        or _CONTROL.search(decoded_path)
        or "\\" in decoded_path
        or _ENCODED_SEPARATOR.search(raw_path)
        or any(segment in {".", ".."} for segment in decoded_path.split("/"))
    ):
        raise UnsafeUrlError("URL USC no permitida para calendarios públicos")
    return decoded_path


def _validated_calendar_url(url: str) -> Literal["page", "ajax"]:
    path = _safe_path(url)
    if _AJAX_PATH.fullmatch(path):
        return "ajax"
    if _CALENDAR_PAGE_PATH.fullmatch(path):
        return "page"
    raise UnsafeUrlError("La URL no es una página o endpoint oficial de calendario USC")


def _validated_subject_url(url: str) -> str:
    path = _safe_path(url)
    if not _SUBJECT_PAGE_PATH.fullmatch(path):
        raise UnsafeUrlError("La URL no es una ficha oficial de materia USC")
    return url


def discover_calendar_endpoint(
    canonical_html: str,
    canonical_url: str,
    academic_year: str,
) -> str:
    """Find the exact academic-year AJAX link in a canonical centre calendar page."""

    if _validated_calendar_url(canonical_url) != "page":
        raise UnsafeUrlError("Se esperaba la URL canónica de un calendario USC")
    year = _validate_academic_year(academic_year)
    expected_labels = {f"Curso {year}", f"Course {year}"}
    soup = BeautifulSoup(canonical_html, "html.parser")
    candidates: list[str] = []
    for link in soup.select("a[href]"):
        # These links live inside a Drupal <template>; BeautifulSoup intentionally
        # excludes TemplateString from get_text(), although the literal label is safe.
        label = _clean_text(str(link.string or link.get("aria-label") or ""))
        if label not in expected_labels:
            continue
        candidate = urljoin(canonical_url, str(link.get("href", "")))
        if _validated_calendar_url(candidate) != "ajax":
            raise UnsafeUrlError("El enlace de curso no apunta al endpoint de calendario USC")
        candidates.append(candidate)
    if len(candidates) != 1:
        raise ExamCalendarParseError(
            f"Se esperaba un único enlace oficial para el curso {year}; encontrados: "
            f"{len(candidates)}"
        )
    return candidates[0]


def extract_calendar_insert(payload: str | bytes) -> str:
    """Extract only Drupal's calendar replacement command from an AJAX response."""

    try:
        decoded = payload.decode("utf-8") if isinstance(payload, bytes) else payload
        commands = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        raise ExamCalendarParseError("La respuesta AJAX del calendario no es JSON válido") from None
    if not isinstance(commands, list):
        raise ExamCalendarParseError("La respuesta AJAX del calendario no es una lista")
    if len(commands) > 32:
        raise ExamCalendarParseError("La respuesta AJAX contiene demasiados comandos")
    inserts = [
        command.get("data")
        for command in commands
        if isinstance(command, dict)
        and command.get("command") == "insert"
        and command.get("selector") == "#schedules-exams-calendar"
        and isinstance(command.get("data"), str)
    ]
    if len(inserts) != 1:
        raise ExamCalendarParseError(
            "La respuesta AJAX no contiene una única inserción del calendario"
        )
    if len(inserts[0].encode("utf-8")) > 3_000_000:
        raise ExamCalendarParseError("El fragmento HTML del calendario es demasiado grande")
    return inserts[0]


def _ajax_academic_year(payload: str | bytes) -> str | None:
    try:
        decoded = payload.decode("utf-8") if isinstance(payload, bytes) else payload
        commands = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return None
    if not isinstance(commands, list):
        return None
    years: list[str] = []
    for command in commands:
        if not isinstance(command, dict) or command.get("command") != "UpdateAcademicCourse":
            continue
        value = _clean_text(str(command.get("value", "")))
        match = re.search(r"20\d{2}/20\d{2}", value)
        if match and match.group(0) not in years:
            years.append(match.group(0))
    if len(years) > 1:
        raise ExamCalendarParseError("Drupal devolvió varios cursos académicos distintos")
    return years[0] if years else None


def _positive_span(cell: Tag, attribute: str) -> int:
    raw = str(cell.get(attribute, "1"))
    try:
        value = int(raw)
    except ValueError:
        raise ExamCalendarParseError(f"{attribute} no numérico en el calendario") from None
    if value < 1 or value > 100:
        raise ExamCalendarParseError(f"{attribute} fuera de límites en el calendario")
    return value


def _expanded_body_rows(table: Tag) -> list[tuple[str, ...]]:
    body = table.find("tbody")
    if not isinstance(body, Tag):
        raise ExamCalendarParseError("Tabla de convocatorias sin tbody")
    active: dict[int, tuple[str, int]] = {}
    expanded: list[tuple[str, ...]] = []
    for tr in body.find_all("tr", recursive=False):
        row: dict[int, str] = {}
        following: dict[int, tuple[str, int]] = {}
        for column, (text, remaining) in active.items():
            row[column] = text
            if remaining > 1:
                following[column] = (text, remaining - 1)

        column = 0
        for cell in tr.find_all(["td", "th"], recursive=False):
            while column in row:
                column += 1
            text = _clean_text(cell.get_text(" ", strip=True))
            rowspan = _positive_span(cell, "rowspan")
            colspan = _positive_span(cell, "colspan")
            for offset in range(colspan):
                target = column + offset
                if target in row:
                    raise ExamCalendarParseError("Solapamiento de celdas en la tabla de exámenes")
                row[target] = text
                if rowspan > 1:
                    following[target] = (text, rowspan - 1)
            column += colspan
        active = following
        if row:
            expanded.append(tuple(row.get(index, "") for index in range(max(row) + 1)))
    if active:
        raise ExamCalendarParseError("rowspan excede el cuerpo de la tabla de exámenes")
    return expanded


def _table_columns(table: Tag) -> dict[str, int]:
    header = table.find("thead")
    if not isinstance(header, Tag):
        raise ExamCalendarParseError("Tabla de convocatorias sin cabecera")
    labels = [
        _normalise_label(cell.get_text(" ", strip=True)) for cell in header.find_all(["th", "td"])
    ]
    aliases = {
        "semester": {"convocatoria", "call"},
        "opportunity": {"oportunidade", "oportunidad", "opportunity"},
        "date_time": {"data", "fecha", "date"},
        "room": {"aula", "room", "classroom"},
        "group": {"grupo", "group"},
    }
    columns: dict[str, int] = {}
    for key, accepted in aliases.items():
        matches = [index for index, label in enumerate(labels) if label in accepted]
        if len(matches) != 1:
            raise ExamCalendarParseError(f"Columna {key} ausente o ambigua")
        columns[key] = matches[0]
    return columns


def _value_at(row: tuple[str, ...], index: int) -> str:
    return row[index] if index < len(row) else ""


def _calls_from_table(table: Tag) -> tuple[ExamCall, ...]:
    columns = _table_columns(table)
    collected: OrderedDict[tuple[str, str, str], dict[str, list[str]]] = OrderedDict()
    for row in _expanded_body_rows(table):
        semester = _value_at(row, columns["semester"])
        opportunity = _value_at(row, columns["opportunity"])
        date_time = _value_at(row, columns["date_time"])
        if not semester or not opportunity or not date_time:
            raise ExamCalendarParseError("Fila de examen sin convocatoria, oportunidad o fecha")
        key = (semester, opportunity, date_time)
        values = collected.setdefault(key, {"rooms": [], "groups": []})
        room = _value_at(row, columns["room"])
        group = _value_at(row, columns["group"])
        if room and room not in values["rooms"]:
            values["rooms"].append(room)
        if group and group not in values["groups"]:
            values["groups"].append(group)
    return tuple(
        ExamCall(
            semester=semester,
            opportunity=opportunity,
            date_time=date_time,
            rooms=tuple(values["rooms"]),
            groups=tuple(values["groups"]),
        )
        for (semester, opportunity, date_time), values in collected.items()
    )


def _normalised_plan_filter(plan_id: str | int | None) -> int | None:
    if plan_id is None:
        return None
    value = str(plan_id).removeprefix("is-type-")
    if not value.isdigit():
        raise ValueError("plan_id debe ser numérico o tener el formato is-type-12345")
    normalised = int(value)
    if normalised < 1 or normalised > 999_999_999:
        raise ValueError("plan_id está fuera de límites")
    return normalised


def parse_exam_calendar_html(
    calendar_html: str,
    *,
    plan_id: str | int | None = None,
    subject_name: str | None = None,
) -> tuple[ExamSubject, ...]:
    """Parse calendar accordions, preserving literal USC labels and exact-name filters."""

    selected_plan = _normalised_plan_filter(plan_id)
    if subject_name is not None:
        subject_name = _clean_text(subject_name)
        if not subject_name:
            raise ValueError("subject_name no puede estar vacío")
    soup = BeautifulSoup(calendar_html, "html.parser")
    subjects: list[ExamSubject] = []
    items = soup.select("usc-accordion-item.subject-class")
    if len(items) > 1_000:
        raise ExamCalendarParseError("El calendario contiene demasiadas materias")
    for item in items:
        plan_ids = [
            match.group("plan_id")
            for class_name in item.get("class", [])
            if (match := _PLAN_CLASS.fullmatch(str(class_name)))
        ]
        if len(plan_ids) != 1:
            raise ExamCalendarParseError("Materia sin una única clase de plan is-type-*")
        current_plan = int(plan_ids[0])
        header = item.find("template")
        table = item.find("table")
        if not isinstance(header, Tag) or not isinstance(table, Tag):
            raise ExamCalendarParseError("Acordeón de materia incompleto")
        name = _clean_text(header.get_text(" ", strip=True))
        if not name or len(name) > 500:
            raise ExamCalendarParseError("Nombre de materia ausente o demasiado largo")
        if selected_plan is not None and current_plan != selected_plan:
            continue
        if subject_name is not None and name != subject_name:
            continue
        subjects.append(
            ExamSubject(
                plan_id=current_plan,
                name=name,
                calls=_calls_from_table(table),
            )
        )
    return tuple(subjects)


def _explicit_subject_code(soup: BeautifulSoup) -> str | None:
    candidates: list[str] = []
    for element in soup.select("[data-subject-code]"):
        if element.name in {"script", "style", "template"}:
            continue
        value = _clean_text(str(element.get("data-subject-code") or element.get_text(" ")))
        try:
            code = normalise_subject_code(value)
        except ValueError:
            continue
        if code not in candidates:
            candidates.append(code)
    code_labels = {"codigo", "codigo da materia", "subject code", "code"}
    for label in soup.find_all(["dt", "b", "strong"]):
        if _normalise_label(label.get_text(" ", strip=True)) not in code_labels:
            continue
        if label.name == "dt":
            value_node = label.find_next_sibling("dd")
            value = value_node.get_text(" ", strip=True) if isinstance(value_node, Tag) else ""
        else:
            parent_text = (
                label.parent.get_text(" ", strip=True) if isinstance(label.parent, Tag) else ""
            )
            label_text = label.get_text(" ", strip=True)
            value = parent_text.removeprefix(label_text).strip(" :")
        value = _clean_text(value)
        try:
            code = normalise_subject_code(value)
        except ValueError:
            continue
        if code not in candidates:
            candidates.append(code)
    if len(candidates) > 1:
        raise ExamCalendarParseError("La ficha contiene códigos de materia contradictorios")
    return candidates[0] if candidates else None


def _subject_semester(soup: BeautifulSoup) -> str | None:
    for label in soup.find_all(["b", "strong"]):
        if _normalise_label(label.get_text(" ", strip=True)) not in {"convocatoria", "call"}:
            continue
        if not isinstance(label.parent, Tag):
            continue
        full_text = _clean_text(label.parent.get_text(" ", strip=True))
        label_text = _clean_text(label.get_text(" ", strip=True))
        value = full_text.removeprefix(label_text).strip(" :")
        if value:
            return value
    return None


def _subject_exam_slots(soup: BeautifulSoup) -> tuple[SubjectExamSlot, ...]:
    collected: OrderedDict[str, dict[str, list[str]]] = OrderedDict()
    exam_captions = {"exame", "exames", "examen", "examenes", "exam", "exams"}
    for table in soup.find_all("table"):
        caption = table.find("caption")
        if not isinstance(caption, Tag):
            continue
        if _normalise_label(caption.get_text(" ", strip=True)) not in exam_captions:
            continue
        for row in table.select("tbody > tr"):
            cells = [
                _clean_text(cell.get_text(" ", strip=True))
                for cell in row.find_all(["td", "th"], recursive=False)
            ]
            if len(cells) < 3 or not cells[0]:
                raise ExamCalendarParseError("Fila de examen inválida en la ficha de materia")
            values = collected.setdefault(cells[0], {"rooms": [], "groups": []})
            group, room = cells[1], cells[2]
            if room and room not in values["rooms"]:
                values["rooms"].append(room)
            if group and group not in values["groups"]:
                values["groups"].append(group)
    return tuple(
        SubjectExamSlot(
            date_time=date_time,
            rooms=tuple(values["rooms"]),
            groups=tuple(values["groups"]),
        )
        for date_time, values in collected.items()
    )


def parse_subject_sheet_html(subject_html: str, source_url: str) -> SubjectSheet:
    """Parse facts printed on a subject sheet; never derive an academic code from its name."""

    _validated_subject_url(source_url)
    soup = BeautifulSoup(subject_html, "html.parser")
    heading = soup.find("h1")
    if not isinstance(heading, Tag):
        raise ExamCalendarParseError("Ficha de materia sin título")
    title = _clean_text(heading.get_text(" ", strip=True))
    years = []
    for tag in soup.select(".at-tag"):
        value = _clean_text(tag.get_text(" ", strip=True))
        if _ACADEMIC_YEAR.fullmatch(value) and value not in years:
            _validate_academic_year(value)
            years.append(value)
    if len(years) > 1:
        raise ExamCalendarParseError("Ficha con varios cursos académicos")
    return SubjectSheet(
        source_url=source_url,
        title=title,
        academic_year=years[0] if years else None,
        semester=_subject_semester(soup),
        exam_slots=_subject_exam_slots(soup),
        code=_explicit_subject_code(soup),
    )


class UscExamCalendarClient:
    """Unauthenticated, GET-only client for the public USC Drupal calendar pages."""

    def __init__(
        self,
        *,
        timeout: float = 20.0,
        max_bytes: int = 5_000_000,
        transport: httpx.AsyncBaseTransport | None = None,
        cache: PublicHttpCache | None = None,
        cache_ttl_seconds: float = 300.0,
        cache_stale_if_error_seconds: float = 3_600.0,
        cache_max_entries: int = 128,
        cache_max_total_bytes: int = 64_000_000,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout debe ser positivo")
        if max_bytes < 1 or max_bytes > 20_000_000:
            raise ValueError("max_bytes debe estar entre 1 y 20000000")
        if cache is None:
            use_default = (
                transport is None
                and cache_ttl_seconds == 300.0
                and cache_stale_if_error_seconds == 3_600.0
                and cache_max_entries == 128
                and cache_max_total_bytes == 64_000_000
            )
            cache = (
                DEFAULT_PUBLIC_HTTP_CACHE
                if use_default
                else PublicHttpCache(
                    ttl_seconds=cache_ttl_seconds,
                    stale_if_error_seconds=cache_stale_if_error_seconds,
                    max_entries=cache_max_entries,
                    max_total_bytes=cache_max_total_bytes,
                )
            )
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.transport = transport
        self.cache = cache
        self._fetcher = SafePublicHttpFetcher(
            timeout=timeout,
            max_bytes=max_bytes,
            cache=cache,
            transport=transport,
        )

    async def _get(
        self,
        url: str,
        *,
        kind: Literal["page", "ajax", "subject"],
        validate: Callable[[bytes], None],
    ) -> PublicHttpResponse:
        if kind == "subject":
            _validated_subject_url(url)
        elif _validated_calendar_url(url) != kind:
            raise UnsafeUrlError("Tipo de URL de calendario inesperado")
        headers = {
            "User-Agent": _USER_AGENT,
            "Accept": "application/json" if kind == "ajax" else "text/html",
        }
        if kind == "ajax":
            headers["X-Requested-With"] = "XMLHttpRequest"
        def validate_redirect(candidate: str) -> None:
            if kind == "subject":
                _validated_subject_url(candidate)
            elif _validated_calendar_url(candidate) != kind:
                raise UnsafeUrlError("Redirección fuera del tipo de recurso esperado")

        try:
            def validate_response(response: PublicHttpResponse) -> None:
                if kind == "ajax" and response.metadata.media_type != "application/json":
                    raise ExamCalendarSchemaChangedError(
                        "El endpoint de calendario no devolvió application/json"
                    )
                validate(response.content)

            response = await self._fetcher.get(
                url,
                headers=headers,
                validate_redirect=validate_redirect,
                validate=validate_response,
            )
        except PublicHttpError as exc:
            raise ExamCalendarError(str(exc)) from None
        return response

    @staticmethod
    def _schema_changed(error: BaseException) -> ExamCalendarSchemaChangedError:
        return ExamCalendarSchemaChangedError(str(error))

    async def fetch_calendar(
        self,
        url: str,
        *,
        academic_year: str | None = None,
        plan_id: str | int | None = None,
        subject_name: str | None = None,
    ) -> ExamCalendar:
        url_kind = _validated_calendar_url(url)
        if academic_year is not None:
            academic_year = _validate_academic_year(academic_year)
        if url_kind == "page":
            if academic_year is None:
                raise ValueError("academic_year es obligatorio al partir de la página canónica")
            def validate_page(content: bytes) -> None:
                try:
                    discover_calendar_endpoint(
                        content.decode("utf-8", errors="replace"), url, academic_year
                    )
                except ExamCalendarParseError as exc:
                    raise self._schema_changed(exc) from None

            page = await self._get(url, kind="page", validate=validate_page)
            try:
                endpoint = discover_calendar_endpoint(
                    page.content.decode("utf-8", errors="replace"), url, academic_year
                )
            except (ExamCalendarParseError, ValueError) as exc:
                raise self._schema_changed(exc) from None
            metadata = [page.metadata]
        else:
            endpoint = url
            metadata = []
        def validate_payload(content: bytes) -> None:
            try:
                candidate_year = _ajax_academic_year(content)
                if candidate_year is not None:
                    _validate_academic_year(candidate_year)
                    if academic_year is not None and candidate_year != academic_year:
                        raise ExamCalendarParseError(
                            "El curso devuelto por Drupal no coincide con el solicitado"
                        )
                candidate_html = extract_calendar_insert(content)
                parse_exam_calendar_html(candidate_html)
            except ExamCalendarParseError as exc:
                raise self._schema_changed(exc) from None

        payload = await self._get(endpoint, kind="ajax", validate=validate_payload)
        try:
            reported_year = _ajax_academic_year(payload.content)
            if reported_year is not None:
                _validate_academic_year(reported_year)
                if academic_year is not None and reported_year != academic_year:
                    raise ExamCalendarParseError(
                        "El curso devuelto por Drupal no coincide con el solicitado"
                    )
                academic_year = reported_year
            calendar_html = extract_calendar_insert(payload.content)
            subjects = parse_exam_calendar_html(
                calendar_html,
                plan_id=plan_id,
                subject_name=subject_name,
            )
        except (ExamCalendarParseError, ValueError) as exc:
            raise self._schema_changed(exc) from None
        metadata.append(payload.metadata)
        return ExamCalendar(
            academic_year=academic_year,
            endpoint_url=endpoint,
            subjects=subjects,
            cache_metadata=tuple(metadata),
        )

    async def fetch_subject_sheet(self, url: str) -> SubjectSheet:
        _validated_subject_url(url)
        def validate_sheet(candidate: bytes) -> None:
            try:
                parse_subject_sheet_html(candidate.decode("utf-8", errors="replace"), url)
            except (ExamCalendarParseError, ValueError) as exc:
                raise self._schema_changed(exc) from None

        content = await self._get(url, kind="subject", validate=validate_sheet)
        try:
            parsed = parse_subject_sheet_html(
                content.content.decode("utf-8", errors="replace"), url
            )
        except (ExamCalendarParseError, ValueError) as exc:
            raise self._schema_changed(exc) from None
        return SubjectSheet(
            source_url=parsed.source_url,
            title=parsed.title,
            academic_year=parsed.academic_year,
            semester=parsed.semester,
            exam_slots=parsed.exam_slots,
            code=parsed.code,
            cache_metadata=(content.metadata,),
        )
