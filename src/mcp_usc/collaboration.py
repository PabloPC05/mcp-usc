"""Read-only Moodle collaboration and course-content helpers.

The functions in this module are transport agnostic. ``invoke`` is expected to
perform one authenticated Moodle REST or same-origin AJAX call. Remote text is
always treated as untrusted data and download URLs are restricted to the USC
Campus allowlist; this module never downloads a resource.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime
from typing import Any, TypeAlias
from zoneinfo import ZoneInfo

from .security import UnsafeUrlError, html_to_text, validate_usc_url

Invoke: TypeAlias = Callable[[str, Mapping[str, Any]], Awaitable[Any]]

MAX_PAGE_SIZE = 100
MAX_COURSE_IDS = 100
MAX_NESTED_ITEMS = 100
MAX_TEXT_LENGTH = 12_000
_MADRID = ZoneInfo("Europe/Madrid")


class CollaborationProtocolError(RuntimeError):
    """Moodle returned an unsupported response shape."""


def _positive_id(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} debe ser un entero positivo")
    return value


def _pagination(offset: int, limit: int) -> tuple[int, int]:
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("offset debe ser un entero no negativo")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_PAGE_SIZE:
        raise ValueError(f"limit debe estar entre 1 y {MAX_PAGE_SIZE}")
    return offset, limit


def _text(value: Any, *, limit: int = MAX_TEXT_LENGTH) -> str:
    if value is None:
        return ""
    return html_to_text(str(value), limit=limit)


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _timestamp(value: Any) -> str | None:
    timestamp = _integer(value)
    if timestamp <= 0:
        return None
    try:
        return datetime.fromtimestamp(timestamp, tz=_MADRID).isoformat()
    except (OSError, OverflowError, ValueError):
        return None


def _safe_campus_url(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return validate_usc_url(value, campus=True)
    except UnsafeUrlError:
        return None


def _mapping_items(payload: Any, key: str, *, allow_list: bool = False) -> list[Mapping[str, Any]]:
    value = payload if allow_list and isinstance(payload, list) else None
    if isinstance(payload, Mapping):
        value = payload.get(key)
    if not isinstance(value, list):
        raise CollaborationProtocolError(f"Moodle no devolvió una lista válida en {key}.")
    return [item for item in value if isinstance(item, Mapping)]


def _warnings(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, Mapping) or not isinstance(payload.get("warnings"), list):
        return []
    result = []
    for warning in payload["warnings"][:MAX_NESTED_ITEMS]:
        if not isinstance(warning, Mapping):
            continue
        result.append(
            {
                "code": _text(warning.get("warningcode") or warning.get("code"), limit=200),
                "message": _text(warning.get("message"), limit=2_000),
                "content_is_untrusted": True,
            }
        )
    return result


def _page_result(
    items: Sequence[dict[str, Any]],
    *,
    offset: int,
    limit: int,
    total_count: int | None = None,
    warnings: Sequence[dict[str, Any]] = (),
    server_side_pagination: bool,
) -> dict[str, Any]:
    returned = len(items)
    has_more = offset + returned < total_count if total_count is not None else returned == limit
    return {
        "items": list(items),
        "offset": offset,
        "limit": limit,
        "returned": returned,
        "total_count": total_count,
        "has_more": has_more,
        "next_offset": offset + returned if has_more else None,
        "server_side_pagination": server_side_pagination,
        "warnings": list(warnings),
        "content_is_untrusted": True,
    }


def _normalise_member(member: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "user_id": _integer(member.get("id") or member.get("userid")),
        "full_name": _text(member.get("fullname") or member.get("name"), limit=500),
        "profile_url": _safe_campus_url(
            member.get("profileurl")
            or member.get("profileimageurl")
            or member.get("profileimageurlsmall")
        ),
        "content_is_untrusted": True,
    }


def _normalise_message(message: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "message_id": _integer(message.get("id") or message.get("messageid")),
        "conversation_id": _integer(
            message.get("conversationid") or message.get("conversation_id")
        ),
        "user_id_from": _integer(message.get("useridfrom") or message.get("userfrom")),
        "text": _text(
            message.get("text")
            or message.get("fullmessage")
            or message.get("smallmessage")
            or message.get("message")
        ),
        "created_at": _timestamp(message.get("timecreated") or message.get("created")),
        "is_read": bool(message.get("isread") or message.get("read")),
        "content_is_untrusted": True,
    }


def _normalise_conversation(conversation: Mapping[str, Any]) -> dict[str, Any]:
    members = conversation.get("members")
    messages = conversation.get("messages")
    member_items = members if isinstance(members, list) else []
    message_items = messages if isinstance(messages, list) else []
    return {
        "conversation_id": _integer(conversation.get("id") or conversation.get("conversationid")),
        "type": _integer(conversation.get("type"), default=-1),
        "name": _text(conversation.get("name"), limit=500),
        "sub_name": _text(conversation.get("subname"), limit=500),
        "unread_count": _integer(conversation.get("unreadcount")),
        "is_favourite": bool(conversation.get("isfavourite")),
        "is_muted": bool(conversation.get("ismuted")),
        "members": [
            _normalise_member(member)
            for member in member_items[:MAX_NESTED_ITEMS]
            if isinstance(member, Mapping)
        ],
        "recent_messages": [
            _normalise_message(message)
            for message in message_items[:10]
            if isinstance(message, Mapping)
        ],
        "nested_items_truncated": (len(member_items) > MAX_NESTED_ITEMS or len(message_items) > 10),
        "content_is_untrusted": True,
    }


async def list_conversations(
    invoke: Invoke,
    *,
    user_id: int,
    offset: int = 0,
    limit: int = 20,
    conversation_type: int | None = None,
    favourites: bool | None = None,
    merge_self: bool = False,
) -> dict[str, Any]:
    """List the current user's Moodle conversations with server-side pagination."""

    user_id = _positive_id(user_id, "user_id")
    offset, limit = _pagination(offset, limit)
    if conversation_type is not None and (
        isinstance(conversation_type, bool) or not isinstance(conversation_type, int)
    ):
        raise ValueError("conversation_type debe ser un entero o null")
    params: dict[str, Any] = {
        "userid": user_id,
        "limitfrom": offset,
        "limitnum": limit,
        "mergeself": bool(merge_self),
    }
    if conversation_type is not None:
        params["type"] = conversation_type
    if favourites is not None:
        params["favourites"] = bool(favourites)
    payload = await invoke("core_message_get_conversations", params)
    conversations = _mapping_items(payload, "conversations")
    items = [_normalise_conversation(item) for item in conversations[:limit]]
    return _page_result(
        items,
        offset=offset,
        limit=limit,
        warnings=_warnings(payload),
        server_side_pagination=True,
    )


