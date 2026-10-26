"""Contextual proactivity (FR4.2): suggestions from what the agent remembers and what is going on.

No model call: the suggestions come from rules over the vault, so they cost almost nothing, cannot
be talked into anything, and run while the owner is busy. Four sources:

- an unfinished plan ("pick up where you stopped");
- a remembered date or deadline in the next few days that has no reminder yet;
- a remembered habit ("every morning I ...") that has no schedule yet;
- the project the owner was last working on, in the morning.

A suggestion is only ever a note in the Inbox. It never runs anything: acting on one is the
owner's decision, through the normal permission checks. Each is shown at most once a week (a
`meta` row remembers when), and a run posts at most three, so it can never become noise.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from ragnarok.memory.store import MemoryStore
from ragnarok.planning.store import PlanStore
from ragnarok.triggers.store import TriggerStore

MAX_PER_RUN = 3
REPEAT_AFTER = timedelta(days=7)
LOOKAHEAD_DAYS = 3
RECENT_DAYS = 21  # a remembered date only matters if it was noted fairly recently

DEADLINE_WORDS = re.compile(
    r"\b(deadline|due|submit|submission|exam|interview|meeting|demo|presentation|appointment|"
    r"flight|viva)\b",
    re.I,
)
HABIT_WORDS = re.compile(
    r"\b(every (morning|evening|day|night|week|weekday|monday|tuesday|wednesday|thursday|friday)"
    r"|each (morning|evening|day|week)|daily|weekly)\b",
    re.I,
)
ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
RELATIVE = {"today": 0, "tonight": 0, "tomorrow": 1}


@dataclass(frozen=True)
class Suggestion:
    key: str  # stable id, so the same idea is not repeated
    title: str
    body: str


def _key(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:12]


def _dates_in(text: str, anchor: date) -> list[date]:
    found: list[date] = []
    for y, m, d in ISO_DATE.findall(text):
        try:
            found.append(date(int(y), int(m), int(d)))
        except ValueError:
            continue
    lowered = text.lower()
    for word, days in RELATIVE.items():
        if re.search(rf"\b{word}\b", lowered):
            found.append(anchor + timedelta(days=days))
    return found


class Proactive:
    """Looks at the vault and posts suggestions to the Inbox."""

    def __init__(
        self,
        store: MemoryStore,
        plans: PlanStore,
        triggers: TriggerStore,
        *,
        clock=datetime.now,
    ) -> None:
        self.store = store
        self.plans = plans
        self.triggers = triggers
        self.clock = clock

    # ---- what could be suggested ---------------------------------------------------------------

    def candidates(self, now: datetime | None = None) -> list[Suggestion]:
        now = now or self.clock()
        found: list[Suggestion] = []
        found += self._unfinished_plans()
        found += self._upcoming_dates(now)
        found += self._habits()
        found += self._last_project(now)
        return found

    def _unfinished_plans(self) -> list[Suggestion]:
        return [
            Suggestion(
                _key("plan", row.id),
                f"Unfinished: {row.goal[:70]}",
                "This plan stopped before it was done. Type /resume to carry on where it stopped.",
            )
            for row in self.plans.unfinished()[:1]
        ]

    def _upcoming_dates(self, now: datetime) -> list[Suggestion]:
        today = now.date()
        reminders = " ".join(t.payload.lower() for t in self.triggers.list())
        out: list[Suggestion] = []
        for memory in self.store.list_memories(limit=200):
            if memory.sensitivity != "normal" or not DEADLINE_WORDS.search(memory.content):
                continue
            if _age_days(memory.created_at, now) > RECENT_DAYS:
                continue
            for day in _dates_in(memory.content, date.fromisoformat(memory.created_at[:10])):
                if not today <= day <= today + timedelta(days=LOOKAHEAD_DAYS):
                    continue
                words = [w for w in re.findall(r"[a-z]{5,}", memory.content.lower())]
                if any(w in reminders for w in words[:6]):
                    break  # a reminder about it already exists
                when = "today" if day == today else f"on {day:%a %d %b}"
                out.append(
                    Suggestion(
                        _key("date", str(memory.id), str(day)),
                        f"You noted something for {when}",
                        f"{memory.content}\nWant a reminder? Say: remind me about it.",
                    )
                )
                break
        return out[:2]

    def _habits(self) -> list[Suggestion]:
        scheduled = " ".join(t.payload.lower() for t in self.triggers.list())
        out: list[Suggestion] = []
        for memory in self.store.list_memories(limit=200):
            if memory.memory_type not in ("preference", "procedural", "semantic"):
                continue
            if memory.sensitivity != "normal" or not HABIT_WORDS.search(memory.content):
                continue
            words = re.findall(r"[a-z]{5,}", memory.content.lower())
            if any(w in scheduled for w in words[:6]):
                continue
            out.append(
                Suggestion(
                    _key("habit", str(memory.id)),
                    "Want me to do this for you on a schedule?",
                    f"You told me: {memory.content}\nSay 'schedule it' and I will set it up.",
                )
            )
        return out[:1]

    def _last_project(self, now: datetime) -> list[Suggestion]:
        if not 5 <= now.hour < 12:
            return []
        for memory in self.store.list_memories(memory_type="project", limit=1):
            if _age_days(memory.created_at, now) <= 14 and memory.sensitivity == "normal":
                return [
                    Suggestion(
                        _key("project", str(memory.id), f"{now:%Y-%m-%d}"),
                        "Last time you were working on this",
                        f"{memory.content}\nShall we carry on?",
                    )
                ]
        return []

    # ---- posting -------------------------------------------------------------------------------

    def run_once(self, now: datetime | None = None) -> list[Suggestion]:
        """Post the suggestions that are new (and not shown in the last week). Returns them."""
        now = now or self.clock()
        posted: list[Suggestion] = []
        for suggestion in self.candidates(now):
            if len(posted) >= MAX_PER_RUN:
                break
            meta_key = f"suggested:{suggestion.key}"
            last = self.store.meta_get(meta_key)
            if last and now - datetime.fromisoformat(last) < REPEAT_AFTER:
                continue
            self.store.meta_set(meta_key, now.replace(microsecond=0).isoformat())
            self.triggers.post("suggestion", suggestion.title, suggestion.body)
            posted.append(suggestion)
        return posted


def _age_days(stamp: str, now: datetime) -> float:
    try:
        made = datetime.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return 0.0
    return (now.replace(tzinfo=None) - made).total_seconds() / 86400
