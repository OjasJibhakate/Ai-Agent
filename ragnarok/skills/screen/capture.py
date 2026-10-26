"""Screen capture with no extra dependencies: Windows GDI (pywin32), numpy, and a tiny PNG writer.

Frames live in memory only. Nothing here writes an image to disk (AGENTS.md hard rule 2): a
screenshot can contain anything the user has open.

Coordinates: the vision model answers "where is X" on a 0-1000 scale across the image (measured:
48 of 48 points inside the right element at 1280x720, median miss 5 px of 1920). `Frame.to_screen`
turns that into physical screen pixels, including the offset of a captured region.
"""

from __future__ import annotations

import ctypes
import struct
import sys
import zlib
from dataclasses import dataclass
from typing import Protocol

import numpy as np

MAX_WIDTH = 1280  # about 1.3 s per look on the RTX 4050; 1920 wide costs 2.6 s for no accuracy gain
DETAIL_WIDTH = 1920  # for reading dense small print
SCALE = 1000
MIN_CHANGED_PIXELS = 48  # fewer changed pixels than this is a blinking cursor, not an effect
MARK_CROP_W, MARK_CROP_H, MARK_ZOOM = 320, 120, 3  # the "what is under this point" picture
PIXEL_STEP = 32 # a pixel counts as changed when a colour channel moved by more than this


@dataclass(frozen=True)
class Frame:
    rgb: np.ndarray  # (h, w, 3) uint8, downscaled for the model
    left: int  # screen position of the captured region's top-left corner, in physical pixels
    top: int
    width: int  # size of the captured region in physical pixels (before downscaling)
    height: int

    def png(self) -> bytes:
        return encode_png(self.rgb)

    def to_screen(self, x: int, y: int) -> tuple[int, int]:
        """Map a point on the model's 0-1000 scale to physical screen pixels."""
        fx = min(max(x, 0), SCALE) / SCALE
        fy = min(max(y, 0), SCALE) / SCALE
        return self.left + round(fx * (self.width - 1)), self.top + round(fy * (self.height - 1))

    def marked_crop(self, sx: int, sy: int) -> bytes:
        """A zoomed PNG of the area around screen point (sx, sy), with a red cross (four arms and an
        empty centre) on that point, so the model can read what is under it."""
        h, w, _ = self.rgb.shape
        x = round((sx - self.left) / max(self.width - 1, 1) * (w - 1))
        y = round((sy - self.top) / max(self.height - 1, 1) * (h - 1))
        cw, ch = min(MARK_CROP_W, w), min(MARK_CROP_H, h)
        left = int(min(max(x - cw // 2, 0), w - cw))
        top = int(min(max(y - ch // 2, 0), h - ch))
        part = self.rgb[top : top + ch, left : left + cw]
        big = np.ascontiguousarray(np.repeat(np.repeat(part, MARK_ZOOM, axis=0), MARK_ZOOM, axis=1))
        cx = (min(max(x, 0), w - 1) - left) * MARK_ZOOM
        cy = (min(max(y, 0), h - 1) - top) * MARK_ZOOM
        red = (255, 0, 0)
        inner, outer = 6, 30
        big[max(cy - 1, 0) : cy + 2, max(cx - outer, 0) : max(cx - inner, 0)] = red
        big[max(cy - 1, 0) : cy + 2, cx + inner : cx + outer] = red
        big[max(cy - outer, 0) : max(cy - inner, 0), max(cx - 1, 0) : cx + 2] = red
        big[cy + inner : cy + outer, max(cx - 1, 0) : cx + 2] = red
        return encode_png(big)

    def looks_like(self, other: Frame, min_pixels: int = MIN_CHANGED_PIXELS) -> bool:
        """True if the two frames show the same thing: fewer than `min_pixels` pixels differ.

        Counts pixels rather than averaging a thumbnail: a few typed words change only a few
        hundred pixels, which a coarse average cannot see. A blinking cursor stays under the bar.
        Does real numpy work, so callers in async code run it with asyncio.to_thread.
        """
        if self.rgb.shape != other.rgb.shape:
            return False
        diff = np.abs(self.rgb.astype(np.int16) - other.rgb.astype(np.int16)).max(axis=2)
        return int(np.count_nonzero(diff > PIXEL_STEP)) < min_pixels


def downscale(rgb: np.ndarray, max_width: int) -> np.ndarray:
    """Area-average the image down to at most `max_width` pixels wide (text stays legible)."""
    h, w, _ = rgb.shape
    if w <= max_width:
        return rgb
    nw = max_width
    nh = max(1, round(h * nw / w))
    xe = np.linspace(0, w, nw + 1).astype(int)
    ye = np.linspace(0, h, nh + 1).astype(int)
    acc = np.add.reduceat(rgb.astype(np.uint32), xe[:-1], axis=1)
    acc = np.add.reduceat(acc, ye[:-1], axis=0)
    counts = np.outer(np.diff(ye), np.diff(xe))[:, :, None].astype(np.uint32)
    return (acc // counts).astype(np.uint8)


def encode_png(rgb: np.ndarray) -> bytes:
    """Encode an (h, w, 3) uint8 array as a PNG, in memory."""
    h, w, _ = rgb.shape
    rows = np.concatenate([np.zeros((h, 1), np.uint8), rgb.reshape(h, w * 3)], axis=1)  # filter 0

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    header = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)  # 8-bit RGB, no interlace
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows.tobytes(), 1))
        + chunk(b"IEND", b"")
    )


class ScreenSource(Protocol):
    def grab(self, max_width: int = MAX_WIDTH) -> Frame: ...


_dpi_ready = False


def _ensure_dpi_aware() -> None:
    """Make Windows report real pixels, so capture sizes and click positions agree."""
    global _dpi_ready
    if _dpi_ready or sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor
    except (OSError, AttributeError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (OSError, AttributeError):
            pass
    _dpi_ready = True


class WindowsScreen:
    """Captures the primary monitor (or a fixed region of it) with GDI."""

    def __init__(self, region: tuple[int, int, int, int] | None = None) -> None:
        self.region = region  # (left, top, width, height) in physical pixels

    def grab(self, max_width: int = MAX_WIDTH) -> Frame:
        if sys.platform != "win32":
            raise OSError("screen capture is only supported on Windows")
        _ensure_dpi_aware()
        import win32con
        import win32gui
        import win32ui

        user32 = ctypes.windll.user32
        left, top, w, h = self.region or (
            0,
            0,
            user32.GetSystemMetrics(0),
            user32.GetSystemMetrics(1),
        )
        desktop = win32gui.GetDesktopWindow()
        hdc = win32gui.GetWindowDC(desktop)
        src = win32ui.CreateDCFromHandle(hdc)
        mem = src.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        try:
            bitmap.CreateCompatibleBitmap(src, w, h)
            mem.SelectObject(bitmap)
            # CAPTUREBLT also captures layered (semi-transparent) windows
            mem.BitBlt((0, 0), (w, h), src, (left, top), win32con.SRCCOPY | 0x40000000)
            raw = bitmap.GetBitmapBits(True)
        finally:
            win32gui.DeleteObject(bitmap.GetHandle())
            mem.DeleteDC()
            src.DeleteDC()
            win32gui.ReleaseDC(desktop, hdc)
        bgra = np.frombuffer(raw, np.uint8).reshape(h, w, 4)
        rgb = np.ascontiguousarray(bgra[:, :, 2::-1])  # BGRA -> RGB
        return Frame(downscale(rgb, max_width), left, top, w, h)
