"""Downloading course files: browser-like opening, incremental saving, confirmation, CLI."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from mcp_usc import service as service_module
from mcp_usc.campus import (
    AuthenticationRequired,
    CampusCapabilityUnavailable,
    CampusProtocolError,
    HttpSessionMoodleGateway,
)
from mcp_usc.cli_commands import CliError, match_course
from mcp_usc.confirmations import ACTION_CONFIRMATIONS
from mcp_usc.course_downloads import (
    MANIFEST_NAME,
    DownloadItem,
    course_folder_name,
    download_items,
    safe_component,
)
from mcp_usc.service import UscService
from mcp_usc.settings import Settings

BASE = "https://cv.usc.es"


class _Store:
    def __init__(self, value: str) -> None:
        self.value = value

    def get(self, name: str) -> str:
        del name
        return self.value

    def set(self, name: str, value: str) -> None:
        del name
        self.value = value


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        moodle_url=BASE,
        moodle_token=None,
        browser_channel="auto",
        browser_profile_dir=tmp_path,
        exam_sources=(),
        download_root=tmp_path / "descargas",
    )


def _gateway(tmp_path: Path) -> HttpSessionMoodleGateway:
    return HttpSessionMoodleGateway(_settings(tmp_path), _Store("cookie-value-1234"))  # type: ignore[arg-type]


# --- Opening an activity like the browser ---------------------------------------------


@respx.mock
async def test_resource_is_opened_and_its_pluginfile_redirect_followed(tmp_path: Path) -> None:
    view = respx.get(f"{BASE}/mod/resource/view.php", params={"id": "77", "redirect": "1"}).mock(
        return_value=httpx.Response(
            303,
            headers={"location": f"{BASE}/pluginfile.php/9/mod_resource/content/2/Tema%201.pdf"},
        )
    )
    respx.get(f"{BASE}/pluginfile.php/9/mod_resource/content/2/Tema%201.pdf").mock(
        return_value=httpx.Response(
            200,
            content=b"%PDF-1.7",
            headers={
                "content-type": "application/pdf",
                "content-disposition": "inline; filename*=UTF-8''Tema%201%3A%20intro.pdf",
            },
        )
    )

    content, media, final_url, name = await _gateway(tmp_path).open_activity_file(
        77, "resource", 1024
    )

    assert view.called
    assert (content, media) == (b"%PDF-1.7", "application/pdf")
    assert final_url.endswith("/Tema%201.pdf")
    assert name == "Tema 1_ intro.pdf"  # the colon is not portable to Windows


@respx.mock
async def test_folder_is_downloaded_as_a_zip(tmp_path: Path) -> None:
    respx.get(f"{BASE}/mod/folder/download_folder.php", params={"id": "88"}).mock(
        return_value=httpx.Response(
            200,
            content=b"PK\x03\x04",
            headers=[
                (b"content-type", b"application/zip"),
                # Servers send raw UTF-8 bytes; httpx only accepts ASCII text here.
                (
                    b"content-disposition",
                    'attachment; filename="Prácticas-20261117.zip"'.encode(),
                ),
            ],
        )
    )

    content, _, _, name = await _gateway(tmp_path).open_activity_file(88, "folder", 1024)

    assert content.startswith(b"PK")
    assert name == "Prácticas-20261117.zip"


@pytest.mark.parametrize(
    ("location", "error"),
    [
        ("https://example.com/file.pdf", CampusProtocolError),
        (f"{BASE}/mod/url/view.php?id=5", CampusProtocolError),
        (f"{BASE}/login/index.php", AuthenticationRequired),
    ],
)
@respx.mock
async def test_redirects_may_only_lead_to_campus_files(
    tmp_path: Path, location: str, error: type[Exception]
) -> None:
    respx.get(f"{BASE}/mod/resource/view.php").mock(
        return_value=httpx.Response(303, headers={"location": location})
    )

    with pytest.raises(error):
        await _gateway(tmp_path).open_activity_file(77, "resource", 1024)


@respx.mock
async def test_an_error_page_is_not_saved_as_the_file(tmp_path: Path) -> None:
    respx.get(f"{BASE}/mod/folder/download_folder.php").mock(
        return_value=httpx.Response(
            200,
            text="<html><body>Non se pode descargar</body></html>",
            headers={"content-type": "text/html; charset=utf-8"},
        )
    )

    with pytest.raises(CampusCapabilityUnavailable):
        await _gateway(tmp_path).open_activity_file(88, "folder", 1024)


async def test_only_files_and_folders_are_opened(tmp_path: Path) -> None:
    with pytest.raises(CampusCapabilityUnavailable):
        await _gateway(tmp_path).open_activity_file(5, "url", 1024)


# --- Saving files incrementally -------------------------------------------------------


class _FakeDownloads:
    def __init__(self) -> None:
        self.opened: list[int] = []

    async def open_activity_file(self, cmid: int, kind: str, max_bytes: int):
        del kind, max_bytes
        self.opened.append(cmid)
        names = {1: "tema1.pdf", 2: "tema1.pdf", 3: "../../escapar.txt", 4: "CON"}
        return f"contenido {cmid}".encode(), "application/pdf", f"{BASE}/x", names[cmid]


def _item(cmid: int, section: str = "Tema 1") -> DownloadItem:
    return DownloadItem(str(cmid), cmid, "resource", f"Material {cmid}", section)


async def test_downloads_are_saved_by_section_and_never_repeated(tmp_path: Path) -> None:
    gateway = _FakeDownloads()
    folder = tmp_path / "IA"

    first = await download_items(gateway, [_item(1), _item(2), _item(3, "Prácticas")], folder)
    second = await download_items(gateway, [_item(1), _item(2), _item(3, "Prácticas")], folder)

    assert [entry["path"] for entry in first["downloaded"]] == [
        "01 Tema 1/tema1.pdf",
        "01 Tema 1/tema1 (2).pdf",
        "02 Prácticas/_.._escapar.txt",  # no separators, no leading dots
    ]
    assert (folder / "01 Tema 1" / "tema1 (2).pdf").read_bytes() == b"contenido 2"
    assert second["downloaded"] == [] and len(second["already_downloaded"]) == 3
    assert gateway.opened == [1, 2, 3]  # nothing re-opened, so nothing re-recorded
    manifest = json.loads((folder / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert set(manifest["items"]) == {"1", "2", "3"}


async def test_force_downloads_again(tmp_path: Path) -> None:
    gateway = _FakeDownloads()
    await download_items(gateway, [_item(1)], tmp_path)
    await download_items(gateway, [_item(1)], tmp_path, force=True)

    assert gateway.opened == [1, 1]


def test_names_are_portable_path_components() -> None:
    assert safe_component("a/b\\c:d*e?", fallback="x") == "a_b_c_d_e_"
    assert safe_component("CON", fallback="archivo") == "archivo"
    assert safe_component("  . ", fallback="archivo") == "archivo"
    assert course_folder_name("Intelixencia Artificial [G4012328] [2026/2027]", 7) == (
        "Intelixencia Artificial"
    )


# --- MCP flow: preview, confirmation, download ----------------------------------------


class _CourseGateway(_FakeDownloads):
    def session_forms(self) -> object:
        return object()  # session transport

    async def status(self) -> dict[str, Any]:
        return {"authenticated": True, "user_id": 5}

    async def list_courses(self, include_archived: bool = False) -> list[dict[str, Any]]:
        del include_archived
        return [{"id": 41, "fullname": "Redes [G4012224] [2026/2027]", "shortname": "R"}]


@pytest.fixture
def download_service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> UscService:
    ACTION_CONFIRMATIONS.clear()

    async def items(gateway: Any, course_id: int, *, session: bool):
        del gateway, course_id
        assert session is True
        return [_item(1), _item(2)], {"url": 3}

    monkeypatch.setattr(service_module, "list_download_items", items)
    service = UscService(_settings(tmp_path))
    gateway = _CourseGateway()
    service._campus = lambda: gateway  # type: ignore[method-assign]
    return service


async def test_preview_downloads_nothing_and_warns_about_recorded_views(
    download_service: UscService, tmp_path: Path
) -> None:
    preview = await download_service.preview_download_course_files(41)

    assert preview["count"] == 2
    assert preview["records_views"] is True
    assert Path(preview["folder"]).parts[-2:] == ("descargas", "Redes")
    assert preview["not_downloadable"] == {"url": 3}
    assert download_service._campus().opened == []  # type: ignore[attr-defined]
    assert not (tmp_path / "descargas").exists()

    result = await download_service.download_course_files(41, None, preview["confirmation_token"])

    assert len(result["downloaded"]) == 2
    assert (tmp_path / "descargas" / "Redes" / "01 Tema 1" / "tema1.pdf").is_file()


async def test_download_without_a_valid_confirmation_never_opens_anything(
    download_service: UscService,
) -> None:
    with pytest.raises(ValueError, match="confirmación"):
        await download_service.download_course_files(41, None, "inventado")

    assert download_service._campus().opened == []  # type: ignore[attr-defined]


async def test_unknown_module_ids_are_rejected_before_any_token(
    download_service: UscService,
) -> None:
    with pytest.raises(ValueError, match="99"):
        await download_service.preview_download_course_files(41, [1, 99])


# --- CLI: choosing a course -----------------------------------------------------------

_COURSES = [
    {"id": 1, "full_name": "Intelixencia Artificial [G4012328] [2026/2027]", "short_name": "IA"},
    {"id": 6, "full_name": "Variable Complexa [G1011422] [2026/2027]", "short_name": "VC"},
    {"id": 7, "full_name": "Coordinación 3º Curso [G1011P01] [2026/2027]", "short_name": "C"},
    {"id": 2, "full_name": "Redes [G4012224] [2026/2027]", "short_name": "R"},
    {"id": 3, "full_name": "Redes [G4012224] [2025/2026]", "short_name": "R"},
    {"id": 4, "full_name": "Sistemas Operativos I [G4012223] [2026/2027]", "short_name": "SO1"},
    {"id": 5, "full_name": "Sistemas Operativos II [G4012227] [2026/2027]", "short_name": "SO2"},
]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("1", 1),
        ("G4012224", 2),
        ("redes", 2),
        ("intelixencia", 1),
        ("sistemas operativos ii", 5),
        ("ia", 1),  # initials, not "ia" inside other words
        ("soi", 4),
        ("intel", 1),
    ],
)
def test_course_is_chosen_by_id_code_or_name_preferring_this_year(
    query: str, expected: int
) -> None:
    assert match_course(_COURSES, query, "2026/2027")["id"] == expected


def test_ambiguous_or_unknown_course_explains_the_options() -> None:
    with pytest.raises(CliError, match="varias"):
        match_course(_COURSES, "sistemas", "2026/2027")
    with pytest.raises(CliError, match="ninguna"):
        match_course(_COURSES, "química", "2026/2027")


def test_settings_default_download_root_is_the_user_downloads_folder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("USC_DOWNLOAD_ROOT", raising=False)
    settings = Settings.from_env()

    assert settings.download_root is not None
    assert settings.download_root.name == "mcp-usc"
    replaced = dataclasses.replace(settings, download_root=Path("/tmp/x"))
    assert replaced.download_root == Path("/tmp/x")
