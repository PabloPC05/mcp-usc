from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp_usc.exam_calendar import ExamCalendar, ExamCalendarError, ExamCall, ExamSubject
from mcp_usc.exam_catalog import DEGREE_EXAM_PROFILES
from mcp_usc.official_exams import (
    _call_signature,
    _merge_calls,
    discover_official_exam_subjects,
    fetch_official_exam_dates,
)
from mcp_usc.service import UscService
from mcp_usc.settings import Settings
from mcp_usc.study_plans import StudyPlan, StudyPlanError, StudyPlanSubject


def _plan(degree_key: str, subjects: tuple[StudyPlanSubject, ...]) -> StudyPlan:
    degree = DEGREE_EXAM_PROFILES[degree_key]
    return StudyPlan(
        academic_year="2025/2026",
        endpoint_url=(
            "https://www.usc.gal/gl/course/76/study-plan-by-course/"
            f"{degree.study_plan_catalog_id}"
        ),
        source_url=degree.study_plan_url,
        subjects=subjects,
    )


class FakeStudyPlanClient:
    def __init__(self, plans: dict[str, StudyPlan]) -> None:
        self.plans = plans
        self.calls: list[tuple[str, str]] = []

    async def fetch_study_plan(self, url: str, *, academic_year: str) -> StudyPlan:
        self.calls.append((url, academic_year))
        return self.plans[url]


class FakeCalendarClient:
    def __init__(self, calendar: ExamCalendar) -> None:
        self.calendar = calendar
        self.calls: list[tuple[str, str]] = []

    async def fetch_calendar(self, url: str, *, academic_year: str) -> ExamCalendar:
        self.calls.append((url, academic_year))
        return self.calendar


class PartialStudyPlanClient(FakeStudyPlanClient):
    async def fetch_study_plan(self, url: str, *, academic_year: str) -> StudyPlan:
        if "2a-edicion" in url:
            raise StudyPlanError("plan unavailable")
        return await super().fetch_study_plan(url, academic_year=academic_year)


class PartialCalendarClient(FakeCalendarClient):
    async def fetch_calendar(self, url: str, *, academic_year: str) -> ExamCalendar:
        if "escola-tecnica" in url:
            raise ExamCalendarError("calendar unavailable")
        return await super().fetch_calendar(url, academic_year=academic_year)


def _clients(
    *,
    current_subjects: tuple[StudyPlanSubject, ...],
    second_subjects: tuple[StudyPlanSubject, ...],
    calendar_subjects: tuple[ExamSubject, ...],
) -> tuple[FakeStudyPlanClient, FakeCalendarClient]:
    current = DEGREE_EXAM_PROFILES["double_degree_current"]
    second = DEGREE_EXAM_PROFILES["double_degree_second_edition"]
    plans = {
        current.study_plan_url: _plan("double_degree_current", current_subjects),
        second.study_plan_url: _plan("double_degree_second_edition", second_subjects),
    }
    calendar = ExamCalendar(
        academic_year="2025/2026",
        endpoint_url="https://www.usc.gal/gl/course/76/schedules-exams-calendar/5060",
        subjects=calendar_subjects,
    )
    return FakeStudyPlanClient(plans), FakeCalendarClient(calendar)


async def test_official_dates_discover_new_plan_instead_of_homonymous_old_plan() -> None:
    subject = StudyPlanSubject(
        "G1012106", "Cálculo numérico nunha variable", "https://www.usc.gal/gl/estudos/a"
    )
    old_call = ExamCall("1º semestre", "1ª Oportunidade", "19.12.2025 - 16:00", (), ())
    new_call = ExamCall("2º semestre", "1ª Oportunidade", "28.05.2026 - 10:00", (), ())
    plans, calendars = _clients(
        current_subjects=(subject,),
        second_subjects=(),
        calendar_subjects=(
            ExamSubject(15959, subject.name, (old_call,)),
            ExamSubject(19955, subject.name, (new_call,)),
        ),
    )

    result = await fetch_official_exam_dates(
        ["G1012106"],
        "2025/2026",
        study_plan_client=plans,  # type: ignore[arg-type]
        calendar_client=calendars,  # type: ignore[arg-type]
    )

    item = result["subjects"][0]
    assert item["status"] == "matched"
    assert [call["date_time"] for call in item["calls"]] == ["28.05.2026 - 10:00"]
    assert item["candidates"][0]["calendar_plan_id"] == 19955


async def test_variant_code_round_trips_from_listing_to_official_query() -> None:
    subject = StudyPlanSubject(
        "G1012106A",
        "Materia variante",
        "https://www.usc.gal/gl/estudos/variante",
    )
    call = ExamCall("1º semestre", "1ª Oportunidade", "20.01.2026 - 10:00", (), ())
    plans, calendars = _clients(
        current_subjects=(subject,),
        second_subjects=(),
        calendar_subjects=(ExamSubject(19955, subject.name, (call,)),),
    )

    listing = await discover_official_exam_subjects(
        "2025/2026",
        client=plans,  # type: ignore[arg-type]
    )
    result = await fetch_official_exam_dates(
        ["g1012106a"],
        "2025/2026",
        study_plan_client=plans,  # type: ignore[arg-type]
        calendar_client=calendars,  # type: ignore[arg-type]
    )

    assert listing["subjects"][0]["subject_code"] == "G1012106A"
    assert result["subjects"][0]["subject_code"] == "G1012106A"
    assert result["subjects"][0]["status"] == "matched"


