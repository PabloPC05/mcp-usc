from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

import httpx
import pytest

from mcp_usc.public_http_cache import PublicHttpCache, public_cache_summary
from mcp_usc.study_plans import (
    StudyPlanError,
    StudyPlanSchemaChangedError,
    UscStudyPlanClient,
)

ENDPOINT = "https://www.usc.gal/gl/course/76/study-plan-by-course/20872"
PLAN_HTML = """
<div id="study-plan-by-course">
 <h3 class="at-title"><a href="/gl/estudos/graos/ciencias/grao-matematicas/20252026/a">
  Álxebra
 </a></h3>
 <ul class="academic-subject-specs-list"><li>G1012106</li><li>6 créditos</li></ul>
</div>
"""


def _payload(fragment: str = PLAN_HTML) -> bytes:
    return json.dumps(
        [
            {
                "command": "UpdateAcademicCourse",
                "selector": "study-plan-by-course",
                "value": "Curso 2025/2026",
            },
            {
                "command": "insert",
                "method": "replaceWith",
                "selector": "#study-plan-by-course",
                "data": fragment,
            },
        ]
    ).encode()


@dataclass
class Clock:
    value: float = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


async def test_fresh_hit_expiry_and_conditional_304_are_visible() -> None:
    clock = Clock()
    cache = PublicHttpCache(ttl_seconds=10, clock=clock)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                200,
                content=_payload(),
                headers={
                    "content-type": "application/json",
                    "etag": '"plan-v1"',
                    "last-modified": "Mon, 24 Aug 2026 12:00:00 GMT",
                },
            )
        assert request.headers["If-None-Match"] == '"plan-v1"'
        assert request.headers["If-Modified-Since"] == "Mon, 24 Aug 2026 12:00:00 GMT"
        return httpx.Response(304, headers={"etag": '"plan-v2"'})

    client = UscStudyPlanClient(
        transport=httpx.MockTransport(handler),
        cache=cache,
    )
    first = await client.fetch_study_plan(ENDPOINT, academic_year="2025/2026")
    second = await client.fetch_study_plan(ENDPOINT, academic_year="2025/2026")
    assert first.cache_metadata[0].status == "fresh"
    assert first.cache_metadata[0].cache_hit is False
    assert second.cache_metadata[0].status == "fresh"
    assert second.cache_metadata[0].cache_hit is True
    assert len(requests) == 1

    clock.advance(11)
    third = await client.fetch_study_plan(ENDPOINT, academic_year="2025/2026")
    assert third.cache_metadata[0].status == "revalidated"
    assert third.cache_metadata[0].etag == '"plan-v2"'
    assert len(requests) == 2
    assert all(request.method == "GET" for request in requests)
    assert all("cookie" not in request.headers for request in requests)


async def test_transient_failure_uses_bounded_stale_data_as_degraded() -> None:
    clock = Clock()
    cache = PublicHttpCache(
        ttl_seconds=5,
        stale_if_error_seconds=20,
        clock=clock,
    )
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                200,
                content=_payload(),
                headers={"content-type": "application/json", "etag": '"v1"'},
            )
        return httpx.Response(503)

    client = UscStudyPlanClient(transport=httpx.MockTransport(handler), cache=cache)
    await client.fetch_study_plan(ENDPOINT, academic_year="2025/2026")
    clock.advance(6)
    degraded = await client.fetch_study_plan(ENDPOINT, academic_year="2025/2026")
    metadata = degraded.cache_metadata[0]
    assert metadata.status == "degraded"
    assert metadata.cache_hit is True
    assert metadata.degraded_reason == "HTTP 503 durante revalidación"

    clock.advance(21)
    with pytest.raises(StudyPlanError, match="HTTP 503"):
        await client.fetch_study_plan(ENDPOINT, academic_year="2025/2026")


async def test_schema_change_is_explicit_never_cached_and_keeps_last_valid_entry() -> None:
    clock = Clock()
    cache = PublicHttpCache(ttl_seconds=1, stale_if_error_seconds=30, clock=clock)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                200,
                content=_payload(),
                headers={"content-type": "application/json", "etag": '"valid"'},
            )
        if calls == 2:
            # Valid JSON/media type, but a changed Drupal command schema.
            return httpx.Response(
                200,
                content=json.dumps([{"command": "new-schema"}]).encode(),
                headers={"content-type": "application/json", "etag": '"broken"'},
            )
        raise httpx.ConnectError("offline", request=request)

    client = UscStudyPlanClient(transport=httpx.MockTransport(handler), cache=cache)
    await client.fetch_study_plan(ENDPOINT, academic_year="2025/2026")
    clock.advance(2)
    with pytest.raises(StudyPlanSchemaChangedError, match="confirmó|inserción"):
        await client.fetch_study_plan(ENDPOINT, academic_year="2025/2026")
    assert cache.entry_count == 1

    fallback = await client.fetch_study_plan(ENDPOINT, academic_year="2025/2026")
    assert fallback.subjects[0].code == "G1012106"
    assert fallback.cache_metadata[0].status == "degraded"
    assert fallback.cache_metadata[0].etag == '"valid"'


async def test_initial_failures_are_not_cached() -> None:
    cache = PublicHttpCache()
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                200,
                content=b"[]",
                headers={"content-type": "application/json"},
            )
        return httpx.Response(
            200,
            content=_payload(),
            headers={"content-type": "application/json"},
        )

    client = UscStudyPlanClient(transport=httpx.MockTransport(handler), cache=cache)
    with pytest.raises(StudyPlanSchemaChangedError):
        await client.fetch_study_plan(ENDPOINT, academic_year="2025/2026")
    assert cache.entry_count == 0
    valid = await client.fetch_study_plan(ENDPOINT, academic_year="2025/2026")
    assert valid.subjects[0].code == "G1012106"
    assert calls == 2
    assert cache.entry_count == 1


