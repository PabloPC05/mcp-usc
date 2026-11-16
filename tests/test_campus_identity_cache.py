"""Pooled HTTP connections and the short-lived Moodle identity cache."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
import respx

from mcp_usc.campus import (
    AuthenticationRequired,
    HttpSessionMoodleGateway,
    RestMoodleGateway,
    _pooled_client,
)
from mcp_usc.settings import Settings

_PREFERENCES = "https://cv.usc.es/user/preferences.php"
_AJAX = "https://cv.usc.es/lib/ajax/service.php"
_REST = "https://cv.usc.es/webservice/rest/server.php"


class MemoryCredentialStore:
    def __init__(self, value: str | None) -> None:
        self.value = value

    def get(self, name: str) -> str | None:
        del name
        return self.value

    def set(self, name: str, value: str) -> None:
        del name
        self.value = value


def _settings(token: str | None = None) -> Settings:
    return Settings(
        moodle_url="https://cv.usc.es",
        moodle_token=token,
        browser_channel="chromium",
        browser_profile_dir=Path("unused"),
        exam_sources=(),
    )


def _context_html(user_id: int = 5) -> str:
    return (
        "<html><head><title>Campus USC</title></head><body>"
        '<div class="usermenu"><span class="usertext">Ada</span></div>'
        f'<script>window.M = {{"sesskey":"abc123","userid":{user_id}}};</script>'
        "</body></html>"
    )


def _session(cookie: str = "cookie-value-1234") -> HttpSessionMoodleGateway:
    return HttpSessionMoodleGateway(_settings(), MemoryCredentialStore(cookie))  # type: ignore[arg-type]


@respx.mock
async def test_session_context_is_reused_by_later_gateways_of_the_same_cookie() -> None:
    preferences = respx.get(_PREFERENCES).mock(
        return_value=httpx.Response(200, text=_context_html())
    )
    ajax = respx.post(url__startswith=_AJAX).mock(
        return_value=httpx.Response(200, json=[{"error": False, "data": {"ok": True}}])
    )

    # Each MCP tool call builds a new gateway; only the first one loads the preferences page.
    assert await _session().invoke("core_fake_read", {}) == {"ok": True}
    assert await _session().invoke("core_fake_read", {}) == {"ok": True}
    assert preferences.call_count == 1
    assert ajax.call_count == 2

    # A different cookie is a different identity and is never served from that cache.
    await _session("other-cookie-5678").invoke("core_fake_read", {})
    assert preferences.call_count == 2


@respx.mock
async def test_session_status_always_revalidates_the_session() -> None:
    preferences = respx.get(_PREFERENCES).mock(
        return_value=httpx.Response(200, text=_context_html())
    )

    await _session().verify_status()
    await _session().verify_status()
    assert preferences.call_count == 2

    # Internal identity lookups (status) reuse the verified context.
    await _session().status()
    assert preferences.call_count == 2


@respx.mock
async def test_rejected_ajax_call_forgets_the_cached_session_context() -> None:
    preferences = respx.get(_PREFERENCES).mock(
        return_value=httpx.Response(200, text=_context_html())
    )
    respx.post(url__startswith=_AJAX).mock(
        side_effect=[
            httpx.Response(
                200,
                json=[{"error": True, "exception": {"errorcode": "invalidsesskey"}}],
            ),
            httpx.Response(200, json=[{"error": False, "data": {"ok": True}}]),
        ]
    )

    with pytest.raises(AuthenticationRequired):
        await _session().invoke("core_fake_read", {})
    await _session().invoke("core_fake_read", {})

    assert preferences.call_count == 2


@respx.mock
async def test_rest_site_info_is_cached_per_token_but_status_revalidates() -> None:
    route = respx.post(_REST).mock(
        return_value=httpx.Response(
            200, json={"sitename": "USC", "fullname": "Ada", "userid": 5, "functions": []}
        )
    )

    first = RestMoodleGateway(_settings("token-one"))
    second = RestMoodleGateway(_settings("token-one"))
    assert (await first.invoke("core_webservice_get_site_info", {}))["userid"] == 5
    assert await second.available_functions() == set()
    assert route.call_count == 1

    await second.status()
    assert route.call_count == 1
    await second.verify_status()
    assert route.call_count == 2

    await RestMoodleGateway(_settings("token-two")).available_functions()
    assert route.call_count == 3
    tokens = [parse_qs(call.request.content.decode())["wstoken"][0] for call in route.calls]
    assert tokens == ["token-one", "token-one", "token-two"]


@respx.mock
async def test_pooled_client_never_carries_response_cookies_to_other_credentials() -> None:
    # Moodle rotates only the first session; nothing of it may reach the second one.
    respx.get(_PREFERENCES).mock(
        side_effect=[
            httpx.Response(
                200,
                text=_context_html(),
                headers={"set-cookie": "MoodleSession=rotated-cookie-aaaa; path=/; secure"},
            ),
            httpx.Response(200, text=_context_html(user_id=6)),
        ]
    )
    ajax = respx.post(url__startswith=_AJAX).mock(
        return_value=httpx.Response(200, json=[{"error": False, "data": {}}])
    )

    await _session("first-cookie-1111").invoke("core_fake_read", {})
    await _session("second-cookie-2222").invoke("core_fake_read", {})

    sent = [call.request.headers.get("cookie") for call in ajax.calls]
    assert sent[0] == "MoodleSession=rotated-cookie-aaaa"
    assert sent[1] == "MoodleSession=second-cookie-2222"
    assert len(_pooled_client().cookies) == 0


async def test_pooled_client_is_shared_within_one_event_loop() -> None:
    assert _pooled_client() is _pooled_client()