async def list_conversation_messages(
    invoke: Invoke,
    *,
    user_id: int,
    conversation_id: int,
    offset: int = 0,
    limit: int = 50,
    newest: bool = True,
    time_from: int = 0,
) -> dict[str, Any]:
    """List messages and visible members from one Moodle conversation."""

    user_id = _positive_id(user_id, "user_id")
    conversation_id = _positive_id(conversation_id, "conversation_id")
    offset, limit = _pagination(offset, limit)
    if isinstance(time_from, bool) or not isinstance(time_from, int) or time_from < 0:
        raise ValueError("time_from debe ser un timestamp no negativo")
    payload = await invoke(
        "core_message_get_conversation_messages",
        {
            "currentuserid": user_id,
            "convid": conversation_id,
            "limitfrom": offset,
            "limitnum": limit,
            "newest": bool(newest),
            "timefrom": time_from,
        },
    )
    messages = _mapping_items(payload, "messages")
    members = []
    raw_members = payload.get("members", []) if isinstance(payload, Mapping) else []
    if isinstance(raw_members, list):
        members = [
            _normalise_member(member)
            for member in raw_members[:MAX_NESTED_ITEMS]
            if isinstance(member, Mapping)
        ]
    result = _page_result(
        [_normalise_message(item) for item in messages[:limit]],
        offset=offset,
        limit=limit,
        warnings=_warnings(payload),
        server_side_pagination=True,
    )
    result["members"] = members
    result["members_truncated"] = len(raw_members) > MAX_NESTED_ITEMS
    return result