async def test_server_failures_and_no_store_responses_are_never_cached() -> None:
    failed_cache = PublicHttpCache()
    failed_calls = 0

    def unavailable(_request: httpx.Request) -> httpx.Response:
        nonlocal failed_calls
        failed_calls += 1
        return httpx.Response(503)

    failed_client = UscStudyPlanClient(
        transport=httpx.MockTransport(unavailable), cache=failed_cache
    )
    for _ in range(2):
        with pytest.raises(StudyPlanError, match="HTTP 503"):
            await failed_client.fetch_study_plan(ENDPOINT, academic_year="2025/2026")
    assert failed_calls == 2
    assert failed_cache.entry_count == 0

    no_store_cache = PublicHttpCache()
    no_store_calls = 0

    def no_store(_request: httpx.Request) -> httpx.Response:
        nonlocal no_store_calls
        no_store_calls += 1
        return httpx.Response(
            200,
            content=_payload(),
            headers={
                "content-type": "application/json",
                "cache-control": "private, no-store",
            },
        )

    no_store_client = UscStudyPlanClient(
        transport=httpx.MockTransport(no_store), cache=no_store_cache
    )
    await no_store_client.fetch_study_plan(ENDPOINT, academic_year="2025/2026")
    await no_store_client.fetch_study_plan(ENDPOINT, academic_year="2025/2026")
    assert no_store_calls == 2
    assert no_store_cache.entry_count == 0


async def test_no_cache_is_revalidated_and_private_is_allowed_locally() -> None:
    cache = PublicHttpCache(ttl_seconds=300)
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            content=_payload(),
            headers={
                "content-type": "application/json",
                "cache-control": "private, no-cache, must-revalidate, max-age=0",
            },
        )

    client = UscStudyPlanClient(transport=httpx.MockTransport(handler), cache=cache)
    first = await client.fetch_study_plan(ENDPOINT, academic_year="2025/2026")
    second = await client.fetch_study_plan(ENDPOINT, academic_year="2025/2026")

    assert calls == 2
    assert cache.entry_count == 1
    assert first.cache_metadata[0].ttl_seconds == 0
    assert second.cache_metadata[0].cache_hit is False
    summary = public_cache_summary((*first.cache_metadata, *second.cache_metadata))
    assert summary["status"] == "fresh"
    assert summary["hit_count"] == 0
    assert "etag" not in summary
    assert "etag" not in summary["resources"][0]  # type: ignore[index]


async def test_vary_response_is_not_cached_without_a_variant_aware_key() -> None:
    cache = PublicHttpCache()
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            content=_payload(),
            headers={"content-type": "application/json", "vary": "Accept-Language"},
        )

    client = UscStudyPlanClient(transport=httpx.MockTransport(handler), cache=cache)
    await client.fetch_study_plan(ENDPOINT, academic_year="2025/2026")
    await client.fetch_study_plan(ENDPOINT, academic_year="2025/2026")

    assert calls == 2
    assert cache.entry_count == 0


async def test_response_and_cache_limits_fail_closed() -> None:
    response_cache = PublicHttpCache()
    oversized_calls = 0

    def oversized(_request: httpx.Request) -> httpx.Response:
        nonlocal oversized_calls
        oversized_calls += 1
        return httpx.Response(
            200,
            content=_payload(),
            headers={
                "content-type": "application/json",
                "content-length": "20000001",
            },
        )

    with pytest.raises(StudyPlanError, match="límite"):
        await UscStudyPlanClient(
            transport=httpx.MockTransport(oversized),
            cache=response_cache,
        ).fetch_study_plan(ENDPOINT, academic_year="2025/2026")
    assert oversized_calls == 1
    assert response_cache.entry_count == 0

    bounded_cache = PublicHttpCache(max_entries=2, max_total_bytes=10)
    for key, content in (("a", b"1111"), ("b", b"2222"), ("c", b"3333")):
        candidate = bounded_cache.candidate(
            key=key,
            final_url=key,
            content=content,
            headers={"content-type": "text/plain"},
        )
        bounded_cache.commit(candidate)
    assert bounded_cache.entry_count == 2
    assert bounded_cache.total_bytes == 8
    assert bounded_cache.fresh("a") is None
    assert bounded_cache.fresh("b") is not None
    assert bounded_cache.fresh("c") is not None


async def test_concurrent_identical_queries_are_collapsed_after_validation() -> None:
    cache = PublicHttpCache(ttl_seconds=60)
    calls = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.02)
        return httpx.Response(
            200,
            content=_payload(),
            headers={"content-type": "application/json", "etag": '"one"'},
        )

    client = UscStudyPlanClient(transport=httpx.MockTransport(handler), cache=cache)
    results = await asyncio.gather(
        *(
            client.fetch_study_plan(ENDPOINT, academic_year="2025/2026")
            for _ in range(20)
        )
    )
    assert calls == 1
    assert all(result.subjects[0].code == "G1012106" for result in results)
    assert sum(not result.cache_metadata[0].cache_hit for result in results) == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ttl_seconds", -1),
        ("stale_if_error_seconds", 604_801),
        ("max_entries", 0),
        ("max_total_bytes", 0),
    ],
)
def test_invalid_cache_limits_are_rejected(field: str, value: int) -> None:
    with pytest.raises(ValueError):
        PublicHttpCache(**{field: value})
