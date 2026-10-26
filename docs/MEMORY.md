# Ragnarok — Persistent Memory Spec

> **Status (2026-10-26):** milestones M0-M5 are built, tested and measured (see `README.md` and `docs/WORKLOG.md`); M6 (held-out evaluation, final numbers) is open. Originally a v1 plan, 2026-10-25. This is the team's initial plan, refined for this laptop and the PDF requirements.
> Persistent memory is **the primary goal of the project**; every other subsystem is optional.
> Numbers are targets until `bench/` measures them on the target laptop (RTX 4050 6 GB, 16 GB RAM).

**Core principle:** SQLite stores what Ragnarok knows. FTS5 finds exact matches, TurboQuant-based vector search
(Turbovec) finds semantically related memories, RRF combines the two, and consolidation organizes knowledge over time.

TurboQuant is a vector-quantization technique, not a memory system. The vector index is a fast, compressed *lookup aid*;
the SQLite records are the memory.

---

## 0. Changes from the initial plan

| Initial plan | Refined | Why |
|---|---|---|
| Encrypt "where practical" | **Always encrypted**: the whole DB is SQLCipher | The PDF makes encryption mandatory (NFR1.2) |
| `vault.tvim` stored on disk | **The Turbovec index lives only in RAM** and is rebuilt from the stored embeddings at startup (or saved encrypted) | A plaintext index file would leak memory content |
| One table holds everything that happens | **Two tiers**: `events` (a raw log with a retention window) → `memories` (curated, long-lived) | Satisfies "don't store every event as permanent memory" while keeping provenance |
| `embedding_id` | **Memory `id` = vector id** (Turbovec ID-mapped index); embeddings are stored in SQLite | One stable ID, and rebuilding never re-embeds |
| `entities`, `tags` as fields | Normalized `entities` and `memory_entities` tables | Allows entity-boosted retrieval and a simple graph view |
| `confidence` | `confidence` + `origin` (`user_stated` / `observed` / `inferred`) | An inference can never silently become a fact |
| — | `status`, `valid_from`/`valid_to`, `pinned`, `access_count`, `project_id`, `sensitivity`, `content_hash` | Needed for supersession, temporal questions, forgetting, and filtering |
| — | **Always-on profile block** in every prompt | The agent "knows you" from the first message of a new day, with no search needed |
| Voice and screen capture | **Pluggable capture adapters**; chat, tools, files, and triggers come first | Voice and screen aren't in the PDF and are optional add-ons |

---

## 1. Storage architecture

One encrypted file holds all durable memory: `%LOCALAPPDATA%\Ragnarok\vault.db` (outside OneDrive).

| Store | Technology | Role |
|---|---|---|
| Memory records | SQLCipher (SQLite + AES-256) | **Source of truth**: content, metadata, timestamps, provenance, relationships |
| Lexical index | FTS5 virtual tables inside the same DB | Exact keyword and identifier search (BM25) |
| Embeddings | `embeddings` table (float32 BLOB + model + dim) inside the DB | Durable vectors; the index is rebuilt from these |
| Semantic index | **Turbovec** (TurboQuant, Rust; Python wheel `turbovec` 1.1.2 for Windows) in RAM | Compressed similarity search, keyed by memory `id` |
| Embedding model | `nomic-embed-text` via local Ollama (768-d) | Text → vector, with `search_document:` / `search_query:` prefixes |

```text
          write path                                read path
 capture ─► events ─► extraction ─► memories       query ─► FTS5 ──┐
                         │            │ (+entities,          ─► Turbovec ─► RRF ─► rerank ─► SQLite verify ─► LLM
                         │            │  provenance)   embedding ┘
                         │            └─► embeddings ─► Turbovec (RAM)
                         └─ dedupe / update / supersede
```

**Rules:**
- Every memory has one stable integer `id`, used in SQLite, FTS5 (`rowid`), and Turbovec (external id).
- The vector index is **derived**. It can always be rebuilt from `memories` + `embeddings`, and startup reconciles the two (§7.3).
- A vector hit is never shown to the LLM without first loading and checking the SQLite record (`status = active`, not deleted).

---

## 2. Capture

### 2.1 Sources (adapters)

| Adapter | Priority | Examples |
|---|---|---|
| Chat | must | user and assistant messages, explicit statements ("remember…", "I prefer…") |
| Tool / action results | must | "created event X", "fixed test Y", "filed invoice Z" |
| Files / projects | should | project folders opened, documents created, git commits |
| Triggers / system | should | mail received, calendar events, scheduled jobs |
| Voice | optional | transcripts once STT exists; captured through the same chat path |
| Screen / active window | optional | opt-in only, with an app allowlist; treated as untrusted |

