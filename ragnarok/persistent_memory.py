"""Persistent memory: how Ragnarok remembers you across restarts and days, in one place.

Everything it knows lives in one encrypted file (the vault). Nothing held in RAM is the only copy:
the vector index, the profile block and the caches can all be rebuilt from the vault, so killing
the process loses nothing that was committed.

TWO TIERS
  events    Raw and short-lived (30 days by default): what was said and done. Passwords, tokens and
            API keys are scrubbed before an event is written. Events a memory cites are kept.
  memories  Curated and long-lived: one self-contained statement each, with a type (preference,
            semantic, project, episodic...), an origin (user_stated, observed, inferred), a
            confidence, an importance, the events it came from, and when it started and stopped
            being true.

WRITING  observe() -> learn()          remember()
  observe() records an event. learn() reads events that have not been learned yet and turns them
  into memories: the local model extracts statements, then for each one the code looks for similar
  memories and decides ADD, UPDATE, SUPERSEDE or NOOP (a model call is made only when a close
  neighbour exists). remember() is the explicit "remember that..." path: the same decision step
  without the extraction. Rules the code enforces, whatever the model says:
    - Never lose a fact. A NOOP or UPDATE that does not actually contain the new statement is kept
      as a new memory; a near-duplicate is dropped only if it adds no words.
    - A changed fact does not erase the old one: the old version becomes "superseded", keeps its
      dates, and stays in the history.
    - An inference can never replace something the user said (the conflict is linked, both stay).
    - The model cannot claim more certainty than the evidence: "user_stated" needs a user message.
    - A date with no year means its next occurrence, unless the user's wording is past tense.

READING  recall() and profile()
  recall() needs no model call except embedding the question (about 80 ms): full-text search (exact
  words, ids, error codes) plus vector search (meaning), merged by reciprocal-rank fusion, re-ranked
  by importance, entities, project, recency (strong only when the question has time words) and
  confidence, then checked against the vault so archived, superseded, deleted and secret memories
  never come back. profile() is a small always-on block of pinned memories, top preferences and
  project facts, so the agent knows you from the first message of a new day.

CHANGING YOUR MIND
  correct() replaces a fact (history kept). revert() brings the older version back. pin()/unpin()
  keeps a memory in the profile and exempt from archiving. archive()/restore() hides and unhides.
  forget() is a hard delete: the row, its search entries, vector, links and provenance are removed
  and only a content-free tombstone (id and time) is left. export() writes plain JSON lines, and
  refuses unless you say you accept that the file is not encrypted.

DURABILITY
  SQLCipher with WAL and synchronous=FULL; every write is one transaction. The vector index is
  derived data: changes are queued in an outbox inside the same transaction, applied afterwards,
  and on start the saved index is loaded, replayed and checked against the vault, or rebuilt.

KEEPING IT TIDY  consolidate(), prune(), maintain()
  consolidate() turns each finished week of small episodic notes into one summary memory. The model
  proposes it; code stores it only if it cites real sources and invents no names or numbers. The
  sources are untouched, so undo_summary() takes it back. prune() archives memories that are both
  low in usefulness (importance, recency, how often used, confidence) and already covered by a
  summary; pinned memories, summaries and anything without a summary are never pruned. maintain()
  runs both, plus expiry of old raw events, at most once a day. Deleting a memory also deletes any
  summary built from it.

LIMITS  Summaries group by project and week only (not by entity). Export is plain text. Nothing is
        ever archived just for being old.

Measured numbers for all of this are in docs/WORKLOG.md; the reasons for each rule are in
docs/DECISIONS.md.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ragnarok.memory.consolidate import ConsolidationReport, Consolidator
from ragnarok.memory.forget import PruneReport, prune
from ragnarok.memory.ingest import Embedder, GatewayLlm, Ingestor, IngestReport, JsonLlm
from ragnarok.memory.models import Memory, MemoryHit, MemoryType
from ragnarok.memory.profile import ProfileCache
from ragnarok.memory.service import MemoryService
from ragnarok.memory.store import MemoryStore
from ragnarok.memory.vault import Vault
from ragnarok.runtime.gateway import Gateway


@dataclass(frozen=True)
class Remembered:
    action: str  # ADD, UPDATE, SUPERSEDE or NOOP
    memory_id: int


MAINTENANCE_INTERVAL_S = 24 * 3600


@dataclass
class MaintenanceReport:
    skipped: bool = False  # ran less than a day ago
    consolidation: ConsolidationReport = field(default_factory=ConsolidationReport)
    pruned: PruneReport = field(default_factory=PruneReport)
    events_expired: int = 0


class PersistentMemory:
    """The whole memory lifecycle behind one object. Every method that touches the vault is async
    and runs the blocking work in a thread, so it is safe to call from the agent's event loop."""

    def __init__(
        self,
        vault: Vault,
        store: MemoryStore,
        service: MemoryService,
        ingestor: Ingestor,
        *,
        gateway: Gateway | None = None,
    ) -> None:
        self.vault = vault
        self.store = store
        self.service = service
        self.ingestor = ingestor
        self._profile = ProfileCache(store)
        self._gateway = gateway  # set only when this object created it, so close() can release it

    @classmethod
    def open(
        cls,
        path: str | Path,
        key: bytes,
        *,
        embedder: Embedder | None = None,
        llm: JsonLlm | None = None,
    ) -> PersistentMemory:
        """Unlock the vault, load or rebuild the index, and connect to the local models."""
        vault = Vault.open(path, key)
        owned = None
        if embedder is None or llm is None:
            owned = Gateway(timeout=300)
            embedder = embedder or owned
            llm = llm or GatewayLlm(owned)
        store = MemoryStore(vault)
        store.start()
        return cls(
            vault,
            store,
            MemoryService(store, embedder),
            Ingestor(store, embedder, llm),
            gateway=owned,
        )

    # ---- writing -----------------------------------------------------------------------------

    async def observe(
        self,
        text: str,
        *,
        kind: str = "user_message",
        source: str = "chat",
        session_id: str | None = None,
        retention_days: int = 30,
    ) -> int | None:
        """Record something that was said or done. Returns the event id (None if it was trivial)."""
        return await asyncio.to_thread(
            self.store.record_event,
            source,
            kind,
            text,
            session_id=session_id,
            retention_days=retention_days,
        )

    async def learn(self, limit: int = 40) -> IngestReport:
        """Turn events that have not been learned yet into memories."""
        return await self.ingestor.ingest_pending(limit)

    async def remember(
        self,
        text: str,
        memory_type: MemoryType = "semantic",
        *,
        importance: float = 0.8,
        event_ids: tuple[int, ...] = (),
    ) -> Remembered:
        """Save something the user asked to remember (still checks for duplicates and changes)."""
        action, memory_id = await self.ingestor.remember_explicit(
            text, memory_type, importance, event_ids
        )
        return Remembered(action, memory_id)

    # ---- reading -----------------------------------------------------------------------------

    async def recall(
        self, query: str, k: int = 8, *, project: str | None = None
    ) -> list[MemoryHit]:
        return await self.service.recall(query, k, project=project)

    async def profile(self) -> str:
        """The always-on block: what the agent knows about the user before any search."""
        text, _ = await asyncio.to_thread(self._profile.get)
        return text

    async def get(self, memory_id: int) -> Memory | None:
        return await asyncio.to_thread(self.store.get, memory_id)

    async def list(
        self, status: str | None = "active", memory_type: str | None = None, limit: int = 50
    ) -> list[Memory]:
        return await asyncio.to_thread(
            self.store.list_memories, status=status, memory_type=memory_type, limit=limit
        )

    async def history(self, memory_id: int) -> list[Memory]:
        """This memory and every earlier version it replaced, newest first."""
        return await asyncio.to_thread(self.store.history, memory_id)

    async def stats(self) -> dict[str, Any]:
        return await asyncio.to_thread(self.store.stats)

    # ---- changing your mind ------------------------------------------------------------------

    async def correct(self, memory_id: int, new_text: str) -> int:
        """The stored fact is wrong: replace it. The old version stays in the history."""
        old = await self.get(memory_id)
        if old is None or old.status != "active":
            raise ValueError(f"memory {memory_id} is not active, so it cannot be corrected")
        vector = (await self.service.embedder.embed(["search_document: " + new_text]))[0]
        return await asyncio.to_thread(
            self.store.add_memory,
            new_text,
            vector,
            memory_type=old.memory_type,
            source="correction",
            origin="user_stated",
            importance=old.importance,
            sensitivity=old.sensitivity,
            pinned=old.pinned,
            supersedes=memory_id,
        )

    async def revert(self, memory_id: int) -> int | None:
        """Bring back an older version (the one that replaced it is archived, not deleted)."""
        return await asyncio.to_thread(self.store.revert, memory_id)

    async def pin(self, memory_id: int) -> None:
        await asyncio.to_thread(self.store.set_pinned, memory_id, True)

    async def unpin(self, memory_id: int) -> None:
        await asyncio.to_thread(self.store.set_pinned, memory_id, False)

    async def archive(self, memory_id: int) -> None:
        await asyncio.to_thread(self.store.archive, memory_id)

    async def restore(self, memory_id: int) -> None:
        await asyncio.to_thread(self.store.restore, memory_id)

    async def forget(self, memory_id: int) -> bool:
        """Delete for good. Returns False if there was no such memory."""
        return await asyncio.to_thread(self.store.delete, memory_id)

    async def export(self, path: str | Path, *, allow_plaintext: bool = False) -> int:
        """Write every memory (all versions) as JSON lines. The file is NOT encrypted."""
        if not allow_plaintext:
            raise PermissionError(
                "export writes your memories as readable text, outside the encrypted vault; "
                "pass allow_plaintext=True to confirm"
            )
        rows = await asyncio.to_thread(self._export_rows)
        text = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
        await asyncio.to_thread(Path(path).write_text, text, "utf-8")
        return len(rows)

    def _export_rows(self) -> list[dict[str, Any]]:
        out = []
        for m in self.store.list_memories(status=None, limit=10**9):
            sources = self.vault.query(
                "SELECT event_id FROM memory_sources WHERE memory_id = ?", (m.id,)
            )
            entities = self.vault.query(
                "SELECT e.name FROM memory_entities me JOIN entities e ON e.id = me.entity_id"
                " WHERE me.memory_id = ?",
                (m.id,),
            )
            row = m.model_dump()
            row["event_ids"] = [r[0] for r in sources]
            row["entities"] = [r[0] for r in entities]
            out.append(row)
        return sorted(out, key=lambda r: r["id"])

    # ---- keeping it tidy ---------------------------------------------------------------------

    async def consolidate(self, limit: int = 5, **options: Any) -> ConsolidationReport:
        """Summarise finished weeks of episodic notes (at most `limit` summaries per call)."""
        consolidator = Consolidator(self.store, self.service.embedder, self.ingestor.llm, **options)
        return await consolidator.run(limit)

    async def prune(self, *, threshold: float | None = None, dry_run: bool = False) -> PruneReport:
        """Archive low-usefulness memories that a summary already covers."""
        kwargs: dict[str, Any] = {"dry_run": dry_run}
        if threshold is not None:
            kwargs["threshold"] = threshold
        return await asyncio.to_thread(prune, self.store, **kwargs)

    async def undo_summary(self, summary_id: int) -> list[int]:
        """Take a summary back: delete it and un-archive the memories it had let go of."""
        summary = await self.get(summary_id)
        if summary is None or summary.memory_type != "summary":
            raise ValueError(f"memory {summary_id} is not a summary")
        restored = []
        for source_id in await asyncio.to_thread(self.store.sources_of, summary_id):
            source = await self.get(source_id)
            if source is not None and source.status == "archived":
                await self.restore(source_id)
                restored.append(source_id)
        await self.forget(summary_id)
        return restored

    async def maintain(self, *, force: bool = False) -> MaintenanceReport:
        """Consolidate, prune and expire old events; at most once a day unless forced."""
        now = await asyncio.to_thread(self.store.now_iso)
        last = await asyncio.to_thread(self.store.meta_get, "last_maintenance")
        if not force and last:
            gap = (datetime.fromisoformat(now) - datetime.fromisoformat(last)).total_seconds()
            if gap < MAINTENANCE_INTERVAL_S:
                return MaintenanceReport(skipped=True)
        report = MaintenanceReport()
        report.consolidation = await self.consolidate()
        report.pruned = await self.prune()
        report.events_expired = await asyncio.to_thread(self.store.expire_events)
        await asyncio.to_thread(self.store.meta_set, "last_maintenance", now)
        return report

    # ---- lifecycle ---------------------------------------------------------------------------

    async def checkpoint(self) -> None:
        """Save the search index into the vault so the next start loads instead of rebuilding."""
        await asyncio.to_thread(self.store.checkpoint_index)
        await asyncio.to_thread(self.vault.checkpoint)

    async def close(self) -> None:
        await self.service.flush()
        await asyncio.to_thread(self.vault.close)
        if self._gateway is not None:
            await self._gateway.aclose()
