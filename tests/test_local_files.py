from __future__ import annotations

from pathlib import Path

import pytest

from mcp_usc.local_files import inspect_upload_files
from mcp_usc.settings import Settings


def _settings(root: Path | None, max_bytes: int = 1024) -> Settings:
    return Settings(
        moodle_url="https://cv.usc.es",
        moodle_token=None,
        browser_channel="chromium",
        browser_profile_dir=Path("browser"),
        exam_sources=(),
        upload_root=root,
        max_upload_bytes=max_bytes,
    )


def test_inspects_only_files_inside_configured_root(tmp_path: Path) -> None:
    document = tmp_path / "entrega.txt"
    document.write_text("contenido", encoding="utf-8")

    result = inspect_upload_files(_settings(tmp_path), ["entrega.txt"])

    assert result[0]["relative_path"] == "entrega.txt"
    assert result[0]["size"] == 9
    assert len(result[0]["sha256"]) == 64


def test_rejects_files_outside_root(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")

    with pytest.raises(ValueError, match="dentro de USC_UPLOAD_ROOT"):
        inspect_upload_files(_settings(allowed), [str(outside)])


def test_requires_explicit_upload_root(tmp_path: Path) -> None:
    document = tmp_path / "entrega.txt"
    document.write_text("contenido", encoding="utf-8")

    with pytest.raises(ValueError, match="subida local está desactivada"):
        inspect_upload_files(_settings(None), [str(document)])


def test_rejects_total_over_limit(tmp_path: Path) -> None:
    (tmp_path / "a.bin").write_bytes(b"1234")
    (tmp_path / "b.bin").write_bytes(b"5678")

    with pytest.raises(ValueError, match="límite de subida"):
        inspect_upload_files(_settings(tmp_path, max_bytes=7), ["a.bin", "b.bin"])
