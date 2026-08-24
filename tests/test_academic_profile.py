from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from mcp_usc.academic_profile import (
    AcademicProfile,
    AcademicProfileError,
    load_academic_profile,
)
from mcp_usc.service import UscService
from mcp_usc.settings import Settings

DEGREE_URL = "https://www.usc.gal/gl/estudos/graos/filoloxia/lingua"


def test_profile_is_loaded_from_explicit_environment_and_normalised() -> None:
    profile = load_academic_profile(
        {
            "USC_ACADEMIC_DEGREE_URL": DEGREE_URL,
            "USC_ACADEMIC_COURSE_NUMBER": "2",
            "USC_ACADEMIC_PROGRAM_ID": "19955",
            "USC_ACADEMIC_GROUP_CODES": " cle_01;CLE_02;cle_01 ",
            "USC_ACADEMIC_YEAR": "2025/2026",
            "USC_ACADEMIC_SEMESTER": "2",
            "USC_ACADEMIC_DATE_IN_WEEK": "2026-02-16",
        }
    )

    assert profile is not None
    assert profile.course_number == 2
    assert profile.program_id == 19955
    assert profile.group_codes == ("CLE_01", "CLE_02")
    assert profile.resolve() == ("2025/2026", 2, "2026-02-16")


def test_profile_file_is_local_and_environment_values_override(tmp_path) -> None:
    path = tmp_path / "academic-profile.json"
    path.write_text(
        json.dumps(
            {
                "degree_url": DEGREE_URL,
                "course_number": 1,
                "program_id": 100,
                "group_codes": ["A"],
            }
        ),
        encoding="utf-8",
    )

    profile = load_academic_profile(
        {
            "USC_ACADEMIC_PROFILE_FILE": str(path),
            "USC_ACADEMIC_COURSE_NUMBER": "3",
        }
    )

    assert profile is not None
    assert profile.source == "file"
    assert profile.course_number == 3
    assert profile.program_id == 100


@pytest.mark.parametrize(
    "env",
    [
        {
            "USC_ACADEMIC_DEGREE_URL": "https://evil.example/degree",
            "USC_ACADEMIC_COURSE_NUMBER": "2",
        },
        {"USC_ACADEMIC_DEGREE_URL": DEGREE_URL},
        {"USC_ACADEMIC_DEGREE_URL": DEGREE_URL, "USC_ACADEMIC_COURSE_NUMBER": "13"},
        {
            "USC_ACADEMIC_DEGREE_URL": DEGREE_URL,
            "USC_ACADEMIC_COURSE_NUMBER": "2",
            "USC_ACADEMIC_SEMESTER": "3",
        },
        {
            "USC_ACADEMIC_DEGREE_URL": DEGREE_URL,
            "USC_ACADEMIC_COURSE_NUMBER": "2",
            "USC_ACADEMIC_GROUP_CODES": "A;not valid",
        },
    ],
)
def test_profile_rejects_incomplete_or_unsafe_values(env) -> None:
    with pytest.raises(AcademicProfileError):
        load_academic_profile(env)


def test_profile_rejects_file_schema_and_oversized_file(tmp_path) -> None:
    path = tmp_path / "profile.json"
    path.write_text(json.dumps({"degree_url": DEGREE_URL, "course_number": 2, "unexpected": True}))
    with pytest.raises(AcademicProfileError):
        load_academic_profile({"USC_ACADEMIC_PROFILE_FILE": str(path)})

    path.write_text("x" * (64 * 1024 + 1))
    with pytest.raises(AcademicProfileError):
        load_academic_profile({"USC_ACADEMIC_PROFILE_FILE": str(path)})


def test_profile_resolves_explicit_overrides_and_safe_calendar_defaults() -> None:
    profile = AcademicProfile(DEGREE_URL, 2, academic_year="2025/2026", semester=1)

    assert profile.resolve(
        academic_year="2026/2027", semester=2, date_in_week="2027-03-01"
    ) == ("2026/2027", 2, "2027-03-01")
    assert profile.resolve(today=date(2026, 8, 25))[:2] == ("2025/2026", 1)
    inferred = AcademicProfile(DEGREE_URL, 2)
    assert inferred.resolve(today=date(2026, 10, 1))[:2] == ("2026/2027", 1)
    assert inferred.resolve(today=date(2027, 2, 1))[:2] == ("2026/2027", 2)


def test_no_profile_is_none() -> None:
    assert load_academic_profile({}) is None


@pytest.mark.asyncio
async def test_high_level_service_uses_profile_without_any_moodle_or_write_call(
    monkeypatch,
) -> None:
    profile = AcademicProfile(
        DEGREE_URL,
        2,
        program_id=19955,
        group_codes=("A",),
        academic_year="2025/2026",
        semester=1,
    )
    settings = Settings(
        moodle_url="https://cv.usc.es",
        moodle_token=None,
        browser_channel="chromium",
        browser_profile_dir=Path("browser"),
        exam_sources=(),
        academic_profile=profile,
    )
    service = UscService(settings)
    calls: list[dict[str, object]] = []

    class FakeClient:
        def __init__(self, **kwargs):
            assert "transport" not in kwargs

        async def fetch_degree_timetable(self, degree_url, **kwargs):
            calls.append({"degree_url": degree_url, **kwargs})
            return {
                "status": "ok",
                "sessions": [],
                "sources": [
                    {
                        "program_id": 19955,
                        "center_name": "Centro",
                        "timetable_url": DEGREE_URL,
                    }
                ],
            }

    monkeypatch.setattr("mcp_usc.service.UscClassTimetableClient", FakeClient)
    result = await service.get_my_class_timetable()

    assert calls == [
        {
            "degree_url": DEGREE_URL,
            "course_number": 2,
            "academic_year": "2025/2026",
            "semester": 1,
            "date_in_week": None,
            "group_codes": ("A",),
            "subject_query": "",
            "program_id": 19955,
        }
    ]
    assert result["profile"]["program_id"] == 19955  # type: ignore[index]
    assert result["profile_resolution"]["academic_year"] == "2025/2026"  # type: ignore[index]


@pytest.mark.asyncio
async def test_high_level_service_requires_profile_before_constructing_network_client() -> None:
    settings = Settings(
        moodle_url="https://cv.usc.es",
        moodle_token=None,
        browser_channel="chromium",
        browser_profile_dir=Path("browser"),
        exam_sources=(),
    )
    service = UscService(settings)
    with pytest.raises(AcademicProfileError, match="perfil académico"):
        await service.get_my_class_timetable()
