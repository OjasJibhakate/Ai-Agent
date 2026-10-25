"""Skill: find, list and read the user's own files (read-only).

Scope is deliberately narrow: the Desktop, Documents and Downloads folders (and their OneDrive
versions). Nothing is ever written, moved or deleted. Keys, password files and other secrets are
hidden from results and refused even if asked for by exact path. Anything outside the allowed
folders, including paths that use `..` or a symlink to get out, is refused.

File text is returned wrapped in <file_content> tags, which the system prompt says is data and
never instructions.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pydantic import Field

from ragnarok.policy.permissions import Risk
from ragnarok.runtime.tools import CURRENT_TEXT, Tool, ToolArgs, ToolResult

ROOT_NAMES = ("Desktop", "Documents", "Downloads")
MAX_DEPTH = 6
MAX_SCAN = 60_000  # directory entries looked at per search
MAX_RESULTS = 15
NAME_FIRST_MIN = 5  # fewer name matches than this: also look inside text files
TIME_BUDGET_S = 6.0
MAX_CONTENT_FILES = 400
MAX_CONTENT_BYTES = 1_000_000
READ_CHARS = 3_500  # per read_file call; the model can page with `offset`
LIST_LIMIT = 60

SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env", ".idea", ".vscode",
    "site-packages", "$recycle.bin", "system volume information", "appdata", ".next", "dist",
    "build", ".cache", ".gradle", ".m2", "target", ".pytest_cache",
}  # fmt: skip
TEXT_EXT = {
    ".txt", ".md", ".py", ".java", ".c", ".cpp", ".h", ".hpp", ".cs", ".js", ".jsx", ".ts", ".tsx",
    ".json", ".csv", ".html", ".css", ".xml", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".log",
    ".sql", ".sh", ".bat", ".ps1", ".go", ".rs", ".kt", ".rb", ".php", ".tex", ".rst",
}  # fmt: skip
# Never listed, searched or read: keys, password stores, credentials, environment files.
DENY = re.compile(
    r"(\.kdbx?$|\.pem$|\.key$|\.pfx$|\.p12$|\.ppk$|^id_(rsa|dsa|ecdsa|ed25519)|^\.env($|\.)|"
    r"password|passwd|credential|secret|\.ssh$|\.aws$|\.gnupg$|private[_ -]?key|token)",
    re.IGNORECASE,
)
STOPWORDS = {
    "a", "an", "the", "any", "my", "me", "i", "is", "are", "there", "do", "have", "of", "to", "for",
    "with", "about", "on", "in", "file", "files", "folder", "folders", "related", "relating",
    "relevant", "named", "called", "containing", "find", "search", "show", "desktop", "documents",
    "downloads", "computer", "pc", "laptop", "some", "anything", "something", "stuff", "and",
}  # fmt: skip


def default_roots(home: Path | None = None) -> list[Path]:
    """Desktop, Documents and Downloads, including OneDrive's redirected copies."""
    real_profile = home is None  # environment variables describe the real user, not a test home
    home = home or Path.home()
    bases = [home, home / "OneDrive"]
    if real_profile:
        bases += [
            Path(v)
            for k in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial")
            if (v := os.environ.get(k))
        ]
    seen: set[Path] = set()
    roots: list[Path] = []
    for base in bases:
        for name in ROOT_NAMES:
            p = base / name
            if p.is_dir() and p.resolve() not in seen:
                seen.add(p.resolve())
                roots.append(p.resolve())
    return roots


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def query_tokens(query: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", query.lower())
    return [w for w in words if w not in STOPWORDS and len(w) >= 2]


def _variants(token: str) -> set[str]:
    out = {token}
    if token.endswith("ies") and len(token) > 4:
        out.add(token[:-3] + "y")
    if token.endswith("s") and len(token) > 3:
        out.add(token[:-1])
    return out


def _matches(token: str, haystack: str) -> bool:
    return any(v in haystack for v in _variants(token))


@dataclass
class Hit:
    path: Path
    is_dir: bool
    size: int
    mtime: float
    depth: int  # how many folders down from the searched root; shallower reads as more relevant
    snippet: str = ""


class FileAccess:
    """What may be touched, and how it is shown."""

    def __init__(self, roots: list[Path], home: Path | None = None) -> None:
        self.roots = [r.resolve() for r in roots]
        self.home = (home or Path.home()).resolve()

    def display(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(self.home))
        except ValueError:
            return str(path)

    def allowed(self, path: Path) -> bool:
        real = path.resolve()
        return any(real == r or r in real.parents for r in self.roots)

    @staticmethod
    def denied(path: Path) -> bool:
        return any(DENY.search(part) for part in path.parts[-6:] if part not in ("\\", "/"))

    def resolve(self, text: str) -> Path:
        """Turn what the model typed into a safe absolute path, or raise ValueError."""
        raw = Path(text.strip().strip('"'))
        path = raw if raw.is_absolute() else self.home / raw
        if not self.allowed(path):
            raise ValueError(
                "that path is outside the folders I may look in (Desktop, Documents, Downloads)"
            )
        if self.denied(path):
            raise ValueError("that file may hold secrets, so I don't open it")
        return path.resolve()

    def roots_for(self, folder: str) -> list[Path]:
        name = folder.strip().lower()
        if name in ("", "all", "everywhere", "anywhere"):
            return self.roots
        named = [r for r in self.roots if r.name.lower() == name]
        if named:
            return named
        path = self.resolve(folder)
        if not path.is_dir():
            raise ValueError("that is not a folder")
        return [path]

    # ---- walking -----------------------------------------------------------------------------

    def walk(self, roots: list[Path], deadline: float):
        """Yield (entry, is_dir) breadth-limited; stops at the time budget or entry cap."""
        scanned = 0
        for root in roots:
            stack = [(root, 0)]
            while stack:
                folder, depth = stack.pop()
                try:
                    entries = list(os.scandir(folder))
                except OSError:
                    continue
                for entry in entries:
                    scanned += 1
                    if scanned > MAX_SCAN or time.monotonic() > deadline:
                        return
                    name = entry.name
                    if name.startswith(".") or DENY.search(name) or self._hidden(entry):
                        continue
                    is_dir = entry.is_dir(follow_symlinks=False)
                    yield entry, is_dir
                    if is_dir and depth < MAX_DEPTH and name.lower() not in SKIP_DIRS:
                        stack.append((Path(entry.path), depth + 1))

    @staticmethod
    def _hidden(entry: os.DirEntry) -> bool:
        if sys.platform != "win32":
            return False
        try:
            return bool(entry.stat(follow_symlinks=False).st_file_attributes & 0x2)  # type: ignore[attr-defined]
        except OSError:
            return False

    # ---- operations --------------------------------------------------------------------------

    def find(
        self, query: str, folder: str, contents: bool | None = None
    ) -> tuple[list[Hit], int, bool, list[str]]:
        """File and folder names first; also text inside files when few names match.

        `contents=None` decides automatically (fewer than NAME_FIRST_MIN name matches),
        True always adds text matches, False searches names only.
        """
        tokens = query_tokens(query)
        if not tokens:
            raise ValueError("give me a word to look for, like 'resume' or 'algebra'")
        roots = self.roots_for(folder)
        deadline = time.monotonic() + TIME_BUDGET_S
        by_name = self._by_name(tokens, roots, deadline)
        inside: list[Hit] = []
        if contents or (contents is None and len(by_name) < NAME_FIRST_MIN):
            seen = {h.path for h in by_name}
            inside = [h for h in self._by_content(tokens, roots, deadline) if h.path not in seen]
        timed_out = time.monotonic() > deadline
        rank = lambda h: (h.depth, h.is_dir, -h.mtime)  # noqa: E731  shallow, files first, recent
        merged = sorted(by_name, key=rank) + sorted(inside, key=rank)
        return merged[:MAX_RESULTS], len(merged), timed_out, tokens

    def _by_name(self, tokens: list[str], roots: list[Path], deadline: float) -> list[Hit]:
        hits = []
        for entry, is_dir in self.walk(roots, deadline):
            if not all(_matches(t, _norm(entry.name)) for t in tokens):
                continue
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            path = Path(entry.path)
            hits.append(Hit(path, is_dir, st.st_size, st.st_mtime, _depth(path, roots)))
        return hits

    def _by_content(self, tokens: list[str], roots: list[Path], deadline: float) -> list[Hit]:
        hits, read = [], 0
        for entry, is_dir in self.walk(roots, deadline):
            path = Path(entry.path)
            if is_dir or path.suffix.lower() not in TEXT_EXT:
                continue
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if st.st_size > MAX_CONTENT_BYTES:
                continue
            read += 1
            if read > MAX_CONTENT_FILES:
                break
            snippet = _content_match(path, tokens)
            if snippet is not None:
                hits.append(Hit(path, False, st.st_size, st.st_mtime, _depth(path, roots), snippet))
        return hits

    def listing(self, folder: str) -> tuple[Path, list[os.DirEntry], int]:
        roots = self.roots_for(folder)
        target = roots[0]
        entries = []
        for e in os.scandir(target):
            if e.name.startswith(".") or DENY.search(e.name) or self._hidden(e):
                continue
            entries.append(e)
        entries.sort(key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower()))
        return target, entries[:LIST_LIMIT], len(entries)


