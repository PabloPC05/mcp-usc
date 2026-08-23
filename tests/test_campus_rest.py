import base64
from pathlib import Path

import httpx
import pytest
import respx

from mcp_usc.campus import (
    AuthenticationRequired,
    CampusCapabilityUnavailable,
    RestMoodleGateway,
    _flatten_form,
)
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


@respx.mock
async def test_rest_file_download_uses_token_internally_and_returns_safe_url() -> None:
    route = respx.get(
        "https://cv.usc.es/webservice/pluginfile.php/7/mod_resource/content/1/tema.txt",
        params={"token": "secret-token"},
    ).mock(
        return_value=httpx.Response(200, content=b"apuntes", headers={"content-type": "text/plain"})
    )

    content, media_type, source_url = await RestMoodleGateway(_settings()).fetch_file(
        "https://cv.usc.es/pluginfile.php/7/mod_resource/content/1/tema.txt",
        1024,
    )

    assert route.call_count == 1
    assert content == b"apuntes"
    assert media_type == "text/plain"
    assert source_url.endswith("/tema.txt")
    assert "secret-token" not in source_url


async def test_rest_file_download_rejects_non_pluginfile_url() -> None:
    with pytest.raises(ValueError, match="archivo Moodle permitido"):
        await RestMoodleGateway(_settings()).fetch_file(
            "https://cv.usc.es/admin/config.php",
            1024,
        )


@respx.mock
async def test_rest_draft_upload_uses_official_multipart_endpoint() -> None:
    route = respx.post("https://cv.usc.es/webservice/upload.php").mock(
        return_value=httpx.Response(
            200,
            json=[{"itemid": 91, "filepath": "/", "filename": "entrega.txt"}],
        )
    )

    result = await RestMoodleGateway(_settings()).invoke(
        "core_files_upload",
        {
            "contextid": 55,
            "component": "user",
            "filearea": "draft",
            "itemid": 91,
            "filepath": "/",
            "filename": "entrega.txt",
            "filecontent": base64.b64encode(b"contenido").decode("ascii"),
        },
    )

    request = route.calls[0].request
    assert result[0]["itemid"] == 91
    assert request.url.query == b""
    assert b"secret-token" in request.content
    assert b"contenido" in request.content


@respx.mock
async def test_rest_upload_capability_is_checked_before_preview() -> None:
    respx.post("https://cv.usc.es/webservice/rest/server.php").mock(
        return_value=httpx.Response(
            200,
            json={
                "sitename": "USC",
                "fullname": "Ada",
                "userid": 5,
                "uploadfiles": 0,
                "functions": [
                    {"name": "core_files_get_unused_draft_itemid"},
                    {"name": "mod_assign_save_submission"},
                ],
            },
        )
    )

    with pytest.raises(CampusCapabilityUnavailable, match="no permite subir archivos"):
        await RestMoodleGateway(_settings()).require_functions(
            {
                "core_files_get_unused_draft_itemid",
                "core_files_upload",
                "mod_assign_save_submission",
            }
        )
