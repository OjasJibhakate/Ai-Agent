"""The screen task loop on a fake desktop: what it does, in which order, and when it must stop."""

import asyncio
import json
import re

import numpy as np
import pytest

from ragnarok.policy import permissions
from ragnarok.skills.screen import task as task_mod
from ragnarok.skills.screen.capture import Frame
from ragnarok.skills.screen.task import MAX_STEPS, ScreenTask
from tests.screen_fakes import (
    GATEWAY_DOWN,
    NOTEPAD,
    POWERSHELL,
    TERMINAL,
    FakeClock,
    FakeDriver,
    FakeScreen,
    FakeWindows,
    ScriptedVision,
    World,
    click,
    done,
    fill,
    key,
    typing,
)
from tests.test_screen_capture import decode_png


def make(decisions, *, verdicts=None, profile="screen", confirm=None, **opts):
    world, clock = World(), FakeClock()
    vision = ScriptedVision(decisions, verdicts)
    logged, said = [], []

    async def log(kind, payload):
        world.events.append(("log", json.loads(payload)))
        logged.append((kind, json.loads(payload)))

    async def progress(text):
        said.append(text)

    loop = ScreenTask(
        FakeScreen(world),
        FakeDriver(world),
        FakeWindows(world),
        vision,
        profile=profile,
        log=log,
        progress=progress,
        confirm=confirm,
        sleep=clock.sleep,
        poll=lambda _seconds: asyncio.sleep(0),  # the Esc watcher yields without moving the clock
        clock=clock,
        **opts,
    )
    return loop, world, vision, logged, said


def acts(world):
    return [e for e in world.events if e[0] in ("click", "type", "key", "scroll")]


# ---- the happy path and the order of things -------------------------------------------------


async def test_a_task_runs_to_the_end_and_is_checked(tmp_path):
    loop, world, vision, logged, said = make(
        [key("win"), typing("notepad"), key("enter"), done("opened Notepad")]
    )
    result = await loop.run("open notepad")
    assert result.ok is True and result.summary == "Done: opened Notepad" and result.steps == 4
    assert acts(world) == [("key", "win"), ("type", "notepad"), ("key", "enter")]
    assert said == [
        "step 1: press win",
        "step 2: type 'notepad' into the box",
        "step 3: press enter",
    ]
    assert result.actions == ["1. press win", "2. type 'notepad' into the box", "3. press enter"]


async def test_every_action_is_logged_before_it_happens():
    loop, world, *_ = make([click(500, 500), typing("hi"), key("enter"), done()])
    await loop.run("do things")
    sequence = [e[0] for e in world.events if e[0] in ("log", "click", "type", "key")]
    assert sequence == [
        "log",
        "click",
        "log",
        "type",
        "log",
        "key",
    ]  # a log entry always comes first


async def test_the_log_holds_a_record_of_the_action_and_never_the_picture():
    loop, world, vision, logged, _ = make(
        [click(500, 250, "the search box"), typing("lofi"), done()]
    )
    await loop.run("search lofi")
    kind, record = logged[0]
    assert (
        kind == "screen_action"
        and record["action"] == "click"
        and record["target"] == "the search box"
    )
    assert (record["x"], record["y"]) == (640 - 1 + 0, round(0.25 * 719)) or record["x"] == round(
        0.5 * 1279
    )
    assert record["task"] == "search lofi" and record["step"] == 1
    assert logged[1][1]["text"] == "lofi"
    assert all(len(json.dumps(r)) < 400 for _, r in logged)  # a short record, no image data


async def test_the_model_gets_one_valid_png_per_step_and_a_full_history():
    loop, world, vision, *_ = make([key("win"), typing("calc"), done()])
    await loop.run("open the calculator", user_words="please open the calculator app")
    assert len(vision.pngs) == 3
    for png in vision.pngs:
        assert decode_png(png).shape == (360, 640, 3)
    first, last = vision.prompts[0], vision.prompts[-1]
    assert "Task: open the calculator" in first and "(nothing yet)" in first
    assert 'The user\'s own words: "please open the calculator app"' in first
    assert "1. press win" in last and "2. type 'calc' into the box" in last


async def test_coordinates_on_the_0_1000_scale_become_screen_pixels():
    loop, world, *_ = make([click(500, 500), click(0, 0), click(1000, 1000), done()], max_steps=6)
    await loop.run("click around")
    assert [e[1][:2] for e in world.events if e[0] == "click"] == [(640, 360), (0, 0), (1279, 719)]