def _depth(path: Path, roots: list[Path]) -> int:
    for root in roots:
        if root in path.parents:
            return len(path.relative_to(root).parts)
    return 99


def _content_match(path: Path, tokens: list[str]) -> str | None:
    try:
        raw = path.read_bytes()[:MAX_CONTENT_BYTES]
    except OSError:
        return None
    if b"\x00" in raw[:2048]:
        return None
    text = raw.decode("utf-8", errors="replace")
    lower = text.lower()
    if not all(any(v in lower for v in _variants(t)) for t in tokens):
        return None
    first = next((v for v in _variants(tokens[0]) if v in lower), tokens[0])
    at = lower.index(first)
    start = text.rfind("\n", 0, at) + 1
    end = text.find("\n", at)
    return text[start : end if end != -1 else len(text)].strip()[:140]


# Words that mean the user wants more than a list: the model must see the results and go on.
WANTS_MORE = re.compile(
    r"\b(read|open|summari[sz]e|first line|last line|what does|what do|says?|contents?|inside|"
    r"explain|compare|which one|newest|latest|largest|biggest|smallest|oldest)\b",
    re.IGNORECASE,
)


def lookup_is_the_answer(user_text: str) -> bool:
    """A plain "is there a file about X" / "what is in Downloads" is answered by the list itself."""
    return not WANTS_MORE.search(user_text)


