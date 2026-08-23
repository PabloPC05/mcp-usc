from __future__ import annotations

import base64
from collections import deque
from pathlib import Path
from typing import Any

import pytest

from mcp_usc.assignments import (
    delete_draft_files,
    delete_submission_files,
    get_submission_status,
    list_assignments,
    prepare_submission_draft,
    remove_entire_submission,
    reopen_submission,
    replace_submission_files,
    save_submission,
    submit_for_grading,
    upload_draft_file,
)


class FakeInvoke:
    def __init__(self, responses: dict[str, list[Any]]) -> None:
        self.responses = {name: deque(values) for name, values in responses.items()}
        self.calls: list[tuple[str, dict[str, Any], str | None]] = []

    async def __call__(self, function: str, params: dict[str, Any]) -> Any:
        self.calls.append((function, dict(params), getattr(params, "client_request_id", None)))
        if function not in self.responses or not self.responses[function]:
            raise AssertionError(f"Llamada inesperada a {function}")
        response = self.responses[function].popleft()
        if isinstance(response, Exception):
            raise response
        return response


def _status(
    *,
    can_edit: bool = True,
    can_submit: bool = True,
    locked: bool = False,
    enabled: bool = True,
    submission_status: str = "draft",
    plugin_types: tuple[str, ...] = ("onlinetext",),
) -> dict[str, Any]:
    return {
        "lastattempt": {
            "submission": {
                "id": 9,
                "status": submission_status,
                "attemptnumber": 0,
                "plugins": [{"type": plugin_type} for plugin_type in plugin_types],
            },
            "submissionsenabled": enabled,
            "locked": locked,
            "graded": False,
            "canedit": can_edit,
            "cansubmit": can_submit,
        },
        "warnings": [],
    }


def _draft() -> dict[str, Any]:
    return {
        "component": "user",
        "contextid": 55,
        "userid": 7,
        "filearea": "draft",
        "itemid": 91,
        "warnings": [],
    }


@pytest.mark.asyncio
async def test_list_assignments_flattens_courses_and_status_is_normalised() -> None:
    invoke = FakeInvoke(
        {
            "mod_assign_get_assignments": [
                {
                    "courses": [
                        {
                            "id": 3,
                            "fullname": "Álxebra",
                            "assignments": [{"id": 8, "name": "Folla 1", "duedate": 99}],
                        }
                    ],
                    "warnings": [],
                }
            ],
            "mod_assign_get_submission_status": [_status(plugin_types=("file",))],
        }
    )

    listed = await list_assignments(invoke, [3, 3])
    status = await get_submission_status(invoke, 8)

    assert listed == {
        "assignments": [
            {
                "id": 8,
                "name": "Folla 1",
                "duedate": 99,
                "course_id": 3,
                "course_name": "Álxebra",
                "content_is_untrusted": True,
            }
        ],
        "warnings": [],
    }
    assert invoke.calls[0][1] == {
        "courseids": [3],
        "capabilities": [],
        "includenotenrolledcourses": False,
    }
    assert status["editable"] is True
    assert status["can_submit"] is True
    assert status["submission_status"] == "draft"


@pytest.mark.asyncio
async def test_save_online_text_checks_permission_and_uses_plain_format() -> None:
    invoke = FakeInvoke(
        {
            "mod_assign_get_submission_status": [_status()],
            "mod_assign_save_submission": [[]],
        }
    )

    result = await save_submission(
        invoke,
        8,
        online_text="Texto <sin interpretar>",
        client_request_id="save-8",
    )

    assert result["ok"] is True
    assert [call[0] for call in invoke.calls] == [
        "mod_assign_get_submission_status",
        "mod_assign_save_submission",
    ]
    assert invoke.calls[1][1] == {
        "assignmentid": 8,
        "plugindata": {
            "onlinetext_editor": {
                "text": "Texto <sin interpretar>",
                "format": 2,
                "itemid": 0,
            }
        },
    }
    assert invoke.calls[1][2] == "save-8"


@pytest.mark.asyncio
async def test_save_does_not_mutate_when_moodle_says_submission_is_locked() -> None:
    invoke = FakeInvoke(
        {"mod_assign_get_submission_status": [_status(can_edit=False, locked=True)]}
    )

    result = await save_submission(
        invoke, 8, online_text="No debe guardarse", client_request_id="locked"
    )

    assert result["ok"] is False
    assert result["code"] == "submission_locked"
    assert [call[0] for call in invoke.calls] == ["mod_assign_get_submission_status"]