async def test_a_region_offset_is_added_to_clicks():
    loop, world, *_ = make([click(0, 0), done()])
    loop.screen = FakeScreen(world, left=60, top=80)
    await loop.run("click the corner")
    assert [e[1][:2] for e in world.events if e[0] == "click"] == [(60, 80)]


async def test_double_right_click_and_scroll_use_the_right_driver_calls():
    loop, world, *_ = make(
        [
            {"action": "double_click", "x": 100, "y": 100, "target": "a file"},
            {"action": "right_click", "x": 200, "y": 200, "target": "the desktop"},
            {"action": "scroll", "amount": 3},
            {"action": "scroll", "amount": -2, "x": 900, "y": 900},
            done(),
        ]
    )
    await loop.run("various")
    got = acts(world)
    assert got[0][1][2:] == ("left", 2) and got[1][1][2:] == ("right", 1)
    assert got[2][1] == (3, (640, 360))  # no point given: the middle of the screen
    assert got[3][1][0] == -2 and got[3][1][1][0] > 1000


# ---- the user's own terminal ----------------------------------------------------------------


async def test_the_users_terminal_is_moved_aside_and_always_put_back():
    loop, world, *_ = make([key("win"), done()])
    world.foreground = TERMINAL
    await loop.run("open the start menu")
    assert world.hidden == [222] and world.restored == [222]
    order = [e[0] for e in world.events if e[0] in ("minimize", "grab", "restore")]
    assert order[0] == "minimize" and order[-1] == "restore"  # hidden before the first look


async def test_the_terminal_is_restored_even_when_the_task_blows_up():
    loop, world, vision, *_ = make([key("win")])

    async def boom(png, prompt):
        raise RuntimeError("kaboom")

    vision.decide = boom
    world.foreground = TERMINAL
    with pytest.raises(RuntimeError):
        await loop.run("anything")
    assert world.restored == [222]


async def test_other_windows_are_left_alone():
    loop, world, *_ = make([done()])
    world.foreground = NOTEPAD
    await loop.run("look")
    assert world.hidden == [] and world.restored == []


# ---- stopping -------------------------------------------------------------------------------


async def test_it_gives_up_after_the_step_limit():
    loop, world, *_ = make([click(10 + i, 10) for i in range(40)] + [done()], max_steps=5)
    # distinct clicks, so repeats do not stop it first; the model never says done
    result = await loop.run("never ends")
    assert result.ok is False and result.steps == 5 and "within 5 steps" in result.summary
    assert len(acts(world)) == 5


async def test_the_default_step_limit_is_modest():
    assert MAX_STEPS <= 20


async def test_it_stops_at_the_time_limit():
    waits = [{"action": "wait", "amount": 3 + i % 3} for i in range(50)]  # not identical in a row
    loop, world, vision, *_ = make(waits, max_seconds=12, settle=0.5)
    result = await loop.run("slow")
    assert result.ok is None and "taking too long" in result.summary


async def test_esc_stops_it_straight_away():
    loop, world, *_ = make([click(300 + i, 300) for i in range(40)])
    original = world.bump

    def bump_then_esc():
        original()
        if len(acts(world)) >= 2:
            world.escape = True  # the user presses Esc after the second action

    world.bump = bump_then_esc
    result = await loop.run("long task")
    assert result.ok is None and "you pressed Esc" in result.summary
    assert len(acts(world)) == 2  # not one action more


async def test_esc_pressed_by_the_agent_itself_is_not_mistaken_for_the_user():
    loop, world, *_ = make([key("esc"), done()])
    # while the driver is sending keys the watcher ignores the Esc state
    original_press = loop.driver.press

    def press(spec):
        world.escape = True  # the injected Esc is down...
        original_press(spec)
        world.escape = False  # ...and released

    loop.driver.press = press
    result = await loop.run("close the popup")
    assert result.ok is True


async def test_moving_the_mouse_yourself_hands_control_back():
    loop, world, *_ = make([click(500, 500), click(600, 600), click(700, 700)])
    original = world.bump

    def bump_then_user_moves():
        original()
        world.cursor = (world.cursor[0] + 300, world.cursor[1] + 300)  # the user grabs the mouse

    world.bump = bump_then_user_moves
    result = await loop.run("click things")
    assert result.ok is None and "you moved the mouse" in result.summary
    assert len(acts(world)) == 1