def _size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


def _line(access: FileAccess, hit: Hit) -> str:
    when = datetime.fromtimestamp(hit.mtime).strftime("%Y-%m-%d")
    where = access.display(hit.path)
    if hit.is_dir:
        return f"{where}  [folder, {when}]"
    base = f"{where}  ({_size(hit.size)}, {when})"
    return base + (f"\n    > {hit.snippet}" if hit.snippet else "")


class FindFilesArgs(ToolArgs):
    query: str = Field(
        min_length=2, max_length=120, description="Words to look for, like 'data structures'."
    )
    folder: str = Field(default="all", description="desktop, documents, downloads, or all.")


class ListFolderArgs(ToolArgs):
    folder: str = Field(
        default="desktop", description="desktop, documents, downloads, or a folder path."
    )


class ReadFileArgs(ToolArgs):
    path: str = Field(
        min_length=1, max_length=400, description="A file path, as shown by find_files."
    )
    offset: int = Field(default=0, ge=0, description="Start reading at this character.")


def file_tools(roots: list[Path] | None = None, home: Path | None = None) -> list[Tool]:
    access = FileAccess(roots if roots is not None else default_roots(home), home)
    root_list = [str(r) for r in access.roots]

    def _find(args: FindFilesArgs) -> str:
        try:
            hits, total, timed_out, tokens = access.find(args.query, args.folder)
        except ValueError as exc:
            return f"error: {exc}"
        words = " ".join(tokens)
        early = " (The search stopped early in a very large folder.)" if timed_out else ""
        if not hits:
            return (
                f"Nothing found for {words!r} in file names or in text files."
                f" Try another word (a short form or a synonym).{early}"
            )
        where = "in names and inside text files" if any(h.snippet for h in hits) else "in names"
        head = f"Found {total} match{'es' if total != 1 else ''} for {words!r} {where}"
        head += f" (showing {len(hits)})" if total > len(hits) else ""
        return head + "." + early + "\n" + "\n".join(_line(access, h) for h in hits)

    def _answer(text: str, found: bool) -> str | ToolResult:
        """Show a successful lookup as it is (fast, exact) unless the user asked for more."""
        if found and lookup_is_the_answer(CURRENT_TEXT.get()):
            return ToolResult(text, final=True)
        return text

    def _list(args: ListFolderArgs) -> str:
        try:
            target, entries, total = access.listing(args.folder)
        except ValueError as exc:
            return f"error: {exc}"
        except OSError:
            return "error: could not open that folder"
        if not entries:
            return f"{access.display(target)} is empty."
        lines = []
        for e in entries:
            if e.is_dir(follow_symlinks=False):
                lines.append(f"{e.name}  [folder]")
            else:
                try:
                    lines.append(f"{e.name}  ({_size(e.stat().st_size)})")
                except OSError:
                    lines.append(e.name)
        more = f"\n... and {total - len(entries)} more" if total > len(entries) else ""
        return f"{access.display(target)} has {total} items:\n" + "\n".join(lines) + more

    def _read(args: ReadFileArgs) -> str:
        try:
            path = access.resolve(args.path)
        except ValueError as exc:
            return f"error: {exc}"
        if not path.is_file():
            return "error: no such file"
        if path.suffix.lower() not in TEXT_EXT:
            return (
                f"error: I can only read plain-text files for now ({path.suffix or 'this type'} "
                "is not supported yet), but I can tell you where it is."
            )
        try:
            if path.stat().st_size > 5_000_000:
                return "error: that file is too large to read"
            raw = path.read_bytes()
        except OSError:
            return "error: could not read that file"
        if b"\x00" in raw[:2048]:
            return "error: that looks like a binary file"
        text = raw.decode("utf-8", errors="replace")
        chunk = text[args.offset : args.offset + READ_CHARS]
        if not chunk:
            return f"error: the file has only {len(text)} characters"
        end = args.offset + len(chunk)
        more = f" Call again with offset={end} for the next part." if end < len(text) else ""
        return (
            f"{access.display(path)} (characters {args.offset}-{end} of {len(text)}).{more}\n"
            f"<file_content>\n{chunk}\n</file_content>"
        )

    async def find_files(args: FindFilesArgs) -> str | ToolResult:
        text = await asyncio.to_thread(_find, args)
        return _answer(text, found=text.startswith("Found"))

    async def list_folder(args: ListFolderArgs) -> str | ToolResult:
        text = await asyncio.to_thread(_list, args)
        return _answer(text, found=" items:" in text.splitlines()[0])

    async def read_file(args: ReadFileArgs) -> str | ToolResult:
        return await asyncio.to_thread(_read, args)

    common = {
        "risk": Risk.R0,
        "idempotent": True,
        "sandbox": "read-only",
        "fs": {"read": root_list},
    }
    return [
        Tool(
            name="find_files",
            description="Search the user's Desktop, Documents and Downloads for files and folders "
            "by name, and inside text files if needed. Use it to check if a file exists.",
            args_model=FindFilesArgs,
            timeout_s=20,
            handler=find_files,
            **common,
        ),
        Tool(
            name="list_folder",
            description="List what is in the Desktop, Documents or Downloads folder. "
            "Use it when the user asks what is in one of them.",
            args_model=ListFolderArgs,
            timeout_s=10,
            handler=list_folder,
            **common,
        ),
        Tool(
            name="read_file",
            description="Read a plain-text file (notes, code, csv, markdown) from those folders. "
            "Use it when the user asks what a file says.",
            args_model=ReadFileArgs,
            timeout_s=10,
            handler=read_file,
            **common,
        ),
    ]
