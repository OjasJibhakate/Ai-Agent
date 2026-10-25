"""Where Windows keeps the user's Desktop, Documents and Downloads.

They are often redirected into OneDrive, so "C:\\Users\\name\\Documents" can be an empty leftover.
Files the agent creates go where the user (and their browser) would look: the folder Windows names.
"""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path

_FOLDER_IDS = {
    "desktop": "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",
    "documents": "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}",
    "downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
}


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def known_folder(name: str) -> Path | None:
    """The folder Windows calls Desktop, Documents or Downloads for this user, if it exists."""
    if sys.platform != "win32" or name not in _FOLDER_IDS:
        return None
    guid = _GUID()
    if ctypes.windll.ole32.CLSIDFromString(ctypes.c_wchar_p(_FOLDER_IDS[name]), ctypes.byref(guid)):
        return None
    raw = ctypes.c_void_p()
    try:
        if ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(guid), 0, None, ctypes.byref(raw)
        ):
            return None
        path = Path(ctypes.wstring_at(raw.value)) if raw.value else None
    finally:
        ctypes.windll.ole32.CoTaskMemFree(raw)
    return path if path is not None and path.is_dir() else None


def _first_existing(name: str, home: Path) -> Path:
    for base in (home, home / "OneDrive"):
        if (base / name).is_dir():
            return (base / name).resolve()
    path = home / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def downloads_folder() -> Path:
    return known_folder("downloads") or _first_existing("Downloads", Path.home())


def documents_folder() -> Path:
    return known_folder("documents") or _first_existing("Documents", Path.home())
