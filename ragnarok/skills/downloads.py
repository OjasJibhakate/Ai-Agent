"""Skill: download a file from the internet into Downloads\\Ragnarok.

The user names the file in one of two ways, and nothing else is fetched:
- a short name from the table below ("steam", "chrome", "vscode", ...): official installers, with
  the link fixed in this file, so the model never writes a URL for them;
- a link the user typed in the message being answered. Text found in a file or a memory therefore
  cannot make the agent fetch anything.

What is fetched is checked before it is kept:
- https only, a real domain name (no IP addresses, no localhost), no credentials or odd ports. The
  name is resolved first and refused if it points at a private or internal address, and every
  redirect is checked the same way (at most 5).
- A web page is not a file: HTML answers are refused (the model offers to open the page instead).
- At most 2 GB, and there must be room on the disk. Nothing is overwritten; a half-finished download
  is deleted, never kept as if it were whole.
- Windows is told the file came from the internet (the same mark browsers add), so SmartScreen
  checks programs before they run. A program is saved but never opened or run by us.

The tool is R2, so the owner is asked before every download and sees the link or name.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import mimetypes
import os
import re
import shutil
import socket
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from email.message import Message
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import unquote, urljoin, urlsplit

import httpx
from pydantic import Field, field_validator

from ragnarok.policy.permissions import Risk
from ragnarok.runtime.tools import CURRENT_TEXT, PROGRESS, Tool, ToolArgs, ToolResult
from ragnarok.skills.folders import downloads_folder
from ragnarok.skills.images import USER_AGENT
from ragnarok.skills.steps import reply as end_turn

MAX_BYTES = 2 * 1024**3
MAX_REDIRECTS = 5
SPARE_BYTES = 256 * 1024**2  # keep this much of the disk free
CHUNK = 256 * 1024
PROGRAM_EXTENSIONS = frozenset(
    ".exe .msi .msix .appx .bat .cmd .com .scr .ps1 .psm1 .vbs .vbe .js .jse .wsf .hta .jar .dll "
    ".lnk .reg .cpl .msp .pif".split()
)
RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}
_HOST = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


@dataclass(frozen=True)
class Known:
    label: str
    url: str


# Official installers. Each link was checked to answer with a real installer (not a web page).
KNOWN: dict[str, Known] = {
    "steam": Known("Steam", "https://cdn.akamai.steamstatic.com/client/installer/SteamSetup.exe"),
    "chrome": Known("Google Chrome", "https://dl.google.com/chrome/install/ChromeStandaloneSetup64.exe"),
    "firefox": Known("Firefox", "https://download.mozilla.org/?product=firefox-latest&os=win64&lang=en-US"),
    "vscode": Known("Visual Studio Code", "https://update.code.visualstudio.com/latest/win32-x64-user/stable"),
    "spotify": Known("Spotify", "https://download.scdn.co/SpotifyFullSetup.exe"),
    "zoom": Known("Zoom", "https://zoom.us/client/latest/ZoomInstaller.exe"),
    "telegram": Known("Telegram", "https://telegram.org/dl/desktop/win64"),
    "edge": Known("Microsoft Edge", "https://go.microsoft.com/fwlink/?linkid=2109047&Channel=Stable&language=en&brand=M100"),
    "ollama": Known("Ollama", "https://ollama.com/download/OllamaSetup.exe"),
    "python": Known("Python 3.12.8 (the version this project uses)", "https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe"),
}  # fmt: skip
ALIASES = {
    "google chrome": "chrome", "mozilla firefox": "firefox", "vs code": "vscode",
    "visual studio code": "vscode", "code": "vscode", "microsoft edge": "edge",
    "telegram desktop": "telegram", "python3": "python", "python 3": "python",
}  # fmt: skip


class DownloadError(ValueError):
    """The request can't be done safely (the message is shown to the model and the user)."""


# ---- what may be fetched -------------------------------------------------------------------------


_LOCAL_FILE_NAME = re.compile(
    r"[^/\\:]+\.(?:pdf|docx?|pptx?|xlsx?|txt|csv|md|rtf|odt|png|jpe?g|zip)", re.IGNORECASE
)


def _known_name(source: str) -> str | None:
    key = re.sub(r"\s+", " ", source.strip().lower())
    key = ALIASES.get(key, key)
    return key if key in KNOWN else None


