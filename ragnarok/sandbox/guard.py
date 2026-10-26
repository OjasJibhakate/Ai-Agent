"""Runs inside the sandboxed Python process, before the skill's script (FR2.3).

Usage: python -I guard.py script.py [args]. It installs an audit hook, then runs the script.
This is defense in depth, not a boundary: native code can get around an audit hook. The Job Object
set up by `runner.py` and the scrubbed environment are what the OS enforces.

It refuses: opening anything under a denied path (the vault, keys, other programs' data); writing
anywhere but the run's work folder (and approved write roots); network connections while offline;
loading ctypes; starting other programs.
"""

import json
import os
import runpy
import sys


def _norm(path: str) -> str:
    return os.path.normcase(os.path.realpath(path))


DENY = [_norm(p) for p in json.loads(os.environ.get("RAGNAROK_DENY", "[]"))]
OFFLINE = os.environ.get("RAGNAROK_OFFLINE") == "1"
_EXTRA_ROOTS = json.loads(os.environ.get("RAGNAROK_WRITE_ROOTS", "[]"))
WRITE_ROOTS = [_norm(p) for p in [os.environ.get("RAGNAROK_WORKDIR", ""), *_EXTRA_ROOTS] if p]
WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
SPAWN_EVENTS = {"subprocess.Popen", "os.system", "os.exec", "os.spawn", "os.posix_spawn"}


def _inside(path: str, roots: list[str]) -> bool:
    return any(path == r or path.startswith(r + os.sep) for r in roots)


def hook(event: str, args: tuple) -> None:
    if event == "open":
        target = args[0]
        if isinstance(target, int):
            return  # an already-open handle
        try:
            path = _norm(os.fsdecode(target))
        except (TypeError, ValueError):
            return
        if any(path.startswith(d) for d in DENY):
            raise PermissionError(f"sandbox: {os.fsdecode(target)} is off limits")
        flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
        mode = args[1] if len(args) > 1 and isinstance(args[1], str) else "r"
        writing = bool(flags & WRITE_FLAGS) or any(c in mode for c in "wax+")
        if writing and not _inside(path, WRITE_ROOTS) and path != os.devnull:
            raise PermissionError(f"sandbox: writing {os.fsdecode(target)} is not allowed here")
    elif event == "socket.connect" and OFFLINE:
        raise PermissionError("sandbox: no network for this skill")
    elif event == "import" and args[0] in ("ctypes", "_ctypes"):
        raise ImportError("sandbox: ctypes is not available to skills")
    elif event in SPAWN_EVENTS:
        raise PermissionError("sandbox: skills may not start other programs")


def main() -> None:
    script = sys.argv[1]
    sys.argv = sys.argv[1:]
    sys.addaudithook(hook)
    runpy.run_path(script, run_name="__main__")


if __name__ == "__main__":
    main()
