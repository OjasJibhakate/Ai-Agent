"""How big the cat is on screen, at any Windows display scaling."""

import tkinter as tk
from fractions import Fraction

import pytest

from ragnarok.gui import sprites
from ragnarok.gui.app import cat_ratio, scaled


def test_the_cat_is_a_little_smaller_than_the_old_2x_size_and_grows_with_display_scaling():
    old_w, old_h = sprites.W * 2, sprites.H * 2  # 88 x 72
    ratio = cat_ratio(1.0)
    w, h = round(sprites.W * ratio), round(sprites.H * ratio)
    assert (w, h) == (77, 63) and 0.8 * old_w < w < old_w and 0.8 * old_h < h < old_h
    assert cat_ratio(1.5) == Fraction(21, 8) and cat_ratio(2.0) == Fraction(7, 2)
    assert cat_ratio(1.25) == Fraction(35, 16)
    assert cat_ratio(1.0) < 2 and all(cat_ratio(s) >= 7 / 4 for s in (1.0, 1.25, 1.5, 2.0))


@pytest.fixture
def root(hidden_tk):
    return hidden_tk


def image_of(rows: list[str]) -> tk.PhotoImage:
    image = tk.PhotoImage(width=sprites.W, height=sprites.H)
    lines = [
        "{" + " ".join(sprites.PALETTE.get(c) or sprites.KEY for c in row) + "}" for row in rows
    ]
    image.put(" ".join(lines))
    return image


@pytest.mark.parametrize("dpi", [1.0, 1.25, 1.5, 2.0])
def test_the_scaled_picture_has_the_expected_size_for_every_frame_shape(root, dpi):
    ratio = cat_ratio(dpi)
    for name in ("stand", "sit", "sleep0", "stretch1"):
        picture = scaled(image_of(sprites.frames()[name]), ratio)
        assert abs(picture.width() - sprites.W * ratio) <= 1.5, name
        assert abs(picture.height() - sprites.H * ratio) <= 1.5, name


def dark(picture: tk.PhotoImage) -> int:
    count = 0
    for y in range(picture.height()):
        for x in range(picture.width()):
            r, g, b = picture.get(x, y)
            count += (r, g, b) == (0x24, 0x1A, 0x2B)  # the outline colour
    return count


def test_the_thin_outline_survives_the_fractional_scaling(root):
    rows = sprites.frames()["stand"]
    full = scaled(image_of(rows), Fraction(2, 1))
    small = scaled(image_of(rows), cat_ratio(1.0))
    outline_pixels = sum(r.count("K") for r in rows)
    assert (
        dark(small) >= 0.6 * outline_pixels * (7 / 4) ** 2 * 0.9
    )  # nearly all of it is still there
    assert 0.6 < dark(small) / dark(full) < 0.95  # a little less area because the cat is smaller


def test_the_background_stays_see_through_after_scaling(root):
    picture = scaled(image_of(sprites.frames()["stand"]), cat_ratio(1.0))
    assert tuple(picture.get(0, 0)) == (254, 0, 254)  # the key colour, so Windows can punch it out
    assert tuple(picture.get(picture.width() - 1, picture.height() - 1)) == (254, 0, 254)
