from __future__ import annotations

import pytest

from mcp_usc.security import UnsafeUrlError
from mcp_usc.settings import Settings


def test_token_is_hidden_from_settings_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("USC_MOODLE_TOKEN", "top-secret-token")

    settings = Settings.from_env()

    assert "top-secret-token" not in repr(settings)


def test_sensitive_exam_source_is_rejected_without_echoing_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "USC_EXAM_SOURCES",
        "https://www.usc.gal/exames?token=top-secret-value",
    )

    with pytest.raises(UnsafeUrlError) as caught:
        Settings.from_env()

    assert "top-secret-value" not in str(caught.value)


@pytest.mark.parametrize("value", ["nan", "inf", "0", "121"])
def test_http_timeout_must_be_finite_and_bounded(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("USC_HTTP_TIMEOUT", value)

    with pytest.raises(ValueError, match="USC_HTTP_TIMEOUT"):
        Settings.from_env()


def test_public_cache_configuration_is_loaded_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("USC_PUBLIC_CACHE_TTL_SECONDS", "42.5")
    monkeypatch.setenv("USC_PUBLIC_CACHE_STALE_IF_ERROR_SECONDS", "90")
    monkeypatch.setenv("USC_PUBLIC_CACHE_MAX_ENTRIES", "17")
    monkeypatch.setenv("USC_PUBLIC_CACHE_MAX_BYTES", "123456")

    settings = Settings.from_env()

    assert settings.public_cache_ttl_seconds == 42.5
    assert settings.public_cache_stale_if_error_seconds == 90
    assert settings.public_cache_max_entries == 17
    assert settings.public_cache_max_total_bytes == 123456


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("USC_PUBLIC_CACHE_TTL_SECONDS", "nan"),
        ("USC_PUBLIC_CACHE_TTL_SECONDS", "86401"),
        ("USC_PUBLIC_CACHE_STALE_IF_ERROR_SECONDS", "604801"),
        ("USC_PUBLIC_CACHE_MAX_ENTRIES", "0"),
        ("USC_PUBLIC_CACHE_MAX_BYTES", "512000001"),
    ],
)
def test_public_cache_configuration_is_bounded(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=name):
        Settings.from_env()
