# Decisions

One entry per decision: the newest number goes at the bottom, and entries are never edited away. If a decision changes, add a new entry that supersedes the old one.
Format: context → decision → alternatives → consequences → revisit if.

---

### D-001 — Focus on the five pillars the PDF stresses
- **Date:** 2026-10-25 · **Status:** accepted
- **Context:** The proposal's requirements put most weight on skills (FR2), planning (FR3), triggers (FR4), encryption (NFR1.2),
  and recovery (NFR3). The earlier team design spent much of its effort on voice and screen capture, which the PDF doesn't ask for.
- **Decision:** Design and build around these five pillars. Memory, UI, and performance work exist to support them.
- **Consequences:** Voice and screen capture move to out-of-scope (D-016). The build plan schedules the pillars first.
- **Revisit if:** The judging criteria emphasize something else.

### D-002 — Build it as an agent harness, the way Claude Code and Codex are built
- **Date:** 2026-10-25 · **Status:** accepted
- **Context:** The requirements are about tool use, autonomy, safety, and recovery. Production agents solve these with the same
  ingredients: a model in a tool-calling loop, permission rules and approvals, a sandbox, and resumable session logs.
- **Decision:** Write our own small loop (~300 lines) with these ingredients, and use no agent framework.
- **Alternatives:** LangGraph, CrewAI, and AutoGen were rejected. They add dependencies and abstraction layers, they're harder to debug live, and they give
  us less control over prompt size, which matters most on a 4B model.
- **Consequences:** We own the loop, context building, and compaction code. Every token in every prompt is under our control.

### D-003 — One resident chat model, `qwen3.5:4b`, plus `nomic-embed-text`
- **Date:** 2026-10-25 · **Status:** accepted, pending the Phase 0 bench
- **Context:** The RTX 4050 has 6 GB of VRAM. Swapping models in Ollama costs seconds, and a model that spills to the CPU slows down several times.
- **Decision:** One chat model handles every call type, including planning, verification, extraction, and chat. It stays pinned alongside the embedder.
- **Alternatives:**
  - `qwen3.5:9b` (Q4_K_M 6.6 GB): doesn't fit.
  - `qwen3:8b` (5.2 GB): leaves no room for the KV cache.
  - A big + small two-model setup: swap lag, and the two models don't fit together.
- **Consequences:** Each call has to be small and focused (step sub-agents, top-k tools, typed checks).
- **Revisit if:** The bench shows poor tool-call accuracy. Then try `qwen3:4b` or the `qwen3.5:4b-mtp-q4_K_M` build.

### D-004 — Ollama configuration and local-only mode
- **Date:** 2026-10-25 · **Status:** accepted
- **Decision:**
  - Set `OLLAMA_NO_CLOUD=1`, `OLLAMA_KEEP_ALIVE=-1`, `OLLAMA_MAX_LOADED_MODELS=2`, `OLLAMA_NUM_PARALLEL=1`,
    `OLLAMA_CONTEXT_LENGTH=16384`, `OLLAMA_FLASH_ATTENTION=1`, and `OLLAMA_KV_CACHE_TYPE=q8_0`.
  - The gateway sends the same `num_ctx` on every request and rejects `-cloud` model names and non-loopback hosts.
- **Why:** The Ollama FAQ documents local-only mode, notes that memory scales with parallel × context, and says `q8_0` uses about half the KV memory of `f16`.
  Constant load options avoid model reloads.
- **Revisit if:** `ollama ps` shows a CPU share. Then drop to `num_ctx` 8192.

### D-005 — Storage: one SQLCipher vault, with vectors held in RAM
- **Date:** 2026-10-25 · **Status:** accepted, pending the Phase 0 FTS5 check
- **Decision:**
  - A single SQLCipher 4 file holds events, memory, plans, the audit log, metrics, and the skill registry.
  - FTS5 handles exact search. Embeddings are stored as BLOBs and searched exactly with numpy in RAM.
  - The library is `sqlcipher3`; the fallback is `apsw-sqlite3mc`.