async def test_same_code_in_both_plans_is_unified_only_when_calls_are_equal() -> None:
    subject = StudyPlanSubject("G4012222", "Algoritmos e Estruturas de Datos", "https://www.usc.gal/gl/estudos/a")
    second_subject = StudyPlanSubject(subject.code, subject.name, "https://www.usc.gal/gl/estudos/b")
    call = ExamCall("1º semestre", "1ª Oportunidade", "20.01.2026 - 10:00", (), ())
    plans, calendars = _clients(
        current_subjects=(subject,),
        second_subjects=(second_subject,),
        calendar_subjects=(
            ExamSubject(19955, subject.name, (call,)),
            ExamSubject(17573, subject.name, (call,)),
        ),
    )

    result = await fetch_official_exam_dates(
        [subject.code],
        "2025/2026",
        study_plan_client=plans,  # type: ignore[arg-type]
        calendar_client=calendars,  # type: ignore[arg-type]
    )

    assert result["subjects"][0]["status"] == "matched_equivalent_plans"
    assert len(result["subjects"][0]["candidates"]) == 2


async def test_same_code_with_different_plan_dates_is_ambiguous() -> None:
    subject = StudyPlanSubject("G4012222", "Algoritmos e Estruturas de Datos", "https://www.usc.gal/gl/estudos/a")
    second_subject = StudyPlanSubject(subject.code, subject.name, "https://www.usc.gal/gl/estudos/b")
    first = ExamCall("1º semestre", "1ª Oportunidade", "20.01.2026 - 10:00", (), ())
    other = ExamCall("1º semestre", "1ª Oportunidade", "21.01.2026 - 10:00", (), ())
    plans, calendars = _clients(
        current_subjects=(subject,),
        second_subjects=(second_subject,),
        calendar_subjects=(
            ExamSubject(19955, subject.name, (first,)),
            ExamSubject(17573, subject.name, (other,)),
        ),
    )

    result = await fetch_official_exam_dates(
        [subject.code],
        "2025/2026",
        study_plan_client=plans,  # type: ignore[arg-type]
        calendar_client=calendars,  # type: ignore[arg-type]
    )

    assert result["subjects"][0]["status"] == "ambiguous"
    assert result["subjects"][0]["calls"] == []

    selected = await fetch_official_exam_dates(
        [subject.code],
        "2025/2026",
        ["double_degree_current"],
        study_plan_client=plans,  # type: ignore[arg-type]
        calendar_client=calendars,  # type: ignore[arg-type]
    )
    assert selected["subjects"][0]["status"] == "matched"
    assert selected["subjects"][0]["calls"][0]["date_time"] == "20.01.2026 - 10:00"


def test_equivalent_calls_merge_room_evidence_without_changing_identity() -> None:
    first = [
        {
            "semester": "1º",
            "opportunity": "1ª",
            "date_time": "20.01.2026 - 10:00",
            "rooms": ["A1"],
            "groups": ["G1"],
        }
    ]
    second = [
        {
            "semester": "1º",
            "opportunity": "1ª",
            "date_time": "20.01.2026 - 10:00",
            "rooms": ["A2"],
            "groups": ["G1"],
        }
    ]

    assert _call_signature(first) == _call_signature(second)
    assert _merge_calls([first, second])[0]["rooms"] == ["A1", "A2"]


async def test_unknown_code_is_not_found_after_dynamic_discovery() -> None:
    plans, calendars = _clients(
        current_subjects=(), second_subjects=(), calendar_subjects=()
    )

    result = await fetch_official_exam_dates(
        ["G9999999"],
        "2025/2026",
        study_plan_client=plans,  # type: ignore[arg-type]
        calendar_client=calendars,  # type: ignore[arg-type]
    )

    assert result["subjects"][0]["status"] == "not_found_in_study_plans"
    assert len(plans.calls) == 2


async def test_partial_plan_failure_keeps_evidence_without_claiming_unique_resolution() -> None:
    subject = StudyPlanSubject(
        "G1012106", "Cálculo numérico nunha variable", "https://www.usc.gal/gl/estudos/a"
    )
    call = ExamCall("2º semestre", "1ª Oportunidade", "28.05.2026 - 10:00", (), ())
    plans, calendars = _clients(
        current_subjects=(subject,),
        second_subjects=(),
        calendar_subjects=(ExamSubject(19955, subject.name, (call,)),),
    )

    result = await fetch_official_exam_dates(
        [subject.code],
        "2025/2026",
        study_plan_client=PartialStudyPlanClient(plans.plans),  # type: ignore[arg-type]
        calendar_client=calendars,  # type: ignore[arg-type]
    )

    assert result["complete"] is False
    assert result["subjects"][0]["status"] == "matched_partial_discovery"
    assert result["subjects"][0]["calls"]


