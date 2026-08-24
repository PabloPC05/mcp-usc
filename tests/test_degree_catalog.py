# Realistic, reduced fixtures based on the public Drupal degree catalogue.
# ruff: noqa: E501

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from mcp_usc.degree_catalog import (
    USC_DEGREE_CATALOG_URL,
    DegreeCatalog,
    DegreeCatalogEntry,
    DegreeCatalogParseError,
    UscDegreeCatalogClient,
    locate_subject_codes,
    parse_degree_catalog_html,
)
from mcp_usc.public_http_cache import PublicHttpCache
from mcp_usc.security import UnsafeUrlError
from mcp_usc.service import UscService
from mcp_usc.settings import Settings
from mcp_usc.study_plans import (
    StudyPlan,
    StudyPlanAcademicYearUnavailable,
    StudyPlanError,
    StudyPlanHttpError,
    StudyPlanSubject,
    UscStudyPlanClient,
)

CATALOG_HTML = """
<usc-content-filter-tier :options="[{key: 'is-type-1', name: 'Artes'}]">
 <template v-slot:content>
  <div class="org-modules-container has-banners" id="degrees">
   <article class="is-l ml-banner is-meta is-studies is-type-1 is-3">
    <a href="/gl/estudos/graos/artes-humanidades" class="banner-link">
     <h2 class="at-title">Artes e Humanidades</h2><div class="at-text"><p></p></div>
    </a>
   </article>
   <article class="is-m ml-banner is-studies is-light is-type-2 is-3">
    <a href="/gl/estudos/graos/ciencias/grao-matematicas-0" class="banner-link">
     <div class="banner-content"><h2 class="at-title">Grao en Matemáticas</h2>
      <div class="at-text"><p>Campus de Santiago de Compostela</p></div></div>
    </a>
   </article>
   <article class="is-m ml-banner is-studies is-light is-type-4 is-3">
    <a href="/gl/estudos/graos/ciencias-sociais-xuridicas/grao-dereito" class="banner-link">
     <div class="banner-content"><h2 class="at-title">Grao en Dereito</h2>
      <div class="at-text"><p>Campus de Santiago de Compostela</p></div></div>
    </a>
   </article>
  </div>
 </template>
</usc-content-filter-tier>
"""


def _entry(name: str, url: str, area: str) -> DegreeCatalogEntry:
    return DegreeCatalogEntry(name, url, area, "Campus de Santiago de Compostela")


def test_catalog_parser_keeps_only_degree_cards_and_printed_metadata() -> None:
    catalog = parse_degree_catalog_html(CATALOG_HTML, USC_DEGREE_CATALOG_URL)

    assert len(catalog.degrees) == 2
    assert catalog.degrees[0].name == "Grao en Matemáticas"
    assert catalog.degrees[0].area_slug == "ciencias"
    assert catalog.degrees[0].campus == "Campus de Santiago de Compostela"
    assert catalog.degrees[1].url.endswith("/grao-dereito")


def test_catalog_parser_fails_closed_on_duplicates_and_schema_changes() -> None:
    duplicate = CATALOG_HTML.replace(
        "/gl/estudos/graos/ciencias-sociais-xuridicas/grao-dereito",
        "/gl/estudos/graos/ciencias/grao-matematicas-0",
    )
    with pytest.raises(DegreeCatalogParseError, match="duplicadas"):
        parse_degree_catalog_html(duplicate, USC_DEGREE_CATALOG_URL)

    with pytest.raises(DegreeCatalogParseError, match="componente"):
        parse_degree_catalog_html(
            CATALOG_HTML.replace("usc-content-filter-tier", "div"),
            USC_DEGREE_CATALOG_URL,
        )


@pytest.mark.parametrize(
    "replacement",
    [
        "https://evil.example/gl/estudos/graos/ciencias/grao-matematicas",
        "https://evil.usc.gal/gl/estudos/graos/ciencias/grao-matematicas",
        "/gl/estudos/graos/ciencias/grao-matematicas?token=secret",
        "/gl/estudos/graos/ciencias/../../admin",
        "/gl/estudos/graos/ciencias/%2e%2e",
        "/gl/estudos/graos/ciencias/grao/matematicas",
    ],
)
def test_catalog_rejects_untrusted_degree_links(replacement: str) -> None:
    html = CATALOG_HTML.replace(
        "/gl/estudos/graos/ciencias/grao-matematicas-0", replacement
    )
    with pytest.raises(UnsafeUrlError):
        parse_degree_catalog_html(html, USC_DEGREE_CATALOG_URL)


