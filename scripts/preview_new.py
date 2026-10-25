"""Draw catart poses into a PNG. Usage: preview_new.py out.png"""

import sys
import tkinter as tk

from ragnarok.gui import catart

root = tk.Tk()
root.withdraw()
poses = {
    "sit": catart.sit(),
    "sit swish": catart.sit(tail="floor2", flick=True, ears="twitch"),
    "yawn": catart.sit(eyes="squint", mouth="open2"),
    "groom": catart.sit(paw_up="groom0", eyes="closed"),
    "scratch": catart.sit(paw_up="scratch0", eyes="squint"),
    "sleep": catart.sleep(),
    "stretch": catart.stretch(1),
    "carried": catart.stand(((0, -2), (0, -3), (0, -2), (0, -3)), rise=6, eyes="wide", tail="low"),
}
zoom, cols = 6, 4
cw, ch = catart.W * zoom + 16, catart.H * zoom + 16
rows_n = (len(poses) + cols - 1) // cols
sheet = tk.PhotoImage(width=cols * cw, height=rows_n * ch)
sheet.put("#3a3f4b", to=(0, 0, sheet.width(), sheet.height()))
for i, canvas in enumerate(poses.values()):
    ox, oy = (i % cols) * cw + 8, (i // cols) * ch + 8
    for y, row in enumerate(canvas.rows()):
        for x, c in enumerate(row):
            colour = catart.PALETTE.get(c)
            if colour:
                sheet.put(
                    colour,
                    to=(ox + x * zoom, oy + y * zoom, ox + (x + 1) * zoom, oy + (y + 1) * zoom),
                )
sheet.write(sys.argv[1], format="png")
print("wrote", list(poses))
