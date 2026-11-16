from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from mcp_usc import setup_wizard
from mcp_usc.academic_profile import AcademicProfileError, load_academic_profile
from mcp_usc.degree_catalog import DegreeCatalogEntry
from mcp_usc.setup_wizard import (
    SERVER_NAME,
    Console,
    SetupError,
    add_to_claude_desktop,
    claude_desktop_config_path,
    cli_registration_commands,
    match_degrees,
    running_from_ephemeral_environment,
    save_profile,
    server_command,
)

_DEGREE_URL = (
    "https://www.usc.gal/gl/estudos/graos/enxenaria-e-arquitectura/grao-enxenaria-informatica"
)


def _degree(name: str, url: str = _DEGREE_URL) -> DegreeCatalogEntry:
    return DegreeCatalogEntry(name=name, url=url, area_slug="area", campus="Santiago")


def test_server_command_is_absolute_and_runs_stdio() -> None:
    command = server_command()

    assert Path(command[0]).is_absolute()
    assert command[1:] == ["-m", "mcp_usc", "serve"]


def test_ephemeral_uvx_environments_are_detected() -> None:
    assert running_from_ephemeral_environment("/Users/a/.cache/uv/archive-v0/x/bin/python")
    assert running_from_ephemeral_environment(r"C:\Users\a\AppData\Local\uv\cache\x\python.exe")
    installed = "/Users/a/.local/share/uv/tools/mcp-usc/bin/python"
    assert not running_from_ephemeral_environment(installed)


@pytest.mark.parametrize(
    ("platform", "env", "expected"),
    [
        ("darwin", {}, "Library/Application Support/Claude/claude_desktop_config.json"),
        ("win32", {"APPDATA": "/appdata"}, "/appdata/Claude/claude_desktop_config.json"),
        ("linux", {"XDG_CONFIG_HOME": "/xdg"}, "/xdg/Claude/claude_desktop_config.json"),
    ],
)
def test_claude_desktop_config_location(platform: str, env: dict, expected: str) -> None:
    path = claude_desktop_config_path(platform, env, Path("/home/ada"))

    assert path.as_posix().endswith(expected)


def test_claude_desktop_merge_keeps_other_servers_and_backs_up(tmp_path: Path) -> None:
    config = tmp_path / "Claude" / "claude_desktop_config.json"
    config.parent.mkdir()
    original = {"mcpServers": {"other": {"command": "x"}}, "theme": "dark"}
    config.write_text(json.dumps(original), encoding="utf-8")

    backup = add_to_claude_desktop(config, ["/py", "-m", "mcp_usc", "serve"])

    merged = json.loads(config.read_text(encoding="utf-8"))
    assert merged["theme"] == "dark"
    assert merged["mcpServers"]["other"] == {"command": "x"}
    assert merged["mcpServers"][SERVER_NAME] == {
        "command": "/py",
        "args": ["-m", "mcp_usc", "serve"],
    }
    assert backup is not None
    assert json.loads(backup.read_text(encoding="utf-8")) == original


def test_claude_desktop_merge_creates_a_missing_config(tmp_path: Path) -> None:
    config = tmp_path / "Claude" / "claude_desktop_config.json"

    assert add_to_claude_desktop(config, ["/py", "-m", "mcp_usc", "serve"]) is None
    assert SERVER_NAME in json.loads(config.read_text(encoding="utf-8"))["mcpServers"]


@pytest.mark.parametrize("content", ["{not json", "[]", '{"mcpServers": []}'])
def test_claude_desktop_merge_never_overwrites_an_unexpected_config(
    tmp_path: Path, content: str
) -> None:
    config = tmp_path / "claude_desktop_config.json"
    config.write_text(content, encoding="utf-8")

    with pytest.raises(SetupError):
        add_to_claude_desktop(config, ["/py"])

    assert config.read_text(encoding="utf-8") == content


def test_cli_registration_replaces_a_previous_entry() -> None:
    remove, add = cli_registration_commands("claude", ["/py", "-m", "mcp_usc", "serve"])

    assert remove[:3] == ["claude", "mcp", "remove"]
    assert add[-5:] == ["--", "/py", "-m", "mcp_usc", "serve"]
    with pytest.raises(ValueError):
        cli_registration_commands("unknown", ["/py"])