- **Alternatives:** Turbovec, FAISS, Chroma, and Qdrant are unnecessary below about 100k vectors, and each adds a separate store that would also need encrypting.
- **Consequences:** One file to encrypt, back up, and recover. `temp_store=MEMORY` keeps temp data off the disk.

### D-006 — Key handling
- **Date:** 2026-10-25 · **Status:** accepted
- **Decision:**
  - A random 256-bit vault key is wrapped with AES-256-GCM under an Argon2id key derived from the user's passphrase.
  - A recovery code provides a second wrap.
  - An optional DPAPI-sealed copy allows unattended unlock at login.
  - Account secrets live in Windows Credential Manager.
- **Consequences:** Unattended restore after a reboot (NFR3.2) needs the DPAPI opt-in. The threat model is written down in ARCHITECTURE §6.3.

### D-007 — Recovery through event sourcing, log-before-act, and idempotency keys
- **Date:** 2026-10-25 · **Status:** accepted
- **Decision:**
  - Every action is an append-only event in the vault. A `tool_call` event is committed with `synchronous=FULL` before any side effect.
  - Our MCP servers accept idempotency keys, so retries after a crash can't duplicate an action.
  - Snapshots are taken every 50 events, so replays stay short.
  - The daemon runs as a Task Scheduler task that restarts on failure.
- **Consequences:** Restore is replay, with an expected time under 1 s. Tools that can't be made idempotent are marked `interrupted` after a crash, and the user is asked what happened.

### D-008 — Planning: plan tool, step sub-agents, typed verifier, failure ladder
- **Date:** 2026-10-25 · **Status:** accepted
- **Decision:**
  - The model emits a JSON plan with `after` dependencies listed in order, so the plan is acyclic by construction.
  - Each step runs as a fresh, short-context sub-agent.
  - `done_when` prefers typed checks over model judgments.
  - Failures climb a ladder: retry → fix → re-plan → degrade (independent steps continue).
- **Alternatives:**
  - A pure ReAct loop: small models drift on long loops.
  - A separate upfront DAG compiler: more machinery than needed.
- **Consequences:** Contexts stay small, which keeps calls fast. NFR3.1 comes almost for free.

### D-009 — Skills are `SKILL.md` folders, MCP servers, and built-in tools
- **Date:** 2026-10-25 · **Status:** accepted
- **Decision:**
  - `SKILL.md` uses the Claude Code frontmatter, with Ragnarok fields under a `ragnarok:` key. MCP is the interface for API wrappers.
  - Registration runs the skill's tests in the sandbox, asks the owner to approve its permissions, and pins a content hash. A changed hash disables the skill.
  - Tools are chosen by embedding top-k, and the tool set is frozen per turn.
- **Consequences:** Skills are portable, any existing MCP server can be plugged in, and nothing changes silently.

### D-010 — A native Windows sandbox
- **Date:** 2026-10-25 · **Status:** accepted, pending a 3-hour spike
- **Context:** WSL isn't installed and there's no Docker. Docker Desktop would cost 2–4 GB of the 16 GB RAM.
- **Decision:**
  - Follow Codex's unelevated Windows sandbox design: a write-restricted token, with ACLs only on workspace folders.
  - Add a Job Object for limits and kill-on-close, an offline environment (plus an optional firewall rule on a dedicated Python copy), and a Python audit hook as defense in depth.
  - Modes: `read-only`, `workspace-write`, `full-access`.
- **Fallback:** a low-integrity process with a low-labelled workspace, plus the Job Object.
- **Consequences:** Reads and network aren't OS-blocked by the token alone. We state this openly.

### D-011 — Triggers run headless sessions in a background profile
- **Date:** 2026-10-25 · **Status:** accepted
- **Decision:**
  - Cron, folder, mail, calendar, system, and date triggers each start a headless session with the `background` profile (read-only sandbox, no sending or deleting).
  - Side effects wait in an Inbox.
  - A `trigger_fires` table with a unique `(trigger_id, dedupe_key)` index makes each event fire once, even across restarts.
  - Catch-up after downtime uses cursors and snapshots.
- **Consequences:** Always-on behavior can't take consequential actions without approval.

