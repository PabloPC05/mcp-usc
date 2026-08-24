"""Una interfaz de solo lectura para los planes de estudios públicos de la USC.

El sitio de estudios de la USC usa páginas Drupal normales para anunciar el
endpoint AJAX ``study-plan-by-course``.  Este módulo mantiene deliberadamente
la superficie pequeña: no sigue enlaces arbitrarios, no usa sesión/cookies y
rechaza HTML que no tenga la forma conocida del componente Drupal.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import unquote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, Tag

from .security import UnsafeUrlError, validate_usc_url

_USER_AGENT = "mcp-usc/0.6 (+https://github.com/PabloPC05/mcp-usc)"
_ACADEMIC_YEAR = re.compile(r"^20(?P<start>\d{2})/20(?P<end>\d{2})$")
_SUBJECT_CODE = re.compile(r"^G\d{7}$")
_AJAX_PATH = re.compile(r"^/(?:gl|es|en)/course/\d+/study-plan-by-course/\d+/?$")
_STUDIES_PATH = re.compile(r"^/(?:gl|es|en)/(?:estudos|estudios|studies)(?:/[^/]+)+/?$")
_ENCODED_SEPARATOR = re.compile(r"%(?:2f|5c)", re.IGNORECASE)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_YEAR_LABEL = re.compile(r"^(?:Curso|Curso académico|Course|Academic year)\s+(20\d{2}/20\d{2})$")


class StudyPlanError(RuntimeError):
    """A public USC study-plan resource could not be read safely."""


class StudyPlanParseError(StudyPlanError):
    """A response did not match the expected official USC structure."""


# Compatibility with the naming used by the other public USC clients.
StudyPlansError = StudyPlanError
StudyPlansParseError = StudyPlanParseError


@dataclass(frozen=True, slots=True)
class StudyPlanSubject:
    """A subject listed by an official study plan.

    ``sheet_url`` is the link printed next to the exact academic code.  It is
    never made from the code or the URL slug.
    """

    code: str
    name: str
    sheet_url: str

    @property
    def url(self) -> str:
        """Alias useful to callers that model links simply as ``url``."""

        return self.sheet_url


StudyPlanEntry = StudyPlanSubject


@dataclass(frozen=True, slots=True)
class StudyPlan:
    academic_year: str | None
    endpoint_url: str
    source_url: str
    subjects: tuple[StudyPlanSubject, ...]


@dataclass(frozen=True, slots=True)
class StudyDegreePage:
    source_url: str
    title: str
    endpoint_urls: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StudyPlanSheet:
    source_url: str
    title: str
    code: str | None
    academic_year: str | None


def _clean_text(value: str) -> str:
    return " ".join(value.split())


def _validate_academic_year(value: str) -> str:
    match = _ACADEMIC_YEAR.fullmatch(value)
    if not match or int(match.group("end")) != int(match.group("start")) + 1:
        raise ValueError("academic_year debe tener el formato consecutivo 2025/2026")
    return value


def _safe_path(url: str) -> str:
    validated = validate_usc_url(url)
    parsed = urlparse(validated)
    if (parsed.hostname or "").casefold().rstrip(".") not in {"www.usc.gal", "usc.gal"}:
        raise UnsafeUrlError("Host no permitido para planes de estudios públicos")
    decoded_path = unquote(parsed.path)
    if (
        parsed.query
        or parsed.fragment
        or _CONTROL.search(decoded_path)
        or "\\" in decoded_path
        or _ENCODED_SEPARATOR.search(parsed.path)
        or any(part in {".", ".."} for part in decoded_path.split("/"))
    ):
        raise UnsafeUrlError("URL USC no permitida para planes de estudios públicos")
    return decoded_path


def _validated_url(url: str, kind: Literal["page", "ajax", "subject"]) -> str:
    path = _safe_path(url)
    # A subject sheet and a degree page are both public Drupal study routes.
    # The parser further requires a printed title/code; no URL slug is trusted.
    valid = _AJAX_PATH.fullmatch(path) if kind == "ajax" else _STUDIES_PATH.fullmatch(path)
    if not valid:
        raise UnsafeUrlError("La URL no es una ruta pública de estudios USC permitida")
    return url


def _validated_ajax_or_page(url: str) -> Literal["page", "ajax"]:
    path = _safe_path(url)
    if _AJAX_PATH.fullmatch(path):
        return "ajax"
    if _STUDIES_PATH.fullmatch(path):
        return "page"
    raise UnsafeUrlError("La URL no es una página o endpoint oficial de plan USC")


def discover_study_plan_endpoint(
    canonical_html: str, canonical_url: str, academic_year: str
) -> str:
    """Return the single endpoint link labelled with ``academic_year``.

    The label and endpoint are both checked.  In particular, a matching label
    pointing off-site is an error rather than a candidate to be ignored.
    """

    if _validated_ajax_or_page(canonical_url) != "page":
        raise UnsafeUrlError("Se esperaba la URL canónica de una titulación USC")
    year = _validate_academic_year(academic_year)
    soup = BeautifulSoup(canonical_html, "html.parser")
    candidates: list[str] = []
    for link in soup.select("a[href]"):
        label = _clean_text(link.get_text(" ", strip=True))
        if not label:
            # Drupal nests these anchors inside <template>; BeautifulSoup omits
            # TemplateString from get_text(), but the literal node remains available.
            label = _clean_text(str(link.string or link.get("aria-label", "")))
        match = _YEAR_LABEL.fullmatch(label)
        if not match or match.group(1) != year:
            continue
        candidate = urljoin(canonical_url, str(link.get("href", "")))
        candidate_path = urlparse(candidate).path
        if re.fullmatch(
            r"/(?:gl|es|en)/course/\d+/study-plan-by-module/\d+/?", candidate_path
        ):
            continue
        if _validated_ajax_or_page(candidate) != "ajax":
            raise UnsafeUrlError("El enlace de curso no apunta al endpoint de plan USC")
        if candidate not in candidates:
            candidates.append(candidate)
    if len(candidates) != 1:
        raise StudyPlanParseError(
            f"Se esperaba un único enlace oficial para el curso {year}; encontrados: "
            f"{len(candidates)}"
        )
    return candidates[0]


# Short alias used by callers that do not distinguish discovery from parsing.
discover_plan_endpoint = discover_study_plan_endpoint


def _decode_json(payload: str | bytes) -> object:
    try:
        decoded = payload.decode("utf-8") if isinstance(payload, bytes) else payload
        return json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        raise StudyPlanParseError("La respuesta AJAX del plan no es JSON válido") from None


def extract_study_plan_insert(payload: str | bytes) -> str:
    """Extract exactly Drupal's ``#study-plan-by-course`` replacement command."""

    commands = _decode_json(payload)
    if not isinstance(commands, list):
        raise StudyPlanParseError("La respuesta AJAX del plan no es una lista")
    if len(commands) > 64:
        raise StudyPlanParseError("La respuesta AJAX contiene demasiados comandos")
    inserts = [
        command.get("data")
        for command in commands
        if isinstance(command, dict)
        and command.get("command") == "insert"
        and command.get("method") == "replaceWith"
        and command.get("selector") == "#study-plan-by-course"
        and isinstance(command.get("data"), str)
    ]
    if len(inserts) != 1:
        raise StudyPlanParseError(
            "La respuesta AJAX no contiene una única inserción del plan de estudios"
        )
    if len(inserts[0].encode("utf-8")) > 5_000_000:
        raise StudyPlanError("El fragmento HTML del plan es demasiado grande")
    return inserts[0]


