"""Test helper: pick the cat up, carry it, drop it, then park the pointer on the taskbar."""
import ctypes
import subprocess
import sys
import time

import numpy as np

sys.path.insert(0, ".")
from ragnarok.skills.screen.capture import WindowsScreen

user32 = ctypes.windll.user32


def find_cat():
    frame = WindowsScreen((0, 900, 1920, 1040)).grab(max_width=4000)
    rgb = frame.rgb.astype(int)
    fur = (abs(rgb[..., 0] - 0xF2) < 6) & (abs(rgb[..., 1] - 0xA6) < 6) & (abs(rgb[..., 2] - 0x5A) < 6)
    ys, xs = np.nonzero(fur)
    return (int(xs.mean()), int(ys.mean()) + 900) if len(xs) > 30 else None


def shot(name):
    subprocess.run([sys.executable, "scripts/shot.py", f"C:/Users/parth/AppData/Local/Temp/{name}.png", "0", "800", "1920", "1080"], check=True, env={**__import__("os").environ, "PYTHONPATH": "."})


def move(x, y):
    user32.SetCursorPos(int(x), int(y))


cat = find_cat()
print("cat at", cat)
cx, cy = cat
move(cx, cy); time.sleep(0.3)
user32.mouse_event(2, 0, 0, 0, 0)                      # press on the cat
target_x = 300 if cx > 960 else 1500
for i in range(1, 31):                                  # carry it up and across
    move(cx + (target_x - cx) * i / 30, cy - 4 * i)
    time.sleep(0.03)
time.sleep(0.25)
shot("demo_carried")
user32.mouse_event(4, 0, 0, 0, 0)                      # let go in mid-air
time.sleep(0.12)
shot("demo_falling")
time.sleep(1.8)
shot("demo_landed")
print("dropped at x ~", target_x)
