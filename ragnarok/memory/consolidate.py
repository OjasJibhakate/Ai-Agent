"""Consolidation (docs/MEMORY.md section 5): turn a week of small episodic notes into one summary.

The local model writes a proposed summary; code decides whether it is kept. A summary is stored
only if every check passes:
  - it cites at least two memories, and every cited id is really in the group;
  - it is short;
  - it introduces no capitalised name or number that is absent from the memories it cites
    (a hallucinated detail would otherwise become a "memory" later retrieved as fact).
A stored summary is an ordinary memory of type "summary": origin "inferred", confidence no higher
than the lowest of its sources, linked `derived_from` to each source. The sources are never
modified here, so deleting the summary undoes everything. Each summary is its own transaction, so
a crash or a failed model call between groups loses nothing and the next run picks up the rest.
"""

from __future__ import annotations

import asyncio
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ragnarok.memory.ingest import Embedder, JsonLlm
from ragnarok.memory.models import Memory
from ragnarok.memory.store import MemoryStore
from ragnarok.runtime.gateway import GatewayError

PROMPTS = Path(__file__).parent.parent / "runtime" / "prompts"
SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "cited_ids": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["summary", "cited_ids"],
}
MAX_SUMMARY_CHARS = 400
# One tokenizer for the summary and its sources, so "w0", "mp3" or "B2B" are one word on both sides.
_TOKEN = re.compile(r"[A-Za-z0-9][\w:.,/'-]*")


@dataclass
class Group:
    key: tuple[int | None, str]  # (project id, ISO year-week)
    members: list[Memory]


@dataclass
class ConsolidationReport:
    groups: int = 0  # groups big and old enough to summarise
    summarized: int = 0
    too_small: int = 0
    rejected: dict[str, int] = field(default_factory=dict)
    errors: int = 0  # model unreachable or unusable output; nothing stored, retried next run
    summary_ids: list[int] = field(default_factory=list)


def _tokens(text: str) -> set[str]:
    words = {t.lower().strip(".,:;/-'") for t in _TOKEN.findall(text)}
    return words | {part for w in words for part in w.split("-") if part}


def novel_terms(summary: str, source_text: str) -> list[str]:
    """Names, numbers and digit words (mp4, w3) in the summary that the sources never use."""
    known = _tokens(source_text)
    novel = []
    for m in _TOKEN.finditer(summary):
        tok = m.group()
        starts_sentence = m.start() == 0 or summary[: m.start()].rstrip().endswith((".", "!", "?"))
        has_digit = any(ch.isdigit() for ch in tok)
        if has_digit or (tok[0].isupper() and not starts_sentence):
            cleaned = tok.lower().strip(".,:;/-'")
            if cleaned.endswith("'s"):
                cleaned = cleaned[:-2]
            parts = [p for p in cleaned.split("-") if p]  # "30-day" is fine if "30" and "day" are
            if cleaned not in known and not (len(parts) > 1 and all(p in known for p in parts)):
                novel.append(tok.strip(".,:;/-'"))
    return novel


def validate(summary: str, cited: list[int], group_ids: set[int], source_text: str) -> str | None:
    """Return why a proposed summary must be rejected, or None if it is acceptable."""
    text = summary.strip()
    if not text or len(text) > MAX_SUMMARY_CHARS:
        return "length"
    if len(set(cited)) < 2:
        return "too_few_sources"
    if not set(cited) <= group_ids:
        return "unknown_id"
    if novel_terms(text, source_text):
        return "new_detail"
    return None


class Consolidator:
    def __init__(
        self,
        store: MemoryStore,
        embedder: Embedder,
        llm: JsonLlm,
        *,
        min_group: int = 4,
        max_group: int = 12,
        min_age_days: float = 7.0,
    ) -> None:
        self.store = store
        self.embedder = embedder
        self.llm = llm
        self.min_group = min_group
        self.max_group = max_group
        self.min_age_days = min_age_days
        self._system = (PROMPTS / "system.txt").read_text(encoding="utf-8")
        self._task = (PROMPTS / "consolidate.txt").read_text(encoding="utf-8")

    def plan(self) -> tuple[list[Group], int]:
        """Uncovered episodic memories grouped by project and week: as (groups, too_small)."""
        covered = self.store.covered_ids()
        now = self.store.clock()
        buckets: dict[tuple[int | None, str], list[Memory]] = defaultdict(list)
        for m in self.store.list_memories(status="active", memory_type="episodic", limit=10**9):
            if m.id in covered or m.pinned:
                continue
            when = datetime.fromisoformat(m.valid_from or m.created_at)
            year, week, _ = when.isocalendar()
            buckets[(m.project_id, f"{year}-W{week:02d}")].append(m)
        groups, small = [], 0
        for key, members in sorted(buckets.items(), key=lambda kv: str(kv[0])):
            newest = max(datetime.fromisoformat(m.valid_from or m.created_at) for m in members)
            if (now - newest).total_seconds() / 86400 < self.min_age_days:
                continue  # the week is still filling up
            if len(members) < self.min_group:
                small += 1
                continue
            members.sort(key=lambda m: m.id)
            groups.append(Group(key, members[: self.max_group]))
        return groups, small

    async def run(self, limit: int = 5) -> ConsolidationReport:
        report = ConsolidationReport()
        groups, report.too_small = await asyncio.to_thread(self.plan)
        report.groups = len(groups)
        for group in groups[:limit]:
            await self._summarize(group, report)
        return report

    async def _summarize(self, group: Group, report: ConsolidationReport) -> None:
        listing = "\n".join(f"[m:{m.id} {m.created_at[:10]}] {m.content}" for m in group.members)
        task = self._task.replace("{memories}", listing)
        try:
            data = await self.llm.json(
                [{"role": "system", "content": self._system}, {"role": "user", "content": task}],
                SUMMARY_SCHEMA,
            )
            summary = str(data["summary"]).strip()
            cited = [int(i) for i in data["cited_ids"]]
        except (GatewayError, KeyError, TypeError, ValueError):
            report.errors += 1
            return
        by_id = {m.id: m for m in group.members}
        reason = validate(
            summary, cited, set(by_id), " ".join(by_id[i].content for i in cited if i in by_id)
        )
        if reason:
            report.rejected[reason] = report.rejected.get(reason, 0) + 1
            return
        sources = [by_id[i] for i in sorted(set(cited))]
        vector = (await self.embedder.embed(["search_document: " + summary]))[0]
        entities: list[tuple[str, str]] = []
        for m in sources:
            for ent in await asyncio.to_thread(self.store.entities_of_memory, m.id):
                if ent not in entities:
                    entities.append(ent)

        def store_it() -> int:
            sid = self.store.add_memory(
                summary,
                vector,
                memory_type="summary",
                source="consolidation",
                origin="inferred",  # a model-written digest is never a stated fact
                confidence=min(m.confidence for m in sources),
                importance=max(m.importance for m in sources),
                project=self.store.project_name(group.key[0]),
                entities=entities,
            )
            for m in sources:
                self.store.link(sid, m.id, "derived_from")
            return sid

        report.summary_ids.append(await asyncio.to_thread(store_it))
        report.summarized += 1
