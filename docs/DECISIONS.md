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

### D-032 — One file for the persistent-memory lifecycle: `ragnarok/persistent_memory.py`
- **Date:** 2026-10-25 · **Status:** accepted
- **Context:** The memory logic was correct but spread over the vault, store, ingest, profile and service modules, so there was no single place to read, demo or explain it.
  `docs/MEMORY.md` section 9 had described a "module API" that did not exist as one thing.
- **Decision:** `PersistentMemory` wraps the existing pieces (no logic is duplicated) behind one async object: `observe`, `learn`, `remember`, `recall`, `profile`, `get/list/history/stats`,
  `correct`, `revert`, `pin/unpin`, `archive/restore`, `forget`, `export`, `checkpoint`, `close`. The module docstring explains the whole design in plain language (two tiers, writing, reading, changing your mind, durability, what is not built).
  New store primitives behind it: `set_pinned`, `revert` (only the fact directly before the current one can come back, so there is never more than one active version; the newer one is archived, not deleted) and `stats`.
  `export` writes unencrypted JSON lines and refuses unless the caller passes `allow_plaintext=True`.
- **Consequences:** Delivers the user-control part of M5 at the API level (correct, revert, pin, export). Not yet wired into the CLI commands. Automatic forgetting and summaries are still open.

### D-033 — M5: consolidation, forgetting by usefulness, and the delete cascade
- **Date:** 2026-10-25 · **Status:** accepted
- **Context:** Memory has to stay useful after months, not just days: raw episodic notes pile up, and the PDF requires both consolidation (FR1.3) and permanent deletion (NFR4.2) that really removes everything derived from a fact.
- **Decision:**
  - **Consolidation** (`memory/consolidate.py`): each finished week (at least 7 days old, at least 4 notes) of uncovered episodic memories per project is summarised by the local model. The summary is a *proposal*:
    code stores it only if it cites at least two memories that are really in the group, is under 400 characters, and contains no name, number or digit-bearing word (`mp4`, `w3`) that the cited memories do not contain
    (hyphenated forms such as "30-day" are fine when every part appears). A stored summary is an ordinary memory of type `summary`, origin `inferred`, confidence no higher than its lowest source, importance the highest,
    linked `derived_from` to each source. The sources are not modified. One transaction per summary, so a crash or a failed model call loses nothing and the next run retries. At most 5 summaries per call.
  - **Forgetting** (`memory/forget.py`): usefulness = 0.35 importance + 0.25 recency (30-day half-life) + 0.25 use (relative to the most used memory) + 0.15 confidence. A memory is archived only if it is active, unpinned,
    not a summary, below 0.35, **and already covered by an active summary**. Nothing is archived just for being old; stated preferences and facts, which are never summarised, stay forever.
  - **Delete cascade:** deleting a memory also deletes every summary built from it (a summary can repeat its content) in the same transaction, with content-free tombstones for each. The surviving sources become eligible
    for a fresh summary. `undo_summary` deletes a summary and un-archives what pruning had hidden.
  - **Maintenance** (`PersistentMemory.maintain`): consolidate, prune and expire old raw events, at most once a day (state in a new `meta` table, migration 0004). The chat runs it in idle time after learning, and it is
    cancelled the moment the user types. Errors there never reach the chat.
  - **CLI:** `memory pin|unpin|correct|revert|export|stats|consolidate|prune|maintain|undo-summary`, `memory list --type`. Export needs `--yes-plaintext`.
- **Consequences:** Identical summary text from two weeks is merged into one summary by the duplicate rule (the second week's sources attach to it). Grouping is by project and week, not by entity.
- **Revisit if:** real use shows summaries lose too much, or weekly grouping is too coarse (try per-entity groups).

### D-034 — `open_website`: the agent can open sites and searches, and stops cutting multi-step requests short
- **Date:** 2026-10-25 · **Status:** accepted
- **Context:** The owner typed "open chrome and go to yt and search carrymanati". Chrome opened and nothing else happened, for two reasons: there was no tool that opens a web page, and the speed shortcut from D-030
  (a successful `open_app` ends the turn) stopped the sentence after its first step.
- **Decision:**
  - `ragnarok/skills/web.py` adds `open_website(site, search?, browser?)`. The model names a site ("yt", "wikipedia") and search words; **code builds the URL** from a fixed table (YouTube, Google, Wikipedia, GitHub,
    Stack Overflow, Reddit, Maps, Gmail, Drive, ChatGPT; common aliases) and URL-encodes the search text, so search text can never change the host or add parameters. Sites without a search page are searched through Google (`site:host words`).
  - **Any other address opens only if the user typed it in the message being answered.** Text found in a file or a memory therefore cannot send the browser anywhere. Whatever was typed must still be an ordinary https domain: no `file:`,
    `javascript:`, localhost, IP addresses, ports, embedded credentials, spaces or markup. 19 hostile forms are tested.
  - **Browser choice:** `chrome`, `edge`, `firefox` or the default. Named browsers are started from their registered install path (Windows `App Paths`) with our validated URL as the only argument; if the named browser is missing the default is used and
    the answer says so. (On this laptop the default is Edge, so "open chrome" has to launch Chrome itself.)
  - **It opens pages; it cannot read, click or play anything on them.** If the message asks for that ("play the first video"), the tool says so and the model passes it on; the real model did.
  - `open_app` now hands control back to the model when the message has more steps ("...and go to...", "...then search..."); a bare "open steam" still ends instantly. The prompt tells the model to use the website tool directly and not open the browser app first.
  - Risk R1 (closing the tab undoes it), sandbox declared `full-access` (a browser runs as the user), network declared as "via the user's browser".
- **Consequences:** The agent itself still makes no network connections; the browser does, as the user would. Reading page contents would be a separate, riskier skill (untrusted web text) and is not planned yet.
- **Verified:** real model + fake browser on six requests (correct tool, URL and browser each time, 1-2.6 s); one real launch of Chrome to the YouTube search (a new Chrome process started; the page itself was not inspected).

### D-035 — Screen skill: `read_screen` and `use_computer` (read the screen, act on it) — experimental
- **Date:** 2026-10-25 · **Status:** accepted for reading; **experimental for acting** (measured success is low, see below)
- **Context:** The owner asked the agent to read the screen and do the tasks they give it. No existing tool could see or click. The chat model `qwen3.5:4b` accepts images, so no second model is needed (hard rule 7).
- **Decision:**
  - **Same model, same gateway, no new dependencies.** Capture is Windows GDI through pywin32 and numpy, with a small hand-written PNG encoder; mouse and keyboard are `SendInput` through ctypes. Screenshots live in memory for one step and are never written to disk (rule 2).
  - **`read_screen` (R1, read-only):** one capture of the primary monitor, one model call, answers the question. It runs only if the user's own message asks about the screen (`asks_about_screen`), so text found on the screen or in a file cannot make the agent look. Its text answer is returned as `<screen_content>` data, never instructions.
  - **`use_computer` (R2, asks the user for each task):** look, choose one action, check, log, act, look again. At most 15 steps and 150 s. Actions: click, double_click, right_click, `fill` (click a field and type), `type`, `key`, `scroll`, `wait`, `done`, `fail`. Every action goes through `permissions.check` (rule 3) and is logged as a `screen_action` event before it runs (rule 5); the work runs in worker threads (rule 6).
  - **Safety rules in code:** Esc or moving the mouse stops it; the user's own terminal is minimised during the task and always restored; it never types into terminals, the Run box, UAC or system tools (checked again after a `fill`'s click, right before the first letter); key allow-list (no alt+f4, win+r, ctrl+alt+*); text that looks like a password, key or card number is refused; targets that look irreversible (delete, send, submit, pay, install, allow, sign out ...) need a fresh yes, and Enter/Space after such a target does too; "done" is never taken on faith (a second look at the screen only); unchanged screen (3 times), repeated action (3 in a row) and wrong "done" (twice) all end the task with an honest failure.
  - **Pointing:** the model answers "where is X" on a 0 to 1000 scale whatever the prompt says, so the prompt asks for that scale and code converts it (`Frame.to_screen`). Measured on synthetic screens at 1280x720: 48 of 48 points inside the right element, median miss 5 px of 1920, about 1.3 s per look (about 1.9 s with the stricter reply schema).