def test_degree_search_ignores_case_and_accents() -> None:
    degrees = [
        _degree("Grao en Enxeñaría Informática"),
        _degree("Grao en Matemáticas"),
        _degree("Dobre grao en Matemáticas e Física"),
    ]

    assert [d.name for d in match_degrees(degrees, "informatica")] == [
        "Grao en Enxeñaría Informática"
    ]
    assert len(match_degrees(degrees, "MATEMATICAS")) == 2
    assert [d.name for d in match_degrees(degrees, "matemáticas física")] == [
        "Dobre grao en Matemáticas e Física"
    ]
    assert match_degrees(degrees, "  ") == []


def test_saved_profile_is_loadable_by_the_server(tmp_path: Path) -> None:
    path = tmp_path / "config" / "perfil.json"

    save_profile(path, degree_url=_DEGREE_URL, course_number=3)

    profile = load_academic_profile({"USC_ACADEMIC_PROFILE_FILE": str(path)})
    assert profile is not None
    assert profile.course_number == 3


def test_invalid_profile_is_never_left_behind(tmp_path: Path) -> None:
    path = tmp_path / "perfil.json"

    with pytest.raises(AcademicProfileError):
        save_profile(path, degree_url="https://example.com/grado", course_number=3)

    assert not path.exists()


class _ScriptedConsole(Console):
    def __init__(self, answers: list[str]) -> None:
        self.answers = list(answers)
        self.output: list[str] = []
        super().__init__(ask=self._ask, ask_secret=self._ask, say=self.output.append)

    def _ask(self, question: str) -> str:
        self.output.append(question)
        return self.answers.pop(0)


async def test_wizard_end_to_end_with_public_only_access(monkeypatch, tmp_path: Path) -> None:
    profile_path = tmp_path / "config" / "perfil.json"
    desktop_config = tmp_path / "Claude" / "claude_desktop_config.json"
    desktop_config.parent.mkdir()
    commands: list[list[str]] = []

    async def no_session(settings) -> None:
        del settings
        return None

    class FakeCatalogClient:
        def __init__(self, **kwargs) -> None:
            del kwargs

        async def fetch_catalog(self, url: str):
            del url

            class Catalog:
                degrees = (_degree("Grao en Enxeñaría Informática"),)

            return Catalog()

    def fake_run(command, **kwargs):
        del kwargs
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(setup_wizard, "_authenticated_user", no_session)
    monkeypatch.setattr(setup_wizard, "UscDegreeCatalogClient", FakeCatalogClient)
    monkeypatch.setattr(setup_wizard, "default_profile_path", lambda: profile_path)
    monkeypatch.setattr(setup_wizard, "claude_desktop_config_path", lambda: desktop_config)

    console = _ScriptedConsole(["3", "informatica", "1", "2", "s", "s"])
    settings = setup_wizard.Settings(
        moodle_url="https://cv.usc.es",
        moodle_token=None,
        browser_channel="auto",
        browser_profile_dir=tmp_path,
        exam_sources=(),
    )

    result = await setup_wizard.run_setup(
        console,
        settings,
        run=fake_run,
        which=lambda name: f"/usr/bin/{name}" if name == "claude" else None,
    )

    assert result == 0

    saved = json.loads(profile_path.read_text(encoding="utf-8"))
    assert saved == {"degree_url": _DEGREE_URL, "course_number": 2}
    desktop = json.loads(desktop_config.read_text(encoding="utf-8"))
    assert desktop["mcpServers"][SERVER_NAME]["args"] == ["-m", "mcp_usc", "serve"]
    assert [command[:3] for command in commands] == [
        ["claude", "mcp", "remove"],
        ["claude", "mcp", "add"],
    ]
    assert any("Listo" in line for line in console.output)


def test_client_config_command_prints_every_client(capsys, monkeypatch) -> None:
    from mcp_usc import cli

    monkeypatch.setattr(sys, "argv", ["mcp-usc", "client-config"])
    cli.main()

    output = capsys.readouterr().out
    assert "claude mcp add" in output
    assert "codex mcp add" in output
    assert '"mcpServers"' in output


def test_profile_without_a_course_is_valid_for_students_with_mixed_courses(
    tmp_path: Path,
) -> None:
    path = tmp_path / "perfil.json"

    save_profile(path, degree_url=_DEGREE_URL)

    assert json.loads(path.read_text(encoding="utf-8")) == {"degree_url": _DEGREE_URL}
    profile = load_academic_profile({"USC_ACADEMIC_PROFILE_FILE": str(path)})
    assert profile is not None and profile.course_number is None
