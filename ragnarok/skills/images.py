"""Skill: download pictures of something into the Downloads folder.

The pictures come from Wikimedia Commons (free images, each with a named author and licence).
This is the first thing in Ragnarok that makes its own internet connection, so it is kept narrow:

- Three fixed Wikimedia hosts only (commons.wikimedia.org to search, upload.wikimedia.org and
  thumb.wikimedia.org for the files). The model never writes a URL; redirects are not followed;
  anything else is refused.
- The search words must come from the user's own message in this turn, so text found in a file or a
  memory cannot be sent out as a search query.
- Only real pictures are kept: the server must say jpeg/png/webp/gif and the first bytes must agree.
  At most 10 pictures, 12 MB each, 40 MB in all.
- Files go to Downloads\\Ragnarok\\<topic>\\ under a cleaned-up name. Nothing is overwritten, so
  asking twice does not download twice, and a half-finished file from a crash is deleted next time.
- credits.txt in that folder names the author, licence and page of every picture.
"""

from __future__ import annotations

import asyncio
import html
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from pydantic import Field

from ragnarok.policy.permissions import Risk
from ragnarok.runtime.tools import CURRENT_TEXT, PROGRESS, Tool, ToolArgs, ToolResult
from ragnarok.skills.folders import downloads_folder
from ragnarok.skills.steps import reply

API = "https://commons.wikimedia.org/w/api.php"
HOSTS = frozenset({"commons.wikimedia.org", "upload.wikimedia.org", "thumb.wikimedia.org"})
USER_AGENT = "Ragnarok/0.1 (local personal agent; https://github.com/OjasJibhakate/Ai-Agent)"
DEFAULT_COUNT = 5
MAX_COUNT = 10
MAX_FILE_BYTES = 12 * 1024 * 1024
MAX_TOTAL_BYTES = 40 * 1024 * 1024
THUMB_WIDTH = (
    1280  # a standard Wikimedia thumbnail size: large enough to look at, not a 20 MB original
)
MIME_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}
RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


class ImageError(ValueError):
    """The request or a download is not acceptable (the message is shown to the model)."""


# ---- checks that decide what may be asked for and what may be kept -------------------------------


def _fold(word: str) -> str:
    return word[:-1] if len(word) > 3 and word.endswith("s") else word


def check_query(query: str, user_text: str) -> str:
    """The search words, if they are plain words the user themselves used in this message."""
    words = re.findall(r"[a-z0-9]+", query.lower())
    if not words or len(query) > 80 or re.search(r"[^\w\s'\-]", query):
        raise ImageError("that search is not usable")
    typed = {_fold(w) for w in re.findall(r"[a-z0-9]+", user_text.lower())}
    unknown = [w for w in words if _fold(w) not in typed]
    if unknown:
        raise ImageError(
            "I only search for words you wrote yourself. "
            f"Use words from your message instead of: {', '.join(unknown)}"
        )
    return " ".join(words)


def safe_url(url: str) -> bool:
    """https, one of the two Wikimedia hosts, no port, no credentials."""
    try:
        parts = urlsplit(url)
        return (
            parts.scheme == "https"
            and (parts.hostname or "").lower() in HOSTS
            and parts.port is None
            and not parts.username
        )
    except ValueError:
        return False


def safe_filename(title: str, mime: str) -> str:
    """A Windows-safe name from a Commons title such as 'File:Cat/with: odd?.jpg'."""
    stem = title.split(":", 1)[-1].rsplit(".", 1)[0] if "." in title else title.split(":", 1)[-1]
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", stem)
    stem = re.sub(r"\s+", "_", stem).strip("._ ")[:80].strip("._ ") or "picture"
    if stem.lower() in RESERVED:
        stem = f"_{stem}"
    return stem + MIME_EXT[mime]