def _normalise_forum(forum: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "forum_id": _integer(forum.get("id")),
        "course_id": _integer(forum.get("course") or forum.get("courseid")),
        "course_module_id": _integer(forum.get("cmid")),
        "instance_id_available": bool(forum.get("instance_id_available", True)),
        "id_is_course_module": bool(forum.get("id_is_course_module", False)),
        "type": _text(forum.get("type"), limit=100),
        "name": _text(forum.get("name"), limit=500),
        "intro": _text(forum.get("intro")),
        "can_create_discussions": bool(forum.get("cancreatediscussions")),
        "content_is_untrusted": True,
    }


async def list_forums(
    invoke: Invoke,
    *,
    course_ids: Sequence[int] = (),
    offset: int = 0,
    limit: int = 50,
) -> dict[str, Any]:
    """List every visible forum, optionally restricted to enrolled course IDs."""

    offset, limit = _pagination(offset, limit)
    if len(course_ids) > MAX_COURSE_IDS:
        raise ValueError(f"course_ids no puede superar {MAX_COURSE_IDS} elementos")
    validated_ids: list[int] = []
    for course_id in course_ids:
        course_id = _positive_id(course_id, "course_id")
        if course_id not in validated_ids:
            validated_ids.append(course_id)
    payload = await invoke("mod_forum_get_forums_by_courses", {"courseids": validated_ids})
    forums = _mapping_items(payload, "forums", allow_list=True)
    page = forums[offset : offset + limit]
    return _page_result(
        [_normalise_forum(item) for item in page],
        offset=offset,
        limit=limit,
        total_count=len(forums),
        warnings=_warnings(payload),
        server_side_pagination=False,
    )


def _normalise_discussion(discussion: Mapping[str, Any]) -> dict[str, Any]:
    author = discussion.get("user") or discussion.get("author")
    author_name = author.get("fullname") if isinstance(author, Mapping) else author
    return {
        "discussion_id": _integer(
            discussion.get("discussion") or discussion.get("discussionid") or discussion.get("id")
        ),
        "forum_id": _integer(discussion.get("forum") or discussion.get("forumid")),
        "course_id": _integer(discussion.get("course") or discussion.get("courseid")),
        "title": _text(discussion.get("name") or discussion.get("subject"), limit=1_000),
        "message": _text(discussion.get("message")),
        "author": _text(discussion.get("userfullname") or author_name, limit=500),
        "author_user_id": _integer(discussion.get("userid")),
        "created_at": _timestamp(discussion.get("created")),
        "modified_at": _timestamp(discussion.get("timemodified") or discussion.get("modified")),
        "reply_count": _integer(discussion.get("numreplies")),
        "unread_count": _integer(discussion.get("numunread")),
        "is_pinned": bool(discussion.get("pinned")),
        "is_locked": bool(discussion.get("locked")),
        "url": _safe_campus_url(discussion.get("discussionurl") or discussion.get("url")),
        "content_is_untrusted": True,
    }


async def list_forum_discussions(
    invoke: Invoke,
    *,
    forum_id: int,
    page: int = 0,
    per_page: int = 20,
    sort_order: int = -1,
    group_id: int = 0,
) -> dict[str, Any]:
    """List discussions in one forum using Moodle's native page/perpage contract."""

    forum_id = _positive_id(forum_id, "forum_id")
    if isinstance(page, bool) or not isinstance(page, int) or page < 0:
        raise ValueError("page debe ser un entero no negativo")
    _, per_page = _pagination(0, per_page)
    if isinstance(sort_order, bool) or not isinstance(sort_order, int):
        raise ValueError("sort_order debe ser un entero")
    if isinstance(group_id, bool) or not isinstance(group_id, int) or group_id < 0:
        raise ValueError("group_id debe ser un entero no negativo")
    payload = await invoke(
        "mod_forum_get_forum_discussions",
        {
            "forumid": forum_id,
            "sortorder": sort_order,
            "page": page,
            "perpage": per_page,
            "groupid": group_id,
        },
    )
    discussions = _mapping_items(payload, "discussions")
    offset = page * per_page
    return _page_result(
        [_normalise_discussion(item) for item in discussions[:per_page]],
        offset=offset,
        limit=per_page,
        warnings=_warnings(payload),
        server_side_pagination=True,
    )


