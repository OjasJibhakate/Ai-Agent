"""Run a skill's command in a restricted child process (FR2.3).

What is enforced, and by what:

- Windows Job Object: memory cap, a cap on how many processes it may start, and kill-on-close, so
  the script and anything it started die together on timeout or when we close the job.
- A scrubbed environment: nothing named like a key, token or secret, none of Ragnarok's own
  settings, and the proxy variables pointing at a closed port while the skill is offline.
- Narrow I/O: arguments arrive on stdin, the result leaves on stdout, there is a time limit and an
  output cap. The skill never gets the vault key or a database handle.
- For Python skills, `guard.py`'s audit hook (see there) refuses denied paths, writes outside the
  run's work folder, network while offline, ctypes and starting programs.

What is NOT enforced: a restricted token (so reads outside the denied list, and a native extension
that skips the audit hook, are not blocked at the OS level), and a firewall rule (offline is by
environment and audit hook). The demo says so openly.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

GUARD = Path(__file__).with_name("guard.py")
MAX_OUTPUT = 20_000
MEMORY_LIMIT_MB = 1024
MAX_PROCESSES = 8
CREATE_NO_WINDOW = 0x08000000
SECRET_ENV = re.compile(r"(key|token|secret|passw|credential|auth|cookie)", re.IGNORECASE)
KEEP_ENV = ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "USERPROFILE", "OS")
SHELLS = {
    "cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh", "pwsh.exe", "bash", "bash.exe",
    "wsl", "wsl.exe", "wscript", "wscript.exe", "cscript", "cscript.exe", "mshta", "mshta.exe",
}  # fmt: skip


class SandboxRefused(Exception):
    """The command may not be run at all."""


@dataclass
class SandboxResult:
    ok: bool
    error_class: str = ""  # denied | timeout | tool_error | unavailable
    stdout: str = ""
    stderr: str = ""
    seconds: float = 0.0
    limits: str = ""  # "job" when the Job Object was applied, "none" when Windows refused it
    notes: list[str] = field(default_factory=list)


def scrubbed_env(
    workdir: Path, *, offline: bool, deny: list[str], write_roots: list[str]
) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k.upper() in KEEP_ENV}
    system32 = os.path.join(os.environ.get("SYSTEMROOT", r"C:\Windows"), "System32")
    env["PATH"] = os.pathsep.join([str(Path(sys.executable).parent), system32])
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["RAGNAROK_WORKDIR"] = str(workdir)
    env["RAGNAROK_DENY"] = json.dumps(deny)
    env["RAGNAROK_WRITE_ROOTS"] = json.dumps(write_roots)
    if offline:
        env["RAGNAROK_OFFLINE"] = "1"
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy"):
            env[name] = "http://127.0.0.1:9"  # a closed port
    return env


def _make_job() -> object | None:
    """A Job Object with the limits, or None if Windows will not give us one."""
    try:
        import win32job

        job = win32job.CreateJobObject(None, "")
        info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
        limits = info["BasicLimitInformation"]
        limits["LimitFlags"] |= (
            win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            | win32job.JOB_OBJECT_LIMIT_PROCESS_MEMORY
            | win32job.JOB_OBJECT_LIMIT_ACTIVE_PROCESS
        )
        limits["ActiveProcessLimit"] = MAX_PROCESSES
        info["ProcessMemoryLimit"] = MEMORY_LIMIT_MB * 1024 * 1024
        win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
        return job
    except Exception:
        return None


def _assign(job: object, pid: int) -> bool:
    try:
        import win32api
        import win32con
        import win32job

        rights = win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE
        handle = win32api.OpenProcess(rights, False, pid)
        try:
            win32job.AssignProcessToJobObject(job, handle)
        finally:
            win32api.CloseHandle(handle)
        return True
    except Exception:
        return False


def _kill(job: object | None, proc: asyncio.subprocess.Process) -> None:
    if job is not None:
        try:
            import win32job

            win32job.TerminateJobObject(job, 1)
            return
        except Exception:
            pass
    with contextlib.suppress(ProcessLookupError):
        proc.kill()


def build_argv(argv: list[str], cwd: Path) -> list[str]:
    """The command to start. Python skills go through the guard; there is never a shell string."""
    if not argv or not all(isinstance(a, str) for a in argv):
        raise SandboxRefused("the command is empty")
    program = os.path.basename(argv[0]).lower()
    if program in SHELLS:
        raise SandboxRefused(f"{argv[0]} is a shell; skills run programs directly")
    if program in ("python", "python.exe", "python3", "py"):
        script = Path(argv[1]) if len(argv) > 1 else None
        if script is None or script.suffix != ".py":
            raise SandboxRefused("a Python skill must run a .py script")
        target = (cwd / script).resolve()
        if not target.is_relative_to(cwd.resolve()):
            raise SandboxRefused("the script must be inside the skill's folder")
        return [sys.executable, "-I", str(GUARD), str(target), *argv[2:]]
    return argv


async def run_sandboxed(
    argv: list[str],
    stdin_text: str,
    *,
    cwd: Path,
    workdir: Path,
    timeout_s: float,
    offline: bool = True,
    deny: list[str] | None = None,
    write_roots: list[str] | None = None,
) -> SandboxResult:
    started = time.monotonic()
    try:
        command = build_argv(argv, cwd)
    except SandboxRefused as exc:
        return SandboxResult(False, "denied", stderr=str(exc))
    workdir.mkdir(parents=True, exist_ok=True)
    env = scrubbed_env(workdir, offline=offline, deny=deny or [], write_roots=write_roots or [])
    job = _make_job()
    try:
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd),
            env=env,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, ValueError) as exc:
        return SandboxResult(False, "unavailable", stderr=f"{type(exc).__name__}: {exc}")
    limits = "none"
    if job is not None and proc.pid is not None and _assign(job, proc.pid):
        limits = "job"
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin_text.encode()), timeout_s)
    except TimeoutError:
        _kill(job if limits == "job" else None, proc)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.communicate(), 5)
        return SandboxResult(
            False, "timeout", seconds=time.monotonic() - started, limits=limits,
            stderr=f"stopped after {timeout_s:g} s",
        )  # fmt: skip
    finally:
        if job is not None:
            with contextlib.suppress(Exception):
                import win32api

                win32api.CloseHandle(job)  # kill-on-close: nothing it started outlives it
    text_out = out.decode("utf-8", "replace")
    text_err = err.decode("utf-8", "replace")
    notes = []
    if len(text_out) > MAX_OUTPUT:
        text_out = text_out[:MAX_OUTPUT]
        notes.append("output cut")
    ok = proc.returncode == 0
    return SandboxResult(
        ok, "" if ok else "tool_error", text_out, text_err[-2000:],
        time.monotonic() - started, limits, notes,
    )  # fmt: skip
