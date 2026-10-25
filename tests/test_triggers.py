"""The trigger engine: reminders, schedules and folder watches fire once, survive a restart, and
report instead of crashing."""

import asyncio
import os
import time
from datetime import datetime, timedelta

import pytest

from ragnarok.policy import permissions
from ragnarok.triggers.engine import MAX_NEW_FILES, TriggerEngine, scan_folder
from ragnarok.triggers.store import TriggerStore
from ragnarok.triggers.tools import (
    CancelArgs,
    ListArgs,
    ScheduleTaskArgs,
    SetReminderArgs,
    WatchFolderArgs,
    trigger_tools,
)
from tests.test_m2_memory import key, make  # noqa: F401  (fixture)

MONDAY_7AM = datetime(2026, 10, 26, 7, 0, 0)


class Clock:
    def __init__(self, now: datetime = MONDAY_7AM) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta) -> None:
        self.now += timedelta(**delta)


@pytest.fixture
def world(tmp_path, key):  # noqa: F811
    vault, _store, _svc, _emb = make(tmp_path, key, "exact")
    clock = Clock()
    store = TriggerStore(vault, clock)
    yield store, clock, vault
    vault.close()


def engine_for(store, clock, **kw) -> TriggerEngine:
    return TriggerEngine(store, clock=clock, **kw)


def titles(store) -> list[str]:
    return [i.title for i in reversed(store.inbox())]


# ---- reminders -----------------------------------------------------------------------------------


async def test_a_reminder_fires_once_when_it_is_due(world):
    store, clock, _ = world
    got = []

    async def listener(item):
        got.append(item)

    engine = engine_for(store, clock, listener=listener)
    t = store.add("date", "2026-10-26 07:05:00", "notify", "call mom", "today at 7:05 AM")
    assert await engine.tick() == 0 and not store.inbox()  # not yet
    clock.advance(minutes=6)
    assert await engine.tick() == 1
    assert titles(store) == ["call mom"] and store.get(t.id).status == "done"
    assert [(g.kind, g.title) for g in got] == [("reminder", "call mom")]
    assert "Set for today at 7:05 AM" in got[0].body and "not running" not in got[0].body
    assert await engine.tick() == 0 and len(store.inbox()) == 1  # never twice


async def test_a_reminder_that_came_due_while_closed_says_so(world):
    store, clock, _ = world
    store.add("date", "2026-10-26 07:05:00", "notify", "submit the form", "today at 7:05 AM")
    clock.advance(hours=3)
    await engine_for(store, clock).tick()
    (item,) = store.inbox()
    assert "I was not running then" in item.body and "7:05 AM" in item.body


async def test_a_crash_between_the_fire_record_and_the_message_loses_nothing(world):
    store, clock, _ = world
    t = store.add("date", "2026-10-26 07:05:00", "notify", "pay rent", "today at 7:05 AM")
    clock.advance(minutes=10)
    assert store.begin_fire(t.id, f"once:{t.spec}") is not None  # ...and the program dies here
    assert not store.inbox() and len(store.unfinished_fires()) == 1

    engine = engine_for(store, clock)  # the next start
    assert await engine.recover() == 1
    assert titles(store) == ["pay rent"] and not store.unfinished_fires()
    assert store.get(t.id).status == "done"
    assert await engine.tick() == 0 and len(store.inbox()) == 1  # and it is not repeated


async def test_a_task_that_was_running_at_the_crash_is_reported_not_run_again(world):
    store, clock, _ = world
    calls = []

    async def runner(goal, event):
        calls.append(goal)
        return "x"

    t = store.add("date", "2026-10-26 07:05:00", "goal", "tidy my downloads", "today")
    clock.advance(minutes=10)
    store.begin_fire(t.id, f"once:{t.spec}")
    engine = engine_for(store, clock, goal_runner=runner)
    await engine.recover()
    (item,) = store.inbox()
    assert item.kind == "failed" and item.title.startswith("Interrupted: tidy my downloads")
    assert calls == []  # an action with side effects is never re-run blindly


# ---- schedules -----------------------------------------------------------------------------------


