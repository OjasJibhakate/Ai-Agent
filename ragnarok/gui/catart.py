"""Drawing the cat: pixel art built from parts, so every pose is the same cat.

Each body part (tail, legs, body, head, ears ...) is a set of pixels. Drawing a part gives it a dark
outline over whatever is behind it, then fills it with fur and shades it from the top left: light on
the edges that face the light, shadow on the edges that face away. Details (stripes, eyes, whiskers,
the collar) go on last. A pose is a handful of parameters, not a hand-drawn picture, so a new pose
or animation is a few lines.

The grid is W x H; the floor is the bottom row. The cat looks right; a left-looking cat is a mirror.
"""

from __future__ import annotations

import math

W, H = 44, 36
Cell = tuple[int, int]

PALETTE: dict[str, str | None] = {
    ".": None,
    "K": "#241a2b",  # outline
    "O": "#f2a65a",  # fur
    "L": "#f9c98e",  # fur in the light
    "S": "#d4813b",  # fur in shadow
    "D": "#bd682a",  # tabby stripes
    "W": "#fff4e3",  # muzzle, belly, socks, tail tip
    "V": "#e3d3bb",  # the same white in shadow (the far socks)
    "P": "#f48fb1",  # nose, inner ears
    "E": "#1b1b2f",  # pupils, mouth line
    "G": "#8fdc7f",  # eyes
    "w": "#f6ecdb",  # whiskers, the glint in an eye
    "C": "#8b6cf0",  # the collar (Ragnarok violet)
    "Y": "#ffd23f",  # the bell
    "M": "#4a1f2d",  # inside the mouth
    "T": "#ff7a93",  # tongue
    "Z": "#9ad7ff",  # sleep "z"
    "R": "#ff5d73",  # the "!" and the heart
}


class Canvas:
    def __init__(self) -> None:
        self.px: list[list[str]] = [["."] * W for _ in range(H)]

    # ---- shapes (they return the set of pixels they cover) -------------------------------------

    @staticmethod
    def ellipse(cx: float, cy: float, rx: float, ry: float) -> set[Cell]:
        cells = set()
        for y in range(int(cy - ry) - 1, int(cy + ry) + 2):
            for x in range(int(cx - rx) - 1, int(cx + rx) + 2):
                if ((x + 0.5 - cx) / rx) ** 2 + ((y + 0.5 - cy) / ry) ** 2 <= 1.0:
                    cells.add((x, y))
        return cells

    @staticmethod
    def rect(x0: int, y0: int, x1: int, y1: int) -> set[Cell]:
        return {(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)}

    @staticmethod
    def polygon(points: list[tuple[float, float]]) -> set[Cell]:
        cells = set()
        ys = [p[1] for p in points]
        for y in range(int(min(ys)), int(max(ys)) + 1):
            xs = []
            for (ax, ay), (bx, by) in zip(points, points[1:] + points[:1], strict=True):
                if (ay <= y + 0.5 < by) or (by <= y + 0.5 < ay):
                    xs.append(ax + (y + 0.5 - ay) * (bx - ax) / (by - ay))
            xs.sort()
            for left, right in zip(xs[0::2], xs[1::2], strict=False):
                cells.update((x, y) for x in range(math.floor(left + 0.5), math.floor(right + 0.5)))
        return cells

    @staticmethod
    def stroke(points: list[tuple[float, float]], thick: float = 1.0) -> set[Cell]:
        """A thick line along a path (a tail, a leg)."""
        cells: set[Cell] = set()
        for (ax, ay), (bx, by) in zip(points, points[1:], strict=False):
            steps = max(2, int(math.dist((ax, ay), (bx, by)) * 2))
            for i in range(steps + 1):
                t = i / steps
                cells |= Canvas.ellipse(
                    ax + (bx - ax) * t, ay + (by - ay) * t, thick / 2, thick / 2
                )
        return cells

    # ---- painting --------------------------------------------------------------------------------

    def put(self, x: int, y: int, ch: str) -> None:
        if 0 <= x < W and 0 <= y < H:
            self.px[y][x] = ch

    def at(self, x: int, y: int) -> str:
        return self.px[y][x] if 0 <= x < W and 0 <= y < H else "."

    def part(
        self, cells: set[Cell], fill: str = "O", *, outline: bool = True, shade: bool = True
    ) -> set[Cell]:
        """Draw a part: outline over what is behind it, fur, then light and shadow."""
        if outline:
            for x, y in cells:
                for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                    if (nx, ny) not in cells:
                        self.put(nx, ny, "K")
        for x, y in cells:
            ch = fill
            if shade and fill == "O":
                lit = (x, y - 1) not in cells or (x - 1, y) not in cells
                dark = (x, y + 1) not in cells or (x + 1, y) not in cells
                ch = "L" if lit and not dark else "S" if dark and not lit else "O"
            self.put(x, y, ch)
        return cells

    def paint(self, cells: set[Cell], ch: str, only_on: str = "") -> None:
        """Colour pixels without an outline (stripes, patches), optionally only over some colours."""
        for x, y in cells:
            if not only_on or self.at(x, y) in only_on:
                self.put(x, y, ch)

    def rows(self) -> list[str]:
        return ["".join(r) for r in self.px]