async def test_catalog_client_is_get_only_cookie_free_and_content_type_strict() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            text=CATALOG_HTML,
            headers={"content-type": "text/html; charset=UTF-8"},
        )

    result = await UscDegreeCatalogClient(
        transport=httpx.MockTransport(handler)
    ).fetch_catalog()
    assert len(result.degrees) == 2
    assert requests[0].method == "GET"
    assert "cookie" not in requests[0].headers

    def bad_type(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"html": CATALOG_HTML})

    with pytest.raises(DegreeCatalogParseError, match="text/html"):
        await UscDegreeCatalogClient(
            transport=httpx.MockTransport(bad_type)
        ).fetch_catalog()


async def test_catalog_client_rejects_external_redirect_before_second_request() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(302, headers={"location": "https://evil.example/steal"})

    with pytest.raises(UnsafeUrlError):
        await UscDegreeCatalogClient(
            transport=httpx.MockTransport(handler)
        ).fetch_catalog()
    assert calls == 1


class FakePlans:
    def __init__(self, plans: dict[str, StudyPlan | Exception]) -> None:
        self.plans = plans
        self.calls: list[tuple[str, str | None]] = []

    async def fetch_study_plan(
        self, url: str, *, academic_year: str | None = None
    ) -> StudyPlan:
        self.calls.append((url, academic_year))
        outcome = self.plans[url]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _plan(url: str, endpoint_id: int, *subjects: StudyPlanSubject) -> StudyPlan:
    return StudyPlan(
        academic_year="2025/2026",
        endpoint_url=f"https://www.usc.gal/gl/course/76/study-plan-by-course/{endpoint_id}",
        source_url=url,
        subjects=subjects,
    )


async def test_locator_finds_exact_code_beyond_curated_double_degree() -> None:
    maths = _entry(
        "Grao en Matemáticas",
        "https://www.usc.gal/gl/estudos/graos/ciencias/grao-matematicas-0",
        "ciencias",
    )
    law = _entry(
        "Grao en Dereito",
        "https://www.usc.gal/gl/estudos/graos/ciencias-sociais-xuridicas/grao-dereito",
        "ciencias-sociais-xuridicas",
    )
    catalog = DegreeCatalog(USC_DEGREE_CATALOG_URL, (maths, law))
    plans = FakePlans(
        {
            maths.url: _plan(
                maths.url,
                20001,
                StudyPlanSubject(
                    "G1012106",
                    "Cálculo numérico nunha variable",
                    f"{maths.url}/20252026/calculo-numerico",
                ),
            ),
            law.url: _plan(
                law.url,
                20002,
                StudyPlanSubject(
                    "G3161331",
                    "Dereito Administrativo II",
                    f"{law.url}/20252026/dereito-administrativo-ii",
                ),
            ),
        }
    )

    result = await locate_subject_codes(
        ["g3161331", "G9999999"],
        "2025/2026",
        catalog=catalog,
        study_plan_client=plans,  # type: ignore[arg-type]
    )
    public = result.public_dict()

    assert result.complete is True
    assert public["subjects"][0]["status"] == "matched"  # type: ignore[index]
    assert public["subjects"][0]["locations"][0]["degree"]["name"] == "Grao en Dereito"  # type: ignore[index]
    assert public["subjects"][1]["status"] == "not_found"  # type: ignore[index]
    assert all(year == "2025/2026" for _, year in plans.calls)


