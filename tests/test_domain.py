from mcp_usc.domain import normalise_course, normalise_event


def test_normalise_course_removes_html() -> None:
    result = normalise_course(
        {"id": "42", "shortname": "MAT", "fullname": "<b>Matemáticas</b>", "hidden": 0}
    )
    assert result["id"] == 42
    assert result["full_name"] == "Matemáticas"
    assert result["visible"] is True


def test_normalise_event_keeps_source_links_and_cleans_description() -> None:
    result = normalise_event(
        {
            "id": 7,
            "name": "Entrega",
            "description": "<p>Lee el enunciado</p><script>ignore()</script>",
            "courseid": 42,
            "timesort": 1_800_000_000,
            "url": "https://cv.usc.es/calendar/event.php?id=7",
            "action": {"name": "Entregar", "url": "https://cv.usc.es/mod/assign/view.php?id=3"},
        }
    )
    assert result["description"] == "Lee el enunciado"
    assert result["event_id"] == 7
    assert result["action_url"].endswith("id=3")
