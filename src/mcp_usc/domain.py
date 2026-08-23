from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .security import html_to_text

MADRID = ZoneInfo("Europe/Madrid")


def _timestamp(value: Any) -> str | None:
    if value in (None, "", 0, "0"):
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=MADRID).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def normalise_course(course: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(course["id"]),
        "short_name": course.get("shortname") or course.get("short_name") or "",
        "full_name": html_to_text(course.get("fullname") or course.get("full_name")),
        "url": course.get("viewurl") or course.get("url"),
        "start_at": _timestamp(course.get("startdate")),
        "end_at": _timestamp(course.get("enddate")),
        "visible": not bool(course.get("hidden", False)),
        "content_is_untrusted": True,
    }


def normalise_event(event: dict[str, Any]) -> dict[str, Any]:
    course = event.get("course") or {}
    action = event.get("action") or {}
    return {
        "event_id": int(event.get("id", 0)),
        "name": html_to_text(event.get("name")),
        "description": html_to_text(event.get("description"), limit=12_000),
        "event_type": event.get("eventtype") or event.get("event_type"),
        "module": event.get("modulename") or event.get("module"),
        "course_id": event.get("courseid") or course.get("id"),
        "course_name": html_to_text(course.get("fullname") or course.get("fullnamedisplay")),
        "starts_at": _timestamp(event.get("timestart")),
        "sort_at": _timestamp(event.get("timesort")),
        "duration_seconds": event.get("timeduration", 0),
        "url": event.get("url"),
        "action_name": html_to_text(action.get("name")),
        "action_url": action.get("url"),
        "actionable": action.get("actionable"),
        "time_left": html_to_text(event.get("timeusermidnight") or event.get("formattedtime")),
        "content_is_untrusted": True,
    }


def normalise_announcement(
    discussion: dict[str, Any], *, course_id: int, course_name: str, forum_name: str
) -> dict[str, Any]:
    author = discussion.get("userfullname") or discussion.get("author") or discussion.get("user")
    discussion_id = discussion.get("discussion") or discussion.get("discussionid")
    discussion_id = discussion_id or discussion.get("id")
    return {
        "discussion_id": discussion_id,
        "course_id": course_id,
        "course_name": course_name,
        "forum": forum_name,
        "title": html_to_text(discussion.get("name") or discussion.get("subject")),
        "author": html_to_text(str(author or "")),
        "message": html_to_text(discussion.get("message"), limit=12_000),
        "created_at": _timestamp(discussion.get("created")),
        "modified_at": _timestamp(discussion.get("timemodified") or discussion.get("modified")),
        "url": discussion.get("discussionurl") or discussion.get("url"),
        "content_is_untrusted": True,
    }
