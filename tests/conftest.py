from __future__ import annotations

from collections.abc import Iterator

import pytest

from mcp_usc import academic_profile
from mcp_usc.campus import clear_identity_caches
from mcp_usc.official_exams import clear_location_cache


@pytest.fixture(autouse=True)
def _isolate_identity_caches() -> Iterator[None]:
    """Moodle identity is cached per process; keep every test independent of the others."""

    clear_identity_caches()
    clear_location_cache()
    yield
    clear_identity_caches()
    clear_location_cache()


@pytest.fixture(autouse=True)
def _isolate_disk_caches(tmp_path_factory: pytest.TempPathFactory, monkeypatch) -> None:
    """Persistent caches live in a throwaway folder, never in the developer's cache dir."""

    monkeypatch.setenv("USC_CACHE_DIR", str(tmp_path_factory.mktemp("cache")))
    monkeypatch.delenv("USC_LOCATION_CACHE", raising=False)


@pytest.fixture(autouse=True)
def _no_personal_profile(tmp_path_factory: pytest.TempPathFactory, monkeypatch) -> None:
    """Never let a developer's own `mcp-usc setup` profile leak into the tests."""

    missing = tmp_path_factory.mktemp("config") / "perfil.json"
    monkeypatch.setattr(academic_profile, "default_profile_path", lambda: missing)
