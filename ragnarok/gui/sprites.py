"""The pixel cat: frames drawn as text, turned into Tk images.

Every frame is a grid of characters facing right (a left-facing frame is its mirror). One letter is
one pixel; '.' is see-through. All frames share one canvas size and one floor line, so switching
frames never makes the cat jump.
"""

from __future__ import annotations

W, H = 26, 26
KEY = "#fe00fe"  # the colour Windows treats as see-through (never used by the cat)
PALETTE = {
    ".": None,
    "K": "#2a1c2e",  # outline
    "O": "#f2a65a",  # fur
    "D": "#d9772f",  # stripes
    "W": "#fff3e0",  # chest, paws, muzzle
    "P": "#f48fb1",  # nose, inner ear
    "E": "#1b1b2f",  # eyes
    "Z": "#9ad7ff",  # sleep "z"
    "R": "#ff5d73",  # the "!" and the heart
    "Y": "#ffd23f",  # alert mark glow
}

STAND = """
..............KK.....KK
..............KOK...KOK
..............KOOKKKOOK
..............KOOOOOOOK
..............KOEOOOEOK
..............KOOOPOOOK
..............KOWWKWWOK
...KK.......KKKKKKKKKK.
..KOK.....KKOOOOOOOK...
..KOK...KKOOOODDOOOK...
..KOKKKKOOOODODDOOOK...
...KOOOOOOODODDOOOOK...
....KOOOOOOOOOOOWWOK...
....KOOOOOOOOOOWWWWK...
.....KKOOOOOOOOOOOOK...
.....KOWK..KOWK.KOWK...
.....KOWK..KOWK.KOWK...
.....KKKK..KKKK.KKKK...
""".strip("\n").split("\n")

SIT = """
................KK.....KK
................KOK...KOK
................KOOKKKOOK
................KOOOOOOOK
................KOEOOOEOK
................KOOOPOOOK
................KOWWKWWOK
.................KKKKKKKK
................KOOOOOOOOK
...............KOODODOOOOK
..............KOOODODOOOOK
..............KOOOWWWWOOOK
..............KOOOWWWWOOOK
.....KK.......KOOOWWWWOOOK
....KOOK......KOOOWWWWOOOK
...KOOOKKKKKKKKOOOOOOOOOOK
...KODOOOOOOOOOOOOOOOOOOOK
...KKOOOOOOOOOOKWKOOKWKOOK
.....KKKKKKKKKKKKKKKKKKKK.
""".strip("\n").split("\n")

SLEEP = """
..........................
..........................
..........................
..........................
..........................
..........................
..........................
..........................
..............KK.....KK...
.......KKKKKKKKOKKKKKKOK..
.....KKOOOOOOOOOOOOOOOOOK.
....KOODODODODOOOKKOOKKOK.
...KOODODODODOOOOOOOOOOOOK
...KODODODODODOOOOOPOOOOWK
...KOOOOOOOOOOOOOOOWWWWWWK
....KKOOOOOOOOOOOOOOOOOK.
......KKKKKKKKKKKKKKKKKK..
""".strip("\n").split("\n")

HEART = ["..R.R..", ".RRRRR.", ".RRRRR.", "..RRR..", "...R..."]
BANG = ["..Y..", "..R..", "..R..", "..R..", ".....", "..R.."]
ZEE = ["ZZZ", "..Z", ".Z.", "ZZZ"]


def _pad(rows: list[str], width: int = W, height: int = H) -> list[str]:
    """Left-align, pad the right with see-through, and push to the bottom of the canvas."""
    rows = [r.ljust(width, ".")[:width] for r in rows]
    blank = ["." * width] * max(0, height - len(rows))
    return (blank + rows)[-height:]


def blink(rows: list[str]) -> list[str]:
    return [r.replace("E", "K") for r in rows]


def lift(rows: list[str], n: int) -> list[str]:
    """The same picture n pixels off the floor (a jump)."""
    return rows[n:] + ["." * len(rows[0])] * n


def overlay(rows: list[str], art: list[str], x: int, y: int) -> list[str]:
    out = [list(r) for r in rows]
    for dy, line in enumerate(art):
        for dx, ch in enumerate(line):
            if ch != "." and 0 <= y + dy < len(out) and 0 <= x + dx < len(out[0]):
                out[y + dy][x + dx] = ch
    return ["".join(r) for r in out]


