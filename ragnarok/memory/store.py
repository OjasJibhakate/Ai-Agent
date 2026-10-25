"""Synchronous memory core: write memories, keep the vector index consistent, search.

See docs/MEMORY.md sections 3-4.

Everything here blocks, so async callers go through `service.MemoryService`, which runs it
in a thread.
The store never talks to a model: callers pass in vectors.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

import numpy as np

from ragnarok.memory.index import ExactIndex, TurbovecIndex, VectorIndex, normalize
from ragnarok.memory.models import Memory, MemoryHit, MemoryType, Origin, Sensitivity
from ragnarok.memory.vault import Vault

EMBED_DIM = 768
INDEX_NAME = "memories"
RRF_K = 60
CANDIDATES = 30
BASE_CONFIDENCE: dict[str, float] = {"user_stated": 0.95, "observed": 0.85, "inferred": 0.7}

_STOP = frozenset(
    "a an and are as at be by did do does for from how i in is it me my of on or so that the this "
    "to was what when where which who why with you your".split()
)
_TIME_WORDS = re.compile(
    r"\b(yesterday|today|tonight|recent(ly)?|last (week|month|night|time)"
    r"|this (week|morning)|ago)\b",
    re.IGNORECASE,
)
_MEMORY_COLUMNS = (
    "id, content, memory_type, origin, confidence, importance, source, project_id, sensitivity, "
    "status, supersedes_id, valid_from, valid_to, pinned, created_at, last_accessed_at,"
    " access_count"
)


def utcnow() -> datetime:
    return datetime.now(UTC)


def _stamp(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def content_hash(text: str) -> str:
    normalized = " ".join(text.lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def fts_query(text: str) -> str | None:
    """Turn free text into a safe FTS5 query: quoted tokens joined by OR."""
    tokens = [t for t in re.findall(r"\w+", text.lower()) if len(t) > 1 and t not in _STOP]
    return " OR ".join(f'"{t}"' for t in dict.fromkeys(tokens)) or None


class MemoryStore:
    def __init__(
        self,
        vault: Vault,
        *,
        dim: int = EMBED_DIM,
        model: str = "nomic-embed-text",
        index_kind: str = "turbovec",
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.vault = vault
        self.dim = dim
        self.model = model
        self.index_kind = index_kind
        self.clock = clock
        self.index: VectorIndex = self._new_index()

    # ---- index lifecycle -------------------------------------------------------------------

    def _new_index(self) -> VectorIndex:
        if self.index_kind == "exact":
            return ExactIndex(self.dim)
        return TurbovecIndex(self.dim, bit_width=4)

    def start(self) -> str:
        """Load or rebuild the vector index and reconcile it with the vault. Returns which."""
        how = "rebuilt"
        row = self.vault.query(
            "SELECT data, dim, outbox_id FROM index_blobs WHERE name = ?", (INDEX_NAME,)
        )
        if row and self.index_kind == "turbovec" and row[0][1] == self.dim:
            try:
                self.index = TurbovecIndex.from_bytes(row[0][0])
                self._replay_outbox_after(row[0][2])
                how = "loaded"
            except Exception:  # corrupt blob: fall back to the ground truth
                how = "rebuilt"
        if how == "rebuilt" or len(self.index) != self._active_vector_count():
            self.rebuild_index()
            how = "rebuilt"
        self.vault.execute(
            "UPDATE index_outbox SET applied_at = ? WHERE applied_at IS NULL", (self._now(),)
        )
        return how

    def rebuild_index(self) -> None:
        self.index = self._new_index()
        rows = self.vault.query(
            "SELECT e.memory_id, e.vector FROM embeddings e JOIN memories m ON m.id = e.memory_id "
            "WHERE m.status = 'active' ORDER BY e.memory_id"
        )
        if rows:
            ids = np.array([r[0] for r in rows], dtype=np.uint64)
            vecs = np.stack([np.frombuffer(r[1], dtype="<f4") for r in rows])
            self.index.add(ids, vecs)

    def _active_vector_count(self) -> int:
        return self.vault.query(
            "SELECT count(*) FROM embeddings e JOIN memories m ON m.id = e.memory_id "
            "WHERE m.status = 'active'"
        )[0][0]

    def _replay_outbox_after(self, outbox_id: int) -> None:
        rows = self.vault.query(
            "SELECT id, op, memory_id FROM index_outbox WHERE id > ? ORDER BY id", (outbox_id,)
        )
        self._apply(rows)

    def _apply_pending(self) -> None:
        rows = self.vault.query(
            "SELECT id, op, memory_id FROM index_outbox WHERE applied_at IS NULL ORDER BY id"
        )
        self._apply(rows)
        if rows:
            with self.vault.transaction() as con:
                con.executemany(
                    "UPDATE index_outbox SET applied_at = ? WHERE id = ?",
                    [(self._now(), r[0]) for r in rows],
                )

    def _apply(self, rows: Sequence[tuple]) -> None:
        for _, op, memory_id in rows:
            if op == "remove":
                self.index.remove([memory_id])
                continue
            vec = self.vault.query(
                "SELECT e.vector FROM embeddings e JOIN memories m ON m.id = e.memory_id "
                "WHERE e.memory_id = ? AND m.status = 'active'",
                (memory_id,),
            )
            if vec:  # the memory may have been removed again after this entry was queued
                self.index.add(
                    np.array([memory_id], dtype=np.uint64),
                    np.frombuffer(vec[0][0], dtype="<f4")[None, :],
                )

    def checkpoint_index(self) -> None:
        """Save the serialized index into the vault (D-020). Only Turbovec can be serialized."""
        if not isinstance(self.index, TurbovecIndex):
            return
        last = self.vault.query("SELECT coalesce(max(id), 0) FROM index_outbox")[0][0]
        with self.vault.transaction() as con:
            con.execute(
                "INSERT OR REPLACE INTO index_blobs"
                "(name, bits, dim, count, data, updated_at, outbox_id) VALUES (?,?,?,?,?,?,?)",
                (
                    INDEX_NAME,
                    self.index.bit_width,
                    self.dim,
                    len(self.index),
                    self.index.to_bytes(),
                    self._now(),
                    last,
                ),
            )

    # ---- writing ---------------------------------------------------------------------------

    def _now(self) -> str:
        return _stamp(self.clock())

    def add_memory(
        self,
        content: str,
        vector: np.ndarray,
        *,
        memory_type: MemoryType,
        source: str,
        origin: Origin = "user_stated",
        importance: float = 0.5,
        project: str | None = None,
        sensitivity: Sensitivity = "normal",
        pinned: bool = False,
        valid_from: str | None = None,
        entities: Sequence[tuple[str, str]] = (),
        tags: Sequence[str] = (),
        event_ids: Sequence[int] = (),
    ) -> int:
        """Store a memory and its vector. An identical active memory is returned, not duplicated."""
        content = content.strip()
        if not content:
            raise ValueError("memory content is empty")
        vec = normalize(vector).reshape(-1)
        if vec.shape[0] != self.dim:
            raise ValueError(f"vector has dim {vec.shape[0]}, expected {self.dim}")
        digest = content_hash(content)
        existing = self.vault.query(
            "SELECT id FROM memories WHERE content_hash = ? AND status = 'active'", (digest,)
        )
        if existing:
            return existing[0][0]
        now = self._now()
        with self.vault.transaction() as con:
            project_id = None
            if project:
                con.execute("INSERT OR IGNORE INTO projects(name) VALUES (?)", (project,))
                project_id = con.execute(
                    "SELECT id FROM projects WHERE name = ?", (project,)
                ).fetchone()[0]
            memory_id = con.execute(
                "INSERT INTO memories(content, memory_type, origin, confidence, importance, source,"
                " project_id, sensitivity, pinned, valid_from, created_at, content_hash)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    content,
                    memory_type,
                    origin,
                    BASE_CONFIDENCE[origin],
                    importance,
                    source,
                    project_id,
                    sensitivity,
                    int(pinned),
                    valid_from,
                    now,
                    digest,
                ),
            ).lastrowid
            con.execute(
                "INSERT INTO embeddings(memory_id, model, dim, vector) VALUES (?,?,?,?)",
                (memory_id, self.model, self.dim, vec.astype("<f4").tobytes()),
            )
            for name, kind in entities:
                con.execute(
                    "INSERT OR IGNORE INTO entities(name, kind) VALUES (?, ?)", (name, kind)
                )
                entity_id = con.execute(
                    "SELECT id FROM entities WHERE name = ?", (name,)
                ).fetchone()[0]
                con.execute(
                    "INSERT OR IGNORE INTO memory_entities VALUES (?, ?)", (memory_id, entity_id)
                )
            con.executemany(
                "INSERT OR IGNORE INTO memory_tags VALUES (?, ?)", [(memory_id, t) for t in tags]
            )
            con.executemany(
                "INSERT INTO memory_sources VALUES (?, ?)", [(memory_id, e) for e in event_ids]
            )
            con.execute(
                "INSERT INTO index_outbox(op, memory_id, created_at) VALUES ('add', ?, ?)",
                (memory_id, now),
            )
        self._apply_pending()
        return memory_id

    def touch(self, ids: Sequence[int]) -> None:
        """Record that memories were used in an answer (feeds forgetting later)."""
        now = self._now()
        with self.vault.transaction() as con:
            con.executemany(
                "UPDATE memories SET last_accessed_at = ?, access_count = access_count + 1"
                " WHERE id = ?",
                [(now, i) for i in ids],
            )

    # ---- reading ---------------------------------------------------------------------------

    def get(self, memory_id: int) -> Memory | None:
        rows = self.vault.query(
            f"SELECT {_MEMORY_COLUMNS} FROM memories WHERE id = ?", (memory_id,)
        )
        return _row_to_memory(rows[0]) if rows else None

    def search(
        self,
        query: str,
        query_vector: np.ndarray | None,
        k: int = 8,
        *,
        project: str | None = None,
        include_secret: bool = False,
    ) -> list[MemoryHit]:
        """Hybrid retrieval: FTS5 + vector -> RRF -> rerank -> verify against SQLite."""
        lexical = self._lexical(query)
        semantic = (
            [i for i, _ in self.index.search(query_vector, CANDIDATES)]
            if query_vector is not None
            else []
        )
        lex_rank = {mid: r for r, mid in enumerate(lexical, 1)}
        sem_rank = {mid: r for r, mid in enumerate(semantic, 1)}
        rrf = {
            mid: sum(1.0 / (RRF_K + r[mid]) for r in (lex_rank, sem_rank) if mid in r)
            for mid in set(lex_rank) | set(sem_rank)
        }
        if not rrf:
            return []

        memories = self._load_active(list(rrf), include_secret)  # verify: drops stale index hits
        project_id = self._project_id(project)
        query_entities = self._query_entities(query)
        memory_entities = self._entities_of(list(memories))
        timey = bool(_TIME_WORDS.search(query))
        now = self.clock()

        hits = []
        for mid, mem in memories.items():
            overlap = (
                len(query_entities & memory_entities.get(mid, set())) / len(query_entities)
                if query_entities
                else 0.0
            )
            same_project = project_id is not None and mem.project_id == project_id
            score = (
                rrf[mid]
                * (1 + 0.30 * mem.importance)
                * (1 + 0.20 * overlap)
                * (1 + 0.15 * same_project)
                * _recency_factor(mem, now, timey)
                * (0.8 + 0.2 * mem.confidence)
            )
            hits.append(
                MemoryHit(
                    memory=mem,
                    lexical_rank=lex_rank.get(mid),
                    semantic_rank=sem_rank.get(mid),
                    rrf=rrf[mid],
                    score=score,
                )
            )
        hits.sort(key=lambda h: (-h.score, h.memory.id))
        return hits[:k]

    def _lexical(self, query: str) -> list[int]:
        match = fts_query(query)
        if match is None:
            return []
        rows = self.vault.query(
            "SELECT m.id FROM memories_fts JOIN memories m ON m.id = memories_fts.rowid"
            " WHERE memories_fts MATCH ? AND m.status = 'active'"
            " ORDER BY bm25(memories_fts) LIMIT ?",
            (match, CANDIDATES),
        )
        return [r[0] for r in rows]

    def _load_active(self, ids: list[int], include_secret: bool) -> dict[int, Memory]:
        marks = ",".join("?" for _ in ids)
        sql = f"SELECT {_MEMORY_COLUMNS} FROM memories WHERE status = 'active' AND id IN ({marks})"
        if not include_secret:
            sql += " AND sensitivity != 'secret'"
        return {r[0]: _row_to_memory(r) for r in self.vault.query(sql, tuple(ids))}

    def _project_id(self, name: str | None) -> int | None:
        if not name:
            return None
        rows = self.vault.query("SELECT id FROM projects WHERE name = ?", (name,))
        return rows[0][0] if rows else None

    def _query_entities(self, query: str) -> set[str]:
        lowered = query.lower()
        names = (r[0].lower() for r in self.vault.query("SELECT name FROM entities"))
        return {n for n in names if re.search(rf"\b{re.escape(n)}\b", lowered)}

    def _entities_of(self, ids: list[int]) -> dict[int, set[str]]:
        if not ids:
            return {}
        marks = ",".join("?" for _ in ids)
        rows = self.vault.query(
            "SELECT me.memory_id, e.name FROM memory_entities me"
            f" JOIN entities e ON e.id = me.entity_id WHERE me.memory_id IN ({marks})",
            tuple(ids),
        )
        out: dict[int, set[str]] = {}
        for mid, name in rows:
            out.setdefault(mid, set()).add(name.lower())
        return out


def _row_to_memory(r: tuple) -> Memory:
    return Memory(
        id=r[0], content=r[1], memory_type=r[2], origin=r[3], confidence=r[4], importance=r[5],
        source=r[6], project_id=r[7], sensitivity=r[8], status=r[9], supersedes_id=r[10],
        valid_from=r[11], valid_to=r[12], pinned=bool(r[13]), created_at=r[14],
        last_accessed_at=r[15], access_count=r[16],
    )  # fmt: skip


def _recency_factor(mem: Memory, now: datetime, timey: bool) -> float:
    """Strong when the query has time words, mild otherwise."""
    when = datetime.fromisoformat(mem.valid_from or mem.created_at)
    age_days = max(0.0, (now - when).total_seconds() / 86400)
    if timey:
        return 0.5 + math.exp(-age_days / 7)
    return 0.9 + 0.1 * math.exp(-age_days / 30)
