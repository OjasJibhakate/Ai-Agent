"""Triggers, their fires and the Inbox, kept in the encrypted vault.

All methods are synchronous (the vault is) and safe to call from worker threads; async code uses
`asyncio.to_thread`. A fire is recorded BEFORE its action runs (AGENTS.md rule 5), and the unique
key on (trigger, dedupe key) means the same event can never fire twice, even across restarts.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from ragnarok.memory.vault import Vault

MAX_ACTIVE = 100  # a runaway model cannot create an endless pile of triggers
MAX_TEXT = 1000


@dataclass(frozen=True)
class Trigger:
    id: int
    kind: str  # date | cron | folder
    spec: str
    glob: str
    action: str  # notify | goal
    payload: str
    label: str
    status: str
    state: dict
    created_at: str
    last_fired_at: str | None


@dataclass(frozen=True)
class Fire:
    id: int
    trigger_id: int
    dedupe_key: str
    fired_at: str
    outcome: str


@dataclass(frozen=True)
class InboxItem:
    id: int
    created_at: str
    trigger_id: int | None
    kind: str
    title: str
    body: str
    status: str


class TriggerLimitError(ValueError):
    """Too many active triggers."""


def _stamp(moment: datetime) -> str:
    return moment.replace(microsecond=0).isoformat(sep=" ")


_COLUMNS = "id, kind, spec, glob, action, payload, label, status, state, created_at, last_fired_at"


def _trigger(row: tuple) -> Trigger:
    return Trigger(*row[:8], json.loads(row[8] or "{}"), row[9], row[10])


class TriggerStore:
    def __init__(self, vault: Vault, clock: Callable[[], datetime] = datetime.now) -> None:
        self.vault = vault
        self.clock = clock

    # ---- triggers -------------------------------------------------------------------------

    def add(
        self,
        kind: str,
        spec: str,
        action: str,
        payload: str,
        label: str,
        *,
        glob: str = "",
        state: dict | None = None,
    ) -> Trigger:
        payload = payload.strip()[:MAX_TEXT]
        if not payload:
            raise ValueError("there is nothing to say or do")
        with self.vault.transaction() as con:
            active = con.execute(
                "SELECT COUNT(*) FROM triggers WHERE status = 'active'"
            ).fetchone()[0]
            if active >= MAX_ACTIVE:
                raise TriggerLimitError(f"there are already {MAX_ACTIVE} active reminders")
            cur = con.execute(
                "INSERT INTO triggers (kind, spec, glob, action, payload, label, state, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (kind, spec, glob, action, payload, label, json.dumps(state or {}),
                 _stamp(self.clock())),
            )  # fmt: skip
            new_id = cur.lastrowid
        return self.get(new_id)  # type: ignore[arg-type]

    def get(self, trigger_id: int) -> Trigger | None:
        rows = self.vault.query(f"SELECT {_COLUMNS} FROM triggers WHERE id = ?", (trigger_id,))
        return _trigger(rows[0]) if rows else None

    def list(self, *, active_only: bool = True) -> list[Trigger]:
        where = "WHERE status = 'active'" if active_only else ""
        rows = self.vault.query(f"SELECT {_COLUMNS} FROM triggers {where} ORDER BY id")
        return [_trigger(r) for r in rows]

    def cancel(self, trigger_id: int) -> bool:
        with self.vault.transaction() as con:
            cur = con.execute(
                "UPDATE triggers SET status = 'cancelled' WHERE id = ? AND status = 'active'",
                (trigger_id,),
            )
            return cur.rowcount > 0

    def finish(self, trigger_id: int) -> None:
        self.vault.execute("UPDATE triggers SET status = 'done' WHERE id = ?", (trigger_id,))

    def save_state(self, trigger_id: int, state: dict) -> None:
        self.vault.execute(
            "UPDATE triggers SET state = ? WHERE id = ?", (json.dumps(state), trigger_id)
        )

    # ---- fires ------------------------------------------------------------------------------

    def begin_fire(self, trigger_id: int, dedupe_key: str) -> int | None:
        """Record that this event is being handled. None means it already was (once only)."""
        with self.vault.transaction() as con:
            cur = con.execute(
                "INSERT OR IGNORE INTO trigger_fires (trigger_id, dedupe_key, fired_at)"
                " VALUES (?, ?, ?)",
                (trigger_id, dedupe_key, _stamp(self.clock())),
            )
            if cur.rowcount == 0:
                return None
            con.execute(
                "UPDATE triggers SET last_fired_at = ? WHERE id = ?",
                (_stamp(self.clock()), trigger_id),
            )
            return cur.lastrowid

    def end_fire(self, fire_id: int, outcome: str) -> None:
        self.vault.execute("UPDATE trigger_fires SET outcome = ? WHERE id = ?", (outcome, fire_id))

    def unfinished_fires(self) -> list[Fire]:
        """Fires whose action may not have completed (the program stopped in the middle)."""
        rows = self.vault.query(
            "SELECT id, trigger_id, dedupe_key, fired_at, outcome FROM trigger_fires"
            " WHERE outcome = 'started' ORDER BY id"
        )
        return [Fire(*r) for r in rows]

    def fired_keys(self, trigger_id: int) -> int:
        return self.vault.query(
            "SELECT COUNT(*) FROM trigger_fires WHERE trigger_id = ?", (trigger_id,)
        )[0][0]

    # ---- the Inbox ---------------------------------------------------------------------------

    def post(
        self, kind: str, title: str, body: str = "", *, trigger_id: int | None = None,
        fire_id: int | None = None,
    ) -> int | None:  # fmt: skip
        """Add an Inbox item; posting for the same fire twice does nothing (returns None)."""
        with self.vault.transaction() as con:
            cur = con.execute(
                "INSERT OR IGNORE INTO inbox (created_at, fire_id, trigger_id, kind, title, body)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (_stamp(self.clock()), fire_id, trigger_id, kind, title[:200], body[:4000]),
            )
            return cur.lastrowid if cur.rowcount else None

    def inbox(self, *, unread_only: bool = False, limit: int = 30) -> list[InboxItem]:
        where = "WHERE status = 'unread'" if unread_only else "WHERE status != 'dismissed'"
        rows = self.vault.query(
            "SELECT id, created_at, trigger_id, kind, title, body, status FROM inbox "
            f"{where} ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return [InboxItem(*r) for r in rows]

    def unread_count(self) -> int:
        return self.vault.query("SELECT COUNT(*) FROM inbox WHERE status = 'unread'")[0][0]

    def mark_read(self, item_id: int | None = None) -> int:
        with self.vault.transaction() as con:
            if item_id is None:
                cur = con.execute("UPDATE inbox SET status = 'read' WHERE status = 'unread'")
            else:
                cur = con.execute(
                    "UPDATE inbox SET status = 'read' WHERE id = ? AND status = 'unread'",
                    (item_id,),
                )
            return cur.rowcount

    def dismiss(self, item_id: int) -> bool:
        with self.vault.transaction() as con:
            cur = con.execute(
                "UPDATE inbox SET status = 'dismissed' WHERE id = ? AND status != 'dismissed'",
                (item_id,),
            )
            return cur.rowcount > 0
