"""Skill: run a PowerShell command and report what it printed.

This is the most powerful tool the agent has, so it is R3: in the default mode every command is
shown to the owner and needs a yes; `/mode full` lifts that. It is NOT a sandbox. What stays on in
every mode:

- a short list of commands that are refused outright (formatting disks, recursive deletes aimed at a
  drive or the profile folder, download-and-run, encoded commands, scheduled tasks and services,
  shutdown, anything that names Ragnarok's own data, saved logins or other programs' sessions).
  A list like this can be got around by a determined attacker; it exists to stop honest mistakes by
  a small model and the most common injected one-liners;
- no stdin (nothing can wait for typing), a time limit (default 60 s, at most 300 s) after which the
  command and everything it started is killed, and an output cap;
- the child process gets the environment without anything named like a key, token, secret or
  password, and without Ragnarok's own settings.

The output comes back inside <command_output> tags, which the system prompt says is data.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import sys
from pathlib import Path

from pydantic import Field

from ragnarok.policy.permissions import Risk
from ragnarok.runtime.tools import Tool, ToolArgs
from ragnarok.skills.pathguard import PathGuard, PathRefused

DEFAULT_TIMEOUT = 60
MAX_TIMEOUT = 300
KEEP_CHARS = 3000  # the start and the end of long output are both kept
MAX_COMMAND_CHARS = 4000
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200
SECRET_ENV = re.compile(r"(key|token|secret|passw|credential|auth|cookie)", re.IGNORECASE)

_DROP = r"(?:remove-item|ri|rm|del|erase|rd|rmdir)"
_RECURSE = r"(?:-recurse|-r\b|/s\b)"
# Targets whose recursive deletion is never a good idea: a drive, the system, the profile folder.
_BROAD_TARGET = re.compile(
    r"(?:^|[\s\"'])(?:[a-z]:[\\/]?\*?"
    r"|[a-z]:[\\/](?:windows|program files|programdata|users)[\\/]?\*?"
    r"|%userprofile%[\\/]?\*?|\$env:userprofile[\\/]?\*?|\$home[\\/]?\*?|~[\\/]?\*?|[\\/]\*"
    r"|\.\.[\\/]?\*?|\*)(?=$|[\s\"';|&])",
    re.IGNORECASE,
)
REFUSED: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"\b(?:format-volume|diskpart|bcdedit|clear-disk|initialize-disk|remove-partition|"
            r"cipher\s+/w|vssadmin\s+delete|wbadmin\s+delete)\b|\bformat\s+[a-z]:",
            re.IGNORECASE,
        ),
        "that changes disks or the boot setup",
    ),
    (
        re.compile(
            r"(?:iwr|invoke-webrequest|irm|invoke-restmethod|curl|wget|downloadstring|"
            r"downloadfile|start-bitstransfer)\b.*(?:\|\s*(?:iex|invoke-expression)\b|"
            r"\b(?:iex|invoke-expression)\b)|(?:iex|invoke-expression)\b.*"
            r"(?:downloadstring|iwr|irm|invoke-webrequest|invoke-restmethod)",
            re.IGNORECASE | re.DOTALL,
        ),
        "that downloads something and runs it straight away",
    ),
    (
        re.compile(r"(?:^|\s)-e(?:nc|ncodedcommand)?\s+[a-z0-9+/=]{20,}|frombase64string", re.I),
        "encoded commands hide what they do",
    ),
    (
        re.compile(
            r"\b(?:schtasks|new-scheduledtask|register-scheduledtask|new-service|"
            r"sc(?:\.exe)?\s+(?:create|config|delete|stop)|set-mppreference|add-mppreference|"
            r"netsh\s+advfirewall)\b|\breg(?:\.exe)?\s+(?:delete|add|import)\b|"
            r"\\currentversion\\run\b|\bhk(?:lm|cr)[:\\]",
            re.IGNORECASE,
        ),
        "that changes startup entries, services, the registry or security settings",
    ),
    (
        re.compile(
            r"\b(?:start|start-process|invoke-item|ii|explorer|cmd\s*/c\s*start|chrome|msedge|firefox"
            r"|iexplore)(?:\.exe)?\b[^|;&\n]*\bhttps?://|\b(?:start|start-process)\b[^|;&\n]*\b(?:www\.)",
            re.IGNORECASE,
        ),
        "use the website tool to open web pages (only addresses you typed or sites I know)",
    ),
    (
        re.compile(r"\b(?:shutdown|restart-computer|stop-computer)\b", re.IGNORECASE),
        "shutting the computer down could lose your unsaved work",
    ),
    (
        re.compile(
            r"[\\/]\.(?:ssh|aws|azure|gnupg|kube|docker|claude|codex)\b|id_(?:rsa|ed25519|ecdsa)|"
            r"login data|logins\.json|key[34]\.db|\\user data\\|"
            r"appdata[\\/](?:roaming[\\/]microsoft[\\/](?:credentials|protect|vault)|"
            r"local[\\/](?:google|microsoft[\\/]edge))|"
            r"\bcmdkey\b|\bvaultcmd\b|mimikatz",
            re.IGNORECASE,
        ),
        "that touches saved logins or other programs' private data",
    ),
]


class CommandRefused(ValueError):
    """The command is on the never-run list (the message is shown to the model and the user)."""


def check_command(command: str, guard: PathGuard) -> None:
    text = command.strip()
    if not text:
        raise CommandRefused("the command is empty")
    if len(text) > MAX_COMMAND_CHARS or "\x00" in text:
        raise CommandRefused("the command is too long or malformed")
    for pattern, why in REFUSED:
        if pattern.search(text):
            raise CommandRefused(f"I won't run that: {why}")
    lowered = os.path.normcase(text.replace("/", "\\")).lower()
    names_data = any(os.path.normcase(str(d)).lower() in lowered for d in guard.data_dirs)
    in_appdata = re.search(r"localappdata|appdata[\\/]+local", lowered) is not None
    if names_data or ("ragnarok" in lowered and in_appdata):
        raise CommandRefused("I won't run that: it names Ragnarok's own data, which is off limits")
    for segment in re.split(r"[;&\n]|\|\|", text):
        if re.search(rf"\b{_DROP}\b", segment, re.I) and re.search(_RECURSE, segment, re.I):
            if _BROAD_TARGET.search(segment):
                raise CommandRefused(
                    "I won't run that: it deletes a whole drive, a system folder or your profile"
                )


def child_environment(env: dict[str, str] | None = None) -> dict[str, str]:
    """The environment the command sees: no keys, tokens or passwords, none of our settings."""
    source = os.environ if env is None else env
    return {
        k: v
        for k, v in source.items()
        if not SECRET_ENV.search(k) and not k.upper().startswith("RAGNAROK")
    }


def powershell_path() -> str:
    root = os.environ.get("SystemRoot", r"C:\Windows")
    return str(Path(root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")


def shorten(text: str) -> str:
    if len(text) <= KEEP_CHARS * 2:
        return text
    cut = len(text) - KEEP_CHARS * 2
    return f"{text[:KEEP_CHARS]}\n... [{cut} characters left out] ...\n{text[-KEEP_CHARS:]}"


async def _kill_tree(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is None and sys.platform == "win32":
        killer = await asyncio.create_subprocess_exec(
            "taskkill", "/F", "/T", "/PID", str(proc.pid),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
        )  # fmt: skip
        await killer.wait()
    with contextlib.suppress(ProcessLookupError):
        proc.kill()


class RunCommandArgs(ToolArgs):
    command: str = Field(
        min_length=1, max_length=MAX_COMMAND_CHARS, description="The PowerShell command to run."
    )
    folder: str | None = Field(
        default=None, max_length=400, description="Where to run it (default: the user's folder)."
    )
    timeout_seconds: int = Field(default=DEFAULT_TIMEOUT, ge=1, le=MAX_TIMEOUT)


def shell_tools(home: Path | None = None, guard: PathGuard | None = None) -> list[Tool]:
    guard = guard or PathGuard(home)

    async def run_command(args: RunCommandArgs) -> str:
        try:
            check_command(args.command, guard)
            cwd = guard.writable(args.folder) if args.folder else guard.home
        except (CommandRefused, PathRefused) as exc:
            return f"error: {exc}"
        if not cwd.is_dir():
            return f"error: {cwd} is not a folder"
        if sys.platform != "win32":
            return "error: commands run through PowerShell, which is only set up on Windows"

        script = (
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
            "$ProgressPreference = 'SilentlyContinue'; " + args.command
        )
        try:
            proc = await asyncio.create_subprocess_exec(
                powershell_path(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-Command", script,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(cwd),
                env=child_environment(),
                creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
            )  # fmt: skip
        except OSError as exc:
            return f"error: couldn't start PowerShell ({type(exc).__name__})"

        chunks: list[bytes] = []
        size = 0

        async def drain() -> None:
            nonlocal size
            assert proc.stdout is not None
            while data := await proc.stdout.read(8192):
                if (
                    size < 400_000
                ):  # keep reading afterwards so the command never blocks on a full pipe
                    chunks.append(data)
                    size += len(data)

        reader = asyncio.create_task(drain())
        timed_out = False
        try:
            await asyncio.wait_for(proc.wait(), args.timeout_seconds)
        except TimeoutError:
            timed_out = True
        finally:
            await _kill_tree(proc)  # no-op when it finished by itself
            with contextlib.suppress(Exception):
                await asyncio.wait_for(reader, 3)
            reader.cancel()

        output = shorten(
            b"".join(chunks).decode("utf-8", errors="replace").replace("\r\n", "\n").strip()
        )
        status = (
            f"stopped after {args.timeout_seconds} s (the command and what it started were killed)"
            if timed_out
            else f"exit code {proc.returncode}"
        )
        body = output if output else "(no output)"
        return f"{status}\n<command_output>\n{body}\n</command_output>"

    return [
        Tool(
            name="run_command",
            description="Run a PowerShell command on the user's computer and return what it "
            "printed. Use it only when no other tool can do the job.",
            args_model=RunCommandArgs,
            risk=Risk.R3,
            idempotent=False,
            timeout_s=MAX_TIMEOUT + 30,
            sandbox="full-access",
            fs={"read": ["the user's profile folder"], "write": ["wherever the command writes"]},
            network=["whatever the command connects to"],
            approval_note="Runs this PowerShell command on your computer, as you. It can change "
            "or delete files. Disk, startup, and download-and-run commands are refused.",
            handler=run_command,
        )
    ]
