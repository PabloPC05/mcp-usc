"""Guided first-time setup: Campus login, academic profile and MCP client registration.

`mcp-usc setup` is meant for students who just want the server working in their own
assistant. Everything stays on the user's machine: the Moodle session goes to the OS
credential store, the profile to a local JSON file, and client registration only
touches the user's own client configuration after an explicit yes.
"""

from __future__ import annotations

import asyncio
import getpass
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .academic_profile import AcademicProfileError, default_profile_path, load_academic_profile
from .campus import (
    CampusError,
    LoginBrowserUnavailable,
    install_login_browser,
    interactive_login,
    login_browser_download_engine,
)
from .credentials import CredentialStoreError
from .degree_catalog import USC_DEGREE_CATALOG_URL, DegreeCatalogEntry, UscDegreeCatalogClient
from .service import UscService
from .session_auth import SessionImportError, import_session_cookie
from .settings import Settings

SERVER_NAME = "usc-campus"
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_MAX_DEGREE_CHOICES = 15
_MAX_COURSE_NUMBER = 6


class SetupError(RuntimeError):
    """A client configuration could not be updated safely; nothing was written."""


@dataclass
class Console:
    """Injectable terminal I/O so the wizard can be tested without a TTY."""

    ask: Callable[[str], str] = input
    ask_secret: Callable[[str], str] = getpass.getpass
    say: Callable[[str], None] = print

    def confirm(self, question: str, *, default: bool = True) -> bool:
        suffix = " [S/n] " if default else " [s/N] "
        answer = self.ask(question + suffix).strip().casefold()
        if not answer:
            return default
        return answer in {"s", "si", "sí", "y", "yes"}


def server_command() -> list[str]:
    """Absolute command for MCP clients; GUI apps often lack the user's PATH."""

    return [sys.executable, "-m", "mcp_usc", "serve"]


def running_from_ephemeral_environment(executable: str | None = None) -> bool:
    """True for `uvx` runs, whose cached environment may disappear later."""

    path = (executable or sys.executable).replace("\\", "/")
    return "/archive-v" in path or "/uv/cache/" in path.casefold()


