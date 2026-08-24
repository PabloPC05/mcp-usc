from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp_usc.exam_calendar import ExamCalendar, ExamCall, ExamSubject
from mcp_usc.official_exams import fetch_official_exam_dates
from mcp_usc.service import UscService
from mcp_usc.settings import Settings


class FakeCalendarClient:
    def __init__(self, calendar: ExamCalendar) -> None:
        self.calendar = calendar
        self.calls: list[tuple[str, str]] = []

    async def fetch_calendar(self, url: str, *, academic_year: str) -> ExamCalendar:
        self.calls.append((url, academic_year))
        return self.calendar


async def test_official_dates_distinguish_new_plan_from_homonymous_old_plan() -> None:
    old_call = ExamCall("1º semestre", "1ª Oportunidade", "19.12.2025 - 16:00", (), ())
    new_call = ExamCall("2º semestre", "1ª Oportunidade", "28.05.2026 - 10:00", (), ())
    calendar = ExamCalendar(
        academic_year="2025/2026",
        endpoint_url="https://www.usc.gal/gl/course/76/schedules-exams-calendar/5060",
        subjects=(
            ExamSubject(15959, "Cálculo numérico nunha variable", (old_call,)),
            ExamSubject(19955, "Cálculo numérico nunha variable", (new_call,)),
        ),
    )

    result = await fetch_official_exam_dates(
        ["G1012106"],
        "2025/2026",
        client=FakeCalendarClient(calendar),  # type: ignore[arg-type]
    )

    assert result["subjects"][0]["status"] == "matched"
    assert result["subjects"][0]["plan_id"] == 19955
    assert [call["date_time"] for call in result["subjects"][0]["calls"]] == [
        "28.05.2026 - 10:00"
    ]


async def test_unknown_code_is_explicit_and_does_not_fetch() -> None:
    client = FakeCalendarClient(ExamCalendar("2025/2026", "unused", ()))
    result = await fetch_official_exam_dates(
        ["G9999999"],
        "2025/2026",
        client=client,  # type: ignore[arg-type]
    )

    assert result["subjects"] == [
        {"subject_code": "G9999999", "status": "unsupported_code", "calls": []}
    ]
    assert client.calls == []


async def test_official_dates_reject_a_date_outside_requested_academic_year() -> None:
    invalid = ExamCall("2º semestre", "1ª Oportunidade", "28.05.2027 - 10:00", (), ())
    calendar = ExamCalendar(
        "2025/2026",
        "https://www.usc.gal/gl/course/76/schedules-exams-calendar/5060",
        (ExamSubject(19955, "Cálculo numérico nunha variable", (invalid,)),),
    )

    result = await fetch_official_exam_dates(
        ["G1012106"],
        "2025/2026",
        client=FakeCalendarClient(calendar),  # type: ignore[arg-type]
    )

    subject = result["subjects"][0]
    assert subject["status"] == "source_changed_or_unavailable"
    assert "fuera del curso" in subject["error"]
    assert subject["calls"] == []


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


async def test_my_schedule_uses_hidden_courses_and_filters_exact_year(
    monkeypatch: Any, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    async def fake_fetch(codes: list[str], year: str, *, timeout: float) -> dict[str, Any]:
        captured.update(codes=codes, year=year, timeout=timeout)
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
    assert result["matched_moodle_courses"][0]["dashboard_hidden"] is True
