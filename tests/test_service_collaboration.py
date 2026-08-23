from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from mcp_usc import service as service_module
from mcp_usc.service import UscService
from mcp_usc.settings import Settings


class FakeCollaborationGateway:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Mapping[str, Any]]] = []
        self.fetched: list[tuple[str, int]] = []

    async def status(self) -> dict[str, Any]:
        return {"authenticated": True, "user_id": 5}

    async def invoke(self, function: str, arguments: Mapping[str, Any]) -> Any:
        self.calls.append((function, arguments))
        if function == "core_message_get_conversations":
            return {"conversations": [{"id": 8, "name": "Profesor"}]}
        if function == "core_course_get_contents":
            return [
                {
                    "id": 70,
                    "section": 1,
                    "name": "Tema",
                    "modules": [
                        {
                            "id": 11,
                            "modname": "resource",
                            "name": "Guía",
                            "contents": [
                                {
                                    "filename": "guia.txt",
                                    "mimetype": "text/plain",
                                    "filesize": 6,
                                    "fileurl": (
                                        "https://cv.usc.es/webservice/pluginfile.php/70/"
                                        "mod_resource/content/1/guia.txt"
                                    ),
                                }
                            ],
                        }
                    ],
                }
            ]
        raise AssertionError(function)

    async def fetch_file(self, url: str, max_bytes: int) -> tuple[bytes, str, str]:
        self.fetched.append((url, max_bytes))
        return b"Leccion", "text/plain", url


@pytest.fixture(autouse=True)
def clear_resource_references() -> None:
    service_module._RESOURCE_REFERENCES.clear()


def _service_with(gateway: FakeCollaborationGateway, root: Path) -> UscService:
    service = object.__new__(UscService)
    service.settings = Settings(
        moodle_url="https://cv.usc.es",
        moodle_token=None,
        browser_channel="chromium",
        browser_profile_dir=root / "browser",
        exam_sources=(),
    )
    service._campus = lambda: gateway
    return service


async def test_conversations_are_scoped_to_authenticated_user(tmp_path: Path) -> None:
    gateway = FakeCollaborationGateway()
    service = _service_with(gateway, tmp_path)

    result = await service.list_conversations(0, 20, None, None)

    assert result["items"][0]["conversation_id"] == 8
    assert gateway.calls[0][1]["userid"] == 5


async def test_resource_must_come_from_recent_listing_before_http_fetch(tmp_path: Path) -> None:
    gateway = FakeCollaborationGateway()
    service = _service_with(gateway, tmp_path)
    listed = await service.list_course_resources(3, None, 0, 20)
    resource = listed["items"][0]

    assert "url" not in resource
    result = await service.read_course_resource(
        resource["resource_token"],
        1024,
        1000,
        10,
    )

    assert result["text"] == "Leccion"
    assert result["content_is_untrusted"] is True
    assert len(gateway.fetched) == 1


async def test_unknown_resource_token_never_fetches(tmp_path: Path) -> None:
    gateway = FakeCollaborationGateway()
    service = _service_with(gateway, tmp_path)

    with pytest.raises(ValueError, match="no procede de una lista reciente"):
        await service.read_course_resource("inventado", 1024, 1000, 10)

    assert gateway.fetched == []
