"""Skill: a project task board (FR3.2, project-management side).

Tasks live in the encrypted vault, so the agent and the owner share one list per project.

  list_tasks            R0  read-only
  add_task, done_task   R1  local and reversible (a done task can be added again)
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime

from pydantic import Field

from ragnarok.memory.store import scrub_secrets
from ragnarok.memory.vault import Vault
from ragnarok.policy.permissions import Risk
from ragnarok.runtime.tools import Tool, ToolArgs
from ragnarok.skills.steps import reply

MAX_OPEN = 500


class TaskBoard:
    def __init__(self, vault: Vault, clock=datetime.now) -> None:
        self.vault = vault
        self.clock = clock

    def _now(self) -> str:
        return self.clock().replace(microsecond=0).isoformat(sep=" ")

    def add(self, title: str, project: str = "general", due: str | None = None) -> int:
        if due and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", due):
            raise ValueError("due must look like 2026-11-03")
        if self.vault.query("SELECT COUNT(*) FROM tasks WHERE status = 'open'")[0][0] >= MAX_OPEN:
            raise ValueError("too many open tasks; finish some first")
        name = project.strip().lower()[:40] or "general"
        with self.vault.transaction() as con:
            return con.execute(
                "INSERT INTO tasks (project, title, due, created_at) VALUES (?, ?, ?, ?)",
                (name, scrub_secrets(title)[:200], due, self._now()),
            ).lastrowid

    def done(self, task_id: int) -> bool:
        with self.vault.transaction() as con:
            cur = con.execute(
                "UPDATE tasks SET status = 'done', done_at = ? WHERE id = ? AND status = 'open'",
                (self._now(), task_id),
            )
            return cur.rowcount > 0

    def list(self, project: str | None = None, status: str = "open") -> list[tuple]:
        sql = "SELECT id, project, title, due, status FROM tasks WHERE status = ?"
        params: list = [status]
        if project:
            sql += " AND project = ?"
            params.append(project.strip().lower())
        return self.vault.query(sql + " ORDER BY due IS NULL, due, id LIMIT 50", tuple(params))


class AddTaskArgs(ToolArgs):
    title: str = Field(min_length=2, max_length=200, description="What needs doing.")
    project: str = Field(default="general", max_length=40)
    due: str | None = Field(default=None, description="Due date, YYYY-MM-DD.")


class ListTasksArgs(ToolArgs):
    project: str | None = Field(default=None, max_length=40)
    status: str = Field(default="open", pattern="^(open|done)$")


class DoneTaskArgs(ToolArgs):
    id: int = Field(ge=1, description="The task number from list_tasks.")


def task_tools(board: TaskBoard) -> list[Tool]:
    async def add(a: AddTaskArgs):
        try:
            n = await asyncio.to_thread(board.add, a.title, a.project, a.due)
        except ValueError as exc:
            return f"error: {exc}"
        return reply(f"Added task #{n} to {a.project}: {a.title}")

    async def listing(a: ListTasksArgs):
        rows = await asyncio.to_thread(board.list, a.project, a.status)
        if not rows:
            return f"No {a.status} tasks."
        return "\n".join(
            f"#{i} [{p}] {t}" + (f" (due {d})" if d else "") for i, p, t, d, _s in rows
        )

    async def done(a: DoneTaskArgs):
        ok = await asyncio.to_thread(board.done, a.id)
        return reply(f"Marked task #{a.id} done.") if ok else f"error: no open task #{a.id}"

    common = {"timeout_s": 10, "sandbox": "workspace-write"}
    return [
        Tool(
            name="add_task",
            description="Add a task to a project's list. Use it when the user wants to track "
            "something to do.",
            args_model=AddTaskArgs,
            risk=Risk.R1,
            idempotent=False,
            handler=add,
            **common,
        ),
        Tool(
            name="list_tasks",
            description="Show the open tasks, optionally for one project. Use it when the user "
            "asks what is left to do.",
            args_model=ListTasksArgs,
            risk=Risk.R0,
            idempotent=True,
            handler=listing,
            **common,
        ),
        Tool(
            name="done_task",
            description="Mark a task finished by its number. Use it when the user says a task "
            "is done.",
            args_model=DoneTaskArgs,
            risk=Risk.R1,
            idempotent=True,
            handler=done,
            **common,
        ),
    ]
