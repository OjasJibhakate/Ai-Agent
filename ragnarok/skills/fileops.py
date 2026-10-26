"""Skill: make, copy, move, edit and delete files and folders, and turn a PDF into a .txt file.

Seven small tools instead of one big one, each with its own risk level so the permission mode can
treat them differently (ragnarok/policy/modes.py):

  make_folder, create_file, copy_path, extract_text   R1  add something new; never replaces anything
  edit_file, move_path                                R2  change or relocate something that exists
  delete_path                                         R3  Recycle Bin only (there is no way to
                                                          delete permanently from here)

Every path goes through PathGuard first: the vault, the system, other programs' saved logins and
the profile folders themselves are off limits in every mode. Nothing is ever overwritten by a
tool that adds (an existing target is an error, not a replacement), writes are atomic, and the
blocking work runs in worker threads.
"""

from __future__ import annotations

import asyncio
import ctypes
import os
import re
import shutil
import sys
from collections.abc import Callable
from pathlib import Path

from pydantic import Field

from ragnarok.policy.modes import Mode
from ragnarok.policy.permissions import Risk
from ragnarok.runtime.tools import Tool, ToolArgs
from ragnarok.skills.downloads import PROGRAM_EXTENSIONS
from ragnarok.skills.extract import ExtractError, text_of
from ragnarok.skills.files import TEXT_EXT, AmbiguousName, FileAccess, default_roots
from ragnarok.skills.pathguard import PathGuard, PathRefused
from ragnarok.skills.steps import reply

MAX_NEW_FILE_CHARS = 200_000
MAX_EDIT_BYTES = 2_000_000
MAX_COPY_BYTES = 4 * 1024**3
MAX_COPY_FILES = 50_000
# Files that run by themselves when a shell or an editor starts: planting one is a way to run code
# later, so changing them needs full mode.
AUTORUN_NAMES = {
    "microsoft.powershell_profile.ps1", "profile.ps1", "microsoft.vscode_profile.ps1", ".bashrc",
    ".bash_profile", ".zshrc", ".profile", "sitecustomize.py", "usercustomize.py",
}  # fmt: skip
Recycler = Callable[[Path], None]


def open_with_default_program(path: Path) -> None:
    """Open a file or folder the way a double-click would."""
    if sys.platform != "win32":
        raise FileOpError("opening files is only set up on Windows")
    os.startfile(str(path))


def find_code_editor() -> str | None:
    """The VS Code launcher (`code.cmd`), from PATH or the usual install folders."""
    import shutil

    found = shutil.which("code")
    if found:
        return found
    for base in (os.environ.get("LOCALAPPDATA"), os.environ.get("ProgramFiles")):
        if base:
            for sub in (
                "Programs/Microsoft VS Code/bin/code.cmd",
                "Microsoft VS Code/bin/code.cmd",
            ):
                candidate = Path(base) / sub
                if candidate.is_file():
                    return str(candidate)
    return None


def open_in_code_editor(path: Path) -> None:
    """Open a file or folder in Visual Studio Code, without a console window."""
    import subprocess

    if sys.platform != "win32":
        raise FileOpError("opening the editor is only set up on Windows")
    code = find_code_editor()
    if code is None:
        raise FileOpError("Visual Studio Code was not found (the `code` command is not installed)")
    # a list, never a shell string: the path cannot add arguments
    subprocess.Popen([code, "--", str(path)], creationflags=0x08000000)  # CREATE_NO_WINDOW


class FileOpError(ValueError):
    """The operation can't be done (the message is shown to the model and the user)."""


# ---- the Recycle Bin ----------------------------------------------------------------------------


