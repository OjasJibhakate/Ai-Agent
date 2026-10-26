"""State recovery after a crash or power loss (NFR3.2: working again in under 5 seconds).

Everything needed is already in the vault, because state is written before the work it announces.
`restore_state` only reads it back and tidies what a dead program left half done:

- plans and steps still marked running become `interrupted`, so `/resume` carries on from them;
- a tool call that was logged as started but never recorded a result is closed with an
  "interrupted" result, and reported, so the owner is asked before a call that is not safe to
  repeat (not idempotent) runs again;
- the last conversation's recent messages come back as the working memory of the new session.

No model is called and nothing runs on the event loop: the cost is a handful of vault queries.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ragnarok.memory.store import MemoryStore, utcnow
from ragnarok.planning.store import PlanStore

RESTORE_BUDGET_S = 5.0  # NFR3.2
SESSION_KEY = "chat_session"  # meta: the id of the chat session the program last ran
HISTORY_MESSAGES = 12
HISTORY_MAX_AGE = timedelta(hours=12)
SCAN_EVENTS = 200  # how far back to look for a tool call that never finished


@dataclass(frozen=True)
class InFlightCall:
    """A tool call that was logged as started, and never got a result."""

    tool: str
    args: dict
    safe_to_repeat: bool  # the tool is idempotent (or unknown to us and read-only by name)


@dataclass
class RestoreReport:
    plans: int = 0  # unfinished plans found
    steps: int = 0  # steps that were running and are now `interrupted`
    in_flight: list[InFlightCall] = field(default_factory=list)
    history: list[dict[str, str]] = field(default_factory=list)  # working memory to put back
    elapsed_ms: float = 0.0

    @property
    def risky(self) -> list[InFlightCall]:
        return [c for c in self.in_flight if not c.safe_to_repeat]

    @property
    def within_budget(self) -> bool:
        return self.elapsed_ms <= RESTORE_BUDGET_S * 1000


def remember_session(store: MemoryStore, session_id: str) -> None:
    """Note which session is live, so the next start can find its messages."""
    store.meta_set(SESSION_KEY, session_id)


def restore_state(
    store: MemoryStore,
    plans: PlanStore,
    idempotent: Mapping[str, bool] | None = None,
    *,
    now: datetime | None = None,
) -> RestoreReport:
    """Read back what a stopped program left behind. Safe to call on every start."""
    started = time.perf_counter()
    report = RestoreReport()
    idempotent = idempotent or {}

    for row in plans.unfinished():
        report.plans += 1
        for step_id, step in plans.steps(row.id).items():
            if step.state in ("running", "waiting_approval"):
                plans.set_state(row.id, step_id, "interrupted")
                report.steps += 1
        if row.status == "running":
            plans.set_status(row.id, "interrupted", "the program stopped")

    report.in_flight = _close_unfinished_calls(store, idempotent)
    report.history = _last_conversation(store, now or utcnow())
    report.elapsed_ms = (time.perf_counter() - started) * 1000
    return report


def _close_unfinished_calls(
    store: MemoryStore, idempotent: Mapping[str, bool]
) -> list[InFlightCall]:
    rows = store.vault.query(
        "SELECT id, payload, session_id FROM events WHERE kind IN"
        " ('memory_tool_call', 'memory_tool_result') ORDER BY id DESC LIMIT ?",
        (SCAN_EVENTS,),
    )
    open_calls: list[InFlightCall] = []
    seen_results: dict[str | None, int] = {}  # results newer than the call being looked at
    for _id, payload, session in rows:  # newest first
        if payload.startswith("{"):
            try:
                call = json.loads(payload)
            except ValueError:
                continue
            if call.get("status") != "started":
                continue
            if seen_results.get(session, 0) > 0:
                seen_results[session] -= 1  # this call has its result
                continue
            name = str(call.get("tool", ""))
            args = call.get("args") if isinstance(call.get("args"), dict) else {}
            open_calls.append(InFlightCall(name, args, bool(idempotent.get(name, False))))
            store.record_event(
                "chat",
                "memory_tool_result",
                f"{name}: interrupted (the program stopped before it finished)",
                session_id=session,
                dedupe_seconds=None,
            )
        else:
            seen_results[session] = seen_results.get(session, 0) + 1
    return list(reversed(open_calls))


def _last_conversation(store: MemoryStore, now: datetime) -> list[dict[str, str]]:
    session = store.meta_get(SESSION_KEY)
    if not session:
        return []
    rows = store.vault.query(
        "SELECT ts, kind, payload FROM events WHERE session_id = ? AND source = 'chat'"
        " AND kind IN ('user_message', 'assistant_message') ORDER BY id DESC LIMIT ?",
        (session, HISTORY_MESSAGES),
    )
    if not rows:
        return []
    last = datetime.strptime(rows[0][0], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=now.tzinfo)
    if now - last > HISTORY_MAX_AGE:
        return []
    role = {"user_message": "user", "assistant_message": "assistant"}
    return [{"role": role[kind], "content": text} for _ts, kind, text in reversed(rows)]
