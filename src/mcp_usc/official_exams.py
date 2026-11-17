from __future__ import annotations

import asyncio
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .degree_catalog import (
    USC_DEGREE_CATALOG_URL,
    DegreeCatalog,
    DegreeCatalogEntry,
    DegreeScanIssue,
    DegreeSubjectSearch,
    SubjectDegreeLocation,
    UscDegreeCatalogClient,
    locate_subject_codes,
)
from .disk_cache import DiskJsonCache
from .domain import MADRID
from .exam_calendar import (
    ExamCalendar,
    ExamCalendarError,
    ExamCall,
    SubjectSheet,
    UscExamCalendarClient,
)
from .exam_catalog import (
    DEGREE_EXAM_PROFILES,
    DegreeExamProfile,
    normalise_academic_year,
    normalise_subject_code,
    normalise_subject_title,
)
from .public_http_cache import PublicHttpCache, PublicHttpCacheMetadata, public_cache_summary
from .study_plans import StudyPlan, StudyPlanError, StudyPlanSubject, UscStudyPlanClient


@dataclass(frozen=True, slots=True)
class ResolvedSubjectCandidate:
    degree: DegreeExamProfile
    study_plan_endpoint_url: str
    subject: StudyPlanSubject


def list_official_exam_degrees() -> list[dict[str, object]]:
    return [profile.public_dict() for profile in DEGREE_EXAM_PROFILES.values()]


def _select_degrees(degree_keys: list[str] | None) -> list[DegreeExamProfile]:
    if degree_keys is None:
        return list(DEGREE_EXAM_PROFILES.values())
    if not degree_keys:
        raise ValueError("degree_keys no puede ser una lista vacía")
    if len(degree_keys) > 20:
        raise ValueError("No se pueden seleccionar más de 20 titulaciones")
    selected: list[DegreeExamProfile] = []
    for key in degree_keys:
        if not isinstance(key, str) or key not in DEGREE_EXAM_PROFILES:
            raise ValueError(f"Titulación oficial no admitida: {key!r}")
        profile = DEGREE_EXAM_PROFILES[key]
        if profile not in selected:
            selected.append(profile)
    return selected


def _validated_codes(subject_codes: list[str]) -> list[str]:
    if not subject_codes:
        raise ValueError("subject_codes debe contener al menos un código")
    if len(subject_codes) > 50:
        raise ValueError("No se pueden consultar más de 50 códigos a la vez")
    codes: list[str] = []
    for raw_code in subject_codes:
        code = normalise_subject_code(raw_code)
        if code not in codes:
            codes.append(code)
    return codes


def _public_error(error: BaseException, expected: tuple[type[BaseException], ...]) -> str:
    if isinstance(error, expected):
        return str(error)
    return "Fallo inesperado al consultar la fuente oficial"