def send_to_recycle_bin(path: Path) -> None:
    """Move a file or folder to the Recycle Bin (never a permanent delete)."""
    if sys.platform != "win32":
        raise FileOpError("the Recycle Bin is only available on Windows")
    drive = os.path.splitdrive(path)[0] + "\\"
    if ctypes.windll.kernel32.GetDriveTypeW(drive) != 3:  # DRIVE_FIXED
        raise FileOpError("I only delete from local drives, where the Recycle Bin can undo it")
    from win32com.shell import shell, shellcon

    flags = (
        shellcon.FOF_ALLOWUNDO | shellcon.FOF_NOCONFIRMATION | shellcon.FOF_NOERRORUI
        | shellcon.FOF_SILENT
    )  # fmt: skip
    code, aborted = shell.SHFileOperation(
        (0, shellcon.FO_DELETE, str(path), None, flags, None, None)
    )
    if code != 0 or aborted:
        raise FileOpError(f"Windows would not move that to the Recycle Bin (code {code})")
    if path.exists():
        raise FileOpError("that is still there after the delete, so I stopped")


# ---- small helpers -------------------------------------------------------------------------------


def _write_atomic(path: Path, data: bytes) -> None:
    part = path.with_name(path.name + ".part")
    part.write_bytes(data)
    os.replace(part, path)  # a crash leaves a .part file, never a half-written file


def _is_text_file(path: Path) -> bool:
    if path.suffix.lower() in TEXT_EXT:
        return True
    try:
        head = path.read_bytes()[:8192]
    except OSError:
        return False
    if b"\x00" in head or head[:4] in (b"PK\x03\x04", b"%PDF", b"\x89PNG"):
        return False
    try:
        head.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _tree_size(path: Path) -> tuple[int, int]:
    """(bytes, files) under a folder, without following links."""
    total = files = 0
    for root, _dirs, names in os.walk(path):
        for name in names:
            files += 1
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
            if files > MAX_COPY_FILES or total > MAX_COPY_BYTES:
                raise FileOpError(
                    "that folder is too big for me to copy (over 4 GB or 50,000 files)"
                )
    return total, files


_reply = reply  # the shared rule for ending a turn


# ---- arguments -----------------------------------------------------------------------------------

_PATH = "Desktop/notes.txt, Documents/Reports, or a full path. Never invent a user name."


class MakeFolderArgs(ToolArgs):
    path: str = Field(min_length=1, max_length=400, description=_PATH)


class CreateFileArgs(ToolArgs):
    path: str = Field(min_length=1, max_length=400, description=_PATH)
    content: str = Field(max_length=MAX_NEW_FILE_CHARS, description="The text to put in it.")


class EditFileArgs(ToolArgs):
    path: str = Field(min_length=1, max_length=400, description=_PATH)
    new_text: str = Field(max_length=MAX_NEW_FILE_CHARS, description="The text to put in.")
    old_text: str | None = Field(
        default=None,
        max_length=MAX_NEW_FILE_CHARS,
        description="The exact text to replace (must appear once). Leave out to replace the file.",
    )


class CopyPathArgs(ToolArgs):
    source: str = Field(min_length=1, max_length=400, description="The file or folder to copy.")
    destination: str = Field(
        min_length=1, max_length=400, description="The new name, or a folder to copy it into."
    )


class MovePathArgs(ToolArgs):
    source: str = Field(min_length=1, max_length=400, description="The file or folder to move.")
    destination: str = Field(
        min_length=1, max_length=400, description="The new name, or a folder to move it into."
    )


class DeletePathArgs(ToolArgs):
    path: str = Field(min_length=1, max_length=400, description=_PATH)


class OpenPathArgs(ToolArgs):
    path: str = Field(min_length=1, max_length=400, description=_PATH)


class ExtractTextArgs(ToolArgs):
    source: str = Field(
        min_length=1, max_length=400, description="The PDF, Word, PowerPoint or text file."
    )
    destination: str | None = Field(
        default=None,
        max_length=400,
        description="Where to save the .txt (a file or a folder). Default: next to the source.",
    )


# ---- the tools -----------------------------------------------------------------------------------