def _squint(rows: list[str]) -> list[str]:
    """Eyes screwed shut (a yawn, a good scratch)."""
    return [r.replace("E", "D") for r in rows]


def _open_mouth(rows: list[str], size: int) -> list[str]:
    """The sitting cat's mouth, a little open (1) or wide open (2)."""
    out = list(rows)
    top = next(i for i, r in enumerate(out) if "KOWWKWWOK" in r)  # the muzzle row
    col = out[top].index("KOWWKWWOK")
    out[top] = out[top][:col] + "KOWKKKWOK" + out[top][col + 9 :]
    if size >= 2:
        below = out[top + 1]
        out[top + 1] = below[:col] + "KOKKKKKOK" + below[col + 9 :]
    return out


def mirror(rows: list[str]) -> list[str]:
    return [r[::-1] for r in rows]


# The three leg rows of the walking cat, one set per step of the walk.
WALK_LEGS = [
    [".....KOWK..KOWK.KOWK....", ".....KOWK..KOWK.KOWK....", ".....KKKK..KKKK.KKKK...."],
    ["....KOWK...KOWK...KOWK..", "....KOWK...KOWK...KKKK...", "....KKKK...KKKK.........."],
    ["......KOWK.KOWK..KOWK...", "......KOWK.KOWK..KOWK....", "......KKKK.KKKK..KKKK...."],
    [".....KOWK...KOWK.KOWK...", ".....KKKK...KOWK.KOWK...", "..........KKKK.KKKK......"],
]


def frames() -> dict[str, list[str]]:
    body = STAND[:-3]
    out: dict[str, list[str]] = {}
    for i, legs in enumerate(WALK_LEGS):
        out[f"walk{i}"] = _pad(body + legs)
    stand = _pad(STAND)
    sit = _pad(SIT)
    sleep = _pad(blink(SLEEP))
    out["stand"] = stand
    out["stand_blink"] = blink(stand)
    out["sit"] = sit
    out["sit_blink"] = blink(sit)
    out["sleep0"] = overlay(sleep, ZEE, 20, 8)
    out["sleep1"] = overlay(sleep, ZEE, 21, 4)
    out["sleep2"] = sleep
    out["alert"] = overlay(stand, BANG, 17, 0)  # ears up, an exclamation mark over the head
    out["jump"] = overlay(lift(_pad(body + ["....KKKK..KKKK.KKKK...."]), 4), HEART, 18, 0)
    # held by the scruff: the legs dangle and swing; or springing at something with the legs tucked
    hang = ["....KOWK..KOWK..KOWK..KOWK"] * 4
    out["carried0"] = _pad(
        [
            *body,
            *[r.replace("....KOWK..KOWK..KOWK..KOWK", ".....KOWK..KOWK.KOWK.KOWK") for r in hang],
            ".....KKKK..KKKK.KKKK.KKKK",
        ]
    )
    out["carried1"] = _pad(
        [
            *body,
            *[r.replace("....KOWK..KOWK..KOWK..KOWK", "....KOWK..KOWK.KOWK..KOWK") for r in hang],
            "....KKKK..KKKK.KKKK..KKKK",
        ]
    )
    out["pounce"] = lift(_pad([*body, "....KKKK..KKKK.KKKK...."]), 3)
    # yawning: eyes squeezed shut, the mouth opens wide and closes again
    out["yawn0"] = _squint(_open_mouth(sit, 1))
    out["yawn1"] = _squint(_open_mouth(sit, 2))
    out["yawn2"] = _squint(_open_mouth(sit, 1))
    # scratching an ear with a hind leg: the leg swings up to the head and back
    leg = ["KWK.", "KOK.", "KOK.", "KOOK", "KOOOK"]
    out["scratch0"] = _squint(overlay(sit, leg, 11, 10))
    out["scratch1"] = _squint(overlay(sit, leg, 11, 11))
    out["groom0"] = overlay(sit, ["KWK", "KWK"], 13, 11)  # a paw at the face
    out["groom1"] = overlay(sit, ["KWK", "KWK"], 13, 9)
    out["happy"] = overlay(sit, HEART, 17, 0)
    for n in range(1, 4):  # thinking: one, two, three dots over the head
        out[f"think{n}"] = overlay(sit, ["Z." * n], 18, 6)
    return out
