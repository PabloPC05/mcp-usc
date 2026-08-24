from __future__ import annotations

import pytest

from mcp_usc import server
from mcp_usc.experience import (
    assignment_review_prompt,
    compatibility_overview,
    daily_briefing_prompt,
    exam_planning_prompt,
    prepare_assignment_submission_prompt,
    safety_guide,
    workflow_catalog,
)


def test_passive_resources_are_local_and_explain_safety() -> None:
    compatibility = compatibility_overview()
    workflows = workflow_catalog()

    assert compatibility["version"] == "0.11.0"
    assert compatibility["network_contacted"] is False
    assert compatibility["mcp"]["tools"] == 86
    assert compatibility["mcp"]["resources"] == 4
    assert workflows["network_contacted"] is False
    assert len(workflows["prompts"]) == 4
    assert "MoodleSession" in safety_guide()


def test_read_only_prompts_are_bounded_and_never_authorize_effects() -> None:
    briefing = daily_briefing_prompt(14, include_archived=True)
    exams = exam_planning_prompt("2026/2027")
    assignments = assignment_review_prompt("AED", 30)

    assert "list_courses(include_archived=true)" in briefing
    assert "No llames previews" in briefing
    assert 'academic_year="2026/2027"' in exams
    assert "No crees eventos" in exams
    assert "No abras páginas stateful" in assignments


@pytest.mark.parametrize("days", [0, 91])
def test_prompt_day_range_is_validated(days: int) -> None:
    with pytest.raises(ValueError, match="days"):
        daily_briefing_prompt(days)


@pytest.mark.parametrize("academic_year", ["2026", "2026/2028", "26/27"])
def test_exam_prompt_requires_a_consecutive_academic_year(academic_year: str) -> None:
    with pytest.raises(ValueError, match="academic_year"):
        exam_planning_prompt(academic_year)


def test_submission_prompt_stops_after_preview() -> None:
    prompt = prepare_assignment_submission_prompt(123, "reemplazar informe.pdf")

    assert "DETENTE después del preview" in prompt
    assert "No llames la herramienta de efecto" in prompt
    assert "confirmación humana nueva" in prompt


def test_submission_prompt_rejects_invalid_scope() -> None:
    with pytest.raises(ValueError, match="assignment_id"):
        prepare_assignment_submission_prompt(0)

    with pytest.raises(ValueError, match="intended_change"):
        prepare_assignment_submission_prompt(1, " ")


def test_registered_resources_and_prompts_never_create_a_campus_service(monkeypatch) -> None:
    def fail_service():
        raise AssertionError("resources and prompts must remain offline")

    monkeypatch.setattr(server, "_service", fail_service)

    assert server.about_resource()["network_contacted"] is False
    assert "no contacta con Moodle" in server.safety_resource()
    assert server.compatibility_resource()["network_contacted"] is False
    assert server.workflows_resource()["network_contacted"] is False
    assert "No llames previews" in server.daily_briefing()
    assert "Este flujo es de solo lectura" in server.exam_planning("2026/2027")
    assert "No abras páginas stateful" in server.assignment_review("AED")
    assert "DETENTE después del preview" in server.prepare_assignment_submission(123)