async def test_a_weekday_schedule_fires_at_its_time_and_moves_on(world):
    store, clock, _ = world
    store.add("cron", "0 8 * * 1-5", "notify", "morning brief", "every weekday at 8:00 AM")
    engine = engine_for(store, clock)
    assert await engine.tick() == 0  # 07:00, set up for 08:00
    clock.advance(hours=1, seconds=5)
    assert await engine.tick() == 1 and await engine.tick() == 0
    clock.advance(days=1)  # Tuesday 08:00:05
    assert await engine.tick() == 1
    assert titles(store) == ["morning brief", "morning brief"]


async def test_missed_slots_are_coalesced_into_one_late_fire(world):
    store, clock, _ = world
    store.add("cron", "0 8 * * 1-5", "notify", "morning brief", "every weekday at 8:00 AM")
    engine = engine_for(store, clock)
    await engine.tick()
    clock.now = datetime(2026, 10, 28, 12, 0)  # Wednesday noon: Mon, Tue and Wed were missed
    assert await engine.tick() == 1
    (item,) = store.inbox()
    assert "I was not running then" in item.body and "Wed 28 Oct at 8:00 AM" in item.body
    assert await engine.tick() == 0
    clock.now = datetime(2026, 10, 29, 8, 0, 1)  # and the next one is the normal Thursday slot
    assert await engine.tick() == 1


async def test_a_schedule_survives_a_restart_without_repeating(world):
    store, clock, vault = world
    store.add("cron", "0 8 * * *", "notify", "stretch", "every day at 8:00 AM")
    await engine_for(store, clock).tick()
    clock.advance(hours=1)
    await engine_for(store, clock).tick()
    again = TriggerStore(vault, clock)  # a fresh program on the same vault
    await engine_for(again, clock).tick()
    assert titles(store) == ["stretch"]


# ---- folders -------------------------------------------------------------------------------------


def touch(folder, name, text="x", size=None):
    path = folder / name
    path.write_text(text * (size or 1), encoding="utf-8")
    return path


async def test_new_files_fire_after_they_stop_changing_and_old_ones_do_not(world, tmp_path):
    store, clock, _ = world
    watched = tmp_path / "Downloads"
    watched.mkdir()
    touch(watched, "old.pdf")
    store.add("folder", str(watched), "notify", "Check it", "", glob="*.pdf")
    engine = engine_for(store, clock)
    assert await engine.scan() == 0  # the first look only records what is there
    touch(watched, "invoice.pdf")
    touch(watched, "photo.png")  # not matching the glob
    assert await engine.scan() == 0  # seen once: it might still be written
    assert await engine.scan() == 1  # unchanged on the second look: it is done
    assert await engine.scan() == 0
    (item,) = store.inbox()
    assert item.kind == "file" and item.title == "New file: invoice.pdf"
    assert str(watched / "invoice.pdf") in item.body


async def test_a_file_still_being_written_is_not_picked_up_half_done(world, tmp_path):
    store, clock, _ = world
    watched = tmp_path / "in"
    watched.mkdir()
    store.add("folder", str(watched), "notify", "x", "")
    engine = engine_for(store, clock)
    await engine.scan()
    path = touch(watched, "big.zip", "a", 10)
    await engine.scan()
    path.write_text("a" * 500, encoding="utf-8")  # grew since the last look
    assert await engine.scan() == 0
    assert await engine.scan() == 1
    assert titles(store) == ["New file: big.zip"]


async def test_temporary_and_partial_files_are_ignored(world, tmp_path):
    store, clock, _ = world
    watched = tmp_path / "in"
    watched.mkdir()
    store.add("folder", str(watched), "notify", "x", "")
    engine = engine_for(store, clock)
    await engine.scan()
    for name in ("~$lock.docx", "movie.mp4.crdownload", "a.tmp", "b.part", "Thumbs.db"):
        touch(watched, name)
    (watched / "subdir").mkdir()
    assert await engine.scan() == 0 and await engine.scan() == 0
    assert not store.inbox()


async def test_a_changed_file_fires_again_but_the_same_version_never_does(world, tmp_path):
    store, clock, _ = world
    watched = tmp_path / "in"
    watched.mkdir()
    store.add("folder", str(watched), "notify", "x", "")
    engine = engine_for(store, clock)
    await engine.scan()
    path = touch(watched, "notes.txt", "one")
    await engine.scan()
    await engine.scan()
    path.write_text("two two", encoding="utf-8")
    os.utime(path, ns=(2_000_000_000_000_000_000, 2_000_000_000_000_000_000))
    await engine.scan()
    await engine.scan()
    assert len(store.inbox()) == 2
    await engine.scan()
    assert len(store.inbox()) == 2