async def _load_study_plans(
    degrees: list[DegreeExamProfile],
    academic_year: str,
    client: UscStudyPlanClient,
) -> tuple[dict[str, StudyPlan], dict[str, str]]:
    outcomes = await asyncio.gather(
        *(
            client.fetch_study_plan(degree.study_plan_url, academic_year=academic_year)
            for degree in degrees
        ),
        return_exceptions=True,
    )
    plans: dict[str, StudyPlan] = {}
    errors: dict[str, str] = {}
    for degree, outcome in zip(degrees, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            if not isinstance(outcome, Exception):
                raise outcome
            errors[degree.key] = _public_error(outcome, (StudyPlanError, ValueError))
        else:
            plans[degree.key] = outcome
    return plans, errors


def _candidate_index(
    degrees: list[DegreeExamProfile], plans: dict[str, StudyPlan]
) -> dict[str, list[ResolvedSubjectCandidate]]:
    indexed: dict[str, list[ResolvedSubjectCandidate]] = defaultdict(list)
    for degree in degrees:
        plan = plans.get(degree.key)
        if plan is None:
            continue
        for subject in plan.subjects:
            indexed[subject.code].append(
                ResolvedSubjectCandidate(degree, plan.endpoint_url, subject)
            )
    return indexed


def _public_candidate(candidate: ResolvedSubjectCandidate) -> dict[str, Any]:
    return {
        "degree_key": candidate.degree.key,
        "degree_name": candidate.degree.name,
        "study_plan_url": candidate.degree.study_plan_url,
        "study_plan_endpoint_url": candidate.study_plan_endpoint_url,
        "study_plan_catalog_id": candidate.degree.study_plan_catalog_id,
        "calendar_plan_id": candidate.degree.calendar_plan_id,
        "subject_code": candidate.subject.code,
        "subject_name": candidate.subject.name,
        "subject_url": candidate.subject.sheet_url,
        "crosswalk_method": candidate.degree.crosswalk_method,
        "content_is_untrusted": True,
    }


async def discover_official_exam_subjects(
    academic_year: str,
    degree_keys: list[str] | None = None,
    *,
    timeout: float = 30.0,
    client: UscStudyPlanClient | None = None,
    cache: PublicHttpCache | None = None,
) -> dict[str, Any]:
    year = normalise_academic_year(academic_year)
    degrees = _select_degrees(degree_keys)
    plan_client = client or UscStudyPlanClient(timeout=timeout, cache=cache)
    plans, errors = await _load_study_plans(degrees, year, plan_client)
    indexed = _candidate_index(degrees, plans)
    subjects = [
        {
            "subject_code": code,
            "candidates": [_public_candidate(candidate) for candidate in candidates],
            "ambiguous_between_degrees": len(candidates) > 1,
        }
        for code, candidates in sorted(indexed.items())
    ]
    return {
        "academic_year": year,
        "degrees": [
            {
                **degree.public_dict(),
                "status": "loaded" if degree.key in plans else "source_changed_or_unavailable",
                "error": errors.get(degree.key),
                "subject_count": len(plans[degree.key].subjects) if degree.key in plans else 0,
            }
            for degree in degrees
        ],
        "subjects": subjects,
        "count": len(subjects),
        "complete": not errors,
        "fetched_at": datetime.now(MADRID).isoformat(),
        "cache": public_cache_summary(
            metadata for plan in plans.values() for metadata in plan.cache_metadata
        ),
        "content_is_untrusted": True,
    }


async def _load_calendars(
    degrees: list[DegreeExamProfile],
    academic_year: str,
    client: UscExamCalendarClient,
) -> tuple[dict[str, ExamCalendar], dict[str, str]]:
    source_urls: list[str] = []
    for degree in degrees:
        for source_url in degree.calendar_urls:
            if source_url not in source_urls:
                source_urls.append(source_url)
    outcomes = await asyncio.gather(
        *(
            client.fetch_calendar(source_url, academic_year=academic_year)
            for source_url in source_urls
        ),
        return_exceptions=True,
    )
    calendars: dict[str, ExamCalendar] = {}
    errors: dict[str, str] = {}
    for source_url, outcome in zip(source_urls, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            if not isinstance(outcome, Exception):
                raise outcome
            errors[source_url] = _public_error(outcome, (ExamCalendarError, ValueError))
        else:
            calendars[source_url] = outcome
    return calendars, errors


def _serialise_calls(calls: tuple[ExamCall, ...], academic_year: str) -> list[dict[str, Any]]:
    start_year, end_year = (int(part) for part in academic_year.split("/"))
    lower_bound = datetime(start_year, 9, 1, tzinfo=MADRID)
    upper_bound = datetime(end_year, 8, 31, 23, 59, 59, tzinfo=MADRID)
    serialised: list[dict[str, Any]] = []
    for call in calls:
        try:
            starts_at = datetime.strptime(call.date_time, "%d.%m.%Y - %H:%M").replace(tzinfo=MADRID)
        except ValueError as exc:
            raise ExamCalendarError(
                "La fuente oficial devolvió una fecha con formato inesperado"
            ) from exc
        if not lower_bound <= starts_at <= upper_bound:
            raise ExamCalendarError(
                "La fuente oficial devolvió una fecha fuera del curso solicitado"
            )
        serialised.append(
            {
                "semester": call.semester,
                "opportunity": call.opportunity,
                "date_time": call.date_time,
                "starts_at": starts_at.isoformat(),
                "rooms": list(call.rooms),
                "groups": list(call.groups),
                "timezone": "Europe/Madrid",
            }
        )
    return serialised


_SHEET_SLOT = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})\s+(\d{2}):(\d{2})(?:\s*-\s*(\d{2}):(\d{2}))?")


def _sheet_calls(sheet: SubjectSheet, academic_year: str) -> list[dict[str, Any]]:
    """Exam slots printed on a subject sheet, in the same shape as calendar calls."""

    start_year, end_year = (int(part) for part in academic_year.split("/"))
    lower_bound = datetime(start_year, 9, 1, tzinfo=MADRID)
    upper_bound = datetime(end_year, 8, 31, 23, 59, 59, tzinfo=MADRID)
    calls: list[dict[str, Any]] = []
    for slot in sheet.exam_slots:
        match = _SHEET_SLOT.fullmatch(slot.date_time.strip())
        if match is None:
            raise ExamCalendarError("La ficha de la materia devolvió una fecha inesperada")
        day, month, year, hour, minute, end_hour, end_minute = match.groups()
        starts_at = datetime(int(year), int(month), int(day), int(hour), int(minute), tzinfo=MADRID)
        if not lower_bound <= starts_at <= upper_bound:
            raise ExamCalendarError("La ficha de la materia tiene una fecha fuera del curso")
        call: dict[str, Any] = {
            "semester": sheet.semester,
            # Sheets list the slots without naming the opportunity; never guess it.
            "opportunity": None,
            "date_time": f"{day}.{month}.{year} - {hour}:{minute}",
            "starts_at": starts_at.isoformat(),
            "rooms": list(slot.rooms),
            "groups": list(slot.groups),
            "timezone": "Europe/Madrid",
        }
        if end_hour is not None:
            call["ends_at"] = starts_at.replace(
                hour=int(end_hour), minute=int(end_minute)
            ).isoformat()
        calls.append(call)
    return calls