def file_op_tools(
    roots: list[Path] | None = None,
    home: Path | None = None,
    mode: Mode | None = None,
    recycler: Recycler = send_to_recycle_bin,
    data_dirs: list[Path] | None = None,
    opener: Recycler | None = None,
    editor: Recycler | None = None,
) -> list[Tool]:
    opener = opener or open_with_default_program
    editor = editor or open_in_code_editor
    access = FileAccess(roots if roots is not None else default_roots(home), home, mode, data_dirs)
    guard: PathGuard = access.guard

    def readable(text: str) -> Path:
        try:
            return guard.readable(text, access.roots, wide=False)  # access.roots follows the mode
        except PathRefused as exc:
            raise FileOpError(str(exc)) from exc

    def writable(text: str) -> Path:
        try:
            real = guard.writable(text)
        except PathRefused as exc:
            raise FileOpError(str(exc)) from exc
        if real.name.lower() in AUTORUN_NAMES and (mode is None or mode.name != "full"):
            raise FileOpError(
                f"{real.name} runs by itself when a shell starts; /mode full is needed to change it"
            )
        return real

    def find(text: str, check: Callable[[str], Path]) -> Path:
        """An existing file or folder the user named, by path or just by name ("notes.txt")."""
        error: FileOpError | None = None
        first: Path | None = None
        try:
            first = check(text)
            if first.exists():
                return first
        except FileOpError as exc:
            error = exc
        try:
            found: Path | None = access.locate(text)
        except AmbiguousName as exc:
            raise FileOpError(str(exc)) from exc
        except ValueError:
            found = None
        if found is not None and found.exists():
            return check(str(found))
        if error is not None:
            raise error
        assert first is not None
        return first

    def is_bare(text: str) -> bool:
        """A plain name with no folder in it, like "ideas.txt" or "Reports"."""
        return not re.search(r"[\\/:~%$]", text.strip())

    def new_path(text: str) -> Path:
        """Where a new file or folder goes: as written, but a bare name lands in Documents\\Ragnarok
        (a file made in the profile folder itself would be easy to lose)."""
        return writable(f"Documents/Ragnarok/{text.strip()}" if is_bare(text) else text)

    def destination_for(source: Path, text: str) -> Path:
        """Where `source` lands: inside the folder named, or at the new name. A bare name means
        a folder of that name if there is one ("copy it into Reports"), else a new name beside
        the source ("rename it to ideas.txt")."""
        if is_bare(text):
            try:
                folder = access.locate(text)
            except ValueError:
                folder = None
            target = writable(
                str(
                    folder
                    if folder is not None and folder.is_dir()
                    else source.parent / text.strip()
                )
            )
        else:
            target = writable(text)
        if target.is_dir():
            target = target / source.name
            writable(str(target))  # the new path must be allowed too
        if target.exists():
            raise FileOpError(f"{target} already exists, and I never replace anything by copying")
        if source.is_dir() and (target == source or source in target.parents):
            raise FileOpError("a folder cannot be put inside itself")
        if not target.parent.is_dir():
            raise FileOpError(f"the folder {target.parent} does not exist; make it first")
        return target

    # -- make_folder
    def _make_folder(args: MakeFolderArgs) -> str:
        path = new_path(args.path)
        if path.is_file():
            raise FileOpError(f"{path} is a file, not a folder")
        existed = path.is_dir()
        path.mkdir(parents=True, exist_ok=True)
        return f"{'The folder already exists' if existed else 'Created the folder'}: {path}"

    # -- create_file
    def _create_file(args: CreateFileArgs) -> str:
        path = new_path(args.path)
        if path.exists():
            raise FileOpError(f"{path} already exists; use edit_file to change it")
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_atomic(path, args.content.encode("utf-8"))
        return f"Created {path} ({len(args.content)} characters)"

    # -- edit_file
    def _edit_file(args: EditFileArgs) -> str:
        path = find(args.path, writable)
        if not path.is_file():
            raise FileOpError(f"{path} is not an existing file")
        if path.stat().st_size > MAX_EDIT_BYTES or not _is_text_file(path):
            raise FileOpError("I only edit plain-text files up to 2 MB")
        old = path.read_text(encoding="utf-8", errors="replace")
        if args.old_text is None:
            new, note = args.new_text, "replaced the whole file"
        else:
            count = old.count(args.old_text)
            if not args.old_text or count != 1:
                raise FileOpError(
                    "the text to replace must appear exactly once "
                    f"(it appears {count} times); give a longer piece"
                )
            new, note = old.replace(args.old_text, args.new_text, 1), "replaced 1 piece of text"
        _write_atomic(path, new.encode("utf-8"))
        return f"Edited {path}: {note}"

    # -- copy_path / move_path
    def _copy(args: CopyPathArgs) -> str:
        source = find(args.source, readable)
        if not source.exists():
            raise FileOpError(f"{source} does not exist")
        target = destination_for(source, args.destination)
        if source.is_dir():
            _tree_size(source)
            shutil.copytree(source, target, symlinks=True)
            return f"Copied the folder {source.name} to {target}"
        if source.stat().st_size > MAX_COPY_BYTES:
            raise FileOpError("that file is too big for me to copy (over 4 GB)")
        shutil.copy2(source, target)
        return f"Copied {source.name} to {target}"

    def _move(args: MovePathArgs) -> str:
        source = find(args.source, writable)
        if not source.exists():
            raise FileOpError(f"{source} does not exist")
        try:
            guard.whole_folder_ok(source, "move")
        except PathRefused as exc:
            raise FileOpError(str(exc)) from exc
        target = destination_for(source, args.destination)
        shutil.move(str(source), str(target))
        return f"Moved {source.name} to {target}"

    # -- delete_path
    def _delete(args: DeletePathArgs) -> str:
        path = find(args.path, writable)
        if not path.exists():
            raise FileOpError(f"{path} does not exist")
        try:
            guard.whole_folder_ok(path, "delete")
        except PathRefused as exc:
            raise FileOpError(str(exc)) from exc
        kind = "folder" if path.is_dir() else "file"
        recycler(path)
        return f"Moved the {kind} {path} to the Recycle Bin (you can restore it from there)"

    # -- extract_text
    def _extract(args: ExtractTextArgs) -> str:
        source = find(args.source, readable)
        if not source.is_file():
            raise FileOpError(f"{source} is not a file")
        try:
            text = text_of(source)
        except ExtractError as exc:
            raise FileOpError(str(exc)) from exc
        if args.destination:
            target = writable(args.destination)
            if target.is_dir():
                target = target / f"{source.stem}.txt"
        else:
            target = writable(str(source.with_suffix(".txt")))
        if target.suffix == "":
            target = target.with_suffix(".txt")
        data = text.encode("utf-8")
        n = 0
        while target.exists():
            if target.read_bytes() == data:
                return f"Already saved: {target}"
            n += 1
            target = target.with_name(f"{source.stem} ({n}).txt")
        if not target.parent.is_dir():
            raise FileOpError(f"the folder {target.parent} does not exist; make it first")
        _write_atomic(target, data)
        pages = text.count("--- page ")
        extent = f"{pages} pages, " if pages else ""
        return f"Saved the text of {source.name} ({extent}{len(text):,} characters) to {target}"

    # -- open_path
    def _open(args: OpenPathArgs) -> str:
        path = find(args.path, readable)
        if not path.exists():
            raise FileOpError(f"{path} does not exist")
        if path.is_file() and path.suffix.lower() in PROGRAM_EXTENSIONS:
            raise FileOpError(
                f"{path.name} is a program or script; I never start those, open it yourself"
            )
        opener(path)
        return f"Opened {path.name or path}"

    # -- open_in_editor (FR3.2: the code editor)
    def _edit(args: OpenPathArgs) -> str:
        path = find(args.path, readable)
        if not path.exists():
            raise FileOpError(f"{path} does not exist")
        editor(path)  # an editor shows a script's text; it never runs it
        return f"Opened {path.name or path} in Visual Studio Code"

    def wrap(fn: Callable) -> Callable:
        async def handler(args):
            try:
                return _reply(await asyncio.to_thread(fn, args))
            except FileOpError as exc:
                return f"error: {exc}"
            except OSError as exc:
                return f"error: Windows refused that ({type(exc).__name__})"

        return handler

    write_fs = {"write": ["the user's profile folder (not the vault, AppData or system folders)"]}
    return [
        Tool(
            name="make_folder",
            description="Create a new folder (and any missing parent folders). Use it when the "
            "user asks you to make a folder.",
            args_model=MakeFolderArgs,
            risk=Risk.R1,
            idempotent=True,
            timeout_s=10,
            sandbox="workspace-write",
            fs=write_fs,
            handler=wrap(_make_folder),
        ),
        Tool(
            name="create_file",
            description="Create a new text file with the given text. It never replaces a file "
            "that exists; use it when the user asks you to write or save text to a new file.",
            args_model=CreateFileArgs,
            risk=Risk.R1,
            idempotent=False,
            timeout_s=15,
            sandbox="workspace-write",
            fs=write_fs,
            handler=wrap(_create_file),
        ),
        Tool(
            name="edit_file",
            description="Change a text file: replace one exact piece of text with new text, or "
            "replace the whole file. Use it when the user asks you to edit or fix a file.",
            args_model=EditFileArgs,
            risk=Risk.R2,
            idempotent=False,
            timeout_s=15,
            sandbox="workspace-write",
            fs=write_fs,
            handler=wrap(_edit_file),
        ),
        Tool(
            name="copy_path",
            description="Copy a file or folder to a new name, or into another folder. Use it "
            "when the user asks you to copy or paste a file somewhere.",
            args_model=CopyPathArgs,
            risk=Risk.R1,
            idempotent=False,
            timeout_s=300,
            sandbox="workspace-write",
            fs={"read": ["the user's profile folder"], **write_fs},
            handler=wrap(_copy),
        ),
        Tool(
            name="move_path",
            description="Move or rename a file or folder. Use it when the user asks you to move "
            "or rename something.",
            args_model=MovePathArgs,
            risk=Risk.R2,
            idempotent=False,
            timeout_s=300,
            sandbox="workspace-write",
            fs=write_fs,
            handler=wrap(_move),
        ),
        Tool(
            name="delete_path",
            description="Move a file or folder to the Recycle Bin. Use it when the user asks you "
            "to delete or remove something; it can be restored from there.",
            args_model=DeletePathArgs,
            risk=Risk.R3,
            idempotent=True,
            timeout_s=60,
            sandbox="workspace-write",
            fs=write_fs,
            approval_note="Goes to the Recycle Bin; you can restore it from there.",
            handler=wrap(_delete),
        ),
        Tool(
            name="extract_text",
            description="Save the text of a PDF, Word or PowerPoint file into a new .txt file. "
            "Use it when the user asks for a text copy of a document.",
            args_model=ExtractTextArgs,
            risk=Risk.R1,
            idempotent=True,
            timeout_s=180,
            sandbox="workspace-write",
            fs={"read": ["the user's profile folder"], **write_fs},
            handler=wrap(_extract),
        ),
        Tool(
            name="open_path",
            description="Open a file or folder with the program Windows uses for it (a deck in "
            "PowerPoint, a folder in Explorer). Use it when the user asks you to open a file or "
            "folder.",
            args_model=OpenPathArgs,
            risk=Risk.R1,  # opens a window; closing it undoes it
            idempotent=True,
            timeout_s=20,
            sandbox="full-access",  # the opening program runs as the user, outside any sandbox
            fs={"read": ["the user's profile folder"]},
            handler=wrap(_open),
        ),
        Tool(
            name="open_in_editor",
            description="Open a file or project folder in Visual Studio Code. Use it when the "
            "user asks to open code or a project in their editor.",
            args_model=OpenPathArgs,
            risk=Risk.R1,  # opens a window; closing it undoes it
            idempotent=True,
            timeout_s=20,
            sandbox="full-access",  # the editor runs as the user, outside any sandbox
            fs={"read": ["the user's profile folder"]},
            handler=wrap(_edit),
        ),
    ]