def _mentioned(word: str, user_text: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", user_text.lower()) is not None


def resolve_source(source: str, user_text: str) -> tuple[str, str]:
    """(url, label) for a known name or a link the user typed; DownloadError otherwise."""
    source = source.strip()
    name = _known_name(source)
    if name is not None:
        words = [name, *(a for a, target in ALIASES.items() if target == name)]
        if not any(_mentioned(w, user_text) for w in words):
            raise DownloadError(f"I only download what you ask for, and you did not mention {name}")
        return KNOWN[name].url, KNOWN[name].label
    if "://" not in source and not re.match(r"^[a-z0-9.-]+\.[a-z]{2,}/", source.lower()):
        if _LOCAL_FILE_NAME.fullmatch(source):
            raise DownloadError(
                f"{source!r} is a file name, not something on the internet. If it is on this "
                "computer (in Downloads, say), find it with find_files and read it with "
                "read_file; do not download it."
            )
        known = ", ".join(sorted(KNOWN))
        raise DownloadError(
            f"I have no trusted link for {source!r}. Paste its download link in your message and "
            f"I will fetch it. (Programs I know by name: {known}.)"
        )
    typed = source.removeprefix("https://").removeprefix("http://").rstrip("/").lower()
    if typed not in user_text.lower():
        raise DownloadError(
            "I only download a link you typed in your message. Paste the link here and ask again."
        )
    return validate_link(source if "://" in source else f"https://{source}"), source


def validate_link(url: str) -> str:
    """The URL, if it is an ordinary https link to a named host. DownloadError otherwise."""
    if len(url) > 2000 or re.search(r"[\s<>\"'`\\^{}|\x00-\x1f]", url):
        raise DownloadError("that is not a link I can download")
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError as exc:
        raise DownloadError("that is not a link I can download") from exc
    if parts.scheme != "https":
        raise DownloadError("I only download over https")
    if parts.username or parts.password or port not in (None, 443) or not _HOST.match(host):
        raise DownloadError(
            "I only download from ordinary web addresses (no IP addresses or ports)"
        )
    return url


Resolver = Callable[[str], Awaitable[list[str]]]


async def system_resolver(host: str) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


def is_public(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address.split("%")[0])
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


async def require_public(host: str, resolver: Resolver) -> None:
    try:
        addresses = await resolver(host)
    except OSError as exc:
        raise DownloadError(f"I could not find {host}. Is the internet on?") from exc
    if not addresses or not all(is_public(a) for a in addresses):
        raise DownloadError(
            f"{host} points at a private or internal address, so I will not fetch it"
        )


# ---- names, types, marks ------------------------------------------------------------------------


def clean_name(raw: str, content_type: str = "") -> str:
    name = unquote(raw).replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r'[<>:"|?*\x00-\x1f]', " ", name)
    name = re.sub(r"\s+", " ", name).strip(". ")
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem:
        stem, ext = name, ""
    if not ext:
        ext = (mimetypes.guess_extension(content_type.split(";")[0].strip()) or ".bin").lstrip(".")
    ext = re.sub(r"[^A-Za-z0-9]", "", ext)[:10] or "bin"
    stem = stem[:100].strip(". ") or "download"
    if stem.lower() in RESERVED:
        stem = f"_{stem}"
    return f"{stem}.{ext}"


def name_from_response(headers: httpx.Headers, final_url: str, label: str) -> str:
    disposition = headers.get("content-disposition", "")
    if disposition:
        msg = Message()
        msg["content-disposition"] = disposition
        filename = msg.get_filename()
        if filename:
            return clean_name(str(filename), headers.get("content-type", ""))
    last = urlsplit(final_url).path.rsplit("/", 1)[-1]
    return clean_name(last or label, headers.get("content-type", ""))


def is_program(name: str) -> bool:
    return Path(name).suffix.lower() in PROGRAM_EXTENSIONS


def unique_path(folder: Path, name: str, expected_size: int | None) -> tuple[Path, bool]:
    """(where to save, True if this exact file is already there)."""
    target = folder / name
    if not target.exists():
        return target, False
    if expected_size is not None and target.stat().st_size == expected_size:
        return target, True
    stem, suffix = target.stem, target.suffix
    for n in range(1, 1000):
        candidate = folder / f"{stem} ({n}){suffix}"
        if not candidate.exists():
            return candidate, False
    raise DownloadError("there are too many files with that name already")


def mark_from_internet(path: Path, url: str) -> None:
    """The mark browsers add so Windows (SmartScreen) checks a downloaded program before it runs."""
    if sys.platform != "win32":
        return
    try:
        with open(f"{path}:Zone.Identifier", "w", encoding="ascii", errors="ignore") as f:
            f.write(f"[ZoneTransfer]\r\nZoneId=3\r\nHostUrl={url}\r\n")
    except OSError:
        pass  # not NTFS, or not Windows: nothing to mark


def _sink(f: BinaryIO, digest: Any, chunk: bytes) -> None:
    f.write(chunk)
    digest.update(chunk)


def _megabytes(n: int) -> str:
    return f"{n / 1024**2:.1f} MB" if n >= 1024**2 else f"{max(1, n // 1024)} KB"


# ---- the tool ------------------------------------------------------------------------------------


class DownloadFileArgs(ToolArgs):
    source: str = Field(
        min_length=2,
        max_length=2000,
        description="A program name like steam or chrome, or the link the user typed.",
    )

    @field_validator("source")
    @classmethod
    def _must_be_something_we_would_fetch(cls, value: str) -> str:
        """Checked while the arguments are parsed, which is before the owner is asked: they are
        only asked about downloads that can really go ahead."""
        try:
            resolve_source(value, CURRENT_TEXT.get())
        except DownloadError as exc:
            raise ValueError(str(exc)) from exc
        return value


