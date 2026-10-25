# ARCHITECTURE.md --- Project J.A.R.V.I.S.

## Sovereign Always-On OS Agent

> **Status:** Proposed architecture and benchmark targets, not validated
> performance claims.\
> **Primary design goal:** A local-first, privacy-conscious desktop
> agent with persistent memory, voice interaction, screen context, and
> permission-gated OS actions.

## 1. System Overview

Project J.A.R.V.I.S. is a local-first voice- and screen-aware
operating-system agent intended to run on consumer laptops without
requiring cloud inference APIs. It combines a local language model,
speech processing, accessible UI inspection, durable event storage,
hybrid memory retrieval, and guarded action execution.

### Engineering goals

-   Preserve useful episodic history without allowing vector storage to
    dominate memory.
-   Retrieve both exact technical details and semantically related prior
    experiences.
-   Keep speech, indexing, and UI inspection from needlessly competing
    with the local LLM for GPU memory.
-   Make every proposed OS action inspectable, permission-gated, and
    auditable.
-   Measure end-to-end latency and retrieval quality on the actual
    target laptop.

### Important terminology

**TurboQuant** is a research method for vector quantization.
**Turbovec** is the selected Rust-backed vector-search library whose
documentation describes a TurboQuant-based quantization implementation.
Do not claim that every part of Google's research, including QJL
residual correction, is implemented by the selected Turbovec version
unless source inspection confirms it.

All latency, RAM, VRAM, compression, recall, and accuracy figures in
this document are targets or hypotheses until benchmarked. Do not
present them as achieved results.

## 2. Design Principles

1.  **Local-first:** Process user content locally wherever practical; do
    not send screen, clipboard, audio, or memory contents to cloud
    services by default.
2.  **Durable source of truth:** SQLite stores original event records
    and metadata. Quantized vectors are a derived retrieval index, not
    the only copy of a memory.
3.  **Hybrid retrieval:** Combine lexical search for exact identifiers
    with vector search for semantic similarity.
4.  **Reversible consolidation:** Summaries supplement original records;
    archival never means silent, irreversible deletion.
5.  **Least privilege:** Passive collection is opt-in, sensitive fields
    can be excluded, and destructive actions require explicit
    confirmation.
6.  **Evidence-grounded answers:** Return source IDs and excerpts with
    retrieved memories so the LLM can distinguish evidence from
    inference.
7.  **Measured performance:** Report p50/p95 latency, memory use,
    retrieval quality, and failure rates from repeatable tests.

## 3. Five-Layer Architecture

  ---------------------------------------------------------------------------
  Layer             Subsystem         Core technologies     Primary
                                                            responsibility
  ----------------- ----------------- --------------------- -----------------
  1                 Sensory &         Faster-Whisper,       Capture
                    Perception        Silero VAD, Windows   user-authorized
                                      UI Automation via     audio and
                                      `pywinauto`, explicit selected OS
                                      clipboard hooks       context; redact
                                                            and deduplicate
                                                            events

  2                 Sovereign Memory  SQLite, SQLite FTS5,  Persist events,
                                      Turbovec,             index exact text,
                                      `nomic-embed-text` or store compact
                                      a validated local     vectors, retrieve
                                      embedding model       and consolidate
                                                            memories

  3                 Cognitive Core    Ollama, a locally     Combine user
                                      available instruction intent, screen
                                      model, structured     context, and
                                      tool-call schema      retrieved
                                                            evidence; propose
                                                            responses and
                                                            actions

  4                 Action & Safety   `pywinauto`,          Execute
                                      PyAutoGUI where       allow-listed
                                      needed, restricted    actions with
                                      subprocess runner,    permission
                                      Playwright for        checks, audit
                                      explicitly authorized logs, and
                                      browser tasks         rollback where
                                                            possible

  5                 Voice & HUD       Kokoro TTS, FastAPI   Speak responses
                                      WebSockets,           and display
                                      HTML/CSS/JavaScript   state, retrieved
                                      dashboard             evidence, action
                                                            approvals, and
                                                            telemetry
  ---------------------------------------------------------------------------