### 2.2 Two tiers

1. **`events`**: everything captured, append-only and cheap. It is kept for a retention window (default 30 days, configurable) and serves
   as the provenance and replay source. Noise filters run here: duplicates by `content_hash` within a time window, empty or trivial
   turns, and secrets (token and password patterns are scrubbed before storage).
2. **`memories`**: only information useful in future interactions, promoted from events by extraction (§3.2). These are long-lived.

### 2.3 When extraction runs
- **Explicit statements:** immediately ("remember that…", corrections, preferences).
- **Everything else:** in batches at session end, or after 10 minutes of idle time, on the background lane, so it never slows a live reply.

---

## 3. Memory records

### 3.1 Schema

```sql
CREATE TABLE memories (
  id               INTEGER PRIMARY KEY,
  content          TEXT    NOT NULL,             -- one self-contained statement
  memory_type      TEXT    NOT NULL CHECK (memory_type IN
                   ('episodic','semantic','preference','procedural','project','summary')),
  origin           TEXT    NOT NULL CHECK (origin IN ('user_stated','observed','inferred')),
  confidence       REAL    NOT NULL,             -- 0..1; inferred <= 0.7 until user confirms
  importance       REAL    NOT NULL DEFAULT 0.5, -- 0..1
  source           TEXT    NOT NULL,             -- adapter: chat, tool, file, trigger, voice, screen
  project_id       INTEGER REFERENCES projects(id),
  sensitivity      TEXT    NOT NULL DEFAULT 'normal',  -- normal | private | secret(never surfaced proactively)
  status           TEXT    NOT NULL DEFAULT 'active'   -- active | archived | superseded
                   CHECK (status IN ('active','archived','superseded')),
  supersedes_id    INTEGER REFERENCES memories(id),
  valid_from       TEXT,                         -- when the fact became true (if known)
  valid_to         TEXT,                         -- set when superseded
  pinned           INTEGER NOT NULL DEFAULT 0,
  created_at       TEXT    NOT NULL,
  last_accessed_at TEXT,
  access_count     INTEGER NOT NULL DEFAULT 0,
  content_hash     TEXT    NOT NULL
);
CREATE TABLE memory_sources  (memory_id INTEGER REFERENCES memories(id), event_id INTEGER REFERENCES events(id));  -- provenance
CREATE TABLE memory_links    (from_id INTEGER, to_id INTEGER, relation TEXT);  -- derived_from | related | contradicts
CREATE TABLE entities        (id INTEGER PRIMARY KEY, name TEXT UNIQUE, kind TEXT);   -- person, project, app, place...
CREATE TABLE memory_entities (memory_id INTEGER, entity_id INTEGER);
CREATE TABLE memory_tags     (memory_id INTEGER, tag TEXT);
CREATE TABLE embeddings      (memory_id INTEGER PRIMARY KEY, model TEXT, dim INTEGER, vector BLOB);
CREATE VIRTUAL TABLE memories_fts USING fts5(content, content='memories', content_rowid='id');
CREATE TABLE events (id INTEGER PRIMARY KEY, ts TEXT, source TEXT, kind TEXT, payload TEXT,
                     content_hash TEXT, session_id TEXT, expires_at TEXT);
```

Provenance is preserved through `memory_sources` (which events produced the memory), `origin`, and `confidence`.
This lets Ragnarok say *"you told me on 21 Oct"* (`user_stated`) or *"I inferred this from your calendar"* (`inferred`),
instead of treating every inference as certain.

### 3.2 Write pipeline

```text
candidates = extract(events)                       # local LLM, JSON schema, 0-N statements with type/origin/entities
for c in candidates:
    if hash_exists(c) and recent: skip                                   # exact duplicate
    similar = recall(c.content, k=5, include_archived=False)             # same hybrid search as reads
    action  = decide(c, similar)                                         # LLM, JSON schema: ADD | UPDATE(id) | SUPERSEDE(id) | NOOP
    with transaction:                                                    # SQLite first...
        apply(action)        # insert/update memory, entities, tags, memory_sources, FTS row, embedding
        outbox.add(index_op) # ...then the index op, recorded in the same transaction
    index.apply(outbox)      # Turbovec add/remove; replayed on startup if the process died here
```