async def test_partial_calendar_failure_is_visible_on_matched_candidate() -> None:
    subject = StudyPlanSubject(
        "G1012106", "Cálculo numérico nunha variable", "https://www.usc.gal/gl/estudos/a"
    )
    call = ExamCall("2º semestre", "1ª Oportunidade", "28.05.2026 - 10:00", (), ())
    plans, calendars = _clients(
        current_subjects=(subject,),
        second_subjects=(),
        calendar_subjects=(ExamSubject(19955, subject.name, (call,)),),
    )

    result = await fetch_official_exam_dates(
        [subject.code],
        "2025/2026",
        study_plan_client=plans,  # type: ignore[arg-type]
        calendar_client=PartialCalendarClient(calendars.calendar),  # type: ignore[arg-type]
    )

    assert result["complete"] is False
    assert result["subjects"][0]["status"] == "matched_partial"
    assert result["subjects"][0]["candidates"][0]["source_errors"]


async def test_dynamic_catalog_lists_subjects_from_both_official_plans() -> None:
    shared = StudyPlanSubject(
        "G4012222", "Algoritmos e Estruturas de Datos", "https://www.usc.gal/gl/estudos/a"
    )
    second = StudyPlanSubject(shared.code, shared.name, "https://www.usc.gal/gl/estudos/b")
    plans, _calendars = _clients(
        current_subjects=(shared,), second_subjects=(second,), calendar_subjects=()
    )

    result = await discover_official_exam_subjects(
        "2025/2026", client=plans  # type: ignore[arg-type]
    )

    assert result["complete"] is True
    assert result["count"] == 1
    assert result["subjects"][0]["ambiguous_between_degrees"] is True
    assert len(result["subjects"][0]["candidates"]) == 2


async def test_official_dates_reject_a_date_outside_requested_academic_year() -> None:
    subject = StudyPlanSubject(
        "G1012106", "Cálculo numérico nunha variable", "https://www.usc.gal/gl/estudos/a"
    )
    invalid = ExamCall("2º semestre", "1ª Oportunidade", "28.05.2027 - 10:00", (), ())
    plans, calendars = _clients(
        current_subjects=(subject,),
        second_subjects=(),
        calendar_subjects=(ExamSubject(19955, subject.name, (invalid,)),),
    )

    result = await fetch_official_exam_dates(
        [subject.code],
        "2025/2026",
        study_plan_client=plans,  # type: ignore[arg-type]
        calendar_client=calendars,  # type: ignore[arg-type]
    )

    candidate = result["subjects"][0]["candidates"][0]
    assert candidate["status"] == "source_changed_or_unavailable"
    assert "fuera del curso" in candidate["error"]


class FakeCourseGateway:
    async def list_courses(self, include_archived: bool = False) -> list[dict[str, Any]]:
        assert include_archived is True
        return [
            {
                "id": 1,
                "shortname": "[2025/26] [G1012106] CNV",
                "fullname": "[2025/26] [G1012106] Cálculo numérico nunha variable",
                "hidden": True,
            },
            {
                "id": 2,
                "shortname": "[2024/25] [G1012106] CNV",
                "fullname": "Curso anterior",
            },
            {"id": 3, "shortname": "COORD", "fullname": "Coordinación do dobre grao"},
        ]


async def test_legacy_subject_listing_without_year_fails_closed_without_network(
    tmp_path: Path,
) -> None:
    service = UscService(
        Settings(
            moodle_url="https://cv.usc.es",
            moodle_token=None,
            browser_channel="chromium",
            browser_profile_dir=tmp_path / "browser",
            exam_sources=(),
        )
    )

    result = await service.list_official_exam_subjects(None, None)

    assert result["status"] == "academic_year_required"
    assert result["subjects"] == []


async def test_my_schedule_uses_hidden_courses_and_filters_exact_year(
    monkeypatch: Any, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    async def fake_fetch(
        codes: list[str],
        year: str,
        degree_keys: list[str] | None,
        *,
        timeout: float,
        cache: object,
    ) -> dict[str, Any]:
        captured.update(
            codes=codes,
            year=year,
            degree_keys=degree_keys,
            timeout=timeout,
            cache=cache,
        )
        return {
            "academic_year": year,
            "subjects": [],
            "sources_checked": [],
            "fetched_at": "now",
        }

    monkeypatch.setattr("mcp_usc.service.fetch_official_exam_dates", fake_fetch)
    service = UscService(
        Settings(
            moodle_url="https://cv.usc.es",
            moodle_token=None,
            browser_channel="chromium",
            browser_profile_dir=tmp_path / "browser",
            exam_sources=(),
        )
    )
    service._campus = lambda: FakeCourseGateway()  # type: ignore[method-assign]

    result = await service.get_my_official_exam_schedule("2025/2026")

    assert captured["codes"] == ["G1012106"]
    assert captured["year"] == "2025/2026"
    assert captured["degree_keys"] is None
    assert captured["cache"] is service._public_http_cache
    assert result["matched_moodle_courses"][0]["dashboard_hidden"] is True