async def _fill_from_subject_sheets(
    resolved: dict[str, list[dict[str, Any]]],
    academic_year: str,
    client: UscExamCalendarClient,
) -> None:
    """Use each subject's official sheet when the centre calendar does not list it.

    usc.gal prints the exam table on every subject sheet, so this also covers subjects
    whose centre calendar is not published yet or does not include their plan.
    """

    pending = [
        item
        for items in resolved.values()
        for item in items
        if item["status"] == "not_published_or_not_found" and item.get("subject_url")
    ]
    urls = list(dict.fromkeys(str(item["subject_url"]) for item in pending))
    if not urls:
        return
    sheets = await asyncio.gather(
        *(client.fetch_subject_sheet(url) for url in urls), return_exceptions=True
    )
    by_url = dict(zip(urls, sheets, strict=True))
    for item in pending:
        url = str(item["subject_url"])
        sheet = by_url[url]
        if isinstance(sheet, BaseException):
            if not isinstance(sheet, Exception):
                raise sheet
            item["source_errors"] = {
                **item.get("source_errors", {}),
                url: "No se pudo leer la ficha oficial de la materia",
            }
            continue
        if sheet.academic_year not in {None, academic_year} or not sheet.exam_slots:
            continue
        try:
            calls = _sheet_calls(sheet, academic_year)
        except ExamCalendarError as exc:
            item["source_errors"] = {**item.get("source_errors", {}), url: str(exc)}
            continue
        item.update(
            status="matched",
            calls=calls,
            evidence=[{"source": "subject_sheet", "source_url": url, "calls": calls}],
        )


def _call_signature(calls: list[dict[str, Any]]) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        sorted((call["semester"], call["opportunity"], call["date_time"]) for call in calls)
    )


def _merge_calls(call_sets: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str, str], dict[str, Any]] = {}
    for calls in call_sets:
        for call in calls:
            key = (call["semester"], call["opportunity"], call["date_time"])
            target = merged.setdefault(
                key,
                {
                    **call,
                    "rooms": [],
                    "groups": [],
                },
            )
            for field in ("rooms", "groups"):
                for value in call[field]:
                    if value not in target[field]:
                        target[field].append(value)
    return list(merged.values())


def _resolve_candidate(
    candidate: ResolvedSubjectCandidate,
    calendars: dict[str, ExamCalendar],
    calendar_errors: dict[str, str],
    academic_year: str,
) -> dict[str, Any]:
    evidence: list[dict[str, Any]] = []
    relevant_errors = {
        source_url: calendar_errors[source_url]
        for source_url in candidate.degree.calendar_urls
        if source_url in calendar_errors
    }
    for source_url in candidate.degree.calendar_urls:
        calendar = calendars.get(source_url)
        if calendar is None:
            continue
        matches = [
            subject
            for subject in calendar.subjects
            if subject.plan_id == candidate.degree.calendar_plan_id
            and normalise_subject_title(subject.name)
            == normalise_subject_title(candidate.subject.name)
        ]
        if len(matches) > 1:
            return {
                **_public_candidate(candidate),
                "status": "ambiguous",
                "error": "El calendario contiene varias filas para el mismo plan y título",
                "calls": [],
                "evidence": [],
                "source_errors": relevant_errors,
            }
        if not matches:
            continue
        try:
            calls = _serialise_calls(matches[0].calls, academic_year)
        except ExamCalendarError as exc:
            return {
                **_public_candidate(candidate),
                "status": "source_changed_or_unavailable",
                "error": str(exc),
                "calls": [],
                "evidence": [],
                "source_errors": relevant_errors,
            }
        evidence.append(
            {
                "source_url": source_url,
                "calendar_endpoint_url": calendar.endpoint_url,
                "calls": calls,
            }
        )
    if not evidence:
        return {
            **_public_candidate(candidate),
            "status": (
                "source_changed_or_unavailable" if relevant_errors else "not_published_or_not_found"
            ),
            "calls": [],
            "evidence": [],
            "source_errors": relevant_errors,
        }
    signatures = {_call_signature(item["calls"]) for item in evidence}
    if len(signatures) != 1:
        return {
            **_public_candidate(candidate),
            "status": "conflict_between_official_sources",
            "calls": [],
            "evidence": evidence,
            "source_errors": relevant_errors,
        }
    return {
        **_public_candidate(candidate),
        "status": "matched_partial" if relevant_errors else "matched",
        "calls": _merge_calls([item["calls"] for item in evidence]),
        "evidence": evidence,
        "source_errors": relevant_errors,
    }


def _without_duplicate_calls(candidate: dict[str, Any]) -> dict[str, Any]:
    """Drop call lists that repeat the subject's top-level calls, keeping provenance.

    Evidence whose calls differ (a conflict between sources) keeps them in full.
    """

    compact = {key: value for key, value in candidate.items() if key != "calls"}
    signature = _call_signature(candidate["calls"]) if candidate.get("calls") else None
    compact["evidence"] = [
        {key: value for key, value in item.items() if key != "calls"}
        if signature is not None and _call_signature(item.get("calls", [])) == signature
        else item
        for item in candidate.get("evidence", [])
    ]
    return compact


