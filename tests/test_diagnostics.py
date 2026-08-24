from __future__ import annotations

from pathlib import Path

from mcp_usc.diagnostics import build_diagnostic
from mcp_usc.settings import Settings


class MemoryStore:
    def __init__(self, value: str | None) -> None:
        self.value = value

    def get(self, name: str) -> str | None:
        assert name == "moodle-session"
        return self.value


def _settings(
    tmp_path: Path, *, token: str | None = None, upload_root: Path | None = None
) -> Settings:
    return Settings(
        moodle_url="https://cv.usc.es",
        moodle_token=token,
        browser_channel="chromium",
        browser_profile_dir=tmp_path / "profile",
        exam_sources=(),
        upload_root=upload_root,
    )


def test_diagnostic_reports_public_only_without_network_or_secrets(tmp_path: Path) -> None:
    result = build_diagnostic(
        _settings(tmp_path), MemoryStore(None), browser_auth_available=False
    )

    assert result["status"] == "public_only"
    assert result["campus_contacted"] is False
    assert result["secrets_exposed"] is False
    assert result["authentication"] == {
        "token_configured": False,
        "session_cookie_stored": False,
        "credential_store_readable": True,
        "private_access_configured": False,
        "credentials_validated_online": False,
    }


def test_diagnostic_only_reports_presence_of_token_and_upload_directory(tmp_path: Path) -> None:
    upload_root = tmp_path / "uploads"
    upload_root.mkdir()
    secret = "do-not-return-this-token"

    result = build_diagnostic(
        _settings(tmp_path, token=secret, upload_root=upload_root),
        MemoryStore(None),
        browser_auth_available=True,
    )

    assert result["status"] == "ready"
    assert result["authentication"]["token_configured"] is True
    assert result["features"]["assignment_uploads"]["usable"] is True
    assert secret not in repr(result)
