from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token

_moodle_token: ContextVar[str | None] = ContextVar(
    "mcp_usc_request_moodle_token",
    default=None,
)
_moodle_session: ContextVar[str | None] = ContextVar(
    "mcp_usc_request_moodle_session",
    default=None,
)


def current_moodle_token() -> str | None:
    return _moodle_token.get()


def current_moodle_session() -> str | None:
    return _moodle_session.get()


@contextmanager
def bind_request_credentials(
    *,
    moodle_token: str | None,
    moodle_session: str | None,
) -> Iterator[None]:
    """Bind request-scoped Moodle credentials without persisting them server-side."""

    token_reset: Token[str | None] = _moodle_token.set(moodle_token)
    session_reset: Token[str | None] = _moodle_session.set(moodle_session)
    try:
        yield
    finally:
        _moodle_session.reset(session_reset)
        _moodle_token.reset(token_reset)