def study_plan_academic_year(payload: str | bytes) -> str | None:
    """Read Drupal's explicit academic-course command, rejecting contradictions."""

    commands = _decode_json(payload)
    if not isinstance(commands, list):
        raise StudyPlanParseError("La respuesta AJAX del plan no es una lista")
    years: list[str] = []
    for command in commands:
        if not isinstance(command, dict) or command.get("command") != "UpdateAcademicCourse":
            continue
        if command.get("selector") != "study-plan-by-course":
            continue
        value = _clean_text(str(command.get("value", "")))
        match = re.search(r"20\d{2}/20\d{2}", value)
        if not match:
            raise StudyPlanParseError("Drupal devolvió un curso académico no válido")
        year = _validate_academic_year(match.group(0))
        if year not in years:
            years.append(year)
    if len(years) > 1:
        raise StudyPlanParseError("Drupal devolvió varios cursos académicos distintos")
    return years[0] if years else None


def _subject_url(value: str, source_url: str) -> str:
    candidate = urljoin(source_url, value)
    # All subject links must be public study routes.  This prevents links in an
    # injected fragment from turning the client into a general web crawler.
    return _validated_url(candidate, "subject")


def parse_study_plan_html(
    plan_html: str, source_url: str, *, academic_year: str | None = None
) -> tuple[StudyPlanSubject, ...]:
    """Parse USC's ``h3.at-title`` + adjacent ``ul.academic-subject-specs-list``.

    The current Drupal markup places the title link immediately before a specs
    list whose first item is the code.  We require that relation and reject
    duplicate/ambiguous entries or schema changes; no code is recovered from a
    subject URL slug.
    """

    source_path = _safe_path(source_url)
    if not (_AJAX_PATH.fullmatch(source_path) or _STUDIES_PATH.fullmatch(source_path)):
        raise UnsafeUrlError("La fuente del fragmento no es una ruta pública de estudios USC")
    if academic_year is not None:
        _validate_academic_year(academic_year)
    soup = BeautifulSoup(plan_html, "html.parser")
    container = soup.select_one("#study-plan-by-course")
    if not isinstance(container, Tag):
        raise StudyPlanParseError("El fragmento del plan no contiene #study-plan-by-course")
    subjects: list[StudyPlanSubject] = []
    seen: set[str] = set()
    seen_urls: set[str] = set()
    items = container.select("h3.at-title")
    if not items or len(items) > 2_000:
        raise StudyPlanParseError("El plan no contiene un número válido de materias")
    for title in items:
        links = title.find_all("a", href=True, recursive=False)
        if len(links) != 1:
            raise StudyPlanParseError("Título de materia sin un único enlace oficial")
        link = links[0]
        name = _clean_text(link.get_text(" ", strip=True))
        if not name or len(name) > 500:
            raise StudyPlanParseError("Nombre de materia ausente o demasiado largo")
        specs = title.find_next_sibling()
        if (
            not isinstance(specs, Tag)
            or specs.name != "ul"
            or "academic-subject-specs-list" not in specs.get("class", [])
        ):
            raise StudyPlanParseError("Materia sin lista de especificaciones adyacente")
        code_cells = specs.find_all("li", recursive=False)
        if not code_cells:
            raise StudyPlanParseError("Materia sin código académico")
        code = _clean_text(code_cells[0].get_text(" ", strip=True))
        if not _SUBJECT_CODE.fullmatch(code):
            raise StudyPlanParseError("Código de materia no coincide exactamente con G\\d{7}")
        if code in seen:
            raise StudyPlanParseError(f"El plan contiene el código de materia duplicado {code}")
        sheet_url = _subject_url(str(link["href"]), source_url)
        if sheet_url in seen_urls:
            raise StudyPlanParseError("El plan contiene enlaces de fichas duplicados")
        seen.add(code)
        seen_urls.add(sheet_url)
        subjects.append(StudyPlanSubject(code=code, name=name, sheet_url=sheet_url))
    return tuple(subjects)