def _combine_candidate_results(
    code: str,
    candidate_results: list[dict[str, Any]],
    *,
    incomplete_sources: bool,
) -> dict[str, Any]:
    if not candidate_results:
        return {
            "subject_code": code,
            "status": (
                "source_changed_or_unavailable"
                if incomplete_sources
                else "not_found_in_study_plans"
            ),
            "calls": [],
            "candidates": [],
        }
    matched = [item for item in candidate_results if item["status"].startswith("matched")]
    if len(candidate_results) == 1:
        status = candidate_results[0]["status"]
        if incomplete_sources:
            status = (
                "matched_partial_discovery"
                if status.startswith("matched")
                else "source_changed_or_unavailable"
            )
        return {
            "subject_code": code,
            "status": status,
            "calls": candidate_results[0]["calls"],
            "candidates": [_without_duplicate_calls(candidate_results[0])],
            "content_is_untrusted": True,
        }
    if len(matched) == len(candidate_results):
        signatures = {_call_signature(item["calls"]) for item in matched}
        if len(signatures) == 1:
            return {
                "subject_code": code,
                "status": (
                    "matched_equivalent_plans_partial"
                    if any(item["status"] == "matched_partial" for item in matched)
                    else "matched_equivalent_plans"
                ),
                "calls": _merge_calls([item["calls"] for item in matched]),
                "candidates": [_without_duplicate_calls(item) for item in candidate_results],
                "content_is_untrusted": True,
            }
    return {
        "subject_code": code,
        "status": "ambiguous",
        "calls": [],
        "candidates": candidate_results,
        "content_is_untrusted": True,
    }


async def fetch_official_exam_dates(
    subject_codes: list[str],
    academic_year: str,
    degree_keys: list[str] | None = None,
    *,
    timeout: float = 30.0,
    calendar_client: UscExamCalendarClient | None = None,
    study_plan_client: UscStudyPlanClient | None = None,
    cache: PublicHttpCache | None = None,
) -> dict[str, Any]:
    """Resolve exact codes dynamically through official study plans and calendars."""

    year = normalise_academic_year(academic_year)
    codes = _validated_codes(subject_codes)
    degrees = _select_degrees(degree_keys)
    plan_client = study_plan_client or UscStudyPlanClient(timeout=timeout, cache=cache)
    exams_client = calendar_client or UscExamCalendarClient(timeout=timeout, cache=cache)
    (plans, plan_errors), (calendars, calendar_errors) = await asyncio.gather(
        _load_study_plans(degrees, year, plan_client),
        _load_calendars(degrees, year, exams_client),
    )
    candidates = _candidate_index(degrees, plans)
    incomplete_plans = bool(plan_errors)
    incomplete_sources = bool(plan_errors or calendar_errors)
    resolved = {
        code: [
            _resolve_candidate(candidate, calendars, calendar_errors, year)
            for candidate in candidates.get(code, [])
        ]
        for code in codes
    }
    await _fill_from_subject_sheets(resolved, year, exams_client)
    results = [
        _combine_candidate_results(code, resolved[code], incomplete_sources=incomplete_plans)
        for code in codes
    ]
    return {
        "academic_year": year,
        "subjects": results,
        "degree_keys": [degree.key for degree in degrees],
        "study_plan_errors": plan_errors,
        "calendar_errors": calendar_errors,
        "sources_checked": [
            *(degree.study_plan_url for degree in degrees),
            *dict.fromkeys(source_url for degree in degrees for source_url in degree.calendar_urls),
        ],
        "complete": not incomplete_sources,
        "fetched_at": datetime.now(MADRID).isoformat(),
        "cache": public_cache_summary(
            metadata
            for resource in (*plans.values(), *calendars.values())
            for metadata in resource.cache_metadata
        ),
        "note": (
            "Las asignaturas se resuelven por código exacto desde los planes públicos y después "
            "por título normalizado y plan institucional en los calendarios; si el calendario "
            "del centro no las incluye, se usa la tabla de exámenes de su ficha oficial "
            "(evidence.source=subject_sheet, sin oportunidad indicada). Convocatoria "
            "publicada no implica asistencia, calificación ni que el examen siga pendiente."
        ),
    }


# The catalogue only announces each degree's current plan, but students in upper courses
# still take subjects of the previous plan while it is phased out (e.g. "grao-medicina"
# next to "grao-medicina-2a-edicion", "grao-matematicas" next to "grao-matematicas-0").
# Sibling pages are only used when they exist and publish a plan for the academic year.
_EDITION_SUFFIX = re.compile(r"-(?:\d+|\d+a-edicion)$")
_EDITION_NAME = re.compile(r"\s*\(\s*\d+\s*[ªa]\.?\s*edici[oó]n\s*\)\s*$", re.IGNORECASE)
_MAX_PREVIOUS_PLAN_CANDIDATES = 240


def _previous_plan_candidates(
    bases: tuple[DegreeCatalogEntry, ...], known_urls: set[str]
) -> tuple[DegreeCatalogEntry, ...]:
    candidates: dict[str, DegreeCatalogEntry] = {}
    for entry in bases:
        prefix, slug = entry.url.rsplit("/", 1)
        base = _EDITION_SUFFIX.sub("", slug)
        for variant in (base, f"{base}-0", f"{base}-2a-edicion"):
            url = f"{prefix}/{variant}"
            if url in known_urls or url in candidates:
                continue
            candidates[url] = DegreeCatalogEntry(
                name=f"{_EDITION_NAME.sub('', entry.name)} (plan anterior)",
                url=url,
                area_slug=entry.area_slug,
                campus=entry.campus,
            )
    return tuple(candidates.values())[:_MAX_PREVIOUS_PLAN_CANDIDATES]


