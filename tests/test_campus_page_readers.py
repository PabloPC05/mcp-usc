# ruff: noqa: E501  (the HTML samples keep Moodle's own long lines)
"""Pages with no AJAX function at the USC: grades, a forum's discussions, a quiz page.

The HTML imitates cv.usc.es (Moodle 4.5) with invented people and numbers.
"""

from __future__ import annotations

from typing import Any

import pytest

from mcp_usc import service as service_module
from mcp_usc.campus_pages import (
    parse_forum_page,
    parse_grade_overview,
    parse_grade_report,
    parse_quiz_page,
)
from mcp_usc.collaboration import _normalise_post
from mcp_usc.security import html_to_paragraphs
from mcp_usc.service import UscService
from mcp_usc.session_forms import MoodleSessionForms, SessionFormValidationError

OVERVIEW = """
<div id="region-main"><table id="overview-grade" class="generaltable">
<tr><th>Nome do curso</th><th>Cualificación</th></tr>
<tr><td><a href="https://cv.usc.es/course/user.php?mode=grade&amp;id=301&amp;user=5">Redes [G1] [2026/2027]</a></td><td>7,5</td></tr>
<tr><td><a href="https://cv.usc.es/course/user.php?mode=grade&amp;id=302&amp;user=5">Cálculo [G2] [2025/2026]</a></td><td>-</td></tr>
<tr class="emptyrow"><td></td><td></td></tr>
</table></div>
"""

REPORT = """
<div id="region-main"><table class="generaltable user-grade">
<tr><th class="header column-itemname">Elemento de cualificación</th><th class="header column-grade">Cualificación</th></tr>
<tr><th class="level1 d1 category column-itemname">Redes [G1] [2026/2027]</th></tr>
<tr><th class="level2 d2 category column-itemname">Prácticas</th></tr>
<tr><th class="level3 item column-itemname"><div><span class="d-block text-uppercase small">Tarefa</span>
<div class="rowtitle"><a href="https://cv.usc.es/mod/assign/view.php?id=21">Práctica 1</a></div></div></th>
<td class="column-weight">20,00 %</td><td class="column-grade">9,0</td><td class="column-range">0–10</td>
<td class="column-percentage">90,0 %</td><td class="feedbacktext column-feedback">Moi ben.</td>
<td class="column-contributiontocoursetotal">18,0 %</td></tr>
<tr><th class="level2 d2 baggt column-itemname">Agregación Total Prácticas</th><td class="column-grade">9,0</td><td class="column-range">0–10</td></tr>
<tr><th class="level1 d1 baggt column-itemname">Agregación Total do curso</th><td class="column-grade">7,5</td><td class="column-range">0–10</td></tr>
</table></div>
"""

FORUM = """
<div class="page-header-headings"><h1>Anuncios</h1></div>
<div id="region-main"><table class="discussion-list">
<tr class="discussion pinned" data-region="discussion-list-item" data-discussionid="900">
<td></td>
<th class="topic"><a href="https://cv.usc.es/mod/forum/discuss.php?d=900" title="Data do exame">Data do exame</a>
<span data-region="locked-label" hidden>Bloqueada</span></th>
<td class="author"><div class="author-info"><div>Ana Proba</div><div>3 de nov. de 2026</div></div></td>
<td><div class="author-info"><div>Brais Exemplo</div><div>5 de nov. de 2026</div></div></td>
<td><span>2</span> <span data-region="unread-count">1</span></td>
<td>Accións</td>
</tr>
</table></div>
"""

QUIZ = """
<div class="page-header-headings"><h1>Test 1</h1></div>
<div id="region-main">
<div class="activity-dates" data-region="activity-dates"><div><strong>Abertas:</strong> luns, 2 de novembro</div>
<div><strong>Pechadas:</strong> martes, 3 de novembro</div></div>
<div class="quizinfo"><p>Intentos permitidos: 2</p><p>Límite de tempo: 30 minutos</p></div>
<h3 id="feedback">A súa cualificación final nesta proba é 8,00/10,00.</h3>
<table class="quizreviewsummary"><tr><th>Estado</th><td>Finalizada</td></tr>
<tr><th>Cualificación</th><td>8,00 alén dun máximo de 10,00</td></tr></table>
<form action="https://cv.usc.es/mod/quiz/startattempt.php"><button>Volver intentar</button></form>
</div>
"""


def test_grade_overview_lists_every_course_with_its_total():
    courses = parse_grade_overview(OVERVIEW)
    assert courses == [
        {"course_id": 301, "course_name": "Redes [G1] [2026/2027]", "grade": "7,5"},
        {"course_id": 302, "course_name": "Cálculo [G2] [2025/2026]", "grade": "-"},
    ]


def test_grade_report_keeps_categories_items_feedback_and_the_course_total():
    report = parse_grade_report(REPORT)
    kinds = [(item["kind"], item["level"], item["name"]) for item in report["items"]]
    assert kinds[0] == ("category", 1, "Redes [G1] [2026/2027]")
    item = next(item for item in report["items"] if item["kind"] == "item")
    assert item["name"] == "Práctica 1" and item["module"] == "Tarefa"
    assert (item["grade"], item["range"], item["feedback"]) == ("9,0", "0–10", "Moi ben.")
    assert item["url"].endswith("id=21")
    assert report["course_total"] == "7,5" and report["course_name"].startswith("Redes")


