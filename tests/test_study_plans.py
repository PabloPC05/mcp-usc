# The realistic HTML fixture intentionally keeps each Drupal element on one line.
# ruff: noqa: E501

from __future__ import annotations

import json

import httpx
import pytest

from mcp_usc.security import UnsafeUrlError
from mcp_usc.study_plans import (
    StudyPlanAcademicYearUnavailable,
    StudyPlanHttpError,
    StudyPlanParseError,
    StudyPlanSchemaChangedError,
    UscStudyPlanClient,
    discover_study_plan_endpoint,
    extract_study_plan_insert,
    parse_study_plan_html,
    parse_study_plan_sheet_html,
)

CANONICAL = "https://www.usc.gal/gl/estudos/graos/ciencias/grao-matematicas"
ENDPOINT = "https://www.usc.gal/gl/course/76/study-plan-by-course/20872"

PLAN_HTML = """
<div id="study-plan-by-course">
 <usc-accordion class="school-year school-year-1">
  <usc-accordion-item><template v-slot:header>1ro curso</template>
   <h3 class="at-title"><a href="/gl/estudos/graos/ciencias/grao-matematicas/20252026/alxebra-1">Álxebra</a></h3> <!-- noqa: E501 -->
   <ul class="academic-subject-specs-list"><li>G1012106</li><li>Formación básica</li><li>6 créditos</li></ul> <!-- noqa: E501 -->
   <h3 class="at-title"><a href="/gl/estudos/graos/ciencias/grao-matematicas/20252026/calculo-1">Cálculo 1</a></h3> <!-- noqa: E501 -->
   <ul class="academic-subject-specs-list"><li>G1012107</li><li>Formación básica</li><li>6 créditos</li></ul> <!-- noqa: E501 -->
  </usc-accordion-item>
 </usc-accordion>
</div>
"""


def payload(fragment: str = PLAN_HTML, year: str = "2025/2026") -> bytes:
    return json.dumps(
        [
            {
                "command": "UpdateAcademicCourse",
                "selector": "study-plan-by-course",
                "value": f"Curso {year}",
            },
            {
                "command": "insert",
                "method": "replaceWith",
                "selector": "#study-plan-by-course",
                "data": fragment,
            },
        ]
    ).encode()


def test_discovers_exact_year_and_rejects_ambiguous_links() -> None:
    html = (
        '<h1>Matemáticas</h1>'
        '<a href="/gl/course/77/study-plan-by-course/20872">Curso 2026/2027</a>'
        '<a href="/gl/course/76/study-plan-by-module/20872">Curso 2025/2026</a>'
        f'<a href="{ENDPOINT}">Curso 2025/2026</a>'
    )
    assert discover_study_plan_endpoint(html, CANONICAL, "2025/2026") == ENDPOINT
    with pytest.raises(StudyPlanParseError):
        discover_study_plan_endpoint(
            html + '<a href="/gl/course/76/study-plan-by-course/20873">Curso 2025/2026</a>',
            CANONICAL,
            "2025/2026",
        )


def test_discovers_a_year_link_rendered_inside_template() -> None:
    html = f"<template><a href='{ENDPOINT}'>Curso 2025/2026</a></template>"

    assert discover_study_plan_endpoint(html, CANONICAL, "2025/2026") == ENDPOINT


def test_by_course_with_unrecognised_year_label_is_schema_change() -> None:
    html = f'<h1>Matemáticas</h1><a href="{ENDPOINT}">Curso académico: 2026/2027</a>'

    with pytest.raises(StudyPlanParseError, match="etiqueta.*no reconocida"):
        discover_study_plan_endpoint(html, CANONICAL, "2026/2027")


def test_unavailable_year_requires_exhaustive_recognised_year_labels() -> None:
    html = (
        f'<a href="{ENDPOINT}">Curso 2025/2026</a>'
        '<a href="/gl/course/77/study-plan-by-course/20873">'
        "Curso académico: 2026/2027</a>"
    )

    with pytest.raises(StudyPlanParseError, match="etiqueta.*no reconocida"):
        discover_study_plan_endpoint(html, CANONICAL, "2027/2028")


def test_unavailable_year_exception_rejects_absence_without_positive_evidence() -> None:
    with pytest.raises(ValueError, match="evidencia"):
        StudyPlanAcademicYearUnavailable("2025/2026", ())


def test_missing_academic_year_is_not_misclassified_as_schema_change() -> None:
    html = '<a href="/gl/course/77/study-plan-by-course/20872">Curso 2026/2027</a>'

    with pytest.raises(StudyPlanAcademicYearUnavailable) as captured:
        discover_study_plan_endpoint(html, CANONICAL, "2025/2026")
    assert captured.value.academic_year == "2025/2026"


def test_plan_parser_keeps_code_bound_to_adjacent_title_link() -> None:
    subjects = parse_study_plan_html(PLAN_HTML, ENDPOINT, academic_year="2025/2026")
    assert [(item.code, item.name) for item in subjects] == [
        ("G1012106", "Álxebra"),
        ("G1012107", "Cálculo 1"),
    ]
    assert subjects[0].sheet_url.endswith("/alxebra-1")


def test_parser_fails_closed_on_duplicates_and_schema_changes() -> None:
    duplicate = PLAN_HTML.replace("G1012107", "G1012106")
    with pytest.raises(StudyPlanParseError, match="contradictorios"):
        parse_study_plan_html(duplicate, ENDPOINT)
    changed = PLAN_HTML.replace("academic-subject-specs-list", "subject-specs")
    with pytest.raises(StudyPlanParseError):
        parse_study_plan_html(changed, ENDPOINT)
    bad_code = PLAN_HTML.replace("G1012106", "G101206")
    with pytest.raises(StudyPlanParseError):
        parse_study_plan_html(bad_code, ENDPOINT)


