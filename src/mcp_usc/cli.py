from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import sys

from .campus import (
    CampusError,
    LoginBrowserUnavailable,
    install_login_browser,
    interactive_login,
    login_browser_download_engine,
)
from .diagnostics import build_diagnostic
from .service import UscService
from .session_auth import SessionImportError, forget_session_cookie, import_session_cookie
from .settings import Settings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MCP local para el Campus Virtual de la USC")
    subparsers = parser.add_subparsers(dest="command")
    serve = subparsers.add_parser(
        "serve", help="Inicia el servidor MCP (STDIO por defecto, o HTTP con --transport http)"
    )
    serve.add_argument(
        "--transport",
        choices=("stdio", "http"),
        default=os.getenv("MCP_USC_TRANSPORT", "stdio"),
        help="stdio para un cliente local; http para servirlo en red (requiere MCP_AUTH_TOKEN)",
    )
    serve.add_argument(
        "--host",
        default=os.getenv("MCP_USC_HOST", "127.0.0.1"),
        help="Interfaz HTTP (por defecto 127.0.0.1; 0.0.0.0 dentro de un contenedor)",
    )
    serve.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("MCP_USC_PORT", "8000")),
        help="Puerto HTTP (por defecto 8000)",
    )
    subparsers.add_parser(
        "setup",
        help="Asistente: inicia sesión, guarda tu grado y conecta Claude/Codex",
    )
    subparsers.add_parser(
        "client-config",
        help="Muestra cómo añadir este servidor a Claude Desktop, Claude Code o Codex",
    )
    login = subparsers.add_parser(
        "login", help="Abre un navegador temporal para completar Microsoft/MFA"
    )
    login.add_argument("--timeout", type=int, default=900, help="Tiempo máximo en segundos")
    login.add_argument(
        "--install-browser",
        action="store_true",
        help="Si no hay Chrome ni Edge, descarga un navegador solo para el login",
    )
    subparsers.add_parser(
        "import-session",
        help="Importa MoodleSession mediante una entrada oculta, sin Playwright",
    )
    subparsers.add_parser(
        "forget-session",
        help="Elimina la cookie local sin cerrar la sesión remota",
    )
    subparsers.add_parser("status", help="Comprueba la sesión sin mostrar secretos")
    doctor = subparsers.add_parser(
        "doctor",
        help="Diagnostica la configuración local sin contactar con el Campus ni mostrar secretos",
    )
    doctor.add_argument("--compact", action="store_true", help="Emite JSON en una sola línea")
    manifest = subparsers.add_parser(
        "manifest",
        help="Exporta el contrato MCP local, sus esquemas y un SHA-256 sin usar la red",
    )
    manifest.add_argument("--compact", action="store_true", help="Emite JSON en una sola línea")
    return parser


async def _status() -> int:
    try:
        status = await UscService().auth_status()
    except CampusError as exc:
        result: dict[str, object] = {"authenticated": False, "error": str(exc)}
        diagnostic = getattr(exc, "as_dict", None)
        if callable(diagnostic):
            result["diagnostic"] = diagnostic()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


def _confirm_browser_download() -> bool:
    if not sys.stdin.isatty():
        return False
    engine = login_browser_download_engine()
    answer = input(
        f"No hay Chrome ni Edge. ¿Descargo un navegador ({engine}) solo para el login? [S/n] "
    )
    return answer.strip().casefold() in {"", "s", "si", "sí", "y", "yes"}


def _print_json(value: object, *, compact: bool = False) -> None:
    if compact:
        print(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
        return
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main() -> None:
    args = _parser().parse_args()
    if args.command is None or (args.command == "serve" and args.transport == "stdio"):
        from .server import run

        run()
        return
    if args.command == "serve":
        from .http_app import HttpConfigError, run_http

        try:
            run_http(args.host, args.port)
        except HttpConfigError as exc:
            print(str(exc), file=sys.stderr)
            raise SystemExit(2) from exc
        return
    if args.command == "setup":
        from .setup_wizard import run_setup

        try:
            raise SystemExit(asyncio.run(run_setup()))
        except (KeyboardInterrupt, EOFError):
            print("\nConfiguración cancelada.", file=sys.stderr)
            raise SystemExit(130) from None
    if args.command == "client-config":
        from .setup_wizard import client_config_text, server_command

        print(client_config_text(server_command()))
        return
    if args.command == "login":
        settings = Settings.from_env()
        try:
            try:
                result = asyncio.run(interactive_login(settings, args.timeout))
            except LoginBrowserUnavailable:
                if not (args.install_browser or _confirm_browser_download()):
                    raise
                install_login_browser()
                result = asyncio.run(interactive_login(settings, args.timeout))
        except (CampusError, TimeoutError) as exc:
            print(str(exc), file=sys.stderr)
            raise SystemExit(1) from exc
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.command == "status":
        raise SystemExit(asyncio.run(_status()))
    if args.command == "doctor":
        result = build_diagnostic()
        _print_json(result, compact=args.compact)
        failed = result["status"] in {"configuration_error", "unsupported_python"}
        raise SystemExit(1 if failed else 0)
    if args.command == "manifest":
        from .manifest import build_manifest

        _print_json(asyncio.run(build_manifest()), compact=args.compact)
        return
    if args.command == "import-session":
        cookie = getpass.getpass("Valor de MoodleSession (entrada oculta): ")
        try:
            result = asyncio.run(import_session_cookie(Settings.from_env(), cookie))
        except SessionImportError as exc:
            print(str(exc), file=sys.stderr)
            raise SystemExit(1) from exc
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.command == "forget-session":
        try:
            result = forget_session_cookie()
        except SessionImportError as exc:
            print(str(exc), file=sys.stderr)
            raise SystemExit(1) from exc
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    raise SystemExit(2)


def login_main() -> None:
    try:
        asyncio.run(interactive_login(Settings.from_env()))
    except (CampusError, TimeoutError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
