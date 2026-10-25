"""Forgetting (docs/MEMORY.md section 6): hide what is no longer useful, never what is only old.

usefulness = 0.35*importance + 0.25*recency (half-life 30 days, from last use or creation)
           + 0.25*how often it was used (relative to the most used memory) + 0.15*confidence

A memory is archived only when ALL of these hold: it is active, not pinned, not itself a summary,
its usefulness is below the threshold, and an active summary already covers it. So a fact is never
dropped just for being old: a preference you stated or a project fact has no summary and stays; an
old episodic note is archived only after its gist was kept in a summary. Archiving hides it from
search and the index but keeps the row, so `restore` brings it back.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

from ragnarok.memory.models import Memory
from ragnarok.memory.store import MemoryStore

HALF_LIFE_DAYS = 30.0
DEFAULT_THRESHOLD = 0.35


def usefulness(memory: Memory, now: datetime, max_access: int) -> float:
    last_used = datetime.fromisoformat(memory.last_accessed_at or memory.created_at)
    age_days = max(0.0, (now - last_used).total_seconds() / 86400)
    recency = 0.5 ** (age_days / HALF_LIFE_DAYS)
    use = math.log1p(memory.access_count) / math.log1p(max_access) if max_access > 0 else 0.0
    return 0.35 * memory.importance + 0.25 * recency + 0.25 * use + 0.15 * memory.confidence


@dataclass
class PruneReport:
    examined: int = 0
    archived: list[int] = field(default_factory=list)
    kept: int = 0


def prune(
    store: MemoryStore, *, threshold: float = DEFAULT_THRESHOLD, dry_run: bool = False
) -> PruneReport:
    """Archive low-usefulness memories that a summary already covers. Safe to repeat."""
    report = PruneReport()
    covered = store.covered_ids()
    if not covered:
        return report
    now = store.clock()
    max_access = store.max_access_count()
    for memory in store.list_memories(status="active", limit=10**9):
        if memory.id not in covered or memory.pinned or memory.memory_type == "summary":
            continue
        report.examined += 1
        if usefulness(memory, now, max_access) < threshold:
            report.archived.append(memory.id)
            if not dry_run:
                store.archive(memory.id)
        else:
            report.kept += 1
    return report
