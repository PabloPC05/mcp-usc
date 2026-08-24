"""Horarios lectivos públicos de las titulaciones de la USC.

La web institucional publica los horarios mediante páginas Drupal y respuestas
AJAX JSON. Este módulo solo realiza GET anónimos, valida cada ruta antes de
seguirla y conserva la página y los endpoints oficiales usados como evidencia.
"""

from __future__ import annotations

import asyncio
import json
import re
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal
from urllib.parse import unquote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, Tag

from .exam_catalog import normalise_academic_year
from .public_http_cache import (
    DEFAULT_PUBLIC_HTTP_CACHE,
    PublicHttpCache,
    PublicHttpCacheMetadata,
    PublicHttpError,
    PublicHttpResponse,
    SafePublicHttpFetcher,
    public_cache_summary,
)
from .security import UnsafeUrlError, validate_usc_url

_USER_AGENT = "mcp-usc/0.10 (+https://github.com/PabloPC05/mcp-usc)"
_ALLOWED_HOSTS = frozenset({"www.usc.gal", "usc.gal"})
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_ENCODED_SEPARATOR = re.compile(r"%(?:2f|5c)", re.IGNORECASE)
_SLUG = r"[a-z0-9][a-z0-9-]{0,199}"
_TIMETABLE_SLUG = r"[a-z0-9][a-z0-9-]{0,399}"
_DEGREE_PAGE_PATH = re.compile(
    rf"^/(?:gl/estudos/graos|es/estudios/grados|en/studies/degrees)/"
    rf"{_SLUG}/{_SLUG}/?$"
)
_CENTER_ROOT_PATH = re.compile(
    rf"^/(?:(?P<romance>gl|es)/centro|(?P<english>en)/center)/"
    rf"(?P<center>{_SLUG})/?$"
)
_INDEX_PATH = re.compile(
    rf"^/(?:(?P<romance>gl|es)/centro|(?P<english>en)/center)/"
    rf"(?P<center>{_SLUG})/(?:(?:horarios/cursos)|(?:schedules/courses))/?$"
)
_TIMETABLE_PATH = re.compile(
    rf"^/(?:(?P<romance>gl|es)/centro|(?P<english>en)/center)/"
    rf"(?P<center>{_SLUG})/(?:(?:horarios/cursos)|(?:schedules/courses))/"
    rf"(?P<slug>{_TIMETABLE_SLUG})-(?P<program>\d{{1,8}})-(?P<course>\d{{1,2}})/?$"
)
_YEAR_AJAX_PATH = re.compile(
    r"^/(?P<lang>gl|es|en)/course/(?P<year>\d{1,4})/"
    r"course-detail-controller/(?P<controller>\d{1,8})/"
    r"(?P<program>\d{1,8})-(?P<course>\d{1,2})/?$"
)
_SEMESTER_AJAX_PATH = re.compile(
    r"^/(?P<lang>gl|es|en)/course/widget/(?P<year>\d{1,4})/"
    r"course-detail-controller-call-filter/(?P<controller>\d{1,8})/"
    r"(?P<program>\d{1,8})-(?P<course>\d{1,2})/(?P<semester>\d{1,4})/?$"
)
_WEEK_AJAX_PATH = re.compile(
    r"^/(?P<lang>gl|es|en)/course/widget/(?P<year>\d{1,4})/"
    r"course-detail-controller-week-filter/(?P<controller>\d{1,8})/"
    r"(?P<program>\d{1,8})-(?P<course>\d{1,2})/"
    r"(?P<semester>\d{1,4})/(?P<week>\d{1,4})/?$"
)
_SUBJECT_PATH = re.compile(
    rf"^/(?:gl|es|en)/plan/\d{{1,8}}/course/\d{{1,4}}/subject/{_SLUG}/?$"
)
_TIME_RANGE = re.compile(
    r"\b(?P<start>(?:[01]\d|2[0-3]):[0-5]\d)\s*-\s*"
    r"(?P<end>(?:[01]\d|2[0-3]):[0-5]\d)\b"
)
_GROUP_CODE = re.compile(r"\b(?:grupo|group)\s*/?([A-Za-z0-9_-]{1,40})\b", re.IGNORECASE)

_MONTHS = {
    "enero": 1,
    "xaneiro": 1,
    "january": 1,
    "febrero": 2,
    "febreiro": 2,
    "february": 2,
    "marzo": 3,
    "march": 3,
    "abril": 4,
    "april": 4,
    "mayo": 5,
    "maio": 5,
    "may": 5,
    "junio": 6,
    "xuno": 6,
    "june": 6,
    "julio": 7,
    "xullo": 7,
    "july": 7,
    "agosto": 8,
    "august": 8,
    "septiembre": 9,
    "setembro": 9,
    "september": 9,
    "octubre": 10,
    "outubro": 10,
    "october": 10,
    "noviembre": 11,
    "novembro": 11,
    "november": 11,
    "diciembre": 12,
    "decembro": 12,
    "december": 12,
}
_WEEKDAYS = {
    "lunes": 0,
    "luns": 0,
    "monday": 0,
    "martes": 1,
    "tuesday": 1,
    "miercoles": 2,
    "mercores": 2,
    "wednesday": 2,
    "jueves": 3,
    "xoves": 3,
    "thursday": 3,
    "viernes": 4,
    "venres": 4,
    "friday": 4,
    "sabado": 5,
    "saturday": 5,
    "domingo": 6,
    "sunday": 6,
}

UrlKind = Literal[
    "degree",
    "index",
    "timetable",
    "year_ajax",
    "semester_ajax",
    "week_ajax",
    "subject",
]


class ClassTimetableError(RuntimeError):
    """No se pudo consultar de forma segura el horario lectivo oficial."""


class ClassTimetableParseError(ClassTimetableError):
    """La fuente no conserva la estructura de horario esperada."""


class ClassTimetableSchemaChangedError(ClassTimetableParseError):
    """La estructura remota cambió y la respuesta se rechazó antes de guardarla."""


