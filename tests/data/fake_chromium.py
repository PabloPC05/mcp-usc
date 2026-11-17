"""A pretend Chromium for tests: writes DevToolsActivePort and answers a few DevTools calls.

The Campus cookie starts as a guest one and becomes «good» after a few reads, as a login
would; ``--headless=new`` with FAKE_STAY_GUEST=1 never logs in (silent renewal gives up).
"""

import asyncio
import contextlib
import json
import os
import sys
from pathlib import Path

import websockets


async def main() -> None:
    profile = Path(next(a.split("=", 1)[1] for a in sys.argv if a.startswith("--user-data-dir=")))
    reads = {"n": 0}
    done = asyncio.Event()

    async def handle(socket):
        async for raw in socket:
            message = json.loads(raw)
            method, result = message["method"], {}
            if method == "Target.getTargets":
                result = {"targetInfos": [{"type": "page", "targetId": "T1", "url": "about:blank"}]}
            elif method == "Target.attachToTarget":
                result = {"sessionId": "S1"}
            elif method == "Storage.getCookies":
                reads["n"] += 1
                stay = os.environ.get("FAKE_STAY_GUEST") == "1"
                value = "good" if reads["n"] >= 3 and not stay else "guest"
                result = {
                    "cookies": [
                        {"name": "MoodleSession", "value": value, "domain": "cv.usc.es"},
                        {"name": "other", "value": "x", "domain": "login.microsoftonline.com"},
                    ]
                }
            await socket.send(json.dumps({"id": message["id"], "result": result}))
            if method == "Browser.close":
                done.set()

    async with websockets.serve(handle, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        (profile / "DevToolsActivePort").write_text(f"{port}\n/devtools/browser/fake\n")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(done.wait(), timeout=60)


asyncio.run(main())
