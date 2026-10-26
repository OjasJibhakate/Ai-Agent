"""Explainability (NFR4.1): which tool was chosen, why it was allowed, and what the steps were.

Everything shown here was written to the vault before the tool ran (AGENTS.md rule 5), so the
answer to "what did you just do?" is a record, not the model's memory of it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from ragnarok.memory.store import MemoryStore
from ragnarok.planning.store import PlanStore

EVENT_KINDS = ("memory_tool_call", "memory_tool_result")


@dataclass(frozen=True)
class ToolRecord:
    ts: str
    tool: str
    args: dict
    reason: str  # the model's own words for why it called the tool
    rule: str  # the permission rule that decided (allow:..., default:R1, role:viewer, ask:...)
    status: str  # started | denied
    result: str = ""  # a short record of what came back ("" while it was still running)


def recent_tool_calls(store: MemoryStore, limit: int = 15) -> list[ToolRecord]:
    """The latest tool calls, oldest first, each with its permission rule and short result."""
    rows = store.vault.query(
        "SELECT ts, kind, payload, session_id FROM events WHERE kind IN (?, ?)"
        " ORDER BY id DESC LIMIT ?",
        (*EVENT_KINDS, limit * 4),
    )
    records: list[ToolRecord] = []
    waiting: dict[tuple[str | None, str], int] = {}  # (session, tool) -> index of the open call
    for ts, kind, payload, session in reversed(rows):
        if kind == "memory_tool_call":
            try:
                call = json.loads(payload)
            except ValueError:
                continue
            records.append(
                ToolRecord(
                    ts,
                    str(call.get("tool", "?")),
                    call.get("args") or {},
                    str(call.get("reason") or ""),
                    str(call.get("rule", "")),
                    str(call.get("status", "")),
                )
            )
            if call.get("status") == "started":
                waiting[(session, records[-1].tool)] = len(records) - 1
        else:
            name, _, text = payload.partition(": ")
            at = waiting.pop((session, name), None)
            if at is not None:
                old = records[at]
                records[at] = ToolRecord(
                    old.ts, old.tool, old.args, old.reason, old.rule, old.status, text
                )
    return records[-limit:]


def plan_steps(plans: PlanStore, plan_id: str) -> list[str]:
    """One line per event of a plan: what was planned, tried, retried, re-planned, finished."""
    return [
        f"{ts[11:19]}  {kind}{(' ' + step) if step else ''}  {detail}".rstrip()
        for ts, kind, step, detail in plans.history(plan_id)
    ]


def latest_plan_id(plans: PlanStore) -> str | None:
    rows = plans.vault.query("SELECT id FROM plans ORDER BY created_at DESC, rowid DESC LIMIT 1")
    return rows[0][0] if rows else None
