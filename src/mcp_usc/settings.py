from __future__ import annotations

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

    @classmethod
    def from_env(cls) -> Settings:
        data_dir = Path(user_data_path("mcp-usc", appauthor=False, ensure_exists=True))
        profile = Path(os.getenv("USC_BROWSER_PROFILE", data_dir / "browser-profile"))
        token = os.getenv("USC_MOODLE_TOKEN") or None
        token_file = os.getenv("USC_MOODLE_TOKEN_FILE")
        if not token and token_file:
            token = Path(token_file).expanduser().read_text(encoding="utf-8").strip() or None

        return cls(
            moodle_url=os.getenv("USC_MOODLE_URL", "https://cv.usc.es").rstrip("/"),
            moodle_token=token,
            browser_channel=os.getenv("USC_BROWSER_CHANNEL", "chromium"),
            browser_profile_dir=profile,
            exam_sources=tuple(
                validate_usc_url(url) for url in _split_urls(os.getenv("USC_EXAM_SOURCES", ""))
            ),
            request_timeout_seconds=float(os.getenv("USC_HTTP_TIMEOUT", "30")),
        )