async def test_global_locator_queries_variant_code_with_shared_normaliser() -> None:
    maths = _entry(
        "Grao en Matemáticas",
        "https://www.usc.gal/gl/estudos/graos/ciencias/grao-matematicas-0",
        "ciencias",
    )
    plans = FakePlans(
        {
            maths.url: _plan(
                maths.url,
                20001,
                StudyPlanSubject(
                    "G1012106A",
                    "Materia variante",
                    f"{maths.url}/20252026/materia-variante",
                ),
            )
        }
    )

    result = await locate_subject_codes(
        ["g1012106a"],
        "2025/2026",
        catalog=DegreeCatalog(USC_DEGREE_CATALOG_URL, (maths,)),
        study_plan_client=plans,  # type: ignore[arg-type]
    )

    assert result.requested_codes == ("G1012106A",)
    assert result.public_dict()["subjects"][0]["status"] == "matched"  # type: ignore[index]


async def test_locator_exposes_partial_failure_instead_of_false_not_found() -> None:
    maths = _entry(
        "Grao en Matemáticas",
        "https://www.usc.gal/gl/estudos/graos/ciencias/grao-matematicas-0",
        "ciencias",
    )
    law = _entry(
        "Grao en Dereito",
        "https://www.usc.gal/gl/estudos/graos/ciencias-sociais-xuridicas/grao-dereito",
        "ciencias-sociais-xuridicas",
    )
    plans = FakePlans(
        {
            maths.url: _plan(maths.url, 20001),
            law.url: StudyPlanError("La estructura oficial cambió"),
        }
    )

    result = await locate_subject_codes(
        ["G3161331"],
        "2025/2026",
        catalog=DegreeCatalog(USC_DEGREE_CATALOG_URL, (maths, law)),
        study_plan_client=plans,  # type: ignore[arg-type]
    )
    public = result.public_dict()

    assert result.complete is False
    assert public["subjects"][0]["status"] == "source_changed_or_unavailable"  # type: ignore[index]
    assert public["scanned_degree_count"] == 1
    assert public["degree_errors"] == {law.url: "La estructura oficial cambió"}


async def test_unoffered_academic_year_is_classified_and_does_not_break_completeness() -> None:
    maths = _entry(
        "Grao en Matemáticas",
        "https://www.usc.gal/gl/estudos/graos/ciencias/grao-matematicas-0",
        "ciencias",
    )
    law = _entry(
        "Grao en Dereito",
        "https://www.usc.gal/gl/estudos/graos/ciencias-sociais-xuridicas/grao-dereito",
        "ciencias-sociais-xuridicas",
    )
    plans = FakePlans(
        {
            maths.url: _plan(maths.url, 20001),
            law.url: StudyPlanAcademicYearUnavailable(
                "2025/2026", ("2026/2027",)
            ),
        }
    )

    result = await locate_subject_codes(
        ["G3161331"],
        "2025/2026",
        catalog=DegreeCatalog(USC_DEGREE_CATALOG_URL, (maths, law)),
        study_plan_client=plans,  # type: ignore[arg-type]
    )
    public = result.public_dict()

    assert result.complete is True
    assert public["subjects"][0]["status"] == "not_found"  # type: ignore[index]
    assert public["academic_year_unavailable_count"] == 1
    assert public["degree_issues"][0]["category"] == "academic_year_unavailable"  # type: ignore[index]
    assert public["degree_errors"] == {}


async def test_unrecognised_year_label_is_schema_changed_and_keeps_result_incomplete() -> None:
    maths = _entry(
        "Grao en Matemáticas",
        "https://www.usc.gal/gl/estudos/graos/ciencias/grao-matematicas-0",
        "ciencias",
    )
    endpoint = "https://www.usc.gal/gl/course/77/study-plan-by-course/20546"

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == maths.url
        return httpx.Response(
            200,
            text=(
                "<h1>Grao en Matemáticas</h1>"
                f'<a href="{endpoint}">Curso académico: 2026/2027</a>'
            ),
            headers={"content-type": "text/html"},
        )

    result = await locate_subject_codes(
        ["G1012106"],
        "2026/2027",
        catalog=DegreeCatalog(USC_DEGREE_CATALOG_URL, (maths,)),
        study_plan_client=UscStudyPlanClient(
            transport=httpx.MockTransport(handler)
        ),
    )
    public = result.public_dict()

    assert result.complete is False
    assert public["subjects"][0]["status"] == "source_changed_or_unavailable"  # type: ignore[index]
    assert public["degree_issues"][0]["category"] == "schema_changed"  # type: ignore[index]