@pytest.mark.asyncio
async def test_partial_plugin_save_is_rejected_before_any_mutation() -> None:
    invoke = FakeInvoke(
        {"mod_assign_get_submission_status": [_status(plugin_types=("onlinetext", "file"))]}
    )

    result = await save_submission(invoke, 8, online_text="Solo texto")

    assert result["ok"] is False
    assert result["code"] == "unsupported_submission_plugins"
    assert [call[0] for call in invoke.calls] == ["mod_assign_get_submission_status"]


@pytest.mark.asyncio
async def test_prepare_and_upload_draft_file_with_validated_path(tmp_path: Path) -> None:
    document = tmp_path / "entrega.txt"
    document.write_bytes(b"contenido")
    invoke = FakeInvoke(
        {
            "core_files_get_unused_draft_itemid": [_draft()],
            "core_files_upload": [
                {
                    "contextid": 55,
                    "component": "user",
                    "filearea": "draft",
                    "itemid": 91,
                    "filepath": "/traballo/",
                    "filename": "entrega.txt",
                    "url": "https://cv.usc.es/draftfile.php/example",
                }
            ],
        }
    )

    draft = await prepare_submission_draft(invoke, client_request_id="draft-1")
    uploaded = await upload_draft_file(
        invoke,
        draft,
        document,
        allowed_root=tmp_path,
        draft_path="/traballo/",
        client_request_id="upload-1",
    )

    assert draft["draft_item_id"] == 91
    assert uploaded["ok"] is True
    assert uploaded["size"] == 9
    upload_params = invoke.calls[1][1]
    assert upload_params["component"] == "user"
    assert upload_params["filearea"] == "draft"
    assert upload_params["filecontent"] == base64.b64encode(b"contenido").decode()
    assert "filecontent" not in uploaded
    assert invoke.calls[0][2] == "draft-1"
    assert invoke.calls[1][2] == "upload-1"


@pytest.mark.asyncio
async def test_upload_endpoint_error_is_not_reported_as_success(tmp_path: Path) -> None:
    document = tmp_path / "entrega.txt"
    document.write_bytes(b"contenido")
    invoke = FakeInvoke(
        {"core_files_upload": [{"error": "File already exists", "errortype": "filenameexist"}]}
    )

    result = await upload_draft_file(
        invoke,
        _draft(),
        document,
        allowed_root=tmp_path,
    )

    assert result["ok"] is False
    assert result["warnings"][0]["code"] == "filenameexist"


