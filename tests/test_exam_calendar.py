from __future__ import annotations

import json

import httpx
import pytest

from mcp_usc.exam_calendar import (
    ExamCalendarParseError,
    UscExamCalendarClient,
    discover_calendar_endpoint,
    extract_calendar_insert,
    parse_exam_calendar_html,
    parse_subject_sheet_html,
)
from mcp_usc.security import UnsafeUrlError

CANONICAL_URL = "https://www.usc.gal/gl/centro/facultade-matematicas/calendarios/convocatorias"
AJAX_URL = "https://www.usc.gal/gl/course/76/schedules-exams-calendar/5060"
SUBJECT_URL = (
    "https://www.usc.gal/gl/estudos/graos/enxenaria-arquitectura/"
    "dobre-grao-enxenaria-informatica-matematicas-0/20252026/"
    "calculo-numerico-variable-20874-19957-11-109205"
)

CALENDAR_HTML = """
<div id="schedules-exams-calendar">
  <usc-accordion>
    <usc-accordion-item class="subject-class is-type-15959">
      <template v-slot:header>Cálculo numérico nunha variable</template>
      <table>
        <thead><tr>
          <th>Convocatoria</th><th>Oportunidade</th><th>Data</th>
          <th>Aula</th><th>Grupo</th>
        </tr></thead>
        <tbody>
          <tr><td>1º semestre</td><td>1ª Oportunidade</td>
              <td>19.12.2025 - 16:00</td><td>Aula 06</td><td>Grupo /CLE_01</td></tr>
        </tbody>
      </table>
    </usc-accordion-item>
    <usc-accordion-item class="subject-class is-type-19955">
      <template v-slot:header> Cálculo numérico nunha variable </template>
      <table>
        <thead><tr>
          <th>Convocatoria</th><th>Oportunidade</th><th>Data</th>
          <th>Aula</th><th>Grupo</th>
        </tr></thead>
        <tbody>
          <tr>
            <td rowspan="4">2º semestre</td>
            <td rowspan="2">1ª Oportunidade</td>
            <td rowspan="2">28.05.2026 - 10:00</td>
            <td>Aula 06</td><td>Grupo /CLE_01</td>
          </tr>
          <tr><td>Aula 07</td><td>Grupo /CLE_02</td></tr>
          <tr>
            <td rowspan="2">2ª Oportunidade</td>
            <td>06.07.2026 - 10:00</td><td>Aula 06</td><td>Grupo /CLE_01</td>
          </tr>
          <tr>
            <td>06.07.2026 - 16:00</td><td>Aula de informática 2</td>
            <td>Grupo /CLE_01</td>
          </tr>
        </tbody>
      </table>
    </usc-accordion-item>
  </usc-accordion>
</div>
"""


def _ajax_payload(calendar_html: str = CALENDAR_HTML) -> bytes:
    return json.dumps(
        [
            {"command": "UpdateAcademicCourse", "value": "Curso 2025/2026"},
            {
                "command": "insert",
                "method": "replaceWith",
                "selector": "#schedules-exams-calendar",
                "data": calendar_html,
            },
        ]
    ).encode()


def test_discovers_only_the_exact_academic_year_link() -> None:
    html = f"""
      <a href="/gl/course/77/schedules-exams-calendar/5060">Curso 2026/2027</a>
      <a href="{AJAX_URL}">Curso 2025/2026</a>
    """
    assert discover_calendar_endpoint(html, CANONICAL_URL, "2025/2026") == AJAX_URL


def test_rejects_a_discovered_non_usc_or_non_calendar_link() -> None:
    html = '<a href="https://attacker.example/calendar">Curso 2025/2026</a>'
    with pytest.raises(UnsafeUrlError):
        discover_calendar_endpoint(html, CANONICAL_URL, "2025/2026")


def test_parser_expands_rowspans_consolidates_rooms_and_filters_exactly() -> None:
    subjects = parse_exam_calendar_html(
        CALENDAR_HTML,
        plan_id="is-type-19955",
        subject_name="Cálculo numérico nunha variable",
    )
    assert len(subjects) == 1
    assert subjects[0].plan_id == 19955
    assert [call.date_time for call in subjects[0].calls] == [
        "28.05.2026 - 10:00",
        "06.07.2026 - 10:00",
        "06.07.2026 - 16:00",
    ]
    first = subjects[0].calls[0]
    assert first.semester == "2º semestre"
    assert first.opportunity == "1ª Oportunidade"
    assert first.rooms == ("Aula 06", "Aula 07")
    assert first.groups == ("Grupo /CLE_01", "Grupo /CLE_02")
    assert (
        parse_exam_calendar_html(
            CALENDAR_HTML,
            plan_id="19955",
            subject_name="cálculo numérico nunha variable",
        )
        == ()
    )


def test_extract_insert_fails_closed_on_ambiguous_commands() -> None:
    command = {
        "command": "insert",
        "selector": "#schedules-exams-calendar",
        "data": CALENDAR_HTML,
    }
    with pytest.raises(ExamCalendarParseError):
        extract_calendar_insert(json.dumps([command, command]))


