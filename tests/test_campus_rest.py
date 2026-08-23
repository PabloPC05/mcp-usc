from pathlib import Path

import httpx
import pytest
import respx

from mcp_usc.campus import AuthenticationRequired, RestMoodleGateway, _flatten_form
from mcp_usc.settings import Settings


def _settings() -> Settings:
    return Settings(
        moodle_url="https://cv.usc.es",
        moodle_token="secret-token",
        browser_channel="chromium",
        browser_profile_dir=Path("unused"),
        exam_sources=(),
    )


def test_flatten_moodle_form_arguments() -> None:
    assert _flatten_form({"courseids": [3, 8], "options": {"userevents": True}}) == {
        "courseids[0]": "3",
        "courseids[1]": "8",
        "options[userevents]": "1",
    }


@respx.mock
async def test_rest_status_and_secret_is_sent_in_body() -> None:
    route = respx.post("https://cv.usc.es/webservice/rest/server.php").mock(
        return_value=httpx.Response(
            200,
            json={"sitename": "USC", "fullname": "Ada", "userid": 5, "functions": []},
        )
    )
    result = await RestMoodleGateway(_settings()).status()
    assert result["authenticated"] is True
    request_body = route.calls[0].request.content.decode()
    assert "wstoken=secret-token" in request_body
    assert "secret-token" not in str(result)


@respx.mock
async def test_invalid_token_becomes_authentication_error() -> None:
    respx.post("https://cv.usc.es/webservice/rest/server.php").mock(
        return_value=httpx.Response(
            200, json={"exception": "moodle_exception", "errorcode": "invalidtoken"}
        )
    )
    with pytest.raises(AuthenticationRequired):
        await RestMoodleGateway(_settings()).status()
