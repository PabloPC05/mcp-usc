from __future__ import annotations

import argparse
import asyncio
import json
import sys

from .campus import CampusError, interactive_login
from .service import UscService
from .settings import Settings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MCP local para el Campus Virtual de la USC")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("serve", help="Inicia el servidor MCP por STDIO")
    login = subparsers.add_parser(
        "login", help="Abre un navegador temporal para completar Microsoft/MFA"
    )
    login.add_argument("--timeout", type=int, default=900, help="Tiempo máximo en segundos")
    subparsers.add_parser("status", help="Comprueba la sesión sin mostrar secretos")
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
    raise SystemExit(2)


def login_main() -> None:
    try:
        asyncio.run(interactive_login(Settings.from_env()))
    except (CampusError, TimeoutError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
