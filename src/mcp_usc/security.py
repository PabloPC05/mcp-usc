from __future__ import annotations

import html
import re
from urllib.parse import parse_qsl, urlparse

from bs4 import BeautifulSoup

ALLOWED_PUBLIC_HOSTS = frozenset({"usc.gal", "usc.es"})
ALLOWED_CAMPUS_HOSTS = frozenset({"cv.usc.es"})
_WHITESPACE = re.compile(r"\s+")
_SENSITIVE_QUERY_KEYS = frozenset(
    {"access_token", "apikey", "auth", "key", "password", "sesskey", "token", "wstoken"}
)


class UnsafeUrlError(ValueError):
    pass


def validate_usc_url(url: str, *, campus: bool = False) -> str:
    parsed = urlparse(url)
    allowed = ALLOWED_CAMPUS_HOSTS if campus else ALLOWED_PUBLIC_HOSTS
    host = (parsed.hostname or "").lower().rstrip(".")
    host_allowed = any(host == suffix or host.endswith(f".{suffix}") for suffix in allowed)
    query_keys = {key.casefold() for key, _ in parse_qsl(parsed.query, keep_blank_values=True)}
    if (
        parsed.scheme != "https"
        or not host_allowed
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
        or query_keys.intersection(_SENSITIVE_QUERY_KEYS)
    ):
        raise UnsafeUrlError("URL no permitida por la política de destinos USC")
    return url


def html_to_text(value: str | None, *, limit: int = 8_000) -> str:
    if not value:
        return ""
    soup = BeautifulSoup(value, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    text = html.unescape(soup.get_text(" ", strip=True))
    return _WHITESPACE.sub(" ", text).strip()[:limit]


def redact_secret(message: str, *secrets: str | None) -> str:
    redacted = message
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "[REDACTED]")
    return redacted
