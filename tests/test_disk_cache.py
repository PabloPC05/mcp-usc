from __future__ import annotations

import json
import os
import stat
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mcp_usc import official_exams
from mcp_usc.degree_catalog import (
    DegreeCatalog,
    DegreeCatalogEntry,
    DegreeScanIssue,
    DegreeSubjectSearch,
    SubjectDegreeLocation,
)
from mcp_usc.disk_cache import DiskJsonCache
from mcp_usc.public_http_cache import PublicHttpCacheMetadata
from mcp_usc.study_plans import StudyPlanSubject


def make_cache(ttl: float = 60.0) -> DiskJsonCache:
    return DiskJsonCache("unit", ttl_seconds=ttl, env_flag="USC_UNIT_TEST_CACHE")


def test_store_then_load_round_trips_and_uses_private_permissions() -> None:
    cache = make_cache()
    key = ["2026/2027", ["G1"], None, True]
    assert cache.load(key) is None
    assert cache.store(key, {"a": [1, 2], "b": "ñ"}) is True
    assert cache.load(key) == {"a": [1, 2], "b": "ñ"}
    assert cache.load(["2026/2027", ["G2"], None, True]) is None
    (file,) = cache.directory.glob("*.json")
    if os.name != "nt":  # Windows has no POSIX modes; the folder is the user's own there
        assert stat.S_IMODE(file.stat().st_mode) == 0o600
        assert stat.S_IMODE(cache.directory.stat().st_mode) == 0o700
    assert not list(cache.directory.glob(".tmp-*"))


def test_entry_expires_after_ttl() -> None:
    cache = make_cache(ttl=60.0)
    cache.store("k", 1)
    (file,) = cache.directory.glob("*.json")
    envelope = json.loads(file.read_text())
    envelope["stored_at"] = time.time() - 61
    file.write_text(json.dumps(envelope))
    assert cache.load("k") is None


@pytest.mark.parametrize(
    "content",
    [b"", b"{not json", b"[]", b'{"format": 99}', b"\xff\xfe\x00", b'{"format": 1}'],
)
def test_corrupt_or_foreign_files_are_misses(content: bytes) -> None:
    cache = make_cache()
    cache.store("k", {"ok": True})
    (file,) = cache.directory.glob("*.json")
    file.write_bytes(content)
    assert cache.load("k") is None
    # A miss must not prevent writing a fresh value afterwards.
    assert cache.store("k", {"ok": 2}) is True
    assert cache.load("k") == {"ok": 2}


def test_file_stored_for_another_key_is_a_miss() -> None:
    cache = make_cache()
    cache.store("one", 1)
    cache.store("two", 2)
    files = {f.name: f for f in cache.directory.glob("*.json")}
    assert len(files) == 2
    first, second = sorted(files.values())
    second.write_bytes(first.read_bytes())  # right name, someone else's content
    assert sum(cache.load(key) is not None for key in ("one", "two")) == 1


def test_disabled_by_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    cache = make_cache()
    cache.store("k", 1)
    monkeypatch.setenv("USC_UNIT_TEST_CACHE", "0")
    assert cache.load("k") is None
    assert cache.store("k2", 2) is False
    monkeypatch.delenv("USC_UNIT_TEST_CACHE")
    assert cache.load("k2") is None
    assert cache.load("k") == 1


def test_unwritable_directory_is_swallowed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setenv("USC_CACHE_DIR", str(blocker / "sub"))
    cache = make_cache()
    assert cache.store("k", 1) is False
    assert cache.load("k") is None


def test_expired_entries_are_pruned_on_store() -> None:
    cache = make_cache(ttl=60.0)
    cache.store("old", 1)
    (old,) = cache.directory.glob("*.json")
    past = time.time() - 120
    os.utime(old, (past, past))
    cache.store("new", 2)
    assert len(list(cache.directory.glob("*.json"))) == 1
    assert cache.load("new") == 2


# --- persisted subject locations -------------------------------------------------------

DEGREE = DegreeCatalogEntry(
    name="Grao en X", url="https://www.usc.gal/gl/estudos/graos/x/grao-x", area_slug="x", campus="S"
)


def _metadata(status: str = "fresh") -> PublicHttpCacheMetadata:
    now = datetime.now(UTC).isoformat()
    return PublicHttpCacheMetadata(
        status=status,  # type: ignore[arg-type]
        requested_url=DEGREE.url,
        final_url=DEGREE.url,
        cache_hit=False,
        fetched_at=now,
        validated_at=now,
        age_seconds=0.0,
        ttl_seconds=300.0,
        media_type="text/html",
        etag='"abc"',
    )


