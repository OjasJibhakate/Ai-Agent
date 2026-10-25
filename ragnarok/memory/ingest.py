"""Turn raw events into curated memories (docs/MEMORY.md section 3.2).

events -> extract (LLM, JSON schema) -> for each candidate: dedupe, look up similar memories,
decide ADD / UPDATE / SUPERSEDE / NOOP -> apply with provenance. Only candidates that have close
neighbours cost a second model call; everything else is a plain ADD.

Safety rules, enforced in code and not left to the model:
  - an unusable decision (unknown action, target not a neighbour) becomes ADD, never a delete;
  - an inferred memory can never supersede a user-stated one (the conflict is linked instead);
  - if the model is unreachable the events stay pending and are retried later.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

import numpy as np
from pydantic import BaseModel, Field, ValidationError

from ragnarok.memory.models import Memory
from ragnarok.memory.store import MemoryStore
from ragnarok.runtime.gateway import GatewayError

PROMPTS = Path(__file__).parent.parent / "runtime" / "prompts"
DOC_PREFIX = "search_document: "

EXTRACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "memories": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "memory_type": {
                        "type": "string",
                        "enum": ["episodic", "semantic", "preference", "procedural", "project"],
                    },
                    "origin": {"type": "string", "enum": ["user_stated", "observed", "inferred"]},
                    "importance": {"type": "number"},
                    "sensitivity": {"type": "string", "enum": ["normal", "private", "secret"]},
                    "entities": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"name": {"type": "string"}, "kind": {"type": "string"}},
                            "required": ["name", "kind"],
                        },
                    },
                },
                "required": ["content", "memory_type", "origin"],
            },
        }
    },
    "required": ["memories"],
}
DECIDE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["ADD", "UPDATE", "SUPERSEDE", "NOOP"]},
        "target_id": {"type": ["integer", "null"]},
        "reason": {"type": "string"},
    },
    "required": ["action", "target_id"],
}


class Embedder(Protocol):
    async def embed(self, texts: list[str]) -> np.ndarray: ...


class JsonLlm(Protocol):
    async def json(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> dict: ...


class GatewayLlm:
    """Adapts the model gateway to the `JsonLlm` protocol."""

    def __init__(self, gateway: Any, *, num_predict: int = 512) -> None:
        self.gateway = gateway
        self.num_predict = num_predict

    async def json(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> dict:
        return await self.gateway.chat_json(messages, schema, num_predict=self.num_predict)


class Entity(BaseModel):
    name: str
    kind: str = "thing"


class Candidate(BaseModel):
    content: str
    memory_type: Literal["episodic", "semantic", "preference", "procedural", "project"]
    origin: Literal["user_stated", "observed", "inferred"] = "user_stated"
    importance: float = Field(default=0.5, ge=0, le=1)
    sensitivity: Literal["normal", "private", "secret"] = "normal"
    entities: list[Entity] = Field(default_factory=list)


class Decision(BaseModel):
    action: Literal["ADD", "UPDATE", "SUPERSEDE", "NOOP"]
    target_id: int | None = None
    reason: str = ""


@dataclass
class IngestReport:
    events: int = 0
    candidates: int = 0
    added: int = 0
    updated: int = 0
    superseded: int = 0
    noop: int = 0
    conflicts: int = 0  # inferred vs stated: linked, not overwritten
    invalid_decisions: int = 0  # unusable model output, fell back to ADD
    deferred: int = 0  # events left pending because the model failed


def _prompt(name: str) -> str:
    return (PROMPTS / name).read_text(encoding="utf-8")


def _role(kind: str) -> str:
    return {"user_message": "user", "assistant_message": "assistant", "tool_result": "tool"}.get(
        kind, kind
    )


def format_events(events: Sequence[dict]) -> str:
    return "\n".join(f"[e{e['id']} {_role(e['kind'])} {e['ts']}] {e['payload']}" for e in events)


class Ingestor:
    def __init__(
        self,
        store: MemoryStore,
        embedder: Embedder,
        llm: JsonLlm,
        *,
        sim_floor: float = 0.6,
        dup_threshold: float = 0.97,
        batch_size: int = 8,
    ) -> None:
        self.store = store
        self.embedder = embedder
        self.llm = llm
        self.sim_floor = sim_floor  # below this, neighbours are unrelated: plain ADD
        self.dup_threshold = dup_threshold  # above this, the memory is already stored
        self.batch_size = batch_size
        self._system = _prompt("system.txt")

    # ---- batch path: events -> memories ------------------------------------------------------

    async def ingest_pending(self, limit: int = 40) -> IngestReport:
        report = IngestReport()
        events = await asyncio.to_thread(self.store.pending_events, limit)
        for start in range(0, len(events), self.batch_size):
            batch = events[start : start + self.batch_size]
            report.events += len(batch)
            if not any(
                e["kind"] in ("user_message", "tool_result", "file", "trigger") for e in batch
            ):
                # assistant-only text can't produce a user-confirmed fact
                await asyncio.to_thread(self.store.mark_ingested, [e["id"] for e in batch])
                continue
            try:
                candidates = await self._extract(batch)
                event_ids = [e["id"] for e in batch]
                for cand in candidates:
                    report.candidates += 1
                    await self.process(cand, event_ids, report, kinds={e["kind"] for e in batch})
            except GatewayError:
                report.deferred += len(batch)  # leave pending; the next run retries
                continue
            await asyncio.to_thread(self.store.mark_ingested, [e["id"] for e in batch])
        return report

    async def _extract(self, batch: Sequence[dict]) -> list[Candidate]:
        now = datetime.fromisoformat(batch[-1]["ts"])
        today = now.strftime("%Y-%m-%d (%A)")

        task = (
            _prompt("extract.txt")
            .replace("{today}", today)
            .replace("{events}", format_events(batch))
        )
        data = await self.llm.json(
            [{"role": "system", "content": self._system}, {"role": "user", "content": task}],
            EXTRACT_SCHEMA,
        )
        out = []
        for raw in data.get("memories", []):
            try:
                cand = Candidate.model_validate(raw)
            except ValidationError:
                continue  # one malformed item should not lose the others
            if not cand.content.strip():
                continue
            source = _closest_event(cand.content, batch)
            cand.content = roll_past_dates(cand.content, source, now.date())
            out.append(cand)
        return out

    # ---- single-candidate path (also used for explicit "remember ..." saves) -----------------

    async def remember_explicit(
        self, text: str, memory_type: str = "semantic", importance: float = 0.8
    ) -> tuple[str, int]:
        """Save what the user asked to remember, still checking for duplicates and changes."""
        cand = Candidate(
            content=text, memory_type=memory_type, origin="user_stated", importance=importance
        )  # type: ignore[arg-type]
        report = IngestReport(candidates=1)
        memory_id = await self.process(cand, [], report, kinds={"user_message"})
        action = (
            "ADD"
            if report.added
            else "SUPERSEDE"
            if report.superseded
            else "UPDATE"
            if report.updated
            else "NOOP"
        )
        return action, memory_id

    async def process(
        self, cand: Candidate, event_ids: Sequence[int], report: IngestReport, *, kinds: set[str]
    ) -> int:
        origin = _clamp_origin(cand.origin, kinds)
        vec = (await self.embedder.embed([DOC_PREFIX + cand.content]))[0]
        neighbours = await asyncio.to_thread(self.store.similar, vec, 5)
        related = [(m, s) for m, s in neighbours if s >= self.sim_floor]

        def add(**extra: Any) -> int:
            return self.store.add_memory(
                cand.content,
                vec,
                memory_type=cand.memory_type,
                source="ingest",
                origin=origin,
                importance=cand.importance,
                sensitivity=cand.sensitivity,
                entities=[(e.name, e.kind) for e in cand.entities],
                event_ids=list(event_ids),
                **extra,
            )

        if related and related[0][1] >= self.dup_threshold:
            target = related[0][0]
            await asyncio.to_thread(self.store.reinforce, target.id, list(event_ids))
            report.noop += 1
            return target.id
        if not related:
            report.added += 1
            return await asyncio.to_thread(add)

        decision = await self._decide(cand, [m for m, _ in related])
        valid_ids = {m.id for m, _ in related}
        if decision.action in ("UPDATE", "SUPERSEDE") and decision.target_id not in valid_ids:
            report.invalid_decisions += 1
            decision = Decision(action="ADD")
        target = next((m for m, _ in related if m.id == decision.target_id), None)

        if decision.action == "NOOP":
            report.noop += 1
            if target:
                await asyncio.to_thread(self.store.reinforce, target.id, list(event_ids))
                return target.id
            return related[0][0].id
        if decision.action == "UPDATE" and target:
            if _adds_information(cand.content, target.content):
                decision = Decision(action="SUPERSEDE", target_id=target.id)  # keep the new detail
            else:
                await asyncio.to_thread(self.store.reinforce, target.id, list(event_ids))
                report.updated += 1
                return target.id
        if decision.action == "SUPERSEDE" and target:
            if origin == "inferred" and target.origin == "user_stated":
                new_id = await asyncio.to_thread(add)
                await asyncio.to_thread(self.store.link, new_id, target.id, "contradicts")
                report.added += 1
                report.conflicts += 1
                return new_id
            new_id = await asyncio.to_thread(add, supersedes=target.id)
            report.superseded += 1
            return new_id
        report.added += 1
        return await asyncio.to_thread(add)

    async def _decide(self, cand: Candidate, similar: Sequence[Memory]) -> Decision:
        listing = "\n".join(f"[m:{m.id} {m.created_at[:10]}] {m.content}" for m in similar)
        task = (
            _prompt("decide.txt").replace("{candidate}", cand.content).replace("{similar}", listing)
        )
        try:
            data = await self.llm.json(
                [{"role": "system", "content": self._system}, {"role": "user", "content": task}],
                DECIDE_SCHEMA,
            )
            return Decision.model_validate(data)
        except (GatewayError, ValidationError):
            return Decision(action="ADD")  # when unsure, keep both: nothing is lost


_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_PAST_WORDS = re.compile(
    r"\b(went|was|were|had|did|visited|met|bought|moved|started|finished|attended|last|ago|"
    r"yesterday|earlier|used to)\b",
    re.IGNORECASE,
)


def _closest_event(content: str, batch: Sequence[dict]) -> str:
    """The event text a candidate most likely came from (candidates don't cite their source)."""
    words = _tokens(content)
    best = max(
        batch, key=lambda e: len(words & _tokens(e["payload"])) / (len(_tokens(e["payload"])) or 1)
    )
    return best["payload"]


def roll_past_dates(content: str, source_text: str, today: date) -> str:
    """Move a date the user gave without a year to its next occurrence.

    The 4B model often fills in the current year even when that date has already passed
    ("anniversary on 14 February" became 2026-02-14 in October 2026). An explicit year in the
    user's own words is always kept. The model labels nearly every dated item "episodic", so
    memory type can't tell plans from history; past-tense words in the user's text do
    ("I went to Goa on 5 March"). This is a heuristic, not a parser.
    """
    if _PAST_WORDS.search(source_text):
        return content

    def fix(match: re.Match[str]) -> str:
        year, month, day = (int(g) for g in match.groups())
        if str(year) in source_text:
            return match.group(0)
        try:
            found = date(year, month, day)
            while found < today:
                found = found.replace(year=found.year + 1)
        except ValueError:  # not a real date, or 29 February in a non-leap year
            return match.group(0)
        return found.isoformat()

    return _ISO_DATE.sub(fix, content)


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.lower()))


def _adds_information(new: str, stored: str) -> bool:
    """True when the new statement has words the stored one lacks (a changed or extra detail).

    The model sometimes calls a changed value an UPDATE ("after 15:00 now" vs "after 14:00").
    Reinforcing the old memory would then drop the new value, so such an UPDATE is applied as
    a SUPERSEDE: the new version is kept and the old one stays in the history.
    """
    return not _tokens(new) <= _tokens(stored)


def _clamp_origin(claimed: str, kinds: set[str]) -> str:
    """The model may not claim more certainty than the evidence allows."""
    if claimed == "user_stated" and "user_message" not in kinds:
        return "observed" if kinds & {"tool_result", "file", "trigger"} else "inferred"
    if claimed == "observed" and not kinds & {"tool_result", "file", "trigger", "user_message"}:
        return "inferred"
    return claimed
