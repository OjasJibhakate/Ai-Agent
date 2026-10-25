"""Draw new-style cat frames into a PNG for a look. Usage: preview_art.py out.png [names...]"""

import sys
import tkinter as tk

from ragnarok.gui import sprites

root = tk.Tk()
root.withdraw()
frames = sprites.frames()
names = sys.argv[2:] or list(frames)
zoom, cols = 6, 4
cw, ch = sprites.W * zoom + 16, sprites.H * zoom + 16
rows_n = (len(names) + cols - 1) // cols
sheet = tk.PhotoImage(width=cols * cw, height=rows_n * ch)
sheet.put("#3a3f4b", to=(0, 0, sheet.width(), sheet.height()))
for i, name in enumerate(names):
    ox, oy = (i % cols) * cw + 8, (i // cols) * ch + 8
    for y, row in enumerate(frames[name]):
        for x, c in enumerate(row):
            colour = sprites.PALETTE.get(c)
            if colour:
                sheet.put(
                    colour,
                    to=(ox + x * zoom, oy + y * zoom, ox + (x + 1) * zoom, oy + (y + 1) * zoom),
                )
sheet.write(sys.argv[1], format="png")
print("wrote", sys.argv[1], len(names), "frames")
