from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from pathlib import Path

from platformdirs import user_data_path

from .security import validate_usc_url


def _split_urls(value: str) -> tuple[str, ...]:
    return tuple(url.strip() for url in value.replace("\n", ";").split(";") if url.strip())


@dataclass(frozen=True, slots=True)
class Settings:
    moodle_url: str
    moodle_token: str | None = field(repr=False)
    browser_channel: str
    browser_profile_dir: Path
    exam_sources: tuple[str, ...]
    request_timeout_seconds: float = 30.0
    upload_root: Path | None = None
    max_upload_bytes: int = 50 * 1024 * 1024

    @classmethod
    def from_env(cls) -> Settings:
        data_dir = Path(user_data_path("mcp-usc", appauthor=False, ensure_exists=True))
        profile = Path(os.getenv("USC_BROWSER_PROFILE", data_dir / "browser-profile"))
        token = os.getenv("USC_MOODLE_TOKEN") or None
        token_file = os.getenv("USC_MOODLE_TOKEN_FILE")
        if not token and token_file:
            token = Path(token_file).expanduser().read_text(encoding="utf-8").strip() or None
        upload_root_value = os.getenv("USC_UPLOAD_ROOT", "").strip()
        upload_root = Path(upload_root_value).expanduser().resolve() if upload_root_value else None
        max_upload_bytes = int(os.getenv("USC_MAX_UPLOAD_BYTES", str(50 * 1024 * 1024)))
        if not 1 <= max_upload_bytes <= 100 * 1024 * 1024:
            raise ValueError("USC_MAX_UPLOAD_BYTES debe estar entre 1 y 104857600")
        request_timeout_seconds = float(os.getenv("USC_HTTP_TIMEOUT", "30"))
        if not math.isfinite(request_timeout_seconds) or not 1 <= request_timeout_seconds <= 120:
            raise ValueError("USC_HTTP_TIMEOUT debe estar entre 1 y 120 segundos")

        return cls(
            moodle_url=os.getenv("USC_MOODLE_URL", "https://cv.usc.es").rstrip("/"),
            moodle_token=token,
            browser_channel=os.getenv("USC_BROWSER_CHANNEL", "chromium"),
            browser_profile_dir=profile,
            exam_sources=tuple(
                validate_usc_url(url) for url in _split_urls(os.getenv("USC_EXAM_SOURCES", ""))
            ),
            request_timeout_seconds=request_timeout_seconds,
            upload_root=upload_root,
            max_upload_bytes=max_upload_bytes,
        )
