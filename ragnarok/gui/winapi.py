"""The few Windows facts the cat needs: where the taskbar is, how sharp the screen is, one copy only."""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes


def make_dpi_aware() -> None:
    """Use real pixels, so the cat stands on the taskbar and is not blurred by Windows scaling."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def dpi_scale() -> float:
    if sys.platform != "win32":
        return 1.0
    try:
        return max(1.0, ctypes.windll.user32.GetDpiForSystem() / 96)
    except Exception:
        return 1.0


def screen_size() -> tuple[int, int]:
    if sys.platform != "win32":
        return 1920, 1080
    user32 = ctypes.windll.user32
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def cursor_pos() -> tuple[int, int]:
    """Where the mouse pointer is, in screen pixels."""
    if sys.platform != "win32":
        return 0, 0
    point = wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y


def work_area() -> tuple[int, int, int, int]:
    """(left, top, right, bottom) of the screen without the taskbar. The cat stands on `bottom`."""
    width, height = screen_size()
    if sys.platform != "win32":
        return 0, 0, width, height
    rect = wintypes.RECT()
    if ctypes.windll.user32.SystemParametersInfoW(
        0x0030, 0, ctypes.byref(rect), 0
    ):  # SPI_GETWORKAREA
        return rect.left, rect.top, rect.right, rect.bottom
    return 0, 0, width, height


def only_one_copy(name: str = "Local\\RagnarokCat") -> bool:
    """True for the first copy of the program; False if the cat is already running."""
    if sys.platform != "win32":
        return True
    handle = ctypes.windll.kernel32.CreateMutexW(None, False, name)
    already = ctypes.windll.kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS
    only_one_copy.handle = handle  # type: ignore[attr-defined]  # kept alive for the whole run
    return not already


SUMMON_NAME = "Local\\RagnarokCatSummon"


def summon(name: str = SUMMON_NAME) -> None:
    """Tell the cat that is already running to open its chat (what a second launch does)."""
    if sys.platform != "win32":
        return
    kernel = ctypes.windll.kernel32
    event = kernel.CreateEventW(None, False, False, name)
    kernel.SetEvent(event)
    kernel.CloseHandle(event)


class Summons:
    """Listens for `summon()`: the running cat asks `pending()` every frame."""

    def __init__(self, name: str = SUMMON_NAME) -> None:
        self.handle = (
            ctypes.windll.kernel32.CreateEventW(None, False, False, name)
            if sys.platform == "win32"
            else None
        )

    def pending(self) -> bool:
        return bool(self.handle) and ctypes.windll.kernel32.WaitForSingleObject(self.handle, 0) == 0
