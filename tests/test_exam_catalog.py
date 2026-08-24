from __future__ import annotations

import pytest

from mcp_usc.exam_catalog import (
    DEGREE_EXAM_PROFILES,
    extract_academic_year,
    extract_subject_code,
    normalise_academic_year,
    normalise_subject_code,
)


def test_degree_crosswalks_distinguish_current_and_second_edition() -> None:
    current = DEGREE_EXAM_PROFILES["double_degree_current"]
    second = DEGREE_EXAM_PROFILES["double_degree_second_edition"]

    assert current.study_plan_catalog_id == 20872
    assert current.calendar_plan_id == 19955
    assert second.study_plan_catalog_id == 18399
    assert second.calendar_plan_id == 17573


def test_course_metadata_extractors_accept_moodle_labels() -> None:
    assert extract_subject_code("[2025/26] [G4012222] Algoritmos") == "G4012222"
    assert extract_subject_code("[2025/26] [G4012222a] Variante") == "G4012222A"
    assert extract_subject_code("[G4012222AB] código ambiguo") is None
    assert extract_academic_year("[2025/26] [G4012222] Algoritmos") == "2025/2026"


def test_subject_code_with_suffix_round_trips_through_normaliser_and_extractor() -> None:
    code = normalise_subject_code(" g1012106a ")

    assert code == "G1012106A"
    assert extract_subject_code(f"[2026/27] [{code}] Materia") == code


@pytest.mark.parametrize("value", ["G1012106AB", "1012106", "G101210", "G10121060"])
def test_subject_code_validation_rejects_ambiguous_values(value: str) -> None:
    with pytest.raises(ValueError, match="formato"):
        normalise_subject_code(value)


def test_academic_year_validation_requires_consecutive_years() -> None:
    assert normalise_academic_year("2025/2026") == "2025/2026"
    with pytest.raises(ValueError, match="formato"):
        normalise_academic_year("2025-2026")
    with pytest.raises(ValueError, match="consecutivos"):
        normalise_academic_year("2025/2027")