def _normalise_file(file: Mapping[str, Any]) -> dict[str, Any]:
    raw_url = file.get("fileurl") or file.get("url")
    return {
        "type": _text(file.get("type"), limit=100),
        "file_name": _text(file.get("filename") or file.get("name"), limit=1_000),
        "file_path": _text(file.get("filepath"), limit=2_000),
        "mime_type": _text(file.get("mimetype"), limit=200),
        "file_size": max(0, _integer(file.get("filesize"))),
        "url": _safe_campus_url(raw_url),
        "url_was_rejected": bool(raw_url) and _safe_campus_url(raw_url) is None,
        "created_at": _timestamp(file.get("timecreated")),
        "modified_at": _timestamp(file.get("timemodified")),
        "is_external": bool(file.get("isexternalfile")),
        "content_is_untrusted": True,
    }


def _post_attachments(post: Mapping[str, Any]) -> list[dict[str, Any]]:
    values = post.get("attachments")
    if not isinstance(values, list):
        return []
    return [
        _normalise_file(value) for value in values[:MAX_NESTED_ITEMS] if isinstance(value, Mapping)
    ]


def _normalise_post(post: Mapping[str, Any]) -> dict[str, Any]:
    author = post.get("author") or post.get("user")
    author_name = author.get("fullname") if isinstance(author, Mapping) else author
    author_id = author.get("id") if isinstance(author, Mapping) else None
    attachments = _post_attachments(post)
    return {
        "post_id": _integer(post.get("id") or post.get("postid")),
        "discussion_id": _integer(post.get("discussion") or post.get("discussionid")),
        "parent_post_id": _integer(post.get("parent")),
        "subject": _text(post.get("subject"), limit=1_000),
        "message": _text(post.get("message")),
        "author": _text(post.get("userfullname") or author_name, limit=500),
        "author_user_id": _integer(post.get("userid") or author_id),
        "created_at": _timestamp(post.get("created")),
        "modified_at": _timestamp(post.get("modified")),
        "is_read": bool(post.get("postread") or post.get("isread")),
        "attachments": attachments,
        "attachments_truncated": (
            isinstance(post.get("attachments"), list)
            and len(post["attachments"]) > MAX_NESTED_ITEMS
        ),
        "content_is_untrusted": True,
    }


async def list_discussion_posts(
    invoke: Invoke,
    *,
    discussion_id: int,
    offset: int = 0,
    limit: int = 50,
    sort_by: str = "created",
    sort_direction: str = "ASC",
) -> dict[str, Any]:
    """List forum posts; Moodle returns all posts, so slicing is local and bounded."""

    discussion_id = _positive_id(discussion_id, "discussion_id")
    offset, limit = _pagination(offset, limit)
    if sort_by not in {"id", "created", "modified"}:
        raise ValueError("sort_by debe ser id, created o modified")
    sort_direction = sort_direction.upper()
    if sort_direction not in {"ASC", "DESC"}:
        raise ValueError("sort_direction debe ser ASC o DESC")
    payload = await invoke(
        "mod_forum_get_discussion_posts",
        {
            "discussionid": discussion_id,
            "sortby": sort_by,
            "sortdirection": sort_direction,
            "includeinlineattachments": False,
        },
    )
    posts = _mapping_items(payload, "posts")
    page = posts[offset : offset + limit]
    return _page_result(
        [_normalise_post(item) for item in page],
        offset=offset,
        limit=limit,
        total_count=len(posts),
        warnings=_warnings(payload),
        server_side_pagination=False,
    )