@dataclass(frozen=True, slots=True)
class StudentSubjectSearch:
    """Where a student's subject codes appear, searching their own degrees first."""

    locations: tuple[SubjectDegreeLocation, ...]
    searches: tuple[DegreeSubjectSearch, ...]
    scope: tuple[str, ...]
    ignored_scope: tuple[str, ...]
    widened: bool
    # Phased-out plans found next to catalogue degrees; their probes do not count
    # towards completeness because most sibling URLs simply do not exist.
    previous_plan_searches: tuple[DegreeSubjectSearch, ...] = ()

    @property
    def complete(self) -> bool:
        """Every requested code was located, or every search finished without errors.

        Once all codes are found, errors from unrelated degrees cannot change the answer.
        """

        requested = set(self.searches[0].requested_codes) if self.searches else set()
        found = {location.subject.code for location in self.locations}
        if requested and requested <= found:
            return True
        return all(search.complete for search in self.searches)

    def for_code(self, code: str) -> list[SubjectDegreeLocation]:
        """Locations of one code: the student's own degrees first, then the degree that
        holds most of the student's subjects (a typical student has nearly all of them in
        one degree), then catalogue order."""

        per_degree: dict[str, int] = {}
        for location in self.locations:
            per_degree[location.degree.url] = per_degree.get(location.degree.url, 0) + 1
        found = [location for location in self.locations if location.subject.code == code]
        return sorted(
            found,
            key=lambda item: (item.degree.url not in self.scope, -per_degree[item.degree.url]),
        )


_LOCATION_TTL_SECONDS = 6 * 3_600
_LOCATION_CACHE: dict[tuple[object, ...], tuple[float, StudentSubjectSearch]] = {}
_LOCATION_CACHE_MAX = 64

# Across processes (every CLI run is a fresh one) the same answer is kept on disk. Where a
# code lives changes at most once per academic year, so a week is conservative. Disable
# with USC_LOCATION_CACHE=0; relocate with USC_CACHE_DIR.
_LOCATION_DISK_TTL_SECONDS = 7 * 86_400
_LOCATION_DISK_CACHE = DiskJsonCache(
    "locations", ttl_seconds=_LOCATION_DISK_TTL_SECONDS, env_flag="USC_LOCATION_CACHE"
)


def clear_location_cache() -> None:
    _LOCATION_CACHE.clear()


def _entry_to_dict(entry: DegreeCatalogEntry) -> dict[str, Any]:
    return {
        "name": entry.name,
        "url": entry.url,
        "area_slug": entry.area_slug,
        "campus": entry.campus,
    }


def _entry_from_dict(data: dict[str, Any]) -> DegreeCatalogEntry:
    return DegreeCatalogEntry(
        name=str(data["name"]),
        url=str(data["url"]),
        area_slug=str(data["area_slug"]),
        campus=None if data["campus"] is None else str(data["campus"]),
    )


def _location_to_dict(location: SubjectDegreeLocation) -> dict[str, Any]:
    subject = location.subject
    return {
        "degree": _entry_to_dict(location.degree),
        "plan_endpoint_url": location.plan_endpoint_url,
        "subject": {
            "code": subject.code,
            "name": subject.name,
            "sheet_url": subject.sheet_url,
            "alternate_sheet_urls": list(subject.alternate_sheet_urls),
            "course_number": subject.course_number,
            "semester": subject.semester,
        },
    }


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("entero esperado")
    return value


def _location_from_dict(data: dict[str, Any]) -> SubjectDegreeLocation:
    subject = data["subject"]
    return SubjectDegreeLocation(
        degree=_entry_from_dict(data["degree"]),
        plan_endpoint_url=str(data["plan_endpoint_url"]),
        subject=StudyPlanSubject(
            code=str(subject["code"]),
            name=str(subject["name"]),
            sheet_url=str(subject["sheet_url"]),
            alternate_sheet_urls=tuple(str(url) for url in subject["alternate_sheet_urls"]),
            course_number=_optional_int(subject["course_number"]),
            semester=_optional_int(subject["semester"]),
        ),
    )


def _metadata_to_dict(item: PublicHttpCacheMetadata) -> dict[str, Any]:
    return {
        "requested_url": item.requested_url,
        "final_url": item.final_url,
        "fetched_at": item.fetched_at,
        "validated_at": item.validated_at,
        "ttl_seconds": item.ttl_seconds,
        "media_type": item.media_type,
        "etag": item.etag,
        "last_modified": item.last_modified,
    }


def _metadata_from_dict(data: dict[str, Any]) -> PublicHttpCacheMetadata:
    """Evidence of the original download, now served from the local disk cache."""

    validated_at = str(data["validated_at"])
    age = max(0.0, (datetime.now(UTC) - datetime.fromisoformat(validated_at)).total_seconds())
    return PublicHttpCacheMetadata(
        status="fresh",
        requested_url=str(data["requested_url"]),
        final_url=str(data["final_url"]),
        cache_hit=True,
        fetched_at=str(data["fetched_at"]),
        validated_at=validated_at,
        age_seconds=age,
        ttl_seconds=float(data["ttl_seconds"]),
        media_type=str(data["media_type"]),
        etag=None if data["etag"] is None else str(data["etag"]),
        last_modified=None if data["last_modified"] is None else str(data["last_modified"]),
    )