def claude_desktop_config_path(
    platform: str | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    platform = platform or sys.platform
    env = os.environ if env is None else env
    home = home or Path.home()
    if platform == "darwin":
        base = home / "Library" / "Application Support"
    elif platform.startswith("win"):
        base = Path(env.get("APPDATA") or home / "AppData" / "Roaming")
    else:
        base = Path(env.get("XDG_CONFIG_HOME") or home / ".config")
    return base / "Claude" / "claude_desktop_config.json"


def stdio_server_entry(command: Sequence[str]) -> dict[str, Any]:
    return {"command": command[0], "args": list(command[1:])}


def add_to_claude_desktop(config_path: Path, command: Sequence[str]) -> Path | None:
    """Merge this server into Claude Desktop's config; returns the backup path, if any."""

    data: dict[str, Any] = {}
    backup: Path | None = None
    if config_path.exists():
        raw = config_path.read_text(encoding="utf-8")
        if raw.strip():
            try:
                loaded = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise SetupError(f"{config_path} no es JSON válido; no se ha modificado.") from exc
            if not isinstance(loaded, dict):
                raise SetupError(f"{config_path} no contiene un objeto JSON; no se ha modificado.")
            data = loaded
        backup = config_path.with_name(config_path.name + ".bak")
        backup.write_text(raw, encoding="utf-8")
    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise SetupError(f"mcpServers en {config_path} no es un objeto; no se ha modificado.")
    servers[SERVER_NAME] = stdio_server_entry(command)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = config_path.with_name(config_path.name + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, config_path)
    return backup


def cli_registration_commands(cli: str, command: Sequence[str]) -> list[list[str]]:
    """Commands that (re)register the server in a CLI client; removal may fail harmlessly."""

    if cli == "claude":
        return [
            ["claude", "mcp", "remove", "--scope", "user", SERVER_NAME],
            ["claude", "mcp", "add", "--scope", "user", SERVER_NAME, "--", *command],
        ]
    if cli == "codex":
        return [
            ["codex", "mcp", "remove", SERVER_NAME],
            ["codex", "mcp", "add", SERVER_NAME, "--", *command],
        ]
    raise ValueError(f"Cliente no soportado: {cli}")


def client_config_text(command: Sequence[str]) -> str:
    entry = json.dumps(
        {"mcpServers": {SERVER_NAME: stdio_server_entry(command)}},
        indent=2,
        ensure_ascii=False,
    )
    quoted = " ".join(_shell_quote(part) for part in command)
    return "\n".join(
        [
            "Claude Code:",
            f"  claude mcp add --scope user {SERVER_NAME} -- {quoted}",
            "",
            "Codex:",
            f"  codex mcp add {SERVER_NAME} -- {quoted}",
            "",
            f"Claude Desktop ({claude_desktop_config_path()}) y otros clientes con JSON:",
            entry,
        ]
    )


def _shell_quote(value: str) -> str:
    return f'"{value}"' if re.search(r"[\s\"']", value) else value


def _fold(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def match_degrees(degrees: Sequence[DegreeCatalogEntry], query: str) -> list[DegreeCatalogEntry]:
    words = [word for word in re.split(r"\W+", _fold(query)) if word]
    if not words:
        return []
    return [degree for degree in degrees if all(word in _fold(degree.name) for word in words)]


def save_profile(path: Path, *, degree_url: str, course_number: int | None = None) -> None:
    """Write the profile and validate it with the same loader the server uses."""

    profile: dict[str, object] = {"degree_url": degree_url}
    if course_number is not None:
        profile["course_number"] = course_number
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = path.read_text(encoding="utf-8") if path.exists() else None
    path.write_text(json.dumps(profile, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        load_academic_profile({"USC_ACADEMIC_PROFILE_FILE": str(path)})
    except AcademicProfileError:
        if previous is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(previous, encoding="utf-8")
        raise


def _clean(value: str) -> str:
    return _CONTROL.sub("", value).strip()


async def _authenticated_user(settings: Settings) -> str | None:
    try:
        status = await UscService(settings).auth_status()
    except (CampusError, CredentialStoreError):
        return None
    return _clean(str(status.get("user_name") or "")) or "tu cuenta"


async def _setup_login(console: Console, settings: Settings) -> bool:
    console.say("\n1/3  Acceso al Campus Virtual")
    user = await _authenticated_user(settings)
    if user:
        console.say(f"  ✓ Ya hay una sesión válida ({user}).")
        return True
    console.say(
        "  1) Abrir el navegador e iniciar sesión con tu cuenta USC (recomendado)\n"
        "  2) Pegar el valor de la cookie MoodleSession\n"
        "  3) Saltar: solo datos públicos (grados, horarios y exámenes oficiales)"
    )
    choice = console.ask("  Elige [1]: ").strip() or "1"
    if choice == "1":
        console.say("  Se abrirá una ventana: entra con tu cuenta USC y completa el MFA.")
        try:
            try:
                await interactive_login(settings)
            except LoginBrowserUnavailable:
                engine = login_browser_download_engine()
                question = (
                    f"  No hay Chrome ni Edge. ¿Descargo un navegador ({engine}) "
                    "solo para el login?"
                )
                if not console.confirm(question):
                    raise
                await asyncio.to_thread(install_login_browser)
                await interactive_login(settings)
        except (CampusError, TimeoutError) as exc:
            console.say(f"  ✗ {exc}")
            console.say("  Puedes repetir con `mcp-usc login` o usar la opción 2.")
            return False
    elif choice == "2":
        console.say(
            "  En https://cv.usc.es abre las herramientas de desarrollo > Aplicación > Cookies"
            " y copia solo el valor de MoodleSession."
        )
        cookie = console.ask_secret("  MoodleSession (entrada oculta): ")
        try:
            await import_session_cookie(settings, cookie)
        except SessionImportError as exc:
            console.say(f"  ✗ {exc}")
            return False
    else:
        console.say("  Sin sesión: podrás usar las consultas públicas.")
        return False
    user = await _authenticated_user(settings)
    if user:
        console.say(f"  ✓ Sesión guardada en el almacén seguro del sistema ({user}).")
        return True
    console.say("  ✗ La sesión no se pudo comprobar; prueba `mcp-usc status`.")
    return False


async def _setup_profile(console: Console, settings: Settings) -> None:
    console.say(
        "\n2/3  Tu grado: hace más rápidos y precisos «mi horario» y «mis exámenes». "
        "Sirve cualquier grado de la USC."
    )
    path = default_profile_path()
    if path.is_file():
        console.say(f"  Ya tienes un perfil en {path}.")
        if not console.confirm("  ¿Quieres cambiarlo?", default=False):
            return
    try:
        catalog = await UscDegreeCatalogClient(
            timeout=settings.request_timeout_seconds
        ).fetch_catalog(USC_DEGREE_CATALOG_URL)
    except Exception:
        console.say("  ✗ No se pudo leer el catálogo de grados de la USC; repite más tarde.")
        return
    for _ in range(3):
        query = console.ask("  Escribe parte del nombre de tu grado (Intro para saltar): ")
        if not query.strip():
            return
        matches = match_degrees(catalog.degrees, query)[:_MAX_DEGREE_CHOICES]
        if matches:
            break
        console.say("  No encontré ese grado; prueba con otra palabra (p. ej. «informática»).")
    else:
        return
    for index, degree in enumerate(matches, start=1):
        campus = f" ({_clean(degree.campus)})" if degree.campus else ""
        console.say(f"  {index}) {_clean(degree.name)}{campus}")
    selected = _ask_number(console, "  Número del grado: ", len(matches))
    if selected is None:
        return
    # Optional: subjects can span several courses; only the whole-course timetable needs it.
    course = _ask_optional_number(
        console,
        f"  ¿En qué curso estás? (1-{_MAX_COURSE_NUMBER}, Intro si tienes asignaturas "
        "de varios cursos): ",
        _MAX_COURSE_NUMBER,
    )
    try:
        save_profile(path, degree_url=matches[selected - 1].url, course_number=course)
    except AcademicProfileError as exc:
        console.say(f"  ✗ {exc}")
        return
    console.say(f"  ✓ Perfil guardado en {path}.")


def _ask_optional_number(console: Console, question: str, maximum: int) -> int | None:
    for _ in range(3):
        answer = console.ask(question).strip()
        if not answer:
            return None
        if answer.isascii() and answer.isdigit() and 1 <= int(answer) <= maximum:
            return int(answer)
        console.say(f"  Escribe un número entre 1 y {maximum}, o pulsa Intro.")
    return None


def _ask_number(console: Console, question: str, maximum: int) -> int | None:
    for _ in range(3):
        answer = console.ask(question).strip()
        if answer.isascii() and answer.isdigit() and 1 <= int(answer) <= maximum:
            return int(answer)
        console.say(f"  Escribe un número entre 1 y {maximum}.")
    return None


def _setup_clients(
    console: Console,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
) -> None:
    console.say("\n3/3  Conectar con tu asistente")
    command = server_command()
    if running_from_ephemeral_environment():
        console.say(
            "  Aviso: estás usando una copia temporal (uvx). Instálalo con `uv tool install` "
            "para que la ruta que guardan los clientes no desaparezca."
        )
    registered = False
    desktop_config = claude_desktop_config_path()
    if desktop_config.parent.is_dir() and console.confirm("  ¿Añadirlo a Claude Desktop?"):
        try:
            backup = add_to_claude_desktop(desktop_config, command)
        except (OSError, SetupError) as exc:
            console.say(f"  ✗ {exc}")
        else:
            registered = True
            saved = f" (copia de seguridad en {backup})" if backup else ""
            console.say(f"  ✓ Añadido a Claude Desktop{saved}. Reinícialo para verlo.")
    for cli, label in (("claude", "Claude Code"), ("codex", "Codex")):
        if not which(cli) or not console.confirm(f"  ¿Añadirlo a {label}?"):
            continue
        remove, add = cli_registration_commands(cli, command)
        run(remove, capture_output=True, text=True, check=False)
        result = run(add, capture_output=True, text=True, check=False)
        if result.returncode == 0:
            registered = True
            console.say(f"  ✓ Añadido a {label}.")
        else:
            console.say(f"  ✗ {label} no aceptó el registro: {_clean(result.stderr)[:300]}")
    if not registered:
        console.say("  Configúralo a mano en tu cliente MCP:\n")
        console.say(client_config_text(command))


async def run_setup(
    console: Console | None = None,
    settings: Settings | None = None,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
) -> int:
    console = console or Console()
    settings = settings or Settings.from_env()
    console.say("Configuración de mcp-usc: Campus Virtual e información académica de la USC.")
    await _setup_login(console, settings)
    await _setup_profile(console, settings)
    _setup_clients(console, run=run, which=which)
    console.say(
        "\nListo. Prueba a preguntar a tu asistente:\n"
        "  «¿Qué tareas tengo pendientes esta semana?»\n"
        "  «¿Cuál es mi horario de esta semana?»\n"
        "  «¿Cuándo son mis exámenes oficiales?»\n"
        "Las escrituras (mensajes, entregas, cuestionarios) siempre piden una vista previa y tu"
        " confirmación."
    )
    return 0