### D-012 — Performance: a zero-LLM hot path and one priority lane
- **Date:** 2026-10-25 · **Status:** accepted
- **Decision:**
  - Retrieval and intent classification (NFR2.1) use embeddings, FTS5, RRF, and nearest-example routing, with a model call only on low confidence.
  - One priority lane sits in front of Ollama, and interactive work preempts background work.
  - Prompts put the stable prefix first so the prompt cache is reused.
- **Consequences:** We expect NFR2.1 well under 100 ms. The user never waits behind consolidation or trigger work.

### D-013 — Interfaces: a daemon with JSON-RPC over WebSocket, a React UI, and a CLI
- **Date:** 2026-10-25 · **Status:** accepted
- **Decision:** Thin clients talk to one daemon. The web UI is served as static files, the CLI is Typer + Rich, and a tray icon and toasts complete the set.
- **Alternatives:** Electron or Tauri. Electron costs RAM, and Tauri needs a Rust toolchain.

### D-014 — Python 3.12 via uv
- **Date:** 2026-10-25 · **Status:** accepted
- **Context:** The installed Python is the Microsoft Store 3.13 build, which redirects some AppData writes. Windows py3.12 wheels were confirmed for
  `sqlcipher3` 0.6.4, `apsw-sqlite3mc` 3.54.0.0, `pywin32` 312, `mcp` 2.3.0, `pystray` 0.19.5, and `windows-toasts` 1.3.1.
- **Decision:** Use a uv-managed Python 3.12 for the project.