async def test_client_discovers_endpoint_uses_ajax_header_and_returns_filtered_data() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if str(request.url) == CANONICAL_URL:
            return httpx.Response(
                200,
                text=('<a href="/gl/course/76/schedules-exams-calendar/5060">Curso 2025/2026</a>'),
            )
        if str(request.url) == AJAX_URL:
            return httpx.Response(
                200, content=_ajax_payload(), headers={"content-type": "application/json"}
            )
        raise AssertionError(f"unexpected URL: {request.url}")

    client = UscExamCalendarClient(transport=httpx.MockTransport(handler))
    calendar = await client.fetch_calendar(
        CANONICAL_URL,
        academic_year="2025/2026",
        plan_id=19955,
        subject_name="Cálculo numérico nunha variable",
    )

    assert calendar.academic_year == "2025/2026"
    assert calendar.endpoint_url == AJAX_URL
    assert len(calendar.subjects) == 1
    assert requests[0].headers.get("X-Requested-With") is None
    assert requests[1].headers["X-Requested-With"] == "XMLHttpRequest"
    assert all(request.method == "GET" for request in requests)
    assert all("cookie" not in request.headers for request in requests)


async def test_client_accepts_ajax_endpoint_without_canonical_page() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200, content=_ajax_payload(), headers={"content-type": "application/json"}
        )

    client = UscExamCalendarClient(transport=httpx.MockTransport(handler))
    calendar = await client.fetch_calendar(AJAX_URL, plan_id="15959")
    assert calendar.academic_year == "2025/2026"
    assert calendar.subjects[0].calls[0].date_time == "19.12.2025 - 16:00"
    assert len(requests) == 1


async def test_client_rejects_non_json_ajax_response() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not ajax</html>")

    client = UscExamCalendarClient(transport=httpx.MockTransport(handler))
    with pytest.raises(ExamCalendarParseError, match="application/json"):
        await client.fetch_calendar(AJAX_URL)


async def test_redirect_does_not_propagate_cookies_and_stays_in_resource_kind() -> None:
    redirected = "https://www.usc.gal/gl/course/76/schedules-exams-calendar/5061"
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if str(request.url) == AJAX_URL:
            return httpx.Response(
                302,
                headers={"location": redirected, "set-cookie": "auth=must-not-propagate"},
            )
        return httpx.Response(
            200, content=_ajax_payload(), headers={"content-type": "application/json"}
        )

    client = UscExamCalendarClient(transport=httpx.MockTransport(handler))
    await client.fetch_calendar(AJAX_URL)
    assert len(requests) == 2
    assert "cookie" not in requests[1].headers


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/gl/course/76/schedules-exams-calendar/5060",
        "https://www.usc.gal/gl/course/76/schedules-exams-calendar/5060?token=secret",
        "https://www.usc.gal/gl/course/76/schedules-exams-calendar/../../admin",
        "https://www.usc.gal/gl/course/76/schedules-exams-calendar/%2e%2e/admin",
    ],
)
async def test_client_rejects_unsafe_urls_before_http(url: str) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=_ajax_payload())

    client = UscExamCalendarClient(transport=httpx.MockTransport(handler))
    with pytest.raises(UnsafeUrlError):
        await client.fetch_calendar(url)
    assert calls == 0


def test_subject_sheet_keeps_code_absent_instead_of_inferring_from_name_or_url() -> None:
    html = """
      <h1>Cálculo numérico nunha variable</h1>
      <span class="at-tag is-primary">2025/2026</span>
      <p><b>Convocatoria:</b><br>Segundo semestre</p>
      <table><caption>Exames</caption><tbody>
        <tr><td>06.07.2026 16:00-20:00</td><td>Grupo /CLE_01</td><td>Aula 06</td></tr>
        <tr><td>06.07.2026 16:00-20:00</td><td>Grupo /CLE_01</td>
            <td>Aula de informática 2</td></tr>
      </tbody></table>
    """
    sheet = parse_subject_sheet_html(html, SUBJECT_URL)
    assert sheet.title == "Cálculo numérico nunha variable"
    assert sheet.academic_year == "2025/2026"
    assert sheet.semester == "Segundo semestre"
    assert sheet.code is None
    assert sheet.exam_slots[0].rooms == ("Aula 06", "Aula de informática 2")


def test_subject_sheet_accepts_only_an_explicit_structured_code() -> None:
    html = """
      <h1>Materia</h1><dl><dt>Código</dt><dd>G1012106</dd></dl>
      <table><caption>Exames</caption><tbody>
        <tr><td>28.05.2026 10:00-14:00</td><td>Grupo 1</td><td>Aula 06</td></tr>
      </tbody></table>
    """
    assert parse_subject_sheet_html(html, SUBJECT_URL).code == "G1012106"


def test_subject_sheet_preserves_and_normalises_variant_code() -> None:
    html = """
      <h1>Materia variante</h1><span data-subject-code="g1012106b"></span>
      <table><caption>Exames</caption><tbody>
        <tr><td>28.05.2026 10:00-14:00</td><td>Grupo 1</td><td>Aula 06</td></tr>
      </tbody></table>
    """

    assert parse_subject_sheet_html(html, SUBJECT_URL).code == "G1012106B"
