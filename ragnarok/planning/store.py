"""Plans in the encrypted vault: the plan, each step's state and result, and an append-only log.

Every state change is written BEFORE the work it announces (AGENTS.md rule 5), so after a crash the
vault says exactly which step was running and what the finished steps produced.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from ragnarok.memory.store import scrub_secrets
from ragnarok.memory.vault import Vault
from ragnarok.planning.models import Plan

MAX_OUTPUT = 1500


@dataclass(frozen=True)
class StepRow:
    step_id: str
    state: str
    attempts: int
    output: str
    error: str
    started_at: str | None
    finished_at: str | None


@dataclass(frozen=True)
class PlanRow:
    id: str
    goal: str
    status: str
    plan: Plan
    replans: int
    session_id: str | None
    created_at: str
    updated_at: str


def _stamp(moment: datetime) -> str:
    return moment.replace(microsecond=0).isoformat(sep=" ")


class PlanStore:
    def __init__(self, vault: Vault, clock: Callable[[], datetime] = datetime.now) -> None:
        self.vault = vault
        self.clock = clock

    def _now(self) -> str:
        return _stamp(self.clock())

    # ---- plans ----------------------------------------------------------------------------

    def create(self, plan: Plan, session_id: str | None = None) -> str:
        plan_id = uuid.uuid4().hex[:10]
        now = self._now()
        with self.vault.transaction() as con:
            con.execute(
                "INSERT INTO plans (id, goal, plan, session_id, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    plan_id,
                    scrub_secrets(plan.goal),
                    json.dumps(plan.as_json()),
                    session_id,
                    now,
                    now,
                ),
            )
            for step in plan.steps:
                con.execute(
                    "INSERT INTO plan_steps (plan_id, step_id) VALUES (?, ?)", (plan_id, step.id)
                )
            con.execute(
                "INSERT INTO plan_log (plan_id, ts, kind, detail) VALUES (?, ?, 'created', ?)",
                (plan_id, now, f"{len(plan.steps)} steps"),
            )
        return plan_id

    def get(self, plan_id: str) -> PlanRow | None:
        rows = self.vault.query(
            "SELECT id, goal, status, plan, replans, session_id, created_at, updated_at"
            " FROM plans WHERE id = ?",
            (plan_id,),
        )
        if not rows:
            return None
        r = rows[0]
        return PlanRow(r[0], r[1], r[2], Plan.from_json(json.loads(r[3])), r[4], r[5], r[6], r[7])

    def set_status(self, plan_id: str, status: str, detail: str = "") -> None:
        now = self._now()
        with self.vault.transaction() as con:
            con.execute(
                "UPDATE plans SET status = ?, updated_at = ? WHERE id = ?", (status, now, plan_id)
            )
            con.execute(
                "INSERT INTO plan_log (plan_id, ts, kind, detail) VALUES (?, ?, 'finished', ?)"
                if status in ("done", "partial", "failed", "cancelled")
                else "INSERT INTO plan_log (plan_id, ts, kind, detail) VALUES (?, ?, 'status', ?)",
                (plan_id, now, f"{status}: {detail}"[:300]),
            )

    def replace_steps(self, plan_id: str, plan: Plan, detail: str) -> None:
        """A re-plan: new steps replace the ones not finished; finished steps keep their rows."""
        now = self._now()
        with self.vault.transaction() as con:
            con.execute(
                "UPDATE plans SET plan = ?, replans = replans + 1, updated_at = ? WHERE id = ?",
                (json.dumps(plan.as_json()), now, plan_id),
            )
            keep = [
                r[0]
                for r in con.execute(
                    "SELECT step_id FROM plan_steps WHERE plan_id = ? AND state = 'done'",
                    (plan_id,),
                ).fetchall()
            ]
            if keep:
                marks = ",".join("?" * len(keep))
                con.execute(
                    f"DELETE FROM plan_steps WHERE plan_id = ? AND step_id NOT IN ({marks})",
                    (plan_id, *keep),
                )
            else:
                con.execute("DELETE FROM plan_steps WHERE plan_id = ?", (plan_id,))
            for step in plan.steps:
                con.execute(
                    "INSERT OR IGNORE INTO plan_steps (plan_id, step_id) VALUES (?, ?)",
                    (plan_id, step.id),
                )
            con.execute(
                "INSERT INTO plan_log (plan_id, ts, kind, detail) VALUES (?, ?, 'replan', ?)",
                (plan_id, now, detail[:300]),
            )

    def unfinished(self) -> list[PlanRow]:
        rows = self.vault.query("SELECT id FROM plans WHERE status IN ('running', 'interrupted')")
        return [p for p in (self.get(r[0]) for r in rows) if p]

    # ---- steps ----------------------------------------------------------------------------

    def set_state(
        self, plan_id: str, step_id: str, state: str, *, output: str | None = None,
        error: str | None = None, attempts: int | None = None,
    ) -> None:  # fmt: skip
        now = self._now()
        sets, args = ["state = ?"], [state]
        if state == "running":
            sets.append("started_at = COALESCE(started_at, ?)")
            args.append(now)
        if state in ("done", "failed", "blocked", "skipped", "interrupted"):
            sets.append("finished_at = ?")
            args.append(now)
        if output is not None:
            sets.append("output = ?")
            args.append(scrub_secrets(output)[:MAX_OUTPUT])
        if error is not None:
            sets.append("error = ?")
            args.append(scrub_secrets(error)[:500])
        if attempts is not None:
            sets.append("attempts = ?")
            args.append(attempts)
        with self.vault.transaction() as con:
            con.execute(
                f"UPDATE plan_steps SET {', '.join(sets)} WHERE plan_id = ? AND step_id = ?",
                (*args, plan_id, step_id),
            )
            con.execute(
                "INSERT INTO plan_log (plan_id, ts, kind, step_id, detail)"
                " VALUES (?, ?, 'step_state', ?, ?)",
                (plan_id, now, step_id, state),
            )

    def steps(self, plan_id: str) -> dict[str, StepRow]:
        rows = self.vault.query(
            "SELECT step_id, state, attempts, output, error, started_at, finished_at"
            " FROM plan_steps WHERE plan_id = ? ORDER BY step_id",
            (plan_id,),
        )
        return {r[0]: StepRow(*r) for r in rows}

    def log(self, plan_id: str, kind: str, step_id: str | None = None, detail: str = "") -> None:
        self.vault.execute(
            "INSERT INTO plan_log (plan_id, ts, kind, step_id, detail) VALUES (?, ?, ?, ?, ?)",
            (plan_id, self._now(), kind, step_id, scrub_secrets(detail)[:300]),
        )

    def history(self, plan_id: str) -> list[tuple[str, str, str | None, str]]:
        return [
            (r[0], r[1], r[2], r[3])
            for r in self.vault.query(
                "SELECT ts, kind, step_id, detail FROM plan_log WHERE plan_id = ? ORDER BY seq",
                (plan_id,),
            )
        ]