def _issue_to_dict(issue: DegreeScanIssue) -> dict[str, Any]:
    return {
        "degree": _entry_to_dict(issue.degree),
        "category": issue.category,
        "message": issue.message,
        "affects_completeness": issue.affects_completeness,
    }


def _issue_from_dict(data: dict[str, Any]) -> DegreeScanIssue:
    return DegreeScanIssue(
        degree=_entry_from_dict(data["degree"]),
        category=data["category"],
        message=str(data["message"]),
        affects_completeness=bool(data["affects_completeness"]),
    )


def _degree_search_to_dict(search: DegreeSubjectSearch) -> dict[str, Any]:
    return {
        "academic_year": search.academic_year,
        "source_url": search.source_url,
        "requested_codes": list(search.requested_codes),
        "locations": [_location_to_dict(item) for item in search.locations],
        "degree_issues": [_issue_to_dict(item) for item in search.degree_issues],
        "scanned_degrees": search.scanned_degrees,
        "catalog_degrees": search.catalog_degrees,
        "total_catalog_degrees": search.total_catalog_degrees,
        "selected_area_slugs": list(search.selected_area_slugs),
        "selected_degree_urls": list(search.selected_degree_urls),
        "cache_metadata": [_metadata_to_dict(item) for item in search.cache_metadata],
        "fetched_at": search.fetched_at,
    }


def _degree_search_from_dict(data: dict[str, Any]) -> DegreeSubjectSearch:
    return DegreeSubjectSearch(
        academic_year=str(data["academic_year"]),
        source_url=str(data["source_url"]),
        requested_codes=tuple(str(code) for code in data["requested_codes"]),
        locations=tuple(_location_from_dict(item) for item in data["locations"]),
        degree_issues=tuple(_issue_from_dict(item) for item in data["degree_issues"]),
        scanned_degrees=int(data["scanned_degrees"]),
        catalog_degrees=int(data["catalog_degrees"]),
        total_catalog_degrees=int(data["total_catalog_degrees"]),
        selected_area_slugs=tuple(str(slug) for slug in data["selected_area_slugs"]),
        selected_degree_urls=tuple(str(url) for url in data["selected_degree_urls"]),
        cache_metadata=tuple(_metadata_from_dict(item) for item in data["cache_metadata"]),
        fetched_at=str(data["fetched_at"]),
    )


def _student_search_to_dict(result: StudentSubjectSearch) -> dict[str, Any]:
    return {
        "locations": [_location_to_dict(item) for item in result.locations],
        "searches": [_degree_search_to_dict(item) for item in result.searches],
        "scope": list(result.scope),
        "ignored_scope": list(result.ignored_scope),
        "widened": result.widened,
        "previous_plan_searches": [
            _degree_search_to_dict(item) for item in result.previous_plan_searches
        ],
    }


def _student_search_from_dict(data: dict[str, Any]) -> StudentSubjectSearch:
    if not isinstance(data["widened"], bool):
        raise TypeError("booleano esperado")
    return StudentSubjectSearch(
        locations=tuple(_location_from_dict(item) for item in data["locations"]),
        searches=tuple(_degree_search_from_dict(item) for item in data["searches"]),
        scope=tuple(str(url) for url in data["scope"]),
        ignored_scope=tuple(str(url) for url in data["ignored_scope"]),
        widened=data["widened"],
        previous_plan_searches=tuple(
            _degree_search_from_dict(item) for item in data["previous_plan_searches"]
        ),
    )


def _load_persisted_search(key: list[object]) -> StudentSubjectSearch | None:
    payload = _LOCATION_DISK_CACHE.load(key)
    if payload is None:
        return None
    try:
        return _student_search_from_dict(payload)
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def _persist_search(key: list[object], result: StudentSubjectSearch) -> None:
    """Keep only clean results: every search finished without errors and nothing stale."""

    every_search = (*result.searches, *result.previous_plan_searches)
    if not all(search.complete for search in result.searches) or any(
        item.status == "degraded" for search in every_search for item in search.cache_metadata
    ):
        return
    _LOCATION_DISK_CACHE.store(key, _student_search_to_dict(result))