def _search(codes: list[str], *, status: str = "fresh", error: bool = False) -> DegreeSubjectSearch:
    locations = tuple(
        SubjectDegreeLocation(
            degree=DEGREE,
            plan_endpoint_url="https://www.usc.gal/gl/plan/1",
            subject=StudyPlanSubject(
                code=code,
                name=f"Materia {code}",
                sheet_url=f"https://www.usc.gal/gl/materia/{code}",
                alternate_sheet_urls=("https://www.usc.gal/gl/materia/alt",),
                course_number=2,
                semester=None,
            ),
        )
        for code in codes
    )
    issues = (
        (DegreeScanIssue(DEGREE, "http_error", "HTTP 500", True),)
        if error
        else (DegreeScanIssue(DEGREE, "http_not_found", "404", False),)
    )
    return DegreeSubjectSearch(
        academic_year="2026/2027",
        source_url="https://www.usc.gal/gl/estudos/graos",
        requested_codes=tuple(codes),
        locations=locations,
        degree_issues=issues,
        scanned_degrees=0,
        catalog_degrees=1,
        total_catalog_degrees=1,
        selected_area_slugs=(),
        selected_degree_urls=(),
        cache_metadata=(_metadata(status),),
        fetched_at=datetime.now(UTC).isoformat(),
    )


@dataclass
class Crawl:
    catalog: int = 0
    locate: int = 0
    status: str = "fresh"
    error: bool = False


@pytest.fixture
def crawl(monkeypatch: pytest.MonkeyPatch) -> Crawl:
    calls = Crawl()

    class FakeCatalogClient:
        def __init__(self, **_: object) -> None: ...

        async def fetch_catalog(self, _url: str) -> DegreeCatalog:
            calls.catalog += 1
            return DegreeCatalog(
                source_url="https://www.usc.gal/gl/estudos/graos", degrees=(DEGREE,)
            )

    class FakePlanClient:
        def __init__(self, **_: object) -> None: ...

    async def fake_locate(codes: list[str], year: str, **_: object) -> DegreeSubjectSearch:
        calls.locate += 1
        return _search(list(codes), status=calls.status, error=calls.error)

    monkeypatch.setattr(official_exams, "UscDegreeCatalogClient", FakeCatalogClient)
    monkeypatch.setattr(official_exams, "UscStudyPlanClient", FakePlanClient)
    monkeypatch.setattr(official_exams, "locate_subject_codes", fake_locate)
    return calls


async def _locate(codes: list[str]) -> official_exams.StudentSubjectSearch:
    return await official_exams.locate_student_subjects(
        codes, "2026/2027", degree_urls=[DEGREE.url]
    )


async def test_location_result_survives_a_new_process(crawl: Crawl) -> None:
    first = await _locate(["G1", "G2"])
    assert crawl.locate == 1
    official_exams.clear_location_cache()  # what a fresh process would not have
    second = await _locate(["G1", "G2"])
    assert crawl.locate == 1 and crawl.catalog == 1  # served from disk, no crawl
    assert second.locations == first.locations
    assert second.scope == first.scope and second.widened == first.widened
    assert second.searches[0].requested_codes == ("G1", "G2")
    assert [i.category for i in second.searches[0].degree_issues] == ["http_not_found"]
    assert second.for_code("G2")[0].subject.alternate_sheet_urls == (
        "https://www.usc.gal/gl/materia/alt",
    )
    metadata = second.searches[0].cache_metadata[0]
    assert metadata.cache_hit is True and metadata.status == "fresh"
    assert metadata.etag == '"abc"'
    # A different question is a different entry.
    await _locate(["G1"])
    assert crawl.locate == 2


async def test_incomplete_or_degraded_results_are_not_persisted(
    crawl: Crawl,
) -> None:
    crawl.error = True
    await _locate(["G1"])
    crawl.error = False
    crawl.status = "degraded"
    official_exams.clear_location_cache()
    await _locate(["G1"])
    official_exams.clear_location_cache()
    await _locate(["G1"])
    assert crawl.locate == 3  # nothing was reused


