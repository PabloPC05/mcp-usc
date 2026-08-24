"""Una interfaz de solo lectura para los planes de estudios públicos de la USC.

El sitio de estudios de la USC usa páginas Drupal normales para anunciar el
endpoint AJAX ``study-plan-by-course``.  Este módulo mantiene deliberadamente
la superficie pequeña: no sigue enlaces arbitrarios, no usa sesión/cookies y
rechaza HTML que no tenga la forma conocida del componente Drupal.
"""

from __future__ import annotations

import json
import re
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
    PublicHttpStatusError,
    SafePublicHttpFetcher,
)
from .security import UnsafeUrlError, validate_usc_url

_USER_AGENT = "mcp-usc/0.7 (+https://github.com/PabloPC05/mcp-usc)"
_ACADEMIC_YEAR = re.compile(r"^20(?P<start>\d{2})/20(?P<end>\d{2})$")
_AJAX_PATH = re.compile(r"^/(?:gl|es|en)/course/\d+/study-plan-by-course/\d+/?$")
_MODULE_PATH = re.compile(r"^/(?:gl|es|en)/course/\d+/study-plan-by-module/\d+/?$")
_STUDIES_PATH = re.compile(r"^/(?:gl|es|en)/(?:estudos|estudios|studies)(?:/[^/]+)+/?$")
_ENCODED_SEPARATOR = re.compile(r"%(?:2f|5c)", re.IGNORECASE)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_YEAR_LABEL = re.compile(r"^(?:Curso|Curso académico|Course|Academic year)\s+(20\d{2}/20\d{2})$")


class StudyPlanError(RuntimeError):
    """A public USC study-plan resource could not be read safely."""


class StudyPlanParseError(StudyPlanError):
    """A response did not match the expected official USC structure."""


class StudyPlanSchemaChangedError(StudyPlanParseError):
    """The official response changed shape and was rejected before caching."""


class StudyPlanAcademicYearUnavailable(StudyPlanError):
    """The degree page does not advertise the requested academic year."""

    def __init__(
        self, academic_year: str, available_academic_years: tuple[str, ...]
    ) -> None:
        requested = _validate_academic_year(academic_year)
        available = tuple(
            dict.fromkeys(_validate_academic_year(value) for value in available_academic_years)
        )
        if not available or requested in available:
            raise ValueError(
                "La ausencia de curso requiere evidencia de otros cursos reconocidos"
            )
        self.academic_year = requested
        self.available_academic_years = available
        super().__init__(
            f"La titulación no publica un plan para el curso {requested}; "
            f"cursos anunciados: {', '.join(available)}"
        )


