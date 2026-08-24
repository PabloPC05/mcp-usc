from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .domain import MADRID
from .exam_calendar import ExamCalendar, ExamCalendarError, ExamCall, UscExamCalendarClient
from .exam_catalog import (
    DEGREE_EXAM_PROFILES,
    DegreeExamProfile,
    normalise_academic_year,
    normalise_subject_code,
    normalise_subject_title,
)
from .public_http_cache import PublicHttpCache, public_cache_summary
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
            starts_at = datetime.strptime(call.date_time, "%d.%m.%Y - %H:%M").replace(
                tzinfo=MADRID
            )
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


def _call_signature(calls: list[dict[str, Any]]) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        sorted(
            (call["semester"], call["opportunity"], call["date_time"])
            for call in calls
        )
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
                "source_changed_or_unavailable"
                if relevant_errors
                else "not_published_or_not_found"
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
            "candidates": candidate_results,
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
                "candidates": candidate_results,
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
    results = [
        _combine_candidate_results(
            code,
            [
                _resolve_candidate(candidate, calendars, calendar_errors, year)
                for candidate in candidates.get(code, [])
            ],
            incomplete_sources=incomplete_plans,
        )
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
            *dict.fromkeys(
                source_url for degree in degrees for source_url in degree.calendar_urls
            ),
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
            "por título normalizado y plan institucional en los calendarios. Convocatoria "
            "publicada no implica asistencia, calificación ni que el examen siga pendiente."
        ),
    }
