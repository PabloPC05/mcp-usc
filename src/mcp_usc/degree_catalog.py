"""Catálogo público de grados y localización de asignaturas de la USC.

La página oficial de grados publica una lista estática de titulaciones. Este
módulo se limita a seguir esos enlaces y los endpoints ``study-plan-by-course``
que cada titulación anuncia para el curso solicitado. Todas las operaciones
son GET anónimos y cada salto vuelve a validarse antes de acceder a la red.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from urllib.parse import unquote, urljoin, urlparse
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup

from .exam_catalog import normalise_academic_year, normalise_subject_code
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
from .study_plans import (
    StudyPlanAcademicYearUnavailable,
    StudyPlanError,
    StudyPlanHttpError,
    StudyPlanSchemaChangedError,
    StudyPlanSubject,
    UscStudyPlanClient,
)

USC_DEGREE_CATALOG_URL = "https://www.usc.gal/gl/estudos/graos"

_USER_AGENT = "mcp-usc/0.7 (+https://github.com/PabloPC05/mcp-usc)"
_ALLOWED_HOSTS = frozenset({"www.usc.gal", "usc.gal"})
_CATALOG_PATH = re.compile(
    r"^/(?:gl/estudos/graos|es/estudios/grados|en/studies/degrees)/?$"
)
_DEGREE_PATH = re.compile(
    r"^/(?:gl/estudos/graos|es/estudios/grados|en/studies/degrees)/"
    r"(?P<area>[a-z0-9][a-z0-9-]{0,99})/(?P<slug>[a-z0-9][a-z0-9-]{0,199})/?$"
)
_ENCODED_SEPARATOR = re.compile(r"%(?:2f|5c)", re.IGNORECASE)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_MADRID = ZoneInfo("Europe/Madrid")


class DegreeCatalogError(RuntimeError):
    """No se pudo leer de forma segura el catálogo público de grados."""


class DegreeCatalogParseError(DegreeCatalogError):
    """La fuente oficial no conserva el contrato estructural esperado."""


class DegreeCatalogSchemaChangedError(DegreeCatalogParseError):
    """El catálogo cambió de esquema y fue rechazado antes de guardarlo."""


@dataclass(frozen=True, slots=True)
class DegreeCatalogEntry:
    """Una titulación enlazada explícitamente desde el catálogo oficial."""

    name: str
    url: str
    area_slug: str
    campus: str | None

    def public_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "url": self.url,
            "area_slug": self.area_slug,
            "campus": self.campus,
            "content_is_untrusted": True,
        }


@dataclass(frozen=True, slots=True)
class DegreeCatalog:
    """Instantánea validada de la lista oficial de titulaciones."""

    source_url: str
    degrees: tuple[DegreeCatalogEntry, ...]
    cache_metadata: tuple[PublicHttpCacheMetadata, ...] = ()


@dataclass(frozen=True, slots=True)
class SubjectDegreeLocation:
    """Aparición de un código exacto dentro de un plan oficial."""

    degree: DegreeCatalogEntry
    plan_endpoint_url: str
    subject: StudyPlanSubject

    def public_dict(self) -> dict[str, object]:
        return {
            "subject_code": self.subject.code,
            "subject_name": self.subject.name,
            "subject_url": self.subject.sheet_url,
            "subject_urls": list(self.subject.sheet_urls),
            "degree": self.degree.public_dict(),
            "study_plan_endpoint_url": self.plan_endpoint_url,
            "content_is_untrusted": True,
        }


DegreeIssueCategory = Literal[
    "academic_year_unavailable",
    "http_forbidden",
    "http_not_found",
    "http_rate_limited",
    "http_error",
    "schema_changed",
    "source_unavailable",
    "unsafe_source",
]


@dataclass(frozen=True, slots=True)
class DegreeScanIssue:
    """A classified outcome for one degree that could not be scanned."""

    degree: DegreeCatalogEntry
    category: DegreeIssueCategory
    message: str
    affects_completeness: bool

    def public_dict(self) -> dict[str, object]:
        return {
            "degree": self.degree.public_dict(),
            "category": self.category,
            "message": self.message,
            "affects_completeness": self.affects_completeness,
        }


@dataclass(frozen=True, slots=True)
class DegreeSubjectSearch:
    """Resultado completo o parcial de buscar códigos en planes públicos."""

    academic_year: str
    source_url: str
    requested_codes: tuple[str, ...]
    locations: tuple[SubjectDegreeLocation, ...]
    degree_issues: tuple[DegreeScanIssue, ...]
    scanned_degrees: int
    catalog_degrees: int
    total_catalog_degrees: int
    selected_area_slugs: tuple[str, ...]
    selected_degree_urls: tuple[str, ...]
    cache_metadata: tuple[PublicHttpCacheMetadata, ...]
    fetched_at: str

    @property
    def degree_errors(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            (issue.degree.url, issue.message)
            for issue in self.degree_issues
            if issue.affects_completeness
        )

    @property
    def unavailable_degrees(self) -> tuple[DegreeScanIssue, ...]:
        return tuple(
            issue for issue in self.degree_issues if not issue.affects_completeness
        )

    @property
    def complete(self) -> bool:
        covered = self.scanned_degrees + len(self.unavailable_degrees)
        return not self.degree_errors and covered == self.catalog_degrees

    def public_dict(self) -> dict[str, Any]:
        indexed: dict[str, list[dict[str, object]]] = {
            code: [] for code in self.requested_codes
        }
        for location in self.locations:
            indexed[location.subject.code].append(location.public_dict())
        return {
            "academic_year": self.academic_year,
            "requested_codes": list(self.requested_codes),
            "subjects": [
                {
                    "subject_code": code,
                    "status": (
                        "matched"
                        if indexed[code] and self.complete
                        else "matched_partial"
                        if indexed[code]
                        else "not_found"
                        if self.complete
                        else "source_changed_or_unavailable"
                    ),
                    "locations": indexed[code],
                }
                for code in self.requested_codes
            ],
            "catalog_source_url": self.source_url,
            "catalog_degree_count": self.total_catalog_degrees,
            "selected_degree_count": self.catalog_degrees,
            "scanned_degree_count": self.scanned_degrees,
            "academic_year_unavailable_count": len(self.unavailable_degrees),
            "degree_issues": [issue.public_dict() for issue in self.degree_issues],
            "degree_errors": dict(self.degree_errors),
            "filters": {
                "area_slugs": list(self.selected_area_slugs),
                "degree_urls": list(self.selected_degree_urls),
            },
            "cache": public_cache_summary(self.cache_metadata),
            "complete": self.complete,
            "fetched_at": self.fetched_at,
            "content_is_untrusted": True,
        }

def _clean_text(value: str) -> str:
    return " ".join(value.split())


def _tag_text(node: Any) -> str:
    """Read text also when Drupal nests the node inside a Vue ``template``.

    BeautifulSoup intentionally excludes ``TemplateString`` from ``get_text``.
    USC's headings and campus paragraphs are direct text nodes, so ``string`` is
    the narrow fallback and does not accidentally include unrelated markup.
    """

    value = _clean_text(node.get_text(" ", strip=True))
    return value or _clean_text(str(node.string or ""))


def _safe_path(url: str) -> str:
    validate_usc_url(url)
    parsed = urlparse(url)
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
        raise UnsafeUrlError("URL no permitida para el catálogo público USC")
    return decoded_path


def _validate_catalog_url(url: str) -> str:
    if not _CATALOG_PATH.fullmatch(_safe_path(url)):
        raise UnsafeUrlError("La URL no es el catálogo oficial de grados USC")
    return url


def _degree_area(url: str) -> str:
    match = _DEGREE_PATH.fullmatch(_safe_path(url))
    if not match:
        raise UnsafeUrlError("La URL no es una titulación oficial permitida")
    return match.group("area")


def parse_degree_catalog_html(html: str, source_url: str) -> DegreeCatalog:
    """Extrae únicamente las tarjetas de titulación del filtro oficial.

    Las tarjetas ``is-meta`` representan áreas, no titulaciones. Exigimos un
    enlace, un título y una ruta con exactamente dos segmentos bajo el catálogo;
    si Drupal cambia esa estructura se falla de forma cerrada.
    """

    _validate_catalog_url(source_url)
    if len(html.encode("utf-8")) > 8_000_000:
        raise DegreeCatalogError("El HTML del catálogo supera el límite permitido")
    soup = BeautifulSoup(html, "html.parser")
    components = soup.select("usc-content-filter-tier")
    candidates = [
        component
        for component in components
        if component.select_one("article.ml-banner.is-studies") is not None
    ]
    if len(candidates) != 1:
        raise DegreeCatalogParseError(
            "Se esperaba un único componente oficial con titulaciones"
        )
    advertised = candidates[0].select("article.ml-banner.is-studies:not(.is-meta)")
    articles = candidates[0].select(
        "article.is-m.ml-banner.is-studies.is-light:not(.is-meta)"
    )
    if len(articles) != len(advertised):
        raise DegreeCatalogParseError("Una tarjeta no conserva la estructura de titulación")
    if not articles or len(articles) > 250:
        raise DegreeCatalogParseError("Número de titulaciones fuera de los límites")
    degrees: list[DegreeCatalogEntry] = []
    seen_urls: set[str] = set()
    for article in articles:
        links = article.find_all("a", href=True, recursive=False)
        if len(links) != 1 or "banner-link" not in links[0].get("class", []):
            raise DegreeCatalogParseError("Tarjeta de titulación sin un único enlace")
        link = links[0]
        url = urljoin(source_url, str(link["href"]))
        area = _degree_area(url)
        headings = link.find_all("h2", class_="at-title")
        if len(headings) != 1:
            raise DegreeCatalogParseError("Tarjeta de titulación sin un único título")
        name = _tag_text(headings[0])
        if not name or len(name) > 500:
            raise DegreeCatalogParseError("Nombre de titulación ausente o demasiado largo")
        text_nodes = link.select(".at-text p")
        if len(text_nodes) > 1:
            raise DegreeCatalogParseError("Tarjeta con información de campus ambigua")
        campus = _tag_text(text_nodes[0]) if text_nodes else ""
        if len(campus) > 200:
            raise DegreeCatalogParseError("Nombre de campus demasiado largo")
        if url in seen_urls:
            raise DegreeCatalogParseError("El catálogo contiene titulaciones duplicadas")
        seen_urls.add(url)
        degrees.append(
            DegreeCatalogEntry(
                name=name,
                url=url,
                area_slug=area,
                campus=campus or None,
            )
        )
    return DegreeCatalog(source_url=source_url, degrees=tuple(degrees))


class UscDegreeCatalogClient:
    """Cliente anónimo, sin cookies y exclusivamente GET para el catálogo USC."""

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

    async def _get_html(self, url: str) -> PublicHttpResponse:
        _validate_catalog_url(url)
        headers = {"User-Agent": _USER_AGENT, "Accept": "text/html"}

        def validate_response(response: PublicHttpResponse) -> None:
            if response.metadata.media_type != "text/html":
                raise DegreeCatalogSchemaChangedError(
                    "El catálogo USC no devolvió text/html"
                )
            try:
                parse_degree_catalog_html(
                    response.content.decode("utf-8", errors="replace"),
                    response.metadata.final_url,
                )
            except DegreeCatalogParseError as exc:
                raise DegreeCatalogSchemaChangedError(str(exc)) from None

        try:
            return await self._fetcher.get(
                url,
                headers=headers,
                validate_redirect=_validate_catalog_url,
                validate=validate_response,
            )
        except PublicHttpError as exc:
            raise DegreeCatalogError(str(exc)) from None

    async def fetch_catalog(
        self, url: str = USC_DEGREE_CATALOG_URL
    ) -> DegreeCatalog:
        response = await self._get_html(url)
        try:
            parsed = parse_degree_catalog_html(
                response.content.decode("utf-8", errors="replace"),
                response.metadata.final_url,
            )
        except DegreeCatalogParseError as exc:
            raise DegreeCatalogSchemaChangedError(str(exc)) from None
        return DegreeCatalog(
            source_url=parsed.source_url,
            degrees=parsed.degrees,
            cache_metadata=(response.metadata,),
        )


def _validate_codes(subject_codes: Sequence[str]) -> tuple[str, ...]:
    if isinstance(subject_codes, (str, bytes)):
        raise ValueError("subject_codes debe ser una lista de códigos")
    if not subject_codes or len(subject_codes) > 100:
        raise ValueError("subject_codes debe contener entre 1 y 100 códigos")
    codes: list[str] = []
    for value in subject_codes:
        code = normalise_subject_code(value)
        if code not in codes:
            codes.append(code)
    return tuple(codes)


def _scan_issue(
    degree: DegreeCatalogEntry, error: BaseException
) -> DegreeScanIssue:
    message = (
        str(error)
        if isinstance(error, (StudyPlanError, UnsafeUrlError, ValueError))
        else "No se pudo procesar esta titulación de forma segura"
    )
    if isinstance(error, StudyPlanAcademicYearUnavailable):
        return DegreeScanIssue(
            degree, "academic_year_unavailable", message, affects_completeness=False
        )
    if isinstance(error, StudyPlanHttpError):
        category: DegreeIssueCategory = (
            "http_forbidden"
            if error.status_code == 403
            else "http_not_found"
            if error.status_code == 404
            else "http_rate_limited"
            if error.status_code == 429
            else "http_error"
        )
        return DegreeScanIssue(degree, category, message, affects_completeness=True)
    if isinstance(error, StudyPlanSchemaChangedError):
        return DegreeScanIssue(degree, "schema_changed", message, affects_completeness=True)
    if isinstance(error, UnsafeUrlError):
        return DegreeScanIssue(degree, "unsafe_source", message, affects_completeness=True)
    return DegreeScanIssue(degree, "source_unavailable", message, affects_completeness=True)


def _normalise_filters(
    catalog: DegreeCatalog,
    area_slugs: Sequence[str] | None,
    degree_urls: Sequence[str] | None,
) -> tuple[
    tuple[DegreeCatalogEntry, ...],
    tuple[str, ...],
    tuple[str, ...],
]:
    if isinstance(area_slugs, (str, bytes)) or isinstance(degree_urls, (str, bytes)):
        raise ValueError("Los filtros deben ser listas")
    if area_slugs is not None and len(area_slugs) > 20:
        raise ValueError("area_slugs admite como máximo 20 valores")
    if degree_urls is not None and len(degree_urls) > 100:
        raise ValueError("degree_urls admite como máximo 100 valores")
    known_areas = {degree.area_slug for degree in catalog.degrees}
    areas: list[str] = []
    for raw in area_slugs or ():
        if not isinstance(raw, str):
            raise ValueError("Cada área debe ser texto")
        area = raw.strip().casefold()
        if area not in known_areas:
            raise ValueError(f"Área USC desconocida: {raw}")
        if area not in areas:
            areas.append(area)
    known_urls = {degree.url for degree in catalog.degrees}
    urls: list[str] = []
    for raw in degree_urls or ():
        if not isinstance(raw, str):
            raise ValueError("Cada URL de titulación debe ser texto")
        url = raw.strip()
        _degree_area(url)
        if url not in known_urls:
            raise ValueError("La URL no está anunciada por el catálogo USC actual")
        if url not in urls:
            urls.append(url)
    selected = tuple(
        degree
        for degree in catalog.degrees
        if (not areas or degree.area_slug in areas)
        and (not urls or degree.url in urls)
    )
    if not selected:
        raise ValueError("Los filtros no seleccionan ninguna titulación")
    return selected, tuple(areas), tuple(urls)


async def locate_subject_codes(
    subject_codes: Sequence[str],
    academic_year: str,
    *,
    catalog_url: str = USC_DEGREE_CATALOG_URL,
    area_slugs: Sequence[str] | None = None,
    degree_urls: Sequence[str] | None = None,
    concurrency: int = 8,
    timeout: float = 30.0,
    catalog_client: UscDegreeCatalogClient | None = None,
    study_plan_client: UscStudyPlanClient | None = None,
    catalog: DegreeCatalog | None = None,
    cache: PublicHttpCache | None = None,
) -> DegreeSubjectSearch:
    """Busca códigos exactos en todos los grados actuales del catálogo oficial.

    No infiere una titulación por los dígitos del código. Escanea todos los
    planes anunciados para el año, porque una materia puede pertenecer a más de
    un grado. Los fallos parciales quedan visibles y nunca se convierten en un
    falso ``not_found``.
    """

    codes = _validate_codes(subject_codes)
    year = normalise_academic_year(academic_year)
    if not isinstance(concurrency, int) or isinstance(concurrency, bool):
        raise ValueError("concurrency debe ser un entero")
    if concurrency < 1 or concurrency > 16:
        raise ValueError("concurrency debe estar entre 1 y 16")
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
        raise ValueError("timeout debe ser positivo")
    if catalog is None:
        client = catalog_client or UscDegreeCatalogClient(timeout=timeout, cache=cache)
        catalog = await client.fetch_catalog(_validate_catalog_url(catalog_url))
    else:
        _validate_catalog_url(catalog.source_url)
    if not catalog.degrees or len(catalog.degrees) > 250:
        raise DegreeCatalogParseError("Número de titulaciones fuera de los límites")
    seen_urls: set[str] = set()
    for degree in catalog.degrees:
        area = _degree_area(degree.url)
        if (
            degree.area_slug != area
            or degree.url in seen_urls
            or not degree.name
            or degree.name != _clean_text(degree.name)
            or len(degree.name) > 500
            or (degree.campus is not None and degree.campus != _clean_text(degree.campus))
            or (degree.campus is not None and len(degree.campus) > 200)
        ):
            raise DegreeCatalogParseError("Catálogo de titulaciones inconsistente")
        seen_urls.add(degree.url)
    degrees, selected_areas, selected_urls = _normalise_filters(
        catalog, area_slugs, degree_urls
    )

    plan_client = study_plan_client or UscStudyPlanClient(timeout=timeout, cache=cache)
    semaphore = asyncio.Semaphore(concurrency)

    async def fetch(degree: DegreeCatalogEntry) -> object:
        async with semaphore:
            try:
                return await plan_client.fetch_study_plan(
                    degree.url, academic_year=year
                )
            except BaseException as exc:
                if not isinstance(exc, Exception):
                    raise
                return exc

    outcomes = await asyncio.gather(*(fetch(degree) for degree in degrees))
    locations: list[SubjectDegreeLocation] = []
    issues: list[DegreeScanIssue] = []
    cache_metadata = list(catalog.cache_metadata)
    requested = set(codes)
    scanned = 0
    for degree, outcome in zip(degrees, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            issues.append(_scan_issue(degree, outcome))
            continue
        scanned += 1
        cache_metadata.extend(outcome.cache_metadata)
        for subject in outcome.subjects:
            if subject.code in requested:
                locations.append(
                    SubjectDegreeLocation(
                        degree=degree,
                        plan_endpoint_url=outcome.endpoint_url,
                        subject=subject,
                    )
                )
    return DegreeSubjectSearch(
        academic_year=year,
        source_url=catalog.source_url,
        requested_codes=codes,
        locations=tuple(locations),
        degree_issues=tuple(issues),
        scanned_degrees=scanned,
        catalog_degrees=len(degrees),
        total_catalog_degrees=len(catalog.degrees),
        selected_area_slugs=selected_areas,
        selected_degree_urls=selected_urls,
        cache_metadata=tuple(cache_metadata),
        fetched_at=datetime.now(_MADRID).isoformat(),
    )


__all__ = [
    "USC_DEGREE_CATALOG_URL",
    "DegreeCatalogError",
    "DegreeCatalogParseError",
    "DegreeCatalogSchemaChangedError",
    "DegreeCatalogEntry",
    "DegreeCatalog",
    "SubjectDegreeLocation",
    "DegreeIssueCategory",
    "DegreeScanIssue",
    "DegreeSubjectSearch",
    "parse_degree_catalog_html",
    "UscDegreeCatalogClient",
    "locate_subject_codes",
]