def download_tools(
    transport: httpx.AsyncBaseTransport | None = None,
    downloads: Path | None = None,
    resolver: Resolver = system_resolver,
) -> list[Tool]:
    base = downloads or downloads_folder()
    folder = base / "Ragnarok"

    def session() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=transport,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=False,  # every hop is checked by hand
            timeout=httpx.Timeout(30.0, connect=10.0),
        )

    async def fetch(url: str, label: str) -> ToolResult:
        progress = PROGRESS.get()
        async with session() as client:
            for _ in range(MAX_REDIRECTS + 1):
                validate_link(url)
                await require_public(urlsplit(url).hostname or "", resolver)
                async with client.stream("GET", url) as reply:
                    if reply.status_code in (301, 302, 303, 307, 308):
                        where = reply.headers.get("location")
                        if not where:
                            raise DownloadError("the server sent me nowhere")
                        url = urljoin(url, where)
                        continue
                    return await save(reply, url, label, progress)
        raise DownloadError("the link redirected too many times")

    async def save(reply: httpx.Response, url: str, label: str, progress) -> ToolResult:
        if reply.status_code != 200:
            raise DownloadError(f"the server answered {reply.status_code}, not a file")
        kind = reply.headers.get("content-type", "").split(";")[0].strip().lower()
        if kind in ("text/html", "application/xhtml+xml") and not re.search(
            r"\.html?$", urlsplit(url).path, re.I
        ):
            raise DownloadError(
                "that link is a web page, not a file. I can open it in your browser instead."
            )
        declared = reply.headers.get("content-length", "")
        size = int(declared) if declared.isdigit() else None
        if size is not None and size > MAX_BYTES:
            raise DownloadError(f"that file is {_megabytes(size)}; my limit is 2 GB")
        name = name_from_response(reply.headers, url, label)
        await asyncio.to_thread(folder.mkdir, parents=True, exist_ok=True)
        free = (await asyncio.to_thread(shutil.disk_usage, folder)).free
        if free < (size or 0) + SPARE_BYTES:
            raise DownloadError("there is not enough free disk space for that file")
        target, already = await asyncio.to_thread(unique_path, folder, name, size)
        if already:
            return end_turn(f"Already downloaded: {target.name} is in {folder}.")

        part = target.with_name(target.name + ".part")
        digest = hashlib.sha256()
        got, last_note = 0, time.monotonic()
        try:
            with part.open("wb") as f:
                async for chunk in reply.aiter_bytes(CHUNK):
                    got += len(chunk)
                    if got > MAX_BYTES:
                        raise DownloadError("that file is bigger than my 2 GB limit")
                    await asyncio.to_thread(_sink, f, digest, chunk)
                    if progress and time.monotonic() - last_note > 1.0:
                        last_note = time.monotonic()
                        of = f" of {_megabytes(size)}" if size else ""
                        await progress(f"downloading {target.name}: {_megabytes(got)}{of}")
            if size is not None and got != size:
                raise DownloadError("the download stopped early, so I threw the file away")
            await asyncio.to_thread(os.replace, part, target)
        finally:
            part.unlink(
                missing_ok=True
            )  # never leave half a file behind (sync: runs when cancelled)
        await asyncio.to_thread(mark_from_internet, target, url)
        text = f"Saved {target.name} ({_megabytes(got)}) to {folder}."
        if is_program(target.name):
            text += " It is a program: I have not run it, and Windows will check it before you do."
        return end_turn(text + f"\nSHA-256 {digest.hexdigest()}")

    async def download_file(args: DownloadFileArgs) -> str | ToolResult:
        try:
            url, label = resolve_source(args.source, CURRENT_TEXT.get())
            for stale in await asyncio.to_thread(
                lambda: list(folder.glob("*.part")) if folder.is_dir() else []
            ):
                await asyncio.to_thread(stale.unlink, missing_ok=True)  # left by an earlier crash
            return await fetch(url, label)
        except DownloadError as exc:
            return f"error: {exc}"
        except httpx.HTTPError:
            return "error: I couldn't reach the server. Is the internet on?"
        except OSError as exc:
            return f"error: I couldn't save the file ({type(exc).__name__})."

    return [
        Tool(
            name="download_file",
            description="Download a file or program to the user's Downloads folder, given a name "
            "like steam or chrome, or a link they typed. Use it when the user asks to download "
            "something from the internet that is not a picture, never a file already on this "
            "computer.",
            args_model=DownloadFileArgs,
            risk=Risk.R2,  # files from the internet: the owner is asked every time
            idempotent=False,
            timeout_s=1800,
            sandbox="workspace-write",
            fs={"write": [str(folder)]},
            network=[
                "https links the user typed",
                *sorted({urlsplit(k.url).hostname or "" for k in KNOWN.values()}),
            ],
            approval_note="Saves a file from the internet into Downloads\\Ragnarok. "
            "I never open or run it.",
            handler=download_file,
        )
    ]
