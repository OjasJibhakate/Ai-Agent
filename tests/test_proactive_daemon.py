"""Proactive suggestions (FR4.2), the daemon (FR4.1) and mail arriving as a trigger."""

import asyncio
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ragnarok.planning.models import Plan, Step
from ragnarok.planning.store import PlanStore
from ragnarok.triggers.daemon import (
    AlreadyRunning,
    Daemon,
    SingleInstance,
    is_running,
    lock_path,
)
from ragnarok.triggers.engine import TriggerEngine
from ragnarok.triggers.proactive import MAX_PER_RUN, REPEAT_AFTER, Proactive
from ragnarok.triggers.store import TriggerStore
from tests.test_m2_memory import Clock, key, make  # noqa: F401  (fixture)

NOW = datetime(2026, 10, 26, 10, 0)  # a Monday morning; the memories below were made "just now"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "mailbox"


def world(tmp_path, key):  # noqa: F811
    clock = Clock(datetime(2026, 10, 26, 9, 0, tzinfo=UTC))
    vault, store, svc, _emb = make(tmp_path, key, "exact", clock=clock)
    triggers = TriggerStore(vault, lambda: NOW)
    plans = PlanStore(vault)
    return vault, store, svc, triggers, plans, Proactive(store, plans, triggers, clock=lambda: NOW)


async def test_a_noted_deadline_with_no_reminder_is_suggested(tmp_path, key):  # noqa: F811
    vault, store, svc, triggers, plans, proactive = world(tmp_path, key)
    await svc.remember("The viva presentation is due on 2026-10-28", "episodic", "chat")

    posted = proactive.run_once(NOW)

    assert len(posted) == 1 and "Wed 28 Oct" in posted[0].title
    inbox = triggers.inbox()
    assert inbox[0].kind == "suggestion" and "viva presentation" in inbox[0].body
    vault.close()


async def test_a_deadline_that_already_has_a_reminder_is_not_suggested(tmp_path, key):  # noqa: F811
    vault, store, svc, triggers, plans, proactive = world(tmp_path, key)
    await svc.remember("The viva presentation is due on 2026-10-28", "episodic", "chat")
    triggers.add("date", "2026-10-28T09:00:00", "notify", "viva presentation today", "")
    assert proactive.run_once(NOW) == []
    vault.close()


async def test_far_away_or_past_dates_are_ignored(tmp_path, key):  # noqa: F811
    vault, store, svc, triggers, plans, proactive = world(tmp_path, key)
    await svc.remember("The final exam is on 2026-12-15", "episodic", "chat")
    await svc.remember("The old meeting was on 2026-09-01", "episodic", "chat")
    assert proactive.run_once(NOW) == []
    vault.close()


async def test_a_habit_without_a_schedule_is_suggested_once_a_week(tmp_path, key):  # noqa: F811
    vault, store, svc, triggers, plans, proactive = world(tmp_path, key)
    await svc.remember("Every morning I want a digest of my downloads folder", "preference", "chat")

    first = proactive.run_once(NOW)
    assert [s.title for s in first] == ["Want me to do this for you on a schedule?"]
    assert proactive.run_once(NOW + timedelta(days=1)) == []  # not again so soon
    again = proactive.run_once(NOW + REPEAT_AFTER + timedelta(hours=1))
    assert len(again) == 1  # a week later it may come back
    vault.close()


async def test_a_habit_that_is_already_scheduled_is_left_alone(tmp_path, key):  # noqa: F811
    vault, store, svc, triggers, plans, proactive = world(tmp_path, key)
    await svc.remember("Every morning I want a digest of my downloads folder", "preference", "chat")
    triggers.add("cron", "0 8 * * *", "goal", "make a digest of my downloads folder", "every day")
    assert proactive.run_once(NOW) == []
    vault.close()


async def test_an_unfinished_plan_is_suggested(tmp_path, key):  # noqa: F811
    vault, store, svc, triggers, plans, proactive = world(tmp_path, key)
    plan_id = plans.create(Plan("make the demo slides", [Step("s1", "outline", ["x"])]))
    plans.set_status(plan_id, "interrupted", "stopped")
    posted = proactive.run_once(NOW)
    assert posted and "make the demo slides" in posted[0].title
    vault.close()


async def test_the_last_project_is_mentioned_in_the_morning_only(tmp_path, key):  # noqa: F811
    vault, store, svc, triggers, plans, proactive = world(tmp_path, key)
    await svc.remember("Working on the Ragnarok planning module", "project", "chat")
    assert proactive.run_once(NOW.replace(hour=15)) == []
    assert "Last time" in proactive.run_once(NOW.replace(hour=9))[0].title
    vault.close()