**Decision rules:**
- **ADD:** new information.
- **UPDATE:** the same fact with more detail. Bump `confidence` and `access_count`, add a provenance link.
- **SUPERSEDE:** a contradicting newer fact. The old memory gets `status='superseded'` and `valid_to=now`, and the new memory gets `supersedes_id=old`.
  History is never silently erased.
- **NOOP:** already known.

**Confidence rules:**
- `user_stated` starts at 0.95. `observed` (tool results) starts at 0.85. `inferred` is capped at 0.7.
- An inferred memory reaches `user_stated` only after the user confirms it. Repetition alone never upgrades it.

---

## 4. Retrieval pipeline

### 4.1 Steps (the hot path; no LLM call, target p95 < 800 ms, expected < 100 ms)

1. **Parse the query.** Sanitize it for FTS5, match **known entities** by dictionary lookup against `entities` (no LLM), and detect
   time words ("yesterday", "last week") and the active project.
2. **Lexical:** FTS5 BM25 over `memories_fts`, top 30.
3. **Semantic:** embed the query (`search_query:` prefix) and search Turbovec, top 30.
4. **Fuse** with RRF: `score(d) = Σ 1 / (60 + rank_list(d))`. A memory missing from a list contributes 0 for that list.
5. **Rerank:**
   ```text
   final = rrf
         × (1 + 0.30·importance)
         × (1 + 0.20·entity_overlap)           # query entities ∩ memory entities
         × (1 + 0.15·same_project)
         × recency_factor                       # strong only when the query has time words; mild otherwise
         × confidence_factor                    # inferred memories rank below stated ones
   ```
6. **Verify in SQLite.** Load the records, drop anything not `active` (or whose sensitivity forbids it), and drop stale index hits.
7. **Pack for the LLM.** Keep the top 5–8 within a token budget (~1,000 tokens), each with its id, type, date, source, origin, and confidence,
   inside a delimited block marked as **data, not instructions**:
   ```text
   <memories>
   [m:812 preference, user_stated 0.95, 2026-10-21, chat] Prefers meetings after 14:00.
   [m:977 project, observed 0.85, 2026-10-23, tool] payments-service: test_refund_rounding failing since 23 Oct.
   </memories>
   ```
8. Bump `last_accessed_at` and `access_count` asynchronously.

### 4.2 Always-on profile

Every prompt also includes a small **profile block** (≤ 400 tokens): pinned memories plus the top preferences and active projects.
Ragnarok therefore knows the user from the first message of a new session or a new day, even before any search.
The block changes only when memory changes, so it also keeps the prompt prefix cacheable.

### 4.3 The model can ask for more

The model gets tools: `memory_search(query, type?, since?, project?)` for deeper lookups ("what did I decide about X last week?")
and `memory_save(content, type)` for explicit saves (shown to the user with an undo option).

---

## 5. Consolidation (idle / nightly)

This runs on the background lane only while the user is idle. It is resumable and idempotent.

1. **Group** recent episodic memories by project, entity, and time window.
2. **Summarize** each group into a `summary` memory: concise, citing source ids, linked via `memory_links(relation='derived_from')`.
3. **Validate** each summary: every cited id must exist, no new entities may appear that aren't in the sources, and confidence ≤ the minimum of the sources.
4. **Find duplicates** (embedding similarity ≥ 0.92 and the same entities) and **contradictions** (same entity and attribute, different value).
   Duplicates are merged through UPDATE. Contradictions go through SUPERSEDE, or are flagged for the user if the evidence is tied.
5. **Never promote** an `inferred` memory to a fact.
6. **Reversible:** originals are untouched. Deleting a summary restores nothing because nothing was removed.

---

## 6. Forgetting and lifecycle

**Usefulness score:**

```text
usefulness = 0.35·importance + 0.25·recency(last_accessed_at, half-life 30 days)
           + 0.25·log1p(access_count)/log1p(max_access) + 0.15·confidence
```

- **Pinned** memories are never archived.
- Below the threshold, and covered by a summary, a memory becomes `status='archived'`: it is removed from Turbovec and default search,
  but stays in SQLite and can be restored from the UI.
- Raw `events` expire after their retention window, unless a memory still links to them as provenance.

**User controls** (UI + `rag memory …`): `list`, `show` (with provenance), `correct` (creates a new version that supersedes the old one),
`pin`, `archive`, `restore`, `delete`, `export`.

