"""Skill: open an installed Windows app by name ("open steam", "open microsoft edge").

The model only ever supplies a *name*. The program to run comes from Windows' own Start menu list
(`Get-StartApps`), is chosen by this code, and is started through the shell by its app id; no model
text is ever put on a command line. Command shells and admin tools are refused.
"""

from __future__ import annotations

import asyncio
import difflib
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Protocol

from pydantic import Field

from ragnarok.policy.permissions import Risk
from ragnarok.runtime.tools import Tool, ToolArgs

CACHE_SECONDS = 300
# Never opened on the model's say-so: a shell or admin tool lets typed text become commands.
REFUSED = re.compile(
    r"\b(powershell|command prompt|cmd|windows terminal|terminal|registry editor|regedit|"
    r"task scheduler|group policy|computer management|services|run as administrator)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AppEntry:
    name: str
    app_id: str


class AppCatalog(Protocol):
    def list_apps(self) -> list[AppEntry]: ...
    def launch(self, app: AppEntry) -> None: ...


class WindowsApps:
    """Real catalog: the Start menu list, launched through `explorer shell:AppsFolder`."""

    def __init__(self) -> None:
        self._apps: list[AppEntry] = []
        self._loaded = 0.0

    def list_apps(self) -> list[AppEntry]:
        if self._apps and time.monotonic() - self._loaded < CACHE_SECONDS:
            return self._apps
        script = (
            "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
            "Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress"
        )
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=True,
        ).stdout
        rows = json.loads(out)
        rows = rows if isinstance(rows, list) else [rows]
        self._apps = [
            AppEntry(r["Name"], r["AppID"])
            for r in rows
            if r.get("Name") and r.get("AppID") and not r["AppID"].lower().startswith("http")
        ]  # entries whose id is a web address are links, not programs
        self._loaded = time.monotonic()
        return self._apps

    def launch(self, app: AppEntry) -> None:
        if sys.platform != "win32":
            raise OSError("opening apps is only supported on Windows")
        subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app.app_id}"])


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9+]+", " ", text.lower()).strip()


def resolve(query: str, apps: list[AppEntry]) -> tuple[str, list[AppEntry]]:
    """Find the app a user means: ("one"|"many"|"none", matches). Never guesses between apps."""
    q = _norm(query)
    if not q:
        return "none", []
    usable = [a for a in apps if "uninstall" in q or "uninstall" not in a.name.lower()]
    exact = [a for a in usable if _norm(a.name) == q]
    if exact:
        return "one", exact[:1]
    words = q.split()
    contains = [a for a in usable if all(w in _norm(a.name) for w in words)]
    if len(contains) == 1:
        return "one", contains
    if contains:
        # Several apps contain the words and none is an exact name: ask, never guess.
        return "many", sorted(contains, key=lambda a: len(a.name))[:6]
    close = difflib.get_close_matches(q, [_norm(a.name) for a in usable], n=3, cutoff=0.75)
    matches = [a for a in usable if _norm(a.name) in close]
    return (
        ("one", matches) if len(matches) == 1 else (("many", matches) if matches else ("none", []))
    )


class OpenAppArgs(ToolArgs):
    name: str = Field(
        min_length=1, max_length=80, description="App name, like Steam or Microsoft Edge."
    )


def app_tools(catalog: AppCatalog | None = None) -> list[Tool]:
    catalog = catalog or WindowsApps()

    async def open_app(args: OpenAppArgs) -> str:
        if REFUSED.search(args.name):
            return "I don't open command shells or admin tools. Please open that one yourself."
        apps = await asyncio.to_thread(catalog.list_apps)
        kind, found = resolve(args.name, apps)
        if kind == "none":
            sample = ", ".join(sorted(a.name for a in apps)[:15])
            return f"No installed app matches {args.name!r}. Some apps I can open: {sample}"
        if kind == "many":
            return f"Several apps match {args.name!r}: " + "; ".join(a.name for a in found)
        app = found[0]
        if REFUSED.search(app.name):
            return "I don't open command shells or admin tools. Please open that one yourself."
        await asyncio.to_thread(catalog.launch, app)
        return f"Opened {app.name}."

    return [
        Tool(
            name="open_app",
            description="Open an installed Windows app by name. Use it when the user asks to "
            "open, launch or start a program such as Steam or Microsoft Edge.",
            args_model=OpenAppArgs,
            risk=Risk.R1,  # starts a program as the user; closing its window undoes it
            idempotent=False,  # a second call can open a second window
            timeout_s=20,
            sandbox="full-access",  # a launched app runs as the user, outside any sandbox
            handler=open_app,
        )
    ]
