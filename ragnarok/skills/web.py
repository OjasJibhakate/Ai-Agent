"""Skill: open a website, or a search on a site, in the user's browser.

The model names a site ("youtube", "yt", "wikipedia") and optionally words to search for; this code
builds the URL from a fixed table, so the model never writes a URL for the common sites. Anything
else is opened only when the user typed that address themselves in the message being answered, which
stops text found in a file or a memory from sending the browser somewhere. Only https pages on real
domain names are opened (no file:, javascript:, localhost, IP addresses or embedded credentials).

This opens a page; it cannot read or click anything on it.
"""

from __future__ import annotations

import asyncio
import re
import subprocess
import sys
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import quote_plus, urlsplit

from pydantic import Field

from ragnarok.policy.permissions import Risk
from ragnarok.runtime.tools import CURRENT_TEXT, Tool, ToolArgs, ToolResult

BROWSERS = {  # name -> (executable in the registry's App Paths, label shown to the user)
    "chrome": ("chrome.exe", "Google Chrome"),
    "edge": ("msedge.exe", "Microsoft Edge"),
    "firefox": ("firefox.exe", "Firefox"),
}


@dataclass(frozen=True)
class Site:
    label: str
    home: str
    search: str | None  # URL template with {q}, or None if the site has no search page


SITES: dict[str, Site] = {
    "youtube": Site("YouTube", "https://www.youtube.com/", "https://www.youtube.com/results?search_query={q}"),
    "google": Site("Google", "https://www.google.com/", "https://www.google.com/search?q={q}"),
    "wikipedia": Site("Wikipedia", "https://en.wikipedia.org/", "https://en.wikipedia.org/w/index.php?search={q}"),
    "github": Site("GitHub", "https://github.com/", "https://github.com/search?q={q}"),
    "stackoverflow": Site("Stack Overflow", "https://stackoverflow.com/", "https://stackoverflow.com/search?q={q}"),
    "reddit": Site("Reddit", "https://www.reddit.com/", "https://www.reddit.com/search/?q={q}"),
    "maps": Site("Google Maps", "https://www.google.com/maps", "https://www.google.com/maps/search/{q}"),
    "gmail": Site("Gmail", "https://mail.google.com/", None),
    "drive": Site("Google Drive", "https://drive.google.com/", None),
    "chatgpt": Site("ChatGPT", "https://chatgpt.com/", None),
}  # fmt: skip
ALIASES = {
    "yt": "youtube", "you tube": "youtube", "youtube.com": "youtube", "google.com": "google",
    "wiki": "wikipedia", "wikipedia.org": "wikipedia", "github.com": "github", "git hub": "github",
    "stack overflow": "stackoverflow", "stackoverflow.com": "stackoverflow", "so": "stackoverflow",
    "google maps": "maps", "gmaps": "maps", "map": "maps", "mail": "gmail", "email": "gmail",
    "google drive": "drive", "gdrive": "drive", "reddit.com": "reddit",
}  # fmt: skip
_HOST = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


class WebError(ValueError):
    """The request can't be turned into a safe page to open (the message is shown to the model)."""


class BrowserLauncher(Protocol):
    def launch(self, browser: str, url: str) -> str:
        """Open `url`; returns the name of the browser that was actually used."""
        ...


class WindowsBrowsers:
    """Launches a named browser from its registered install path, else the default browser."""

    @staticmethod
    def find(browser: str) -> Path | None:
        if sys.platform != "win32" or browser not in BROWSERS:
            return None
        import winreg

        exe = BROWSERS[browser][0]
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(
                    hive, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}"
                ) as key:
                    path = Path(winreg.QueryValue(key, None))
                    if path.is_file():
                        return path
            except OSError:
                continue
        return None

    def launch(self, browser: str, url: str) -> str:
        path = self.find(browser)
        if path is not None:
            subprocess.Popen([str(path), url])  # our own validated https URL, never a shell string
            return BROWSERS[browser][1]
        if not webbrowser.open(url):
            raise OSError("no browser could be started")
        return (
            "your default browser"
            if browser == "default"
            else f"your default browser ({browser} was not found)"
        )


