"""Save a PNG of a region of the screen (to check how the cat and its windows look).
Usage: python scripts/shot.py out.png [left top right bottom]"""

import sys

from ragnarok.skills.screen.capture import WindowsScreen

region = tuple(int(v) for v in sys.argv[2:6]) if len(sys.argv) >= 6 else None
frame = WindowsScreen(region).grab(max_width=2000)
open(sys.argv[1], "wb").write(frame.png())
print("saved", sys.argv[1], frame.width, frame.height)
