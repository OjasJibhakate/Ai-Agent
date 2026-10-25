"""Draw every cat frame side by side into a PNG (so the art can be looked at). Run:
uv run python scripts/preview_cat.py out.png"""

import sys
import tkinter as tk

from ragnarok.gui import sprites

root = tk.Tk()
root.withdraw()
zoom = 8
names = list(sprites.frames())
cols = 4
rows_n = (len(names) + cols - 1) // cols
sheet = tk.PhotoImage(width=cols * (sprites.W * zoom + 20), height=rows_n * (sprites.H * zoom + 20))
sheet.put("#3a3f4b", to=(0, 0, sheet.width(), sheet.height()))
for i, name in enumerate(names):
    ox, oy = (i % cols) * (sprites.W * zoom + 20) + 10, (i // cols) * (sprites.H * zoom + 20) + 10
    for y, row in enumerate(sprites.frames()[name]):
        for x, ch in enumerate(row):
            colour = sprites.PALETTE.get(ch)
            if colour:
                sheet.put(
                    colour,
                    to=(ox + x * zoom, oy + y * zoom, ox + (x + 1) * zoom, oy + (y + 1) * zoom),
                )
sheet.write(sys.argv[1], format="png")
print("wrote", sys.argv[1], names)