async def test_files_that_arrived_while_closed_are_found_at_the_next_start(world, tmp_path):
    store, clock, vault = world
    watched = tmp_path / "in"
    watched.mkdir()
    store.add("folder", str(watched), "notify", "x", "")
    await engine_for(store, clock).scan()  # baseline, then the program is closed
    touch(watched, "while_away.pdf")
    reopened = engine_for(TriggerStore(vault, clock), clock)
    await reopened.scan()
    await reopened.scan()
    assert titles(store) == ["New file: while_away.pdf"]


async def test_a_flood_of_files_is_paced(world, tmp_path):
    store, clock, _ = world
    watched = tmp_path / "in"
    watched.mkdir()
    store.add("folder", str(watched), "notify", "x", "")
    engine = engine_for(store, clock)
    await engine.scan()
    for i in range(MAX_NEW_FILES + 15):
        touch(watched, f"f{i:03}.txt")
    await engine.scan()
    assert await engine.scan() == MAX_NEW_FILES  # the rest wait for the next round
    assert await engine.scan() == 15
    assert len(store.inbox(limit=100)) == MAX_NEW_FILES + 15


def test_scan_folder_reports_size_and_time_and_skips_unreadable_places(tmp_path):
    touch(tmp_path, "a.txt", "abc")
    found = scan_folder(str(tmp_path), "*.txt")
    assert found["a.txt"][0] == 3
    assert scan_folder(str(tmp_path / "nope"), "*") == {}


# ---- goals ---------------------------------------------------------------------------------------


async def test_a_goal_trigger_runs_the_goal_and_posts_the_result(world, tmp_path):
    store, clock, _ = world
    seen = []

    async def runner(goal, event):
        seen.append((goal, event.get("path")))
        return "Summary: 3 pages about convolution."

    watched = tmp_path / "in"
    watched.mkdir()
    store.add("folder", str(watched), "goal", "Summarize $path", "")
    engine = engine_for(store, clock, goal_runner=runner)
    await engine.scan()
    touch(watched, "paper.txt")
    await engine.scan()
    await engine.scan()
    assert seen == [(f"Summarize {watched / 'paper.txt'}", str(watched / "paper.txt"))]
    (item,) = store.inbox()
    assert item.kind == "result" and "convolution" in item.body


async def test_a_goal_that_fails_is_reported_and_the_engine_keeps_going(world):
    store, clock, _ = world

    async def runner(goal, event):
        raise RuntimeError("model fell over")

    store.add("date", "2026-10-26 07:05:00", "goal", "brief me", "today")
    store.add("date", "2026-10-26 07:06:00", "notify", "still works", "today")
    clock.advance(minutes=10)
    engine = engine_for(store, clock, goal_runner=runner)
    assert await engine.tick() == 2
    kinds = {i.title: i.kind for i in store.inbox()}
    assert kinds["Could not finish: brief me"] == "failed" and kinds["still works"] == "reminder"
    assert "RuntimeError" in next(i.body for i in store.inbox() if i.kind == "failed")


async def test_a_goal_waits_while_the_user_is_in_the_middle_of_a_turn(world):
    store, clock, _ = world
    busy = {"now": True}
    started = []

    async def runner(goal, event):
        started.append(time.monotonic())
        return "ok"

    store.add("date", "2026-10-26 07:05:00", "goal", "brief me", "today")
    clock.advance(minutes=10)
    engine = engine_for(store, clock, goal_runner=runner, idle=lambda: not busy["now"])
    task = asyncio.create_task(engine.tick())
    await asyncio.sleep(0.8)
    assert started == [] and not task.done()  # held back
    busy["now"] = False
    assert await asyncio.wait_for(task, 3) == 1 and len(started) == 1


# ---- running in the background -------------------------------------------------------------------


async def test_the_background_loop_delivers_and_stops_cleanly(world):
    store, clock, _ = world
    got = asyncio.Queue()

    async def listener(item):
        await got.put(item)

    store.add("date", "2026-10-26 06:00:00", "notify", "already due", "earlier")
    engine = engine_for(store, clock, listener=listener, tick_seconds=0.01, folder_seconds=0.01)
    engine.start()
    item = await asyncio.wait_for(got.get(), 2)
    assert item.title == "already due"
    engine.paused = True
    store.add("date", "2026-10-26 06:00:00", "notify", "while paused", "earlier")
    await asyncio.sleep(0.1)
    assert got.empty()  # "pause all" holds events back
    engine.paused = False
    assert (await asyncio.wait_for(got.get(), 2)).title == "while paused"
    await engine.stop()
    await engine.stop()  # stopping twice is fine


