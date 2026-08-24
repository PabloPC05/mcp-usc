from __future__ import annotations

import pytest

from mcp_usc.exam_catalog import (
    EXAM_SUBJECT_PROFILES,
    extract_academic_year,
    extract_subject_code,
    normalise_academic_year,
    normalise_subject_code,
)


def test_new_numerical_calculus_profile_is_bound_to_new_plan() -> None:
    profile = EXAM_SUBJECT_PROFILES["G1012106"]

    assert profile.name == "Cálculo numérico nunha variable"
    assert profile.plan_id == 19955


def test_course_metadata_extractors_accept_moodle_labels() -> None:
    assert extract_subject_code("[2025/26] [G4012222] Algoritmos") == "G4012222"
    assert extract_academic_year("[2025/26] [G4012222] Algoritmos") == "2025/2026"


@pytest.mark.parametrize("value", ["G1012106x", "1012106", "G101210", "G10121060"])
def test_subject_code_validation_rejects_ambiguous_values(value: str) -> None:
    with pytest.raises(ValueError, match="formato"):
        normalise_subject_code(value)


def test_academic_year_validation_requires_consecutive_years() -> None:
    assert normalise_academic_year("2025/2026") == "2025/2026"
    with pytest.raises(ValueError, match="formato"):
        normalise_academic_year("2025-2026")
    with pytest.raises(ValueError, match="consecutivos"):
        normalise_academic_year("2025/2027")
