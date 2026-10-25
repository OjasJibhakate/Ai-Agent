"""Test helper: find the cat by its fur colour on the taskbar strip and click it."""

import ctypes
import sys
import time

import numpy as np

from ragnarok.skills.screen.capture import WindowsScreen

left, top, right, bottom = 0, 900, 1920, 1035
frame = WindowsScreen((left, top, right, bottom)).grab(max_width=4000)
rgb = frame.rgb.astype(int)
fur = (
    (abs(rgb[..., 0] - 0xF2) < 14) & (abs(rgb[..., 1] - 0xA6) < 14) & (abs(rgb[..., 2] - 0x5A) < 14)
)
ys, xs = np.nonzero(fur)
if len(xs) < 30:
    print("cat not found")
    sys.exit(1)
cx, cy = int(xs.mean()) + left, int(ys.mean()) + top
print("cat at", cx, cy)
user32 = ctypes.windll.user32
user32.SetCursorPos(cx, cy)
time.sleep(0.15)
user32.mouse_event(2, 0, 0, 0, 0)
time.sleep(0.06)
user32.mouse_event(4, 0, 0, 0, 0)