async def test_http_forbidden_is_distinct_from_schema_change() -> None:
    maths = _entry(
        "Grao en Matemáticas",
        "https://www.usc.gal/gl/estudos/graos/ciencias/grao-matematicas-0",
        "ciencias",
    )
    plans = FakePlans({maths.url: StudyPlanHttpError(403)})

    result = await locate_subject_codes(
        ["G1012106"],
        "2025/2026",
        catalog=DegreeCatalog(USC_DEGREE_CATALOG_URL, (maths,)),
        study_plan_client=plans,  # type: ignore[arg-type]
    )

    assert result.complete is False
    assert result.public_dict()["degree_issues"][0]["category"] == "http_forbidden"  # type: ignore[index]


async def test_locator_can_scope_by_official_area_and_catalogued_degree_url() -> None:
    maths = _entry(
        "Grao en Matemáticas",
        "https://www.usc.gal/gl/estudos/graos/ciencias/grao-matematicas-0",
        "ciencias",
    )
    law = _entry(
        "Grao en Dereito",
        "https://www.usc.gal/gl/estudos/graos/ciencias-sociais-xuridicas/grao-dereito",
        "ciencias-sociais-xuridicas",
    )
    plans = FakePlans({law.url: _plan(law.url, 20002)})
    catalog = DegreeCatalog(USC_DEGREE_CATALOG_URL, (maths, law))

    result = await locate_subject_codes(
        ["G3161331"],
        "2025/2026",
        area_slugs=["ciencias-sociais-xuridicas"],
        degree_urls=[law.url],
        catalog=catalog,
        study_plan_client=plans,  # type: ignore[arg-type]
    )
    public = result.public_dict()

    assert result.complete is True
    assert public["catalog_degree_count"] == 2
    assert public["selected_degree_count"] == 1
    assert plans.calls == [(law.url, "2025/2026")]
    with pytest.raises(ValueError, match="no está anunciada"):
        await locate_subject_codes(
            ["G3161331"],
            "2025/2026",
            degree_urls=[
                "https://www.usc.gal/gl/estudos/graos/ciencias/grao-inventado"
            ],
            catalog=catalog,
            study_plan_client=plans,  # type: ignore[arg-type]
        )


