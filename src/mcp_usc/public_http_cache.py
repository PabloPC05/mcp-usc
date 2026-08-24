"""Bounded, validator-aware cache for USC's unauthenticated public GETs.

The cache deliberately separates downloading from committing a response.  A
caller must parse and validate a fresh response before calling ``commit``;
therefore a changed upstream schema can never poison a previously valid
entry.  Stale data is only returned for transient transport/server failures
and is always labelled ``degraded``.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable, Iterable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import urljoin, urlparse

import httpx

from .security import UnsafeUrlError

CacheStatus = Literal["fresh", "revalidated", "degraded"]


class PublicHttpError(RuntimeError):
    """A public resource could not be downloaded under the safe policy."""


class PublicHttpStatusError(PublicHttpError):
    """A non-transient HTTP status returned by an allowed public URL."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"La fuente USC respondió con HTTP {status_code}")


@dataclass(frozen=True, slots=True)
class PublicHttpCacheMetadata:
    """Freshness evidence attached to every returned public document."""

    status: CacheStatus
    requested_url: str
    final_url: str
    cache_hit: bool
    fetched_at: str
    validated_at: str
    age_seconds: float
    ttl_seconds: float
    media_type: str
    etag: str | None = None
    last_modified: str | None = None
    degraded_reason: str | None = None


@dataclass(frozen=True, slots=True)
class PublicHttpResponse:
    content: bytes
    metadata: PublicHttpCacheMetadata
    _cache_key: str
    _cacheable: bool = False
    _is_candidate: bool = False
    _allow_stale: bool = True


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    content: bytes
    final_url: str
    fetched_at_wall: str
    fetched_at_mono: float
    validated_at_wall: str
    validated_at_mono: float
    ttl_seconds: float
    allow_stale: bool
    media_type: str
    etag: str | None
    last_modified: str | None


@dataclass(slots=True)
class _KeyLock:
    lock: asyncio.Lock
    users: int = 0


def _wall_now() -> str:
    return datetime.now(UTC).isoformat()


def _safe_validator(value: str | None) -> str | None:
    if value is None or not value or len(value) > 1_024:
        return None
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return None
    return value


def _cache_permitted(headers: Mapping[str, str]) -> bool:
    directives = {
        part.strip().casefold().split("=", 1)[0]
        for part in headers.get("cache-control", "").split(",")
        if part.strip()
    }
    if "no-store" in directives:
        return False
    # This cache is deliberately keyed only by URL. Until request-header variants
    # are part of the key, storing any Vary response could serve the wrong
    # representation to a future consumer.
    vary = {part.strip().casefold() for part in headers.get("vary", "").split(",") if part.strip()}
    return not vary


def _cache_policy(
    headers: Mapping[str, str], configured_ttl: float
) -> tuple[bool, float, bool]:
    values = [part.strip().casefold() for part in headers.get("cache-control", "").split(",")]
    names = {value.split("=", 1)[0] for value in values if value}
    effective_ttl = configured_ttl
    for value in values:
        if not value.startswith("max-age="):
            continue
        raw = value.split("=", 1)[1].strip().strip('"')
        if raw.isdigit():
            effective_ttl = min(effective_ttl, float(int(raw)))
    if "no-cache" in names:
        effective_ttl = 0.0
    allow_stale = not names.intersection({"must-revalidate", "no-cache"})
    return _cache_permitted(headers), effective_ttl, allow_stale


