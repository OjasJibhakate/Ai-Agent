"""The cat's behaviour and pictures: no window needed."""

import random

import pytest

from ragnarok.gui import sprites
from ragnarok.gui.brain import STEP_TIME, WALK_SPEED, CatBrain

LEFT, RIGHT = 0.0, 1800.0


def run(brain: CatBrain, seconds: float, dt: float = 0.04):
    poses = []
    for _ in range(int(seconds / dt)):
        poses.append(brain.step(dt))
    return poses


def brain(seed: int = 1, x: float | None = 600.0) -> CatBrain:
    return CatBrain(LEFT, RIGHT, rng=random.Random(seed), x=x)


# ---- pictures --------------------------------------------------------------------------------


def test_every_frame_is_the_same_size_and_uses_only_known_colours():
    for name, rows in sprites.frames().items():
        assert len(rows) == sprites.H, name
        assert all(len(r) == sprites.W for r in rows), name
        assert set("".join(rows)) <= set(sprites.PALETTE), name
        assert any(ch != "." for r in rows for ch in r), name  # not empty


def test_the_see_through_colour_is_not_used_by_the_cat():
    assert sprites.KEY.lower() not in {c.lower() for c in sprites.PALETTE.values() if c}


def test_mirroring_twice_gives_the_same_picture():
    for rows in sprites.frames().values():
        assert sprites.mirror(sprites.mirror(rows)) == rows


def test_every_frame_the_brain_can_ask_for_exists():
    known = set(sprites.frames())
    seen: set[str] = set()
    for seed in range(8):
        b = brain(seed)
        if seed == 0:
            b.alert()
        for step in range(400):
            seen.add(b.step(0.04).frame)
            if seed == 1 and step == 100:
                b.poke()
            if seed == 2 and step == 100:
                b.set_busy(True)
            if seed == 2 and step == 200:
                b.set_busy(False)
            if seed == 3 and step == 100:
                b.drag_to(900)
            if seed == 3 and step == 150:
                b.drop()
        for _ in range(3000):  # and a long ordinary stretch
            seen.add(b.step(0.1).frame)
    assert seen <= known, seen - known
    assert {"walk0", "sit", "sleep0", "groom0", "alert", "happy", "think1", "jump"} <= seen


def test_the_feet_are_on_the_bottom_row_of_every_standing_frame():
    for name in ("stand", "sit", "walk0", "walk2"):
        assert any(ch != "." for ch in sprites.frames()[name][-1]), name  # paws touch the floor


# ---- wandering --------------------------------------------------------------------------------


def test_the_cat_never_leaves_the_taskbar_and_does_a_bit_of_everything():
    b = brain(3)
    poses = run(b, 20 * 60)  # twenty minutes
    assert all(LEFT <= p.x <= RIGHT for p in poses)
    modes = {p.mode for p in poses}
    assert {"walk", "sit", "sleep", "groom", "stand"} <= modes
    assert 150 < max(p.x for p in poses) - min(p.x for p in poses)  # it does drift about


def test_walking_moves_at_a_steady_pace_toward_the_target_and_faces_it():
    b = brain(5)
    for _ in range(4000):  # until it decides to walk
        pose = b.step(0.05)
        if pose.mode == "walk":
            break
    start, facing = pose.x, pose.facing
    for _ in range(20):
        after = b.step(0.05)
    moved = after.x - start
    assert after.mode == "walk" and abs(abs(moved) - WALK_SPEED * 1.0) < WALK_SPEED * 0.15
    assert (moved > 0) == (facing > 0) and after.facing == facing


def test_the_walk_cycles_through_four_frames():
    b = brain(5)
    frames = []
    for _ in range(4000):
        pose = b.step(0.02)
        if pose.mode == "walk":
            frames.append(pose.frame)
        elif frames:
            break
    assert set(frames) == {"walk0", "walk1", "walk2", "walk3"}
    assert STEP_TIME * 4 < 1.0


def test_it_blinks_now_and_then():
    frames = set()
    for seed in range(6):
        b = brain(seed)
        for _ in range(15 * 60 * 10):
            frames.add(b.step(0.1).frame)
    assert frames & {"stand_blink", "sit_blink"}


