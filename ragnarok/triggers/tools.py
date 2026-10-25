"""Tools that create, list and cancel triggers (FR4.1).

The model never works out a time itself: it passes the person's words ("tomorrow at 9", "every
weekday at 8") and `parse_when` decides, so a small model cannot get the hour wrong by arithmetic.

Risk follows what the trigger will do later without anyone watching:
  set_reminder, cancel_reminder   R1  a note for the user; cancelling undoes it
  schedule_task, watch_folder     R2  a goal that will run by itself, so the owner approves it once
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from pydantic import Field

from ragnarok.policy.modes import Mode
from ragnarok.policy.permissions import Risk
from ragnarok.runtime.tools import Tool, ToolArgs
from ragnarok.skills.files import AmbiguousName, FileAccess, default_roots
from ragnarok.skills.pathguard import PathRefused
from ragnarok.skills.steps import reply
from ragnarok.triggers.schedule import WhenError, parse_when
from ragnarok.triggers.store import Trigger, TriggerLimitError, TriggerStore

NOT_RUNNING_NOTE = (
    " I can only do this while Ragnarok is open; if it is closed when the time comes, I will "
    "tell you as soon as you open it."
)


class SetReminderArgs(ToolArgs):
    what: str = Field(min_length=2, max_length=300, description="What to remind about.")
    when: str = Field(
        min_length=2,
        max_length=120,
        description="When, in the user's words: 'in 10 minutes', 'at 6pm', 'tomorrow at 9am'.",
    )


class ScheduleTaskArgs(ToolArgs):
    goal: str = Field(min_length=3, max_length=400, description="What to do each time.")
    when: str = Field(
        min_length=2, max_length=120, description="'every weekday at 8', 'tomorrow at 9am', ..."
    )


class WatchFolderArgs(ToolArgs):
    folder: str = Field(min_length=1, max_length=400, description="The folder to watch.")
    pattern: str = Field(
        default="*", max_length=80, description="Which file names count, like *.pdf or *invoice*."
    )
    goal: str | None = Field(
        default=None,
        max_length=400,
        description="What to do for each new file; use $path for its path. Leave out to just "
        "tell the user about new files.",
    )


class CancelArgs(ToolArgs):
    id: int = Field(ge=1, description="The number of the reminder, schedule or watch.")


class ListArgs(ToolArgs):
    pass


def describe(trigger: Trigger) -> str:
    what = {"date": "reminder", "cron": "schedule", "folder": "watch"}[trigger.kind]
    where = f" in {trigger.spec} ({trigger.glob or '*'})" if trigger.kind == "folder" else ""
    do = "" if trigger.action == "notify" else " [runs a task]"
    when = trigger.label or "on new files"
    return f"#{trigger.id} {what}{where}: {trigger.payload[:80]}{do} - {when}"


def trigger_tools(
    store: TriggerStore,
    roots: list[Path] | None = None,
    home: Path | None = None,
    mode: Mode | None = None,
    clock: Callable[[], datetime] = datetime.now,
) -> list[Tool]:
    access = FileAccess(roots if roots is not None else default_roots(home), home, mode)

    def folder_path(text: str) -> Path:
        try:
            path = access.guard.readable(text, access.roots, wide=False)
            if not path.is_dir():
                path = access.locate(text)
            path = access.guard.readable(str(path), access.roots, wide=False)
        except (PathRefused, AmbiguousName, ValueError) as exc:
            raise ValueError(str(exc)) from exc
        if not path.is_dir():
            raise ValueError(f"{path} is not a folder")
        return path

    async def add(kind, spec, action, payload, label, **kw) -> Trigger | str:
        try:
            return await asyncio.to_thread(store.add, kind, spec, action, payload, label, **kw)
        except (TriggerLimitError, ValueError) as exc:
            return f"error: {exc}"

    async def set_reminder(args: SetReminderArgs):
        try:
            when = parse_when(args.when, clock())
        except WhenError as exc:
            return f"error: {exc}"
        if when.kind == "once":
            made = await add("date", when.at.isoformat(sep=" "), "notify", args.what, when.label)
        else:
            made = await add("cron", when.cron, "notify", args.what, when.label)
        if isinstance(made, str):
            return made
        return reply(
            f"Okay, I'll remind you to {args.what.rstrip('.')} {when.label}. (#{made.id})"
            + NOT_RUNNING_NOTE
        )

    async def schedule_task(args: ScheduleTaskArgs):
        try:
            when = parse_when(args.when, clock())
        except WhenError as exc:
            return f"error: {exc}"
        kind, spec = (
            ("date", when.at.isoformat(sep=" ")) if when.kind == "once" else ("cron", when.cron)
        )
        made = await add(kind, spec, "goal", args.goal, when.label)
        if isinstance(made, str):
            return made
        return reply(
            f"Scheduled: {args.goal.rstrip('.')} {when.label}. (#{made.id}) It runs without "
            "changing anything: whatever needs your OK waits in your Inbox." + NOT_RUNNING_NOTE
        )

    async def watch_folder(args: WatchFolderArgs):
        try:
            path = await asyncio.to_thread(folder_path, args.folder)
        except ValueError as exc:
            return f"error: {exc}"
        action = "goal" if args.goal else "notify"
        payload = args.goal or f"New file matching {args.pattern}"
        made = await add("folder", str(path), action, payload, "", glob=args.pattern)
        if isinstance(made, str):
            return made
        what = "work on" if args.goal else "tell you about"
        return reply(
            f"Okay, I'll {what} new files matching {args.pattern} in {path}. (#{made.id})"
            + NOT_RUNNING_NOTE
        )

    async def cancel_reminder(args: CancelArgs):
        done = await asyncio.to_thread(store.cancel, args.id)
        return reply(f"Cancelled #{args.id}.") if done else f"error: there is no active #{args.id}"

    async def list_reminders(args: ListArgs) -> str:
        active = await asyncio.to_thread(store.list)
        if not active:
            return "No active reminders, schedules or watches."
        return "\n".join(describe(t) for t in active)

    common = {"idempotent": False, "sandbox": "workspace-write"}
    return [
        Tool(
            name="set_reminder",
            description="Remind the user of something at a time or on a repeat. Use it when the "
            "user says remind me, or asks for a recurring reminder.",
            args_model=SetReminderArgs,
            risk=Risk.R1,
            timeout_s=10,
            handler=set_reminder,
            fs={},
            **common,
        ),
        Tool(
            name="schedule_task",
            description="Run a task by itself later or on a repeat, like 'every weekday at 8 "
            "brief me'. Use it only for work to be done, not for a plain reminder.",
            args_model=ScheduleTaskArgs,
            risk=Risk.R2,
            timeout_s=10,
            handler=schedule_task,
            approval_note="This will run by itself on that schedule. It can only read and "
            "propose; anything that changes something waits in your Inbox for your OK.",
            fs={},
            **common,
        ),
        Tool(
            name="watch_folder",
            description="Watch a folder and tell the user, or work on, each new file that "
            "appears. Use it when the user asks to be told about or to handle new files.",
            args_model=WatchFolderArgs,
            risk=Risk.R2,
            timeout_s=10,
            handler=watch_folder,
            approval_note="This will keep watching that folder and act on every new file. It "
            "can only read and propose; anything that changes something waits in your Inbox.",
            fs={"read": ["the folder named"]},
            **common,
        ),
        Tool(
            name="list_reminders",
            description="List the active reminders, schedules and folder watches.",
            args_model=ListArgs,
            risk=Risk.R0,
            idempotent=True,
            timeout_s=10,
            sandbox="read-only",
            handler=list_reminders,
        ),
        Tool(
            name="cancel_reminder",
            description="Cancel a reminder, schedule or folder watch by its number.",
            args_model=CancelArgs,
            risk=Risk.R1,
            idempotent=True,
            timeout_s=10,
            sandbox="workspace-write",
            handler=cancel_reminder,
        ),
    ]
