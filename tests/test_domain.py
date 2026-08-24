import pytest

from mcp_usc.domain import normalise_course, normalise_event
from mcp_usc.service import UscService


def test_normalise_course_removes_html() -> None:
    result = normalise_course(
        {"id": "42", "shortname": "MAT", "fullname": "<b>Matemáticas</b>", "hidden": 0}
    )
    assert result["id"] == 42
    assert result["full_name"] == "Matemáticas"
    assert result["visible"] is True
    assert result["dashboard_hidden"] is False


def test_normalise_course_distinguishes_visibility_from_dashboard_preference() -> None:
    result = normalise_course(
        {
            "id": "43",
            "fullname": "Álxebra",
            "hidden": True,
            "visible": True,
        }
    )

    assert result["visible"] is True
    assert result["dashboard_hidden"] is True


def test_normalise_event_keeps_source_links_and_cleans_description() -> None:
    result = normalise_event(
        {
            "id": 7,
            "name": "Entrega",
            "description": "<p>Lee el enunciado</p><script>ignore()</script>",
            "courseid": 42,
            "timesort": 1_800_000_000,
            "timeusermidnight": 1_799_960_400,
            "formattedtime": "<span>martes, 15 de xaneiro, 09:00</span>",
            "url": "https://cv.usc.es/calendar/event.php?id=7",
            "action": {"name": "Entregar", "url": "https://cv.usc.es/mod/assign/view.php?id=3"},
        }
    )
    assert result["description"] == "Lee el enunciado"
    assert result["event_id"] == 7
    assert result["action_url"].endswith("id=3")
    assert result["time_left"] == "martes, 15 de xaneiro, 09:00"


async def test_list_events_rejects_values_above_moodle_page_limit() -> None:
    with pytest.raises(ValueError, match="1 y 50"):
        await UscService().list_events(
            days=60,
            include_overdue=True,
            course_ids=None,
            limit=51,
        )
