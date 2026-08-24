from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import sys

from .campus import CampusError, interactive_login
from .diagnostics import build_diagnostic
from .service import UscService
from .session_auth import SessionImportError, forget_session_cookie, import_session_cookie
from .settings import Settings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MCP local para el Campus Virtual de la USC")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("serve", help="Inicia el servidor MCP por STDIO")
    login = subparsers.add_parser(
        "login", help="Abre un navegador temporal para completar Microsoft/MFA"
    )
    login.add_argument("--timeout", type=int, default=900, help="Tiempo máximo en segundos")
    subparsers.add_parser(
        "import-session",
        help="Importa MoodleSession mediante una entrada oculta, sin Playwright",
    )
    subparsers.add_parser(
        "forget-session",
        help="Elimina la cookie local sin cerrar la sesión remota",
    )
    subparsers.add_parser("status", help="Comprueba la sesión sin mostrar secretos")
    subparsers.add_parser(
        "doctor",
        help="Diagnostica la configuración local sin contactar con el Campus ni mostrar secretos",
    )
    return parser


async def _status() -> int:
    try:
        status = await UscService().auth_status()
    except CampusError as exc:
        print(json.dumps({"authenticated": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


def main() -> None:
    args = _parser().parse_args()
    if args.command in (None, "serve"):
        from .server import run

        run()
        return
    if args.command == "login":
        try:
            result = asyncio.run(interactive_login(Settings.from_env(), args.timeout))
        except (CampusError, TimeoutError) as exc:
            print(str(exc), file=sys.stderr)
            raise SystemExit(1) from exc
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.command == "status":
        raise SystemExit(asyncio.run(_status()))
    if args.command == "doctor":
        result = build_diagnostic()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        failed = result["status"] in {"configuration_error", "unsupported_python"}
        raise SystemExit(1 if failed else 0)
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