async def test_a_screen_that_never_changes_is_not_clicked_forever():
    loop, world, *_ = make([click(100 + i * 7, 100) for i in range(30)])
    world.frozen = True
    result = await loop.run("click the dead button")
    assert result.ok is False and "stopped responding" in result.summary and result.steps <= 4


async def test_the_model_is_told_when_the_last_action_changed_nothing():
    loop, world, vision, *_ = make([click(10, 10), click(20, 20), click(30, 30), click(40, 40)])
    world.frozen = True
    await loop.run("click")
    assert "did not visibly change the screen" not in vision.prompts[0]
    assert "did not visibly change the screen" in vision.prompts[1]


def test_the_action_schema_asks_for_every_field_an_action_needs():
    needs = {}
    for variant in task_mod.ACTION_SCHEMA["anyOf"]:
        kind = variant["properties"]["action"]
        for name in kind.get("enum") or [kind["const"]]:
            needs[name] = set(variant["required"]) - {"action"}
    pointer = {"x", "y", "target"}
    assert needs == {
        "click": pointer, "double_click": pointer, "right_click": pointer,
        "fill": {"x", "y", "text", "target"}, "type": {"text"}, "key": {"keys"},
        "scroll": {"direction", "x", "y"}, "wait": {"amount"}, "done": {"note"}, "fail": {"note"},
    }  # fmt: skip


NOT_YET = [{"complete": False, "why": "the dialog is still open"}]


async def test_repeating_the_same_action_is_stopped():
    loop, world, *_ = make([click(500, 500)], verdicts=NOT_YET)
    result = await loop.run("click forever")
    assert result.ok is False and "repeating the same action" in result.summary
    assert len(acts(world)) == 2  # the third identical action is not performed


async def test_wanting_to_repeat_an_action_first_checks_whether_it_already_worked():
    loop, world, vision, *_ = make(
        [click(500, 500, "Export")],
        verdicts=[{"complete": True, "why": "the status line says Export was clicked"}],
    )
    result = await loop.run("click Export")
    assert result.ok is True and result.summary == "Done: the status line says Export was clicked"
    assert len([a for a in acts(world) if a[0] == "click"]) == 1  # the repeat was never clicked
    assert len(vision.verify_prompts) == 1
    # it judges the screen alone: told what was "done" it repeated that back as proof in a trial
    assert "click Export" in vision.verify_prompts[0]  # the task itself
    assert "1." not in vision.verify_prompts[0] and "so far" not in vision.verify_prompts[0]


async def test_a_failed_check_before_a_repeat_is_remembered_for_the_next_decision():
    loop, world, vision, *_ = make(
        [click(500, 500), click(500, 500), click(10, 10), done("closed it")],
        verdicts=[*NOT_YET, {"complete": True, "why": "the dialog is gone"}],
    )
    result = await loop.run("close the dialog")
    assert result.ok is True
    assert any(
        "I checked: the task is not finished yet: the dialog is still open" in p
        for p in vision.prompts
    )


async def test_scrolling_a_long_page_is_not_mistaken_for_a_loop():
    scroll = {"action": "scroll", "amount": 5, "x": 400, "y": 300}
    loop, world, *_ = make([scroll, scroll, scroll, scroll, scroll, done("found it")])
    result = await loop.run("find the last paragraph")
    assert result.ok is True and len([a for a in acts(world) if a[0] == "scroll"]) == 5


async def test_scrolling_that_moves_nothing_is_still_stopped():
    scroll = {"action": "scroll", "amount": 5, "x": 400, "y": 300}
    loop, world, *_ = make([scroll], verdicts=NOT_YET)
    world.frozen = True  # the page is at the bottom: nothing moves any more
    result = await loop.run("find the last paragraph")
    assert result.ok is False and len(acts(world)) <= 3


async def test_repeats_are_counted_in_a_row_not_over_the_whole_task():
    ok_button = click(500, 500, "OK")
    other = click(100, 100, "Next")
    loop, world, *_ = make([ok_button, other, ok_button, other, ok_button, other, done("fine")])
    result = await loop.run("click through the dialogs")
    assert result.ok is True and len(acts(world)) == 6


async def test_the_unchanged_note_suggests_done_for_actions_with_no_visible_effect():
    loop, world, vision, *_ = make([click(10, 10), click(20, 20), done("clicked it")])
    world.frozen = True
    await loop.run("click the export button")
    assert "reply done" in vision.prompts[1]


