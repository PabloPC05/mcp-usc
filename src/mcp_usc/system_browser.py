"""Logging in with the browser you already have: Chrome, Edge, Brave or Chromium.

Chromium browsers speak the DevTools protocol. Started with a profile folder of their own
and ``--remote-debugging-port=0`` they write the port they chose to ``DevToolsActivePort``,
and one websocket to it can read the browser's cookies. That is all the Campus login needs,
so nothing has to be downloaded for it (Playwright and its browsers weigh some 160 MB).

* the window is a normal window of your browser, on a profile that is only for this: your
  tabs, history and passwords are never touched, and that profile remembers your Microsoft
  account between logins like any browser;
* ``headless`` runs the same without a window, for the silent renewal;
* only the ``MoodleSession`` cookie of the Campus is read, and only kept once Moodle says it
  belongs to an authenticated session.

Safari and Firefox do not speak this protocol: there Playwright (the ``browser-auth``
extra) or pasting the cookie remain.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from .security import validate_usc_url
from .settings import Settings

# (name, executable) to look for, best first. Edge comes with every Windows.
_MAC = (
    ("chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    ("msedge", "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
    ("brave", "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"),
    ("chromium", "/Applications/Chromium.app/Contents/MacOS/Chromium"),
)
_WINDOWS = (
    ("chrome", r"Google\Chrome\Application\chrome.exe"),
    ("msedge", r"Microsoft\Edge\Application\msedge.exe"),
    ("brave", r"BraveSoftware\Brave-Browser\Application\brave.exe"),
)
_LINUX = (
    ("chrome", "google-chrome"),
    ("chrome", "google-chrome-stable"),
    ("msedge", "microsoft-edge"),
    ("msedge", "microsoft-edge-stable"),
    ("brave", "brave-browser"),
    ("chromium", "chromium"),
    ("chromium", "chromium-browser"),
)
_PORT_FILE = "DevToolsActivePort"


class SystemBrowserError(RuntimeError):
    pass


def find_browser() -> tuple[str, str] | None:
    """(name, executable) of an installed Chromium browser, or None.

    ``USC_LOGIN_BROWSER`` may point to a specific executable.
    """

    override = os.getenv("USC_LOGIN_BROWSER", "").strip()
    if override:
        path = shutil.which(override) or override
        return ("custom", path) if Path(path).exists() else None
    if sys.platform == "darwin":
        for name, path in _MAC:
            for candidate in (Path(path), Path.home() / path.lstrip("/")):
                if candidate.exists():
                    return name, str(candidate)
        return None
    if os.name == "nt":
        roots = [os.getenv(key) for key in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
        for name, relative in _WINDOWS:
            for root in filter(None, roots):
                candidate = Path(root) / relative
                if candidate.exists():
                    return name, str(candidate)
        return None
    for name, command in _LINUX:
        found = shutil.which(command)
        if found:
            return name, found
    return None


async def _port_file(profile: Path, process: asyncio.subprocess.Process) -> tuple[int, str]:
    deadline = time.monotonic() + 30
    path = profile / _PORT_FILE
    while time.monotonic() < deadline:
        if process.returncode is not None:
            raise SystemBrowserError("El navegador se cerró al arrancar.")
        with contextlib.suppress(OSError, ValueError, IndexError):
            port, websocket_path = path.read_text(encoding="utf-8").splitlines()[:2]
            return int(port), websocket_path
        await asyncio.sleep(0.1)
    raise SystemBrowserError("El navegador no abrió su puerto de depuración a tiempo.")


class _DevTools:
    """The handful of DevTools calls the login needs, over one websocket."""

    def __init__(self, socket: Any) -> None:
        self.socket = socket
        self.ids = itertools.count(1)

    async def call(
        self, method: str, params: dict[str, Any] | None = None, session: str | None = None
    ) -> dict[str, Any]:
        number = next(self.ids)
        message: dict[str, Any] = {"id": number, "method": method, "params": params or {}}
        if session:
            message["sessionId"] = session
        await self.socket.send(json.dumps(message))
        while True:
            message = json.loads(await self.socket.recv())
            if message.get("id") == number:
                if "error" in message:
                    raise SystemBrowserError(str(message["error"].get("message") or "DevTools"))
                return dict(message.get("result") or {})

    async def open(self, url: str) -> None:
        """Navigates the browser's first tab (a URL on the command line does not always
        load in headless mode, a navigation through DevTools does)."""

        targets = (await self.call("Target.getTargets")).get("targetInfos") or []
        page = next((target for target in targets if target.get("type") == "page"), None)
        if page is None:
            await self.call("Target.createTarget", {"url": url})
            return
        attached = await self.call(
            "Target.attachToTarget", {"targetId": page["targetId"], "flatten": True}
        )
        await self.call("Page.navigate", {"url": url}, session=attached.get("sessionId"))


async def capture_session(
    settings: Settings,
    *,
    headless: bool,
    timeout: int,
    give_up_after: float | None = None,
    browser: tuple[str, str] | None = None,
) -> str:
    """Opens the Campus's Microsoft sign-in in your browser; the authenticated MoodleSession.

    Returns "" when time runs out or, with ``give_up_after`` (silent renewal), when no
    session appeared in that many seconds: Microsoft is then waiting for a person.
    """

    import websockets

    from .campus import LoginBrowserUnavailable, _cookie_is_authenticated

    browser = browser or find_browser()
    if browser is None:
        raise LoginBrowserUnavailable("No hay Chrome, Edge, Brave ni Chromium en este equipo.")
    name, executable = browser
    base = validate_usc_url(settings.moodle_url, campus=True).rstrip("/")
    campus_host = urlparse(base).hostname or ""
    start_url = validate_usc_url(urljoin(f"{base}/", "auth/oidc/?source=loginpage"), campus=True)
    profile = Path(settings.browser_profile_dir) / f"sistema-{name}"
    profile.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        profile.chmod(0o700)
        (profile / _PORT_FILE).unlink()
    # (a .py "browser" is the tests' fake DevTools endpoint)
    launcher = [sys.executable, executable] if executable.endswith(".py") else [executable]
    arguments = [
        *launcher,
        f"--user-data-dir={profile}",
        "--remote-debugging-port=0",
        "--no-first-run",
        "--no-default-browser-check",
        "--password-store=basic",
    ]
    if headless:
        arguments += ["--headless=new", "--disable-gpu"]
    arguments.append("about:blank")
    process = await asyncio.create_subprocess_exec(
        *arguments,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    started = time.monotonic()
    rejected: set[str] = set()
    try:
        port, path = await _port_file(profile, process)
        async with websockets.connect(
            f"ws://127.0.0.1:{port}{path}", max_size=None, open_timeout=20
        ) as socket:
            devtools = _DevTools(socket)
            await devtools.open(start_url)
            while time.monotonic() - started < timeout:
                cookies = (await devtools.call("Storage.getCookies")).get("cookies") or []
                for cookie in cookies:
                    domain = str(cookie.get("domain") or "").lstrip(".")
                    value = str(cookie.get("value") or "")
                    if cookie.get("name") != "MoodleSession" or domain != campus_host:
                        continue
                    if value and value not in rejected:
                        if await _cookie_is_authenticated(settings, value):
                            with contextlib.suppress(Exception):
                                await devtools.call("Browser.close")
                            return value
                        rejected.add(value)  # a guest cookie: Moodle issues a new one later
                if give_up_after is not None and time.monotonic() - started > give_up_after:
                    return ""
                await asyncio.sleep(1)
            return ""
    except (OSError, websockets.exceptions.WebSocketException) as exc:
        if process.returncode is not None and not headless:
            raise SystemBrowserError("Cerraste la ventana antes de entrar.") from exc
        raise SystemBrowserError("No se pudo hablar con el navegador.") from exc
    finally:
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()


__all__ = ["SystemBrowserError", "capture_session", "find_browser"]
