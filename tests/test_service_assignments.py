from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from mcp_usc.confirmations import ACTION_CONFIRMATIONS
from mcp_usc.service import UscService
from mcp_usc.settings import Settings


def _editable_status(plugin_type: str) -> dict[str, Any]:
    return {
        "lastattempt": {
            "submission": {
                "id": 9,
                "status": "draft",
                "attemptnumber": 0,
                "plugins": [{"type": plugin_type}],
            },
            "submissionsenabled": True,
            "locked": False,
            "graded": False,
            "canedit": True,
            "cansubmit": True,
        },
        "warnings": [],
    }


class FakeAssignmentGateway:
    def __init__(self, plugin_type: str = "onlinetext") -> None:
        self.calls: list[tuple[str, Mapping[str, Any]]] = []
        self.plugin_type = plugin_type

    async def status(self) -> dict[str, Any]:
        return {"authenticated": True, "user_id": 5}

    async def invoke(self, function: str, arguments: Mapping[str, Any]) -> Any:
        self.calls.append((function, arguments))
        if function == "mod_assign_get_submission_status":
            return _editable_status(self.plugin_type)
        if function in {"mod_assign_save_submission", "mod_assign_submit_for_grading"}:
            return []
        if function == "mod_assign_remove_submission":
            return {"status": True, "warnings": []}
        raise AssertionError(function)


@pytest.fixture(autouse=True)
def clear_confirmations() -> None:
    ACTION_CONFIRMATIONS.clear()


def _service_with(gateway: FakeAssignmentGateway, root: Path) -> UscService:
    service = object.__new__(UscService)
    service.settings = Settings(
        moodle_url="https://cv.usc.es",
        moodle_token=None,
        browser_channel="chromium",
        browser_profile_dir=root / "browser",
        exam_sources=(),
        upload_root=root,
        max_upload_bytes=1024,
    )
    service._campus = lambda: gateway
    return service


async def test_online_text_preview_does_not_write_and_confirmation_saves(tmp_path: Path) -> None:
    gateway = FakeAssignmentGateway()
    service = _service_with(gateway, tmp_path)

    preview = await service.preview_save_online_submission(8, "Mi respuesta")

    assert [call[0] for call in gateway.calls] == ["mod_assign_get_submission_status"]
    result = await service.save_online_submission(
        8,
        "Mi respuesta",
        preview["confirmation_token"],
    )

    assert result["ok"] is True
    assert [call[0] for call in gateway.calls[-2:]] == [
        "mod_assign_get_submission_status",
        "mod_assign_save_submission",
    ]


async def test_changed_file_after_preview_is_rejected_before_upload(tmp_path: Path) -> None:
    document = tmp_path / "entrega.txt"
    document.write_text("primera versión", encoding="utf-8")
    gateway = FakeAssignmentGateway("file")
    service = _service_with(gateway, tmp_path)
    preview = await service.preview_replace_submission_files(8, ["entrega.txt"])
    call_count = len(gateway.calls)

    document.write_text("segunda versión", encoding="utf-8")

    with pytest.raises(ValueError, match="Token de confirmación inválido"):
        await service.replace_submission_files(
            8,
            ["entrega.txt"],
            preview["confirmation_token"],
        )
    assert len(gateway.calls) == call_count


async def test_submit_assignment_has_separate_preview_and_write(tmp_path: Path) -> None:
    gateway = FakeAssignmentGateway()
    service = _service_with(gateway, tmp_path)
    preview = await service.preview_submit_assignment(8, True)

    assert [call[0] for call in gateway.calls] == ["mod_assign_get_submission_status"]
    result = await service.submit_assignment(8, True, preview["confirmation_token"])

    assert result["ok"] is True
    assert gateway.calls[-1][0] == "mod_assign_submit_for_grading"


async def test_remove_entire_submission_uses_current_user_only(tmp_path: Path) -> None:
    gateway = FakeAssignmentGateway()
    service = _service_with(gateway, tmp_path)
    preview = await service.preview_remove_submission(8)

    result = await service.remove_submission(8, preview["confirmation_token"])

    assert result["removed"] is True
    assert gateway.calls[-1][1] == {"userid": 5, "assignid": 8}