@pytest.mark.asyncio
async def test_upload_rejects_files_outside_allowed_root_before_invoke(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    invoke = FakeInvoke({})

    with pytest.raises(ValueError, match="allowed_root"):
        await upload_draft_file(invoke, _draft(), outside, allowed_root=allowed)

    assert invoke.calls == []


@pytest.mark.asyncio
async def test_delete_selected_draft_files_uses_owner_scoped_api() -> None:
    invoke = FakeInvoke(
        {"core_files_delete_draft_files": [{"parentpaths": ["/", "/sub/"], "warnings": []}]}
    )

    result = await delete_draft_files(
        invoke,
        91,
        ["entrega.txt", {"filepath": "/sub/", "filename": "anexo.pdf"}],
        client_request_id="delete-draft",
    )

    assert result["ok"] is True
    assert invoke.calls[0] == (
        "core_files_delete_draft_files",
        {
            "draftitemid": 91,
            "files": [
                {"filepath": "/", "filename": "entrega.txt"},
                {"filepath": "/sub/", "filename": "anexo.pdf"},
            ],
        },
        "delete-draft",
    )


@pytest.mark.asyncio
async def test_replace_submission_files_is_staged_then_saved(tmp_path: Path) -> None:
    first = tmp_path / "a.txt"
    second = tmp_path / "b.pdf"
    first.write_bytes(b"a")
    second.write_bytes(b"bb")
    invoke = FakeInvoke(
        {
            "mod_assign_get_submission_status": [_status(plugin_types=("file",))],
            "core_files_get_unused_draft_itemid": [_draft()],
            "core_files_upload": [
                {"itemid": 91, "filepath": "/", "filename": "a.txt"},
                {"itemid": 91, "filepath": "/", "filename": "b.pdf"},
            ],
            "mod_assign_save_submission": [[]],
        }
    )

    result = await replace_submission_files(
        invoke,
        8,
        [first, second],
        allowed_root=tmp_path,
        client_request_id="replace-8",
    )

    assert result["ok"] is True
    assert result["draft_item_id"] == 91
    assert [call[0] for call in invoke.calls] == [
        "mod_assign_get_submission_status",
        "core_files_get_unused_draft_itemid",
        "core_files_upload",
        "core_files_upload",
        "mod_assign_save_submission",
    ]
    assert invoke.calls[-1][1] == {
        "assignmentid": 8,
        "plugindata": {"files_filemanager": 91},
    }


@pytest.mark.asyncio
async def test_file_replace_rejects_unknown_plugins_before_creating_draft(tmp_path: Path) -> None:
    document = tmp_path / "a.txt"
    document.write_bytes(b"a")
    invoke = FakeInvoke({"mod_assign_get_submission_status": [_status(plugin_types=())]})

    result = await replace_submission_files(
        invoke,
        8,
        [document],
        allowed_root=tmp_path,
    )

    assert result["ok"] is False
    assert result["code"] == "submission_plugins_unknown"
    assert [call[0] for call in invoke.calls] == ["mod_assign_get_submission_status"]
    assert all(call[2].startswith("replace-8:") for call in invoke.calls[1:])


@pytest.mark.asyncio
async def test_replace_validates_total_size_before_any_network_call(tmp_path: Path) -> None:
    document = tmp_path / "large.bin"
    document.write_bytes(b"1234")
    invoke = FakeInvoke({})

    with pytest.raises(ValueError, match="límite total"):
        await replace_submission_files(
            invoke,
            8,
            [document],
            allowed_root=tmp_path,
            max_file_bytes=10,
            max_total_bytes=3,
        )

    assert invoke.calls == []


@pytest.mark.asyncio
async def test_delete_all_submission_files_uses_empty_draft_only_if_editable() -> None:
    allowed = FakeInvoke(
        {
            "mod_assign_get_submission_status": [_status(plugin_types=("file",))],
            "core_files_get_unused_draft_itemid": [_draft()],
            "mod_assign_save_submission": [[]],
        }
    )
    blocked = FakeInvoke({"mod_assign_get_submission_status": [_status(can_edit=False)]})

    result = await delete_submission_files(allowed, 8, client_request_id="clear-files")
    denied = await delete_submission_files(blocked, 8, client_request_id="deny-files")

    assert result["deleted_all_files"] is True
    assert allowed.calls[-1][1]["plugindata"] == {"files_filemanager": 91}
    assert denied["ok"] is False
    assert [call[0] for call in blocked.calls] == ["mod_assign_get_submission_status"]


@pytest.mark.asyncio
async def test_submit_for_grading_propagates_clear_moodle_warning() -> None:
    invoke = FakeInvoke(
        {
            "mod_assign_get_submission_status": [_status()],
            "mod_assign_submit_for_grading": [
                [
                    {
                        "itemid": 8,
                        "warningcode": "couldnotsubmitforgrading",
                        "message": "Statement required",
                    }
                ]
            ],
        }
    )

    result = await submit_for_grading(
        invoke,
        8,
        accept_submission_statement=False,
        client_request_id="submit-8",
    )

    assert result["ok"] is False
    assert result["code"] == "couldnotsubmitforgrading"
    assert result["reason"] == "Statement required"
    assert invoke.calls[-1][1]["acceptsubmissionstatement"] is False
    assert invoke.calls[-1][2] == "submit-8"


@pytest.mark.asyncio
async def test_remove_and_reopen_return_clear_results_when_not_permitted() -> None:
    remove_invoke = FakeInvoke({"mod_assign_get_submission_status": [_status(can_edit=False)]})
    reopen_invoke = FakeInvoke(
        {
            "mod_assign_get_submission_status": [
                _status(can_edit=False, can_submit=False, submission_status="submitted")
            ]
        }
    )

    removed = await remove_entire_submission(remove_invoke, 8, 7, client_request_id="remove-8")
    reopened = await reopen_submission(reopen_invoke, 8, client_request_id="reopen-8")

    assert removed["ok"] is False
    assert removed["code"] == "submission_not_editable"
    assert reopened["ok"] is False
    assert reopened["code"] == "reopen_requires_teacher"
    assert reopened["mutated"] is False
    assert [call[0] for call in remove_invoke.calls] == ["mod_assign_get_submission_status"]
    assert [call[0] for call in reopen_invoke.calls] == ["mod_assign_get_submission_status"]


@pytest.mark.asyncio
async def test_remove_entire_submission_calls_moodle_45_api_with_request_id() -> None:
    invoke = FakeInvoke(
        {
            "mod_assign_get_submission_status": [_status()],
            "mod_assign_remove_submission": [{"status": False, "warnings": []}],
        }
    )

    result = await remove_entire_submission(invoke, 8, 7, client_request_id="remove-entire")

    assert result["ok"] is False
    assert result["code"] == "remove_not_allowed"
    assert invoke.calls[-1] == (
        "mod_assign_remove_submission",
        {"userid": 7, "assignid": 8},
        "remove-entire",
    )