# ---- being interrupted --------------------------------------------------------------------------


def test_a_click_makes_it_hop_and_then_it_carries_on():
    b = brain(4)
    run(b, 5)
    b.poke()
    assert b.step(0.04).mode == "happy" and b.step(0.04).frame in ("jump", "happy")
    later = run(b, 5)
    assert later[-1].mode != "happy"


def test_an_alert_stays_until_it_is_cleared():
    b = brain(4)
    b.alert()
    poses = run(b, 120)
    assert all(p.mode == "alert" and p.frame == "alert" for p in poses)
    b.clear_alert()
    assert b.step(0.04).mode == "happy"
    b.clear_alert()  # clearing again does nothing
    assert not b.alerting


def test_while_busy_it_sits_and_thinks_with_growing_dots_then_celebrates():
    b = brain(4)
    b.set_busy(True)
    poses = run(b, 6)
    assert {p.mode for p in poses} == {"think"}
    assert {p.frame for p in poses} == {"think1", "think2", "think3"}
    b.set_busy(True)  # saying it twice changes nothing
    assert b.step(0.04).mode == "think"
    b.set_busy(False)
    assert b.step(0.04).mode == "happy"


def test_it_can_be_carried_along_the_taskbar_but_not_off_it():
    b = brain(4)
    b.drag_to(-500)
    assert b.x == LEFT and b.step(0.04).mode == "carried"
    b.drag_to(99999)
    assert b.x == RIGHT
    b.drag_to(900)
    b.drop()
    assert b.x == 900 and not b.dragging
    assert b.step(0.04).mode == "stand"


def test_with_the_chat_open_it_stays_put():
    b = brain(6)
    b.anchored = True
    poses = run(b, 600)
    assert "walk" not in {p.mode for p in poses}
    assert len({round(p.x) for p in poses}) == 1


def test_a_screen_that_shrinks_keeps_the_cat_on_it():
    b = brain(7, x=1500)
    b.resize(0, 800)
    assert b.x == 800
    assert all(0 <= p.x <= 800 for p in run(b, 300))


def test_a_very_narrow_strip_does_not_make_it_wander_forever():
    b = CatBrain(0, 20, rng=random.Random(1), x=10)
    assert all(0 <= p.x <= 20 for p in run(b, 300))


@pytest.mark.parametrize("seed", range(5))
def test_the_same_seed_gives_the_same_day(seed):
    a = [p.frame for p in run(brain(seed), 60)]
    b = [p.frame for p in run(brain(seed), 60)]
    assert a == b


# ---- the pointer, carrying and falling ---------------------------------------------------------

FLOOR, WIDTH, HEIGHT = 1000.0, 78.0, 78.0


def watcher(seed: int = 1, x: float = 600.0) -> CatBrain:
    return CatBrain(
        LEFT, RIGHT, rng=random.Random(seed), x=x, floor=FLOOR, width=WIDTH, height=HEIGHT
    )


def move_pointer(b: CatBrain, x: float, y: float, seconds: float, dt: float = 0.04):
    poses = []
    for _ in range(int(seconds / dt)):
        b.set_cursor(x, y)
        poses.append(b.step(dt))
    return poses


def test_a_pointer_resting_on_the_cat_makes_it_purr_and_stopping_ends_it():
    b = watcher()
    poses = move_pointer(b, b.x + 39, FLOOR - 30, 2.0)
    assert poses[-1].mode == "purr" and poses[-1].frame == "happy"
    assert poses[10].mode != "purr"  # not at once: it takes a moment
    after = move_pointer(b, 100, 300, 1.5)  # the pointer leaves
    assert after[-1].mode != "purr"


def test_a_pointer_far_away_on_the_taskbar_is_ignored():
    b = watcher(x=200)
    poses = move_pointer(b, 1300, FLOOR + 20, 12.0)  # 1000 px away
    assert not {p.mode for p in poses} & {"chase", "watch", "pounce", "purr"}
    assert max(p.x for p in poses) < 700  # it wandered, but not after the pointer


