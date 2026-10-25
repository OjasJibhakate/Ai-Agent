"""The pixel-art renderer: shapes, outlines, shading, and every pose being the same cat."""

import pytest

from ragnarok.gui import catart, sprites
from ragnarok.gui.catart import Canvas


def count(rows: list[str], ch: str) -> int:
    return sum(r.count(ch) for r in rows)


# ---- the shapes ---------------------------------------------------------------------------------


def test_an_ellipse_is_symmetric_and_about_the_right_size():
    cells = Canvas.ellipse(10, 10, 5, 3)
    assert abs(len(cells) - 3.14159 * 5 * 3) < 6
    assert min(x for x, _ in cells) == 5 and max(x for x, _ in cells) == 14
    assert {(19 - x, y) for x, y in cells} == cells  # the same on both sides


def test_a_polygon_fills_its_inside_and_a_stroke_is_as_thick_as_asked():
    triangle = Canvas.polygon([(0, 0), (10, 0), (0, 10)])
    assert (2, 2) in triangle and (9, 9) not in triangle
    line = Canvas.stroke([(2, 5), (12, 5)], 3)
    assert 3 <= len({y for _, y in line}) <= 4 and min(x for x, _ in line) <= 2  # about 3 thick


def test_a_part_gets_an_outline_over_what_is_behind_it_and_shading_inside():
    c = Canvas()
    c.part(Canvas.rect(5, 5, 20, 20))
    c.part(Canvas.ellipse(12, 12, 4, 4))  # a second part on top of the first
    rows = c.rows()
    assert rows[12][7] == "K" or rows[12][8] == "K"  # the new part is ringed in the old one
    first = Canvas()
    first.part(Canvas.rect(5, 5, 20, 20))
    shown = first.rows()
    assert (
        shown[4][10] == "K" and shown[5][5] == "L" and shown[20][20] == "S" and shown[12][12] == "O"
    )


def test_paint_only_covers_the_colours_it_is_told_to():
    c = Canvas()
    c.part(Canvas.rect(2, 2, 8, 8))
    c.paint(Canvas.rect(0, 0, 10, 10), "D", only_on="O")
    assert c.at(5, 5) == "D" and c.at(2, 2) != "D" and c.at(0, 0) == "."  # not outside, not the rim


def test_drawing_off_the_edge_is_harmless():
    c = Canvas()
    c.part(Canvas.ellipse(0, 0, 6, 6))
    c.part(Canvas.ellipse(catart.W, catart.H, 6, 6))
    assert len(c.rows()) == catart.H and all(len(r) == catart.W for r in c.rows())


# ---- the cat ------------------------------------------------------------------------


ALL = sprites.frames()
POSED = [n for n in ALL if "@" not in n]


@pytest.mark.parametrize("name", POSED)
def test_every_pose_has_an_outline_fur_and_is_not_cut_off_by_the_edge(name):
    rows = ALL[name]
    assert count(rows, "K") > 40 and count(rows, "O") + count(rows, "L") + count(rows, "S") > 80
    assert all(r[0] not in "OLSDW" and r[-1] not in "OLSDW" for r in rows)  # (whiskers may reach)


@pytest.mark.parametrize(
    "name", [n for n in POSED if n.startswith(("stand", "walk", "sit", "yawn", "groom", "scratch"))]
)
def test_the_cat_wears_its_collar_and_bell_in_every_awake_pose(name):
    rows = ALL[name]
    assert count(rows, "C") >= 6 and count(rows, "Y") >= 2


@pytest.mark.parametrize(
    "name", [n for n in POSED if n.startswith(("stand", "sit", "alert", "walk"))]
)
def test_the_awake_cat_has_green_eyes_pink_ears_and_a_pink_nose(name):
    rows = ALL[name]
    if name in ("stand_blink", "sit_blink"):
        assert count(rows, "G") == 0  # eyes shut
    else:
        assert count(rows, "G") >= 8
    assert count(rows, "P") >= 6


def test_the_tail_has_stripes_and_a_white_tip_and_the_socks_are_white():
    rows = ALL["stand"]
    assert count(rows, "D") > 15 and count(rows, "W") > 20
    assert any(r[:12].count("W") for r in rows[:12])  # the white tip of the raised tail


def test_the_far_legs_are_darker_than_the_near_legs():
    rows = ALL["stand"]
    assert count(rows, "S") > 40 and count(rows, "V") >= 2  # the shadowed far side


def test_eyes_look_the_way_they_are_told_to():
    left, centre, right = ALL["stand@-1"], ALL["stand"], ALL["stand@1"]
    assert left != centre != right and left != right
    assert abs(count(left, "E") - count(centre, "E")) <= 3  # the same eyes, the pupils shifted
    assert abs(count(right, "E") - count(centre, "E")) <= 3


def test_the_yawn_opens_the_mouth_in_stages_and_the_sleeper_has_its_eyes_shut():
    small, wide = count(ALL["yawn0"], "M"), count(ALL["yawn1"], "M")
    assert 0 < small < wide and count(ALL["yawn1"], "T") >= 2  # a tongue at the widest
    assert count(ALL["sleep2"], "G") == 0 and count(ALL["loaf"], "G") >= 8  # shut, then open


def test_walking_changes_the_legs_and_standing_breathing_changes_one_row():
    assert len({tuple(ALL[f"walk{i}"]) for i in range(4)}) >= 3
    assert ALL["sit"] != ALL["sit_b"]
    changed = sum(a != b for a, b in zip(ALL["sit"], ALL["sit_b"], strict=True))
    assert 3 <= changed <= 40  # the upper body moves one row: a breath, not a different cat


def test_the_overlays_sit_in_the_free_space_above_the_cat_and_do_not_replace_it():
    for name, colour in (("alert", "R"), ("happy", "R"), ("sleep0", "Z"), ("think3", "Z")):
        assert count(ALL[name], colour) >= 3, name
    base = ALL["sit"]
    assert (
        sum(
            1
            for a, b in zip(base, ALL["think3"], strict=True)
            for x, y in zip(a, b, strict=True)
            if x != y
        )
        < 40
    )