- **What the live test found and changed** (`bench/screen_eval.py` drives a small test window with the real model, real capture and real mouse):
  - A loose JSON schema let the model answer `{"action":"click","x":189,"text":"Submit"}` (no y). Now one schema variant per action, each listing the fields it needs.
  - It had the scroll sign backwards (`-500` for "down"). Now `direction: up|down`.
  - It often typed without clicking the field first. `fill` does both in one action.
  - Change detection averaged a 48-column thumbnail and could not see typed text. It now counts changed pixels (cursor blink stays under the bar).
  - The completion check, when given the list of steps, repeated them back as proof ("the fields are filled") although the typing never arrived. It now sees the screen only. Wanting to repeat an action triggers that check first, instead of a blind stop or a third irreversible click.
  - A scroll moved the mouse and the next step read that as the user moving it. Fixed.
- **Consequences / limits (read these):**
  - **Reading the screen is dependable; acting is not yet.** On the test window, 3 trials of 5 tasks: **5 of 15 succeeded** (form 2/3, buttons 1/3, search 1/3, scroll 1/3, list 0/3). Typical misses: wrong row in a dense list, an extra click on a button that had already worked, giving up on a long scroll. One run claimed success while the check failed.
  - Three runs ended with "you moved the mouse". The owner was probably using the laptop at the time, which is the intended behaviour, but it also lowers the number.
  - The approval prompt says it is experimental. `RAGNAROK_NO_SCREEN=1` turns both tools off.
  - Prompt injection through what is on screen is reduced, not removed: screen text is declared content, risky targets need a yes, and typed text is screened, but a hostile page could still mislead a 4B model into a wrong (non-risky-looking) click.
  - The test window shows a status line after each action, like real apps. A first run without it did worse (2 of 5), because some correct clicks showed no change.
- **Revisit if:** a zoom-in step (second look at a crop before clicking) lifts list and small-button accuracy; a larger vision model fits in 6 GB; or Windows UI Automation (reading controls by name instead of pixels) is worth adding for apps that expose it.

