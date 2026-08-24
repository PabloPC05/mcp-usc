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
        "call_student_read",
        "cancel_choice_response",
        "check_submission_reopen",
        "create_forum_discussion",
        "create_personal_calendar_event",
        "delete_personal_calendar_event",
        "delete_submission_files",
        "finish_quiz",
        "execute_student_action",
        "get_my_completion",
        "get_my_grades",
        "get_my_preferences",
        "get_my_profile",
        "get_private_files_info",
        "get_quiz_attempt_page",
        "get_quiz_attempt_review",
        "get_quiz_attempt_summary",
        "get_quiz_best_grade",
        "get_submission_status",
        "get_work_item",
        "inspect_quiz_attempt",
        "inspect_submission_status",
        "inspect_discussion_posts",
        "list_announcements",
        "list_assignments",
        "list_calendar_events",
        "list_conversation_messages",
        "list_conversations",
        "list_course_contents",
        "list_course_participants",
        "list_course_resources",
        "list_courses",
        "list_discussion_posts",
        "list_exam_sources",
        "list_forum_discussions",
        "list_forums",
        "list_my_badges",
        "list_my_groups",
        "list_messages",
        "list_notifications",
        "list_pending_work",
        "list_quiz_attempts",
        "list_quizzes",
        "list_student_capabilities",
        "list_upcoming_events",
        "preview_delete_submission_files",
        "preview_cancel_choice_response",
        "preview_create_forum_discussion",
        "preview_create_personal_calendar_event",
        "preview_delete_personal_calendar_event",
        "preview_finish_quiz",
        "preview_inspect_quiz_attempt",
        "preview_inspect_submission_status",
        "preview_inspect_discussion_posts",
        "preview_message",
        "preview_remove_submission",
        "preview_reply_forum_post",
        "preview_replace_submission_files",
        "preview_save_online_submission",
        "preview_save_quiz_answers",
        "preview_start_quiz",
        "preview_student_action",
        "preview_submit_choice_response",
        "preview_submit_assignment",
        "read_course_resource",
        "remove_submission",
        "replace_submission_files",
        "reply_forum_post",
        "save_online_submission",
        "save_quiz_answers",
        "search_exam_dates",
        "search_message_contacts",
        "send_message",
        "start_quiz",
        "submit_assignment",
        "submit_choice_response",
    }
    by_name = {tool.name: tool for tool in response.tools}
    write_names = {
        "cancel_choice_response",
        "create_forum_discussion",
        "create_personal_calendar_event",
        "delete_personal_calendar_event",
        "delete_submission_files",
        "finish_quiz",
        "inspect_quiz_attempt",
        "inspect_discussion_posts",
        "remove_submission",
        "replace_submission_files",
        "reply_forum_post",
        "save_online_submission",
        "save_quiz_answers",
        "send_message",
        "start_quiz",
        "submit_assignment",
        "submit_choice_response",
        "execute_student_action",
    }
    preview_names = {
        "preview_cancel_choice_response",
        "preview_create_forum_discussion",
        "preview_create_personal_calendar_event",
        "preview_delete_personal_calendar_event",
        "preview_delete_submission_files",
        "preview_finish_quiz",
        "preview_inspect_quiz_attempt",
        "preview_inspect_submission_status",
        "preview_inspect_discussion_posts",
        "preview_message",
        "preview_remove_submission",
        "preview_reply_forum_post",
        "preview_replace_submission_files",
        "preview_save_online_submission",
        "preview_save_quiz_answers",
        "preview_start_quiz",
        "preview_submit_assignment",
        "preview_student_action",
        "preview_submit_choice_response",
    }
    assert all(by_name[name].annotations.readOnlyHint is False for name in write_names)
    assert all(by_name[name].annotations.destructiveHint is True for name in write_names)
    assert all(by_name[name].annotations.idempotentHint is False for name in preview_names)
    stateful_read_names = {"inspect_submission_status"}
    assert all(by_name[name].annotations.readOnlyHint is False for name in stateful_read_names)
    assert all(by_name[name].annotations.destructiveHint is False for name in stateful_read_names)
    assert all(
        tool.annotations.readOnlyHint is True
        for name, tool in by_name.items()
        if name not in write_names | stateful_read_names
    )
