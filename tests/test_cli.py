from __future__ import annotations

import json
import sys

import pytest

from mcp_usc import cli


def test_import_session_cli_uses_hidden_prompt_and_never_prints_cookie(
    monkeypatch, capsys
) -> None:
    cookie = "abcdef0123456789abcdef0123456789"
    prompted: list[str] = []

    def fake_getpass(prompt: str) -> str:
        prompted.append(prompt)
        return cookie

    async def fake_import(settings, supplied_cookie: str) -> dict[str, object]:
        del settings
        assert supplied_cookie == cookie
        return {
            "authenticated": True,
            "method": "moodle_http_session",
            "user_id": 42,
        }

    monkeypatch.setattr(sys, "argv", ["mcp-usc", "import-session"])
    monkeypatch.setattr(cli.getpass, "getpass", fake_getpass)
    monkeypatch.setattr(cli, "import_session_cookie", fake_import)

    cli.main()

    captured = capsys.readouterr()
    assert json.loads(captured.out)["authenticated"] is True
    assert captured.err == ""
    assert cookie not in captured.out
    assert prompted == ["Valor de MoodleSession (entrada oculta): "]


def test_forget_session_cli_reports_local_only_logout(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["mcp-usc", "forget-session"])
    monkeypatch.setattr(
        cli,
        "forget_session_cookie",
        lambda: {
            "authenticated": False,
            "local_session_removed": True,
            "remote_session_unchanged": True,
        },
    )

    cli.main()

    result = json.loads(capsys.readouterr().out)
    assert result["local_session_removed"] is True
    assert result["remote_session_unchanged"] is True


def test_doctor_cli_is_offline_and_returns_public_only(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["mcp-usc", "doctor", "--compact"])
    monkeypatch.setattr(
        cli,
        "build_diagnostic",
        lambda: {
            "status": "public_only",
            "campus_contacted": False,
            "secrets_exposed": False,
        },
    )

    with pytest.raises(SystemExit) as exit_info:
        cli.main()

    assert exit_info.value.code == 0
    output = capsys.readouterr().out
    result = json.loads(output)
    assert result["campus_contacted"] is False
    assert result["secrets_exposed"] is False
    assert output.count("\n") == 1


def test_manifest_cli_outputs_the_local_contract(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["mcp-usc", "manifest", "--compact"])

    cli.main()

    result = json.loads(capsys.readouterr().out)
    assert result["version"] == "0.9.0"
    assert result["counts"] == {"tools": 84, "resources": 4, "prompts": 4}
    assert result["network_contacted"] is False
