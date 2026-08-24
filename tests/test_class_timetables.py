from __future__ import annotations

import json
from datetime import date

import httpx
import pytest

from mcp_usc.class_timetables import (
    ClassTimetableAcademicYearUnavailable,
    ClassTimetableParseError,
    TimetableWeek,
    UscClassTimetableClient,
    _degree_edition,
    _normalise_degree_name,
    discover_timetable_year_endpoint,
    parse_degree_page_html,
    parse_timetable_html,
    parse_timetable_index_html,
)
from mcp_usc.security import UnsafeUrlError

DEGREE_URL = (
    "https://www.usc.gal/es/estudios/grados/ingenieria-arquitectura/"
    "doble-grado-ingenieria-informatica-matematicas-2a-edicion"
)
CENTER_URL = "https://www.usc.gal/es/centro/facultad-matematicas"
INDEX_URL = f"{CENTER_URL}/horarios/cursos"
TIMETABLE_URL = (
    f"{INDEX_URL}/doble-grado-ingenieria-informatica-matematicas-2o-curso-19955-2"
)
OLD_TIMETABLE_URL = (
    f"{INDEX_URL}/doble-grado-ingenieria-informatica-matematicas-2o-curso-15959-2"
)
YEAR_URL = "https://www.usc.gal/es/course/76/course-detail-controller/5060/19955-2"
SEMESTER_URL = (
    "https://www.usc.gal/es/course/widget/76/"
    "course-detail-controller-call-filter/5060/19955-2/25"
)
WEEK_1_URL = (
    "https://www.usc.gal/es/course/widget/76/"
    "course-detail-controller-week-filter/5060/19955-2/25/37"
)
WEEK_2_URL = (
    "https://www.usc.gal/es/course/widget/76/"
    "course-detail-controller-week-filter/5060/19955-2/25/38"
)

DEGREE_HTML = f"""
<main>
  <h1 class="at-title">Doble Grado en Ingeniería Informática y en Matemáticas (2ª edición)</h1>
  <div class="center-address-info"><a href="{CENTER_URL}">Facultad de Matemáticas</a></div>
</main>
"""

INDEX_HTML = f"""
<main>
  <section class="org-tier">
    <div class="tier-header">
      <h2 class="tier-title">Doble Grado en Ingeniería Informática y en Matemáticas</h2>
    </div>
    <div class="tier-content"><article class="ml-banner is-s is-light">
      <a href="{TIMETABLE_URL}" class="banner-link"><h2 class="at-title">2º Curso</h2></a>
    </article></div>
  </section>
  <section class="org-tier">
    <div class="tier-header">
      <h2 class="tier-title">Doble Grado en Ingeniería Informática y en Matemáticas</h2>
    </div>
    <div class="tier-content"><article class="ml-banner is-s is-light">
      <a href="{OLD_TIMETABLE_URL}" class="banner-link"><h2 class="at-title">2º Curso</h2></a>
    </article></div>
  </section>
</main>
"""


def _schedule_html(subject: str = "Álgebra Lineal", day: str = "Lunes") -> str:
    return f"""
    <div id="course-detail-controller">
      <section class="org-tier"><div class="tier-content filtered-container">
        <section class="org-calendar">
          <article class="calendar-day">
            <header class="calendar-day-header"><h3 class="at-title">{day}</h3></header>
            <ul class="calendar-day-time-group day-time-group-19">
              <li class="expository-group is-group-grupo-cle-01 is-group-grupo-cle-02">
                <article class="ml-academic-subject is-mini">
                  <h3 class="at-title">
                    <a href="/es/plan/16612/course/76/subject/algebra-75969">{subject}</a>
                  </h3>
                  <ul class="academic-subject-specs-list">
                    <li>Grupo /CLE_01<br>Aula 02</li>
                    <li>Grupo /CLE_02<br>Aula 03</li>
                    <li>19:00-20:00</li>
                  </ul>
                </article>
              </li>
            </ul>
          </article>
        </section>
      </div></section>
    </div>
    """


def _payload(*commands: dict[str, object]) -> bytes:
    return json.dumps(list(commands)).encode()


def _insert(selector: str, data: str) -> dict[str, object]:
    return {
        "command": "insert",
        "method": "replaceWith",
        "selector": selector,
        "data": data,
    }


def _semester_filter() -> str:
    return f"""
    <usc-dropdown-filter>
      <a href="{SEMESTER_URL}">1º semestre</a>
      <a href="/es/course/widget/76/course-detail-controller-call-filter/5060/19955-2/26">
        2º semestre
      </a>
    </usc-dropdown-filter>
    """


def _week_filter() -> str:
    return f"""
    <usc-dropdown-filter>
      <a href="{WEEK_1_URL}">Del 08 al 14 de Septiembre</a>
      <a href="{WEEK_2_URL}">Del 15 al 21 de Septiembre</a>
    </usc-dropdown-filter>
    """