# ---- the cat ---------------------------------------------------------------------------------------


def tail_path(kind: str) -> list[tuple[float, float]]:
    """Where the tail goes, from the rump outward."""
    return {
        "up": [(9, 17), (6, 15), (4, 11), (4, 7), (6, 4)],
        "up2": [(9, 17), (6, 14), (3.5, 10), (3.5, 6), (5, 3)],
        "low": [(9, 19), (5, 20), (3.5, 18), (3, 15)],
        "floor": [(11, 33), (7, 34), (4, 33), (3, 30)],
        "floor2": [(11, 33), (7, 34), (5, 34), (3, 33)],
        "curl": [(30, 34), (24, 34), (14, 34), (8, 33), (5, 30)],
    }[kind]


def stripe_tail(c: Canvas, path: list[tuple[float, float]], thick: float = 2.4) -> None:
    n = len(path) - 1
    for i in range(1, n + 1):
        x, y = path[i]
        if i % 2 == 0:
            c.paint(Canvas.ellipse(x, y, thick / 2 + 0.2, thick / 2 + 0.2), "D", only_on="OLS")
    x, y = path[-1]
    c.paint(Canvas.ellipse(x, y, 1.2, 1.2), "W", only_on="OLSD")  # a white tip


def head(
    c: Canvas,
    cx: float,
    cy: float,
    *,
    eyes: str = "open",
    look: int = 0,
    mouth: str = "closed",
    ears: str = "normal",
    tilt: int = 0,
) -> None:
    """The face: ears, head, white muzzle, eyes, nose, mouth, whiskers."""
    up = {"normal": 0, "up": 1, "twitch": 0, "back": -1, "flat": -2}[ears]
    twitch = -1 if ears == "twitch" else 0
    # ears (behind the head)
    left = [(cx - 6.5, cy - 3), (cx - 5.2, cy - 8.5 - up), (cx - 1.0, cy - 5)]
    right = [(cx + 1.0, cy - 5), (cx + 5.2 + twitch, cy - 8.5 - up), (cx + 6.5, cy - 3)]
    c.part(Canvas.polygon(left))
    c.part(Canvas.polygon(right))
    c.paint(
        Canvas.polygon([(cx - 5.2, cy - 4.4), (cx - 4.6, cy - 7.0 - up), (cx - 2.6, cy - 5)]), "P"
    )
    c.paint(
        Canvas.polygon(
            [(cx + 2.6, cy - 5), (cx + 4.6 + twitch, cy - 7.0 - up), (cx + 5.2, cy - 4.4)]
        ),
        "P",
    )
    # head, with cheek tufts
    skull = (
        Canvas.ellipse(cx, cy, 7.2, 5.6)
        | Canvas.ellipse(cx - 5.5, cy + 2.5, 2.2, 2.0)
        | Canvas.ellipse(cx + 5.5, cy + 2.5, 2.2, 2.0)
    )
    c.part(skull)
    c.paint(Canvas.ellipse(cx, cy + 3, 3.4, 2.2), "W", only_on="OLSD")  # the white muzzle
    c.paint(
        {(int(cx), int(cy - 5)), (int(cx - 1), int(cy - 4)), (int(cx + 1), int(cy - 4))},
        "D",
        only_on="OLS",
    )  # the M on a tabby's forehead
    ex_l, ex_r, ey = int(cx - 4), int(cx + 2), int(cy - 1)
    for ex in (ex_l, ex_r):
        draw_eye(c, ex, ey, eyes, look)
    # nose and mouth
    nx, ny = int(cx - 1), int(cy + 2)
    c.put(nx, ny, "P")
    c.put(nx + 1, ny, "P")
    if mouth == "closed":
        c.put(nx - 1, ny + 1, "E")
        c.put(nx + 2, ny + 1, "E")
        c.put(nx, ny + 2, "E") if tilt else None
    else:
        size = 3 if mouth == "open2" else 2
        for yy in range(ny + 1, ny + 1 + size):
            for xx in range(nx - 1, nx + 3):
                c.put(xx, yy, "M")
        c.paint({(nx, ny + size), (nx + 1, ny + size)}, "T")  # a pink tongue
    # whiskers go on last, only over the empty background
    for sign, base in ((-1, cx - 6.5), (1, cx + 6.5)):
        for dy, reach in ((1, 5), (3, 5)):
            for i in range(1, reach + 1):
                x, y = int(base + sign * i), int(cy + dy + (i // 3))
                if c.at(x, y) == ".":
                    c.put(x, y, "w")


def draw_eye(c: Canvas, x: int, y: int, state: str, look: int) -> None:
    if state == "closed":
        for dx in range(3):
            c.put(x + dx, y + 1, "E")
        return
    if state == "squint":
        for dx in range(3):
            c.put(x + dx, y + 1, "D")
        c.put(x, y, "D")
        c.put(x + 2, y, "D")
        return
    height = 4 if state == "wide" else 3
    for dy in range(height):
        for dx in range(3):
            c.put(x + dx, y + dy, "G")
    c.put(x + 1 + look, y, "E")  # the slit pupil, shifted to look toward something
    c.put(x + 1 + look, y + 1, "E")
    c.put(x + 1 + look, y + 2, "E")
    if height == 4:
        c.put(x + 1 + look, y + 3, "E")
    c.put(x, y, "w")  # the glint


def collar(c: Canvas, cx: float, cy: float) -> None:
    """A violet collar with a gold bell, drawn over the neck (after the head)."""
    for i in range(-5, 6):
        y = int(cy + (abs(i) ** 2) * 0.09)
        c.paint({(int(cx + i), y), (int(cx + i), y + 1)}, "C", only_on="OLSWD")
    bx, by = int(cx), int(cy) + 3
    c.paint({(bx, by), (bx + 1, by), (bx, by + 1), (bx + 1, by + 1)}, "Y")
    c.put(bx, by + 2, "K")
    c.put(bx + 1, by + 2, "K")


def leg(
    c: Canvas, x: float, dx: float = 0, up: float = 0, *, far: bool = False, floor: int = 34,
    top: float = 23, width: float = 3.0,
) -> None:  # fmt: skip
    """A leg from the body to the floor, swung by dx and lifted by `up`, ending in a white sock."""
    foot = floor - up
    path = [(x + 1.5, top), (x + 1.5 + dx * 0.4, (top + foot) / 2), (x + 1.5 + dx, foot - 1)]
    cells = Canvas.stroke(path, width)
    cells |= {(int(x + dx), int(foot)), (int(x + dx + 1), int(foot)), (int(x + dx + 2), int(foot))}
    c.part(cells, fill="S" if far else "O", shade=not far)
    c.paint({(px, py) for px, py in cells if py >= foot - 2}, "V" if far else "W", only_on="OLSD")


def stand(
    legs: tuple[tuple[float, float], ...] = ((0, 0), (0, 0), (0, 0), (0, 0)),
    *,
    tail: str = "up",
    lift: int = 0,
    rise: int = 0,
    bob: int = 0,
    **face: object,
) -> Canvas:
    """Standing on four legs, looking right. `legs` are (swing, lift) for the far hind, near hind,
    far front and near front legs."""
    c = Canvas()
    path = tail_path(tail)
    c.part(Canvas.stroke(path, 2.6))
    stripe_tail(c, path)
    dy = -rise - bob
    bases = (10, 14, 29, 33)
    for i in (0, 2):  # the legs on the far side
        swing, up = legs[i]
        leg(c, bases[i], swing, up, far=True, floor=34 - lift, top=23 + dy)
    body = (
        Canvas.ellipse(20, 19, 11.5, 6.8)
        | Canvas.ellipse(29, 20, 5.5, 6.2)
        | Canvas.ellipse(13, 21, 5.5, 5.5)
    )
    c.part({(x, y + dy) for x, y in body})
    c.paint(
        {(x, y + dy) for x, y in Canvas.ellipse(23, 24, 8, 2.6)}, "W", only_on="OLSD"
    )  # the belly
    c.paint(
        {(x, y + dy) for x, y in Canvas.ellipse(30, 21, 2.5, 3.2)}, "W", only_on="OLSD"
    )  # the chest tuft
    for sx in (12, 15, 18, 21, 24):  # tabby stripes down the back
        c.paint({(sx + k // 2, 13 + k + dy) for k in range(0, 4)}, "D", only_on="OLS")
    for i in (1, 3):  # the near legs, with a thigh on the hind one
        swing, up = legs[i]
        if i == 1:
            c.part({(x, y + dy) for x, y in Canvas.ellipse(14.5, 22.5, 4.4, 4.6)})
        leg(c, bases[i], swing, up, floor=34 - lift, top=24 + dy)
    head(c, 34, 12 + dy, **face)
    collar(c, 33, 17 + dy)
    return c


def sit(
    *,
    tail: str = "floor",
    bob: int = 0,
    paw_up: str = "",
    flick: bool = False,
    **face: object,
) -> Canvas:
    """Sitting upright, looking right. `paw_up` raises a paw: "groom" to the mouth, "scratch" to the ear."""
    c = Canvas()
    path = tail_path(tail)
    c.part(Canvas.stroke(path, 2.8))
    stripe_tail(c, path)
    dy = -bob
    c.part({(x, y + dy) for x, y in Canvas.ellipse(15, 28, 8.5, 7.0)})  # the haunch
    c.part(Canvas.ellipse(20, 33, 4.5, 1.8))  # the hind foot
    c.paint(Canvas.ellipse(20, 33, 3.6, 1.0), "W", only_on="OLSD")
    torso = Canvas.ellipse(23, 24, 7.6, 10.0)
    c.part({(x, y + dy) for x, y in torso})
    c.paint(
        {(x, y + dy) for x, y in Canvas.ellipse(26, 25, 3.8, 7.0)}, "W", only_on="OLSD"
    )  # chest
    for sy in (18, 21, 24):
        c.paint({(x, sy + dy) for x in range(16, 20)}, "D", only_on="OLS")  # stripes on the flank
    leg(c, 24, 0, 0, far=True, floor=34, top=26 + dy, width=2.6)  # the far front leg
    leg(c, 27, 0, 0, floor=34, top=26 + dy, width=3.0)
    for k in range(3):
        c.paint({(14 + k * 3 + j, 22 + dy + k) for j in range(2)}, "D", only_on="OLS")
    if paw_up:
        # a hind leg swings up to the head to scratch, or a front paw comes up to the mouth
        if paw_up.startswith("scratch"):
            swing = 1 if paw_up.endswith("1") else 0
            thigh = Canvas.stroke(
                [(14, 29 + dy), (12 - swing, 21 + dy), (15 - swing, 14 + dy)], 3.2
            )
            c.part(thigh)
            c.paint(Canvas.ellipse(15 - swing, 13.5 + dy, 1.6, 1.2), "W", only_on="OLSD")
        else:
            lift = 1 if paw_up.endswith("1") else 0
            c.part(Canvas.stroke([(28, 27 + dy), (30, 20 + dy), (31, 16 - lift + dy)], 3.0))
            c.paint(Canvas.ellipse(31, 15.5 - lift + dy, 1.7, 1.3), "W", only_on="OLSD")
    head(c, 28, 10 + dy, **face)
    collar(c, 27, 15 + dy)
    if flick:  # the very tip of the tail lifts
        x, y = path[-1]
        c.paint(Canvas.ellipse(x, y - 1, 1.3, 1.3), "W")
    return c


def sleep(*, bob: int = 0, eyes: str = "closed", ears: str = "flat", **face: object) -> Canvas:
    """Curled up: a rounded loaf, the head resting on its paws, the tail wrapped in front."""
    c = Canvas()
    dy = -bob
    body = Canvas.ellipse(19, 28.5, 13.5, 6.8) | Canvas.ellipse(10.5, 27.5, 7.4, 6.8)
    c.part({(x, y + dy) for x, y in body})
    c.paint({(x, y + dy) for x, y in Canvas.ellipse(21, 32, 8, 1.6)}, "W", only_on="OLSD")
    for sx in (9, 12, 15, 18, 21, 24):
        c.paint({(sx + k // 2, 22 + k + dy) for k in range(0, 4)}, "D", only_on="OLS")
    tail = [(7, 33), (14, 34.5), (22, 34.5), (29, 33.5)]  # in front, round the belly
    c.part(Canvas.stroke(tail, 2.6))
    stripe_tail(c, tail[::-1])
    c.part(Canvas.ellipse(31, 33, 3.6, 1.7))  # a front paw tucked under the chin
    c.paint(Canvas.ellipse(31, 33, 2.8, 1.0), "W", only_on="OLSD")
    head(c, 34, 26 + dy, eyes=eyes, ears=ears, **face)
    collar(c, 33, 31 + dy)
    return c


def stretch(phase: int = 0) -> Canvas:
    """A play bow: chest down, front legs reaching, rump and tail up. `phase` 1 is the deepest."""
    c = Canvas()
    deep = phase
    path = [(9, 16 - deep), (7, 12 - deep), (6, 8 - deep)]
    c.part(Canvas.stroke(path, 2.6))
    stripe_tail(c, path)
    leg(c, 7, 0, 0, far=True, floor=34, top=21 - deep, width=3.0)  # far hind, straight
    body = Canvas.stroke([(11, 19 - deep), (24, 25 + deep)], 10.5)
    c.part(body)
    c.paint(Canvas.stroke([(14, 25 - deep), (24, 28 + deep)], 3.0), "W", only_on="OLSD")
    for sx in range(12, 22, 3):
        c.paint({(sx, 15 + (sx - 12) // 3 + k - deep) for k in range(3)}, "D", only_on="OLS")
    leg(c, 11, 0, 0, floor=34, top=23 - deep)  # near hind
    far = Canvas.stroke([(25, 27 + deep), (34, 32)], 3.0)
    c.part(far, fill="S", shade=False)
    c.paint({(x, y) for x, y in far if x >= 32}, "V", only_on="S")
    near = Canvas.stroke([(27, 28 + deep), (36, 33)], 3.0)
    c.part(near)
    c.paint({(x, y) for x, y in near if x >= 34}, "W", only_on="OLSD")
    head(c, 35, 27 + deep, eyes="squint", mouth="closed", ears="normal")
    collar(c, 33, 32 + deep)
    return c
