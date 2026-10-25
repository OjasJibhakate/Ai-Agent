"""The pixel cat's pictures: every pose, drawn by catart from the same parts.

A frame is a list of strings (one per row, one letter per pixel; "." is see-through). All frames
share one size and one floor line, so switching frames never makes the cat jump. Frames face right;
a left-facing frame is the mirror image. Poses that can look toward the pointer also exist as
"name@-1" and "name@1" (pupils shifted).
"""

from __future__ import annotations

from ragnarok.gui import catart

W, H = catart.W, catart.H
KEY = "#fe00fe"  # the colour Windows treats as see-through (never used by the cat)
PALETTE = catart.PALETTE

HEART = [".R.R.", "RRRRR", "RRRRR", ".RRR.", "..R.."]
BANG = [".Y.", ".R.", ".R.", ".R.", "...", ".R."]
ZEE = ["ZZZ", "..Z", ".Z.", "ZZZ"]
DOTS = [["ZZ", "ZZ"], ["ZZ.ZZ", "ZZ.ZZ"], ["ZZ.ZZ.ZZ", "ZZ.ZZ.ZZ"]]  # one, two, three dots

WALK = [
    ((-3, 0), (3, 1), (3, 0), (-3, 1)),
    ((0, 0), (0, 0), (0, 0), (0, 0)),
    ((3, 1), (-3, 0), (-3, 1), (3, 0)),
    ((0, 0), (0, 0), (0, 0), (0, 0)),
]
LOOKS = (-1, 1)  # pupils to the left and to the right of the sprite


def overlay(rows: list[str], art: list[str], x: int, y: int) -> list[str]:
    out = [list(r) for r in rows]
    for dy, line in enumerate(art):
        for dx, ch in enumerate(line):
            if ch != "." and 0 <= y + dy < len(out) and 0 <= x + dx < len(out[0]):
                out[y + dy][x + dx] = ch
    return ["".join(r) for r in out]


def mirror(rows: list[str]) -> list[str]:
    return [r[::-1] for r in rows]


def frames() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    # ---- walking and standing
    for i, legs in enumerate(WALK):
        out[f"walk{i}"] = catart.stand(legs, bob=1 if i in (1, 3) else 0).rows()
    out["stand"] = catart.stand().rows()
    out["stand_blink"] = catart.stand(eyes="closed").rows()
    for look in LOOKS:
        out[f"stand@{look}"] = catart.stand(look=look).rows()
    # ---- sitting, and the small things a sitting cat does
    out["sit"] = catart.sit().rows()
    out["sit_b"] = catart.sit(bob=1).rows()  # one breath in
    out["sit_blink"] = catart.sit(eyes="closed").rows()
    out["sit_swish0"] = catart.sit(tail="floor2", flick=True).rows()
    out["sit_swish1"] = catart.sit(tail="floor").rows()
    out["sit_ear"] = catart.sit(ears="twitch").rows()
    for look in LOOKS:
        out[f"sit@{look}"] = catart.sit(look=look).rows()
    out["sit_tilt0"] = catart.sit(tilt=1, ears="up").rows()  # curious: the head turns a little...
    out["sit_tilt1"] = catart.sit(tilt=-1, ears="up").rows()  # ...one way, then the other
    # ---- yawning, scratching, grooming
    out["yawn0"] = catart.sit(eyes="squint", mouth="open1").rows()
    out["yawn1"] = catart.sit(eyes="squint", mouth="open2", ears="back").rows()
    out["yawn2"] = catart.sit(eyes="squint", mouth="open1").rows()
    out["scratch0"] = catart.sit(paw_up="scratch0", eyes="squint").rows()
    out["scratch1"] = catart.sit(paw_up="scratch1", eyes="squint").rows()
    out["groom0"] = catart.sit(paw_up="groom0", eyes="closed").rows()
    out["groom1"] = catart.sit(paw_up="groom1", eyes="closed").rows()
    # ---- lying down, sleeping, waking and stretching
    out["loaf"] = catart.sleep(eyes="open", ears="normal").rows()
    sleeping = [catart.sleep(bob=b).rows() for b in (0, 1, 0)]
    out["sleep0"] = overlay(sleeping[0], ZEE, 38, 14)
    out["sleep1"] = overlay(sleeping[1], ZEE, 39, 9)
    out["sleep2"] = sleeping[2]
    out["stretch0"] = catart.stretch(0).rows()
    out["stretch1"] = catart.stretch(1).rows()
    # ---- reacting
    out["alert"] = overlay(catart.stand(eyes="wide", ears="up", tail="up2").rows(), BANG, 41, 0)
    out["happy"] = overlay(catart.sit(eyes="squint", tail="floor2").rows(), HEART, 38, 0)
    out["happy_b"] = overlay(catart.sit(eyes="squint", tail="floor").rows(), HEART, 38, 0)  # wag
    tucked = ((0, 6), (0, 6), (0, 6), (0, 6))
    out["jump"] = overlay(catart.stand(tucked, eyes="squint", tail="up2").rows(), HEART, 39, 0)
    out["pounce"] = catart.stand(tucked, eyes="wide", ears="back", tail="low").rows()
    dangle = ((0, -3), (0, -4), (0, -3), (0, -4))
    out["carried0"] = catart.stand(dangle, rise=7, eyes="wide", tail="low").rows()
    swing = ((1, -3), (-1, -4), (1, -3), (-1, -4))
    out["carried1"] = catart.stand(swing, rise=7, eyes="wide", tail="up").rows()
    for n in (1, 2, 3):  # thinking: dots over its head
        out[f"think{n}"] = overlay(catart.sit().rows(), DOTS[n - 1], 36, 1)
    return out
