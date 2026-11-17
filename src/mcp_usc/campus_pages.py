"""Reading the Campus pages that have no AJAX function at the USC.

With a MoodleSession the USC only exposes part of Moodle over AJAX: grades, a forum's
discussion list and a quiz's attempts are only on their web pages. These functions turn
those pages into data (no network here); :class:`MoodleSessionForms.read_page` fetches
them. Opening any of these pages is what a student does on the web, and Moodle logs it
as a view, so the service only reads them after a confirmation.

Everything returned is text written by other people: untrusted.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup, Tag

_LEVEL = re.compile(r"\blevel(\d+)\b")
_DIGITS = re.compile(r"\d+")
_MAX_ROWS = 500
_MAX_CELL = 600
_MAX_FEEDBACK = 2_000


def _text(element: Tag | None, limit: int = _MAX_CELL) -> str:
    if element is None:
        return ""
    return " ".join(element.get_text(" ", strip=True).split())[:limit]


def _query_int(href: Any, name: str) -> int | None:
    values = parse_qs(urlparse(str(href or "")).query).get(name) or []
    return int(values[0]) if values and values[0].isdigit() else None


def _main(html: str) -> Tag:
    soup = BeautifulSoup(html, "html.parser")
    return soup.select_one("#region-main") or soup


def _heading(main: Tag) -> str:
    """The page's title: the activity name in Moodle 4's page header."""

    root = main
    while root.parent is not None:
        root = root.parent
    for scope, selector in (
        (root, ".page-header-headings h1"),
        (main, "h2"),
        (root, "#page-header h1"),
        (main, "h1"),
    ):
        element = scope.select_one(selector)
        if element is not None and _text(element):
            return _text(element, 200)
    return ""


# --- «Cualificacións» -----------------------------------------------------------------------


def parse_grade_overview(html: str) -> list[dict[str, Any]]:
    """Every course with its course total, as the overview report lists them."""

    main = _main(html)
    table = main.select_one("table#overview-grade") or main.select_one("table.generaltable")
    courses: list[dict[str, Any]] = []
    for row in (table.select("tr") if table else [])[:_MAX_ROWS]:
        cells = row.select("td")
        if len(cells) < 2 or "emptyrow" in (row.get("class") or []):
            continue
        link = cells[0].select_one("a[href]")
        courses.append(
            {
                "course_id": _query_int(link.get("href") if link else None, "id"),
                "course_name": _text(cells[0], 300),
                "grade": _text(cells[1], 60) or None,
            }
        )
    return courses


def parse_grade_report(html: str) -> dict[str, Any]:
    """A course's user report: categories, items and totals with grade, range, feedback."""

    main = _main(html)
    table = main.select_one("table.user-grade")
    items: list[dict[str, Any]] = []
    for row in (table.select("tr") if table else [])[:_MAX_ROWS]:
        head = row.select_one("th.column-itemname")
        if head is None or "header" in (head.get("class") or []):
            continue  # the table's own heading row
        classes = head.get("class") or []
        level_match = _LEVEL.search(" ".join(classes))
        if "category" in classes:
            kind = "category"
        elif "baggt" in classes or "baggb" in classes:
            kind = "total"
        else:
            kind = "item"
        title = head.select_one(".rowtitle") or head
        link = head.select_one("a[href]")
        module_label = head.select_one("span.small, .text-uppercase")

        def cell(name: str, limit: int = 120, row: Tag = row) -> str | None:
            return _text(row.select_one(f"td.column-{name}"), limit) or None

        items.append(
            {
                "kind": kind,
                "level": int(level_match.group(1)) if level_match else 1,
                "name": _text(title, 300),
                "module": _text(module_label, 60) or None,
                "url": str(link.get("href")) if link else None,
                "weight": cell("weight"),
                "grade": cell("grade"),
                "range": cell("range"),
                "percentage": cell("percentage"),
                "feedback": cell("feedback", _MAX_FEEDBACK),
                "contribution": cell("contributiontocoursetotal"),
            }
        )
    course = next((item["name"] for item in items if item["kind"] == "category"), "")
    totals = [item for item in items if item["kind"] == "total"]
    course_total = min(totals, key=lambda item: item["level"]) if totals else None
    return {
        "course_name": course,
        "items": items,
        "course_total": course_total["grade"] if course_total else None,
        "content_is_untrusted": True,
    }


