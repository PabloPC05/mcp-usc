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
