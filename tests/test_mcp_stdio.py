import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def test_stdio_server_advertises_read_tools_and_one_confirmed_write() -> None:
    parameters = StdioServerParameters(command=sys.executable, args=["-m", "mcp_usc", "serve"])
    async with (
        stdio_client(parameters) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        response = await session.list_tools()

    names = {tool.name for tool in response.tools}
    assert names == {
        "auth_status",
        "check_submission_reopen",
        "delete_submission_files",
        "finish_quiz",
        "get_quiz_attempt_page",
        "get_quiz_attempt_summary",
        "get_submission_status",
        "get_work_item",
        "list_announcements",
        "list_assignments",
        "list_conversation_messages",
        "list_conversations",
        "list_course_contents",
        "list_course_resources",
        "list_courses",
        "list_discussion_posts",
        "list_exam_sources",
        "list_forum_discussions",
        "list_forums",
        "list_pending_work",
        "list_quiz_attempts",
        "list_quizzes",
        "list_upcoming_events",
        "preview_delete_submission_files",
        "preview_finish_quiz",
        "preview_message",
        "preview_remove_submission",
        "preview_replace_submission_files",
        "preview_save_online_submission",
        "preview_save_quiz_answers",
        "preview_start_quiz",
        "preview_submit_assignment",
        "read_course_resource",
        "remove_submission",
        "replace_submission_files",
        "save_online_submission",
        "save_quiz_answers",
        "search_exam_dates",
        "search_message_contacts",
        "send_message",
        "start_quiz",
        "submit_assignment",
    }
    by_name = {tool.name: tool for tool in response.tools}
    write_names = {
        "delete_submission_files",
        "finish_quiz",
        "remove_submission",
        "replace_submission_files",
        "save_online_submission",
        "save_quiz_answers",
        "send_message",
        "start_quiz",
        "submit_assignment",
    }
    preview_names = {
        "preview_delete_submission_files",
        "preview_finish_quiz",
        "preview_message",
        "preview_remove_submission",
        "preview_replace_submission_files",
        "preview_save_online_submission",
        "preview_save_quiz_answers",
        "preview_start_quiz",
        "preview_submit_assignment",
    }
    assert all(by_name[name].annotations.readOnlyHint is False for name in write_names)
    assert all(by_name[name].annotations.destructiveHint is True for name in write_names)
    assert all(by_name[name].annotations.idempotentHint is False for name in preview_names)
    assert all(
        tool.annotations.readOnlyHint is True
        for name, tool in by_name.items()
        if name not in write_names
    )