async def locate_student_subjects(
    codes: list[str],
    academic_year: str,
    *,
    degree_urls: list[str] | None = None,
    widen_to_catalog: bool = True,
    timeout: float = 30.0,
    concurrency: int = 8,
    cache: PublicHttpCache | None = None,
    catalog_client: UscDegreeCatalogClient | None = None,
    study_plan_client: UscStudyPlanClient | None = None,
) -> StudentSubjectSearch:
    """Find subject codes in ``degree_urls`` and, if allowed, the rest of the catalogue.

    Works for any degree: nothing is assumed from the code's digits. Degree URLs that
    the current catalogue does not announce are reported, not trusted.
    """

    # Where a code lives changes at most once per academic year; probing phased-out plan
    # pages (mostly 404) on every call cost seconds. Injected clients (tests) bypass this.
    memo_key: tuple[object, ...] | None = None
    disk_key: list[object] = []
    if catalog_client is None and study_plan_client is None:
        memo_key = (academic_year, tuple(codes), tuple(degree_urls or ()), widen_to_catalog)
        memo = _LOCATION_CACHE.get(memo_key)
        if memo is not None and memo[0] > time.monotonic():
            return memo[1]
        disk_key = [
            USC_DEGREE_CATALOG_URL,
            academic_year,
            list(codes),
            list(degree_urls or ()),
            widen_to_catalog,
        ]
        persisted = _load_persisted_search(disk_key)
        if persisted is not None:
            _remember_location(memo_key, persisted)
            return persisted
    catalog_client = catalog_client or UscDegreeCatalogClient(timeout=timeout, cache=cache)
    plan_client = study_plan_client or UscStudyPlanClient(timeout=timeout, cache=cache)
    catalog = await catalog_client.fetch_catalog(USC_DEGREE_CATALOG_URL)
    known_urls = {degree.url for degree in catalog.degrees}
    requested = list(dict.fromkeys(degree_urls or []))
    scope = tuple(url for url in requested if url in known_urls)
    ignored = tuple(url for url in requested if url not in known_urls)

    async def locate(codes_to_find: list[str], urls: tuple[str, ...]) -> DegreeSubjectSearch:
        return await locate_subject_codes(
            codes_to_find,
            academic_year,
            degree_urls=list(urls) or None,
            concurrency=concurrency,
            timeout=timeout,
            catalog=catalog,
            study_plan_client=plan_client,
        )

    searches = [await locate(codes, scope)]
    found = {location.subject.code for location in searches[0].locations}
    missing = [code for code in codes if code not in found]
    widened = bool(scope and widen_to_catalog and missing)
    if widened:
        searches.append(await locate(missing, ()))
        found |= {location.subject.code for location in searches[-1].locations}
        missing = [code for code in codes if code not in found]

    previous: list[DegreeSubjectSearch] = []
    tiers = [tuple(degree for degree in catalog.degrees if degree.url in scope)]
    if widen_to_catalog:
        tiers.append(catalog.degrees)
    for bases in tiers:
        candidates = _previous_plan_candidates(bases, known_urls)
        if not missing or not candidates:
            continue
        search = await locate_subject_codes(
            missing,
            academic_year,
            concurrency=16,
            timeout=timeout,
            catalog=DegreeCatalog(source_url=catalog.source_url, degrees=candidates),
            study_plan_client=plan_client,
        )
        previous.append(search)
        found |= {location.subject.code for location in search.locations}
        missing = [code for code in codes if code not in found]

    unique: dict[tuple[str, str], SubjectDegreeLocation] = {}
    for search in (*searches, *previous):
        for location in search.locations:
            unique.setdefault((location.subject.code, location.degree.url), location)
    result = StudentSubjectSearch(
        locations=tuple(unique.values()),
        searches=tuple(searches),
        scope=scope,
        ignored_scope=ignored,
        widened=widened,
        previous_plan_searches=tuple(previous),
    )
    if memo_key is not None and result.complete:
        _remember_location(memo_key, result)
        _persist_search(disk_key, result)
    return result


def _remember_location(memo_key: tuple[object, ...], result: StudentSubjectSearch) -> None:
    while len(_LOCATION_CACHE) >= _LOCATION_CACHE_MAX:
        _LOCATION_CACHE.pop(next(iter(_LOCATION_CACHE)))
    _LOCATION_CACHE[memo_key] = (time.monotonic() + _LOCATION_TTL_SECONDS, result)


def _location_candidate(location: SubjectDegreeLocation) -> dict[str, Any]:
    return {
        "degree_name": location.degree.name,
        "degree_url": location.degree.url,
        "campus": location.degree.campus,
        "subject_code": location.subject.code,
        "subject_name": location.subject.name,
        "subject_url": location.subject.sheet_url,
        "course_number": location.subject.course_number,
        "semester": location.subject.semester,
        "study_plan_endpoint_url": location.plan_endpoint_url,
        "content_is_untrusted": True,
    }


def _sheet_candidate(
    location: SubjectDegreeLocation, sheet: SubjectSheet | Exception, academic_year: str
) -> dict[str, Any]:
    candidate = _location_candidate(location)
    url = location.subject.sheet_url
    if isinstance(sheet, Exception):
        return {
            **candidate,
            "status": "source_changed_or_unavailable",
            "calls": [],
            "evidence": [],
            "source_errors": {url: "No se pudo leer la ficha oficial de la materia"},
        }
    if sheet.academic_year not in {None, academic_year} or not sheet.exam_slots:
        return {**candidate, "status": "not_published_or_not_found", "calls": [], "evidence": []}
    try:
        calls = _sheet_calls(sheet, academic_year)
    except ExamCalendarError as exc:
        return {
            **candidate,
            "status": "source_changed_or_unavailable",
            "calls": [],
            "evidence": [],
            "source_errors": {url: str(exc)},
        }
    return {
        **candidate,
        "status": "matched",
        "calls": calls,
        "evidence": [{"source": "subject_sheet", "source_url": url, "calls": calls}],
    }