def test_parser_merges_same_subject_across_itineraries_and_accepts_variant_codes() -> None:
    repeated = PLAN_HTML.replace("Cálculo 1", "Álxebra").replace(
        "G1012107", "G1012106"
    )
    subjects = parse_study_plan_html(repeated, ENDPOINT)

    assert len(subjects) == 1
    assert len(subjects[0].sheet_urls) == 2

    variant = PLAN_HTML.replace("G1012106", "G1012106A")
    assert parse_study_plan_html(variant, ENDPOINT)[0].code == "G1012106A"


def test_study_plan_sheet_round_trips_variant_code() -> None:
    url = f"{CANONICAL}/20252026/materia-variante"
    html = "<h1>Materia variante</h1><dl><dt>Código</dt><dd>g1012106b</dd></dl>"

    assert parse_study_plan_sheet_html(html, url).code == "G1012106B"


def test_ajax_insert_requires_replace_with_method() -> None:
    data = json.loads(payload())
    next(command for command in data if command.get("command") == "insert").pop("method")

    with pytest.raises(StudyPlanParseError, match="inserción"):
        extract_study_plan_insert(json.dumps(data))


async def test_client_is_get_only_json_strict_and_does_not_send_cookies() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if str(request.url) == CANONICAL:
            return httpx.Response(200, text=f'<a href="{ENDPOINT}">Curso 2025/2026</a>')
        return httpx.Response(200, content=payload(), headers={"content-type": "application/json"})

    client = UscStudyPlanClient(transport=httpx.MockTransport(handler))
    result = await client.fetch_study_plan(CANONICAL, academic_year="2025/2026")
    assert result.academic_year == "2025/2026"
    assert result.subjects[0].code == "G1012106"
    assert all(request.method == "GET" for request in requests)
    assert all("cookie" not in request.headers for request in requests)
    assert requests[1].headers["X-Requested-With"] == "XMLHttpRequest"


async def test_client_rejects_year_mismatch_and_external_redirect() -> None:
    def mismatch(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=payload(year="2026/2027"), headers={"content-type": "application/json"}
        )

    with pytest.raises(StudyPlanParseError, match="no coincide"):
        await UscStudyPlanClient(transport=httpx.MockTransport(mismatch)).fetch_study_plan(
            ENDPOINT, academic_year="2025/2026"
        )

    def redirect(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://evil.example/steal"})

    with pytest.raises(UnsafeUrlError):
        await UscStudyPlanClient(transport=httpx.MockTransport(redirect)).fetch_study_plan(ENDPOINT)


async def test_client_exposes_http_status_without_text_classification() -> None:
    def forbidden(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403)

    with pytest.raises(StudyPlanHttpError) as captured:
        await UscStudyPlanClient(
            transport=httpx.MockTransport(forbidden)
        ).fetch_study_plan(ENDPOINT)
    assert captured.value.status_code == 403


async def test_client_requires_drupal_to_confirm_requested_year() -> None:
    data = [command for command in json.loads(payload()) if command["command"] != "UpdateAcademicCourse"]

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=json.dumps(data).encode(),
            headers={"content-type": "application/json"},
        )

    with pytest.raises(StudyPlanParseError, match="confirmó"):
        await UscStudyPlanClient(transport=httpx.MockTransport(handler)).fetch_study_plan(
            ENDPOINT, academic_year="2025/2026"
        )


async def test_unoffered_year_is_distinct_from_schema_change_and_page_is_cached() -> None:
    requests: list[httpx.Request] = []
    other_year = "https://www.usc.gal/gl/course/77/study-plan-by-course/20872"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            text=(
                "<h1>Grao en Matemáticas</h1>"
                f'<a href="{other_year}">Curso 2026/2027</a>'
            ),
            headers={"content-type": "text/html"},
        )

    client = UscStudyPlanClient(transport=httpx.MockTransport(handler))
    for _ in range(2):
        with pytest.raises(StudyPlanAcademicYearUnavailable) as exc_info:
            await client.fetch_study_plan(CANONICAL, academic_year="2025/2026")
        assert exc_info.value.academic_year == "2025/2026"
        assert exc_info.value.available_academic_years == ("2026/2027",)
    assert len(requests) == 1


async def test_page_without_any_plan_endpoint_is_a_schema_change() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="<h1>Grao sen estrutura de plans</h1>",
            headers={"content-type": "text/html"},
        )

    with pytest.raises(StudyPlanSchemaChangedError, match="endpoints by-course"):
        await UscStudyPlanClient(
            transport=httpx.MockTransport(handler)
        ).fetch_study_plan(CANONICAL, academic_year="2025/2026")


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/gl/course/76/study-plan-by-course/20872",
        "https://evil.usc.gal/gl/course/76/study-plan-by-course/20872",
        ENDPOINT + "?token=secret",
        "https://www.usc.gal/gl/course/76/study-plan-by-course/../../admin",
        "https://www.usc.gal/gl/course/76/study-plan-by-course/%2e%2e/admin",
    ],
)
async def test_ssrf_and_unsafe_paths_are_rejected_before_http(url: str) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=payload(), headers={"content-type": "application/json"})

    with pytest.raises(UnsafeUrlError):
        await UscStudyPlanClient(transport=httpx.MockTransport(handler)).fetch_study_plan(url)
    assert calls == 0