def test_a_pointer_that_comes_near_is_chased_then_watched():
    b = watcher(x=200)
    move_pointer(b, 1300, FLOOR + 20, 2.0)  # first far away: nothing
    assert b.mode not in ("chase", "watch")
    poses = move_pointer(b, 420, FLOOR + 20, 8.0)  # then it comes close
    modes = [p.mode for p in poses]
    assert "chase" in modes and modes[-1] == "watch"
    assert poses[-1].frame in ("sit", "sit_blink") and poses[-1].facing == 1
    assert abs((poses[-1].x + WIDTH / 2) - 420) <= 100  # sat a little way from it
    start = modes.index("chase")
    assert (poses[start + 15].x - poses[start].x) / 0.6 > 100  # it runs, it does not stroll


def test_a_pointer_that_only_whizzes_past_does_not_make_it_chase():
    b = watcher(x=200)
    for _ in range(6):  # 0.2 s near it, then gone
        b.set_cursor(380, FLOOR + 20)
        b.step(0.033)
    b.set_cursor(1700, FLOOR + 20)
    modes = {b.step(0.04).mode for _ in range(30)}
    assert not modes & {"chase", "watch"}


def test_once_following_it_keeps_going_until_the_pointer_is_really_far():
    b = watcher(x=200)
    move_pointer(b, 420, FLOOR + 20, 6.0)
    assert b.mode == "watch"
    move_pointer(b, 620, FLOOR + 20, 3.0)  # 380 px from where it sits: still interested
    assert b.mode in ("chase", "watch") and b.x > 300
    move_pointer(b, 1750, FLOOR + 20, 2.0)  # a long way off now
    assert b.mode not in ("chase", "watch")


def test_the_cat_looks_toward_a_pointer_on_its_left():
    b = watcher(x=1400)
    poses = move_pointer(b, 1280, FLOOR + 20, 8.0)
    assert poses[-1].mode == "watch" and poses[-1].facing == -1
    assert poses[-1].x < 1400


def test_a_fast_flick_near_a_watching_cat_makes_it_pounce_then_watch_again():
    b = watcher(x=200)
    move_pointer(b, 420, FLOOR + 20, 6.0)
    assert b.mode == "watch"
    modes, lifts = [], []
    for i in range(25):  # the pointer whips past
        b.set_cursor(420 + i * 22, FLOOR + 20)
        pose = b.step(0.02)
        modes.append(pose.mode)
        lifts.append(pose.lift)
    assert "pounce" in modes and max(lifts) > 20
    for _ in range(40):
        b.set_cursor(700, FLOOR + 20)
        b.step(0.04)
    assert b.mode in ("chase", "watch") and b.lift == 0


def test_a_pointer_far_above_the_taskbar_is_ignored_and_a_follower_loses_interest():
    b = watcher(x=200)
    move_pointer(b, 420, FLOOR - 600, 3.0)
    assert b.mode not in ("chase", "watch", "purr")
    move_pointer(b, 420, FLOOR + 20, 4.0)
    assert b.mode in ("chase", "watch")
    move_pointer(b, 420, FLOOR - 600, 3.0)  # it went away
    assert b.mode not in ("chase", "watch")


def test_turning_chasing_off_stops_it_following():
    b = watcher(x=200)
    b.chase = False
    poses = move_pointer(b, 400, FLOOR + 20, 8.0)
    assert not {p.mode for p in poses} & {"chase", "watch", "pounce", "purr"}
    b2 = watcher(x=200)
    move_pointer(b2, 400, FLOOR + 20, 4.0)
    assert b2.mode in ("chase", "watch")
    b2.chase = False
    assert b2.step(0.04).mode not in ("chase", "watch")


def test_it_ignores_the_pointer_while_the_chat_is_open_or_it_is_busy_or_alerting():
    for setup in ("anchored", "busy", "alert"):
        b = watcher(x=200)
        if setup == "anchored":
            b.anchored = True
        elif setup == "busy":
            b.set_busy(True)
        else:
            b.alert()
        modes = {p.mode for p in move_pointer(b, 1300, FLOOR + 20, 6.0)}
        assert not modes & {"chase", "watch", "pounce", "purr"}, setup