def parse_study_plan_page_html(html: str, source_url: str) -> StudyDegreePage:
    """Parse a degree page and its explicitly advertised study-plan endpoints."""

    _validated_url(source_url, "page")
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.find("h1")
    if not isinstance(heading, Tag):
        raise StudyPlanParseError("Página de titulación sin título")
    title = _clean_text(heading.get_text(" ", strip=True))
    if not title or len(title) > 500:
        raise StudyPlanParseError("Título de titulación ausente o demasiado largo")
    endpoints: list[str] = []
    for link in soup.select("a[href]"):
        candidate = urljoin(source_url, str(link["href"]))
        try:
            candidate_kind = _validated_ajax_or_page(candidate)
        except UnsafeUrlError:
            continue
        if candidate_kind == "ajax" and candidate not in endpoints:
            endpoints.append(candidate)
            if len(endpoints) > 20:
                raise StudyPlanParseError("La página anuncia demasiados endpoints de planes")
    return StudyDegreePage(source_url=source_url, title=title, endpoint_urls=tuple(endpoints))


def parse_study_plan_sheet_html(html: str, source_url: str) -> StudyPlanSheet:
    """Parse only facts printed on a public subject sheet."""

    _validated_url(source_url, "subject")
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.find("h1")
    if not isinstance(heading, Tag):
        raise StudyPlanParseError("Ficha de materia sin título")
    title = _clean_text(heading.get_text(" ", strip=True))
    if not title:
        raise StudyPlanParseError("Ficha de materia sin título")
    codes: list[str] = []
    for element in soup.select("[data-subject-code]"):
        value = _clean_text(str(element.get("data-subject-code", "")))
        if _SUBJECT_CODE.fullmatch(value) and value not in codes:
            codes.append(value)
    for label in soup.find_all(["dt", "b", "strong"]):
        if _clean_text(label.get_text(" ", strip=True)).casefold() not in {
            "código",
            "codigo",
            "código da materia",
            "codigo da materia",
            "subject code",
            "code",
        }:
            continue
        if label.name == "dt":
            value_node = label.find_next_sibling("dd")
            value = value_node.get_text(" ", strip=True) if isinstance(value_node, Tag) else ""
        elif isinstance(label.parent, Tag):
            value = (
                label.parent.get_text(" ", strip=True)
                .removeprefix(label.get_text(" ", strip=True))
                .strip(" :")
            )
        else:
            value = ""
        value = _clean_text(value)
        if _SUBJECT_CODE.fullmatch(value) and value not in codes:
            codes.append(value)
    if len(codes) > 1:
        raise StudyPlanParseError("La ficha contiene códigos de materia contradictorios")
    years = []
    for text in soup.stripped_strings:
        for match in re.finditer(r"20\d{2}/20\d{2}", text):
            year = _validate_academic_year(match.group(0))
            if year not in years:
                years.append(year)
    if len(years) > 1:
        raise StudyPlanParseError("Ficha con varios cursos académicos")
    return StudyPlanSheet(
        source_url=source_url,
        title=title,
        code=codes[0] if codes else None,
        academic_year=years[0] if years else None,
    )