def _course_options(section_id: int | None) -> list[dict[str, str]]:
    if section_id is None:
        return []
    _positive_id(section_id, "section_id")
    return [{"name": "sectionid", "value": str(section_id)}]


def _module_files(module: Mapping[str, Any]) -> list[dict[str, Any]]:
    contents = module.get("contents")
    if not isinstance(contents, list):
        return []
    return [
        _normalise_file(content)
        for content in contents[:MAX_NESTED_ITEMS]
        if isinstance(content, Mapping)
    ]


def _normalise_module(
    module: Mapping[str, Any], section: Mapping[str, Any], course_id: int
) -> dict[str, Any]:
    contents = module.get("contents")
    files = _module_files(module)
    return {
        "course_id": course_id,
        "section_id": _integer(section.get("id")),
        "section_number": _integer(section.get("section")),
        "section_name": _text(section.get("name"), limit=500),
        "module_id": _integer(module.get("id")),
        "instance_id": _integer(module.get("instance")),
        "module_type": _text(module.get("modname"), limit=100),
        "name": _text(module.get("name"), limit=1_000),
        "description": _text(module.get("description")),
        "url": _safe_campus_url(module.get("url")),
        "visible": bool(module.get("uservisible", module.get("visible", True))),
        "availability": _text(module.get("availabilityinfo"), limit=2_000),
        "files": files,
        "files_truncated": isinstance(contents, list) and len(contents) > MAX_NESTED_ITEMS,
        "downloadable": any(file["url"] for file in files),
        "content_is_untrusted": True,
    }


async def _course_modules(
    invoke: Invoke, course_id: int, section_id: int | None
) -> tuple[Any, list[dict[str, Any]]]:
    payload = await invoke(
        "core_course_get_contents",
        {"courseid": course_id, "options": _course_options(section_id)},
    )
    sections = _mapping_items(payload, "sections", allow_list=True)
    modules: list[dict[str, Any]] = []
    for section in sections:
        raw_modules = section.get("modules")
        if not isinstance(raw_modules, list):
            continue
        modules.extend(
            _normalise_module(module, section, course_id)
            for module in raw_modules
            if isinstance(module, Mapping)
        )
    return payload, modules


async def list_course_contents(
    invoke: Invoke,
    *,
    course_id: int,
    section_id: int | None = None,
    offset: int = 0,
    limit: int = 50,
) -> dict[str, Any]:
    """List visible modules and file metadata; no file body is downloaded."""

    course_id = _positive_id(course_id, "course_id")
    offset, limit = _pagination(offset, limit)
    payload, modules = await _course_modules(invoke, course_id, section_id)
    result = _page_result(
        modules[offset : offset + limit],
        offset=offset,
        limit=limit,
        total_count=len(modules),
        warnings=_warnings(payload),
        server_side_pagination=False,
    )
    result["downloaded"] = False
    return result


async def list_downloadable_resources(
    invoke: Invoke,
    *,
    course_id: int,
    section_id: int | None = None,
    offset: int = 0,
    limit: int = 50,
) -> dict[str, Any]:
    """Flatten safe downloadable file metadata from ``core_course_get_contents``."""

    course_id = _positive_id(course_id, "course_id")
    offset, limit = _pagination(offset, limit)
    payload, modules = await _course_modules(invoke, course_id, section_id)
    resources: list[dict[str, Any]] = []
    for module in modules:
        for file in module["files"]:
            if not file["url"]:
                continue
            resources.append(
                {
                    "course_id": course_id,
                    "section_id": module["section_id"],
                    "section_name": module["section_name"],
                    "module_id": module["module_id"],
                    "module_type": module["module_type"],
                    "module_name": module["name"],
                    **file,
                    "content_is_untrusted": True,
                }
            )
    result = _page_result(
        resources[offset : offset + limit],
        offset=offset,
        limit=limit,
        total_count=len(resources),
        warnings=_warnings(payload),
        server_side_pagination=False,
    )
    result["downloaded"] = False
    return result