# ---- the tools -----------------------------------------------------------------------------------


@pytest.fixture
def tools(world, tmp_path):
    store, clock, _ = world
    home = tmp_path / "home"
    (home / "Downloads").mkdir(parents=True)
    return {t.name: t for t in trigger_tools(store, [home], home, clock=clock)}, store, home


async def text_of(result) -> str:
    return result if isinstance(result, str) else result.text


async def test_set_reminder_understands_the_users_words_and_stores_the_time(tools):
    by_name, store, _ = tools
    out = await text_of(
        await by_name["set_reminder"].handler(SetReminderArgs(what="call mom", when="at 6pm"))
    )
    assert "remind you to call mom today at 6:00 PM" in out and "(#1)" in out
    assert "only do this while Ragnarok is open" in out  # honest about what it can do
    (t,) = store.list()
    assert (t.kind, t.spec, t.action) == ("date", "2026-10-26 18:00:00", "notify")


async def test_set_reminder_repeats_and_rejects_what_it_cannot_parse(tools):
    by_name, store, _ = tools
    await by_name["set_reminder"].handler(
        SetReminderArgs(what="stretch", when="every weekday at 8")
    )
    assert [(t.kind, t.spec) for t in store.list()] == [("cron", "0 8 * * 1-5")]
    bad = await text_of(
        await by_name["set_reminder"].handler(SetReminderArgs(what="x y", when="whenever"))
    )
    assert bad.startswith("error:") and len(store.list()) == 1
    past = await text_of(
        await by_name["set_reminder"].handler(SetReminderArgs(what="x y", when="yesterday at 5"))
    )
    assert past.startswith("error:") and "past" in past


async def test_schedule_task_and_watch_folder_create_goal_triggers(tools):
    by_name, store, home = tools
    await by_name["schedule_task"].handler(
        ScheduleTaskArgs(goal="brief me on today", when="every weekday at 8")
    )
    out = await text_of(
        await by_name["watch_folder"].handler(
            WatchFolderArgs(folder="Downloads", pattern="*invoice*.pdf", goal="File $path")
        )
    )
    assert "work on new files matching *invoice*.pdf" in out
    kinds = {t.kind: t for t in store.list()}
    assert kinds["cron"].action == "goal" and kinds["folder"].action == "goal"
    assert kinds["folder"].spec == str((home / "Downloads").resolve()) and kinds["folder"].glob


async def test_watch_folder_refuses_places_outside_the_allowed_folders(tools):
    by_name, store, _ = tools
    for folder in ("C:/Windows", "Downloads/does_not_exist"):
        out = await text_of(await by_name["watch_folder"].handler(WatchFolderArgs(folder=folder)))
        assert out.startswith("error:")
    assert store.list() == []


async def test_list_and_cancel(tools):
    by_name, store, _ = tools
    assert "No active" in await text_of(await by_name["list_reminders"].handler(ListArgs()))
    await by_name["set_reminder"].handler(SetReminderArgs(what="call mom", when="at 6pm"))
    listing = await text_of(await by_name["list_reminders"].handler(ListArgs()))
    assert "#1 reminder: call mom" in listing and "today at 6:00 PM" in listing
    assert "Cancelled #1" in await text_of(
        await by_name["cancel_reminder"].handler(CancelArgs(id=1))
    )
    assert (await text_of(await by_name["cancel_reminder"].handler(CancelArgs(id=1)))).startswith(
        "error"
    )
    assert store.list() == []


def test_the_permissions_match_what_each_tool_does_later_unattended(tools):
    by_name, _, _ = tools
    expected = {"set_reminder": False, "cancel_reminder": False, "list_reminders": False,
                "schedule_task": True, "watch_folder": True}  # fmt: skip
    for name, ask in expected.items():
        tool = by_name[name]
        decision = permissions.check("interactive", tool.name, tool.risk)
        assert decision.ask is ask and (decision.allowed is not ask)
        assert len(tool.description.split(". ")) <= 2
        assert tool.spec()["function"]["parameters"]["type"] == "object"
    assert by_name["watch_folder"].approval_note and by_name["schedule_task"].approval_note
