"""Things Moodle hands out in pieces: the timeline in pages of 50, labels one by one."""

from __future__ import annotations

from typing import Any

from mcp_usc import campus
from mcp_usc.service import _fill_label_texts


async def test_timeline_follows_moodle_pages_after_the_last_event():
    events = [{"id": n, "name": f"T{n}"} for n in range(1, 121)]
    calls: list[dict[str, Any]] = []

    async def call(function: str, arguments: dict[str, Any]) -> dict[str, Any]:
        calls.append(arguments)
        after = arguments["aftereventid"]
        start = next((i + 1 for i, event in enumerate(events) if event["id"] == after), 0)
        return {"events": events[start : start + arguments["limitnum"]]}

    got = await campus._paged_action_events(call, 0, 10, 200)

    assert [event["id"] for event in got] == list(range(1, 121))
    assert [c["aftereventid"] for c in calls] == [0, 50, 100]
    assert all(c["limitnum"] == 50 for c in calls)


async def test_timeline_stops_at_the_limit():
    async def call(function: str, arguments: dict[str, Any]) -> dict[str, Any]:
        after = arguments["aftereventid"]
        return {"events": [{"id": after + n} for n in range(1, arguments["limitnum"] + 1)]}

    got = await campus._paged_action_events(call, 0, 10, 70)
    assert len(got) == 70 and got[-1]["id"] == 70


class _Gateway:
    def __init__(self, rendered: list[Any]) -> None:
        self.rendered = rendered
        self.calls: list[tuple[str, list[dict[str, Any]]]] = []

    async def invoke_many(self, function: str, arguments: list[dict[str, Any]]) -> list[Any]:
        self.calls.append((function, list(arguments)))
        return self.rendered


async def test_labels_get_their_own_text_in_one_request():
    items = [
        {
            "module_type": "label",
            "module_id": 7,
            "name": "ProfesoradoAna: Cate...",
            "description": "",
        },
        {"module_type": "resource", "module_id": 8, "name": "Tema 1", "description": ""},
        {"module_type": "label", "module_id": 9, "name": "Etiqueta", "description": ""},
    ]
    gateway = _Gateway(
        [
            '<li class="activity label"><div class="activity-altcontent"><p>Profesorado</p>'
            "<p>Ana Proba: Catedrática. Despacho 7.</p></div></li>",
            campus.CampusProtocolError("Moodle no respondió a esta llamada."),
        ]
    )

    await _fill_label_texts(gateway, items)

    assert gateway.calls == [("core_course_get_module", [{"id": 7}, {"id": 9}])]
    assert "Ana Proba: Catedrática. Despacho 7." in items[0]["description"]
    assert items[1]["description"] == "" and items[2]["description"] == ""


async def test_labels_without_batching_keep_their_names():
    class _Rest:
        pass

    items = [{"module_type": "label", "module_id": 7, "name": "Aviso", "description": ""}]
    await _fill_label_texts(_Rest(), items)
    assert items[0]["description"] == ""
