"""A contact sheet of the cat's frames: big (to judge detail) and real size (to judge how it reads).
Usage: python -m scripts.preview_sheet out.png [frame names...]"""

import os
import sys
import tkinter as tk

from ragnarok.gui import sprites
from ragnarok.gui.app import cat_ratio, scaled

DEFAULT = [
    "stand", "sit", "sit_blink", "yawn1", "groom0", "scratch0", "loaf", "sleep0",
    "stretch1", "alert", "happy", "carried0",
]  # fmt: skip
ZOOM, COLS = int(os.environ.get("ZOOM", 7)), int(os.environ.get("COLS", 4))


def image_of(rows: list[str]) -> tk.PhotoImage:
    image = tk.PhotoImage(width=sprites.W, height=sprites.H)
    lines = ["{" + " ".join(sprites.PALETTE.get(c) or "#3a3f4b" for c in row) + "}" for row in rows]
    image.put(" ".join(lines))
    return image


def main() -> None:
    root = tk.Tk()
    root.withdraw()
    names = sys.argv[2:] or DEFAULT
    frames = sprites.frames()
    cw, ch = sprites.W * ZOOM + 12, sprites.H * ZOOM + 12
    rows_n = (len(names) + COLS - 1) // COLS
    real = round(sprites.H * cat_ratio(1.0)) + 12
    sheet = tk.PhotoImage(width=COLS * cw, height=rows_n * ch + real)
    sheet.put("#3a3f4b", to=(0, 0, sheet.width(), sheet.height()))
    sheet.put("#6b2d85", to=(0, rows_n * ch + real - 14, sheet.width(), sheet.height()))  # taskbar
    for i, name in enumerate(names):
        big = image_of(frames[name]).zoom(ZOOM, ZOOM)
        sheet.tk.call(sheet, "copy", big, "-to", (i % COLS) * cw + 6, (i // COLS) * ch + 6)
        small = scaled(image_of(frames[name]), cat_ratio(1.0))
        x = i * (small.width() + 8) + 6
        if x + small.width() < sheet.width():
            sheet.tk.call(sheet, "copy", small, "-to", x, rows_n * ch + 4)
    sheet.write(sys.argv[1], format="png")
    print("wrote", sheet.width(), sheet.height(), names)


main()
