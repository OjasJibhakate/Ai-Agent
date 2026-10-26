"""The always-on background process (FR4.1): `rag daemon`.

Without it, reminders, schedules and watched folders only fire while the chat or the cat is open.
The daemon is the same trigger engine, run on its own with no window:

- it restores unfinished work first (recovery/restore.py) and finishes fires a crash left open;
- reminders and schedules fire on time, watched folders and mailboxes are polled, and goals run
  in the read-only `background` profile: anything that would change something waits in the Inbox;
- new Inbox items also become Windows notifications;
- suggestions (FR4.2) are looked for every half hour while nothing else is happening.

Only one daemon runs at a time (a lock file). If the chat is open as well, both run the engine and
that is safe: every fire has a unique key in the vault, so an event is handled once, by whichever
process gets there first.

It sleeps almost all the time: the loop wakes once a second, does a few small vault queries and
scans watched folders every two seconds in a worker thread (measured by `bench/resource_bench.py`).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from ragnarok.memory.service import MemoryService
from ragnarok.memory.store import MemoryStore
from ragnarok.planning.store import PlanStore
from ragnarok.policy.modes import Mode
from ragnarok.recovery.restore import RestoreReport, restore_state
from ragnarok.runtime.gateway import Gateway
from ragnarok.runtime.loop import Agent, Session
from ragnarok.runtime.tools import Tool, memory_tools
from ragnarok.skills.files import file_tools
from ragnarok.skills.mail import mail_tools
from ragnarok.skills.tasks import TaskBoard, task_tools
from ragnarok.triggers.engine import Delivered, TriggerEngine
from ragnarok.triggers.proactive import Proactive
from ragnarok.triggers.store import TriggerStore
from ragnarok.triggers.toast import toast

HEARTBEAT_KEY = "daemon_heartbeat"
HEARTBEAT_EVERY = 30.0
STALE_AFTER = 90.0


class AlreadyRunning(Exception):
    pass


class SingleInstance:
    """A lock file only one process can hold; the OS frees it when the process ends, even killed."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle = None

    def __enter__(self) -> SingleInstance:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+b")
        try:
            if sys.platform == "win32":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise AlreadyRunning("the daemon is already running") from None
        self._handle = handle
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._handle is not None:
            with contextlib.suppress(OSError):
                self._handle.close()
            self._handle = None


def is_running(store: MemoryStore, now: datetime | None = None) -> bool:
    """Is a daemon alive? It writes a heartbeat to the vault every 30 seconds."""
    stamp = store.meta_get(HEARTBEAT_KEY)
    if not stamp:
        return False
    age = ((now or datetime.now()) - datetime.fromisoformat(stamp)).total_seconds()
    return age < STALE_AFTER


def daemon_tools(store: MemoryStore, svc: MemoryService, ingestor, mail_root: Path) -> list[Tool]:
    """What an unattended goal may use; the `background` profile refuses every change."""
    mode = Mode("ask")
    return [
        *memory_tools(svc, ingestor),
        *file_tools(mode=mode),
        *mail_tools(mail_root),
        *task_tools(TaskBoard(store.vault)),
    ]


class Daemon:
    def __init__(
        self,
        store: MemoryStore,
        svc: MemoryService,
        ingestor,
        gateway: Gateway,
        *,
        mail_root: Path,
        notify: Callable[[str, str], object] = toast,
        log: Callable[[str], None] = print,
        engine_options: dict | None = None,
    ) -> None:
        self.store = store
        self.log = log
        self.notify = notify
        self.triggers = TriggerStore(store.vault)
        self.plans = PlanStore(store.vault)
        self.proactive = Proactive(store, self.plans, self.triggers)
        self.tools = daemon_tools(store, svc, ingestor, mail_root)
        self.svc, self.ingestor, self.gateway = svc, ingestor, gateway
        self.engine = TriggerEngine(
            self.triggers,
            goal_runner=self._run_goal,
            listener=self._delivered,
            suggester=lambda: self.proactive.run_once(),
            **(engine_options or {}),
        )
        self.report: RestoreReport | None = None
        self._beat: asyncio.Task | None = None

    async def _delivered(self, item: Delivered) -> None:
        self.log(f"[{datetime.now():%H:%M:%S}] {item.kind}: {item.title}")
        result = self.notify(item.title, item.body)
        if asyncio.iscoroutine(result):
            await result

    async def _run_goal(self, goal: str, event: dict) -> str:
        proposals: list[str] = []

        async def never(name: str, args: dict, decision: object) -> bool:
            proposals.append(f"- {name}({', '.join(f'{k}={v!r}' for k, v in args.items())})")
            return False  # nobody is watching: a change waits for the owner

        helper = Agent(
            self.store, self.svc, self.ingestor, self.gateway,
            profile="background", approve=never, tools=self.tools,
        )  # fmt: skip
        result = await helper.turn(Session(), goal)
        text = result.text.strip() or "(nothing to report)"
        if proposals:
            text += "\nWaiting for your OK (ask me and I will do it):\n" + "\n".join(proposals)
        return text

    async def _heartbeat(self) -> None:
        while True:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(
                    self.store.meta_set, HEARTBEAT_KEY, datetime.now().isoformat(timespec="seconds")
                )
            await asyncio.sleep(HEARTBEAT_EVERY)

    async def start(self) -> RestoreReport:
        idem = {t.name: t.idempotent for t in self.tools}
        self.report = await asyncio.to_thread(restore_state, self.store, self.plans, idem)
        self._beat = asyncio.create_task(self._heartbeat())
        self.engine.start()
        return self.report

    async def stop(self) -> None:
        await self.engine.stop()
        if self._beat is not None:
            self._beat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._beat
        with contextlib.suppress(Exception):
            await asyncio.to_thread(self.store.meta_set, HEARTBEAT_KEY, "")


async def run_daemon(
    daemon: Daemon, stop: asyncio.Event | None = None, *, forever: bool = True
) -> None:
    report = await daemon.start()
    daemon.log(
        f"Ragnarok daemon started. Restored in {report.elapsed_ms:.0f} ms: "
        f"{report.plans} unfinished plans, {len(report.in_flight)} interrupted tool calls."
    )
    try:
        if forever:
            await (stop or asyncio.Event()).wait()
    finally:
        await daemon.stop()


def lock_path(home: Path) -> Path:
    return home / "daemon.lock"


def mail_root(home: Path) -> Path:
    return Path(os.environ.get("RAGNAROK_MAIL_DIR") or home / "mail")