### D-015 — Runtime data lives outside OneDrive
- **Date:** 2026-10-25 · **Status:** accepted
- **Decision:** The vault, keys, logs, workspaces, and the sandbox Python go in `%LOCALAPPDATA%\Ragnarok\`. The project folder under OneDrive holds only code and docs.

### D-016 — Out of scope: voice, screen capture, GUI automation
- **Date:** 2026-10-25 · **Status:** accepted
- **Context:** None of these appear in the PDF. They add latency, GPU and CPU contention, and demo risk.
- **Revisit if:** All the "must" and "should" items are done before hour 30.

### D-017 — Project context and logs
- **Date:** 2026-10-25 · **Status:** accepted
- **Decision:**
  - `AGENTS.md` holds the project context; Codex reads it directly, and `CLAUDE.md` imports it for Claude Code.
  - `docs/DECISIONS.md` records why things were chosen; `docs/WORKLOG.md` records what happened and what was measured.
  - Old design docs are kept in `docs/history/`.

### D-018 — Persistent memory is the primary goal
- **Date:** 2026-10-25 · **Status:** accepted (supersedes the priority order in D-001)
- **Context:** The team decided that remembering the user across days is the core of the project. The other pillars are optional.
- **Decision:** Build and demo memory first (`docs/MEMORY.md` milestones M0–M6). Skills, planning, triggers, and recovery
  work only after the memory milestones are done, or where memory needs them (e.g. a minimal chat loop for the demo).
- **Consequences:** The first demo is "it remembers you after a restart and on a new day", with measured recall and latency.

### D-019 — Memory storage: SQLCipher source of truth + FTS5 + Turbovec in RAM
- **Date:** 2026-10-25 · **Status:** accepted, pending the M0 checks (supersedes the "no Turbovec" part of D-005)
- **Context:** The team's initial memory plan uses SQLite + FTS5 + a Turbovec/TurboQuant vector index + RRF. `turbovec` 1.1.2
  has a Windows py3.12 wheel, ID-mapped indexes with remove, and allowlist search (to be confirmed in M0).
- **Decision:**
  - All memory records, provenance, and float32 embeddings live in the encrypted SQLCipher vault.
  - Turbovec is used as the vector index **in RAM only**, rebuilt from the vault at startup. If it is ever saved, the saved bytes must be encrypted.
  - The vector index sits behind a `VectorIndex` interface, with exact numpy search as the baseline and fallback.
  - Memories are two-tier (`events` → `memories`), and `origin` + `confidence` keep inferences separate from facts.
- **Alternatives:** Mem0 (good extraction logic but its own unencrypted stores; we borrow its ADD/UPDATE/SUPERSEDE/NOOP idea),
  Letta (a full agent server, too heavy), Graphiti/Zep (needs Neo4j, too heavy), basic-memory (plaintext markdown files).
- **Revisit if:** Turbovec recall@10 against exact search is < 0.95 at the chosen bit width, or the source shows the API doesn't fit.

### D-020 — Store the Turbovec index inside the vault as bytes
- **Date:** 2026-10-25 · **Status:** accepted (refines D-019)
- **Context:** M0 found that `IdMapIndex.to_bytes()` / `from_bytes()` serialize the whole index in memory, byte-identical to the `.tvim` file.
  D-019 said the index would be RAM-only and rebuilt at startup.
- **Decision:**
  - Persist the index as a BLOB in the encrypted vault (checkpointed after batches of changes), so a restart loads it instead of rebuilding.
  - The `embeddings` table remains the ground truth. If the blob is missing, corrupt, or its count doesn't match the active memories, rebuild from `embeddings`.
  - No `.tvim` file is ever written to disk.
- **Consequences:** Faster, simpler restarts; encryption still covers the index. Fixed overhead of about 0.86 MB (4-bit) per index.
- **Revisit if:** the blob grows too large to checkpoint quickly (then checkpoint less often and rely on the outbox).

### D-021 — Start Ollama through a script that applies and verifies its settings
- **Date:** 2026-10-25 · **Status:** accepted
- **Context:** The installer launched Ollama from a shell that predated our `OLLAMA_*` user variables, so the first server ran with defaults
  (`Ollama cloud disabled: false`). The tray app has the same problem whenever it is launched from a stale environment.
- **Decision:** `scripts/start_ollama.ps1` loads the variables, restarts `ollama serve` with its log in `%LOCALAPPDATA%\Ragnarok\logs`, and prints the applied
  settings from the server's own "server config" line. `rag doctor` (later) will do the same check.
- **Consequences:** We can show evidence that local-only mode is on (`Ollama cloud disabled: true`) instead of assuming it.

### D-022 — The vault takes a raw key; key wrapping is a separate, later layer
- **Date:** 2026-10-25 · **Status:** accepted (refines D-006)
- **Context:** M1 needs an encrypted file now. Passphrase wrapping (Argon2id + AES-GCM), the recovery code, and DPAPI need new
  dependencies (`argon2-cffi`, `cryptography`, `pywin32`), and the project rules say to ask before adding dependencies or touching key handling.
- **Decision:** `Vault.open(path, key)` accepts exactly 32 raw bytes, applies the key with `PRAGMA key = "x'<hex>'"`, and never logs or stores it.
  Schema migrations are numbered SQL files in `ragnarok/memory/migrations/`, applied in one transaction each and tracked in `PRAGMA user_version`.
  A file with a newer version than the code is refused.
- **Consequences:** Tests use random keys. Key management (D-006) is added later without changing the vault API; until then no real user key exists.
- **Revisit if:** the team approves the key-wrapping dependencies.

### D-023 — Index consistency: outbox in the write transaction, checkpoint records the outbox id it covers
- **Date:** 2026-10-25 · **Status:** accepted (implements D-019/D-020)
- **Context:** The vector index lives in RAM and is saved as a blob in the vault. A crash can leave the blob behind the database.
- **Decision:**
  - `add_memory` writes the memory, its embedding, and an `index_outbox` row in one transaction, then applies the outbox to the index.
  - A checkpoint stores the index bytes plus the highest outbox id it includes (migration 0002).
  - On start: load the blob, replay outbox rows with a higher id, and compare the index count with the active embeddings.
    A missing or corrupt blob, or a count mismatch, triggers a full rebuild from `embeddings`.
- **Consequences:** Nothing written before a crash is lost from search, and a bad blob costs one rebuild instead of wrong answers.
  Count comparison cannot detect a swapped id (one add lost, one remove lost); the rebuild path is the backstop and the M6 benchmark checks recall.

### D-024 — Async tests run through a small conftest hook, not pytest-asyncio
- **Date:** 2026-10-25 · **Status:** accepted
- **Context:** The memory service is async. `pytest-asyncio` would be a new dependency, and the project rules say to ask before adding one.
- **Decision:** `tests/conftest.py` runs `async def` tests with `asyncio.run`. Fixtures stay synchronous.
- **Revisit if:** we need async fixtures or shared event loops; then ask to add `pytest-asyncio`.

### D-025 — Key wrapping implemented; dependencies approved
- **Date:** 2026-10-25 · **Status:** accepted (implements D-006, resolves the "revisit if" in D-022)
- **Context:** The team lead approved adding `argon2-cffi`, `cryptography` and `pywin32` (asked per the project rules).
- **Decision:** `ragnarok/vault/keys.py`:
  - a random 256-bit vault key, never stored in plaintext;
  - passphrase wrap = Argon2id (time 6, 256 MiB, 4 lanes, about 0.4 s measured) + AES-256-GCM;
  - recovery wrap = HKDF-SHA256 of a one-time 160-bit code + AES-256-GCM;
  - each wrap's AAD names its purpose, so a wrap can't be moved to the other slot;
  - changing or resetting the passphrase re-wraps the key without touching the vault file;
  - opt-in DPAPI copy (current Windows user, fixed entropy string).
  `key.json` stores the KDF parameters, so cost can be raised later without breaking old keystores.
- **Consequences:** `keyring` (Credential Manager for account secrets) is not added yet, since no account secrets exist.
  DPAPI means anyone logged in as the user can unlock, as stated in the threat model (ARCHITECTURE 6.3).

### D-026 — Defaults from the retrieval benchmark: exact index, `min_semantic` 0.55
- **Date:** 2026-10-25 · **Status:** accepted (resolves the "revisit if" in D-019; supersedes the Turbovec default in D-019/D-020)
- **Context:** `bench/retrieval_bench.py` ran 124 labelled queries over 500 active memories with real `nomic-embed-text` vectors.
  Turbovec 4-bit reached recall@10 of 0.920 against exact search, below the 0.95 bar set in D-019. Through the whole pipeline, though, both indexes
  scored the same (recall@5 0.895, MRR 0.835 vs 0.837). Turbovec is faster only at large sizes (50k vectors: 1.1 ms vs 6.7 ms).
  Vector search always returns neighbours, and nomic scores unrelated text around 0.5, so a cutoff is needed.
- **Decision:**
  - `MemoryStore` defaults to `ExactIndex`. `TurbovecIndex` stays available (`index_kind="turbovec"`) for corpora in the tens of thousands, and the
    checkpoint/replay code and its tests are kept. If we switch to it, add exact rescoring of its top candidates first.
  - `DEFAULT_MIN_SEMANTIC = 0.55`. Sweep: recall@5 0.911 (no cutoff) -> 0.901 (0.55) -> 0.895 (0.60) -> 0.813 (0.65). Paraphrase recall@5 is
    0.82 / 0.77 / 0.74 / 0.56 across the same steps. The utility function in the bench preferred 0.60, but it sits close to a cliff, and a missed memory
    costs more than an irrelevant one (the model receives memories as data and can ignore them), so I chose 0.55.
- **Consequences:** The cutoff is not a precise relevance filter: 47% of unanswerable queries still return something. Deciding relevance is left to
  the chat model, within the ~1,000-token memory budget.
- **Revisit if:** the corpus grows past about 20k memories, or the embedding model changes (the cutoff is specific to `nomic-embed-text`).

### D-027 — Ingestion rules enforced in code, not left to the 4B model
- **Date:** 2026-10-25 · **Status:** accepted
- **Context:** The first real-model run of the M3 pipeline showed two failures that a good pass rate hid: the model labelled a changed value
  ("meetings after 14:00" -> "after 15:00 now") as UPDATE, which in our pipeline would have kept the old value and lost the new one; and it
  wrote a past date for a yearless future date ("anniversary on 14 February" became 2026-02-14 in October 2026).
- **Decision:**
  - An unusable decision (unknown action, or a target that isn't one of the neighbours shown to the model) becomes ADD. A failed decision call also becomes ADD. Nothing is ever deleted on a guess.
  - UPDATE is applied as a plain reinforcement only when the new statement adds no words the stored one lacks; otherwise it is applied as SUPERSEDE, which keeps the new
    detail and leaves the old version in the history.
  - An inferred memory can never supersede a user-stated one; the two are linked as `contradicts` and both stay.
  - The model can't claim more certainty than the evidence: `user_stated` needs a user message in the batch (else `observed` or `inferred`).
  - A yearless date is moved to its next occurrence (`roll_past_dates`) unless the user gave a year or their wording is past tense ("went", "last", "ago"...).
    This is a heuristic; the model labels nearly every dated item `episodic`, so memory type can't be used for it.
  - If the model is down, events stay pending and are retried; assistant-only batches never reach the model.
- **Consequences:** More superseded rows from reworded facts (cheap, and history is kept). The pipeline's safety no longer depends on prompt quality.

### D-028 — The chat loop, tools, and permission check (M4)
- **Date:** 2026-10-25 · **Status:** accepted
- **Decision:**
  - The agent is a plain loop (`ragnarok/runtime/loop.py`): log the user message, retrieve memory (no model call besides the query embedding), build the prompt,
    let the model call tools until it answers. Limits: 6 model calls per turn; the same call with the same arguments three times stops the turn.
  - Prompt order for cache reuse: system prefix + chat rules + `<profile>` block (changes only when memory changes) | history | the retrieved
    `<memory_context>` in the last user message only. Memories are always delimited as data, and the system prompt says never to follow instructions inside them.
  - Two tools for now: `memory_search` (R0) and `memory_save` (R1, idempotent). Each declares schema, risk, idempotency, timeout, sandbox, fs and network.
    Arguments are validated with pydantic; errors go back to the model as text.
  - Every call goes through `policy/permissions.check` (deny, ask, allow, then the default by risk). Profiles are code for now (`interactive`, `background`); there is no YAML file yet.
  - A tool call is written to the event log **before** it runs. Tool traffic uses event kinds that extraction never reads, so the agent can't learn from its own search results.
  - `memory_save` records the user's message as the memory's source, so extraction at session end recognises the same assertion and reinforces it instead of re-versioning it.
  - The CLI is `argparse` (no Typer dependency yet): `init`, `chat`, `memory ...`, `ingest`, `doctor`. Models are warmed once at start. The data folder must not be inside OneDrive.
- **Consequences:** `rag chat` works end to end on the real models. Background profile (`ask` for everything with a side effect) exists, but nothing waits in an Inbox yet.

### D-029 — Ingestion must never lose a fact (found by the real-model demo)
- **Date:** 2026-10-25 · **Status:** accepted (extends D-027)
- **Context:** Running the full flow on the real models found two silent data-loss paths that the scripted tests had not covered:
  1. The decision step answered NOOP for "my manager is Ravi" against an unrelated meeting preference, so the fact was never stored.
  2. "prefers meetings after 3pm" scored cosine 0.98 against "after 2pm", so the near-duplicate shortcut swallowed the change.
- **Measured (nomic-embed-text, `search_document:` on both sides):** reworded same fact 0.885-0.990; changed value 0.627-0.980; different fact 0.525-0.821.
  The ranges overlap, so no similarity threshold can tell "changed" from "different".
- **Decision:**
  - A near-duplicate is dropped only if the new statement also adds no words the stored one lacks.
  - A NOOP/UPDATE whose target doesn't contain the new statement becomes SUPERSEDE if cosine >= 0.9 (a reword or change; the old version stays in history), otherwise ADD (keep both).
  - Rule of thumb: when unsure, keep more, never less. Duplicates and extra versions are cheap; a lost fact is not.
- **Known risk:** the model's explicit SUPERSEDE is trusted. A wrong one hides a fact in the history (not deleted). There is no "undo supersede" command yet.

### D-030 — Actions end their own turn; the terminal screen; the app-opening skill
- **Date:** 2026-10-25 · **Status:** accepted
- **Context:** The owner tried "open steam" and "open microsoft edge" and the agent did nothing and invented answers: it only had memory tools. Chat also felt slow:
  tool turns took 2.2-3.9 s because the model was called a second time just to say "Saved" or "Opened".
- **Decision:**
  - New skill `open_app` (`ragnarok/skills/apps.py`): the model supplies a name; the program comes from Windows' own Start menu list (`Get-StartApps`), is chosen by code,
    and is started with `explorer.exe shell:AppsFolder\<id>`, so no model text ever reaches a command line. Ambiguous names ask instead of guessing, web-link entries and uninstallers
    are skipped, command shells and admin tools are refused. Risk R1, sandbox declared `full-access` (a launched app runs as the user); the interactive profile allows R1, the background profile asks.
  - `ToolResult(text, final=True)`: a tool whose text already answers the user ends the turn with no second model call (`Agent.fast_finish`). Data-returning tools (`memory_search`),
    errors, denials and mixed batches still go back to the model. `memory_save` now answers `Saved: <what was stored> [m:N]`, which is also more faithful than the model's paraphrase.
  - The optional `reason` argument is hidden from the model (it cost about 19 generated tokens per call, 0.4 s, and only restated the request); it is still accepted and logged if given.
  - The chat prompt says the assistant can do only what its tools allow and must not invent details; replies are one or two short sentences.
  - Terminal screen (`ragnarok/ui.py`, `ragnarok/chat.py`): plain ANSI, no new dependency. One hue per kind of thing, off for pipes and `NO_COLOR`, ASCII fallback if the console cannot print
    box characters. Models, the Start-menu app list, and learning all happen in the background; learning is cancelled the moment the user types, so quitting is instant.
    Unlearned events stay pending and are read at the next session (the screen says so).
- **Consequences:** Learning from a conversation now happens during idle time (15 s) or at the next start, not at exit. `rag ingest` and the next `chat` catch up.

### D-031 — Read-only file skill (find, list, read) with a narrow scope
- **Date:** 2026-10-25 · **Status:** accepted
- **Context:** The owner asked "is there any file related to datastructure in my desktop" and the agent correctly said it had no file tool.
- **Decision:** `ragnarok/skills/files.py` adds `find_files`, `list_folder` and `read_file`, all R0, read-only, declared with the roots they may touch.
  - **Scope:** only Desktop, Documents and Downloads, including the OneDrive copies (this laptop's real Desktop is `OneDrive\Desktop`). Paths are resolved first, so `..`, absolute paths
    outside the roots, symlinks and directory junctions that lead out are refused. Tested with a real junction on Windows.
  - **Secrets:** names matching keys, password stores, credentials, `.env`, `.ssh` and similar are skipped by every walk and refused even by exact path. `node_modules`, `.git`, virtualenvs and build folders are skipped.
  - **Search:** file and folder names first, ignoring case, separators and plurals (so "datastructure" finds `Data_structure_certificate.pdf`); every word must match. Only when fewer than five names match
    does it also look inside text files (at most 400 files under 1 MB, within a 6 s budget). The model does not choose the mode: when it was allowed to, the 4B model picked "inside files"
    for the owner's own question and missed the obvious file. Results rank shallow paths first, files before folders.
  - **Reading:** plain-text types only, 3,500 characters per call with an `offset` to continue. The text is wrapped in `<file_content>` tags, which the system prompt treats as data. PDFs and Office files are
    reported as unsupported (no parsing library is installed).
  - **Fast and exact:** a plain lookup ("is there a file about X", "what is in Downloads") that found something shows the list itself and ends the turn (no second model call; the model re-typing long paths was slow and
    error-prone). If the message asks for more ("...and tell me its first line", open, read, summarise, newest, largest...), or nothing was found, the results go back to the model.
  - The event log keeps only the first 300 characters of any tool result.
- **Consequences:** The agent still cannot open, change, move or delete files, and cannot read PDFs. "Open that file" is the natural next skill (it needs an approval rule, since it starts a program).
- **Test hygiene found on the way:** `default_roots` read the real `OneDrive` environment variable, so tests with a pretend home searched the owner's real folders. Environment-based roots now apply only to the real profile.
