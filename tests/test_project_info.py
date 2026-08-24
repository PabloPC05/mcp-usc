from __future__ import annotations

from mcp_usc import __version__
from mcp_usc.project_info import CAPABILITY_TOOL_GROUPS, TOOL_INVENTORY, project_overview


def test_project_overview_is_network_free_and_explains_boundaries() -> None:
    overview = project_overview()

    assert overview["version"] == __version__ == "0.8.0"
    assert overview["network_contacted"] is False
    assert overview["tool_inventory"] == TOOL_INVENTORY
    assert overview["tool_inventory"]["total"] == 84
    assert sum(len(names) for names in CAPABILITY_TOOL_GROUPS.values()) == 84
    assert any("independiente" in item for item in overview["boundaries"])