def test_degree_page_and_index_keep_exact_official_urls_and_plan_ids() -> None:
    title, centers = parse_degree_page_html(DEGREE_HTML, DEGREE_URL)
    entries = parse_timetable_index_html(INDEX_HTML, INDEX_URL)

    assert title.endswith("(2ª edición)")
    assert centers[0].timetable_index_url == INDEX_URL
    assert [(item.program_id, item.course_number) for item in entries] == [
        (19955, 2),
        (15959, 2),
    ]


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("Grao en Enfermaría (3ªed)", "Grao en Enfermaría"),
        ("Grao en ADE [S]", "Grao en ADE"),
        (
            "Dobre Grao en Educación Infantil e Primaria (S)",
            "Doble Grado de Educación Infantil y Primaria",
        ),
        ("Grao en Mestre en Educación Primaria", "Grao de Mestre de Educación Primaria"),
        ("Dobre Grao en Física e en Química", "Doble Grado de Física y Química"),
    ],
)
def test_degree_name_key_accepts_institutional_variants(left: str, right: str) -> None:
    assert _normalise_degree_name(left) == _normalise_degree_name(right)


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Grao en Enfermaría (3ªed)", 3),
        ("Doble Grado en Informática y Matemáticas (2ª edición)", 2),
        ("Grado en Matemáticas", None),
    ],
)
def test_degree_edition_is_preserved_separately(title: str, expected: int | None) -> None:
    assert _degree_edition(title) == expected


