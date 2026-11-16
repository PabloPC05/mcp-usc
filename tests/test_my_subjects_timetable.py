"""Timetable of the student's own subjects across courses and degrees (any degree)."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest

from mcp_usc import service as service_module
from mcp_usc.academic_profile import AcademicProfile, AcademicProfileError
from mcp_usc.degree_catalog import DegreeCatalogEntry, SubjectDegreeLocation
from mcp_usc.official_exams import StudentSubjectSearch
from mcp_usc.service import UscService
from mcp_usc.settings import Settings
from mcp_usc.study_plans import StudyPlanSubject

INF = "https://www.usc.gal/gl/estudos/graos/enxenaria-arquitectura/grao-enxenaria-informatica"
MAT = "https://www.usc.gal/gl/estudos/graos/ciencias/grao-matematicas"


def _location(
    degree_url: str, code: str, name: str, course: int | None, semester: int | None, offering: str
) -> SubjectDegreeLocation:
    degree = DegreeCatalogEntry(
        name=f"Grao {degree_url.rsplit('-', 1)[-1]}",
        url=degree_url,
        area_slug="x",
        campus="Santiago",
    )
    subject = StudyPlanSubject(
        code,
        name,
        f"{degree_url}/20262027/{name.lower()}-{offering}",
        course_number=course,
        semester=semester,
    )
    return SubjectDegreeLocation(
        degree=degree, plan_endpoint_url=f"{degree_url}/plan", subject=subject
    )


class _Search:
    def __init__(self, codes: tuple[str, ...]) -> None:
        self.requested_codes = codes
        self.complete = True


def _session(offering: str, name: str, day: str, start: str, group: str) -> dict[str, Any]:
    return {
        "date": day,
        "start_time": start,
        "subject_name": name,
        "subject_url": f"https://www.usc.gal/gl/plan/1/course/77/subject/{offering}",
        "group_code": group,
        "room": "A1",
    }


class _FakeTimetables:
    calls: list[tuple[str, int, int | None]] = []

    def __init__(self, **kwargs: Any) -> None:
        del kwargs

    async def fetch_degree_timetable(
        self, degree_url: str, *, course_number: int, program_id: int | None = None, **kwargs: Any
    ) -> dict:
        self.calls.append((degree_url, course_number, program_id))
        if degree_url == MAT and program_id is None:
            return {
                "status": "program_selection_required",
                "program_options": [{"program_id": 11}, {"program_id": 12}],
            }
        if degree_url == MAT:
            other = "9-9-9-9" if program_id == 11 else "5-5-5-5"
            return {
                "status": "ok",
                "complete": True,
                "sources": [],
                "sessions": [_session(other, "Topoloxía", "2026-11-17", "10:00", "/CLE_01")],
            }
        return {
            "status": "ok",
            "complete": True,
            "degree_name": "Informática",
            "sources": [],
            "sessions": [
                _session("1-1-1-1", "Intelixencia Artificial", "2026-11-16", "11:00", "/CLE_01"),
                _session("1-1-1-1", "Intelixencia Artificial", "2026-11-16", "15:30", "/CLIL_03"),
                _session("7-7-7-7", "Outra materia do curso", "2026-11-16", "09:00", "/CLE_01"),
            ],
        }


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> UscService:
    locations = (
        _location(INF, "G4012328", "Intelixencia", 3, 1, "1-1-1-1"),
        _location(MAT, "G1011422", "Topoloxia", 2, None, "5-5-5-5"),
        _location(INF, "G4012227", "Sistemas", 2, 2, "3-3-3-3"),
    )

    async def fake_locate(codes: list[str], year: str, **kwargs: Any) -> StudentSubjectSearch:
        return StudentSubjectSearch(
            locations=tuple(loc for loc in locations if loc.subject.code in codes),
            searches=(_Search(tuple(codes)),),  # type: ignore[arg-type]
            scope=(INF,),
            ignored_scope=(),
            widened=False,
        )

    _FakeTimetables.calls = []
    monkeypatch.setattr(service_module, "locate_student_subjects", fake_locate)
    monkeypatch.setattr(service_module, "UscClassTimetableClient", _FakeTimetables)
    return UscService(
        Settings(
            moodle_url="https://cv.usc.es",
            moodle_token=None,
            browser_channel="auto",
            browser_profile_dir=tmp_path,
            exam_sources=(),
            academic_profile=AcademicProfile(degree_url=INF),
        )
    )


async def test_only_my_subjects_are_kept_across_courses_and_degrees(service: UscService) -> None:
    result = await service.get_my_subjects_timetable(
        academic_year="2026/2027",
        semester=1,
        date_in_week="2026-11-16",
        subject_codes=["G4012328", "G1011422", "G4012227", "G9999999"],
    )

    assert result["subject_source"] == "argument"
    assert [(s["subject_code"], s["start_time"]) for s in result["sessions"]] == [
        ("G4012328", "11:00"),
        ("G4012328", "15:30"),
        ("G1011422", "10:00"),
    ]
    statuses = {item["subject_code"]: item["status"] for item in result["subjects"]}
    assert statuses == {
        "G4012328": "included",
        "G1011422": "included",
        "G4012227": "other_semester",
        "G9999999": "not_found_in_study_plans",
    }
    # Homonymous plans were tried one by one; only the exact offering id survived.
    assert (MAT, 2, 11) in _FakeTimetables.calls and (MAT, 2, 12) in _FakeTimetables.calls
    assert (INF, 2, None) not in _FakeTimetables.calls  # other-semester subject not fetched


async def test_profile_subject_codes_are_used_without_moodle(service: UscService) -> None:
    profile = AcademicProfile(degree_url=INF, subject_codes=("G4012328",))
    service.settings = dataclasses.replace(service.settings, academic_profile=profile)

    result = await service.get_my_subjects_timetable(academic_year="2026/2027", semester=1)

    assert result["subject_source"] == "profile"
    assert {s["subject_code"] for s in result["sessions"]} == {"G4012328"}


async def test_without_any_subject_source_the_error_explains_the_options(
    service: UscService, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def no_courses(year: str) -> tuple[list[str], list[dict[str, Any]]]:
        del year
        return [], []

    monkeypatch.setattr(service, "_moodle_subject_codes", no_courses)

    with pytest.raises(AcademicProfileError, match="subject_codes"):
        await service.get_my_subjects_timetable(academic_year="2026/2027", semester=1)


def test_without_a_profile_the_degree_holding_most_subjects_wins() -> None:
    shared = _location(MAT, "G1", "Comun", 1, 1, "1-0-0-1")
    mine = (
        _location(INF, "G1", "Comun", 1, 1, "1-0-0-2"),
        _location(INF, "G2", "Outra", 1, 1, "1-0-0-3"),
        _location(INF, "G3", "Mais", 1, 1, "1-0-0-4"),
    )
    search = StudentSubjectSearch(
        locations=(shared, *mine), searches=(), scope=(), ignored_scope=(), widened=False
    )

    assert search.for_code("G1")[0].degree.url == INF