async def test_location_disk_cache_can_be_disabled(
    crawl: Crawl, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("USC_LOCATION_CACHE", "0")
    await _locate(["G1"])
    official_exams.clear_location_cache()
    await _locate(["G1"])
    assert crawl.locate == 2


async def test_corrupt_location_file_is_recomputed(crawl: Crawl) -> None:
    await _locate(["G1"])
    (file,) = official_exams._LOCATION_DISK_CACHE.directory.glob("*.json")
    envelope = json.loads(file.read_text())
    envelope["payload"]["locations"][0]["subject"].pop("code")
    file.write_text(json.dumps(envelope))
    official_exams.clear_location_cache()
    result = await _locate(["G1"])
    assert crawl.locate == 2
    assert result.locations[0].subject.code == "G1"


async def test_injected_clients_bypass_the_disk_cache(
    crawl: Crawl, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Catalog:
        async def fetch_catalog(self, _url: str) -> DegreeCatalog:
            return DegreeCatalog(
                source_url="https://www.usc.gal/gl/estudos/graos", degrees=(DEGREE,)
            )

    for _ in range(2):
        official_exams.clear_location_cache()
        await official_exams.locate_student_subjects(
            ["G1"],
            "2026/2027",
            degree_urls=[DEGREE.url],
            catalog_client=Catalog(),  # type: ignore[arg-type]
            study_plan_client=object(),  # type: ignore[arg-type]
        )
    assert crawl.locate == 2
    assert not list(official_exams._LOCATION_DISK_CACHE.directory.glob("*.json"))


# --- persisted degree timetable discovery ----------------------------------------------


def _discovery(*, complete: bool = True, status: str = "fresh") -> dict[str, object]:
    return {
        "degree_name": "Grao en X",
        "degree_url": DEGREE.url,
        "course_number": 2,
        "timetables": [{"program_id": 1, "timetable_url": "https://www.usc.gal/gl/t"}],
        "count": 1,
        "issues": [] if complete else [{"message": "boom"}],
        "complete": complete,
        "cache": {
            "status": status,
            "hit": False,
            "hit_count": 0,
            "resource_count": 3,
            "status_counts": {"fresh": 3, "revalidated": 0, "degraded": 0},
            "fetched_at": "2026-01-01T00:00:00+00:00",
            "validated_at": "2026-01-01T00:00:00+00:00",
            "age_seconds": 0.0,
            "ttl_seconds": 300.0,
        },
        "content_is_untrusted": True,
    }


def _timetable_client(monkeypatch: pytest.MonkeyPatch, outcome: dict[str, object], **kwargs):
    from mcp_usc.class_timetables import UscClassTimetableClient

    client = UscClassTimetableClient(**kwargs)
    calls = {"live": 0}

    async def live(degree_url: str, course_number: int | None) -> dict[str, object]:
        calls["live"] += 1
        return json.loads(json.dumps(outcome))

    monkeypatch.setattr(client, "_discover_degree_timetables", live)
    return client, calls


async def test_timetable_discovery_is_reused_across_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, calls = _timetable_client(monkeypatch, _discovery())
    first = await client.find_degree_timetables(DEGREE.url, course_number=2)
    assert calls["live"] == 1 and first["cache"]["hit"] is False

    other, other_calls = _timetable_client(monkeypatch, _discovery())  # "new process"
    second = await other.find_degree_timetables(DEGREE.url, course_number=2)
    assert other_calls["live"] == 0
    assert second["timetables"] == first["timetables"] and list(second) == list(first)
    assert second["cache"]["hit"] is True
    assert second["cache"]["hit_count"] == 3
    assert second["cache"]["status_counts"] == {"fresh": 3, "revalidated": 0, "degraded": 0}
    assert second["cache"]["age_seconds"] > 0
    # Another course is another entry.
    await other.find_degree_timetables(DEGREE.url, course_number=3)
    assert other_calls["live"] == 1


async def test_timetable_discovery_expires_and_skips_bad_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mcp_usc import class_timetables

    for bad in (_discovery(complete=False), _discovery(status="degraded")):
        client, calls = _timetable_client(monkeypatch, bad)
        await client.find_degree_timetables(DEGREE.url, course_number=2)
        await client.find_degree_timetables(DEGREE.url, course_number=2)
        assert calls["live"] == 2

    client, calls = _timetable_client(monkeypatch, _discovery())
    await client.find_degree_timetables(DEGREE.url, course_number=2)
    (file,) = class_timetables._DISCOVERY_DISK_CACHE.directory.glob("*.json")
    envelope = json.loads(file.read_text())
    envelope["stored_at"] = time.time() - 13 * 3_600
    file.write_text(json.dumps(envelope))
    await client.find_degree_timetables(DEGREE.url, course_number=2)
    assert calls["live"] == 2  # the first call and the one after expiry


async def test_timetable_discovery_disabled_or_injected_transport_never_uses_disk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    from mcp_usc import class_timetables

    client, calls = _timetable_client(
        monkeypatch, _discovery(), transport=httpx.MockTransport(lambda request: None)
    )
    await client.find_degree_timetables(DEGREE.url, course_number=2)
    await client.find_degree_timetables(DEGREE.url, course_number=2)
    assert calls["live"] == 2
    assert not list(class_timetables._DISCOVERY_DISK_CACHE.directory.glob("*.json"))

    monkeypatch.setenv("USC_TIMETABLE_DISCOVERY_CACHE", "0")
    client, calls = _timetable_client(monkeypatch, _discovery())
    await client.find_degree_timetables(DEGREE.url, course_number=2)
    await client.find_degree_timetables(DEGREE.url, course_number=2)
    assert calls["live"] == 2


async def test_unreadable_discovery_payload_falls_back_to_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mcp_usc import class_timetables

    client, calls = _timetable_client(monkeypatch, _discovery())
    await client.find_degree_timetables(DEGREE.url, course_number=2)
    (file,) = class_timetables._DISCOVERY_DISK_CACHE.directory.glob("*.json")
    envelope = json.loads(file.read_text())
    envelope["payload"]["cache"]["validated_at"] = "garbage"
    file.write_text(json.dumps(envelope))
    await client.find_degree_timetables(DEGREE.url, course_number=2)
    assert calls["live"] == 2
