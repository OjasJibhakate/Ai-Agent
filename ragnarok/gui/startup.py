"""Start the cat when you log in to Windows, or from one short command, with no console window.

A tiny .vbs file runs `pythonw` (Python without a console) in the project folder. Nothing is
installed system-wide and nothing needs administrator rights; turning it off deletes the files.

Two ways in:
  - `rag gui --startup on`   the cat appears when you log in (a file in the Startup folder);
  - `rag gui --command on`   typing `meow` anywhere starts the cat; typing it again while the cat is
                             running opens its chat.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

NAME = "Ragnarok Cat.vbs"
PROJECT = Path(__file__).resolve().parents[2]
COMMAND = "meow"


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


# ---- the short command: `meow`, from any terminal or the Run box -----------------------------


def command_folder() -> Path:
    """A folder Windows already puts on every user's PATH, and the user may write to."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Microsoft" / "WindowsApps"


def on_path(folder: Path | None = None) -> bool:
    here = os.path.normcase(str(folder or command_folder()))
    return any(os.path.normcase(p.strip()) == here for p in os.environ.get("PATH", "").split(";"))


def command_files(folder: Path | None = None) -> tuple[Path, Path]:
    folder = folder or command_folder()
    return folder / f"{COMMAND}.cmd", folder / f"{COMMAND}.vbs"


def install_command(
    folder: Path | None = None, project: Path = PROJECT, uv: str | None = None
) -> list[Path]:
    """Write `meow.cmd` and the hidden launcher it runs.

    The first `meow` starts the cat; another one, while the cat is running, opens its chat.
    """
    cmd, vbs = command_files(folder)
    cmd.parent.mkdir(parents=True, exist_ok=True)
    vbs.write_bytes(script_text(project, uv).encode("utf-8"))
    cmd.write_bytes(f'@echo off\r\nwscript.exe //nologo "%~dp0{COMMAND}.vbs"\r\n'.encode())
    return [cmd, vbs]


def remove_command(folder: Path | None = None) -> int:
    count = 0
    for path in command_files(folder):
        if path.exists():
            path.unlink()
            count += 1
    return count
