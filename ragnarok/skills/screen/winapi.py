"""Window facts and controls the screen skill needs, through ctypes (no extra dependencies).

Everything the loop asks about the desktop goes through the `Windows` class, so tests can replace it
with a fake and never touch a real desktop.
"""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from dataclasses import dataclass

VK_ESCAPE = 0x1B
SW_MINIMIZE, SW_RESTORE = 6, 9
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TH32CS_SNAPPROCESS = 0x2
# Windows that host a console or run commands. Text typed into these is executed, not just entered.
TERMINAL_HOSTS = frozenset(
    {"windowsterminal.exe", "conhost.exe", "openconsole.exe", "cmd.exe", "powershell.exe",
     "pwsh.exe", "powershell_ise.exe", "wt.exe", "wsl.exe", "bash.exe"}
)  # fmt: skip


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    process: str  # lower-case executable name, e.g. "chrome.exe"
    pid: int


class Windows:
    """Real window operations. Methods are cheap and safe to call from a worker thread."""

    # ---- looking -----------------------------------------------------------------------------

    def foreground(self) -> WindowInfo | None:
        import win32gui
        import win32process

        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return None
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        return WindowInfo(hwnd, win32gui.GetWindowText(hwnd), _process_name(pid), pid)

    def title_of(self, hwnd: int) -> str:
        """The title of one window, wherever it is (a browser shows its active tab here)."""
        import win32gui

        return win32gui.GetWindowText(hwnd) if win32gui.IsWindow(hwnd) else ""

    def is_own_terminal(self, window: WindowInfo | None) -> bool:
        """Is this the window the user typed to Ragnarok in?"""
        if window is None:
            return False
        console = ctypes.windll.kernel32.GetConsoleWindow()
        if console and window.hwnd == console:
            return True
        if window.pid in _ancestor_pids():
            return True
        return window.process in TERMINAL_HOSTS

    def cursor(self) -> tuple[int, int]:
        point = wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
        return point.x, point.y

    def escape_down(self) -> bool:
        """True while the user is holding Esc (works whichever window has the focus)."""
        return bool(ctypes.windll.user32.GetAsyncKeyState(VK_ESCAPE) & 0x8000)

    # ---- moving windows out of the way ---------------------------------------------------------

    def minimize(self, hwnd: int) -> None:
        import win32gui

        win32gui.ShowWindow(hwnd, SW_MINIMIZE)

    def restore(self, hwnd: int) -> None:
        import win32gui

        win32gui.ShowWindow(hwnd, SW_RESTORE)
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            # Windows refuses focus changes from background programs; a tap on Alt lifts that rule
            ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)
            ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)
            try:
                win32gui.SetForegroundWindow(hwnd)
            except Exception:
                pass  # the window is restored even if it could not take the focus


def _process_name(pid: int) -> str:
    kernel = ctypes.windll.kernel32
    handle = kernel.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if kernel.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value).lower()
        return ""
    finally:
        kernel.CloseHandle(handle)


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]  # fmt: skip


def _ancestor_pids() -> set[int]:
    """This process and every process above it (the shell and terminal that started us)."""
    if sys.platform != "win32":
        return {os.getpid()}
    kernel = ctypes.windll.kernel32
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snap = kernel.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    parents: dict[int, int] = {}
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = kernel.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            parents[entry.th32ProcessID] = entry.th32ParentProcessID
            ok = kernel.Process32NextW(snap, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snap)
    chain, pid = set(), os.getpid()
    while pid and pid not in chain:
        chain.add(pid)
        pid = parents.get(pid, 0)
    return chain