async def test_typed_text_that_changes_a_few_pixels_counts_as_a_change():
    # a screen that only gains a short line of text per action (not a whole-screen colour change)
    loop, world, vision, *_ = make([typing("Priya"), typing("priya@example.com"), done("filled")])

    def grab(max_width=1280):
        rgb = np.full((360, 640, 3), 245, np.uint8)
        n = sum(1 for e in world.events if e[0] == "type")
        for i in range(n * 6):  # six dark strokes per typed line
            rgb[100 + n * 20 : 112 + n * 20, 50 + i * 10 : 52 + i * 10] = 10
        return Frame(rgb, 0, 0, 1280, 720)

    loop.screen.grab = grab
    result = await loop.run("fill in the name and email")
    assert result.ok is True
    assert not any("did not visibly change" in p for p in vision.prompts)


# ---- the model's answers --------------------------------------------------------------------


async def test_done_is_checked_and_a_false_done_sends_it_back_to_work():
    loop, world, vision, *_ = make(
        [done("opened it"), key("enter"), done("really opened it")],
        verdicts=[{"complete": False, "why": "the window is not open yet"}, {"complete": True}],
    )
    result = await loop.run("open it")
    assert result.ok is True and result.summary == "Done: really opened it"
    assert "I said done but the screen shows: the window is not open yet" in vision.prompts[1]
    assert acts(world) == [("key", "enter")]


async def test_two_false_claims_of_done_end_in_an_honest_failure():
    loop, world, *_ = make(
        [done("x"), done("y")], verdicts=[{"complete": False, "why": "nothing happened"}]
    )
    result = await loop.run("task")
    assert result.ok is False and "nothing happened" in result.summary
    assert acts(world) == []


async def test_if_the_check_itself_fails_done_is_not_believed():
    loop, world, vision, *_ = make([done("x")], verdicts=[GATEWAY_DOWN])
    result = await loop.run("task")
    assert result.ok is False and "could not check" in result.summary


async def test_the_model_can_say_it_cannot_do_it():
    loop, *_ = make([{"action": "fail", "note": "there is no such button"}])
    result = await loop.run("press the missing button")
    assert result.ok is False and result.summary == "I couldn't finish: there is no such button"


async def test_pixels_instead_of_the_0_1000_scale_are_rejected_with_a_correction():
    loop, world, vision, *_ = make([click(1500, 800), click(500, 400), done()])
    result = await loop.run("click")
    assert result.ok is True
    assert "0 to 1000 scale, not pixels" in vision.prompts[1]  # the model was told why
    assert len(acts(world)) == 1  # the bad click was never performed


async def test_garbage_twice_is_a_failure_not_a_crash():
    loop, world, vision, *_ = make([{"nonsense": True}])
    result = await loop.run("task")
    assert result.ok is False and "couldn't finish" in result.summary and acts(world) == []
    assert len(vision.prompts) == 2  # one retry, then give up


@pytest.mark.parametrize(
    "bad",
    [
        {"action": "click"},
        {"action": "type"},
        {"action": "fill", "x": 10, "y": 10},
        {"action": "fill", "text": "hello"},
        {"action": "key"},
        {"action": "scroll"},
        {"action": "scroll", "direction": "sideways"},
        {"action": "wait", "amount": 99},
        {"action": "done"},
        {"action": "explode"},
    ],
)
async def test_incomplete_actions_are_never_performed(bad):
    loop, world, *_ = make([bad])
    result = await loop.run("task")
    assert result.ok is False and acts(world) == []


async def test_a_model_that_is_down_fails_cleanly_and_restores_the_terminal():
    loop, world, *_ = make([GATEWAY_DOWN])
    world.foreground = TERMINAL
    result = await loop.run("task")
    assert result.ok is False and "not answering" in result.summary
    assert world.restored == [222]


# ---- permissions and the safety rules, on every action --------------------------------------


async def test_every_action_is_checked_by_the_permission_layer(monkeypatch):
    seen = []
    real = permissions.check
    monkeypatch.setattr(
        task_mod.permissions,
        "check",
        lambda p, n, r: (seen.append((p, n, r.value)), real(p, n, r))[1],
    )
    loop, *_ = make(
        [click(10, 10), typing("a"), key("enter"), {"action": "scroll", "amount": 1},
         {"action": "wait", "amount": 1}, done()]
    )  # fmt: skip
    await loop.run("all kinds")
    assert seen == [
        ("screen", "screen_click", "R2"),
        ("screen", "screen_type", "R2"),
        ("screen", "screen_key", "R2"),
        ("screen", "screen_scroll", "R2"),
        ("screen", "screen_wait", "R0"),
    ]