async def test_selected_degree_edition_excludes_other_explicit_plans() -> None:
    index_html = INDEX_HTML.replace(
        "Doble Grado en Ingeniería Informática y en Matemáticas</h2>",
        "Doble Grado en Ingeniería Informática y en Matemáticas (2ª edición)</h2>",
        1,
    ).replace(
        "Doble Grado en Ingeniería Informática y en Matemáticas</h2>",
        "Doble Grado en Ingeniería Informática y en Matemáticas (1ª edición)</h2>",
        1,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == DEGREE_URL:
            return httpx.Response(200, text=DEGREE_HTML)
        if str(request.url) == INDEX_URL:
            return httpx.Response(200, text=index_html)
        raise AssertionError(f"unexpected URL: {request.url}")

    client = UscClassTimetableClient(transport=httpx.MockTransport(handler))
    result = await client.find_degree_timetables(DEGREE_URL, course_number=2)

    assert result["edition"] == 2
    assert result["count"] == 1
    assert result["timetables"][0]["program_id"] == 19955
    assert result["timetables"][0]["edition"] == 2


def test_index_accepts_a_bounded_long_official_slug() -> None:
    slug = "curso-" + "formacion-pedagoxica-" * 10 + "primeiro"
    url = f"{INDEX_URL}/{slug}-21586-1"
    html = f"""
      <main><section class="org-tier">
        <div class="tier-header"><h2 class="tier-title">Grao de Proba</h2></div>
        <div class="tier-content"><article class="ml-banner">
          <a class="banner-link" href="{url}"><h2 class="at-title">1º Curso</h2></a>
        </article></div>
      </section></main>
    """
    assert parse_timetable_index_html(html, INDEX_URL)[0].url == url


def test_discovers_exact_year_and_reports_available_years() -> None:
    html = f"""
      <a href="/es/course/77/course-detail-controller/5060/19955-2">Curso 2026/2027</a>
      <a href="{YEAR_URL}">Curso 2025/2026</a>
    """
    endpoint, years = discover_timetable_year_endpoint(html, TIMETABLE_URL, "2025/2026")
    assert endpoint == YEAR_URL
    assert years == ("2025/2026", "2026/2027")

    with pytest.raises(ClassTimetableAcademicYearUnavailable) as error:
        discover_timetable_year_endpoint(html, TIMETABLE_URL, "2024/2025")
    assert error.value.available == years


@pytest.mark.parametrize(
    "href",
    [
        "https://evil.example/es/course/76/course-detail-controller/5060/19955-2",
        "/es/course/76/course-detail-controller/5060/12389-2",
        "/es/course/76/study-plan-by-course/5060",
    ],
)
def test_year_discovery_rejects_offsite_cross_program_or_wrong_kind_links(href: str) -> None:
    html = f'<a href="{href}">Curso 2025/2026</a>'
    with pytest.raises(ClassTimetableParseError):
        discover_timetable_year_endpoint(html, TIMETABLE_URL, "2025/2026")


def test_schedule_parser_expands_each_explicit_group_and_room() -> None:
    week = TimetableWeek(
        "Del 08 al 14 de Septiembre", date(2025, 9, 8), date(2025, 9, 14), WEEK_1_URL
    )
    sessions = parse_timetable_html(_schedule_html(), WEEK_1_URL, week=week)

    assert len(sessions) == 2
    assert [item.group_code for item in sessions] == ["/CLE_01", "/CLE_02"]
    assert [item.room for item in sessions] == ["Aula 02", "Aula 03"]
    assert sessions[0].date.isoformat() == "2025-09-08"
    assert sessions[0].subject_url == (
        "https://www.usc.gal/es/plan/16612/course/76/subject/algebra-75969"
    )


async def test_discovery_returns_ambiguous_editions_instead_of_choosing_one() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if str(request.url) == DEGREE_URL:
            return httpx.Response(200, text=DEGREE_HTML)
        if str(request.url) == INDEX_URL:
            return httpx.Response(200, text=INDEX_HTML)
        raise AssertionError(f"unexpected URL: {request.url}")

    client = UscClassTimetableClient(transport=httpx.MockTransport(handler))
    result = await client.find_degree_timetables(DEGREE_URL, course_number=2)

    assert result["count"] == 2
    assert all(item["ambiguous_same_title_and_course"] for item in result["timetables"])
    assert result["complete"] is True
    assert all(request.method == "GET" for request in requests)
    assert all("cookie" not in request.headers for request in requests)


async def test_client_selects_requested_week_and_filters_exact_group() -> None:
    requests: list[httpx.Request] = []
    canonical_html = f'<a href="{YEAR_URL}">Curso 2025/2026</a>'
    year_payload = _payload(
        {"command": "UpdateAcademicCourse", "value": "Curso 2025/2026"},
        _insert("#course-detail-controller-call-filter", _semester_filter()),
        _insert("#course-detail-controller", _schedule_html()),
    )
    semester_payload = _payload(
        _insert("#course-detail-controller-week-filter", _week_filter()),
        _insert("#course-detail-controller", _schedule_html()),
    )
    week_payload = _payload(
        _insert("#course-detail-controller", _schedule_html("Álgebra Lineal", "Martes"))
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        url = str(request.url)
        if url == TIMETABLE_URL:
            return httpx.Response(200, text=canonical_html)
        payloads = {
            YEAR_URL: year_payload,
            SEMESTER_URL: semester_payload,
            WEEK_2_URL: week_payload,
        }
        if url in payloads:
            return httpx.Response(
                200,
                content=payloads[url],
                headers={"content-type": "application/json"},
            )
        raise AssertionError(f"unexpected URL: {request.url}")

    client = UscClassTimetableClient(transport=httpx.MockTransport(handler))
    result = await client.fetch_timetable(
        TIMETABLE_URL,
        academic_year="2025/2026",
        semester=1,
        date_in_week="2025-09-16",
        group_codes=["CLE_02"],
    )

    assert result["week"]["start_date"] == "2025-09-15"
    assert result["schedule_endpoint_url"] == WEEK_2_URL
    assert result["available_group_codes"] == ["/CLE_01", "/CLE_02"]
    assert result["count"] == 1
    assert result["sessions"][0] == {
        "date": "2025-09-16",
        "weekday": "Martes",
        "start_time": "19:00",
        "end_time": "20:00",
        "subject_name": "Álgebra Lineal",
        "subject_url": "https://www.usc.gal/es/plan/16612/course/76/subject/algebra-75969",
        "activity_type": "expository",
        "group_code": "/CLE_02",
        "room": "Aula 03",
        "content_is_untrusted": True,
    }
    assert [str(request.url) for request in requests] == [
        TIMETABLE_URL,
        YEAR_URL,
        SEMESTER_URL,
        WEEK_2_URL,
    ]
    assert all(
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        for request in requests[1:]
    )


async def test_selected_degree_requires_a_program_only_when_official_plans_are_ambiguous() -> None:
    requests: list[httpx.Request] = []
    canonical_html = f'<a href="{YEAR_URL}">Curso 2025/2026</a>'
    year_payload = _payload(
        {"command": "UpdateAcademicCourse", "value": "Curso 2025/2026"},
        _insert("#course-detail-controller-call-filter", _semester_filter()),
        _insert("#course-detail-controller", _schedule_html()),
    )
    semester_payload = _payload(
        _insert("#course-detail-controller-week-filter", _week_filter()),
        _insert("#course-detail-controller", _schedule_html()),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        url = str(request.url)
        if url == DEGREE_URL:
            return httpx.Response(200, text=DEGREE_HTML)
        if url == INDEX_URL:
            return httpx.Response(200, text=INDEX_HTML)
        if url == TIMETABLE_URL:
            return httpx.Response(200, text=canonical_html)
        payloads = {YEAR_URL: year_payload, SEMESTER_URL: semester_payload}
        if url in payloads:
            return httpx.Response(
                200,
                content=payloads[url],
                headers={"content-type": "application/json"},
            )
        raise AssertionError(f"unexpected URL: {request.url}")

    client = UscClassTimetableClient(transport=httpx.MockTransport(handler))
    unresolved = await client.fetch_degree_timetable(
        DEGREE_URL, course_number=2, academic_year="2025/2026"
    )

    assert unresolved["status"] == "program_selection_required"
    assert unresolved["available_program_ids"] == [15959, 19955]
    assert {item["program_id"] for item in unresolved["program_options"]} == {
        15959,
        19955,
    }
    assert [str(request.url) for request in requests] == [DEGREE_URL, INDEX_URL]

    requests.clear()
    selected = await client.fetch_degree_timetable(
        DEGREE_URL,
        course_number=2,
        academic_year="2025/2026",
        group_codes=["CLE_02"],
        program_id=19955,
    )

    assert selected["status"] == "ok"
    assert selected["selected_program_id"] == 19955
    assert selected["count"] == 1
    assert selected["sessions"][0]["center_slug"] == "facultad-matematicas"
    assert selected["sessions"][0]["program_id"] == 19955
    assert selected["sources"][0]["timetable_url"] == TIMETABLE_URL


async def test_selected_degree_does_not_report_not_published_when_an_index_failed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == DEGREE_URL:
            return httpx.Response(200, text=DEGREE_HTML)
        if str(request.url) == INDEX_URL:
            return httpx.Response(404, text="not found")
        raise AssertionError(f"unexpected URL: {request.url}")

    client = UscClassTimetableClient(transport=httpx.MockTransport(handler))
    result = await client.fetch_degree_timetable(
        DEGREE_URL, course_number=2, academic_year="2025/2026"
    )

    assert result["status"] == "source_unavailable"
    assert result["complete"] is False
    assert result["count"] == 0
    assert result["discovery"]["issues"]


async def test_client_returns_explicit_no_data_without_guessing_another_center() -> None:
    canonical_html = f'<a href="{YEAR_URL}">Curso 2025/2026</a>'
    no_data_payload = _payload(
        {"command": "UpdateAcademicCourse", "value": "Curso 2025/2026"},
        _insert(
            "#course-detail-controller-call-filter",
            "<usc-dropdown-filter></usc-dropdown-filter>",
        ),
        _insert(
            "#course-detail-controller",
            '<div id="course-detail-controller"><p>No hay datos disponibles.</p></div>',
        ),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == TIMETABLE_URL:
            return httpx.Response(200, text=canonical_html)
        if str(request.url) == YEAR_URL:
            return httpx.Response(
                200,
                content=no_data_payload,
                headers={"content-type": "application/json"},
            )
        raise AssertionError(f"unexpected URL: {request.url}")

    client = UscClassTimetableClient(transport=httpx.MockTransport(handler))
    result = await client.fetch_timetable(
        TIMETABLE_URL, academic_year="2025/2026", semester=1
    )

    assert result["status"] == "no_data"
    assert result["count"] == 0
    assert result["available_semesters"] == []


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/es/centro/facultad/horarios/cursos/grado-1o-curso-1-1",
        f"{TIMETABLE_URL}?token=secret",
        "https://www.usc.gal/es/course/76/course-detail-controller/5060/../../admin",
        "https://www.usc.gal/es/course/76/course-detail-controller/5060/%2e%2e/admin",
    ],
)
async def test_unsafe_timetable_urls_fail_before_network(url: str) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, text="unexpected")

    client = UscClassTimetableClient(transport=httpx.MockTransport(handler))
    with pytest.raises(UnsafeUrlError):
        await client.fetch_timetable(url, academic_year="2025/2026")
    assert calls == 0


def test_schedule_rejects_external_subject_links() -> None:
    week = TimetableWeek(
        "Del 08 al 14 de Septiembre", date(2025, 9, 8), date(2025, 9, 14), WEEK_1_URL
    )
    html = _schedule_html().replace(
        "/es/plan/16612/course/76/subject/algebra-75969", "https://evil.example/subject"
    )
    with pytest.raises((UnsafeUrlError, ClassTimetableParseError)):
        parse_timetable_html(html, WEEK_1_URL, week=week)


def test_schedule_preserves_multiple_official_activity_types() -> None:
    week = TimetableWeek(
        "Del 08 al 14 de Septiembre", date(2025, 9, 8), date(2025, 9, 14), WEEK_1_URL
    )
    html = _schedule_html().replace(
        'class="expository-group ',
        'class="laboratory-group expository-group ',
    )

    sessions = parse_timetable_html(html, WEEK_1_URL, week=week)

    assert {session.activity_type for session in sessions} == {
        "expository+laboratory"
    }
