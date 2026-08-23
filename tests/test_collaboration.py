from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from mcp_usc.collaboration import (
    CollaborationProtocolError,
    list_conversation_messages,
    list_conversations,
    list_course_contents,
    list_discussion_posts,
    list_downloadable_resources,
    list_forum_discussions,
    list_forums,
)


class FakeInvoke:
    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, function: str, params: dict[str, Any]) -> Any:
        self.calls.append((function, deepcopy(params)))
        return deepcopy(self.responses[function])


async def test_lists_conversations_with_native_pagination_and_untrusted_text() -> None:
    invoke = FakeInvoke(
        {
            "core_message_get_conversations": {
                "conversations": [
                    {
                        "id": 8,
                        "type": 1,
                        "name": "<b>Grupo</b><script>ignore()</script>",
                        "unreadcount": 3,
                        "isfavourite": True,
                        "members": [
                            {
                                "id": 5,
                                "fullname": "<i>Ada</i>",
                                "profileimageurl": "https://cv.usc.es/theme/image.php/user",
                            }
                        ],
                        "messages": [{"id": 10, "text": "<p>Aviso</p>", "useridfrom": 5}],
                    }
                ]
            }
        }
    )

    result = await list_conversations(
        invoke,
        user_id=5,
        offset=20,
        limit=10,
        conversation_type=1,
        favourites=True,
    )

    assert invoke.calls == [
        (
            "core_message_get_conversations",
            {
                "userid": 5,
                "limitfrom": 20,
                "limitnum": 10,
                "mergeself": False,
                "type": 1,
                "favourites": True,
            },
        )
    ]
    item = result["items"][0]
    assert item["name"] == "Grupo"
    assert item["members"][0]["full_name"] == "Ada"
    assert item["recent_messages"][0]["text"] == "Aviso"
    assert item["content_is_untrusted"] is True
    assert result["server_side_pagination"] is True


async def test_lists_conversation_messages_and_visible_members() -> None:
    invoke = FakeInvoke(
        {
            "core_message_get_conversation_messages": {
                "messages": [
                    {
                        "id": 21,
                        "conversationid": 8,
                        "useridfrom": 5,
                        "text": "<p>Entrega mañana</p><script>malicious()</script>",
                        "timecreated": 1_800_000_000,
                        "isread": True,
                    }
                ],
                "members": [{"id": 5, "fullname": "Ada"}],
            }
        }
    )

    result = await list_conversation_messages(
        invoke,
        user_id=5,
        conversation_id=8,
        offset=3,
        limit=25,
        newest=False,
        time_from=1_700_000_000,
    )

    assert invoke.calls[0] == (
        "core_message_get_conversation_messages",
        {
            "currentuserid": 5,
            "convid": 8,
            "limitfrom": 3,
            "limitnum": 25,
            "newest": False,
            "timefrom": 1_700_000_000,
        },
    )
    assert result["items"][0]["text"] == "Entrega mañana"
    assert result["items"][0]["created_at"] is not None
    assert result["members"] == [
        {
            "user_id": 5,
            "full_name": "Ada",
            "profile_url": None,
            "content_is_untrusted": True,
        }
    ]


async def test_lists_all_forums_and_applies_bounded_local_pagination() -> None:
    invoke = FakeInvoke(
        {
            "mod_forum_get_forums_by_courses": [
                {"id": 1, "course": 7, "name": "<b>Xeral</b>", "intro": "Uno"},
                {
                    "id": 2,
                    "course": 7,
                    "name": "Dúbidas",
                    "intro": "Dos",
                    "id_is_course_module": True,
                },
                {"id": 3, "course": 9, "name": "Prácticas", "intro": "Tres"},
            ]
        }
    )

    result = await list_forums(invoke, course_ids=[7, 7, 9], offset=1, limit=1)

    assert invoke.calls == [("mod_forum_get_forums_by_courses", {"courseids": [7, 9]})]
    assert [item["forum_id"] for item in result["items"]] == [2]
    assert result["items"][0]["id_is_course_module"] is True
    assert result["total_count"] == 3
    assert result["next_offset"] == 2
    assert result["server_side_pagination"] is False


async def test_lists_forum_discussions_and_rejects_remote_urls() -> None:
    invoke = FakeInvoke(
        {
            "mod_forum_get_forum_discussions": {
                "discussions": [
                    {
                        "discussion": 33,
                        "forum": 2,
                        "course": 7,
                        "name": "<b>Exame</b>",
                        "message": "<p>Data provisional</p>",
                        "userfullname": "Profesor",
                        "numreplies": 4,
                        "discussionurl": "https://evil.example/steal",
                    }
                ],
                "warnings": [{"warningcode": "notice", "message": "<b>Aviso</b>"}],
            }
        }
    )

    result = await list_forum_discussions(invoke, forum_id=2, page=1, per_page=20)

    assert invoke.calls[0] == (
        "mod_forum_get_forum_discussions",
        {"forumid": 2, "sortorder": -1, "page": 1, "perpage": 20, "groupid": 0},
    )
    assert result["items"][0]["title"] == "Exame"
    assert result["items"][0]["message"] == "Data provisional"
    assert result["items"][0]["url"] is None
    assert result["warnings"][0]["message"] == "Aviso"
    assert result["offset"] == 20