### D-036 — Downloads: the agent makes its first direct internet connections (`download_images`, `download_file`)
- **Date:** 2026-10-25 · **Status:** accepted · **Supersedes** the line in D-034 that "the agent itself makes no network connections".
- **Context:** The owner typed "download cat images" and got a Google search page, then said it "should be able to download anything I said". `open_website` only starts the browser, so nothing could be saved.
- **Decision:** two tools, each narrow on purpose. Model calls (hard rule 1) are unchanged: the gateway still talks only to 127.0.0.1; these tools fetch data, not completions.
  - **`download_images(query, count)`** (R1): searches **Wikimedia Commons** (free images with a named author and licence) and saves up to 10 pictures to `Downloads\Ragnarok\<topic>\` with `credits.txt` (author, licence, page). Three fixed hosts (`commons.wikimedia.org`, `upload.wikimedia.org`, `thumb.wikimedia.org`); the model never writes a URL; redirects are not followed. The **search words must come from the user's own message**, so text found in a file or a memory cannot leave the machine as a search query. Only jpeg/png/webp/gif whose first bytes match the claimed type are kept; 12 MB each, 40 MB in all.
  - **`download_file(source)`** (R2, so the owner is asked before every download and sees the name or link): `source` is either a **name from a table of official installers** (steam, chrome, firefox, vscode, spotify, zoom, telegram, edge, ollama, python 3.12.8; each link was checked to return a real installer) or **a link the user typed in the message**. The name or link is checked while the arguments are parsed, so the owner is never asked about a download that cannot go ahead. https only, a real domain name (no IP addresses, ports or credentials); the name is resolved first and refused if any address is private, loopback, link-local (including the cloud metadata address) or otherwise non-public; every redirect (at most 5) is checked the same way; HTML answers are refused ("that is a web page, not a file"); at most 2 GB and enough free disk; the file name is taken from the server but cleaned for Windows; nothing is overwritten (a second copy is `name (1).ext`, the same file again is "already downloaded"); a half-finished download is deleted (also on timeout or cancel). Files get the **internet mark** (Zone.Identifier) so SmartScreen checks a program before it runs, and the reply shows the SHA-256. **A program is saved, never opened or run.**
  - Both tools write only under `Downloads\Ragnarok`, in the folder Windows names Downloads (not a guess: Documents is OneDrive-redirected on this laptop). `RAGNAROK_NO_DOWNLOADS=1` turns both off.
- **Consequences / limits:**
  - "Anything" is bounded by what can be named safely. There is **no search for arbitrary files**: for "download photoshop" or a paper title the tool answers that it needs the link pasted into the chat (a made-up link is never fetched). Things behind a login, a store or a paywall (Steam games, movies, music) are out of scope.
  - DNS rebinding (a name that resolves to a public address when checked and a private one when connected) is not closed: the check and the connection resolve separately. The tool only writes what it fetches to disk and never returns it to the model, which limits the damage to a blind GET.
  - The Steam installer's SHA-256 was recorded but cannot be compared with a published one (vendors rarely publish them).
  - The first real run found the live Commons API had moved thumbnails to `thumb.wikimedia.org`, which the allow-list correctly refused ("no pictures found"); the host was added after checking it is first-party. Mock-only tests would not have shown this.
- **Verified:** real internet, temp folder: 3 cat photos (211 to 533 KB) with credits in 3.8 s; Steam's 2.3 MB installer in 0.8 s (valid `MZ` header, internet mark present, no leftovers); a web-page link refused. Real model: "download cat images" and "3 pictures of dogs" pick `download_images`; "download steam", "chrome and vs code", "python" and a typed link pick `download_file`; "download photoshop" and a paper title get a request for the link.

### D-037 — Documents the agent can create, folders found by name, and two screen fixes from real use
- **Date:** 2026-10-25 · **Status:** accepted
- **Context:** The owner asked for "a doc file for the files in the deep learning folder". Two things were missing: a tool that creates a file, and a way to list a folder by its name (the model asked for `all` and got the Desktop). The attempt fell back to `use_computer`, which failed twice (below).
- **Decision:**
  - **`create_document(title, content, format)`** (R1): makes a **new** `.docx` (default), `.md` or `.txt` in `Documents\Ragnarok`. It cannot overwrite or edit anything: a different document with the same title becomes `Title (1).docx`; the same one again is "already created"; a crash leaves no half file. The `.docx` is built by hand (three small XML parts in a zip, fixed timestamps so equal input gives equal bytes), so there is no new dependency. Text is XML-escaped and control characters are dropped. **Checked in real Microsoft Word 16** (private invisible instance, temp copy): title bold 20 pt, heading bold 15 pt, bullets, `&`, `<b>`, arrows and Japanese all intact.
  - **Rule 2 note:** this writes plaintext the user asked for into their own Documents folder, like `memory export` (explicit request, new file, one fixed folder). It never writes vault contents, and nothing from it goes to the debug log.
  - **Folders by name:** `list_folder` and `find_files` accept "deep learning" and find `Desktop\Deep_learning` (shallowest, then most recent match; secret and hidden folders are skipped; unknown names are reported by name). "all" or an empty folder now asks which folder instead of listing the Desktop.
  - **Where files go:** a small `folders.py` asks Windows for the real Desktop/Documents/Downloads (they are often redirected); the file skill can read those too.
  - **Screen skill, from the owner's real run:** (1) after our terminal is minimised Windows gives the focus to the next window, which here was another Windows Terminal; typing there was correctly refused, but the model now gets "click the program you want to use first" in the refusal and in the prompt. (2) The "you moved the mouse" threshold went from 40 to 120 px: a probe showed the cursor lands exactly where it is sent (0 of 6 far-apart points off), so the stop was a nudge while watching, not a bug.
- **Consequences:** The document is only as good as the text the 4B model writes; for "the files in a folder" it copied the five exact file names from the listing (real model, temp tree). No editing of existing documents, no images or tables.

### D-038 — Day-to-day work: permission modes, file and command tools, documents, decks and sheets
- **Date:** 2026-10-26 · **Status:** accepted
- **Context:** The owner's real requests kept failing: "make a txt of this PDF" and "make a folder and paste that file in it" got "I can't", and "make a python program" just opened Notepad. They asked to "give it all the permissions" and to do what Claude Code does, with 6 hours left. Nothing in the agent could read a PDF, write a file, move one or run a command.
- **Decision:**
  - **Three permission modes** (`ragnarok/policy/modes.py`), like Claude Code's, switched live with `/mode ask|edits|full` or started with `RAGNAROK_MODE`. **ask** (default; asked first, as the owner chose): R0/R1 run, R2 and R3 ask. **edits**: R0-R2 run, R3 (delete, commands) asks. **full**: nothing asks except moving the real mouse (`use_computer`). The owner chose "ask first, `/mode full` to unlock" rather than full access from the start. `edits` and `full` also let the file tools read the whole user profile (ask: Desktop, Documents, Downloads).
  - **Hard limits in every mode** (`skills/pathguard.py`, one place): Ragnarok's own data (the vault), system folders and other drives' roots, other programs' saved logins (`.ssh`, `.aws`, `.claude`, `.codex`, `AppData`, ...), files named like keys and passwords (for reading), and the profile folders themselves (never moved or deleted as a whole). Paths are resolved first, so `..` and links cannot lead out. Files that run by themselves when a shell starts (PowerShell profile, `.bashrc`) can only be changed in `full`.
  - **Seven file tools** (`skills/fileops.py`): `make_folder`, `create_file`, `copy_path`, `extract_text` (R1: they add, never replace), `edit_file`, `move_path` (R2), `delete_path` (R3, **Recycle Bin only**; there is no permanent delete). Writes are atomic, nothing is overwritten by an adding tool, copies refuse a folder inside itself and caps (4 GB / 50,000 files).
  - **`run_command`** (`skills/shell.py`, R3): PowerShell, no stdin, 60 s default and 300 s maximum, then the command and everything it started are killed; output capped and returned inside `<command_output>` (data, never instructions); the child gets the environment without anything named like a key, token, secret or password and without our own settings. A refusal list stays on in every mode: disk and boot tools, recursive deletes aimed at a drive, the system or the profile, download-and-run, encoded commands, scheduled tasks, services, registry and security settings, shutdown, saved logins and the vault. **This is not a sandbox**: the list stops honest mistakes of a small model and the common injected one-liners, not a determined attacker.
  - **Documents, decks and sheets** (`skills/documents.py`, `skills/office.py`): `create_document` (.docx/.md/.txt), `create_presentation` (.pptx from a `# Slide` / `- point` outline), `create_spreadsheet` (.xlsx from a table; numbers stay numbers; ordinary formulas stay formulas; anything that looks like DDE, WEBSERVICE, HYPERLINK, CALL and similar is stored as text). All built by hand from zipped XML, so **no dependency**, and checked in the real Word, PowerPoint and Excel (D-037 for Word; here a deck opens with its text and renders cleanly, a sheet calculates `=SUM`). Same input gives the same bytes; saved in `Documents\Ragnarok`, never over an existing file.
  - **PDF, Word and PowerPoint text** (`skills/extract.py`): `read_file` and `extract_text` read them. **New dependency `pypdf`** (pure Python, BSD), added with the owner's yes. Limits: 100 MB, 1500 pages, 5 million characters; encrypted PDFs and scanned pages (no text layer) are reported, not guessed at.
  - **Paths a model writes** (found by the real-model battery): the first run scored 19 of 26 because the model wrote `C:\Users\name\Downloads\...`, copying an example from our own tool description. Now: stand-in user names (`name`, `user`, `username`, ...) mean the real user; `Desktop`, `Documents`, `Downloads` mean the folders Windows shows (OneDrive-redirected on the owner's laptop); a bare name (`notes.txt`) is looked for in the usual folders and used if there is exactly one match (several are listed, not guessed); `rename it to ideas.txt` renames beside the source; a new file or folder with a bare name goes to `Documents\Ragnarok`. Another real user's profile is still refused.
- **Consequences / limits:**
  - A 4B model with this many tools needs the prompt and descriptions to be exact; every failure so far was a wording or path problem, not a missing ability. `bench/daily_tasks.py` (26 everyday requests, checked by what ends up on disk) is the regression test for it.
  - `full` mode with the command runner can do real damage that the refusal list does not cover; deletes through `delete_path` are recoverable, anything a command does is not.
  - No editing of Word/PowerPoint/Excel files that already exist, no images or charts in decks, no `.docx` tables.
- **Verified:** 981 tests; real PowerPoint, Excel and Word open the generated files; the real Recycle Bin call works; real PowerShell runs for the command tests (timeout kill, Unicode, environment, output cap); everyday battery on the real model **25 of 26** (the 26th was a too-strict check, fixed in the check).

### D-039 - Terminal look: Claude Code-style layout, 24-bit colour, and motion that switches itself off
- **Date:** 2026-10-26 - **Status:** accepted
- **Context:** The owner asked for a more colourful, unique, minimal and professional CLI "like Claude Code", with animations and transitions.
- **Decision:**
  - Layout follows Claude Code: a rounded welcome box, a hairline above the prompt that carries the mode, a round bullet for answers, a hook-arrow line for tool results, a star spinner with elapsed seconds, and a "Do you want to proceed?" permission card.
  - Colour is 24-bit (violet to cyan gradient) when the terminal says it supports it (`COLORTERM`, `WT_SESSION`, VS Code); otherwise the old 256-colour codes. `RAGNAROK_256=1` forces the fallback.
  - Motion: the welcome box draws itself and a light sweeps over the name; the spinner star changes colour and a highlight passes over its label; the goodbye star closes. Motion runs only on a colour terminal, never into a pipe or log, and `RAGNAROK_NO_ANIM=1` turns it off. Every wait is `asyncio.sleep`, so the event loop is never blocked.
  - Still plain ANSI: no new dependency (the project has no `rich`).
- **Consequences:** The old boxed banner and the `allow? [y/N]` wording are gone; tests were updated. The welcome animation adds about 0.7 s once at start.
- **Revisit if:** the animation feels slow on the owner's terminal (lower `pace` or drop the sweep).

### D-040 - Folder-to-file summaries are one tool, not a loop of file reads
- **Date:** 2026-10-26 - **Status:** accepted
- **Context:** "make a ppt on this folder" and "read the pdf and files in the folder and make a new pdf" ended with "I stopped after too many steps": the model read one file per step (6 steps) and never built the file. The real-model battery reproduced it.
- **Decision:**
  - `summarize_files` walks a folder (or one file) in code, reads PDF/Word/PowerPoint/text, summarizes each part with small focused model calls (about 6,000 characters each, at most 24 calls per request, evenly sampled for large files), and builds the output (pdf, pptx, docx, md, txt) itself. If the model does not answer, the text is quoted instead, so a file is still made.
  - A small PDF writer (`pdfwriter.py`) means no new dependency. The step cap is 8 and the cap message now says what ran and what failed.
  - `create_animation` (sorting animations, steps worked out in Python) and `create_webpage` (a page the model writes, saved with a Content-Security-Policy that blocks all network requests) replace "write a Python script and pip install matplotlib".
  - A result that leaves the turn open now tells the model it is finished, because it used to "verify" with run_command and loop.
- **Consequences:** Folder jobs take 16-18 s on the target laptop and never run out of steps. Output files are always new files in Documents\Ragnarok.

### D-041 - Passwords are never typed, and web addresses are never guessed
- **Date:** 2026-10-26 - **Status:** accepted
- **Context:** A user asked the agent to log in to a college site with a pasted id and password. The model invented an address (a different country's university) and typed it 15 times; the screen skill's typing guard only refused text that was labelled "password".
- **Decision:**
  - The screen skill refuses to type any password-shaped word (8-64 characters mixing cases, digits and a symbol) and any value the user labelled as a password, PIN, OTP, passcode or token in their message. The user logs in; the agent can then read the page.
  - It also refuses to type a web address the user did not write.
  - The prompt says: never guess an address (search for it or ask for the link), never log in for the user.
  - The event scrubber also covers passcode, passphrase, PIN and OTP and the `label:- value` form, so these never reach the vault or memory.
- **Consequences:** "Log in and check my attendance" becomes "open the site, you log in, then I read it". Tell users not to paste passwords into the chat.

### D-042 - Triggers run inside the chat process, poll folders, and headless goals can only propose
- **Date:** 2026-10-26 - **Status:** accepted
- **Context:** FR4 needs things that start by themselves. There is no always-on daemon yet, and `watchdog` is not a dependency.
- **Decision:**
  - `TriggerEngine` runs as a task inside the chat. Reminders (`date`), schedules (`cron`) and folder watches fire once: the fire is written to the vault before the action, with a unique `(trigger, dedupe key)`.
  - Downtime: a schedule that missed slots fires once (coalesced) and says it was late; a reminder that came due fires at the next start; folders are compared with a saved snapshot, and a file counts only when its size stopped changing. A fire that began but did not finish is completed (reminders) or reported as interrupted (goals), never re-run blindly.
  - Folders are polled every 2 s in a worker thread (no new dependency).
  - A `goal` trigger runs a headless agent in the `background` profile, which may read but not change anything; whatever it wanted to do is listed in the Inbox as "waiting for your OK". It waits while the user is mid-turn.
  - Desktop toasts use PowerShell's WinRT API (no `windows-toasts`); the text is passed through the environment, never the command line.
  - The tools are honest that reminders only fire while Ragnarok is open.
- **Alternatives:** `watchdog` (a dependency; revisit when the daemon exists), a Task Scheduler service now (needs the daemon first).
- **Consequences:** FR4.1 works in the chat session; true always-on needs the daemon (not built).
- **Revisit if:** the daemon is built: move the engine into it unchanged.

### D-043 - Planning: sequential steps, typed checks only where reliable, a shared circuit breaker
- **Date:** 2026-10-26 - **Status:** accepted
- **Decision:**
  - A plan (`planning/`) is JSON with steps that depend only on earlier steps. Each ready step runs as a fresh sub-agent; steps run one at a time (one model lane, and approvals must not arrive in parallel), but a failed step blocks only its dependents.
  - Ladder per step: retry (transient) -> fix (a retry gets every tool) -> re-plan (max 2) -> degrade. A refusal by the owner is final.
  - `done_when` is `tool_ok`, `file:<wildcard>` or a judged `text:`; guessed file names and placeholder conditions are downgraded to `tool_ok` because they failed good work in the real-model bench. A step titled to make or change something fails if only read tools ran.
  - Plan state is in its own vault tables (`plans`, `plan_steps`, `plan_log`), not the event stream.
  - `CircuitBreaker` (3 crashes or timeouts in a row pause a tool for 60 s) is shared by the chat, plan steps and headless goals.
- **Consequences:** `/plan`, `/resume`, and automatic planning for requests with three or more distinct actions.


### D-044 - The interface is a pixel cat on the taskbar (tkinter), and the terminal is optional
- **Date:** 2026-10-26 - **Status:** accepted (supersedes the "React web UI first" plan in AGENTS.md for now)
- **Context:** The owner does not want to open a terminal for every task. They asked for a small pixel cat that lives on the taskbar, moves like a real cat, and opens a short chat window when clicked.
- **Decision:**
  - `ragnarok/gui/` is a tkinter program (Python's own, so no new dependency): a borderless always-on-top window with a see-through colour key for the cat, and a small chat popup. `rag gui` starts it; `rag gui --startup on` adds a hidden launcher to the Windows Startup folder.
  - The chat engine (`ChatApp`) is reused unchanged. `GuiConsole` turns what it prints into events and `Bridge` carries them and the window's answers over two queues, so a permission question becomes a card with Allow/Deny buttons and a plan becomes a card with Run/Cancel. The engine runs in its own thread, so the cat keeps moving while the model thinks.
  - The cat's behaviour (`brain.py`) is pure logic with a seeded random source, so every reaction is unit-tested without a screen. Pixel art started as hand-typed text frames and is now drawn by a program (see Addendum 2).
  - Interactions: click opens the chat; drag lifts it by the scruff and you can drop it anywhere on the taskbar (it falls, bounces, lands); it purrs when the pointer rests on it, runs after the pointer only when the pointer comes within about 200 px (and keeps following until it is 400 px away), and pounces at a fast flick.
  - It is calm by default (about one change a minute, walking under 2% of the time) because it must not distract; "Playful" in its menu makes it busier. Unlocking uses the Windows (DPAPI) key if set up, else a small passphrase dialog.
- **Consequences:** The web UI in the architecture is deferred. Reminders and plan alerts make the cat stand up with an "!" while the chat is closed.
- **Addendum 2 (2026-10-26, later): the cat is drawn by a program, and it is smaller.** The owner asked for a more detailed cat (look and behaviour), a smaller chat box, then a slightly smaller cat. The hand-typed text sprites could not hold more detail without mistakes, so `catart.py` now draws every pose from shared parts (head, eyes, ears, tail, legs, collar, stripes) on a small canvas, and `sprites.py` only lists which parts each frame uses (38 frames, 44 x 36 cat pixels). Consequences: a new pose is a few lines, all poses share one outline and one floor line, and tests check every frame for size, known colours and clipping at the edges. The cat is drawn at 7/4 screen pixels per cat pixel (about 68 x 61 px on a 1080p screen at 100% scaling; it grows with Windows display scaling). A fractional size is done with Tk's `zoom` then `subsample`, which keeps every cat pixel at least one screen pixel wide, so the outline stays visible (a test checks this); a plain 2x was too big. The chat window went from 400 x 520 to 340 x 420 with 9 pt text. New behaviour is still calm by default: the cat stretches after waking, swishes its tail and twitches an ear while sitting, breathes slowly, follows a nearby pointer with its eyes, lies down as a "loaf" before it sleeps, and does not doze off during its first minute (so a new cat is not mistaken for a missing one).
- **Addendum (same day):** the cat is started by one short command, `meow` (installed once with `rag gui --command on` into `%LOCALAPPDATA%\Microsoft\WindowsApps`, which Windows already has on the PATH, so no PATH change). It runs a hidden launcher (no console window); a second `meow` while the cat is running opens that cat's chat instead of starting another (a named Windows event).

- **Addendum 3 (2026-10-26, later): a cuter cat.** The owner asked for "more cute". The shared `head()` in `catart.py` now draws a bigger, rounder head with narrower pointed ears, big dark glossy eyes (a glint and a green rim) set low on the face, pink cheeks, a tiny nose and a small "u" mouth; the "M" on the forehead is thinner so it does not look like a patch. Every pose uses `head()`, so the frames changed together (41 now, with two head-tilt frames and one more tail-wag frame), and the paws and overlays (grooming paw, heart, "!", thought dots) were moved to suit the larger head. New behaviour, still calm: a cat watching your pointer tilts its head (one way as soon as it settles, the other way about 3.5 s later, for 1.3 s each time, only while it is watching), and a purring or happy cat wags its tail. The head tilt is a real parameter of `head()` (one ear higher, the far eye one row lower), so it can be used for other poses.

### D-045 - "Unlock automatically on this Windows account" is offered by the cat, and can be turned off from its menu
- **Date:** 2026-10-26 - **Status:** accepted (the owner asked for it; it touches key handling, so it was approved first)
- **Context:** Every start of the cat asked for the passphrase unless the vault had been created with the Windows (DPAPI) option. The cat's dialog offered the option only when the vault was first created, so an existing vault could never get it, short of a terminal command that was not meant to be needed.
- **Decision:**
  - No new key handling. The existing opt-in functions in `ragnarok/vault/keys.py` (`enable_dpapi`, `unlock_with_dpapi`, `disable_dpapi`) are used as they are; the GUI only calls them from new places.
  - The passphrase dialog for an existing vault now has the check box (unticked by default), with a line saying what it means: anyone signed in to Windows as this user can then open the vault without the passphrase. It is acted on only after the passphrase has been checked as correct, never before and never after a wrong try.
  - The cat's right-click menu has a switch with the same name, so the choice can be undone (it removes `key.dpapi`) or made later. Turning it on there uses the key the cat already holds in memory; nothing is asked again.
  - If Windows refuses (another platform, a damaged profile), the vault still opens and the dialog simply appears again next time. If `key.dpapi` is damaged or belongs to another Windows user, the cat falls back to asking, and ticking the box replaces the file.
  - The passphrase, the recovery code and the stored key file are unchanged, and the passphrase always works.
- **Consequences:** The weaker choice is the owner's, per vault, and visible and reversible in one click. `key.dpapi` lives next to the vault in `%LOCALAPPDATA%\Ragnarok\`, not in the OneDrive folder. 11 tests in `tests/test_gui_unlock.py` (the dialog is replaced by a function that answers for the owner; Windows DPAPI is real): the box only acts after a right passphrase, a wrong try never enables it, damaged and foreign files fall back to asking, Windows refusing still opens the vault, and the stored file does not contain the key in clear.
- **Checked on the real screen** with a throwaway vault and a made-up passphrase: the dialog shows the box and the line; a real click ticks it; after Enter `key.dpapi` appears; the next start opens with no dialog.
- **Addendum 4 (2026-10-26, later): the chat window is smaller still, 300 x 360** (it was 340 x 420, and 400 x 520 before that). At that width the header could not hold the model, the GPU share and the memory count, and the close button was pushed out of the window. The close button is now packed first so a long status can never hide it, the header shows only `model · GPU`, and the memory count moved into the greeting for returning users ("Welcome back. I remember 12 things about you."). Permission and plan cards were checked at this width and fit, buttons included.
- **Addendum 5 (2026-10-26, later): quitting the cat ends the process (`exit_now`).** Found when the owner typed `meow` and nothing happened: the cat had no window, but its process was alive and still held the one-copy lock, so `meow` only signalled a cat nobody was running. Cause: the engine's `asyncio.to_thread(read_line / approve_line)` workers wait on a queue, and Python joins those threads when the process exits, so a worker still blocked at quit keeps the process alive. `main()` now ends with `exit_now(0)` (flush, then `os._exit`) after the window loop; the vault is closed by the engine first and is crash-safe (WAL), so nothing is lost.

### D-046 - Screen skill: why it clicked the wrong thing, and four small fixes (addendum to D-035)
- **Date:** 2026-10-26 - **Status:** accepted (the owner asked for the cat to read the screen and do things on it)
- **Context:** `bench/screen_eval.py` (real model, real capture, real mouse and keyboard on a test window) gave **3 of 10** at the start of the day, as D-035 had said (5 of 15). Looking at the failures instead of guessing:
  - Pointing is not the main problem. A one-shot "where is this file" test, 12 real double-clicks on shuffled lists, hit the right file 10 times, and 11 with a zoom-and-repoint second pass. A "read what is under the red cross" check on every click (tried, 23 trials) moved 19 of 23 to 20 of 23 and once rejected a correct point, so it was **not adopted**.
  - The model's answer for the same picture and prompt changes from run to run (the same row came back as y=32, 42 and 243 in separate runs). So nothing may assume the first pointing is right.
  - The real failures were in the loop: the search box was typed into without being clicked first (`type` as the first action), a repeated click on a spot that was really on a different row was repeated again and again with nothing telling the model where it had landed, a scroll aimed outside the list changed nothing and got only a generic "try something different", and slightly shifted re-clicks of the same button hid "it already worked" from the repeat check.
- **Decision:** (all in `ragnarok/skills/screen/task.py` and `capture.py`; no new dependency, same model)
  1. A bare `type` before anything has been clicked or pressed is held back (after the permission and safety checks, so those refusals still fire first) and the model is told to use `fill`. It also stops text going to whatever window is in front. The prompt says the same.
  2. When the model clicks within 30 screen pixels of its last click, the zoomed picture around that point (red cross on it) is shown to the model, which reads what is under it. If that is not the named target the click is not made and the model is told what is really there. If the reading is empty or unsure, the click goes ahead.
  3. A scroll that changes nothing gets its own note: its x and y were not on the list. The prompt says to put them on the middle of the list and never to click an item that is not visible.
  4. A click within 30 px of the last one, on the same target, counts as a repeat, so the existing "does the screen already show it worked?" check runs before the extra click.
- **Measured** (`bench.screen_eval`, 3 trials each, same test window): before **3 of 10**; after fix 1 and 2 **10 of 15**; after 3 **11 of 15** (scroll 0 to 3 of 3); after 4 **13 of 15 (87%)**: buttons 2/3, search 3/3, form 3/3, list 2/3, scroll 3/3. One run in 15 still claimed success when the check failed (a wrong button was clicked first). Small samples and a noisy model: expect plus or minus a couple of runs.
- **Consequences:** Reading the screen was already dependable; acting on a simple window is now usually right, but this is a test window, not every app. It stays experimental, still asks before each task, and still stops on Esc or mouse movement. The cat's right-click menu has "What is on my screen?" which asks the model without special wording.
- **Revisit if:** real apps (browser, Explorer) fail in ways the test window does not show; then add scenarios to the eval from those, not guesses.

### D-047 - Skills are folders with `SKILL.md` + `skill.json`, tested and approved before they register, and run in a sandbox (FR2.1, FR2.3)
- **Date:** 2026-10-26 - **Status:** accepted
- **Context:** FR2.1 asked for user-added skills and FR2.3 for isolation, and neither existed. The design (D-009, D-010) wanted a YAML front matter, but no YAML library is installed and adding a dependency needs the owner's approval.
- **Decision:**
  - A skill is a folder: `SKILL.md` (a flat `name:` / `description:` header between `---` lines, then instructions), `skill.json` (version, risk, tools with a JSON-schema-like input, the command to run, network need, time limit, and tests), and its scripts. No new dependency.
  - `/skill add PATH` validates the manifest, runs the skill's own tests inside the sandbox, shows the owner what it declares and asks for approval, then stores the manifest and a content hash in the vault (migration 0007). If a skill's files change on disk, the hash no longer matches and the skill is switched off until the owner approves it again. A skill may not take the name of a built-in tool.
  - `ragnarok/sandbox/runner.py` runs a skill's command in a child process under a Windows Job Object (memory cap, process-count cap, kill-on-close), with a scrubbed environment (no keys, tokens, vault settings; proxies pointing at a closed port while offline) and narrow I/O (arguments on stdin, one JSON result on stdout, an output cap). `guard.py` runs first inside that process and installs an audit hook that refuses vault and key paths, writes outside the run's folder, network use while offline, `ctypes`, and starting other programs.
- **Consequences:** The audit hook is defence in depth, not a boundary (native code can get round it); the Job Object and the environment are what the OS enforces. `fixtures/evil-skill` tries each bad thing and `tests/test_sandbox.py` proves each is stopped (12 tests). Registration, tampering and approval: `tests/test_skills_registry.py`; the chat commands: `tests/test_chat_skills.py`.

### D-048 - Crash restore reads the vault back; a cut-off tool call that is not idempotent is asked about, not re-run (NFR3.2)
- **Date:** 2026-10-26 - **Status:** accepted
- **Context:** Plans had `/resume`, but nothing tidied what a killed program left half done, the conversation was lost, and there was no way to show the 5-second target was met.
- **Decision:** `recovery/restore.py::restore_state` runs at every chat start and for the daemon. It (1) marks steps still `running` or `waiting_approval` as `interrupted` and their plan as `interrupted`; (2) finds tool calls logged as started with no result, closes each with an "interrupted" result so it is reported once, and flags those whose tool is not idempotent as risky; (3) puts the last conversation's recent messages (up to 12, up to 12 hours old) back as working memory, found through a `chat_session` meta row. `/resume` asks before carrying on if a risky call was cut off. No model call is involved.
- **Consequences:** Measured by `bench/recovery_bench.py`: a child process working through a six-step plan is killed with TerminateProcess at a random moment, then the vault is reopened and restored. **50 of 50 runs passed**; restart (open the vault with its integrity check, plus restore) p50 78 ms, p95 89 ms, max 223 ms against the 5,000 ms target (a vault key is used directly, so typing a passphrase is not in the figure). `tests/test_recovery_restore.py` repeats three real kills in the normal test run.
- **Not covered:** a reboot with a passphrase-only vault waits for the passphrase; the unlock time depends on the person typing.

### D-049 - Always-on is a separate `rag daemon` process; suggestions come from rules over the vault, not from the model (FR4.1, FR4.2)
- **Date:** 2026-10-26 - **Status:** accepted
- **Context:** D-042 ran triggers inside the chat, so reminders only fired while the chat or the cat was open (the honest limit the worklog recorded). ARCHITECTURE 5.4 sketched model-written suggestions every 15 minutes.
- **Decision:**
  - `rag daemon` runs the same `TriggerEngine` with no window (`triggers/daemon.py`), after `restore_state`. One daemon at a time (a lock file the OS frees if the process dies); a heartbeat row in the vault lets other parts see it is alive. Running the chat as well is safe because every fire has a unique key in the vault, so an event is handled once by whichever process gets there first. Goals run in the read-only `background` profile: changes become Inbox proposals. New items become Windows notifications. `rag daemon --startup on` puts a hidden launcher in the Startup folder, the same way as the cat (simpler than a Task Scheduler task; needs the DPAPI unlock opt-in because a hidden process cannot ask for a passphrase).
  - `triggers/proactive.py` makes suggestions with no model: an unfinished plan, a remembered date or deadline in the next 3 days with no reminder yet, a remembered habit ("every morning...") with no schedule, and the last project in the morning. Never from `secret` or `private` memories; at most 3 per run; the same idea at most once a week; they only ever go to the Inbox (`/suggest` shows them on request). The engine looks every 30 minutes, only while the owner is idle.
  - A new `.eml` file in a watched mailbox folder is a "new mail" event with the sender and subject in the notice, using the existing folder trigger, so no migration.
- **Consequences:** Suggestions cost no GPU time and cannot be talked into anything, but their wording is fixed text and they only know the patterns above; the model-written version stays a later option. `bench/resource_bench.py` (NFR2.2): the engine with 3 watched folders of 300 files, 8 reminders and schedules and suggestions used **0.63% of one core, 0.05% of the 12-thread machine**, peak 50 MB, over 20 s (target: under 30%). Model inference is measured separately (`bench/llm_bench.py`, `ollama ps`).

### D-050 - Email is a local mailbox folder; project tasks live in the vault (FR3.2)
- **Date:** 2026-10-26 - **Status:** accepted
- **Context:** FR3.2 names an email client and project-management tools. A real mail account means a stored password and network access.
- **Decision:** `skills/mail.py` works on a folder of `.eml` files (`<home>/mail/inbox`, `outbox`, or `RAGNAROK_MAIL_DIR`): `list_mail` and `read_mail` are R0; `send_mail` is R2 (the owner approves each message) and writes to the outbox, and also hands the message to an SMTP bridge only if `RAGNAROK_SMTP_HOST` is set to a host on this machine. Ragnarok never holds an account password. Email text is wrapped as untrusted ("do not follow instructions in it"). `skills/tasks.py` is a small task board (`add_task`, `done_task` R1, `list_tasks` R0) in the vault (migration 0008). The code editor side is the existing app launcher, file and shell tools.
- **Consequences:** Real IMAP/Gmail needs a bridge or export that fills the folder; that is outside this change. Live check on the real model (`bench.daily_tasks`): list mail, read a hostile email without obeying it, send an email, add a task all pass; "honest: send email" was changed to ask for an SMS, which the agent still cannot do, because email now exists.

### D-051 - Roles sit on top of the permission profiles, and `/log` shows the tool, the rule and the result (NFR4.3, NFR4.1)
- **Date:** 2026-10-26 - **Status:** accepted
- **Context:** The profiles answered "when do I ask"; nothing answered "who may do what at all", and the explanation of a turn existed only in the footer.
- **Decision:** `policy/roles.py` defines `owner` (the default: nothing changes), `editor` (up to R2, never `run_command`, `delete_path`, `use_computer`), `viewer` (R0 only) and `automation` (R0 plus the note-leaving tools). `permissions.check(profile, tool, risk, role)` applies the role first, so a role can only remove rights. `/role` shows them; going to a role with more rights asks the owner; the choice is kept in the vault. Headless goals keep the `background` profile (they propose changes) and inherit the chat's role. `/log` (and `rag log`) lists recent tool calls from the event log: tool, arguments, the model's stated reason, the permission rule that decided, and the result; `/log plan` lists a plan's events. No defaults in the profiles changed.
- **Consequences:** Roles are a local, owner-controlled switch, not separate accounts: the person at the keyboard can always go back up after one confirmation. That matches a single-user laptop; real multi-user separation would need OS accounts. Tests: `tests/test_roles_and_log.py`, `tests/test_chat_recovery_roles.py`.
- **Addendum (2026-10-26, later): Gmail in the browser, by itself.** The owner tried "open gmail and write a mail" and the local-folder tools could not do it. Two tools were added to `skills/mail.py`. `compose_email` (R1) opens Gmail's compose page with the recipient, subject and text already in the address, and sends nothing (a draft to review). `send_gmail` (R2) does the same and then sends it with no help: `GmailSender` opens the page, waits until the front window's title is a Gmail compose window and has stayed one for 2 seconds (Gmail reloads while it starts), presses Ctrl+Enter, and then follows that same window until its title is no longer "compose". It presses nothing if Gmail shows a sign-in page or the window changes, stops if Esc is held, and reports a draft that is still open. It uses its own narrow Ctrl+Enter, so the screen skill's key allow-list is unchanged, and it does not read or store any Google password: it uses the browser's own signed-in session. **Sending mail asks in every mode, including `full`** (`SENDS_EMAIL` in `policy/permissions.py`), per NFR4.3; no existing default changed.

### D-052 - Inference is measured against the 30% target; Gmail sends by clicking the Send button; code opens in VS Code
- **Date:** 2026-10-26 - **Status:** accepted (the owner asked to make the two partly-met checklist items done)
- **Context:** Two checklist items were "partly": NFR2.2 had only the background engine measured, and FR3.2 had never sent a real email or touched an IDE.
- **Decision:**
  - `bench/inference_bench.py` samples whole-machine CPU, Ollama's own CPU (its processes by id) and the GPU (`nvidia-smi`) twice a second while idle with the model resident, and during six real chat turns, and reports a duty cycle at one request a minute.
  - `send_gmail` was run live against the owner's own address. Synthesized Ctrl+Enter, and Tab then Enter, did not send in Gmail's full-page compose view (Tab moved focus only). What works: click into the message to give the page focus, find the blue Send button on a screenshot (the largest blob of the button's two blues in the lower half) and click its "Send" half, never the arrow beside it (that opens the schedule-send menu). "Sent" is confirmed from the screen: while Gmail sends, the Send half greys out and only the dark arrow half is left, and the tab title does not change in this view.
  - `open_in_editor` (R1) opens a file or project folder in Visual Studio Code with a list argument (never a shell string), from the same path rules as `open_path`. Opening a script in an editor does not run it, so program files are allowed.
- **Measured:** Ollama 0.0% of the machine's CPU idle and 8.3% (peak 10.7%) generating; GPU 3.8% idle and 77.7% (peak 86%) during an answer of about 6 s; about 11% GPU averaged at one request a minute; 5.3 of 6.1 GB of GPU memory held. Whole-machine CPU was 34% at idle because of other programs, so Ollama's own share is what is reported.
- **Consequences:** The requirement's "30% of baseline resources" is read as sustained use. GPU use during an answer is above 30% by design, and we say so. Clicking a button found by colour depends on Gmail's layout and on the browser window staying in front: it is checked before and after (a compose title, the right window, the button found or greyed), and nothing is clicked if the checks fail. In the live runs one attempt sent the mail but could not confirm it, so the timeout was lengthened. VS Code would not start from the shell used for testing, even `code --version` failed with an ICU error, so only the tool, its path checks and the model's choice of it are tested, not a live launch. About six test emails were sent to the owner's own address during the live runs.

### D-053 - Voice: Windows' offline speech engine, with Ragnarok recording the audio itself
- **Date:** 2026-10-26 - **Status:** accepted (the owner asked for text to speech and speech to text)
- **Context:** The owner wants to talk to it. Rules: local only, no new dependency without approval, no always-open microphone, nothing written to disk.
- **Decision:**
  - **Speaking:** the Windows `System.Speech` voices (Zira by default) through PowerShell, with the text passed in an environment variable, never in the command line. Answers are cleaned first (no code, links, paths or markdown) and cut to about 5 sentences. `/voice on|off`.
  - **Listening:** `/listen` (one sentence) and `/voice talk` (hands-free until "stop listening" or two silences). The microphone only opens for those commands, never for a tool or the model.
  - **First version** let the recogniser open the default microphone. Live it returned nothing: the default input was a Bluetooth headset that was not in use, the laptop microphone peaks at about 1% of full scale, and an unexplained 30 s wait. So Ragnarok now **records the sentence itself** (`voice_capture.py`, Windows waveIn, no new package): choose the microphone with `RAGNAROK_MIC`, find the start and end of speech by level, amplify quiet speech up to 40x, then pass the audio to the recogniser through a pipe in memory (nothing is written to disk). `/voice test` reports the microphone, its level and what was understood, and a silent microphone is told apart from a quiet room.
- **Measured live:** speech output works. Listening on the laptop microphone detected the speech (1.9 s, loudest 1.2% of full scale, boosted 40x) but the offline recogniser misheard it ("You did I use of or she loved"). The signal is simply too quiet: Windows shows the input volume at 36%.
- **Consequences:** Recognition quality is the limit, not the code: the offline recogniser is old and needs a clear, loud signal. Raising the input volume to near 100% or speaking closer should help; if not, a Whisper-class model would need a new dependency and a download, which needs the owner's approval. **A wake phrase ("hey Ragnarok") is not built**: it needs the microphone open all the time, so it should be opt-in, and it depends on the same signal quality. A hotkey or a microphone button on the cat is the safer first step. 33 tests in `tests/test_voice.py` (the engine and the microphone are faked; speech detection, amplification and device choice are tested on synthetic audio).
- **Addendum (2026-10-26, later): Whisper for listening (the owner approved the new dependency).** Measured on the real microphone and on synthetic speech: the Windows recogniser (`MS-1033-80-DESK`) scored 39-52% on clean synthetic speech ("Up and steam in tell me what time it S") and 2% on a clear sentence from a headset ("Wheat or"). The recording was fine (headset: 2.7 s, 55% of full scale, ended by itself), so the recogniser is the limit, not the pipeline. Added `faster-whisper` (CTranslate2, runs on the CPU in int8; `base.en`, about 145 MB, downloaded once into `%LOCALAPPDATA%\Ragnarok\models`, telemetry off, later loads are `local_files_only`). The CPU is used on purpose: the 6 GB GPU holds the chat model, and a second GPU model would make Ollama reload. Same live sentence now: "It is open screen and tell me what time it is." for "open steam and tell me what time it is". The Windows recogniser stays as the fallback (`RAGNAROK_STT=windows`, or when the model cannot be loaded). New dependencies: `faster-whisper` and what it pulls in (onnxruntime, tokenizers, huggingface-hub...). The audio itself never leaves the process; only the model weights are downloaded.
- **Also found:** the default microphone changes with Bluetooth: with the earbuds connected, Windows' default input is the earbuds, and the laptop microphone is device 1. `RAGNAROK_MIC=0` or a name picks one. The laptop's own array microphone is very quiet (about 1% peaks, input volume 36%); the headset gives about 50%.
- **Addendum (2026-10-26, later): double-click the cat to talk.** One click still opens or closes the chat; two clicks within 300 ms start a hands-free voice conversation (`/voice talk`: listen, answer aloud, listen again), and a second double-click ends it (it cancels the recording at once). The single click now waits 300 ms to see whether a second follows (`gui/clicks.py`), so a double click does not also open and close the chat. While listening, the chat header says "listening by voice" and the cat's ears go up; what the microphone understood appears as a message bubble. The right-click menu has "Talk to me (double-click)". The microphone still opens only for this gesture, `/listen` or `/voice talk`. **Not verified live**: the running cat has to be restarted, and a double-click on the real window was not tried (the click harness would take over the owner's mouse); the click counter, the window and the engine are covered by 8 tests.
- **Addendum (2026-10-26, later): "it just keeps on listening" fixed, and the wake phrase.**
  - **The bug:** the cat's double-click started `/voice talk`, a standing conversation that listens again after every answer and only ended on "stop listening" or two silences; background noise that Whisper turned into a word ("Thank you.") counted as speech, so it never ended. **Fix:** a double-click is now **one request**: `/listen aloud` opens the microphone once, answers aloud, and closes it (the header shows "listening" only for that moment; a second double-click cancels it). Whisper's habitual inventions on noise ("you", "thank you", "thanks for watching", "bye", "okay", "so") are dropped when they are the whole result. `/voice talk` is still available for a deliberate continuous conversation.
  - **Wake phrase "hey Ragnarok"** (`ragnarok/wake.py`), **opt-in**: `/voice wake on|off` or the cat's right-click menu, remembered in the vault (`voice_wake`) and restarted with the app. It reads the microphone in bursts of up to 5 s of waiting; only speech of 4 s or less is transcribed (a conversation in the room is not), with the local Whisper model given the phrase as a hint; the text is dropped unless it contains the phrase (a fuzzy match: "hey rag narok", "hey ragnarock", "ok ragnarok" pass; "hey Roger", "hey google", "Ragnar is a viking" do not). Nothing is stored or sent. After the phrase, the words that follow are the request ("hey Ragnarok open steam"), or it listens for one sentence; the answer is read aloud. It pauses while a request is running, while Ragnarok speaks, and during a hands-free conversation, and a lock keeps one thing on the microphone at a time. Every request still goes through the normal permission checks.
  - **Measured:** the matcher accepts 9 of 9 mishearings and rejects 11 of 11 ordinary sentences in the tests; on clean synthetic speech (two voices, three phrases, with and without the hint) the model plus matcher woke 12 of 12 times, each clip taking about 0.7 s to transcribe. **Live on the owner's headset it did not wake**: the burst came back as "I'm Waking Inside Video." The cause was not found (the owner may not have spoken the phrase, or the live audio is worse than the synthetic audio); `small.en` is the next step if it keeps happening.
  - **Cost of leaving it on:** the microphone is open whenever the app runs, and the CPU transcribes each short burst of speech, about 0.7 s each. It is off until the owner turns it on.
- **Addendum (2026-10-26, later): why the wake phrase "did not work", and Indian English.**
  - **Found by running the real listener, three causes:** (1) the laptop microphone was at 36% input volume, so the owner's voice peaked at 1-5% of full scale and Whisper mis-heard it; raising it to 100% was too much (the sound clipped at about 99% and sentences never ended), so it was set to **70%** (a Windows setting, changed three times during testing: 36% -> 100% -> 70%; the owner can change it in Settings > System > Sound > Input). (2) The recorded clip included the 1.2 s of silence that ended the sentence, so "hey Ragnarok" was 3-4 s long and the wake listener threw it away as "a conversation"; the trailing silence is now trimmed to 0.3 s, and the limit is about 5 s of speech. (3) The end of a sentence was judged only against the room's noise; it is now also judged against the speaker's own peak (below 6% of it), so loud speech ends promptly. A level above 97% now says "too loud".
  - **Live result after the fixes:** the real listener woke on "Hey Ragnarok. What time it is?" and passed "What time it is" on as the request; other speech in the room was transcribed and ignored (no false wake).
  - **Indian English.** Whisper has no accent switch, so: the model that hears your request is now **`small.en`** (about 460 MB, downloaded once, better with accents) while the wake phrase stays on the fast `base.en` (checked on every burst of speech); both models get a prompt that says "Indian English speaker" and lists words like Ragnarok, Steam, Gmail, WhatsApp, PowerPoint (`RAGNAROK_ACCENT=us` turns the prompt off, `RAGNAROK_WHISPER_MODEL` forces one model). A result that is only the prompt, or one of Whisper's stock inventions on noise, is dropped. Speaking: if Windows has an **English (India)** voice installed (Settings > Time & Language > Speech > Add voices, for example Heera or Ravi), it is chosen automatically; here only Zira and David are installed, so Zira is used until one is added.
  - **Cost:** both models stay in memory (about 0.5 GB of RAM) once voice is used; a command takes about 1.8 s to transcribe, a wake burst about 0.7 s.
  - **Not verified:** accuracy on longer requests (the live command tests were cut short by the test's own timing), and whether `small.en` is clearly better than `base.en` for the owner's accent.
- **Addendum (2026-10-26, later still): the cat stayed on "thinking" after a wake request.** The owner's report: the wake phrase was heard, but the chat then showed "thinking" for ever and the request was not taken. **Cause (found by reading the screen and the code):** with the wake phrase on, the engine keeps one typing-wait running across loops so that a spoken request does not start a second one. The cat's window only learns that the engine is ready again when a new typing-wait starts (the bridge emits "idle" from `read_line`), so after a voice request no "idle" ever arrived and the window stayed busy. **Fix:** the engine now tells the console it is ready on every loop (`Console.ready_for_input`, the cat's console emits "idle"); a regression test fails without it. **Also:** a chime (two rising tones) and the message "Yes? I'm listening - say your request now." as soon as the phrase is heard (before, it listened in silence for up to 7 s and looked dead); a low tone and a message when no request follows; the name is also accepted as "rangrok", and "hi ragnarok" / "rangrok open steam" work, while "I rank rock bands" and "hey Roger" do not.
