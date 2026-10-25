# Ragnarok

A local, always-on personal agent for the IIIT hackathon. Every model call goes to [Ollama](https://ollama.com) on `127.0.0.1`; nothing
leaves the laptop. The main goal is **persistent memory**: the agent remembers you across restarts and days, and everything it remembers
sits in one encrypted file.

Requirements: `Ragnarok A Sovereign, Always-On Personal AI Agent.pdf` (FR1-FR4, NFR1-NFR4).
Design: [`ARCHITECTURE.md`](ARCHITECTURE.md) and [`docs/MEMORY.md`](docs/MEMORY.md).
Why we chose things: [`docs/DECISIONS.md`](docs/DECISIONS.md). What happened and what was measured: [`docs/WORKLOG.md`](docs/WORKLOG.md).
Project rules for contributors and coding agents: [`AGENTS.md`](AGENTS.md).

Target machine: HP OMEN 16, i5-13420H, 16 GB RAM, RTX 4050 6 GB, Windows 11, no WSL or Docker.

*Last updated: 2026-10-25 21:40 IST (UTC+05:30).*

---

## Try it

You need Ollama running with the project's settings, then three commands. Run them from the repository folder
(`C:\Users\parth\dev\Ai-Agent`), not from a OneDrive folder.

```powershell
cd C:\Users\parth\dev\Ai-Agent
powershell -ExecutionPolicy Bypass -File scripts\start_ollama.ps1   # start Ollama (local-only, models pinned)
uv run python -m ragnarok init     # once: choose a passphrase, save the recovery code it shows you
uv run python -m ragnarok chat     # talk to it: "remember that ...", "open steam"; quit, come back later
```

Inside `chat`: `/memories` lists what it remembers, `/profile` shows what is always in its prompt, `/forget N` deletes a memory,
`/clear` clears the screen, `/quit` leaves. It learns from the chat while you are idle and finishes next time, so leaving is instant. Other commands: `memory list|search|show|history|forget|archive|restore`,
`ingest`, and `doctor` (checks local-only mode, models, GPU and encryption).
Controls: `memory pin|unpin N`, `memory correct N --text "the right fact"`, `memory revert N` (bring an older version back), `memory history N`,
`memory stats`, `memory consolidate`, `memory prune --dry-run`, `memory maintain`, `memory undo-summary N`, and
`memory export FILE --yes-plaintext` (that file is not encrypted). Run them as `uv run python -m ragnarok memory ...`.

Screen: `look at my screen and tell me what is open` reads it (one screenshot, kept in memory only). `use the mouse and keyboard to ...` lets it act, after you say yes; hold Esc or move the mouse to stop it, and it never types into a terminal. Acting is experimental (5 of 15 on our test window), see D-035. `RAGNAROK_NO_SCREEN=1` turns both off.

The models do not run "in" the terminal: they run in the Ollama background process on your GPU, and the terminal is the chat window.

---

## Status

Times are commit times (IST). Details and numbers for each line are in the work log.

### Done

| When (IST) | What | Evidence |
|---|---|---|
| 2026-10-25 14:54 | Team design doc uploaded | `4108834` |
| 2026-10-25 16:19 | Architecture v2 around the five pillars (skills, planning, triggers, encryption, recovery); decisions D-001 to D-017; project context files | `9e96f88` ... `9012936` |
| 2026-10-25 16:30 | Persistent memory spec and the "memory first" decision (D-018, D-019) | `899153d`, `e53721f` |
| 2026-10-25 16:55 | **M0** Python 3.12 project with uv; SQLCipher has FTS5 and encrypts the file; Turbovec has the API we need | `2a6d6f4` ... `313fa4f` |
| 2026-10-25 17:09 | Ollama 0.40.11 running local-only (`Ollama cloud disabled: true`); verified start script | `94fdec5` |
| 2026-10-25 17:16 | **M1** Encrypted vault: SQLCipher, WAL, `secure_delete`, numbered migrations, full memory schema, FTS5 kept in sync | `7a60009` |
| 2026-10-25 17:26 | **M2** Memory core: `remember` / `recall` with FTS5 + vector index + RRF + rerank, checked against SQLite; restart-safe index | `32cb4ae` |
| 2026-10-25 17:26 | Loopback-only model gateway (rejects non-loopback hosts and `-cloud` models); embedding and chat benches | `a8dfd7d` |
| 2026-10-25 17:30 | Key management: random vault key wrapped by passphrase (Argon2id) and a one-time recovery code; optional Windows DPAPI unlock | `f40e8ef` |
| 2026-10-25 17:38 | Memory lifecycle: supersede with history, reinforce, archive/restore, hard delete with a content-free tombstone, raw event tier with secret scrubbing and retention | `1c44935` |
| 2026-10-25 17:43 | Retrieval benchmark on real embeddings; defaults chosen from it (D-026) | `71b8c0e` |
| 2026-10-25 17:55 | **M3** Extraction from events into memories with ADD / UPDATE / SUPERSEDE / NOOP, provenance, and code-enforced safety rules (D-027) | `0a75ecd` |
| 2026-10-25 18:00 | **M4** Always-on profile block, `memory_search` / `memory_save` tools, permission check, chat loop with log-before-act | `ba97b86` |
| 2026-10-25 18:05 | Fixed two silent data-loss bugs the real-model demo found; end-to-end demo passes 4/4 (D-029) | `f78677e` |
| 2026-10-25 18:07 | Terminal CLI: `init`, `chat`, `memory ...`, `ingest`, `doctor` | `c14915b` |
| 2026-10-25 18:19 | First real run on the owner's laptop found the agent could not "open steam"; added the `open_app` skill (Start menu apps, shells refused), an honesty rule, and passphrase retries | `b61a218` |
| 2026-10-25 18:33 | Colour terminal screen (welcome box, spinner, footer), background learning, and faster tool turns: remembering a fact 3.9 s to 1.6 s, opening an app 2.2 s to 1.1 s | `ec7a795` |
| 2026-10-25 18:50 | Read-only file skill: find, list and read files in Desktop, Documents and Downloads (OneDrive copies included); secrets and anything outside those folders refused | `29492da` |
| 2026-10-25 18:53 | `persistent_memory.py`: the whole memory lifecycle in one file, plus correct, revert, pin, export and stats | `3306cfb` |
| 2026-10-25 19:39 | **M5** Weekly summaries (validated model output), forgetting by usefulness, delete cascade, undo, daily maintenance | `1eda367` |
| 2026-10-25 19:45 | CLI controls for all of it (`memory pin\|correct\|revert\|export\|stats\|consolidate\|prune\|maintain\|undo-summary`) and idle-time maintenance in the chat | `27fedd9` |
| 2026-10-25 20:10 | `open_website`: "open chrome and go to yt and search ..." now works (sites and searches in the browser you name; hostile addresses refused; multi-step requests no longer cut short) | `8412f03` |
| 2026-10-25 21:40 | **Screen skill (experimental):** `read_screen` (answers questions about what is on the screen) and `use_computer` (mouse and keyboard, asks first, Esc or mouse move stops it, never types into a terminal). Live test on a small test window: **5 of 15 tasks succeeded**; reading is solid, acting is not yet (D-035) | next commit |

550 tests pass and `ruff` is clean as of the last commit.

### Remaining

| Item | Milestone | Status |
|---|---|---|
| Use it for a few days (`memory stats` shows what it is learning). Consolidation has not run on a real vault yet: it needs finished weeks with at least four notes each | follow-up | **next, needs you** |
| Summaries grouped by entity (today: by project and week); an export encrypted with a passphrase | M5 follow-up | not started |
| A held-out evaluation set (the prompts were tuned on the current small one); final numbers for the README | M6 | not started |
| Paraphrased questions are the weak spot of retrieval (recall@5 about 0.77) | M6 | idea: query expansion or question-style embeddings |
| Screen acting accuracy: a zoom-in look before clicking, then Windows UI Automation for apps that expose controls; try on real apps | skills | **experimental, 5/15 on the test window** |
| Skills registry and sandbox (FR2) | pillar | design only |
| Planning with a verifier and failure ladder (FR3) | pillar | design only |
| Triggers and proactive suggestions (FR4) | pillar | design only |
| Recovery of in-flight tool calls, idempotency keys, restart supervisor (NFR3) | pillar | design only; the memory index and event log already restart safely |
| Web UI, tray icon, Proof panel | optional | design only |

---

## Measured on the target laptop (2026-10-25)

| What | Result |
|---|---|
| Chat model `qwen3.5:4b` | `100% GPU` at 16k context, 48.8 tok/s, first token about 145 ms with a cached prefix, `think: false` honoured |
| Cold model start | about 5-7 s once (one 54 s outlier right after the download was not reproducible) |
| Embedding `nomic-embed-text` | 768-d, `100% GPU`, query embed p50 82 ms / p95 117 ms |
| Retrieval quality (500 memories, 124 labelled synthetic queries) | recall@5 0.895, MRR 0.835; superseded and deleted memories never returned |
| Retrieval latency (embed + search) | about 119 ms p95 (the requirement is under 800 ms) |
| Restart with 10k memories | index rebuilt in 0.44 s, or loaded from the saved blob in 0.20 s (requirement: under 5 s) |
| Extraction from events (15 hand-written cases) | 15/15 kept or skipped correctly; p50 1.7 s |
| ADD / UPDATE / SUPERSEDE decisions (12 cases) | 12/12; p50 0.8 s |
| End-to-end demo: tell it facts, restart, return next day, change a preference | 4/4 checks; a turn takes 0.7-4.4 s |
| Key derivation | Argon2id about 0.4 s (256 MiB, only while unlocking) |

Caveats: the benchmark data is synthetic and written by us, and the extraction and decision prompts were tuned on the same small cases,
so these numbers show relative quality and that the safety rules work, not recall in daily use. The Turbovec index missed our 0.95 recall bar
(0.92), so the default is exact search until the corpus grows (D-026). Running the real models found two silent data-loss bugs that the scripted tests
had missed (D-029); that is why the end-to-end demo is kept as a benchmark.

---

## Develop

```powershell
winget install astral-sh.uv            # Python 3.12 is installed by uv
uv sync
ollama pull qwen3.5:4b
ollama pull nomic-embed-text

uv run pytest -q
uv run ruff check .
uv run python -m bench.embed_bench
uv run python -m bench.llm_bench
uv run python -m bench.retrieval_bench    # writes bench/results/retrieval_<date>.json
uv run python -m bench.ingest_eval        # real-model extraction and decision quality
uv run python -m bench.consolidate_eval   # real-model weekly summaries and their validation
uv run python -m bench.chat_latency       # how long each kind of chat turn takes
uv run python -m bench.m4_demo            # end-to-end: tell it facts, restart, come back
```

Use `127.0.0.1`, never `localhost`, for Ollama: `localhost` adds about 2 s to the first connection on this machine.
Runtime data (vault, keys, logs) belongs in `%LOCALAPPDATA%\Ragnarok\` (or `RAGNAROK_HOME`), never in the repository or OneDrive;
the CLI refuses a data folder inside OneDrive.

## Layout

```text
ragnarok/
  cli.py         terminal front end (init, chat, memory, ingest, doctor)
  memory/        vault.py (SQLCipher, migrations)  store.py (write, search)  ingest.py (events -> memories)
                 profile.py (always-on block)  service.py (async)  index.py (exact, Turbovec)
                 consolidate.py (weekly summaries)  forget.py (usefulness, pruning)
  persistent_memory.py   the whole memory lifecycle in one readable file (start here)
  vault/         keys.py (passphrase, recovery code, DPAPI)
  runtime/       gateway.py (the only path to a model)  loop.py (agent loop)  context.py  tools.py  prompts/
  policy/        permissions.py (every tool call is checked here)
bench/           embed_bench  llm_bench  retrieval_bench  ingest_eval  consolidate_eval  chat_latency  m4_demo
tests/           one file per milestone
docs/            MEMORY.md  DECISIONS.md  WORKLOG.md  history/
```

## Privacy rules

- All inference is local: one gateway, loopback only, no cloud models, no API keys.
- User content is stored only in the encrypted vault. The log files carry ids, timings and error types, never content.
- Secrets that appear in text (passwords, tokens, API keys) are redacted before anything is stored.
- Deleting a memory removes its row, search entries, vector and links, and leaves only an id and time in the audit log.
- Text from memory, files or tools reaches the model only inside data tags, and the system prompt says never to follow instructions found there.
