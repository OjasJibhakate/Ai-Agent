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

from ragnarok.policy.modes import Mode
from ragnarok.policy.permissions import Risk
from ragnarok.runtime.tools import CURRENT_TEXT, Tool, ToolArgs, ToolResult
from ragnarok.skills.extract import DOCUMENT_EXT, ExtractError, text_of
from ragnarok.skills.folders import known_folder
from ragnarok.skills.pathguard import DENY, PROFILE_PRIVATE, PathGuard

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
    if real_profile:  # the folders Windows itself names (what a download or a new document uses)
        for name in ROOT_NAMES:
            p = known_folder(name.lower())
            if p is not None and p.resolve() not in seen:
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


class AmbiguousName(ValueError):
    """A name matches more than one file or folder."""


class FileAccess:
    """What may be touched, and how it is shown."""

    def __init__(
        self,
        roots: list[Path],
        home: Path | None = None,
        mode: Mode | None = None,
        data_dirs: list[Path] | None = None,
    ) -> None:
        self.base_roots = [r.resolve() for r in roots]
        self.home = (home or Path.home()).resolve()
        self.mode = mode
        # a test home has no vault; the real one is found from the environment
        self.guard = PathGuard(self.home, data_dirs if home is None or data_dirs else [])

    @property
    def roots(self) -> list[Path]:
        """Desktop, Documents and Downloads; the whole profile folder in the wider modes."""
        return [self.home] if self.mode is not None and self.mode.wide else self.base_roots

    def display(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(self.home))
        except ValueError:
            return str(path)

    def allowed(self, path: Path) -> bool:
        real = path.resolve()
        if not any(real == r or r in real.parents for r in self.roots):
            return False
        return self.guard.protected_reason(real) is None  # the vault, AppData, system folders ...

    @staticmethod
    def denied(path: Path) -> bool:
        return any(DENY.search(part) for part in path.parts[-6:] if part not in ("\\", "/"))

    def resolve(self, text: str) -> Path:
        """Turn what the model typed into a safe absolute path, or raise ValueError."""
        path = self.guard.resolve(text)  # stand-in user names, Desktop/..., ~ and %USERPROFILE%
        if not self.allowed(path):
            raise ValueError(
                "that path is outside the folders I may look in (Desktop, Documents, Downloads)"
            )
        if self.denied(path):
            raise ValueError("that file may hold secrets, so I don't open it")
        return path.resolve()

    def locate(self, text: str) -> Path:
        """Like `resolve`, but a name the user spoke ("notes.txt", "Reports/a.pdf") is looked for
        in Desktop, Documents, Downloads and the profile folder when it is not where it was written.

        One match is used; several are an error that lists them, so nothing is guessed.
        """
        rel = text.strip().strip('"').strip("'")
        absolute_like = bool(re.match(r"^(?:[a-zA-Z]:|[\\/]|~|%|\$)", rel))
        error: ValueError | None = None
        try:
            path = self.resolve(text)
            if path.exists() or absolute_like:
                return path
        except ValueError as exc:
            if absolute_like:
                raise
            path, error = None, exc  # a bare name may still be found in the usual folders
        seen: set[Path] = set()
        for root in [*self.base_roots, self.home]:
            try:
                found = (root / rel).resolve()
            except (OSError, ValueError):
                continue
            if found.exists() and self.allowed(found) and not self.denied(found):
                seen.add(found)
        if len(seen) > 1:
            where = "; ".join(sorted(self.display(p) for p in seen))
            raise AmbiguousName(f"{rel!r} is in more than one place ({where}); say which one")
        if seen:
            return next(iter(seen))
        if error is not None:
            raise error
        return path  # type: ignore[return-value]

    def roots_for(self, folder: str) -> list[Path]:
        name = folder.strip().lower()
        if name in ("", "all", "everywhere", "anywhere"):
            return self.roots
        named = [r for r in self.base_roots if r.name.lower() == name]
        if named:
            return named
        if not re.search(r"[\/:]", name):  # a plain name like "deep learning", not a path
            found = self.find_folder(folder)
            if found is None:
                raise ValueError(
                    f"I couldn't find a folder called {folder.strip()!r} in Desktop, "
                    "Documents or Downloads"
                )
            return [found]
        path = self.resolve(folder)
        if not path.is_dir():
            raise ValueError("that is not a folder")
        return [path]

    def find_folder(self, text: str) -> Path | None:
        """The folder whose name matches these words ("deep learning" finds Deep_learning)."""
        tokens = query_tokens(text)
        if not tokens:
            return None
        deadline = time.monotonic() + TIME_BUDGET_S
        best: tuple[int, float, Path] | None = None
        for entry, is_dir in self.walk(self.roots, deadline):
            if not is_dir or not all(_matches(t, _norm(entry.name)) for t in tokens):
                continue
            path = Path(entry.path)
            try:
                key = (_depth(path, self.roots), -entry.stat(follow_symlinks=False).st_mtime, path)
            except OSError:
                continue
            if best is None or key[:2] < best[:2]:
                best = key
        return best[2] if best else None

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
                    if depth == 0 and root == self.home and name.lower() in PROFILE_PRIVATE:
                        continue  # AppData and friends: never searched, even in the wide modes
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
        if folder.strip().lower() in ("", "all", "everywhere", "anywhere"):
            raise ValueError("say which folder: desktop, documents, downloads, or a folder's name")
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
        default="desktop",
        description="desktop, documents, downloads, or the name of a folder, like 'deep learning'.",
    )


class ReadFileArgs(ToolArgs):
    path: str = Field(
        min_length=1, max_length=400, description="A file path, as shown by find_files."
    )
    offset: int = Field(default=0, ge=0, description="Start reading at this character.")


def file_tools(
    roots: list[Path] | None = None, home: Path | None = None, mode: Mode | None = None
) -> list[Tool]:
    access = FileAccess(roots if roots is not None else default_roots(home), home, mode)
    root_list = [str(r) for r in access.base_roots]

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
            path = access.locate(args.path)
        except ValueError as exc:
            return f"error: {exc}"
        if not path.is_file():
            return "error: no such file"
        suffix = path.suffix.lower()
        if suffix in DOCUMENT_EXT:
            try:
                text = text_of(path)
            except ExtractError as exc:
                return f"error: {exc}"
        elif suffix not in TEXT_EXT:
            return (
                f"error: I can read text, PDF, Word and PowerPoint files ({suffix or 'this type'} "
                "is not supported yet), but I can tell you where it is."
            )
        else:
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
            description="List what is in the Desktop, Documents or Downloads folder, or in a "
            "folder inside them by name. Use it when the user asks what is in a folder.",
            args_model=ListFolderArgs,
            timeout_s=10,
            handler=list_folder,
            **common,
        ),
        Tool(
            name="read_file",
            description="Read a text file, PDF, Word or PowerPoint document (notes, code, csv, "
            "reports). Use it when the user asks what a file says.",
            args_model=ReadFileArgs,
            timeout_s=10,
            handler=read_file,
            **common,
        ),
    ]