class ClassTimetableAcademicYearUnavailable(ClassTimetableError):
    """La página no anuncia el curso académico solicitado."""

    def __init__(self, requested: str, available: Sequence[str]) -> None:
        self.requested = requested
        self.available = tuple(available)
        super().__init__(
            f"El horario no publica {requested}; cursos disponibles: "
            f"{', '.join(self.available) or 'ninguno'}"
        )


@dataclass(frozen=True, slots=True)
class DegreeCenter:
    name: str
    url: str
    timetable_index_url: str

    def public_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "url": self.url,
            "timetable_index_url": self.timetable_index_url,
            "content_is_untrusted": True,
        }


@dataclass(frozen=True, slots=True)
class DegreeTimetablePage:
    degree_name: str
    course_number: int
    program_id: int
    url: str
    index_url: str
    center_slug: str

    def public_dict(self) -> dict[str, object]:
        return {
            "degree_name": self.degree_name,
            "edition": _degree_edition(self.degree_name),
            "course_number": self.course_number,
            "program_id": self.program_id,
            "timetable_url": self.url,
            "index_url": self.index_url,
            "center_slug": self.center_slug,
            "content_is_untrusted": True,
        }


@dataclass(frozen=True, slots=True)
class TimetableWeek:
    label: str
    start: date
    end: date
    endpoint_url: str

    def public_dict(self) -> dict[str, object]:
        return {
            "label": self.label,
            "start_date": self.start.isoformat(),
            "end_date": self.end.isoformat(),
            "endpoint_url": self.endpoint_url,
            "content_is_untrusted": True,
        }


@dataclass(frozen=True, slots=True)
class ClassSession:
    date: date
    weekday: str
    start_time: str
    end_time: str
    subject_name: str
    subject_url: str | None
    activity_type: str
    group_code: str | None
    room: str | None

    def public_dict(self) -> dict[str, object]:
        return {
            "date": self.date.isoformat(),
            "weekday": self.weekday,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "subject_name": self.subject_name,
            "subject_url": self.subject_url,
            "activity_type": self.activity_type,
            "group_code": self.group_code,
            "room": self.room,
            "content_is_untrusted": True,
        }


def _clean_text(value: str) -> str:
    return " ".join(value.split())