def test_carrying_it_lifts_it_with_the_pointer_and_clamps_the_height():
    b = watcher()
    pose = None
    b.drag_to(900, 120)
    pose = b.step(0.04)
    assert pose.mode == "carried" and pose.lift == 120 and pose.x == 900
    assert pose.frame in ("carried0", "carried1")
    b.drag_to(900, 5000)
    assert b.lift == 280  # no higher than it can be held
    b.drag_to(900, -50)
    assert b.lift == 0


def test_letting_go_in_the_air_it_falls_bounces_and_lands_on_the_taskbar():
    b = watcher()
    b.drag_to(900, 200)
    b.drop()
    assert b.mode == "fall"
    lifts, modes = [], []
    for _ in range(200):
        pose = b.step(0.016)
        lifts.append(pose.lift)
        modes.append(pose.mode)
    assert lifts[0] < 200 and all(lift >= 0 for lift in lifts)
    first_floor = next(i for i, lift in enumerate(lifts) if lift == 0)
    assert any(lift > 3 for lift in lifts[first_floor + 1 : first_floor + 12])  # it bounced
    assert modes[-1] != "fall" and lifts[-1] == 0  # and it landed
    assert b.x == 900  # where you let go


def test_letting_go_on_the_taskbar_just_puts_it_down():
    b = watcher()
    b.drag_to(300, 0)
    b.drop()
    assert b.mode == "stand" and b.lift == 0 and b.x == 300


def test_it_can_be_dropped_at_either_end_of_the_taskbar():
    b = watcher()
    for target, expected in ((-400, LEFT), (99999, RIGHT)):
        b.drag_to(target, 0)
        b.drop()
        assert b.x == expected


def test_sleep_here_makes_it_doze_and_a_click_wakes_it():
    b = watcher()
    b.sleep_now()
    assert b.step(0.04).mode == "sleep"
    b.poke()
    assert b.step(0.04).mode == "happy"


# ---- idling like a cat ---------------------------------------------------------------------------


def everything_it_does(seeds: int = 12, minutes: int = 12, lively: bool = False):
    seen: dict[str, int] = {}
    frames: set[str] = set()
    for seed in range(seeds):
        b = brain(seed)
        b.lively = lively
        last = ""
        for _ in range(minutes * 60 * 10):
            pose = b.step(0.1)
            frames.add(pose.frame)
            if pose.mode != last:
                seen[pose.mode] = seen.get(pose.mode, 0) + 1
                last = pose.mode
    return seen, frames


def test_when_nobody_is_around_it_still_does_a_bit_of_everything_just_rarely():
    seen, frames = everything_it_does(seeds=20, minutes=40)
    for mode in ("walk", "sit", "stand", "groom", "sleep", "yawn", "scratch"):
        assert seen.get(mode, 0) >= 8, (mode, seen)
    assert {"yawn0", "yawn1", "yawn2", "scratch0", "scratch1"} <= frames


def test_the_playful_cat_does_all_of_it_much_more_often_including_batting_at_nothing():
    seen, frames = everything_it_does(seeds=6, minutes=12, lively=True)
    for mode in ("walk", "sit", "stand", "groom", "sleep", "yawn", "scratch", "play"):
        assert seen.get(mode, 0) > 5, (mode, seen)
    assert "pounce" in frames


def test_a_yawn_opens_wide_and_closes_and_the_cat_sometimes_dozes_off_after_it():
    b = brain(3)
    b._enter("yawn")
    sequence = []
    for _ in range(30):
        sequence.append(b.step(0.1).frame)
    first = [f for i, f in enumerate(sequence) if i == 0 or f != sequence[i - 1]]
    assert first[:3] == ["yawn0", "yawn1", "yawn2"]
    after_yawn = {"sleep": 0, "other": 0}
    for seed in range(60):
        c = brain(seed)
        c._enter("yawn", 0.5)
        for _ in range(8):
            pose = c.step(0.1)
        after_yawn["sleep" if pose.mode == "sleep" else "other"] += 1
    assert 12 < after_yawn["sleep"] < 45  # often, not always