**Hard delete** (user-initiated) cascades:
- the memory row, its FTS row, embedding, tags, entity links, and provenance links;
- `Turbovec.remove(id)`;
- summaries derived from it are regenerated without it, or deleted;
- a content-free tombstone (id, time) goes in the audit log;
- `PRAGMA secure_delete=ON` overwrites the freed pages.

Retention and deletion behaviour is shown in the UI, not hidden.

---

## 7. Privacy and reliability

### 7.1 Privacy
- All processing is local: Ollama on 127.0.0.1 with local-only mode, so no memory leaves the machine.
- The DB is SQLCipher-encrypted, with `temp_store=MEMORY` so no plaintext temp files are written. The key is wrapped by an Argon2id key
  derived from the passphrase, with an optional DPAPI auto-unlock (see ARCHITECTURE §6).
- Screen text, clipboard, files, mail, and retrieved memories are **untrusted data**. They are delimited, never executed as instructions,
  and can't change permissions.

### 7.2 Durability
- WAL mode; every write is a transaction; the outbox keeps SQLite and Turbovec consistent.
- `PRAGMA quick_check` at startup and `integrity_check` weekly while idle.
- A daily encrypted backup via the SQLite online backup API, keeping 7 days.
- Schema versioning with explicit migrations.

### 7.3 Startup and recovery
1. Open the vault.
2. Replay any outbox entries not yet applied to the index.
3. Rebuild Turbovec from `embeddings WHERE status='active'`. The expected time for 10k memories is well under 1 s; this is to be measured.
4. Reconcile: index count = active memory count. On a mismatch, run a full rebuild.
5. Ready.

---

## 8. TurboQuant / Turbovec verification (before we rely on it)

| Check | How | Pass condition |
|---|---|---|
| Implementation | read the `turbovec` 1.1.2 source: random rotation, scalar quantization, **QJL residual stage present or not** | documented in `docs/WORKLOG.md`; never claim full paper compatibility unless the source shows it |
| API fit | external uint64 ids, `remove(id)`, search with an id allowlist | all three work |
| Quality | recall@10 against an exact float32 numpy baseline, same vectors and queries, at 2-bit and 4-bit | ≥ 0.95 at the chosen bit width |
| Speed | p50/p95 search latency at 1k / 10k / 100k vectors on this laptop | search < 10 ms p95 at 10k |
| Rebuild | time to rebuild from SQLite BLOBs | < 1 s at 10k |
| Persistence | does `save()` write plaintext? | if yes, keep the index RAM-only (default) or encrypt the bytes |

### 8.1 M0 results (2026-10-25, `turbovec` 1.1.2, Windows, Python 3.12)

| Check | Status | Evidence |
|---|---|---|
| API fit | ✅ pass | `IdMapIndex` has `add_with_ids` (uint64 ids), `remove(id)`, `contains(id)`, `search(..., allowlist=...)`. Covered by `tests/test_m0_turbovec_api.py` (all pass). |
| Persistence | ✅ resolved, with a better option | `to_bytes()` / `from_bytes()` serialize the whole index without touching the filesystem. We can store that blob **inside the encrypted vault** instead of a plaintext `.tvim`, so startup doesn't need a full rebuild. The embeddings table stays the ground truth and the rebuild path stays as the recovery path. |
| Implementation | ⚠️ partly verified | The package README and compiled binary describe: normalise + store length, one shared random rotation, Lloyd-Max scalar quantization (2 or 4 bits), a per-vector scalar that removes inner-product bias, and an optional `calibrate(sample)` ("TQ+"). **No QJL residual stage is mentioned** in either place. We have not read the Rust source, so the claim stays "TurboQuant-based, no QJL found", never "full paper". |
| Size | ✅ measured | Fixed overhead ≈ 0.86 MB (4-bit) / 0.45 MB (2-bit), then ≈ 396 / 204 bytes per vector (ideal 384 / 192). At 20,000 vectors: 7.0× smaller than float32 at 4-bit, 13.6× at 2-bit. At 10k memories the 4-bit index is about 4.8 MB. |
| Constraints | ✅ fine for us | dimension must be a multiple of 8 (768 works); bit widths 2 or 4; brute-force scan (no graph), which is fine at this scale. |
| Calibration | ⚠️ to-do | `calibrate()` needs ~1,024 representative vectors and should run **before** bulk adds; a bad sample hurts recall. Decide in M2 after measuring recall on real embeddings. |
| Quality (recall@10 vs exact) | ⏳ pending | Needs real embeddings, so it waits for `nomic-embed-text` (M2 benchmark). Random vectors would not be representative. |
| Speed | ⏳ pending | Same: measured in the M2/M6 benchmark. |