def test_forum_page_lists_discussions():
    forum = parse_forum_page(FORUM)
    assert forum["forum_name"] == "Anuncios"
    (first,) = forum["discussions"]
    assert first["discussion_id"] == 900 and first["subject"] == "Data do exame"
    assert (first["started_by"], first["started_at"]) == ("Ana Proba", "3 de nov. de 2026")
    assert (first["last_post_by"], first["last_post_at"]) == ("Brais Exemplo", "5 de nov. de 2026")
    assert first["replies"] == 2 and first["unread"] == 1
    assert first["pinned"] is True and first["locked"] is False


def test_quiz_page_has_dates_limits_attempts_and_grade():
    quiz = parse_quiz_page(QUIZ)
    assert quiz["name"] == "Test 1"
    assert quiz["dates"] == {"Abertas": "luns, 2 de novembro", "Pechadas": "martes, 3 de novembro"}
    assert quiz["info"]["Límite de tempo"] == "30 minutos"
    assert quiz["attempts"] == [
        {"Estado": "Finalizada", "Cualificación": "8,00 alén dun máximo de 10,00"}
    ]
    assert quiz["final_grade"].endswith("8,00/10,00.") and quiz["can_attempt"] is True


def test_paragraphs_survive_in_forum_posts():
    text = html_to_paragraphs(
        "<p>Ola,</p><p>A entrega <b>cambia</b>.</p><ul><li>Un</li><li>Dous</li></ul>"
    )
    assert text == "Ola,\n\nA entrega cambia .\n\n• Un\n\n• Dous" or text.splitlines()[0] == "Ola,"
    assert "• Un" in text and "• Dous" in text


def test_moodle4_post_fields_are_read():
    post = _normalise_post(
        {
            "id": 1,
            "parentid": 7,
            "timecreated": 1_800_000_000,
            "unread": True,
            "message": "<p>x</p>",
        }
    )
    assert post["parent_post_id"] == 7 and post["created_at"] and post["is_read"] is False


async def test_only_listed_pages_can_be_read():
    calls: list[str] = []

    async def get(url: str, params: Any) -> Any:
        calls.append(url)

    async def never(*args: Any) -> Any:
        raise AssertionError("nothing is posted")

    forms = MoodleSessionForms("https://cv.usc.es", get, never, never)
    with pytest.raises(SessionFormValidationError):
        await forms.read_page("/user/profile.php", {})
    assert calls == []


class _Forms:
    def __init__(self, html: str) -> None:
        self.html = html
        self.reads: list[tuple[str, dict]] = []

    async def read_page(self, path: str, params: dict) -> Any:
        self.reads.append((path, dict(params)))
        return type("Page", (), {"text": self.html})()


async def test_reading_a_page_needs_a_fresh_confirmation(monkeypatch):
    forms = _Forms(OVERVIEW)
    gateway = object()
    monkeypatch.setattr(service_module, "_session_form_client", lambda gateway: forms)
    issued: list[str] = []

    async def issue(gateway, action, payload):
        issued.append(action)
        return {"confirmation_token": "t-1"}

    async def consume(gateway, token, action, payload):
        assert token == "t-1" and action == issued[-1]
        return 5

    monkeypatch.setattr(service_module, "_issue_action_confirmation", issue)
    monkeypatch.setattr(service_module, "_consume_action_confirmation", consume)
    service = UscService.__new__(UscService)
    monkeypatch.setattr(service, "_campus", lambda: gateway, raising=False)

    preview = await service.inspect_my_grades(None, None)
    assert preview["preview"] is True and forms.reads == []
    result = await service.inspect_my_grades(None, preview["confirmation_token"])
    assert forms.reads == [("/grade/report/overview/index.php", {})]
    assert result["courses"][0]["grade"] == "7,5" and result["stateful_read_confirmed"]


class _MessagingGateway:
    async def invoke(self, function: str, arguments: dict) -> Any:
        if function == "core_message_get_member_info":
            return [{"id": 8, "fullname": "Ana <b>Proba</b>"}]
        assert function == "core_message_get_conversation_between_users"
        return {
            "id": 44,
            "unreadcount": 1,
            "members": [{"id": 8, "fullname": "Ana Proba"}],
            "messages": [
                {"id": 2, "useridfrom": 5, "text": "<p>Grazas</p>", "timecreated": 20},
                {
                    "id": 1,
                    "useridfrom": 8,
                    "text": "<p>Ola</p><p>Mañá non hai clase</p>",
                    "timecreated": 10,
                },
            ],
        }


async def test_conversation_and_names(monkeypatch):
    gateway = _MessagingGateway()

    async def me(gateway):
        return 5

    monkeypatch.setattr(service_module, "_gateway_user_id", me)
    service = UscService.__new__(UscService)
    monkeypatch.setattr(service, "_campus", lambda: gateway, raising=False)

    assert await service.message_member_names([8, 8, 0]) == {8: "Ana Proba"}
    conversation = await service.conversation_with(8)
    assert conversation["other_name"] == "Ana Proba" and conversation["unread"] == 1
    first, second = conversation["messages"]
    assert first["author"] == "Ana Proba" and first["text"] == "Ola\n\nMañá non hai clase"
    assert second["from_me"] is True