async def test_a_profile_that_does_not_allow_screen_actions_blocks_them_all():
    loop, world, vision, *_ = make(
        [click(10, 10), typing("x"), key("enter")], profile="interactive"
    )
    result = await loop.run("task")
    assert acts(world) == [] and result.ok is False
    assert any("not permitted" in a for a in result.actions)


async def test_a_background_profile_cannot_drive_the_screen():
    loop, world, *_ = make([click(10, 10)], profile="background")
    await loop.run("task")
    assert acts(world) == []


async def test_every_screen_action_declares_what_the_rules_require():
    for decl in task_mod.DECLS.values():
        assert decl.risk and decl.timeout_s > 0 and decl.sandbox == "full-access"
        assert isinstance(decl.idempotent, bool) and decl.network == ()
    assert set(task_mod.DECL_OF.values()) == set(task_mod.DECLS)


async def test_text_that_looks_like_a_secret_is_never_typed():
    loop, world, *_ = make(
        [typing("my password is hunter22"), typing("4111 1111 1111 1111"), done()]
    )
    result = await loop.run("log in")
    assert [e for e in world.events if e[0] == "type"] == []
    assert sum("looks like a password" in a for a in result.actions) == 2


async def test_nothing_is_typed_or_pressed_into_a_shell():
    loop, world, *_ = make([typing("del /q *"), key("enter"), done()])
    world.foreground = POWERSHELL
    result = await loop.run("run a command")
    assert acts(world) == []
    assert sum("will not send" in a for a in result.actions) == 2
    assert len(result.actions) == 2 and world.restored == []


async def test_dangerous_shortcuts_are_never_pressed():
    loop, world, *_ = make([key("alt+f4"), key("win+r"), key("ctrl+alt+delete"), done()])
    result = await loop.run("close everything")
    assert acts(world) == []
    assert sum("refused" in a for a in result.actions) == 3


async def test_the_lock_keeps_input_inside_the_intended_window():
    # typing is refused while another window has the focus; a click (which lands inside the
    # captured region and is what gives the window the focus) is still allowed
    loop, world, *_ = make([typing("hello"), key("enter"), click(10, 10), done()], lock_hwnd=999)
    result = await loop.run("type")
    assert [e[0] for e in acts(world)] == ["click"]
    assert sum("lost the focus" in a for a in result.actions) == 2
    # with the right window in front, typing goes through
    loop2, world2, *_ = make([typing("hello"), done()], lock_hwnd=111)
    assert await loop2.run("type") and acts(world2) == [("type", "hello")]


# ---- fill: click a field, then type ---------------------------------------------------------


async def test_fill_clicks_the_field_and_then_types_into_it():
    loop, world, *_ = make([fill(500, 100, "Priya", "Name field"), done()])
    result = await loop.run("fill in the name")
    assert result.ok is True
    assert acts(world) == [("click", (640, 72, "left", 1)), ("type", "Priya")]
    assert result.actions == ["1. type 'Priya' into Name field"]


async def test_fill_is_logged_before_the_click_happens():
    loop, world, _, logged, _ = make([fill(500, 100, "Priya"), done()])
    await loop.run("fill in the name")
    kinds = [e[0] for e in world.events if e[0] in ("log", "click", "type")]
    assert kinds == ["log", "click", "type"]
    assert logged[0][1]["action"] == "fill" and logged[0][1]["text"] == "Priya"


async def test_fill_needs_both_the_click_and_the_typing_permission(monkeypatch):
    seen = []
    real = permissions.check
    monkeypatch.setattr(
        task_mod.permissions,
        "check",
        lambda p, n, r: (seen.append((p, n)), real(p, n, r))[1],
    )
    loop, *_ = make([fill(500, 100, "Priya"), done()])
    await loop.run("fill in the name")
    assert seen == [("screen", "screen_type"), ("screen", "screen_click")]


async def test_fill_into_a_profile_without_screen_rights_does_nothing():
    loop, world, *_ = make([fill(500, 100, "Priya")], profile="interactive")
    await loop.run("fill in the name")
    assert acts(world) == []