def _combine_sheet_candidates(
    code: str,
    candidates: list[dict[str, Any]],
    preferred_degree_urls: set[str],
    *,
    search_complete: bool,
) -> dict[str, Any]:
    """Decide one subject from every plan where its code appears.

    Plans without published dates do not contradict the ones that have them. When the
    student's own degrees are known, their dates win over other plans that share the code.
    """

    if not candidates:
        return {
            "subject_code": code,
            "status": (
                "not_found_in_study_plans" if search_complete else "source_changed_or_unavailable"
            ),
            "calls": [],
            "candidates": [],
        }
    matched = [item for item in candidates if item["status"] == "matched"]
    preferred = [item for item in matched if item["degree_url"] in preferred_degree_urls]
    deciding = preferred or matched
    if not deciding:
        statuses = {item["status"] for item in candidates}
        status = (
            "source_changed_or_unavailable"
            if "source_changed_or_unavailable" in statuses
            else "not_published_or_not_found"
        )
        return {
            "subject_code": code,
            "status": status,
            "calls": [],
            "candidates": candidates,
            "content_is_untrusted": True,
        }
    signatures = {_call_signature(item["calls"]) for item in deciding}
    if len(signatures) != 1:
        return {
            "subject_code": code,
            "status": "ambiguous",
            "calls": [],
            "candidates": candidates,
            "content_is_untrusted": True,
        }
    calls = _merge_calls([item["calls"] for item in deciding])
    return {
        "subject_code": code,
        "status": "matched" if len(deciding) == 1 else "matched_equivalent_plans",
        "calls": calls,
        "candidates": [
            _without_duplicate_calls(item) if item in deciding else item for item in candidates
        ],
        "content_is_untrusted": True,
    }


async def fetch_subject_exam_dates(
    subject_codes: list[str],
    academic_year: str,
    *,
    degree_urls: list[str] | None = None,
    widen_to_catalog: bool = True,
    timeout: float = 30.0,
    concurrency: int = 8,
    cache: PublicHttpCache | None = None,
    catalog_client: UscDegreeCatalogClient | None = None,
    study_plan_client: UscStudyPlanClient | None = None,
    calendar_client: UscExamCalendarClient | None = None,
) -> dict[str, Any]:
    """Exam dates for subjects of any USC degree, read from each subject's official sheet.

    Codes are looked up first in ``degree_urls`` (the student's degrees) and, when
    ``widen_to_catalog`` allows it, missing codes in every degree of the catalogue.
    Nothing here is specific to a degree or centre.
    """

    year = normalise_academic_year(academic_year)
    codes = _validated_codes(subject_codes)
    exams_client = calendar_client or UscExamCalendarClient(timeout=timeout, cache=cache)
    located = await locate_student_subjects(
        codes,
        year,
        degree_urls=degree_urls,
        widen_to_catalog=widen_to_catalog,
        timeout=timeout,
        concurrency=concurrency,
        cache=cache,
        catalog_client=catalog_client,
        study_plan_client=study_plan_client,
    )
    searches = located.searches
    scope = located.scope
    ignored_scope = located.ignored_scope
    widened = located.widened
    locations = {
        (location.subject.code, location.degree.url): location for location in located.locations
    }

    semaphore = asyncio.Semaphore(concurrency)

    async def read_sheet(url: str) -> SubjectSheet | Exception:
        async with semaphore:
            try:
                return await exams_client.fetch_subject_sheet(url)
            except Exception as exc:
                return exc

    sheet_urls = list(dict.fromkeys(item.subject.sheet_url for item in locations.values()))
    sheets = dict(
        zip(sheet_urls, await asyncio.gather(*(read_sheet(url) for url in sheet_urls)), strict=True)
    )
    preferred = set(scope)
    search_complete = all(search.complete for search in searches)
    results = []
    for code in codes:
        candidates = [
            _sheet_candidate(location, sheets[location.subject.sheet_url], year)
            for (location_code, _), location in locations.items()
            if location_code == code
        ]
        results.append(
            _combine_sheet_candidates(code, candidates, preferred, search_complete=search_complete)
        )
    sheet_errors = any(isinstance(sheet, Exception) for sheet in sheets.values())
    metadata = [
        *(item for search in searches for item in search.cache_metadata),
        *(
            item
            for sheet in sheets.values()
            if not isinstance(sheet, Exception)
            for item in sheet.cache_metadata
        ),
    ]
    issues = [issue for search in searches for issue in search.degree_issues]
    return {
        "academic_year": year,
        "subjects": results,
        "resolution": "study_plans_and_subject_sheets",
        "degree_scope": {
            "degree_urls": list(scope),
            "ignored_degree_urls": list(ignored_scope),
            "widened_to_catalog": widened,
            "scanned_degrees": sum(search.scanned_degrees for search in searches),
        },
        "degree_issues": [issue.public_dict() for issue in issues[:20]],
        "complete": search_complete and not sheet_errors,
        "fetched_at": datetime.now(MADRID).isoformat(),
        "cache": public_cache_summary(metadata),
        "note": (
            "Fechas leídas de la tabla de exámenes de la ficha oficial de cada materia "
            "(source_url) para el curso indicado, válidas para cualquier grado. La ficha no "
            "nombra la oportunidad: el orden de fechas no la garantiza. Si una materia está "
            "en varios planes, se priorizan los grados del estudiante."
        ),
    }