class PublicHttpCache:
    """In-memory LRU constrained by entry count, total bytes and TTL."""

    def __init__(
        self,
        *,
        ttl_seconds: float = 300.0,
        stale_if_error_seconds: float = 3_600.0,
        max_entries: int = 128,
        max_total_bytes: int = 64_000_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if isinstance(ttl_seconds, bool) or not 0 <= ttl_seconds <= 86_400:
            raise ValueError("ttl_seconds debe estar entre 0 y 86400")
        if (
            isinstance(stale_if_error_seconds, bool)
            or not 0 <= stale_if_error_seconds <= 604_800
        ):
            raise ValueError("stale_if_error_seconds debe estar entre 0 y 604800")
        if isinstance(max_entries, bool) or not 1 <= max_entries <= 4_096:
            raise ValueError("max_entries debe estar entre 1 y 4096")
        if isinstance(max_total_bytes, bool) or not 1 <= max_total_bytes <= 512_000_000:
            raise ValueError("max_total_bytes debe estar entre 1 y 512000000")
        self.ttl_seconds = float(ttl_seconds)
        self.stale_if_error_seconds = float(stale_if_error_seconds)
        self.max_entries = max_entries
        self.max_total_bytes = max_total_bytes
        self._clock = clock
        self._entries: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._total_bytes = 0
        self._guard = asyncio.Lock()
        self._key_locks: dict[str, _KeyLock] = {}

    @property
    def entry_count(self) -> int:
        return len(self._entries)

    @property
    def total_bytes(self) -> int:
        return self._total_bytes

    def _entry(self, key: str) -> _CacheEntry | None:
        entry = self._entries.get(key)
        if entry is not None:
            self._entries.move_to_end(key)
        return entry

    def _metadata(
        self,
        key: str,
        entry: _CacheEntry,
        *,
        status: CacheStatus,
        cache_hit: bool,
        reason: str | None = None,
    ) -> PublicHttpCacheMetadata:
        age = max(0.0, self._clock() - entry.validated_at_mono)
        return PublicHttpCacheMetadata(
            status=status,
            requested_url=key,
            final_url=entry.final_url,
            cache_hit=cache_hit,
            fetched_at=entry.fetched_at_wall,
            validated_at=entry.validated_at_wall,
            age_seconds=age,
            ttl_seconds=entry.ttl_seconds,
            media_type=entry.media_type,
            etag=entry.etag,
            last_modified=entry.last_modified,
            degraded_reason=reason,
        )

    def fresh(self, key: str) -> PublicHttpResponse | None:
        entry = self._entry(key)
        if entry is None or self._clock() - entry.validated_at_mono >= entry.ttl_seconds:
            return None
        return PublicHttpResponse(
            entry.content,
            self._metadata(key, entry, status="fresh", cache_hit=True),
            key,
        )

    def stale(self, key: str) -> _CacheEntry | None:
        return self._entry(key)

    def conditional_headers(self, key: str) -> dict[str, str]:
        entry = self._entry(key)
        if entry is None:
            return {}
        headers: dict[str, str] = {}
        if entry.etag:
            headers["If-None-Match"] = entry.etag
        if entry.last_modified:
            headers["If-Modified-Since"] = entry.last_modified
        return headers

    def revalidated(
        self, key: str, headers: Mapping[str, str] | None = None
    ) -> PublicHttpResponse:
        entry = self._entry(key)
        if entry is None:
            raise PublicHttpError("La fuente devolvió 304 sin una entrada de caché")
        now_wall, now_mono = _wall_now(), self._clock()
        ttl_seconds, allow_stale = entry.ttl_seconds, entry.allow_stale
        if headers is not None and "cache-control" in headers:
            _, ttl_seconds, allow_stale = _cache_policy(headers, self.ttl_seconds)
        updated = replace(
            entry,
            validated_at_wall=now_wall,
            validated_at_mono=now_mono,
            ttl_seconds=ttl_seconds,
            allow_stale=allow_stale,
            etag=_safe_validator((headers or {}).get("etag")) or entry.etag,
            last_modified=(
                _safe_validator((headers or {}).get("last-modified")) or entry.last_modified
            ),
        )
        self._entries[key] = updated
        result = PublicHttpResponse(
            updated.content,
            self._metadata(key, updated, status="revalidated", cache_hit=True),
            key,
        )
        if headers is not None and not _cache_permitted(headers):
            self._entries.pop(key, None)
            self._total_bytes -= len(updated.content)
        return result

    def degraded(self, key: str, reason: str) -> PublicHttpResponse | None:
        entry = self._entry(key)
        if entry is None:
            return None
        stale_age = self._clock() - entry.validated_at_mono - entry.ttl_seconds
        if not entry.allow_stale or stale_age > self.stale_if_error_seconds:
            return None
        return PublicHttpResponse(
            entry.content,
            self._metadata(
                key,
                entry,
                status="degraded",
                cache_hit=True,
                reason=reason,
            ),
            key,
        )

    def candidate(
        self,
        *,
        key: str,
        final_url: str,
        content: bytes,
        headers: Mapping[str, str],
    ) -> PublicHttpResponse:
        now_wall = _wall_now()
        cacheable, effective_ttl, allow_stale = _cache_policy(headers, self.ttl_seconds)
        metadata = PublicHttpCacheMetadata(
            status="fresh",
            requested_url=key,
            final_url=final_url,
            cache_hit=False,
            fetched_at=now_wall,
            validated_at=now_wall,
            age_seconds=0.0,
            ttl_seconds=effective_ttl,
            media_type=headers.get("content-type", "").split(";", 1)[0].strip().casefold(),
            etag=_safe_validator(headers.get("etag")),
            last_modified=_safe_validator(headers.get("last-modified")),
        )
        cacheable = cacheable and len(content) <= self.max_total_bytes
        return PublicHttpResponse(content, metadata, key, cacheable, True, allow_stale)

    def commit(self, response: PublicHttpResponse) -> None:
        """Store a response only after the caller has validated its schema."""

        if not response._is_candidate:
            return
        metadata = response.metadata
        previous = self._entries.pop(response._cache_key, None)
        if previous is not None:
            self._total_bytes -= len(previous.content)
        if not response._cacheable:
            return
        entry = _CacheEntry(
            content=response.content,
            final_url=metadata.final_url,
            fetched_at_wall=metadata.fetched_at,
            fetched_at_mono=self._clock(),
            validated_at_wall=metadata.validated_at,
            validated_at_mono=self._clock(),
            ttl_seconds=metadata.ttl_seconds,
            allow_stale=response._allow_stale,
            media_type=metadata.media_type,
            etag=metadata.etag,
            last_modified=metadata.last_modified,
        )
        self._entries[response._cache_key] = entry
        self._total_bytes += len(entry.content)
        while len(self._entries) > self.max_entries or self._total_bytes > self.max_total_bytes:
            _, evicted = self._entries.popitem(last=False)
            self._total_bytes -= len(evicted.content)

    @asynccontextmanager
    async def serialise(self, key: str) -> AsyncIterator[None]:
        """Collapse concurrent requests for one cache key without leaking locks."""

        async with self._guard:
            state = self._key_locks.get(key)
            if state is None:
                state = _KeyLock(asyncio.Lock())
                self._key_locks[key] = state
            state.users += 1
        try:
            async with state.lock:
                yield
        finally:
            async with self._guard:
                state.users -= 1
                if state.users == 0:
                    self._key_locks.pop(key, None)


DEFAULT_PUBLIC_HTTP_CACHE = PublicHttpCache()


class SafePublicHttpFetcher:
    """GET-only HTTP fetcher with exact-origin redirects and bounded bodies."""

    def __init__(
        self,
        *,
        timeout: float,
        max_bytes: int,
        cache: PublicHttpCache,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.cache = cache
        self.transport = transport

    async def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        validate_redirect: Callable[[str], None],
        validate: Callable[[PublicHttpResponse], None],
    ) -> PublicHttpResponse:
        key = url
        cached = self.cache.fresh(key)
        if cached is not None:
            if len(cached.content) > self.max_bytes:
                raise PublicHttpError(
                    f"La respuesta supera el límite de {self.max_bytes} bytes"
                )
            return cached
        async with self.cache.serialise(key):
            cached = self.cache.fresh(key)
            if cached is not None:
                if len(cached.content) > self.max_bytes:
                    raise PublicHttpError(
                        f"La respuesta supera el límite de {self.max_bytes} bytes"
                    )
                return cached
            stale = self.cache.stale(key)
            stale_fits = stale is None or len(stale.content) <= self.max_bytes
            conditional = self.cache.conditional_headers(key) if stale_fits else {}
            current = url
            request_headers = {**headers, **conditional}
            async with httpx.AsyncClient(
                timeout=self.timeout,
                follow_redirects=False,
                transport=self.transport,
            ) as client:
                for _ in range(5):
                    try:
                        async with client.stream(
                            "GET", current, headers=request_headers
                        ) as response:
                            if response.status_code == 304:
                                result = self.cache.revalidated(key, response.headers)
                                if len(result.content) > self.max_bytes:
                                    raise PublicHttpError(
                                        f"La respuesta supera el límite de {self.max_bytes} bytes"
                                    )
                                return result
                            if response.is_redirect:
                                location = response.headers.get("location")
                                if not location:
                                    raise PublicHttpError("Redirección USC sin destino")
                                candidate = urljoin(current, location)
                                old, new = urlparse(current), urlparse(candidate)
                                if (new.scheme, new.hostname, new.port) != (
                                    old.scheme,
                                    old.hostname,
                                    old.port,
                                ):
                                    raise UnsafeUrlError(
                                        "Redirección fuera del origen exacto de USC"
                                    )
                                validate_redirect(candidate)
                                client.cookies.clear()
                                current = candidate
                                continue
                            if response.status_code >= 400:
                                if response.status_code == 429 or response.status_code >= 500:
                                    degraded = self.cache.degraded(
                                        key, f"HTTP {response.status_code} durante revalidación"
                                    )
                                    if (
                                        degraded is not None
                                        and len(degraded.content) <= self.max_bytes
                                    ):
                                        return degraded
                                raise PublicHttpStatusError(response.status_code)
                            declared = response.headers.get("content-length")
                            if declared and declared.isdigit() and int(declared) > self.max_bytes:
                                raise PublicHttpError(
                                    f"La respuesta supera el límite de {self.max_bytes} bytes"
                                )
                            chunks: list[bytes] = []
                            size = 0
                            async for chunk in response.aiter_bytes():
                                size += len(chunk)
                                if size > self.max_bytes:
                                    raise PublicHttpError(
                                        f"La respuesta supera el límite de {self.max_bytes} bytes"
                                    )
                                chunks.append(chunk)
                            candidate = self.cache.candidate(
                                key=key,
                                final_url=current,
                                content=b"".join(chunks),
                                headers=response.headers,
                            )
                            validate(candidate)
                            self.cache.commit(candidate)
                            return candidate
                    except httpx.HTTPError:
                        degraded = self.cache.degraded(key, "fallo transitorio de transporte")
                        if degraded is not None and len(degraded.content) <= self.max_bytes:
                            return degraded
                        raise PublicHttpError("No se pudo leer la fuente pública USC") from None
            raise PublicHttpError("Demasiadas redirecciones en la fuente pública USC")


def public_cache_summary(
    metadata: Iterable[PublicHttpCacheMetadata],
) -> dict[str, object]:
    """Return a secret-free aggregate suitable for MCP JSON responses."""

    items = list(metadata)
    resources = [
        {
            "status": item.status,
            "hit": item.cache_hit,
            "fetched_at": item.fetched_at,
            "validated_at": item.validated_at,
            "age_seconds": round(item.age_seconds, 3),
            "ttl_seconds": item.ttl_seconds,
            "final_url": item.final_url,
            "degraded_reason": item.degraded_reason,
        }
        for item in items
    ]
    if not items:
        return {
            "status": "not_used",
            "hit": False,
            "hit_count": 0,
            "resource_count": 0,
            "status_counts": {"fresh": 0, "revalidated": 0, "degraded": 0},
            "fetched_at": None,
            "validated_at": None,
            "age_seconds": None,
            "ttl_seconds": None,
            "final_url": None,
            "degraded_reason": None,
            "resources": [],
            "resources_truncated": False,
        }
    priority = {"fresh": 0, "revalidated": 1, "degraded": 2}
    status = max((item.status for item in items), key=priority.__getitem__)
    reasons = list(
        dict.fromkeys(item.degraded_reason for item in items if item.degraded_reason)
    )
    visible_resources = resources
    if len(resources) > 20:
        visible_resources = [item for item in resources if item["status"] == "degraded"][:20]
    status_counts = {
        status: sum(item.status == status for item in items)
        for status in ("fresh", "revalidated", "degraded")
    }
    return {
        "status": status,
        "hit": all(item.cache_hit for item in items),
        "hit_count": sum(item.cache_hit for item in items),
        "resource_count": len(items),
        "status_counts": status_counts,
        "fetched_at": max(item.fetched_at for item in items),
        "validated_at": max(item.validated_at for item in items),
        "age_seconds": round(max(item.age_seconds for item in items), 3),
        "ttl_seconds": min(item.ttl_seconds for item in items),
        "final_url": items[0].final_url if len(items) == 1 else None,
        "degraded_reason": "; ".join(reasons) if reasons else None,
        "resources": visible_resources,
        "resources_truncated": len(visible_resources) < len(resources),
    }


__all__ = [
    "CacheStatus",
    "PublicHttpError",
    "PublicHttpStatusError",
    "PublicHttpCacheMetadata",
    "PublicHttpResponse",
    "PublicHttpCache",
    "DEFAULT_PUBLIC_HTTP_CACHE",
    "SafePublicHttpFetcher",
    "public_cache_summary",
]