async def test_service_exposes_catalogue_and_global_code_locator(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    maths = _entry(
        "Grao en Matemáticas",
        "https://www.usc.gal/gl/estudos/graos/ciencias/grao-matematicas-0",
        "ciencias",
    )
    catalog = DegreeCatalog(USC_DEGREE_CATALOG_URL, (maths,))
    captured: dict[str, object] = {}

    class FakeCatalogClient:
        def __init__(self, *, timeout: float, cache: object) -> None:
            captured["catalog_timeout"] = timeout
            captured["catalog_cache"] = cache

        async def fetch_catalog(self, url: str) -> DegreeCatalog:
            captured["catalog_url"] = url
            return catalog

    class FakeSearch:
        def public_dict(self) -> dict[str, object]:
            return {"kind": "global_subject_search"}

    async def fake_locate(
        codes: list[str],
        year: str,
        *,
        area_slugs: list[str] | None,
        degree_urls: list[str] | None,
        concurrency: int,
        timeout: float,
        cache: object,
    ) -> FakeSearch:
        captured.update(
            codes=codes,
            year=year,
            area_slugs=area_slugs,
            degree_urls=degree_urls,
            concurrency=concurrency,
            search_timeout=timeout,
            search_cache=cache,
        )
        return FakeSearch()

    monkeypatch.setattr("mcp_usc.service.UscDegreeCatalogClient", FakeCatalogClient)
    monkeypatch.setattr("mcp_usc.service.locate_subject_codes", fake_locate)
    service = UscService(
        Settings(
            moodle_url="https://cv.usc.es",
            moodle_token=None,
            browser_channel="chromium",
            browser_profile_dir=tmp_path,
            exam_sources=(),
            request_timeout_seconds=17.0,
        )
    )

    listed = await service.list_usc_degrees()
    located = await service.locate_usc_subject_codes(
        ["G1012106"], "2026/2027", concurrency=4
    )

    assert listed["count"] == 1
    assert listed["degrees"][0]["name"] == "Grao en Matemáticas"
    assert located == {"kind": "global_subject_search"}
    assert captured.pop("catalog_cache") is service._public_http_cache
    assert captured.pop("search_cache") is service._public_http_cache
    assert captured == {
        "catalog_timeout": 17.0,
        "catalog_url": USC_DEGREE_CATALOG_URL,
        "codes": ["G1012106"],
        "year": "2026/2027",
        "area_slugs": None,
        "degree_urls": None,
        "concurrency": 4,
        "search_timeout": 17.0,
    }


async def test_separate_mcp_invocations_reuse_the_process_public_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from mcp_usc import server

    cache_hits: list[bool] = []
    maths = _entry(
        "Grao en MatemÃ¡ticas",
        "https://www.usc.gal/gl/estudos/graos/ciencias/grao-matematicas-0",
        "ciencias",
    )

    class CacheExercisingCatalogClient:
        def __init__(self, *, timeout: float, cache: PublicHttpCache) -> None:
            self.cache = cache

        async def fetch_catalog(self, url: str) -> DegreeCatalog:
            response = self.cache.fresh(url)
            if response is None:
                response = self.cache.candidate(
                    key=url,
                    final_url=url,
                    content=b"validated catalogue",
                    headers={
                        "cache-control": "max-age=60",
                        "content-type": "text/html",
                    },
                )
                self.cache.commit(response)
            cache_hits.append(response.metadata.cache_hit)
            return DegreeCatalog(url, (maths,), (response.metadata,))

    monkeypatch.setenv("USC_BROWSER_PROFILE", str(tmp_path / "browser"))
    monkeypatch.setattr(
        "mcp_usc.service.UscDegreeCatalogClient", CacheExercisingCatalogClient
    )
    server._process_public_cache.cache_clear()
    try:
        first = await server.list_usc_degrees()
        second = await server.list_usc_degrees()
    finally:
        server._process_public_cache.cache_clear()

    assert first["cache"]["hit"] is False
    assert second["cache"]["hit"] is True
    assert cache_hits == [False, True]


async def test_locator_injects_one_configured_cache_into_both_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    maths = _entry(
        "Grao en Matemáticas",
        "https://www.usc.gal/gl/estudos/graos/ciencias/grao-matematicas-0",
        "ciencias",
    )
    configured_cache = PublicHttpCache(ttl_seconds=17, max_entries=3)
    captured: dict[str, object] = {}

    class CapturingCatalogClient:
        def __init__(self, *, timeout: float, cache: PublicHttpCache) -> None:
            captured["catalog_cache"] = cache

        async def fetch_catalog(self, _url: str) -> DegreeCatalog:
            return DegreeCatalog(USC_DEGREE_CATALOG_URL, (maths,))

    class CapturingPlanClient:
        def __init__(self, *, timeout: float, cache: PublicHttpCache) -> None:
            captured["plan_cache"] = cache

        async def fetch_study_plan(
            self, url: str, *, academic_year: str | None = None
        ) -> StudyPlan:
            return _plan(url, 20001)

    monkeypatch.setattr(
        "mcp_usc.degree_catalog.UscDegreeCatalogClient", CapturingCatalogClient
    )
    monkeypatch.setattr(
        "mcp_usc.degree_catalog.UscStudyPlanClient", CapturingPlanClient
    )

    result = await locate_subject_codes(
        ["G1012106"],
        "2025/2026",
        cache=configured_cache,
    )

    assert result.complete is True
    assert captured == {
        "catalog_cache": configured_cache,
        "plan_cache": configured_cache,
    }


@pytest.mark.parametrize("concurrency", [0, 17, True, 1.5])
async def test_locator_rejects_unsafe_concurrency(concurrency: object) -> None:
    with pytest.raises(ValueError, match="concurrency"):
        await locate_subject_codes(
            ["G1012106"],
            "2025/2026",
            catalog=DegreeCatalog(
                USC_DEGREE_CATALOG_URL,
                (
                    _entry(
                        "Grao en Matemáticas",
                        "https://www.usc.gal/gl/estudos/graos/ciencias/grao-matematicas-0",
                        "ciencias",
                    ),
                ),
            ),
            concurrency=concurrency,  # type: ignore[arg-type]
        )
