"""The trigger engine (FR4.1): things that start by themselves.

Three kinds of trigger, one rule. A trigger is a reminder at a time (`date`), a repeating schedule
(`cron`) or a watched folder (`folder`). Every event they produce has a dedupe key, and the fire is
written to the vault BEFORE its action runs, so an event is handled once, even across a restart or
a crash (AGENTS.md rule 5). What happens when one fires:

- `notify`: the text goes to the Inbox, and the screen/desktop is told;
- `goal`: a headless agent works on the goal in the read-only `background` profile. Anything that
  would change something becomes an Inbox item waiting for the owner's approval, never an action.

Downtime is handled the way the design says: a repeating schedule that missed slots fires once
(coalesced), a reminder that came due fires as soon as the program is back and says it is late, and
a folder is compared with its saved snapshot so files that arrived meanwhile are found.

The folder watcher polls (every two seconds, in a worker thread) instead of using a file-event
library: no new dependency, and it works on OneDrive folders and network shares. A file counts only
once its size has stopped changing, so a download in progress is never picked up half-written.
"""

from __future__ import annotations

import asyncio
import contextlib
import fnmatch
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from ragnarok.triggers.schedule import Cron
from ragnarok.triggers.store import Trigger, TriggerStore

TEMP_NAME = re.compile(
    r"^(~\$.*|\.~.*|.*\.(tmp|part|crdownload|partial|download)|desktop\.ini|thumbs\.db)$", re.I
)
LATE_AFTER = timedelta(minutes=2)
MAX_NEW_FILES = 20  # one scan never starts more than this many fires (a copied folder)

GoalRunner = Callable[[str, dict], Awaitable[str]]
Listener = Callable[["Delivered"], Awaitable[None]]


@dataclass(frozen=True)
class Delivered:
    """What the user should be told about right now."""

    kind: str  # reminder | result | file | failed
    title: str
    body: str
    trigger_id: int


def scan_folder(folder: str, pattern: str) -> dict[str, tuple[int, int]]:
    """{file name: (size, modified time in ns)} for the matching files directly in `folder`."""
    found: dict[str, tuple[int, int]] = {}
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return found
    for entry in entries:
        try:
            if not entry.is_file(follow_symlinks=False) or TEMP_NAME.match(entry.name):
                continue
            if pattern and not fnmatch.fnmatch(entry.name.lower(), pattern.lower()):
                continue
            info = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        found[entry.name] = (info.st_size, info.st_mtime_ns)
    return found


def say_when(moment: datetime) -> str:
    clock = f"{moment.hour % 12 or 12}:{moment.minute:02d} {'AM' if moment.hour < 12 else 'PM'}"
    return f"{moment:%a %d %b} at {clock}"


