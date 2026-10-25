# Ragnarok

A local, always-on personal agent for the IIIT hackathon. Every model call goes to [Ollama](https://ollama.com) on `127.0.0.1`; nothing
leaves the laptop. The main goal is **persistent memory**: the agent remembers you across restarts and days, and everything it remembers
sits in one encrypted file.

Requirements: `Ragnarok A Sovereign, Always-On Personal AI Agent.pdf` (FR1-FR4, NFR1-NFR4).
Design: [`ARCHITECTURE.md`](ARCHITECTURE.md) and [`docs/MEMORY.md`](docs/MEMORY.md).
Why we chose things: [`docs/DECISIONS.md`](docs/DECISIONS.md). What happened and what was measured: [`docs/WORKLOG.md`](docs/WORKLOG.md).
Project rules for contributors and coding agents: [`AGENTS.md`](AGENTS.md).

Target machine: HP OMEN 16, i5-13420H, 16 GB RAM, RTX 4050 6 GB, Windows 11, no WSL or Docker.

*Last updated: 2026-10-25 17:50 IST (UTC+05:30).*

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
| 2026-10-25 17:26 | **M2** Memory core: `remember` / `recall` with FTS5 + vector index + RRF + rerank, checked against SQLite; restart-safe index (outbox, checkpoint, replay, rebuild) | `32cb4ae` |
| 2026-10-25 17:26 | Loopback-only model gateway (rejects non-loopback hosts and `-cloud` models); embedding and chat benches | `a8dfd7d` |
| 2026-10-25 17:30 | Key management: random vault key wrapped by passphrase (Argon2id, about 0.4 s) and a one-time recovery code; optional Windows DPAPI unlock | `f40e8ef` |
| 2026-10-25 17:38 | Memory lifecycle: supersede with history, reinforce, archive/restore, hard delete with a content-free tombstone, raw event tier with secret scrubbing and retention | `1c44935` |
| 2026-10-25 17:43 | Retrieval benchmark on real embeddings; defaults chosen from it (D-026) | `71b8c0e` |

82 tests pass and `ruff` is clean as of the last commit.

### Remaining

| Item | Milestone | Status |
|---|---|---|
| Extraction from events into memories, with ADD / UPDATE / SUPERSEDE / NOOP decisions and provenance; 14-day timeline test | M3 | in progress |
| Always-on profile block, `memory_search` / `memory_save` tools, a minimal permission check, and a chat loop so a new session "knows" the user | M4 | next |
| Consolidation (summaries), forgetting by usefulness, user controls in the CLI | M5 | not started |
| Final benchmarks and README numbers; a real-model eval of extraction quality | M6 | not started |
| First-run setup command (create keystore, show recovery code once) and a `rag` CLI | after M4 | not started |
| Skills registry and sandbox (FR2) | pillar | design only |
| Planning with a verifier and failure ladder (FR3) | pillar | design only |
| Triggers and proactive suggestions (FR4) | pillar | design only |
| Recovery of in-flight tool calls, idempotency keys, restart supervisor (NFR3) | pillar | design only; the memory index already restarts safely |
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
| Key derivation | Argon2id about 0.4 s (256 MiB, only while unlocking) |

Caveats: the benchmark data is synthetic and written by us, so it shows relative quality between stages, not real-life recall.
Paraphrased questions are the weak spot (recall@5 0.77). The Turbovec index missed our 0.95 recall bar (0.92), so the default is exact search
until the corpus grows (D-026).

---

## Run it

```powershell
winget install astral-sh.uv            # Python 3.12 is installed by uv
uv sync
# Ollama: install, then start it with the project's settings and check them
powershell -ExecutionPolicy Bypass -File scripts\start_ollama.ps1
ollama pull qwen3.5:4b
ollama pull nomic-embed-text

uv run pytest -q
uv run ruff check .
uv run python -m bench.embed_bench
uv run python -m bench.llm_bench
uv run python -m bench.retrieval_bench    # writes bench/results/retrieval_<date>.json
```

Use `127.0.0.1`, never `localhost`, for Ollama: `localhost` adds about 2 s to the first connection on this machine.
Runtime data (vault, keys, logs) belongs in `%LOCALAPPDATA%\Ragnarok\`, never in the repository or OneDrive.

## Layout

```text
ragnarok/
  memory/      vault.py (SQLCipher, migrations)  store.py (write, search)  service.py (async)  index.py (exact, Turbovec)
  vault/       keys.py (passphrase, recovery code, DPAPI)
  runtime/     gateway.py (the only path to a model)
bench/         embed_bench  llm_bench  retrieval_bench  retrieval_data
tests/         one file per milestone
docs/          MEMORY.md  DECISIONS.md  WORKLOG.md  history/
```

## Privacy rules

- All inference is local: one gateway, loopback only, no cloud models, no API keys.
- User content is stored only in the encrypted vault. The log files carry ids, timings and error types, never content.
- Secrets that appear in text (passwords, tokens, API keys) are redacted before anything is stored.
- Deleting a memory removes its row, search entries, vector and links, and leaves only an id and time in the audit log.
