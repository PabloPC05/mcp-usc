"""Small on-disk JSON cache for slow-changing public results that outlive one process.

A command-line run starts a fresh interpreter every time, so in-memory caches never help it.
Derived public results (where a subject code lives in the official study plans, ...) change
at most once per academic year; keeping them for days on disk turns a minute of crawling
into a file read.

Design rules:
* JSON only (never pickle), written atomically (``tempfile`` + ``os.replace``) with mode
  0600 in a 0700 directory under the user's cache folder.
* Every failure reads as a miss and every write failure is swallowed: this is an
  optimisation and must never change an answer or raise.
* The stored envelope repeats the exact key, so a foreign, truncated or renamed file is a
  miss rather than a wrong answer.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from platformdirs import user_cache_path

_FORMAT = 1
_MAX_FILE_BYTES = 8_000_000
_MAX_FILES = 256
_FALSE = {"0", "false", "no", "off"}


def disk_cache_root() -> Path:
    """Base folder of every on-disk cache (``USC_CACHE_DIR`` overrides the user cache dir)."""

    override = os.getenv("USC_CACHE_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return Path(user_cache_path("mcp-usc", appauthor=False))


class DiskJsonCache:
    """Key -> JSON payload with a wall-clock TTL, one file per key."""

    def __init__(self, namespace: str, *, ttl_seconds: float, env_flag: str) -> None:
        self.namespace = namespace
        self.ttl_seconds = ttl_seconds
        self.env_flag = env_flag

    @property
    def enabled(self) -> bool:
        return os.getenv(self.env_flag, "1").strip().casefold() not in _FALSE

    @property
    def directory(self) -> Path:
        return disk_cache_root() / self.namespace

    def _path(self, key: object) -> tuple[Path, str]:
        canonical = json.dumps(key, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("ascii")).hexdigest()
        return self.directory / f"{digest}.json", canonical

    def load(self, key: object) -> Any | None:
        """Payload stored under ``key`` and younger than the TTL, else ``None``."""

        if not self.enabled:
            return None
        try:
            path, canonical = self._path(key)
            with path.open("rb") as handle:
                raw = handle.read(_MAX_FILE_BYTES + 1)
            if len(raw) > _MAX_FILE_BYTES:
                return None
            envelope = json.loads(raw)
            if (
                not isinstance(envelope, dict)
                or envelope.get("format") != _FORMAT
                or envelope.get("namespace") != self.namespace
                or json.dumps(
                    envelope.get("key"), sort_keys=True, ensure_ascii=True, separators=(",", ":")
                )
                != canonical
            ):
                return None
            stored_at = envelope.get("stored_at")
            if isinstance(stored_at, bool) or not isinstance(stored_at, int | float):
                return None
            age = time.time() - stored_at
            if age < 0 or age >= self.ttl_seconds:
                return None
            return envelope.get("payload")
        except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
            return None

    def store(self, key: object, payload: Any) -> bool:
        """Best-effort atomic write; returns whether the file was written."""

        if not self.enabled:
            return False
        temporary: str | None = None
        try:
            path, _ = self._path(key)
            directory = path.parent
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            data = json.dumps(
                {
                    "format": _FORMAT,
                    "namespace": self.namespace,
                    "key": key,
                    "stored_at": time.time(),
                    "payload": payload,
                },
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode("ascii")
            if len(data) > _MAX_FILE_BYTES:
                return False
            descriptor, temporary = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".json")
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
            temporary = None
            self._prune()
            return True
        except (OSError, ValueError, TypeError, RecursionError):
            return False
        finally:
            if temporary is not None:
                with contextlib.suppress(OSError):
                    os.unlink(temporary)

    def clear(self) -> None:
        """Delete every entry of this namespace (used by tests and ``--refresh`` paths)."""

        with contextlib.suppress(OSError):
            for path in self.directory.glob("*.json"):
                path.unlink(missing_ok=True)

    def _prune(self) -> None:
        """Drop expired entries and keep the folder bounded."""

        try:
            now = time.time()
            entries = []
            for path in self.directory.glob("*.json"):
                modified = path.stat().st_mtime
                if now - modified >= self.ttl_seconds:
                    path.unlink(missing_ok=True)
                else:
                    entries.append((modified, path))
            for _, path in sorted(entries)[: max(0, len(entries) - _MAX_FILES)]:
                path.unlink(missing_ok=True)
        except OSError:
            pass


__all__ = ["DiskJsonCache", "disk_cache_root"]
