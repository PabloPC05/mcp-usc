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
        "get_work_item",
        "list_announcements",
        "list_courses",
        "list_exam_sources",
        "list_pending_work",
        "list_upcoming_events",
        "preview_message",
        "search_exam_dates",
        "search_message_contacts",
        "send_message",
    }
    by_name = {tool.name: tool for tool in response.tools}
    assert by_name["send_message"].annotations.readOnlyHint is False
    assert by_name["send_message"].annotations.destructiveHint is True
    assert by_name["preview_message"].annotations.idempotentHint is False
    assert all(
        tool.annotations.readOnlyHint is True
        for name, tool in by_name.items()
        if name != "send_message"
    )