def _fold(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _normalise_degree_name(value: str) -> str:
    folded = _fold(value)
    folded = re.sub(r"\[(?:s|l)\]", " ", folded)
    folded = re.sub(r"\((?:s|l)\)", " ", folded)
    folded = re.sub(r"\[[^]]*(?:extincion|tramitacion)[^]]*]", " ", folded)
    folded = re.sub(
        r"\([^)]*(?:(?:\d+\s*[ao]?\s*)?(?:edicion|edition|ed)\b|adscrita)[^)]*\)",
        " ",
        folded,
    )
    folded = re.sub(r"\b(?:en extincion|en tramitacion)\b", " ", folded)
    tokens = re.findall(r"[a-z0-9]+", folded)
    aliases = {"grao": "grado", "dobre": "doble"}
    stopwords = {"a", "as", "da", "das", "de", "do", "dos", "e", "en", "y"}
    return " ".join(aliases.get(token, token) for token in tokens if token not in stopwords)


def _degree_edition(value: str) -> int | None:
    match = re.search(
        r"\(\s*(\d{1,2})\s*[ao]?\s*(?:edicion|edition|ed)\b",
        _fold(value),
    )
    return int(match.group(1)) if match else None


def _safe_path(url: str) -> str:
    validated = validate_usc_url(url)
    parsed = urlparse(validated)
    host = (parsed.hostname or "").casefold().rstrip(".")
    decoded_path = unquote(parsed.path)
    if (
        host not in _ALLOWED_HOSTS
        or parsed.query
        or parsed.fragment
        or _CONTROL.search(decoded_path)
        or "\\" in decoded_path
        or _ENCODED_SEPARATOR.search(parsed.path)
        or any(part in {".", ".."} for part in decoded_path.split("/"))
    ):
        raise UnsafeUrlError("URL no permitida para horarios públicos USC")
    return decoded_path


def _url_kind(url: str) -> UrlKind:
    path = _safe_path(url)
    patterns: tuple[tuple[UrlKind, re.Pattern[str]], ...] = (
        ("degree", _DEGREE_PAGE_PATH),
        ("index", _INDEX_PATH),
        ("timetable", _TIMETABLE_PATH),
        ("year_ajax", _YEAR_AJAX_PATH),
        ("semester_ajax", _SEMESTER_AJAX_PATH),
        ("week_ajax", _WEEK_AJAX_PATH),
        ("subject", _SUBJECT_PATH),
    )
    for kind, pattern in patterns:
        if pattern.fullmatch(path):
            return kind
    raise UnsafeUrlError("La URL no es una ruta oficial de horarios USC permitida")


def _index_for_center(center_url: str) -> str:
    match = _CENTER_ROOT_PATH.fullmatch(_safe_path(center_url))
    if not match:
        raise UnsafeUrlError("La URL no es la página oficial de un centro USC")
    suffix = "schedules/courses" if match.group("english") else "horarios/cursos"
    return f"{center_url.rstrip('/')}/{suffix}"


def parse_degree_page_html(html: str, source_url: str) -> tuple[str, tuple[DegreeCenter, ...]]:
    """Obtiene el título y los centros explícitos de una titulación oficial."""

    if _url_kind(source_url) != "degree":
        raise UnsafeUrlError("Se esperaba una página oficial de titulación USC")
    if len(html.encode("utf-8")) > 8_000_000:
        raise ClassTimetableError("La página de la titulación supera el límite permitido")
    soup = BeautifulSoup(html, "html.parser")
    titles = soup.select("main h1.at-title, main #block-page-title-block")
    title_values = [_clean_text(item.get_text(" ", strip=True)) for item in titles]
    title_values = [value for value in title_values if value]
    if not title_values:
        raise ClassTimetableParseError("La página de la titulación no contiene un título")
    title = max(title_values, key=len)
    centers: list[DegreeCenter] = []
    seen: set[str] = set()
    for link in soup.select(".center-address-info a[href]"):
        candidate = urljoin(source_url, str(link.get("href", "")))
        try:
            index_url = _index_for_center(candidate)
        except UnsafeUrlError:
            continue
        if candidate in seen:
            continue
        name = _clean_text(link.get_text(" ", strip=True))
        if not name or len(name) > 300:
            raise ClassTimetableParseError("Un centro no conserva un nombre válido")
        seen.add(candidate)
        centers.append(DegreeCenter(name, candidate, index_url))
    if not centers or len(centers) > 10:
        raise ClassTimetableParseError(
            "La titulación no anuncia un número válido de centros oficiales"
        )
    return title, tuple(centers)


def parse_timetable_index_html(html: str, source_url: str) -> tuple[DegreeTimetablePage, ...]:
    """Lista las páginas de curso enlazadas en el índice oficial de un centro."""

    if _url_kind(source_url) != "index":
        raise UnsafeUrlError("Se esperaba un índice oficial de horarios USC")
    if len(html.encode("utf-8")) > 8_000_000:
        raise ClassTimetableError("El índice de horarios supera el límite permitido")
    soup = BeautifulSoup(html, "html.parser")
    entries: list[DegreeTimetablePage] = []
    seen: set[str] = set()
    for section in soup.select("main section.org-tier"):
        heading = section.select_one(":scope > .tier-header .tier-title")
        degree_name = _clean_text(heading.get_text(" ", strip=True)) if heading else ""
        if not degree_name:
            continue
        for article in section.select(":scope > .tier-content article.ml-banner"):
            links = article.select(":scope > a.banner-link[href]")
            if len(links) != 1:
                raise ClassTimetableParseError("Una tarjeta de horario no tiene un enlace único")
            link = links[0]
            candidate = urljoin(source_url, str(link.get("href", "")))
            if _url_kind(candidate) != "timetable":
                raise ClassTimetableParseError(
                    "Una tarjeta de horario apunta fuera de las rutas permitidas"
                )
            if candidate.rstrip("/").rsplit("/", 1)[0] != source_url.rstrip("/"):
                raise ClassTimetableParseError(
                    "Una tarjeta de horario apunta a otro índice de centro"
                )
            match = _TIMETABLE_PATH.fullmatch(_safe_path(candidate))
            assert match is not None
            if candidate in seen:
                raise ClassTimetableParseError("El índice contiene horarios duplicados")
            course_title = article.select_one("h2.at-title")
            course_text = (
                _clean_text(course_title.get_text(" ", strip=True)) if course_title else ""
            )
            course_match = re.search(
                r"\b(\d{1,2})\s*[ºoªa]?\s*(?:curso|year)\b", _fold(course_text)
            )
            course_number = int(match.group("course"))
            if course_match is None or int(course_match.group(1)) != course_number:
                raise ClassTimetableParseError("La tarjeta contradice el curso indicado por su URL")
            seen.add(candidate)
            entries.append(
                DegreeTimetablePage(
                    degree_name=degree_name,
                    course_number=course_number,
                    program_id=int(match.group("program")),
                    url=candidate,
                    index_url=source_url,
                    center_slug=match.group("center"),
                )
            )
    if not entries or len(entries) > 1_000:
        raise ClassTimetableParseError("Número de horarios fuera de los límites esperados")
    return tuple(entries)


def discover_timetable_year_endpoint(
    canonical_html: str, canonical_url: str, academic_year: str
) -> tuple[str, tuple[str, ...]]:
    """Selecciona el único endpoint AJAX asociado al curso académico exacto."""

    if _url_kind(canonical_url) != "timetable":
        raise UnsafeUrlError("Se esperaba una página oficial de horario USC")
    requested = normalise_academic_year(academic_year)
    soup = BeautifulSoup(canonical_html, "html.parser")
    endpoints: dict[str, str] = {}
    timetable_match = _TIMETABLE_PATH.fullmatch(_safe_path(canonical_url))
    assert timetable_match is not None
    for link in soup.select("a[href]"):
        label = _clean_text(str(link.string or link.get("aria-label") or ""))
        match = re.search(r"20\d{2}/20\d{2}", label)
        if not match:
            continue
        candidate = urljoin(canonical_url, str(link.get("href", "")))
        try:
            kind = _url_kind(candidate)
        except UnsafeUrlError as exc:
            raise ClassTimetableParseError(
                "Una etiqueta de curso apunta fuera de los endpoints permitidos"
            ) from exc
        if kind != "year_ajax":
            raise ClassTimetableParseError(
                "Una etiqueta de curso no apunta a un endpoint anual de horario"
            )
        endpoint_match = _YEAR_AJAX_PATH.fullmatch(_safe_path(candidate))
        assert endpoint_match is not None
        if (
            endpoint_match.group("program") != timetable_match.group("program")
            or endpoint_match.group("course") != timetable_match.group("course")
        ):
            raise ClassTimetableParseError(
                "Un endpoint anual pertenece a otra titulación o curso"
            )
        year = normalise_academic_year(match.group(0))
        previous = endpoints.get(year)
        if previous is not None and previous != candidate:
            raise ClassTimetableParseError(f"El curso {year} anuncia varios endpoints")
        endpoints[year] = candidate
        if len(endpoints) > 20:
            raise ClassTimetableParseError("La página anuncia demasiados cursos académicos")
    if not endpoints:
        raise ClassTimetableParseError("La página no anuncia endpoints de horarios por curso")
    if requested not in endpoints:
        raise ClassTimetableAcademicYearUnavailable(requested, sorted(endpoints))
    return endpoints[requested], tuple(sorted(endpoints))


def _decode_commands(payload: str | bytes) -> list[dict[str, Any]]:
    try:
        text = payload.decode("utf-8") if isinstance(payload, bytes) else payload
        value = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        raise ClassTimetableParseError("La respuesta AJAX del horario no es JSON válido") from None
    if not isinstance(value, list) or len(value) > 64:
        raise ClassTimetableParseError("La respuesta AJAX no contiene una lista válida")
    if not all(isinstance(item, dict) for item in value):
        raise ClassTimetableParseError("La respuesta AJAX contiene comandos no válidos")
    return value


def _extract_insert(payload: str | bytes, selector: str) -> str:
    inserts = [
        command.get("data")
        for command in _decode_commands(payload)
        if command.get("command") == "insert"
        and command.get("method") == "replaceWith"
        and command.get("selector") == selector
        and isinstance(command.get("data"), str)
    ]
    if len(inserts) != 1:
        raise ClassTimetableParseError(
            f"La respuesta AJAX no contiene una única inserción {selector}"
        )
    if len(inserts[0].encode("utf-8")) > 5_000_000:
        raise ClassTimetableError("El fragmento HTML del horario es demasiado grande")
    return inserts[0]


def extract_timetable_insert(payload: str | bytes) -> str:
    return _extract_insert(payload, "#course-detail-controller")


def _ajax_academic_year(payload: str | bytes) -> str | None:
    years: set[str] = set()
    for command in _decode_commands(payload):
        if command.get("command") != "UpdateAcademicCourse":
            continue
        value = _clean_text(str(command.get("value", "")))
        match = re.search(r"20\d{2}/20\d{2}", value)
        if match:
            years.add(normalise_academic_year(match.group(0)))
    if len(years) > 1:
        raise ClassTimetableParseError("Drupal devolvió varios cursos académicos")
    return next(iter(years), None)


def _filter_links(
    payload: str | bytes,
    selector: str,
    kind: UrlKind,
    base_url: str,
    *,
    allow_empty: bool = False,
) -> list[tuple[str, str]]:
    fragment = _extract_insert(payload, selector)
    soup = BeautifulSoup(fragment, "html.parser")
    links: list[tuple[str, str]] = []
    seen: set[str] = set()
    for link in soup.select("a[href]"):
        label = _clean_text(str(link.string or link.get("aria-label") or ""))
        candidate = urljoin(base_url, str(link.get("href", "")))
        if not label or _url_kind(candidate) != kind:
            raise ClassTimetableParseError("Un filtro del horario contiene un enlace no válido")
        if candidate in seen:
            raise ClassTimetableParseError("Un filtro del horario contiene enlaces duplicados")
        seen.add(candidate)
        links.append((label, candidate))
    if (not links and not allow_empty) or len(links) > 60:
        raise ClassTimetableParseError("Número de opciones de horario fuera de los límites")
    return links


def _semester_links(payload: str | bytes, base_url: str) -> dict[int, tuple[str, str]]:
    result: dict[int, tuple[str, str]] = {}
    base_match = _YEAR_AJAX_PATH.fullmatch(_safe_path(base_url))
    if base_match is None:
        raise UnsafeUrlError("Se esperaba un endpoint anual de horario")
    links = _filter_links(
        payload,
        "#course-detail-controller-call-filter",
        "semester_ajax",
        base_url,
        allow_empty=True,
    )
    for position, item in enumerate(links, 1):
        endpoint_match = _SEMESTER_AJAX_PATH.fullmatch(_safe_path(item[1]))
        assert endpoint_match is not None
        if any(
            endpoint_match.group(field) != base_match.group(field)
            for field in ("lang", "year", "controller", "program", "course")
        ):
            raise ClassTimetableParseError(
                "Un filtro de semestre pertenece a otro horario"
            )
        match = re.search(r"\b([12])\s*[ºoªa]?", _fold(item[0]))
        semester = int(match.group(1)) if match else position
        if semester not in {1, 2} or semester in result:
            raise ClassTimetableParseError("El filtro de semestres es ambiguo")
        result[semester] = item
    return result


def _is_explicit_no_data(payload: str | bytes) -> bool:
    fragment = extract_timetable_insert(payload)
    soup = BeautifulSoup(fragment, "html.parser")
    roots = soup.select("#course-detail-controller")
    if len(roots) != 1 or roots[0].select("article.calendar-day"):
        return False
    text = _fold(_clean_text(roots[0].get_text(" ", strip=True)))
    markers = (
        "no hay datos disponibles",
        "non hai datos disponib",
        "no data available",
    )
    return any(marker in text for marker in markers)


def _month_tokens(value: str) -> list[int]:
    words = re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+", value)
    return [_MONTHS[_fold(word)] for word in words if _fold(word) in _MONTHS]


def _week_range(label: str, academic_year: str, semester: int) -> tuple[date, date]:
    numbers = [int(value) for value in re.findall(r"\b\d{1,2}\b", label)]
    months = _month_tokens(label)
    if len(numbers) != 2 or len(months) not in {1, 2}:
        raise ClassTimetableParseError(f"Semana no reconocida: {label}")
    start_month, end_month = (months[0], months[0]) if len(months) == 1 else months[:2]
    start_academic, end_academic = (int(value) for value in academic_year.split("/"))

    def year_for(month: int) -> int:
        if semester == 1:
            return start_academic if month >= 7 else end_academic
        return end_academic if month <= 7 else start_academic

    try:
        start = date(year_for(start_month), start_month, numbers[0])
        end = date(year_for(end_month), end_month, numbers[1])
    except ValueError as exc:
        raise ClassTimetableParseError(f"Semana con fecha no válida: {label}") from exc
    if end < start:
        end = date(end.year + 1, end.month, end.day)
    if not 0 <= (end - start).days <= 7:
        raise ClassTimetableParseError(f"Intervalo semanal no válido: {label}")
    return start, end


def _week_links(
    payload: str | bytes, base_url: str, academic_year: str, semester: int
) -> tuple[TimetableWeek, ...]:
    values: list[TimetableWeek] = []
    base_match = _SEMESTER_AJAX_PATH.fullmatch(_safe_path(base_url))
    if base_match is None:
        raise UnsafeUrlError("Se esperaba un endpoint semestral de horario")
    for label, endpoint in _filter_links(
        payload,
        "#course-detail-controller-week-filter",
        "week_ajax",
        base_url,
    ):
        endpoint_match = _WEEK_AJAX_PATH.fullmatch(_safe_path(endpoint))
        assert endpoint_match is not None
        if any(
            endpoint_match.group(field) != base_match.group(field)
            for field in ("lang", "year", "controller", "program", "course", "semester")
        ):
            raise ClassTimetableParseError("Una semana pertenece a otro horario")
        start, end = _week_range(label, academic_year, semester)
        values.append(TimetableWeek(label, start, end, endpoint))
    if any(
        previous.start >= current.start
        for previous, current in zip(values, values[1:], strict=False)
    ):
        raise ClassTimetableParseError("Las semanas del horario no están ordenadas")
    return tuple(values)


def _subject_url(value: str, source_url: str) -> str:
    candidate = urljoin(source_url, value)
    if _url_kind(candidate) != "subject":
        raise ClassTimetableParseError("Una materia enlaza fuera de las fichas USC permitidas")
    return candidate


def _activity_type(item: Tag) -> str:
    classes = set(item.get("class", []))
    matches = [
        value
        for value, class_name in (
            ("expository", "expository-group"),
            ("laboratory", "laboratory-group"),
            ("seminar", "seminar-group"),
        )
        if class_name in classes
    ]
    if not matches:
        raise ClassTimetableParseError("Una sesión no indica ningún tipo lectivo reconocido")
    return "+".join(matches)


def _group_and_room(spec: Tag) -> tuple[str | None, str | None]:
    lines = [_clean_text(value) for value in spec.get_text("\n", strip=True).splitlines()]
    lines = [value for value in lines if value]
    text = " ".join(lines)
    match = _GROUP_CODE.search(text)
    group = f"/{match.group(1).upper()}" if match else None
    room_lines = [value for value in lines if _GROUP_CODE.search(value) is None]
    room = _clean_text(" ".join(room_lines)) or None
    return group, room


def parse_timetable_html(
    html: str,
    source_url: str,
    *,
    week: TimetableWeek,
) -> tuple[ClassSession, ...]:
    """Convierte una semana oficial en sesiones exactas, sin inferir grupos."""

    if _url_kind(source_url) not in {"year_ajax", "semester_ajax", "week_ajax"}:
        raise UnsafeUrlError("Se esperaba un endpoint AJAX oficial de horario")
    soup = BeautifulSoup(html, "html.parser")
    roots = soup.select("#course-detail-controller")
    if len(roots) != 1:
        raise ClassTimetableParseError("El horario no contiene un contenedor único")
    days = roots[0].select("article.calendar-day")
    if len(days) > 7:
        raise ClassTimetableParseError("El horario contiene más de siete días")
    sessions: list[ClassSession] = []
    for day in days:
        heading = day.select_one(":scope > .calendar-day-header .at-title")
        weekday = _clean_text(heading.get_text(" ", strip=True)) if heading else ""
        weekday_number = _WEEKDAYS.get(_fold(weekday))
        if weekday_number is None:
            raise ClassTimetableParseError(f"Día de la semana no reconocido: {weekday}")
        session_date = week.start + timedelta(days=weekday_number)
        if session_date > week.end:
            raise ClassTimetableParseError("Un día cae fuera del intervalo semanal anunciado")
        for item in day.select("li:has(> article.ml-academic-subject)"):
            article = item.select_one(":scope > article.ml-academic-subject")
            assert article is not None
            title = article.select_one("h3.at-title")
            subject_name = _clean_text(title.get_text(" ", strip=True)) if title else ""
            if not subject_name or len(subject_name) > 500:
                raise ClassTimetableParseError("Una sesión no contiene una materia válida")
            link = title.select_one("a[href]") if title else None
            subject_url = _subject_url(str(link["href"]), source_url) if link else None
            specs = article.select("ul.academic-subject-specs-list > li")
            time_specs = [
                spec for spec in specs if _TIME_RANGE.search(spec.get_text(" ", strip=True))
            ]
            if len(time_specs) != 1:
                raise ClassTimetableParseError("Una sesión no contiene un horario único")
            time_match = _TIME_RANGE.search(time_specs[0].get_text(" ", strip=True))
            assert time_match is not None
            group_specs = [spec for spec in specs if spec is not time_specs[0]]
            pairs = [_group_and_room(spec) for spec in group_specs] or [(None, None)]
            activity_type = _activity_type(item)
            for group, room in pairs:
                sessions.append(
                    ClassSession(
                        date=session_date,
                        weekday=weekday,
                        start_time=time_match.group("start"),
                        end_time=time_match.group("end"),
                        subject_name=subject_name,
                        subject_url=subject_url,
                        activity_type=activity_type,
                        group_code=group,
                        room=room,
                    )
                )
            if len(sessions) > 2_000:
                raise ClassTimetableParseError("El horario contiene demasiadas sesiones")
    return tuple(sessions)


def _validated_course_number(value: int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 12:
        raise ValueError("course_number debe estar entre 1 y 12")
    return value


def _validated_semester(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value not in {1, 2}:
        raise ValueError("semester debe ser 1 o 2")
    return value


def _validated_groups(values: Sequence[str] | None) -> tuple[str, ...]:
    if values is None:
        return ()
    if len(values) > 30:
        raise ValueError("No se pueden filtrar más de 30 grupos")
    result: list[str] = []
    for value in values:
        cleaned = value.strip().upper().lstrip("/")
        if not re.fullmatch(r"[A-Z0-9_-]{1,40}", cleaned):
            raise ValueError("Cada group_code debe ser un código simple, como CLE_01")
        normalised = f"/{cleaned}"
        if normalised not in result:
            result.append(normalised)
    return tuple(result)


class UscClassTimetableClient:
    """Cliente anónimo y exclusivamente GET para los horarios lectivos USC."""

    def __init__(
        self,
        *,
        timeout: float = 20.0,
        max_bytes: int = 8_000_000,
        transport: httpx.AsyncBaseTransport | None = None,
        cache: PublicHttpCache | None = None,
        cache_ttl_seconds: float = 300.0,
        cache_stale_if_error_seconds: float = 3_600.0,
        cache_max_entries: int = 128,
        cache_max_total_bytes: int = 64_000_000,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout debe ser positivo")
        if not 1 <= max_bytes <= 20_000_000:
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
        kind: UrlKind,
        validate: Callable[[bytes], None],
    ) -> PublicHttpResponse:
        if _url_kind(url) != kind:
            raise UnsafeUrlError("Tipo de URL de horario inesperado")
        is_ajax = kind.endswith("_ajax")
        headers = {
            "User-Agent": _USER_AGENT,
            "Accept": "application/json" if is_ajax else "text/html",
        }
        if is_ajax:
            headers["X-Requested-With"] = "XMLHttpRequest"
        try:
            def validate_redirect(candidate: str) -> None:
                if _url_kind(candidate) != kind:
                    raise UnsafeUrlError("Redirección de horario no permitida")

            def validate_response(response: PublicHttpResponse) -> None:
                if is_ajax and response.metadata.media_type != "application/json":
                    raise ClassTimetableSchemaChangedError(
                        "El endpoint de horario no devolvió application/json"
                    )
                validate(response.content)

            return await self._fetcher.get(
                url,
                headers=headers,
                validate_redirect=validate_redirect,
                validate=validate_response,
            )
        except PublicHttpError as exc:
            raise ClassTimetableError(str(exc)) from None

    async def _fetch_index(
        self, center: DegreeCenter
    ) -> tuple[tuple[DegreeTimetablePage, ...], PublicHttpCacheMetadata]:
        def validate_index(content: bytes) -> None:
            parse_timetable_index_html(
                content.decode("utf-8", errors="replace"), center.timetable_index_url
            )

        response = await self._get(
            center.timetable_index_url,
            kind="index",
            validate=validate_index,
        )
        entries = parse_timetable_index_html(
            response.content.decode("utf-8", errors="replace"),
            response.metadata.final_url,
        )
        return entries, response.metadata

    async def find_degree_timetables(
        self, degree_url: str, *, course_number: int | None = None
    ) -> dict[str, object]:
        """Descubre los horarios que los centros enlazan para una titulación."""

        course_number = _validated_course_number(course_number)

        def validate_degree(content: bytes) -> None:
            parse_degree_page_html(content.decode("utf-8", errors="replace"), degree_url)

        page = await self._get(degree_url, kind="degree", validate=validate_degree)
        degree_name, centers = parse_degree_page_html(
            page.content.decode("utf-8", errors="replace"), page.metadata.final_url
        )
        fetched = await asyncio.gather(
            *(self._fetch_index(center) for center in centers), return_exceptions=True
        )
        target = _normalise_degree_name(degree_name)
        name_matches: list[DegreeTimetablePage] = []
        issues: list[dict[str, object]] = []
        metadata = [page.metadata]
        for center, result in zip(centers, fetched, strict=True):
            if isinstance(result, BaseException):
                if not isinstance(result, Exception):
                    raise result
                issues.append(
                    {
                        "center": center.public_dict(),
                        "message": str(result),
                        "affects_completeness": True,
                    }
                )
                continue
            entries, index_metadata = result
            metadata.append(index_metadata)
            for entry in entries:
                candidate = _normalise_degree_name(entry.degree_name)
                if candidate != target:
                    continue
                name_matches.append(entry)

        selected_edition = _degree_edition(degree_name)
        if selected_edition is not None:
            explicit_editions = {
                edition
                for entry in name_matches
                if (edition := _degree_edition(entry.degree_name)) is not None
            }
            if explicit_editions:
                selected_program_ids = {
                    entry.program_id
                    for entry in name_matches
                    if _degree_edition(entry.degree_name) == selected_edition
                }
                name_matches = [
                    entry
                    for entry in name_matches
                    if _degree_edition(entry.degree_name) == selected_edition
                    or (
                        _degree_edition(entry.degree_name) is None
                        and entry.program_id in selected_program_ids
                    )
                ]

        matches = [
            entry
            for entry in name_matches
            if course_number is None or entry.course_number == course_number
        ]
        matches.sort(key=lambda item: (item.course_number, item.program_id, item.url))
        program_ids_by_key: dict[tuple[str, int], set[int]] = {}
        for item in matches:
            program_ids_by_key.setdefault(
                (_normalise_degree_name(item.degree_name), item.course_number), set()
            ).add(item.program_id)
        ambiguous_keys = {
            key for key, program_ids in program_ids_by_key.items() if len(program_ids) > 1
        }
        return {
            "degree_name": degree_name,
            "edition": selected_edition,
            "degree_url": page.metadata.final_url,
            "course_number": course_number,
            "centers": [center.public_dict() for center in centers],
            "timetables": [
                {
                    **item.public_dict(),
                    "ambiguous_same_title_and_course": (
                        _normalise_degree_name(item.degree_name), item.course_number
                    )
                    in ambiguous_keys,
                }
                for item in matches
            ],
            "count": len(matches),
            "issues": issues,
            "complete": not issues,
            "cache": public_cache_summary(metadata),
            "content_is_untrusted": True,
        }

    async def fetch_degree_timetable(
        self,
        degree_url: str,
        *,
        course_number: int,
        academic_year: str,
        semester: int = 1,
        date_in_week: str | None = None,
        group_codes: Sequence[str] | None = None,
        subject_query: str = "",
        program_id: int | None = None,
    ) -> dict[str, object]:
        """Resolve and aggregate the timetable for one explicitly selected degree."""

        course_number = _validated_course_number(course_number)
        assert course_number is not None
        academic_year = normalise_academic_year(academic_year)
        semester = _validated_semester(semester)
        groups = _validated_groups(group_codes)
        if program_id is not None and (
            isinstance(program_id, bool)
            or not isinstance(program_id, int)
            or not 1 <= program_id <= 99_999_999
        ):
            raise ValueError("program_id debe ser un entero positivo")

        discovery = await self.find_degree_timetables(
            degree_url, course_number=course_number
        )
        candidates = list(discovery["timetables"])
        available_program_ids = sorted({int(item["program_id"]) for item in candidates})
        common = {
            "degree_name": discovery["degree_name"],
            "degree_url": discovery["degree_url"],
            "course_number": course_number,
            "academic_year": academic_year,
            "semester": semester,
            "date_in_week": date_in_week,
            "available_program_ids": available_program_ids,
            "discovery": {
                "centers": discovery["centers"],
                "issues": discovery["issues"],
                "complete": discovery["complete"],
                "cache": discovery["cache"],
            },
            "read_only": True,
            "authentication_required": False,
            "content_is_untrusted": True,
        }
        if not candidates:
            discovery_complete = bool(discovery["complete"])
            return {
                **common,
                "status": (
                    "timetable_not_published"
                    if discovery_complete
                    else "source_unavailable"
                ),
                "selected_program_id": program_id,
                "program_options": [],
                "sources": [],
                "sessions": [],
                "count": 0,
                "complete": discovery_complete,
                "note": (
                    "Los centros consultados no enlazan un horario estructurado para ese curso. "
                    "Esto no autoriza a usar el horario de una carrera parecida."
                    if discovery_complete
                    else "No se pudo comprobar algún índice oficial de la titulación; no es "
                    "posible concluir que el horario no esté publicado."
                ),
            }
        if program_id is None and len(available_program_ids) > 1:
            return {
                **common,
                "status": "program_selection_required",
                "selected_program_id": None,
                "program_options": [
                    {
                        "program_id": candidate_program_id,
                        "timetables": [
                            item
                            for item in candidates
                            if int(item["program_id"]) == candidate_program_id
                        ],
                        "content_is_untrusted": True,
                    }
                    for candidate_program_id in available_program_ids
                ],
                "sources": [],
                "sessions": [],
                "count": 0,
                "complete": False,
                "note": (
                    "La USC publica varios planes con el mismo nombre y curso. Elige uno de "
                    "los program_id devueltos; el MCP no decide por semejanza."
                ),
            }
        selected_program_id = program_id or available_program_ids[0]
        if selected_program_id not in available_program_ids:
            raise ValueError(
                "program_id no pertenece a los horarios publicados para la titulación y curso"
            )
        selected = [
            item for item in candidates if int(item["program_id"]) == selected_program_id
        ]
        outcomes = await asyncio.gather(
            *(
                self.fetch_timetable(
                    str(item["timetable_url"]),
                    academic_year=academic_year,
                    semester=semester,
                    date_in_week=date_in_week,
                    group_codes=groups,
                    subject_query=subject_query,
                )
                for item in selected
            ),
            return_exceptions=True,
        )
        center_names = {
            urlparse(str(center["timetable_index_url"])).path.rstrip("/").split("/")[-3]: str(
                center["name"]
            )
            for center in discovery["centers"]
        }
        source_results: list[dict[str, object]] = []
        sessions: list[dict[str, object]] = []
        errors = 0
        ok_sources = 0
        no_data_sources = 0
        for timetable, outcome in zip(selected, outcomes, strict=True):
            center_slug = str(timetable["center_slug"])
            if isinstance(outcome, BaseException):
                if not isinstance(outcome, Exception):
                    raise outcome
                errors += 1
                source_results.append(
                    {
                        "status": "error",
                        "center_slug": center_slug,
                        "center_name": center_names.get(center_slug),
                        "program_id": selected_program_id,
                        "timetable_url": timetable["timetable_url"],
                        "message": (
                            str(outcome)
                            if isinstance(
                                outcome,
                                (ClassTimetableError, UnsafeUrlError, ValueError),
                            )
                            else "Fallo inesperado al consultar la fuente oficial"
                        ),
                        "content_is_untrusted": True,
                    }
                )
                continue
            source_status = str(outcome["status"])
            ok_sources += source_status == "ok"
            no_data_sources += source_status == "no_data"
            source_results.append(
                {
                    "status": source_status,
                    "center_slug": center_slug,
                    "center_name": center_names.get(center_slug),
                    "program_id": selected_program_id,
                    "timetable_url": outcome["timetable_url"],
                    "schedule_endpoint_url": outcome["schedule_endpoint_url"],
                    "week": outcome["week"],
                    "available_academic_years": outcome["available_academic_years"],
                    "available_semesters": outcome["available_semesters"],
                    "available_weeks": outcome["available_weeks"],
                    "available_group_codes": outcome["available_group_codes"],
                    "count": outcome["count"],
                    "note": outcome.get("note"),
                    "cache": outcome["cache"],
                    "content_is_untrusted": True,
                }
            )
            for session in outcome["sessions"]:
                sessions.append(
                    {
                        **session,
                        "center_slug": center_slug,
                        "center_name": center_names.get(center_slug),
                        "program_id": selected_program_id,
                        "timetable_url": outcome["timetable_url"],
                        "schedule_endpoint_url": outcome["schedule_endpoint_url"],
                    }
                )
        sessions.sort(
            key=lambda item: (
                str(item["date"]),
                str(item["start_time"]),
                str(item["subject_name"]).casefold(),
                str(item.get("group_code") or ""),
                str(item["center_slug"]),
            )
        )
        discovery_complete = bool(discovery["complete"])
        if ok_sources:
            status = "partial" if errors or not discovery_complete else "ok"
        elif no_data_sources:
            status = "partial" if errors or not discovery_complete else "no_data"
        else:
            status = "source_unavailable"
        return {
            **common,
            "status": status,
            "selected_program_id": selected_program_id,
            "program_options": [],
            "sources": source_results,
            "sessions": sessions,
            "count": len(sessions),
            "complete": not errors and discovery_complete,
            "filters": {
                "group_codes": list(groups),
                "subject_query": subject_query.strip(),
            },
        }

    async def fetch_timetable(
        self,
        timetable_url: str,
        *,
        academic_year: str,
        semester: int = 1,
        date_in_week: str | None = None,
        group_codes: Sequence[str] | None = None,
        subject_query: str = "",
    ) -> dict[str, object]:
        """Obtiene una semana lectiva exacta de una página de grado/curso."""

        academic_year = normalise_academic_year(academic_year)
        semester = _validated_semester(semester)
        groups = _validated_groups(group_codes)
        subject_query = subject_query.strip()
        if len(subject_query) > 300:
            raise ValueError("subject_query no puede superar 300 caracteres")
        requested_date: date | None = None
        if date_in_week:
            try:
                requested_date = date.fromisoformat(date_in_week)
            except ValueError:
                raise ValueError("date_in_week debe tener formato YYYY-MM-DD") from None

        def validate_page(content: bytes) -> None:
            discover_timetable_year_endpoint(
                content.decode("utf-8", errors="replace"), timetable_url, academic_year
            )

        page = await self._get(timetable_url, kind="timetable", validate=validate_page)
        year_endpoint, available_years = discover_timetable_year_endpoint(
            page.content.decode("utf-8", errors="replace"),
            page.metadata.final_url,
            academic_year,
        )

        def validate_year_payload(content: bytes) -> None:
            reported = _ajax_academic_year(content)
            if reported is not None and reported != academic_year:
                raise ClassTimetableParseError("Drupal devolvió otro curso académico")
            semesters = _semester_links(content, year_endpoint)
            extract_timetable_insert(content)
            if not semesters and not _is_explicit_no_data(content):
                raise ClassTimetableParseError(
                    "El horario no anuncia semestres ni una ausencia explícita de datos"
                )

        year_payload = await self._get(
            year_endpoint, kind="year_ajax", validate=validate_year_payload
        )
        semesters = _semester_links(year_payload.content, year_endpoint)
        timetable_match = _TIMETABLE_PATH.fullmatch(_safe_path(page.metadata.final_url))
        assert timetable_match is not None
        if not semesters:
            return {
                "status": "no_data",
                "academic_year": academic_year,
                "semester": semester,
                "week": None,
                "course_number": int(timetable_match.group("course")),
                "program_id": int(timetable_match.group("program")),
                "timetable_url": page.metadata.final_url,
                "schedule_endpoint_url": year_endpoint,
                "available_academic_years": list(available_years),
                "available_semesters": [],
                "available_weeks": [],
                "available_group_codes": [],
                "filters": {
                    "group_codes": list(groups),
                    "subject_query": subject_query,
                    "date_in_week": requested_date.isoformat() if requested_date else None,
                },
                "sessions": [],
                "count": 0,
                "note": (
                    "La fuente oficial indica que no hay datos para ese curso académico "
                    "en este centro. Consulta las otras páginas devueltas por "
                    "list_degree_timetables si la titulación se imparte en varios centros."
                ),
                "cache": public_cache_summary([page.metadata, year_payload.metadata]),
                "read_only": True,
                "authentication_required": False,
                "content_is_untrusted": True,
            }
        if semester not in semesters:
            raise ClassTimetableError(f"El horario no publica el semestre {semester}")
        _, semester_endpoint = semesters[semester]

        def validate_semester_payload(content: bytes) -> None:
            _week_links(content, semester_endpoint, academic_year, semester)
            extract_timetable_insert(content)

        semester_payload = await self._get(
            semester_endpoint,
            kind="semester_ajax",
            validate=validate_semester_payload,
        )
        weeks = _week_links(
            semester_payload.content, semester_endpoint, academic_year, semester
        )
        selected_week = weeks[0]
        if requested_date is not None:
            selected_week = next(
                (item for item in weeks if item.start <= requested_date <= item.end),
                None,
            )
            if selected_week is None:
                raise ValueError(
                    "date_in_week no pertenece a ninguna semana publicada para ese semestre"
                )

        metadata = [page.metadata, year_payload.metadata, semester_payload.metadata]
        schedule_source = semester_endpoint
        schedule_html = extract_timetable_insert(semester_payload.content)
        if selected_week != weeks[0]:
            def validate_week_payload(content: bytes) -> None:
                extract_timetable_insert(content)

            week_payload = await self._get(
                selected_week.endpoint_url,
                kind="week_ajax",
                validate=validate_week_payload,
            )
            metadata.append(week_payload.metadata)
            schedule_source = selected_week.endpoint_url
            schedule_html = extract_timetable_insert(week_payload.content)

        sessions = list(
            parse_timetable_html(schedule_html, schedule_source, week=selected_week)
        )
        available_groups = sorted(
            {item.group_code for item in sessions if item.group_code is not None}
        )
        if groups:
            wanted = set(groups)
            sessions = [item for item in sessions if item.group_code in wanted]
        if subject_query:
            query = _fold(subject_query)
            sessions = [item for item in sessions if query in _fold(item.subject_name)]
        sessions.sort(
            key=lambda item: (
                item.date,
                item.start_time,
                item.subject_name.casefold(),
                item.group_code or "",
            )
        )
        return {
            "status": "ok",
            "academic_year": academic_year,
            "semester": semester,
            "week": selected_week.public_dict(),
            "course_number": int(timetable_match.group("course")),
            "program_id": int(timetable_match.group("program")),
            "timetable_url": page.metadata.final_url,
            "schedule_endpoint_url": schedule_source,
            "available_academic_years": list(available_years),
            "available_semesters": sorted(semesters),
            "available_weeks": [item.public_dict() for item in weeks],
            "available_group_codes": available_groups,
            "filters": {
                "group_codes": list(groups),
                "subject_query": subject_query,
                "date_in_week": requested_date.isoformat() if requested_date else None,
            },
            "sessions": [item.public_dict() for item in sessions],
            "count": len(sessions),
            "cache": public_cache_summary(metadata),
            "read_only": True,
            "authentication_required": False,
            "content_is_untrusted": True,
        }