def test_scratching_swings_the_leg_between_two_pictures_without_moving():
    b = brain(3)
    b._enter("scratch", 2.0)
    poses = [b.step(0.05) for _ in range(30)]
    assert {p.frame for p in poses} == {"scratch0", "scratch1"}
    assert len({round(p.x) for p in poses}) == 1


def test_playing_is_a_short_hop_forward_that_ends_back_on_the_floor():
    b = brain(3, x=600)
    b.facing = 1
    b._enter("play")
    poses = [b.step(0.02) for _ in range(40)]
    assert poses[0].mode == "play" and poses[0].frame == "pounce"
    assert max(p.lift for p in poses) > 15 and poses[-1].lift == 0
    assert 20 < poses[-1].x - 600 < 80  # a hop, not a walk
    assert poses[-1].mode != "play"


def test_a_cat_in_the_corner_does_not_hop_out_of_the_screen():
    b = CatBrain(0, 1800, rng=random.Random(2), x=1800)
    b.facing = 1
    b._enter("play")
    assert all(0 <= b.step(0.02).x <= 1800 for _ in range(40))


def test_with_the_chat_open_it_neither_wanders_nor_hops():
    b = brain(9)
    b.anchored = True
    poses = run(b, 900, 0.1)
    assert not {p.mode for p in poses} & {"walk", "play"}
    assert len({round(p.x) for p in poses}) == 1


def test_it_sometimes_turns_to_look_behind_it_when_standing_about():
    flips = 0
    for seed in range(40):
        b = brain(seed)
        before = b.facing
        for _ in range(60 * 60 * 10):
            b.step(0.1)
            if b.mode == "stand" and b.facing != before:
                flips += 1
                break
    assert flips > 8


# ---- calm: it must not pull the eye while you work ----------------------------------


def day(lively: bool, seeds: int = 15, minutes: int = 30):
    changes = walking = ticks = 0
    travelled = 0.0
    longest_still = 0.0
    for seed in range(seeds):
        b = brain(seed)
        b.lively = lively
        last, last_x, still = "", b.x, 0.0
        for _ in range(minutes * 600):
            pose = b.step(0.1)
            ticks += 1
            walking += pose.mode in ("walk", "play")
            travelled += abs(pose.x - last_x)
            moving = pose.x != last_x or pose.mode in ("play", "walk")
            still = 0.0 if moving else still + 0.1
            longest_still = max(longest_still, still)
            last_x = pose.x
            if pose.mode != last:
                changes += 1
                last = pose.mode
    return {
        "changes_per_minute": changes / (seeds * minutes),
        "walking": walking / ticks,
        "px_per_minute": travelled / (seeds * minutes),
        "longest_still": longest_still,
    }


def test_the_default_cat_is_calm_about_one_change_a_minute_and_almost_never_walking():
    calm = day(lively=False)
    assert calm["changes_per_minute"] < 2.0  # not every few seconds
    assert calm["walking"] < 0.05  # on its feet and moving less than 1 second in 20
    assert calm["px_per_minute"] < 120  # a stroll across a few icons, not a lap of the screen
    assert calm["longest_still"] > 120  # and it can sit without moving for minutes


def test_the_playful_cat_really_is_busier_than_the_calm_one():
    calm, lively = day(lively=False), day(lively=True, seeds=5, minutes=10)
    assert lively["changes_per_minute"] > 3 * calm["changes_per_minute"]
    assert lively["walking"] > 5 * calm["walking"]


def test_a_calm_stroll_is_short_and_a_rest_is_long():
    b = brain(4)
    stroll, rests = [], []
    last_mode, start_x, started = "", b.x, 0.0
    for tick in range(60 * 60 * 10):
        pose = b.step(0.1)
        if pose.mode != last_mode:
            if last_mode == "walk":
                stroll.append(abs(pose.x - start_x))
            if last_mode in ("sit", "sleep"):
                rests.append(tick / 10 - started)
            last_mode, start_x, started = pose.mode, pose.x, tick / 10
    assert stroll and max(stroll) <= 270  # never a trek across the screen
    assert (
        rests and sorted(rests)[len(rests) // 2] > 25
    )  # the typical rest is half a minute or more