SQLCipher check (same day): SQLCipher 4.19.0 on SQLite 3.53.4, FTS5 compiled in, exact-identifier search works, the file header
is not `SQLite format 3`, demo strings don't appear in the raw bytes, and opening without or with the wrong key fails.
Covered by `tests/test_m0_sqlcipher.py`.

**Fallback:** if Turbovec fails a check, switch to exact numpy cosine search (same interface; 10k × 768 float32 ≈ 31 MB) or `sqlite-vec`.
The rest of the system is unaffected because the index sits behind one small interface:

```python
class VectorIndex(Protocol):
    def add(self, ids: np.ndarray, vectors: np.ndarray) -> None: ...
    def remove(self, ids: Iterable[int]) -> None: ...
    def search(self, query: np.ndarray, k: int, allow: set[int] | None = None) -> list[tuple[int, float]]: ...
    def __len__(self) -> int: ...
```

---

## 9. Module API

```python
remember(content, memory_type, source, origin="user_stated", project=None) -> MemoryId   # explicit save
ingest(events) -> IngestReport          # extraction + dedupe + ADD/UPDATE/SUPERSEDE/NOOP
recall(query, k=8, filters=None) -> list[MemoryHit]   # hit: record + lexical_rank + semantic_rank + rrf + final score
profile() -> list[Memory]               # always-on block
correct(id, new_content) -> MemoryId    # supersedes id
archive(id) / restore(id) / delete(id) -> None
consolidate() -> ConsolidationReport
export(filters=None, path) -> Path
```

Layout: `ragnarok/memory/` → `vault.py`, `schema.sql`, `capture.py`, `extract.py`, `write.py`, `retrieve.py`, `rerank.py`,
`index_turbovec.py`, `index_exact.py`, `consolidate.py`, `forget.py`, `export.py`.

---

## 10. How we prove it remembers for days

| Test | What it shows |
|---|---|
| **Restart test** | Store facts, kill the process, reopen: everything is recalled; the index is rebuilt; nothing is lost |
| **Simulated multi-day timeline** | A fake clock replays 14 days of synthetic conversations (preferences, projects, changes of mind); queries on day 15 must return the *current* fact and be able to show the superseded history |
| **Retrieval benchmark** | 500 synthetic memories + 100 labelled queries (exact tokens, paraphrases, temporal, entity, contradicted facts, deleted items that must **not** come back): recall@5, MRR, p50/p95 latency for FTS-only, vector-only, hybrid+RRF, and hybrid+rerank |
| **Turbovec vs exact** | §8 table |
| **Encryption check** | the DB header isn't `SQLite format 3`; known demo strings don't appear in the raw file; opening without the key fails |
| **Deletion check** | a deleted memory is absent from the DB, FTS, Turbovec, summaries, and the profile |

All results are recorded in `docs/WORKLOG.md` with the hardware and software versions.

---

## 11. Milestones (one commit and push per step)

| # | Deliverable | Done when |
|---|---|---|
| M0 | Environment: Ollama + `nomic-embed-text` + `qwen3.5:4b`, uv + Python 3.12; SQLCipher FTS5 check; Turbovec API + source check | checks recorded in the WORKLOG |
| M1 | `vault.py` + schema + migrations (encrypted) | open/close/reopen tests pass; header check passes. **Done 2026-10-25.** |
| M2 | `remember` / `recall` with FTS5 + vector index + RRF + rerank + SQLite verify | restart test passes; latency measured. **Done 2026-10-25** (bench: recall@5 0.90, MRR 0.84, ~119 ms p95; see D-026). |
| M3 | `ingest`: extraction + ADD/UPDATE/SUPERSEDE/NOOP + provenance | multi-day timeline test passes. **Done 2026-10-25** (real-model eval 15/15 extraction, 12/12 decisions on a small tuned set; see D-027). |
| M4 | Profile block + `memory_search` / `memory_save` tools + a minimal chat loop to demo it | a new session on a "new day" knows the user. **Done 2026-10-25** (real-model demo 4/4; see D-028, D-029). |
| M5 | Consolidation, forgetting, user controls, hard-delete cascade | deletion check passes; consolidation is reversible. **Done 2026-10-25** (D-033; real-model summaries 4/4 accepted on a small set). |
| M6 | Benchmarks + Turbovec-vs-exact report | numbers in the WORKLOG and README |