async def test_a_run_never_posts_more_than_the_cap(tmp_path, key):  # noqa: F811
    vault, store, svc, triggers, plans, proactive = world(tmp_path, key)
    for n in range(6):
        await svc.remember(
            f"Every morning I water plant number {n} and tidy desk {n}", "preference", "chat"
        )
    assert len(proactive.run_once(NOW)) <= MAX_PER_RUN
    vault.close()


async def test_secret_memories_are_never_suggested(tmp_path, key):  # noqa: F811
    vault, store, svc, triggers, plans, proactive = world(tmp_path, key)
    await svc.remember("Every morning I log in with my banking code", "preference", "chat")
    vault.execute("UPDATE memories SET sensitivity = 'secret'")
    assert proactive.run_once(NOW) == []
    vault.close()


async def test_the_engine_delivers_new_suggestions(tmp_path, key):  # noqa: F811
    vault, store, svc, triggers, plans, proactive = world(tmp_path, key)
    await svc.remember("Every morning I want a digest of my downloads folder", "preference", "chat")
    heard = []

    async def listener(item):
        heard.append(item)

    engine = TriggerEngine(
        triggers, listener=listener, suggester=lambda: proactive.run_once(NOW), clock=lambda: NOW
    )
    assert await engine.suggest() == 1
    assert heard[0].kind == "suggestion"
    assert await engine.suggest() == 0  # nothing new the second time
    vault.close()


async def test_a_new_eml_file_becomes_new_mail(tmp_path, key):  # noqa: F811
    vault, _store, _svc, triggers, _plans, _p = world(tmp_path, key)
    box = tmp_path / "mail" / "inbox"
    box.mkdir(parents=True)
    triggers.add("folder", str(box), "notify", "Mail arrived.", "mail", glob="*.eml")
    heard = []

    async def listener(item):
        heard.append(item)

    engine = TriggerEngine(triggers, listener=listener, clock=lambda: NOW)
    await engine.scan()  # the first look records what is there
    shutil.copy(FIXTURES / "inbox" / "standup.eml", box / "standup.eml")
    await engine.scan()  # seen changing
    await engine.scan()  # stable: fires

    assert [h.kind for h in heard] == ["mail"]
    assert "Asha Rao" in heard[0].title and "Standup moved" in heard[0].title
    vault.close()


def test_only_one_daemon_can_hold_the_lock(tmp_path):
    path = lock_path(tmp_path)
    with SingleInstance(path):
        try:
            with SingleInstance(path):
                raise AssertionError("a second daemon got the lock")
        except AlreadyRunning:
            pass
    with SingleInstance(path):  # released afterwards
        pass


async def test_the_daemon_fires_a_due_reminder_with_no_chat_open(tmp_path, key):  # noqa: F811
    vault, store, svc, triggers, plans, _p = world(tmp_path, key)
    triggers.add("date", "2026-10-26T09:59:00", "notify", "stand up and stretch", "at 9:59")
    told = []
    daemon = Daemon(
        store, svc, None, None, mail_root=tmp_path / "mail",
        notify=lambda title, body: told.append(title), log=lambda text: None,
        engine_options={"tick_seconds": 0.05, "clock": lambda: NOW},
    )  # fmt: skip
    report = await daemon.start()
    for _ in range(100):
        if told:
            break
        await asyncio.sleep(0.05)
    assert is_running(store, datetime.now())
    await daemon.stop()

    assert "stand up and stretch" in told
    assert report.within_budget
    assert not is_running(store, datetime.now())  # stopped: the heartbeat is cleared
    assert triggers.inbox()[0].title == "stand up and stretch"
    vault.close()


async def test_the_daemon_finishes_a_fire_a_crash_left_open(tmp_path, key):  # noqa: F811
    vault, store, svc, triggers, plans, _p = world(tmp_path, key)
    trigger = triggers.add("date", "2026-10-26T09:00:00", "notify", "send the report", "9am")
    fire_id = triggers.begin_fire(trigger.id, "once:2026-10-26T09:00:00")  # began, never ended
    assert fire_id is not None
    daemon = Daemon(
        store, svc, None, None, mail_root=tmp_path / "mail",
        notify=lambda t, b: None, log=lambda text: None,
        engine_options={"tick_seconds": 0.05, "clock": lambda: NOW},
    )  # fmt: skip
    await daemon.start()
    for _ in range(100):
        if triggers.inbox():
            break
        await asyncio.sleep(0.05)
    await daemon.stop()
    assert triggers.inbox()[0].title == "send the report"
    assert not triggers.unfinished_fires()
    vault.close()