class TriggerEngine:
    def __init__(
        self,
        store: TriggerStore,
        *,
        goal_runner: GoalRunner | None = None,
        listener: Listener | None = None,
        idle: Callable[[], bool] = lambda: True,
        clock: Callable[[], datetime] = datetime.now,
        tick_seconds: float = 1.0,
        folder_seconds: float = 2.0,
    ) -> None:
        self.store = store
        self.goal_runner = goal_runner
        self.listener = listener
        self.idle = idle
        self.clock = clock
        self.tick_seconds = tick_seconds
        self.folder_seconds = folder_seconds
        self.paused = False
        self.restored = 0
        self._task: asyncio.Task | None = None

    # ---- life cycle ---------------------------------------------------------------------------

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        await self.recover()
        last_scan = 0.0
        loop = asyncio.get_running_loop()
        while True:
            if not self.paused:
                with contextlib.suppress(Exception):  # one bad trigger must not stop the rest
                    await self.tick()
                    if loop.time() - last_scan >= self.folder_seconds:
                        last_scan = loop.time()
                        await self.scan()
            await asyncio.sleep(self.tick_seconds)

    # ---- after a crash --------------------------------------------------------------------------

    async def recover(self) -> int:
        """Finish what a stop in the middle left: fires that began but never recorded an outcome.

        A reminder is safe to finish again (the Inbox post is keyed by the fire). A goal is not
        re-run blindly: the owner is told it was interrupted and can ask again.
        """
        fires = await asyncio.to_thread(self.store.unfinished_fires)
        for fire in fires:
            trigger = await asyncio.to_thread(self.store.get, fire.trigger_id)
            if trigger is None:
                continue
            if trigger.action == "notify":
                kind, title, body = self._notice(trigger, {"late": False})
                await self._deliver(trigger, fire.id, "done", kind, title, body)
            else:
                await self._deliver(
                    trigger, fire.id, "failed", "failed",
                    f"Interrupted: {trigger.payload[:60]}",
                    "The program stopped while this was running. Ask me again if you want it.",
                )  # fmt: skip
            if trigger.kind == "date":
                await asyncio.to_thread(self.store.finish, trigger.id)
        self.restored = len(fires)
        return len(fires)

    # ---- time -----------------------------------------------------------------------------------

    async def tick(self, now: datetime | None = None) -> int:
        """Fire every reminder and schedule that is due. Returns how many fired."""
        now = now or self.clock()
        fired = 0
        for trigger in await asyncio.to_thread(self.store.list):
            if trigger.kind == "date":
                due = datetime.fromisoformat(trigger.spec)
                if due <= now:
                    late = now - due > LATE_AFTER
                    event = {"due": due, "late": late}
                    fired += await self._fire(trigger, f"once:{trigger.spec}", event, finish=True)
            elif trigger.kind == "cron":
                fired += await self._tick_cron(trigger, now)
        return fired

    async def _tick_cron(self, trigger: Trigger, now: datetime) -> int:
        cron = Cron(trigger.spec)
        nxt = trigger.state.get("next")
        if nxt is None:
            created = datetime.fromisoformat(trigger.created_at)
            nxt_time = cron.next_after(created)
        else:
            nxt_time = datetime.fromisoformat(nxt)
        if nxt_time is None:
            await asyncio.to_thread(self.store.finish, trigger.id)
            return 0
        if nxt_time > now:
            if nxt is None:
                await asyncio.to_thread(
                    self.store.save_state, trigger.id, {**trigger.state, "next": str(nxt_time)}
                )
            return 0
        due = cron.last_at_or_before(now) or nxt_time  # missed slots are coalesced into one fire
        event = {"due": due, "late": now - due > LATE_AFTER}
        count = await self._fire(trigger, f"cron:{due:%Y-%m-%d %H:%M}", event)
        following = cron.next_after(now)
        await asyncio.to_thread(
            self.store.save_state,
            trigger.id,
            {**trigger.state, "next": str(following) if following else None},
        )
        return count

    # ---- folders --------------------------------------------------------------------------------

    async def scan(self) -> int:
        """Look at every watched folder once. Returns how many files started a fire."""
        started = 0
        for trigger in await asyncio.to_thread(self.store.list):
            if trigger.kind == "folder":
                started += await self._scan_one(trigger)
        return started

    async def _scan_one(self, trigger: Trigger) -> int:
        now_files = await asyncio.to_thread(scan_folder, trigger.spec, trigger.glob)
        state = dict(trigger.state)
        seen: dict[str, list[int]] = state.get("seen") or {}
        if "seen" not in state:  # the first look only records what is already there
            state["seen"] = {name: list(meta) for name, meta in now_files.items()}
            await asyncio.to_thread(self.store.save_state, trigger.id, state)
            return 0
        pending: dict[str, list[int]] = state.get("pending") or {}
        new_pending: dict[str, list[int]] = {}
        started = 0
        for name, meta in sorted(now_files.items()):
            if seen.get(name) == list(meta):
                continue
            if pending.get(name) != list(meta):
                new_pending[name] = list(meta)  # changed since the last look: wait one more round
                continue
            if started >= MAX_NEW_FILES:
                new_pending[name] = list(meta)
                continue
            path = str(Path(trigger.spec) / name)
            event = {"path": path, "name": name, "size": meta[0]}
            fired = await self._fire(trigger, f"{path}|{meta[0]}|{meta[1]}", event)
            seen[name] = list(meta)  # this exact version is handled, fired now or once before
            started += fired
        state["seen"] = {n: m for n, m in seen.items() if n in now_files}  # forget deleted files
        state["pending"] = new_pending
        if state != trigger.state:
            await asyncio.to_thread(self.store.save_state, trigger.id, state)
        return started

    # ---- firing ---------------------------------------------------------------------------------

    def _notice(self, trigger: Trigger, event: dict) -> tuple[str, str, str]:
        if trigger.kind == "folder":
            name = event.get("name", "a file")
            return (
                "file",
                f"New file: {name}",
                f"{event.get('path', '')}\n{trigger.payload}".strip(),
            )
        body = f"Set for {trigger.label}" if trigger.label else ""
        if event.get("late") and event.get("due"):
            body += f" (it was due {say_when(event['due'])}; I was not running then)"
        return "reminder", trigger.payload, body.strip()

    async def _fire(self, trigger: Trigger, key: str, event: dict, *, finish: bool = False) -> int:
        fire_id = await asyncio.to_thread(self.store.begin_fire, trigger.id, key)
        if fire_id is None:
            if finish:
                await asyncio.to_thread(self.store.finish, trigger.id)
            return 0  # this exact event was handled before
        outcome, kind, title, body = "done", "reminder", trigger.payload, ""
        try:
            if trigger.action == "notify":
                kind, title, body = self._notice(trigger, event)
            else:
                kind, title, body = await self._run_goal(trigger, event)
        except Exception as exc:  # the action failing is reported, never raised
            outcome, kind = "failed", "failed"
            title = f"Could not finish: {trigger.payload[:60]}"
            body = f"It stopped with {type(exc).__name__}."
        await self._deliver(trigger, fire_id, outcome, kind, title, body)
        if finish:
            await asyncio.to_thread(self.store.finish, trigger.id)
        return 1

    async def _run_goal(self, trigger: Trigger, event: dict) -> tuple[str, str, str]:
        if self.goal_runner is None:
            raise RuntimeError("no goal runner")
        while not self.idle():  # background work waits for the user's own turn to finish
            await asyncio.sleep(0.5)
        goal = trigger.payload.replace("$path", str(event.get("path", "")))
        text = await self.goal_runner(goal, event)
        return "result", f"Done: {trigger.payload[:60]}", text

    async def _deliver(
        self, trigger: Trigger, fire_id: int, outcome: str, kind: str, title: str, body: str
    ) -> None:
        await asyncio.to_thread(
            self.store.post, kind, title, body, trigger_id=trigger.id, fire_id=fire_id
        )
        await asyncio.to_thread(self.store.end_fire, fire_id, outcome)
        if self.listener is not None:
            with contextlib.suppress(Exception):
                await self.listener(Delivered(kind, title, body, trigger.id))
