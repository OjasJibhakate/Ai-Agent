"""Two guards found by the live screen eval: no typing before anything has focus, and no second
click on a spot that is really on a different item than the model said."""

import numpy as np

from ragnarok.skills.screen.capture import Frame
from ragnarok.skills.screen.task import names_the_target
from tests.screen_fakes import click, done, fill, typing
from tests.test_screen_capture import decode_png
from tests.test_screen_task import acts, make

NOT_YET = [{"complete": False, "why": "the file is not open"}]


def two_clicks():
    # nearby but not identical points, so the loop's own "same action twice" check stays out of it
    return [click(300, 300, "budget.xlsx"), click(305, 300, "budget.xlsx"), done()]


async def test_typing_before_anything_has_focus_is_held_back_and_the_model_is_told_to_fill():
    loop, world, vision, *_ = make([typing("lofi beats"), fill(300, 200, "lofi beats"), done()])
    result = await loop.run("search for lofi beats")
    assert [e[0] for e in acts(world)] == ["click", "type"]  # the fill, not the bare type
    assert any("nothing has the focus" in a for a in result.actions) or any(
        "nothing has the focus" in p for p in vision.prompts
    )


async def test_a_bare_type_after_a_click_is_allowed():
    loop, world, *_ = make([click(100, 100), typing("hello"), done()])
    assert await loop.run("type hello")
    assert [e[0] for e in acts(world)] == ["click", "type"]


async def test_the_same_spot_again_is_not_clicked_when_it_is_on_something_else():
    loop, world, vision, *_ = make(two_clicks(), verdicts=NOT_YET)
    vision.under = ["readme.md"]  # what is really at that point
    await loop.run("open budget.xlsx")
    assert [e[0] for e in acts(world)] == ["click"]  # the repeat was held back
    assert vision.marked, "the model was shown a marked picture of the spot"
    assert any("readme.md" in p for p in vision.prompts)  # and told what is really there


async def test_the_same_spot_again_goes_ahead_when_it_is_the_target():
    loop, world, vision, *_ = make(two_clicks(), verdicts=NOT_YET)
    vision.under = ["budget.xlsx"]
    await loop.run("open budget.xlsx")
    assert [e[0] for e in acts(world)] == ["click", "click"]


async def test_an_unsure_reading_never_blocks_a_click():
    loop, world, vision, *_ = make(two_clicks(), verdicts=NOT_YET)
    vision.under = [""]
    await loop.run("open budget.xlsx")
    assert [e[0] for e in acts(world)] == ["click", "click"]


def test_names_the_target_is_forgiving_about_wording():
    assert names_the_target("budget.xlsx", "budget.xlsx")
    assert names_the_target("the Submit button", "Submit")
    assert not names_the_target("budget.xlsx", "readme.md")
    assert not names_the_target("budget.xlsx", "")


def test_the_marked_picture_zooms_in_and_draws_a_red_cross_on_the_point():
    rgb = np.full((400, 640, 3), 200, np.uint8)
    frame = Frame(rgb, 0, 0, 1280, 800)
    image = decode_png(frame.marked_crop(640, 400))
    assert image.shape[0] > 120 and image.shape[1] > 320  # zoomed
    red = (image[..., 0] == 255) & (image[..., 1] == 0) & (image[..., 2] == 0)
    assert red.sum() > 100
    ys, xs = np.nonzero(red)
    assert abs(xs.mean() - image.shape[1] / 2) < 6 and abs(ys.mean() - image.shape[0] / 2) < 6


async def test_a_scroll_that_moved_nothing_tells_the_model_to_aim_at_the_list():
    scroll = {"action": "scroll", "direction": "down", "x": 900, "y": 900}
    loop, world, vision, *_ = make([scroll, scroll, done()])
    world.frozen = True  # the screen does not change, as when the scroll is not over the list
    await loop.run("find zebra.txt")
    assert any("That scroll moved nothing" in p for p in vision.prompts)


async def test_a_click_a_few_pixels_from_the_last_on_the_same_thing_counts_as_a_repeat():
    # the model re-aims slightly each time; without this it clicked "Export" four times
    loop, world, vision, *_ = make(
        [click(300, 300, "Export"), click(310, 305, "Export"), click(300, 300, "Export")]
    )  # default verdict: the task is complete
    result = await loop.run("click Export")
    # the second click was checked first, and it had worked
    assert [e[0] for e in acts(world)] == ["click"]
    assert result.ok
