"""Start the cat when you log in to Windows, with no console window.

A tiny .vbs file in the user's Startup folder runs `pythonw` (Python without a console) in the
project folder. Nothing is installed system-wide and nothing needs administrator rights; turning it
off deletes the file.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

NAME = "Ragnarok Cat.vbs"
PROJECT = Path(__file__).resolve().parents[2]


def startup_folder() -> Path:
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def script_text(project: Path = PROJECT, uv: str | None = None) -> str:
    """The VBScript: go to the project folder and start the cat, hidden."""
    uv = uv or shutil.which("uv") or "uv"
    command = f'""{uv}"" run pythonw -m ragnarok gui'  # doubled quotes: VBScript's way of quoting
    return (
        'Set shell = CreateObject("WScript.Shell")\r\n'
        f'shell.CurrentDirectory = "{project}"\r\n'
        f'shell.Run "{command}", 0, False\r\n'
    )


def install(folder: Path | None = None, project: Path = PROJECT, uv: str | None = None) -> Path:
    target = (folder or startup_folder()) / NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(script_text(project, uv).encode("utf-8"))
    return target


def remove(folder: Path | None = None) -> bool:
    target = (folder or startup_folder()) / NAME
    if target.exists():
        target.unlink()
        return True
    return False


def installed(folder: Path | None = None) -> bool:
    return ((folder or startup_folder()) / NAME).exists()