# --- a forum's discussion list ------------------------------------------------------------


def _person_and_date(cell: Tag | None) -> tuple[str, str]:
    if cell is None:
        return "", ""
    lines = [_text(part, 120) for part in cell.select(".author-info > div")]
    lines = [line for line in lines if line]
    if not lines:
        return _text(cell, 120), ""
    return lines[0], (lines[1] if len(lines) > 1 else "")


def parse_forum_page(html: str) -> dict[str, Any]:
    """The discussions a forum page lists: subject, who started it, last post, replies."""

    main = _main(html)
    discussions: list[dict[str, Any]] = []
    for row in main.select("[data-region='discussion-list-item']")[:_MAX_ROWS]:
        raw_id = str(row.get("data-discussionid") or "")
        if not raw_id.isdigit():
            continue
        topic = row.select_one("th.topic a[href]")
        author_cell = row.select_one("td.author")
        cells = row.select("td")
        last_cell = None
        if author_cell is not None and author_cell in cells:
            position = cells.index(author_cell)
            last_cell = cells[position + 1] if position + 1 < len(cells) else None
        replies_cell = next(
            (cell for cell in cells if _DIGITS.fullmatch(_text(cell.select_one("span")) or "")),
            None,
        )
        unread = row.select_one("[data-region='unread-count'], .badge.bg-primary")
        started_by, started_at = _person_and_date(author_cell)
        last_by, last_at = _person_and_date(last_cell)

        def flag(region: str, row: Tag = row) -> bool:
            label = row.select_one(f"[data-region='{region}']")
            return label is not None and not label.has_attr("hidden")

        discussions.append(
            {
                "discussion_id": int(raw_id),
                "subject": (str(topic.get("title") or "") or _text(topic, 300)) if topic else "",
                "url": str(topic.get("href")) if topic else None,
                "started_by": started_by,
                "started_at": started_at,
                "last_post_by": last_by,
                "last_post_at": last_at,
                "replies": int(_text(replies_cell.select_one("span"))) if replies_cell else 0,
                "unread": int(_text(unread)) if unread and _text(unread).isdigit() else 0,
                "pinned": flag("pinned-label") or "pinned" in (row.get("class") or []),
                "locked": flag("locked-label"),
            }
        )
    following = main.select_one("nav.pagination [aria-label*='eguinte'], .page-item a[rel='next']")
    return {
        "forum_name": _heading(main),
        "discussions": discussions,
        "has_more": following is not None,
        "content_is_untrusted": True,
    }


# --- a quiz page ----------------------------------------------------------------------------


def parse_quiz_page(html: str) -> dict[str, Any]:
    """Dates, limits, your attempts and your grade, as the quiz page shows them."""

    main = _main(html)
    info: dict[str, str] = {}
    for paragraph in main.select(".quizinfo p")[:20]:
        label, _, value = _text(paragraph).partition(":")
        if value:
            info[label.strip()] = value.strip()
    dates: dict[str, str] = {}
    for line in main.select("[data-region='activity-dates'] > div")[:10]:
        strong = line.select_one("strong")
        label = _text(strong).rstrip(":")
        value = _text(line)[len(_text(strong)) :].strip() if strong else _text(line)
        if label:
            dates[label] = value
    attempts: list[dict[str, str]] = []
    for table in main.select("table.quizreviewsummary")[:50]:
        attempt: dict[str, str] = {}
        for row in table.select("tr"):
            head, value = row.select_one("th"), row.select_one("td")
            if head is not None and value is not None:
                attempt[_text(head, 80)] = _text(value, 200)
        if attempt:
            attempts.append(attempt)
    feedback = main.select_one("#feedback")
    start = main.select_one("form[action*='startattempt.php']")
    return {
        "name": _heading(main),
        "info": info,
        "dates": dates,
        "attempts": attempts,
        "final_grade": _text(feedback, 300) or None,
        "can_attempt": start is not None,
        "content_is_untrusted": True,
    }