def _normalize_site(text: str) -> str:
    key = re.sub(r"\s+", " ", text.strip().lower()).removeprefix("https://").removeprefix("http://")
    key = key.removeprefix("www.").rstrip("/")
    return ALIASES.get(key, key)


def _typed_by_user(candidate: str, user_text: str) -> bool:
    return candidate.lower() in user_text.lower()


def build_url(site: str, search: str | None, user_text: str) -> tuple[str, str]:
    """Return (url, label). Raises WebError when the request is not safe or not understood."""
    query = (search or "").strip()
    if len(query) > 300 or any(ch in query for ch in "\r\n\x00"):
        raise WebError("that search text is not usable")
    key = _normalize_site(site)
    known = SITES.get(key)
    if known is not None:
        if query and known.search:
            return known.search.format(q=quote_plus(query)), known.label
        if query:  # a site with no search page: search it through Google instead
            host = urlsplit(known.home).hostname
            return (
                f"https://www.google.com/search?q={quote_plus(f'site:{host} {query}')}",
                known.label,
            )
        return known.home, known.label

    # Anything else: only if the user typed that address in this very message.
    raw = site.strip()
    if re.search(r"[\s<>\"'`\\^{}|]", raw) or "@" in raw:
        raise WebError("that is not a web address I can open")
    parts = urlsplit(raw if "://" in raw else f"https://{raw}")
    host = (parts.hostname or "").lower()
    if (
        parts.scheme not in ("http", "https")
        or not _HOST.match(host)
        or parts.username
        or parts.port
    ):
        raise WebError("I only open ordinary https web addresses")
    if not _typed_by_user(raw.removeprefix("https://").removeprefix("http://"), user_text):
        raise WebError(
            "I only open sites I know, or an address you type yourself. "
            f"Type {host} in your message and I will open it."
        )
    path = parts.path or "/"
    url = f"https://{host}{path}" + (f"?{parts.query}" if parts.query else "")
    if query:
        url = f"https://www.google.com/search?q={quote_plus(f'site:{host} {query}')}"
    return url, host


class OpenWebsiteArgs(ToolArgs):
    site: str = Field(
        min_length=2,
        max_length=200,
        description="youtube, google, wikipedia, github... or a web address.",
    )
    search: str | None = Field(
        default=None, max_length=300, description="What to search for on that site."
    )
    browser: Literal["default", "chrome", "edge", "firefox"] = Field(
        default="default", description="The browser the user named, if any."
    )


# Words that mean the user wants something this skill cannot do on the page itself.
BEYOND_PAGE = re.compile(
    r"\b(play|click|press|scroll|watch|log ?in|sign ?in|download|fill|type into|"
    r"subscribe|comment)\b",
    re.IGNORECASE,
)


def web_tools(launcher: BrowserLauncher | None = None) -> list[Tool]:
    launcher = launcher or WindowsBrowsers()

    async def open_website(args: OpenWebsiteArgs) -> str | ToolResult:
        user_text = CURRENT_TEXT.get()
        try:
            url, label = build_url(args.site, args.search, user_text)
        except WebError as exc:
            return f"error: {exc}"
        try:
            used = await asyncio.to_thread(launcher.launch, args.browser, url)
        except OSError:
            return "error: no browser could be started"
        what = f"a search for {args.search!r} on {label}" if args.search else label
        text = f"Opened {what} in {used}."
        if BEYOND_PAGE.search(user_text):
            # The model must tell the user what stops here (it cannot click or play on the page).
            return text + " (I can open pages and searches, but not click or play on them.)"
        return ToolResult(text, final=True)

    return [
        Tool(
            name="open_website",
            description="Open a website, or a search on a site like YouTube or Google, in the "
            "user's browser. Use it to go to a site or search on one.",
            args_model=OpenWebsiteArgs,
            risk=Risk.R1,  # starts a browser tab as the user; closing it undoes it
            idempotent=False,
            timeout_s=20,
            sandbox="full-access",  # the browser runs as the user, outside any sandbox
            network=["via the user's browser"],
            handler=open_website,
        )
    ]
