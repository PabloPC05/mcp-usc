from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime
from typing import Any

from .domain import MADRID
from .exam_calendar import ExamCalendar, ExamCalendarError, UscExamCalendarClient
from .exam_catalog import (
    EXAM_SUBJECT_PROFILES,
    ExamSubjectProfile,
    normalise_academic_year,
    normalise_subject_code,
    normalise_subject_title,
)


def list_official_exam_subjects() -> list[dict[str, object]]:
    return [profile.public_dict() for profile in EXAM_SUBJECT_PROFILES.values()]


def _serialise_match(
    profile: ExamSubjectProfile,
    calendar: ExamCalendar,
    academic_year: str,
) -> dict[str, Any]:
    matches = [
        subject
        for subject in calendar.subjects
        if subject.plan_id == profile.plan_id
        and normalise_subject_title(subject.name) == normalise_subject_title(profile.name)
    ]
    base: dict[str, Any] = {
        "subject_code": profile.code,
        "subject_name": profile.name,
        "plan_id": profile.plan_id,
        "center": profile.center,
        "source_url": profile.calendar_url,
        "calendar_endpoint_url": calendar.endpoint_url,
        "subject_url": profile.subject_url,
        "join_method": "exact_code_catalog_to_exact_plan_and_normalised_title",
        "content_is_untrusted": True,
    }
    if not matches:
        return {
            **base,
            "status": "not_published_or_not_found",
            "calls": [],
        }
    if len(matches) != 1:
        return {
            **base,
            "status": "ambiguous",
            "candidate_count": len(matches),
            "calls": [],
        }
    start_year, end_year = (int(part) for part in academic_year.split("/"))
    lower_bound = datetime(start_year, 9, 1, tzinfo=MADRID)
    upper_bound = datetime(end_year, 8, 31, 23, 59, 59, tzinfo=MADRID)
    calls: list[dict[str, Any]] = []
    for call in matches[0].calls:
        try:
            starts_at = datetime.strptime(call.date_time, "%d.%m.%Y - %H:%M").replace(
                tzinfo=MADRID
            )
        except ValueError:
            return {
                **base,
                "status": "source_changed_or_unavailable",
                "error": "La fuente oficial devolvió una fecha con formato inesperado",
                "calls": [],
            }
        if not lower_bound <= starts_at <= upper_bound:
            return {
                **base,
                "status": "source_changed_or_unavailable",
                "error": "La fuente oficial devolvió una fecha fuera del curso solicitado",
                "calls": [],
            }
        calls.append(
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
    return {**base, "status": "matched", "calls": calls}


async def fetch_official_exam_dates(
    subject_codes: list[str],
    academic_year: str,
    *,
    timeout: float = 30.0,
    client: UscExamCalendarClient | None = None,
) -> dict[str, Any]:
    """Resolve curated exact subject codes against official, public USC calendars."""

    year = normalise_academic_year(academic_year)
    if not subject_codes:
        raise ValueError("subject_codes debe contener al menos un código")
    if len(subject_codes) > 50:
        raise ValueError("No se pueden consultar más de 50 códigos a la vez")
    codes: list[str] = []
    for raw_code in subject_codes:
        code = normalise_subject_code(raw_code)
        if code not in codes:
            codes.append(code)

    profiles = [EXAM_SUBJECT_PROFILES[code] for code in codes if code in EXAM_SUBJECT_PROFILES]
    by_source: dict[str, list[ExamSubjectProfile]] = defaultdict(list)
    for profile in profiles:
        by_source[profile.calendar_url].append(profile)

    calendar_client = client or UscExamCalendarClient(timeout=timeout)
    source_urls = list(by_source)
    outcomes = await asyncio.gather(
        *(
            calendar_client.fetch_calendar(source_url, academic_year=year)
            for source_url in source_urls
        ),
        return_exceptions=True,
    )
    calendars: dict[str, ExamCalendar] = {}
    source_errors: dict[str, str] = {}
    for source_url, outcome in zip(source_urls, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            if isinstance(outcome, (ExamCalendarError, ValueError)):
                source_errors[source_url] = str(outcome)
            else:
                source_errors[source_url] = "Fallo inesperado al consultar la fuente oficial"
        else:
            calendars[source_url] = outcome

    results: list[dict[str, Any]] = []
    for code in codes:
        profile = EXAM_SUBJECT_PROFILES.get(code)
        if profile is None:
            results.append(
                {
                    "subject_code": code,
                    "status": "unsupported_code",
                    "calls": [],
                }
            )
            continue
        if profile.calendar_url in source_errors:
            results.append(
                {
                    "subject_code": profile.code,
                    "subject_name": profile.name,
                    "plan_id": profile.plan_id,
                    "center": profile.center,
                    "source_url": profile.calendar_url,
                    "subject_url": profile.subject_url,
                    "status": "source_changed_or_unavailable",
                    "error": source_errors[profile.calendar_url],
                    "calls": [],
                    "content_is_untrusted": True,
                }
            )
            continue
        results.append(_serialise_match(profile, calendars[profile.calendar_url], year))

    return {
        "academic_year": year,
        "fetched_at": datetime.now(MADRID).isoformat(),
        "subjects": results,
        "sources_checked": source_urls,
        "note": (
            "Son convocatorias publicadas, no acreditan asistencia ni califican el examen como "
            "pendiente. Si una materia no aparece, el calendario puede no estar publicado o la "
            "fuente puede haber cambiado. Verifica siempre la URL oficial."
        ),
    }