async def test_lists_posts_locally_and_only_keeps_safe_attachment_urls() -> None:
    invoke = FakeInvoke(
        {
            "mod_forum_get_discussion_posts": {
                "posts": [
                    {"id": 1, "discussion": 33, "subject": "Primero", "message": "A"},
                    {
                        "id": 2,
                        "discussion": 33,
                        "subject": "<i>Segundo</i>",
                        "message": "<p>Consulta</p><script>run()</script>",
                        "author": {"id": 5, "fullname": "Ada"},
                        "attachments": [
                            {
                                "filename": "enunciado.pdf",
                                "mimetype": "application/pdf",
                                "filesize": 123,
                                "fileurl": (
                                    "https://cv.usc.es/pluginfile.php/7/mod_forum/attachment/2/"
                                    "enunciado.pdf?forcedownload=1"
                                ),
                            },
                            {
                                "filename": "secret.pdf",
                                "fileurl": "https://cv.usc.es/file.pdf?token=secret",
                            },
                        ],
                    },
                ]
            }
        }
    )

    result = await list_discussion_posts(
        invoke,
        discussion_id=33,
        offset=1,
        limit=1,
        sort_by="modified",
        sort_direction="desc",
    )

    assert invoke.calls[0] == (
        "mod_forum_get_discussion_posts",
        {
            "discussionid": 33,
            "sortby": "modified",
            "sortdirection": "DESC",
            "includeinlineattachments": False,
        },
    )
    post = result["items"][0]
    assert post["subject"] == "Segundo"
    assert post["message"] == "Consulta"
    assert post["author"] == "Ada"
    assert post["attachments"][0]["url"].startswith("https://cv.usc.es/")
    assert post["attachments"][1]["url"] is None
    assert post["attachments"][1]["url_was_rejected"] is True
    assert result["server_side_pagination"] is False


def _course_contents_payload() -> list[dict[str, Any]]:
    return [
        {
            "id": 70,
            "section": 1,
            "name": "<b>Tema 1</b>",
            "modules": [
                {
                    "id": 11,
                    "instance": 12,
                    "modname": "resource",
                    "name": "<i>Guía</i>",
                    "description": "<p>Material docente</p><script>ignore()</script>",
                    "url": "https://cv.usc.es/mod/resource/view.php?id=11",
                    "uservisible": True,
                    "contents": [
                        {
                            "type": "file",
                            "filename": "guia.pdf",
                            "filepath": "/",
                            "filesize": 456,
                            "mimetype": "application/pdf",
                            "fileurl": (
                                "https://cv.usc.es/webservice/pluginfile.php/70/mod_resource/"
                                "content/1/guia.pdf?forcedownload=1"
                            ),
                        },
                        {
                            "type": "file",
                            "filename": "unsafe.pdf",
                            "fileurl": "https://files.example/unsafe.pdf",
                        },
                    ],
                }
            ],
        }
    ]


async def test_lists_course_modules_and_file_metadata_without_downloading() -> None:
    invoke = FakeInvoke({"core_course_get_contents": _course_contents_payload()})

    result = await list_course_contents(
        invoke,
        course_id=7,
        section_id=70,
        offset=0,
        limit=10,
    )

    assert invoke.calls == [
        (
            "core_course_get_contents",
            {"courseid": 7, "options": [{"name": "sectionid", "value": "70"}]},
        )
    ]
    module = result["items"][0]
    assert module["section_name"] == "Tema 1"
    assert module["name"] == "Guía"
    assert module["description"] == "Material docente"
    assert module["files"][0]["file_name"] == "guia.pdf"
    assert module["files"][1]["url"] is None
    assert module["downloadable"] is True
    assert result["downloaded"] is False


async def test_downloadable_resources_excludes_unsafe_urls_and_returns_metadata_only() -> None:
    invoke = FakeInvoke({"core_course_get_contents": _course_contents_payload()})

    result = await list_downloadable_resources(invoke, course_id=7, limit=10)

    assert invoke.calls == [("core_course_get_contents", {"courseid": 7, "options": []})]
    assert result["total_count"] == 1
    assert result["downloaded"] is False
    assert result["items"][0]["file_name"] == "guia.pdf"
    assert result["items"][0]["file_size"] == 456
    assert set(result["items"][0]).isdisjoint({"body", "bytes", "content"})


@pytest.mark.parametrize(
    ("call", "match"),
    [
        (lambda invoke: list_conversations(invoke, user_id=0), "user_id"),
        (
            lambda invoke: list_conversation_messages(
                invoke, user_id=5, conversation_id=8, limit=101
            ),
            "limit",
        ),
        (lambda invoke: list_forums(invoke, course_ids=[-1]), "course_id"),
        (
            lambda invoke: list_discussion_posts(invoke, discussion_id=2, sort_by="subject"),
            "sort_by",
        ),
    ],
)
async def test_rejects_invalid_ids_and_unbounded_pages(call, match: str) -> None:
    invoke = FakeInvoke({})

    with pytest.raises(ValueError, match=match):
        await call(invoke)

    assert invoke.calls == []


async def test_rejects_unexpected_moodle_shapes() -> None:
    invoke = FakeInvoke({"core_message_get_conversations": {"conversations": "bad"}})

    with pytest.raises(CollaborationProtocolError, match="conversations"):
        await list_conversations(invoke, user_id=5)