def has_image_signature(data: bytes, mime: str) -> bool:
    """The first bytes of the file agree with what the server claimed."""
    if mime == "image/jpeg":
        return data[:3] == b"\xff\xd8\xff"
    if mime == "image/png":
        return data[:8] == b"\x89PNG\r\n\x1a\n"
    if mime == "image/gif":
        return data[:6] in (b"GIF87a", b"GIF89a")
    if mime == "image/webp":
        return data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    return False


def topic_folder_name(query: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-")[:40] or "pictures"


# ---- Wikimedia Commons ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Candidate:
    title: str
    url: str
    mime: str
    size: int
    page: str
    author: str
    licence: str


def _plain(value: object, limit: int = 120) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", "", str(value or "")))
    return re.sub(r"\s+", " ", text).strip()[:limit]


def parse_candidates(payload: dict) -> list[Candidate]:
    """Pictures from a Commons API reply, best match first. Anything unusual is dropped."""
    pages = (payload.get("query") or {}).get("pages") or []
    found: list[tuple[int, Candidate]] = []
    for page in pages:
        info = (page.get("imageinfo") or [{}])[0]
        mime = info.get("mime")
        url = info.get("thumburl") or info.get("url") or ""
        title = str(page.get("title") or "")
        if mime not in MIME_EXT or not title or not safe_url(url):
            continue
        size = info.get("size")
        meta = info.get("extmetadata") or {}
        found.append(
            (
                int(page.get("index") or 0),
                Candidate(
                    title=title,
                    url=url,
                    mime=mime,
                    size=int(size) if isinstance(size, int) else 0,
                    page=str(info.get("descriptionurl") or ""),
                    author=_plain((meta.get("Artist") or {}).get("value")) or "unknown author",
                    licence=_plain((meta.get("LicenseShortName") or {}).get("value"), 40)
                    or "licence not stated",
                ),
            )
        )
    return [c for _, c in sorted(found, key=lambda pair: pair[0])]


class CommonsClient:
    """Talks to the two fixed hosts and nobody else."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    def session(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=self._transport,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=False,  # a redirect could lead off the two allowed hosts
            timeout=httpx.Timeout(20.0, connect=10.0),
        )

    async def search(self, client: httpx.AsyncClient, query: str, limit: int) -> list[Candidate]:
        params = {
            "action": "query", "format": "json", "formatversion": "2",
            "generator": "search", "gsrsearch": f"{query} filetype:bitmap",
            "gsrnamespace": "6", "gsrlimit": str(limit),
            "prop": "imageinfo", "iiprop": "url|mime|size|extmetadata",
            "iiurlwidth": str(THUMB_WIDTH),
        }  # fmt: skip
        reply = await client.get(API, params=params)
        if reply.status_code != 200:
            raise ImageError(f"Wikimedia Commons answered {reply.status_code}")
        try:
            return parse_candidates(reply.json())
        except ValueError as exc:
            raise ImageError("Wikimedia Commons sent something I could not read") from exc

    async def fetch(self, client: httpx.AsyncClient, item: Candidate, room: int) -> bytes | None:
        """The picture's bytes, or None if it is not an acceptable picture."""
        if not safe_url(item.url):
            return None
        limit = min(MAX_FILE_BYTES, room)
        async with client.stream("GET", item.url) as reply:
            if reply.status_code != 200:
                return None
            kind = reply.headers.get("content-type", "").split(";")[0].strip().lower()
            declared = reply.headers.get("content-length", "")
            if kind != item.mime or (declared.isdigit() and int(declared) > limit):
                return None
            data = bytearray()
            async for chunk in reply.aiter_bytes():
                data += chunk
                if len(data) > limit:
                    return None
        return bytes(data) if has_image_signature(bytes(data), item.mime) else None


# ---- the tool ------------------------------------------------------------------------------------


class DownloadImagesArgs(ToolArgs):
    query: str = Field(
        min_length=2,
        max_length=80,
        description="What the pictures show, in the user's own words, e.g. cat.",
    )
    count: int = Field(default=DEFAULT_COUNT, ge=1, le=100, description="How many pictures.")


def _save(path: Path, data: bytes) -> None:
    part = path.with_name(path.name + ".part")
    part.write_bytes(data)
    os.replace(part, path)  # a crash leaves a .part file, never a half-written picture


def _write_credits(folder: Path, lines: list[str]) -> None:
    credits = folder / "credits.txt"
    have = credits.read_text(encoding="utf-8").splitlines() if credits.exists() else []
    new = [line for line in lines if line not in have]
    if new:
        with credits.open("a", encoding="utf-8") as f:
            f.write("\n".join(new) + "\n")


def image_tools(
    transport: httpx.AsyncBaseTransport | None = None, downloads: Path | None = None
) -> list[Tool]:
    commons = CommonsClient(transport)
    base = downloads or downloads_folder()

    async def download_images(args: DownloadImagesArgs) -> str | ToolResult:
        try:
            query = check_query(args.query, CURRENT_TEXT.get())
        except ImageError as exc:
            return f"error: {exc}"
        wanted = min(args.count, MAX_COUNT)
        folder = base / "Ragnarok" / topic_folder_name(query)
        progress = PROGRESS.get()

        try:
            await asyncio.to_thread(folder.mkdir, parents=True, exist_ok=True)
            for stale in await asyncio.to_thread(lambda: list(folder.glob("*.part"))):
                await asyncio.to_thread(stale.unlink, missing_ok=True)  # left by an earlier crash
            saved, had, credit_lines, spent = [], [], [], 0
            async with commons.session() as client:
                candidates = await commons.search(client, query, min(30, wanted * 3))
                for item in candidates:
                    if len(saved) + len(had) >= wanted:
                        break
                    name = safe_filename(item.title, item.mime)
                    target = folder / name
                    line = f"{name} | {item.author} | {item.licence} | {item.page}"
                    if await asyncio.to_thread(target.exists):
                        had.append(name)
                        credit_lines.append(line)
                        continue
                    if progress:
                        await progress(
                            f"downloading {len(saved) + len(had) + 1} of {wanted}: {name}"
                        )
                    data = await commons.fetch(client, item, MAX_TOTAL_BYTES - spent)
                    if data is None:
                        continue
                    await asyncio.to_thread(_save, target, data)
                    spent += len(data)
                    saved.append(name)
                    credit_lines.append(line)
            if credit_lines:
                await asyncio.to_thread(_write_credits, folder, credit_lines)
        except (httpx.HTTPError, OSError) as exc:
            if isinstance(exc, httpx.HTTPError):
                return "error: I couldn't reach Wikimedia Commons. Is the internet on?"
            return f"error: I couldn't save the pictures ({type(exc).__name__})."
        except ImageError as exc:
            return f"error: {exc}"

        total = len(saved) + len(had)
        if total == 0:
            return reply(f"I found no pictures of {query!r} I could download.")
        parts = [
            f"Saved {len(saved)} picture{'s' if len(saved) != 1 else ''} of {query!r} to {folder}"
        ]
        if had:
            parts.append(f"{len(had)} were already there")
        text = (
            ", ".join(parts) + ". They are from Wikimedia Commons; credits.txt names the authors."
        )
        if args.count > MAX_COUNT:
            text += f" (I download at most {MAX_COUNT} at a time.)"
        if total < wanted:
            text += f" That is all I found ({total} of {wanted})."
        return reply(text)

    return [
        Tool(
            name="download_images",
            description="Download pictures of something (for example cats) from Wikimedia Commons "
            "into the user's Downloads folder. Use it when the user asks to download or save "
            "images, photos or pictures.",
            args_model=DownloadImagesArgs,
            risk=Risk.R1,  # saves new files only; deleting the folder undoes it
            idempotent=True,  # files that already exist are kept, not fetched again
            timeout_s=120,
            sandbox="workspace-write",
            fs={"write": [str(base / "Ragnarok")]},
            network=sorted(HOSTS),
            handler=download_images,
        )
    ]