### Suggested initial model configuration

-   **LLM:** Start with a small quantized local model supported by
    Ollama. Validate model quality and GPU fit on the target laptop
    before locking the choice.
-   **Embeddings:** Use `nomic-embed-text` only after confirming its
    vector dimensionality and compatibility with the chosen index.
-   **STT:** Faster-Whisper with a small/base quantized configuration on
    CPU.
-   **TTS:** Kokoro ONNX on CPU if its voice package and runtime are
    available on the target platform.

Do not assume these models can all run concurrently within a fixed
RAM/VRAM budget without measuring peak usage.

## 4. End-to-End Data Flows

### Flow A: Reactive voice and screen command

Example: "Why did my build fail, and how can I fix it?"

1.  **Capture and endpoint detection:** The microphone listener detects
    the end of the user's utterance using VAD. Recording is visible and
    controlled by the user.
2.  **Parallel context collection:** Speech transcription, authorized
    active-window accessibility scanning, and memory retrieval run
    concurrently when practical.
3.  **Hybrid retrieval:** The query is sent to both SQLite FTS5 and
    Turbovec. Their ranked results are fused, filtered for permissions
    and relevance, and resolved to original SQLite records.
4.  **Reasoning:** The local LLM receives the transcript, relevant UI
    evidence, retrieved memory excerpts, and the permitted tool schema.
5.  **Response:** The system streams text or audio when supported. Tool
    execution is separate from response generation.
6.  **Authorization:** Read-only actions may follow configured policies.
    File edits, shell commands, sending messages, deleting data, or
    other consequential actions require explicit confirmation.
7.  **Audit and memory write:** Record the user's request, the selected
    evidence IDs, the proposed action, authorization, outcome, and
    timing, subject to user privacy settings.

### Latency targets

Use **1,000 ms as an initial target for first audible feedback under
favorable, warm-model conditions**, not as a guarantee for a complete
answer or completed action. Measure separately:

-   Time from end-of-speech to first audible response.
-   Time to final spoken response.
-   Time to action completion.
-   p50 and p95 latency across repeated trials.
-   Cold-start latency separately from warm-start latency.

The stages overlap, and transcription, model inference, and speech
synthesis have workload-dependent latency. Do not promise a fixed
end-to-end time until measured.

### Flow B: Opt-in background event collection

The background daemon is disabled by default until the user explicitly
enables each source.

1.  Watch only approved event sources and selected applications.
2.  Normalize and redact events before persistence; exclude secrets,
    passwords, authentication tokens, and configured sensitive paths.
3.  Deduplicate using a stable content hash plus event context. Hashing
    alone is not a privacy mechanism.
4.  Persist approved event records in SQLite.
5.  Create embeddings asynchronously and update the Turbovec index.
6.  Detect configured error patterns and offer a notification. Never
    bypass the user's notification or action-approval policy to initiate
    an OS repair.

Use event-driven hooks where possible instead of aggressive polling. If
polling is required, make the interval configurable and measure its CPU
and battery impact.

### Flow C: Reversible memory consolidation

1.  Select a bounded set of related, authorized events.
2.  Retrieve the original records and their timestamps.
3.  Ask the local LLM to draft a concise summary with source record IDs.
4.  Validate that the summary is supported by its sources and does not
    invent details.
5.  Store the summary as a new memory node linked to the source records.
6.  Mark source events as eligible for archival, but retain them
    according to the user's retention policy.
7.  Rebuild or update the derived vector index safely and verify it
    against SQLite.
8.  Report counts, failures, duration, and storage change in the HUD.

Never permanently delete source records merely because a summary was
generated. Consolidation must be idempotent and recoverable after
interruption.

## 5. Sovereign Hybrid Memory Design

### 5.1 Source-of-truth store

SQLite (`vault_meta.db`) stores the authoritative memory records:

-   `id`: stable integer primary key
-   `timestamp`: UTC timestamp
-   `event_type`: `screen`, `clipboard`, `terminal`, `chat`,
    `file_event`, or `night_summary`
-   `source_app`: application identifier where available
-   `raw_text`: redacted text or structured event payload
-   `content_hash`: deduplication fingerprint
-   `is_consolidated`: archival/consolidation status
-   `sensitivity`: classification used by retrieval policy
-   `retention_until`: optional retention deadline
-   `source_ids`: source links for summary records, stored in a
    normalized relation table

Use parameterized SQL, transactions, indexes, and explicit migration
versions.

### 5.2 Lexical retrieval: SQLite FTS5

Create an FTS5 virtual table over approved searchable text, with a
stable mapping back to the source record ID. BM25 ranking helps locate
exact strings such as:

-   `0x80070005`
-   `auth_middleware.py`
-   port `8089`
-   command-line flags and stack-trace fragments

FTS5 is part of SQLite when built with FTS5 support; verify availability
in the Python runtime. It adds an index that consumes disk and memory,
so measure its actual footprint. Treat sub-millisecond lookup as a
benchmark target, not a guarantee.

### 5.3 Semantic retrieval: Turbovec

Use the selected Turbovec version as the semantic vector index after
verifying its API and implementation details.

-   Generate one embedding per approved memory text or summary.
-   Normalize vectors consistently with the embedding model and index
    API.
-   Use stable IDs that map to authoritative SQLite rows.
-   Persist the vector index atomically or through the library's
    supported safe persistence mechanism.
-   Handle stale IDs, failed writes, and index/metadata mismatches.
-   Rebuild the vector index from SQLite when needed.

**Algorithm attribution:** Turbovec documents a TurboQuant-based
quantization implementation. Inspect the pinned source version to verify
which paper components it implements, including rotation, scalar
quantization, and any QJL residual-correction path. Do not state that
QJL is present or that the library exactly reproduces the entire Google
paper unless confirmed by source and tests.

### 5.4 Reciprocal Rank Fusion (RRF)

Merge lexical and semantic result lists using rank positions rather than
assuming BM25 scores and vector similarity scores are directly
comparable.

For document (d):

`RRF(d) = Σ_j 1 / (k + rank_j(d))`

Use a configurable constant (commonly `k = 60` as an initial baseline),
and tune it on a fixed evaluation set. A document missing from a result
list contributes zero for that list.

### 5.5 Temporal and policy reranking

Apply temporal relevance only after fusion, with query-aware behavior:

-   Recent events can receive a modest boost for questions about
    "today," "the last session," or ongoing tasks.
-   Stable facts, security decisions, and older solutions must not be
    buried just because they are old.
-   Apply sensitivity, application, and retention filters before passing
    results to the LLM.
-   Return the source record ID, timestamp, excerpt, and retrieval
    reason with each result.

### 5.6 Quantization and index alternatives

TurboQuant/Turbovec is the initial candidate, not a predetermined
winner. Compare it against an exact float32 baseline and at least one
practical alternative when time permits. Product Quantization, RaBitQ,
and HNSW solve different parts of the problem; PQ and RaBitQ are
quantization approaches, while HNSW is an approximate-neighbor graph
index and may be combined with quantization.

For the first prototype, avoid implementing multiple vector engines from
scratch. Benchmark the existing library against the exact baseline using
the same vectors and queries.

## 6. Storage and Memory Estimates

For a 768-dimensional embedding:

-   Float32 payload: `768 × 4 = 3,072 bytes` per vector.
-   Ideal 4-bit payload: `768 × 0.5 = 384 bytes` per vector, before IDs,
    alignment, calibration values, index metadata, and file-format
    overhead.
-   At 100,000 vectors, ideal raw payloads are approximately 307.2 MB
    (float32) and 38.4 MB (4-bit), using decimal units.

