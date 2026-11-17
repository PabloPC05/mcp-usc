from datetime import datetime, timedelta

import pytest

from mcp_usc.domain import MADRID, normalise_course, normalise_event
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
    with pytest.raises(ValueError, match="1 y 200"):
        await UscService().list_events(
            days=60,
            include_overdue=True,
            course_ids=None,
            limit=201,
        )


class _TimelineGateway:
    """Timeline as Moodle returns it: oldest first, at most `limit` events."""

    def __init__(self, events: list[dict]) -> None:
        self.events = events
        self.requests: list[tuple[int, int, int]] = []

    async def action_events(self, start: int, end: int, limit: int) -> list[dict]:
        self.requests.append((start, end, limit))
        return [event for event in self.events if start <= event["timesort"] <= end][:limit]


def _event(event_id: int, course_id: int, when: datetime, description: str = "") -> dict:
    return {
        "id": event_id,
        "name": f"Tarea {event_id}",
        "description": description,
        "courseid": course_id,
        "timesort": int(when.timestamp()),
        "timestart": int(when.timestamp()),
    }


def _service_with(gateway: _TimelineGateway) -> UscService:
    service = object.__new__(UscService)
    service._campus = lambda: gateway
    return service


async def test_overdue_work_is_limited_to_the_current_academic_year() -> None:
    now = datetime.now(MADRID)
    last_year = now - timedelta(days=400)
    gateway = _TimelineGateway(
        [_event(1, 3, last_year), _event(2, 3, now - timedelta(days=3)),
         _event(3, 3, now + timedelta(days=2))]
    )

    result = await _service_with(gateway).list_events(
        days=21, include_overdue=True, course_ids=None, limit=50
    )

    assert [item["event_id"] for item in result] == [2, 3]
    start = datetime.fromtimestamp(gateway.requests[0][0], MADRID)
    assert (start.month, start.day) == (9, 1)
    assert start <= now - timedelta(days=3)


async def test_course_filter_is_applied_before_the_result_limit() -> None:
    now = datetime.now(MADRID)
    others = [_event(i, 9, now + timedelta(hours=i)) for i in range(1, 6)]
    wanted = _event(99, 3, now + timedelta(days=4))
    gateway = _TimelineGateway([*others, wanted])

    result = await _service_with(gateway).list_events(
        days=21, include_overdue=False, course_ids=[3], limit=2
    )

    assert [item["event_id"] for item in result] == [99]
    assert gateway.requests[0][2] == 200


async def test_listing_shortens_descriptions_but_keeps_the_marker() -> None:
    now = datetime.now(MADRID)
    gateway = _TimelineGateway([_event(1, 3, now + timedelta(days=1), "palabra " * 400)])

    [item] = await _service_with(gateway).list_events(
        days=21, include_overdue=False, course_ids=None, limit=50
    )

    assert len(item["description"]) <= 401
    assert item["description_truncated"] is True
