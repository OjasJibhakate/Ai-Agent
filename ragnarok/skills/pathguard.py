"""Which paths the file and command tools may touch, in every permission mode.

Modes decide whether the agent asks before acting; this decides what it may act on at all. It is
the one place that knows the places no mode may touch:

- Ragnarok's own data (the vault and its key files), whatever RAGNAROK_HOME says;
- the system: Windows, Program Files, ProgramData, the Recycle Bin, drive roots, and anything else
  on the system drive outside the user's profile;
- credentials and other programs' private data: ~/.ssh, ~/.aws, ~/.claude, ~/.codex, AppData
  (browser profiles, saved logins), and files named like keys, passwords or tokens (for reading);
- the structure of the profile itself: the profile folder, Desktop, Documents, Downloads and
  OneDrive can be used but not moved or deleted as a whole.

Paths are resolved first (`..`, symlinks and junctions included), so a link cannot lead out.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from ragnarok.skills.folders import known_folder

# Never listed, searched or read: keys, password stores, credentials, environment files.
DENY = re.compile(
    r"(\.kdbx?$|\.pem$|\.key$|\.pfx$|\.p12$|\.ppk$|^id_(rsa|dsa|ecdsa|ed25519)|^\.env($|\.)|"
    r"password|passwd|credential|secret|\.ssh$|\.aws$|\.gnupg$|private[_ -]?key|token)",
    re.IGNORECASE,
)
SYSTEM_FOLDERS = {
    "windows", "program files", "program files (x86)", "programdata", "$recycle.bin",
    "system volume information", "recovery", "boot", "perflogs",
}  # fmt: skip
# First folder under the profile that holds other programs' secrets or sessions.
PROFILE_PRIVATE = {
    ".ssh", ".aws", ".azure", ".gnupg", ".kube", ".docker", ".config", ".claude", ".codex",
    ".gemini", "appdata",
}  # fmt: skip
MAX_PATH_TEXT = 400
# User names a model puts in a path when it does not know the real one.
PLACEHOLDERS = {
    "name", "user", "username", "your name", "yourname", "your_name", "your-name", "yourusername",
    "your_username", "your-username", "<username>", "[username]", "{username}", "%username%",
    "you", "me", "owner", "current user", "currentuser", "example", "john", "jane", "john doe",
}  # fmt: skip
_PLACEHOLDER_HOME = re.compile(
    r"^(?:[a-zA-Z]:)?[\\/]+(?:users|home)[\\/]+([^\\/]+)(?:[\\/]+(.*))?$", re.IGNORECASE
)
_PROFILE_VARIABLE = re.compile(r"%USERPROFILE%|\$env:USERPROFILE|\$HOME", re.IGNORECASE)


class PathRefused(ValueError):
    """The path is off limits (the message is shown to the model and the user)."""


def _key(path: Path) -> str:
    return os.path.normcase(str(path))


def _inside(path: Path, folder: Path) -> bool:
    return _key(path) == _key(folder) or _key(folder) + os.sep in _key(path) + os.sep


class PathGuard:
    def __init__(self, home: Path | None = None, data_dirs: list[Path] | None = None) -> None:
        self.home = Path(os.path.realpath(home or Path.home()))
        if data_dirs is None:
            data_dirs = []
            if base := os.environ.get("LOCALAPPDATA"):
                data_dirs.append(Path(base) / "Ragnarok")
            if custom := os.environ.get("RAGNAROK_HOME"):
                data_dirs.append(Path(custom))
        self.data_dirs = [Path(os.path.realpath(d)) for d in data_dirs]
        self.drive = os.path.splitdrive(self.home)[0].lower()
        # "Documents/x" means the folder Windows calls Documents (often inside OneDrive), not a
        # same-named leftover under the profile folder. Only for the real profile, not a test home.
        self.known: dict[str, Path] = {}
        if home is None:
            for name in ("desktop", "documents", "downloads"):
                if (found := known_folder(name)) is not None:
                    self.known[name] = Path(os.path.realpath(found))
        # folders that may be used but not moved or deleted as a whole
        named = [self.home, self.home / "OneDrive"]
        for name in ("desktop", "documents", "downloads"):
            found = known_folder(name) if home is None else None
            named += [self.home / name.capitalize(), self.home / "OneDrive" / name.capitalize()]
            if found is not None:
                named.append(found)
        for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
            if home is None and (v := os.environ.get(var)):
                named.append(Path(v))
        self.structural = {_key(Path(os.path.realpath(p))) for p in named}

    # ---- turning text into a path ----------------------------------------------------------

    def resolve(self, text: str) -> Path:
        """An absolute, fully resolved path.

        Models often write a path the way they imagine it (C:\\Users\\name\\Downloads\\x.pdf or
        /home/user/x): a stand-in user name is taken to mean this user. Relative text starts in
        the profile folder, and Desktop, Documents and Downloads mean the folders Windows names
        (the real ones, which may live inside OneDrive).
        """
        raw = text.strip().strip('"').strip("'")
        if not raw or len(raw) > MAX_PATH_TEXT or "\x00" in raw:
            raise PathRefused("that is not a usable path")
        raw = _PROFILE_VARIABLE.sub(lambda _m: str(self.home), raw)
        if raw.startswith("~"):
            raw = str(self.home) + raw[1:]
        placeholder = _PLACEHOLDER_HOME.match(raw)
        if placeholder and placeholder.group(1).lower() in PLACEHOLDERS:
            raw = placeholder.group(2) or "."  # the rest, relative to the real profile folder
        path = Path(raw)
        if not path.is_absolute():
            parts = [p for p in path.parts if p not in (".", "")]
            first = parts[0].lower() if parts else ""
            if first in self.known:
                path = self.known[first].joinpath(*parts[1:])
            else:
                path = self.home.joinpath(*parts)
        return self._prefer_known(Path(os.path.realpath(path)))

    def _prefer_known(self, real: Path) -> Path:
        """A path under the old Desktop/Documents/Downloads folder means the one Windows shows,
        when that folder has moved (OneDrive) and the old one no longer exists."""
        if not self.known or not _inside(real, self.home):
            return real
        rel = real.relative_to(self.home).parts
        if rel and rel[0].lower() in self.known and not (self.home / rel[0]).exists():
            return self.known[rel[0].lower()].joinpath(*rel[1:])
        return real

    # ---- what no mode may touch --------------------------------------------------------------

    def protected_reason(self, real: Path) -> str | None:
        """Why this (already resolved) path is off limits in every mode, or None."""
        for data in self.data_dirs:
            if _inside(real, data):
                return "that is Ragnarok's own data (the vault), which no mode may touch"
        parts = [p.lower() for p in real.parts]
        if len(real.parts) <= 1 or real == Path(real.anchor):
            return "that is a drive root"
        if _inside(real, self.home):
            first = real.relative_to(self.home).parts[:1]
            if first and first[0].lower() in PROFILE_PRIVATE:
                return f"{first[0]} holds other programs' saved logins and sessions"
            return None
        drive = os.path.splitdrive(real)[0].lower()
        if drive == self.drive:
            return "that is outside your user folder on the system drive"
        if len(parts) > 1 and parts[1] in SYSTEM_FOLDERS:
            return "that is a system folder"
        return None

    def writable(self, text: str) -> Path:
        """A path the tools may create, change, copy to or move to."""
        real = self.resolve(text)
        if reason := self.protected_reason(real):
            raise PathRefused(reason)
        return real

    def readable(self, text: str, roots: list[Path], wide: bool) -> Path:
        """A path the tools may read: inside `roots`, or anywhere allowed when `wide`."""
        real = self.resolve(text)
        if reason := self.protected_reason(real):
            raise PathRefused(reason)
        if not wide and not any(_inside(real, r) for r in roots):
            raise PathRefused(
                "that path is outside the folders I may look in (Desktop, Documents, Downloads); "
                "/mode edits or /mode full opens the rest of your user folder"
            )
        if any(DENY.search(part) for part in real.parts[-6:] if part not in ("\\", "/")):
            raise PathRefused("that file may hold secrets, so I don't open it")
        return real

    def whole_folder_ok(self, real: Path, what: str) -> None:
        """Moving or deleting the profile folder, Desktop, Documents, ... as a whole is refused."""
        if _key(real) in self.structural:
            raise PathRefused(f"I won't {what} {real.name or real} itself, only things inside it")
