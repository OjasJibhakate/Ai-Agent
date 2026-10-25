"""Test helper: is the cat ignoring a far pointer, and following a near one?"""
import ctypes
import sys
import time

sys.path.insert(0, ".")
from scripts.gui_demo_lib import find_cat

user32 = ctypes.windll.user32
cat = find_cat()
print("cat at", cat)
cx = cat[0]
far_x = 150 if cx > 1000 else 1750
user32.SetCursorPos(far_x, 1048)
x0 = find_cat()[0]
time.sleep(6)
x1 = find_cat()
print("far pointer: cat moved", None if x1 is None else x1[0] - x0, "px in 6 s (it wanders on its own)")
cat = find_cat()
near = cat[0] + (230 if cat[0] < 1500 else -230)
user32.SetCursorPos(near, 1048)
time.sleep(4)
x2 = find_cat()
print("near pointer at", near, "-> cat now at", x2)
user32.SetCursorPos(960, 500)