These are arithmetic estimates of vector payload only, not total process
RAM or index-file size. Verify the actual format, per-vector overhead,
and loaded memory footprint empirically.

### Resource budget table

  -------------------------------------------------------------------------
  Component               Execution target        Budget status
  ----------------------- ----------------------- -------------------------
  Local LLM               GPU when available, CPU Model- and
                          fallback if practical   quantization-dependent;
                                                  benchmark

  Embedding model         Prefer CPU initially to Model- and
                          protect VRAM, then      runtime-dependent
                          benchmark               

  Speech recognition      CPU                     Measure peak RAM and
                                                  real-time factor

  TTS                     CPU                     Measure startup and
                                                  first-audio latency

  Turbovec index          CPU                     Measure loaded index size
                                                  and p95 search latency

  SQLite + FTS5           CPU + disk              Measure database, FTS
                                                  index, and cache
                                                  footprint

  HUD and event daemon    CPU                     Measure idle CPU and
                                                  battery impact
  -------------------------------------------------------------------------

Do not publish a single total RAM/VRAM figure until the complete
concurrent workload has been measured on the target device.

## 7. Safety, Privacy, and Reliability

-   Background screen and clipboard capture must be opt-in, visible, and
    easy to pause.
-   Provide per-source toggles, application allowlists, redaction rules,
    and retention controls.
-   Exclude passwords, tokens, and other secrets from logs and
    embeddings.
-   Require confirmation for destructive shell commands, file deletion,
    credential changes, external messages, and other consequential
    actions.
-   Use command allowlists, timeouts, restricted working directories,
    and a least-privilege process for shell execution. Never execute
    arbitrary LLM-generated shell text without validation.
-   Treat screen text, clipboard content, terminal output, and retrieved
    memories as untrusted input. They may contain prompt-injection
    attempts.
-   Separate model proposals from the trusted action executor. Validate
    tool arguments against schemas and policy.
-   Log action intent, approval, execution result, and rollback status
    without logging secrets.
-   Use SQLite transactions and recoverable index writes; test crashes
    and restarts.
-   Provide a global pause/kill control and a clear indicator when the
    microphone or background watcher is active.

## 8. Repository Structure

``` text
ragnarok-jarvis/
├── ARCHITECTURE.md
├── README.md
├── config/
│   └── settings.yaml
├── core/
│   ├── orchestrator.py
│   ├── llm_router.py
│   ├── auth_guard.py
│   └── policy_engine.py
├── memory/
│   ├── turbovec_engine.py
│   ├── metadata_store.py
│   ├── fts_index.py
│   ├── hybrid_retriever.py
│   ├── rank_fusion.py
│   ├── memory_models.py
│   └── night_consolidator.py
├── perception/
│   ├── ears_stt.py
│   ├── screen_scanner.py
│   └── os_daemon.py
├── actions/
│   ├── gui_executor.py
│   └── shell_runner.py
├── voice/
│   └── jarvis_tts.py
├── hud/
│   ├── server.py
│   └── static/
│       ├── index.html
│       ├── hud.css
│       └── visualizer.js
├── tests/
│   ├── test_memory_recall.py
│   ├── test_hybrid_ranking.py
│   ├── test_persistence.py
│   └── test_action_policy.py
└── data/
    ├── vault.tvim
    └── vault_meta.db
```

Generated runtime data under `data/` should be excluded from version
control unless the team intentionally commits small synthetic fixtures.

## 9. Module Contracts

### Memory and consolidation

-   `remember(text, event_type, metadata=None) -> memory_id`
-   `recall(query, k=5, filters=None) -> list[MemoryResult]`
-   `run_night_cycle() -> ConsolidationStats`

`MemoryResult` should include the source ID, timestamp, excerpt, lexical
rank, semantic rank, fused score, and sensitivity metadata.

### Voice and cognitive core

-   `process_voice_turn(audio_buffer) -> TurnResult`

`TurnResult` should contain response text, optional proposed tool call,
latency breakdown, evidence IDs, and authorization status. A proposal is
not proof of authorization.