class UscStudyPlanClient:
    """Unauthenticated, GET-only client for public USC study plans."""

    def __init__(
        self,
        *,
        timeout: float = 20.0,
        max_bytes: int = 8_000_000,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout debe ser positivo")
        if max_bytes < 1 or max_bytes > 20_000_000:
            raise ValueError("max_bytes debe estar entre 1 y 20000000")
        self.timeout, self.max_bytes, self.transport = timeout, max_bytes, transport

    async def _get(self, url: str, *, kind: Literal["page", "ajax", "subject"]) -> bytes:
        current = _validated_url(url, kind)
        headers = {
            "User-Agent": _USER_AGENT,
            "Accept": "application/json" if kind == "ajax" else "text/html",
        }
        if kind == "ajax":
            headers["X-Requested-With"] = "XMLHttpRequest"
        async with httpx.AsyncClient(
            timeout=self.timeout, follow_redirects=False, transport=self.transport
        ) as client:
            for _ in range(5):
                try:
                    async with client.stream("GET", current, headers=headers) as response:
                        if response.is_redirect:
                            location = response.headers.get("location")
                            if not location:
                                raise StudyPlanError("Redirección USC sin destino")
                            candidate = urljoin(current, location)
                            old, new = urlparse(current), urlparse(candidate)
                            if (new.scheme, new.hostname, new.port) != (
                                old.scheme,
                                old.hostname,
                                old.port,
                            ):
                                raise UnsafeUrlError("Redirección fuera del origen exacto de USC")
                            _validated_url(candidate, kind)
                            client.cookies.clear()
                            current = candidate
                            continue
                        if response.status_code >= 400:
                            raise StudyPlanError(
                                f"La fuente USC respondió con HTTP {response.status_code}"
                            )
                        if (
                            kind == "ajax"
                            and response.headers.get("content-type", "").split(";", 1)[0].casefold()
                            != "application/json"
                        ):
                            raise StudyPlanParseError(
                                "El endpoint de plan no devolvió application/json"
                            )
                        declared = response.headers.get("content-length")
                        if declared and declared.isdigit() and int(declared) > self.max_bytes:
                            raise StudyPlanError(
                                f"La respuesta supera el límite de {self.max_bytes} bytes"
                            )
                        chunks: list[bytes] = []
                        size = 0
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > self.max_bytes:
                                raise StudyPlanError(
                                    f"La respuesta supera el límite de {self.max_bytes} bytes"
                                )
                            chunks.append(chunk)
                        return b"".join(chunks)
                except httpx.HTTPError:
                    raise StudyPlanError("No se pudo leer la fuente pública USC") from None
        raise StudyPlanError("Demasiadas redirecciones en la fuente pública USC")

    async def fetch_study_plan(self, url: str, *, academic_year: str | None = None) -> StudyPlan:
        kind = _validated_ajax_or_page(url)
        if academic_year is not None:
            academic_year = _validate_academic_year(academic_year)
        source_url = url
        if kind == "page":
            if academic_year is None:
                raise ValueError("academic_year es obligatorio al partir de la página canónica")
            page = await self._get(url, kind="page")
            endpoint = discover_study_plan_endpoint(
                page.decode("utf-8", errors="replace"), url, academic_year
            )
        else:
            endpoint = url
        payload = await self._get(endpoint, kind="ajax")
        reported = study_plan_academic_year(payload)
        if academic_year is not None and reported is None:
            raise StudyPlanParseError("Drupal no confirmó el curso académico solicitado")
        if reported is not None:
            if academic_year is not None and reported != academic_year:
                raise StudyPlanParseError(
                    "El curso devuelto por Drupal no coincide con el solicitado"
                )
            academic_year = reported
        fragment = extract_study_plan_insert(payload)
        subjects = parse_study_plan_html(fragment, endpoint, academic_year=academic_year)
        return StudyPlan(
            academic_year=academic_year,
            endpoint_url=endpoint,
            source_url=source_url,
            subjects=subjects,
        )

    async def fetch_subject_sheet(self, url: str) -> StudyPlanSheet:
        content = await self._get(url, kind="subject")
        return parse_study_plan_sheet_html(content.decode("utf-8", errors="replace"), url)


# Alternative spelling used by a few integrations.
USCStudyPlanClient = UscStudyPlanClient
UscStudyPlansClient = UscStudyPlanClient
parse_study_plan_fragment_html = parse_study_plan_html
extract_study_plan_html = extract_study_plan_insert


__all__ = [
    "StudyPlanError",
    "StudyPlanParseError",
    "StudyPlansError",
    "StudyPlansParseError",
    "StudyPlanSubject",
    "StudyPlanEntry",
    "StudyPlan",
    "StudyDegreePage",
    "StudyPlanSheet",
    "discover_study_plan_endpoint",
    "discover_plan_endpoint",
    "extract_study_plan_insert",
    "study_plan_academic_year",
    "parse_study_plan_html",
    "parse_study_plan_page_html",
    "parse_study_plan_sheet_html",
    "UscStudyPlanClient",
    "USCStudyPlanClient",
    "UscStudyPlansClient",
    "parse_study_plan_fragment_html",
    "extract_study_plan_html",
]