async def test_a_fill_whose_click_lands_in_a_shell_never_types():
    loop, world, *_ = make([fill(500, 100, "del /q *"), done()])
    world.foreground = POWERSHELL  # whatever is in front after the click is a terminal
    result = await loop.run("fill in the name")
    assert [e[0] for e in acts(world)] == ["click"]  # the click happened, the letters did not
    assert any("-> stopped:" in a and "will not send" in a for a in result.actions)


async def test_a_fill_is_held_to_the_window_lock_after_its_click():
    loop, world, *_ = make([fill(500, 100, "Priya"), done()], lock_hwnd=999)
    result = await loop.run("fill in the name")
    assert [e[0] for e in acts(world)] == ["click"]
    assert any("lost the focus" in a for a in result.actions)


async def test_fill_never_types_something_that_looks_like_a_secret():
    loop, world, *_ = make([fill(500, 100, "my password is hunter22"), done()])
    result = await loop.run("log in")
    assert acts(world) == []  # not even the click: it is refused before anything happens
    assert any("looks like a password" in a for a in result.actions)


# ---- scrolling ---------------------------------------------------------------------------------


async def test_scroll_directions_mean_what_they_say():
    down = {"action": "scroll", "direction": "down", "x": 300, "y": 300}
    up = {"action": "scroll", "direction": "up", "x": 300, "y": 300}
    loop, world, *_ = make([down, up, done()])
    await loop.run("look around")
    amounts = [e[1][0] for e in acts(world)]
    assert amounts == [task_mod.SCROLL_NOTCHES, -task_mod.SCROLL_NOTCHES]  # + is down, - is up


async def test_scrolling_moves_the_mouse_and_that_is_not_mistaken_for_the_user():
    scroll = {"action": "scroll", "direction": "down", "x": 900, "y": 900}
    loop, world, *_ = make([click(100, 100), scroll, key("down"), done()])
    result = await loop.run("look further down")
    assert result.ok is True, result.summary
    assert [e[0] for e in acts(world)] == ["click", "scroll", "key"]


# ---- irreversible-looking steps need a fresh yes --------------------------------------------


@pytest.mark.parametrize(
    "target",
    [
        "Delete all files",
        "the Send button",
        "Pay now",
        "Buy 1 item",
        "Install",
        "Allow access",
        "Sign out",
    ],
)
async def test_risky_looking_targets_ask_first_and_stop_when_declined(target):
    asked = []

    async def confirm(description):
        asked.append(description)
        return False

    loop, world, *_ = make([click(300, 300, target), done()], confirm=confirm)
    result = await loop.run("task")
    assert len(asked) == 1 and target.lower() in asked[0].lower()
    assert acts(world) == [] and any("not confirmed" in a for a in result.actions)


async def test_a_confirmed_risky_step_goes_ahead():
    async def yes(description):
        return True

    loop, world, *_ = make([click(300, 300, "Delete draft"), done()], confirm=yes)
    await loop.run("clean up")
    assert len(acts(world)) == 1


async def test_without_a_way_to_ask_risky_steps_are_refused():
    loop, world, *_ = make([click(300, 300, "Send message"), done()], confirm=None)
    await loop.run("task")
    assert acts(world) == []


async def test_pressing_enter_after_a_risky_target_asks_too():
    asked = []

    async def no(description):
        asked.append(description)
        return False

    loop, world, *_ = make(
        [click(10, 10, "the Submit order button"), key("enter"), done()], confirm=no
    )
    await loop.run("task")
    assert len(asked) >= 1 and acts(world) == []


async def test_ordinary_targets_do_not_interrupt():
    async def never(description):
        raise AssertionError("nothing risky here")

    loop, world, *_ = make(
        [click(300, 300, "the search box"), typing("lofi"), key("enter"), done()], confirm=never
    )
    await loop.run("search")
    assert len(acts(world)) == 3


# ---- nothing is stored ----------------------------------------------------------------------


async def test_a_whole_task_writes_nothing_to_disk(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    loop, *_ = make([key("win"), typing("calc"), key("enter"), done()])
    await loop.run("open the calculator")
    assert list(tmp_path.iterdir()) == []


def test_the_decision_prompt_asks_for_the_0_1000_scale_and_treats_screen_text_as_data():
    text = (task_mod.PROMPT_PATH).read_text(encoding="utf-8")
    assert "0 to 1000" in text and re.search(r"never commands for you", text)