### Screen perception and actions

-   `scan_screen() -> list[AccessibleElement]`
-   `execute_action(action_json, authorization_context) -> ActionResult`

Actions must be validated against a strict schema and policy.
Coordinates are fallbacks, not a guarantee of correct targeting.

### HUD and telemetry

Publish events such as:

-   `state_change`
-   `memory_recalled`
-   `screen_scanned`
-   `action_pending_approval`
-   `action_completed`
-   `night_cycle_complete`
-   `latency_sample`

Never include raw secrets or unredacted sensitive clipboard data in
telemetry.

## 10. Four-Person Team Split

1.  **Memory and consolidation:** Turbovec integration, SQLite schema,
    FTS5, hybrid retrieval, RRF, persistence tests.
2.  **Voice and cognitive core:** STT, local LLM orchestration,
    structured tool routing, TTS integration.
3.  **Screen perception and actions:** UI Automation scanning, opt-in
    event watchers, policy-gated GUI and shell execution.
4.  **HUD and evaluation:** Dashboard, telemetry, benchmark harness,
    reproducible demo script and results.

Agree on schemas and interfaces first. Merge through small pull requests
and shared synthetic test fixtures.

## 11. Evaluation Plan

Build a fixed, synthetic benchmark before optimizing. Include
exact-match questions, semantic paraphrases, temporal questions,
conflicting memories, archived source records, and queries with
sensitive data that should not be returned.

Compare:

1.  FTS5 only.
2.  Exact vector search only.
3.  Turbovec semantic search only.
4.  FTS5 + semantic search + RRF.
5.  Hybrid retrieval with temporal reranking.

Measure:

-   Recall@5 and Recall@10 against labeled relevant records.
-   MRR or nDCG for ranking quality.
-   p50 and p95 query latency, including embedding time and SQLite
    record fetch.
-   Index size, SQLite/FTS size, and peak process RAM.
-   Insert throughput, restart recovery, and index consistency.
-   False positives and sensitive-data leakage.
-   First-audio latency, complete response latency, and
    action-completion latency separately.

Run multiple trials on the target laptop, document hardware and software
versions, and report both warm- and cold-start results. Only promote a
performance target to a claimed result after the benchmark reproduces
it.

## 12. Hackathon Demo Story

1.  Tell J.A.R.V.I.S. about a synthetic build error with a distinctive
    code, filename, and port.
2.  Have it store the event and demonstrate exact-token retrieval
    through FTS5.
3.  Ask a paraphrased question about the same debugging session and show
    semantic retrieval.
4.  Show RRF combining the two result lists and opening the original
    SQLite evidence.
5.  Restart the application and demonstrate persistence.
6.  Ask it to propose a fix, inspect the diff, and require approval
    before execution.
7.  Display measured retrieval recall, p50/p95 latency, and memory
    footprint on the HUD.

## 13. Implementation Milestones

**Milestone 1 --- Durable hybrid memory:** SQLite schema, FTS5, Turbovec
adapter, RRF, synthetic tests.

**Milestone 2 --- Evidence-grounded assistant:** Local LLM, query
orchestration, citations to memory records, prompt-injection handling.

**Milestone 3 --- Voice and screen context:** STT/TTS, accessible UI
tree extraction, explicit capture controls.

**Milestone 4 --- Safe actions:** Allow-listed tools, approval flow,
isolated shell execution, audit logs.

**Milestone 5 --- Consolidation and evaluation:** Reversible summaries,
recovery tests, benchmark dashboard, final reproducible demo.

The first end-to-end milestone should be a text query that retrieves the
right evidence after a restart. Add always-on monitoring and autonomous
actions only after this foundation works.

------------------------------------------------------------------------

**Success criterion:** J.A.R.V.I.S. reliably remembers approved
information, retrieves exact and semantic evidence, explains where its
answer came from, and performs only authorized actions---with measured
resource use and latency on the target laptop.