class StudyPlanHttpError(StudyPlanError):
    """The allowed study-plan URL returned a structured HTTP failure."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"La fuente USC respondió con HTTP {status_code}")


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
    alternate_sheet_urls: tuple[str, ...] = ()

    @property
    def url(self) -> str:
        """Alias useful to callers that model links simply as ``url``."""

        return self.sheet_url

    @property
    def sheet_urls(self) -> tuple[str, ...]:
        """All official sheet links when a subject appears in several itineraries."""

        return (self.sheet_url, *self.alternate_sheet_urls)


StudyPlanEntry = StudyPlanSubject


@dataclass(frozen=True, slots=True)
class StudyPlan:
    academic_year: str | None
    endpoint_url: str
    source_url: str
    subjects: tuple[StudyPlanSubject, ...]
    cache_metadata: tuple[PublicHttpCacheMetadata, ...] = ()


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
    cache_metadata: tuple[PublicHttpCacheMetadata, ...] = ()


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
    endpoints_by_year: dict[str, str] = {}
    endpoint_years: dict[str, str] = {}
    for link in soup.select("a[href]"):
        label = _clean_text(link.get_text(" ", strip=True))
        if not label:
            # Drupal nests these anchors inside <template>; BeautifulSoup omits
            # TemplateString from get_text(), but the literal node remains available.
            label = _clean_text(str(link.string or link.get("aria-label", "")))
        candidate = urljoin(canonical_url, str(link.get("href", "")))
        candidate_path = urlparse(candidate).path
        if _MODULE_PATH.fullmatch(candidate_path):
            continue
        match = _YEAR_LABEL.fullmatch(label)
        if not _AJAX_PATH.fullmatch(candidate_path):
            if match:
                raise StudyPlanParseError(
                    "Una etiqueta de curso no apunta a un endpoint by-course"
                )
            continue
        _validated_url(candidate, "ajax")
        if not match:
            raise StudyPlanParseError(
                "Un endpoint by-course tiene una etiqueta de curso no reconocida"
            )
        linked_year = _validate_academic_year(match.group(1))
        previous_endpoint = endpoints_by_year.get(linked_year)
        if previous_endpoint is not None and previous_endpoint != candidate:
            raise StudyPlanParseError(
                f"El curso {linked_year} anuncia varios endpoints by-course"
            )
        previous_year = endpoint_years.get(candidate)
        if previous_year is not None and previous_year != linked_year:
            raise StudyPlanParseError(
                "Un endpoint by-course aparece asociado a varios cursos"
            )
        endpoints_by_year[linked_year] = candidate
        endpoint_years[candidate] = linked_year
        if len(endpoint_years) > 20:
            raise StudyPlanParseError("La página anuncia demasiados endpoints de planes")
    if not endpoints_by_year:
        raise StudyPlanParseError(
            "La página no anuncia endpoints by-course con cursos reconocidos"
        )
    endpoint = endpoints_by_year.get(year)
    if endpoint is None:
        raise StudyPlanAcademicYearUnavailable(year, tuple(sorted(endpoints_by_year)))
    candidates = {value for key, value in endpoints_by_year.items() if key == year}
    if len(candidates) != 1:
        raise StudyPlanParseError(
            f"Se esperaba un único enlace oficial para el curso {year}; encontrados: "
            f"{len(candidates)}"
        )
    return endpoint


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
    seen: dict[str, int] = {}
    seen_urls: dict[str, str] = {}
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
        raw_code = _clean_text(code_cells[0].get_text(" ", strip=True))
        try:
            code = normalise_subject_code(raw_code)
        except ValueError:
            raise StudyPlanParseError(
                "Código de materia no coincide con G seguido de 7 cifras y sufijo opcional"
            ) from None
        sheet_url = _subject_url(str(link["href"]), source_url)
        if code in seen:
            index = seen[code]
            existing = subjects[index]
            if name != existing.name:
                raise StudyPlanParseError(
                    f"El plan asigna títulos contradictorios al código {code}"
                )
            owner = seen_urls.get(sheet_url)
            if owner is not None and owner != code:
                raise StudyPlanParseError("Una ficha aparece asociada a varios códigos")
            if sheet_url not in existing.sheet_urls:
                subjects[index] = StudyPlanSubject(
                    code=existing.code,
                    name=existing.name,
                    sheet_url=existing.sheet_url,
                    alternate_sheet_urls=(*existing.alternate_sheet_urls, sheet_url),
                )
                seen_urls[sheet_url] = code
            continue
        owner = seen_urls.get(sheet_url)
        if owner is not None and owner != code:
            raise StudyPlanParseError("Una ficha aparece asociada a varios códigos")
        seen[code] = len(subjects)
        seen_urls[sheet_url] = code
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
        try:
            code = normalise_subject_code(value)
        except ValueError:
            continue
        if code not in codes:
            codes.append(code)
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
        try:
            code = normalise_subject_code(value)
        except ValueError:
            continue
        if code not in codes:
            codes.append(code)
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
        self.timeout, self.max_bytes, self.transport = timeout, max_bytes, transport
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
        _validated_url(url, kind)
        headers = {
            "User-Agent": _USER_AGENT,
            "Accept": "application/json" if kind == "ajax" else "text/html",
        }
        if kind == "ajax":
            headers["X-Requested-With"] = "XMLHttpRequest"
        try:
            def validate_response(response: PublicHttpResponse) -> None:
                if kind == "ajax" and response.metadata.media_type != "application/json":
                    raise StudyPlanSchemaChangedError(
                        "El endpoint de plan no devolvió application/json"
                    )
                validate(response.content)

            response = await self._fetcher.get(
                url,
                headers=headers,
                validate_redirect=lambda candidate: _validated_url(candidate, kind),
                validate=validate_response,
            )
        except PublicHttpStatusError as exc:
            raise StudyPlanHttpError(exc.status_code) from None
        except PublicHttpError as exc:
            raise StudyPlanError(str(exc)) from None
        return response

    @staticmethod
    def _schema_changed(error: BaseException) -> StudyPlanSchemaChangedError:
        return StudyPlanSchemaChangedError(str(error))

    async def fetch_study_plan(self, url: str, *, academic_year: str | None = None) -> StudyPlan:
        kind = _validated_ajax_or_page(url)
        if academic_year is not None:
            academic_year = _validate_academic_year(academic_year)
        source_url = url
        if kind == "page":
            if academic_year is None:
                raise ValueError("academic_year es obligatorio al partir de la página canónica")
            def validate_page(content: bytes) -> None:
                page_html = content.decode("utf-8", errors="replace")
                try:
                    discover_study_plan_endpoint(
                        page_html, url, academic_year
                    )
                except StudyPlanAcademicYearUnavailable:
                    # ``discover_study_plan_endpoint`` only emits this after
                    # exhaustively validating one or more recognised other years.
                    pass
                except StudyPlanParseError as exc:
                    raise self._schema_changed(exc) from None

            page = await self._get(url, kind="page", validate=validate_page)
            try:
                endpoint = discover_study_plan_endpoint(
                    page.content.decode("utf-8", errors="replace"), url, academic_year
                )
            except (StudyPlanParseError, ValueError) as exc:
                raise self._schema_changed(exc) from None
            metadata = [page.metadata]
        else:
            endpoint = url
            metadata = []
        def validate_payload(content: bytes) -> None:
            try:
                candidate_year = study_plan_academic_year(content)
                if academic_year is not None and candidate_year is None:
                    raise StudyPlanParseError(
                        "Drupal no confirmó el curso académico solicitado"
                    )
                if (
                    candidate_year is not None
                    and academic_year is not None
                    and candidate_year != academic_year
                ):
                    raise StudyPlanParseError(
                        "El curso devuelto por Drupal no coincide con el solicitado"
                    )
                candidate_fragment = extract_study_plan_insert(content)
                parse_study_plan_html(
                    candidate_fragment,
                    endpoint,
                    academic_year=candidate_year or academic_year,
                )
            except StudyPlanParseError as exc:
                raise self._schema_changed(exc) from None

        payload = await self._get(endpoint, kind="ajax", validate=validate_payload)
        try:
            reported = study_plan_academic_year(payload.content)
            if academic_year is not None and reported is None:
                raise StudyPlanParseError("Drupal no confirmó el curso académico solicitado")
            if reported is not None:
                if academic_year is not None and reported != academic_year:
                    raise StudyPlanParseError(
                        "El curso devuelto por Drupal no coincide con el solicitado"
                    )
                academic_year = reported
            fragment = extract_study_plan_insert(payload.content)
            subjects = parse_study_plan_html(fragment, endpoint, academic_year=academic_year)
        except (StudyPlanParseError, ValueError) as exc:
            raise self._schema_changed(exc) from None
        metadata.append(payload.metadata)
        return StudyPlan(
            academic_year=academic_year,
            endpoint_url=endpoint,
            source_url=source_url,
            subjects=subjects,
            cache_metadata=tuple(metadata),
        )

    async def fetch_subject_sheet(self, url: str) -> StudyPlanSheet:
        def validate_sheet(candidate: bytes) -> None:
            try:
                parse_study_plan_sheet_html(candidate.decode("utf-8", errors="replace"), url)
            except (StudyPlanParseError, ValueError) as exc:
                raise self._schema_changed(exc) from None

        content = await self._get(url, kind="subject", validate=validate_sheet)
        try:
            parsed = parse_study_plan_sheet_html(
                content.content.decode("utf-8", errors="replace"), url
            )
        except (StudyPlanParseError, ValueError) as exc:
            raise self._schema_changed(exc) from None
        return StudyPlanSheet(
            source_url=parsed.source_url,
            title=parsed.title,
            code=parsed.code,
            academic_year=parsed.academic_year,
            cache_metadata=(content.metadata,),
        )


# Alternative spelling used by a few integrations.
USCStudyPlanClient = UscStudyPlanClient
UscStudyPlansClient = UscStudyPlanClient
parse_study_plan_fragment_html = parse_study_plan_html
extract_study_plan_html = extract_study_plan_insert


__all__ = [
    "StudyPlanError",
    "StudyPlanParseError",
    "StudyPlanSchemaChangedError",
    "StudyPlanAcademicYearUnavailable",
    "StudyPlanHttpError",
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
