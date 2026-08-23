from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from mcp_usc import service as service_module
from mcp_usc.campus import CampusCapabilityUnavailable
from mcp_usc.confirmations import ACTION_CONFIRMATIONS
from mcp_usc.service import UscService
from mcp_usc.settings import Settings


class FakeCollaborationGateway:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Mapping[str, Any]]] = []
        self.fetched: list[tuple[str, int]] = []
        self.required: list[set[str]] = []

    async def status(self) -> dict[str, Any]:
        return {"authenticated": True, "user_id": 5}

    async def invoke(self, function: str, arguments: Mapping[str, Any]) -> Any:
        self.calls.append((function, arguments))
        if function == "core_message_get_messages":
            return {"messages": [{"id": 8, "text": "Profesor", "useridfrom": 7}]}
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
        if function == "mod_forum_get_discussion_posts":
            return {
                "posts": [
                    {
                        "id": 41,
                        "discussionid": arguments["discussionid"],
                        "subject": "Aviso",
                        "message": "Texto",
                    }
                ],
                "warnings": [],
            }
        raise AssertionError(function)

    async def require_functions(self, functions: set[str]) -> None:
        self.required.append(functions)

    async def fetch_file(self, url: str, max_bytes: int) -> tuple[bytes, str, str]:
        self.fetched.append((url, max_bytes))
        return b"Leccion", "text/plain", url


@pytest.fixture(autouse=True)
def clear_resource_references() -> None:
    service_module._RESOURCE_REFERENCES.clear()
    ACTION_CONFIRMATIONS.clear()


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


async def test_safe_messages_are_scoped_to_authenticated_user(tmp_path: Path) -> None:
    gateway = FakeCollaborationGateway()
    service = _service_with(gateway, tmp_path)

    result = await service.list_messages("received", "conversations", "all", 0, 20, True)

    assert result["items"][0]["message_id"] == 8
    assert gateway.calls[0][1]["useridto"] == 5


async def test_unsafe_conversation_listing_fails_without_invocation(tmp_path: Path) -> None:
    gateway = FakeCollaborationGateway()
    service = _service_with(gateway, tmp_path)

    with pytest.raises(CampusCapabilityUnavailable, match="list_messages"):
        await service.list_conversations(0, 20, None, None)

    assert gateway.calls == []


async def test_discussion_posts_require_confirmed_stateful_inspection(tmp_path: Path) -> None:
    gateway = FakeCollaborationGateway()
    service = _service_with(gateway, tmp_path)

    with pytest.raises(CampusCapabilityUnavailable, match="preview_inspect_discussion_posts"):
        await service.list_discussion_posts(33, 0, 20)

    preview = await service.inspect_discussion_posts(33, 0, 20, None)
    assert gateway.calls == []
    assert gateway.required == [{"mod_forum_get_discussion_posts"}]

    result = await service.inspect_discussion_posts(33, 0, 20, preview["confirmation_token"])

    assert result["stateful_inspection_confirmed"] is True
    assert result["items"][0]["post_id"] == 41
    assert [call[0] for call in gateway.calls] == ["mod_forum_get_discussion_posts"]


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
