# Work log

A dated record of what was done, found, and measured. Newest entries go at the bottom of each day.
Decisions live in `DECISIONS.md`; this file records evidence and progress.

---

## 2026-10-25

### Directions received
- Make it run smoothly on this laptop, with no lag.
- Keep it real: model the design on how Claude Code and Codex actually work, with no sci-fi framing.
- Focus on the five pillars the PDF stresses: skills, planning, triggers, encryption, recovery.
- Keep the docs and logs, and add project context (`AGENTS.md` / `CLAUDE.md`).

### Inputs reviewed
- **Proposal PDF** (4 pages): FR1 memory, FR2 skills, FR3 orchestration, FR4 proactivity; NFR1 privacy, NFR2 performance,
  NFR3 reliability, NFR4 usability. Its hard numbers are < 800 ms for retrieval + intent, ≤ 30% CPU/GPU, and < 5 s restore.
- **Team repo** https://github.com/OjasJibhakate/Ai-Agent at commit `4108834` (2026-10-25 14:54 +0530): it holds only
  `ARCHITECTURE-1.md` and no code. A copy is kept at `docs/history/original-repo-ARCHITECTURE.md`.
- **Gaps between that doc and the PDF:**
  - no planner or self-correction (FR3.1, FR3.3);
  - no skill registration (FR2.1);
  - no encryption at rest (NFR1.2);
  - no state recovery (NFR3.2) or graceful degradation (NFR3.1);
  - its "never delete" rule conflicts with the PDF's permanent delete (NFR4.2);
  - its voice and screen-capture work isn't required by the PDF.

### Architecture versions
- v1 written, then archived to `docs/history/ARCHITECTURE-v1.md`.
- v2 (`ARCHITECTURE.md`) is the current design: a harness modeled on Claude Code and Codex, sized to this laptop, built around the five pillars.

### Machine probe (this laptop)
| Item | Result |
|---|---|
| Model | HP OMEN by HP Gaming Laptop 16-wd0xxx, Windows 11 Home Single Language 10.0.26300 |
| CPU | 13th Gen Intel Core i5-13420H: 8 cores, 12 threads |
| RAM | 15.7 GB |
| GPU | NVIDIA GeForce RTX 4050 Laptop: 6141 MiB (6 GB), driver 592.82, 0 MiB used at probe time; plus Intel UHD Graphics |
| Disk | C: 65.0 GB free |
| Tools present | Python 3.13.14 (Microsoft Store alias), Node v24.18.0, npm, Git, nvidia-smi |
| Missing | Ollama, uv, Docker; WSL not installed ("The Windows Subsystem for Linux is not installed") |

### Package availability
Checked with `pip index versions --platform win_amd64 --python-version 3.12 --only-binary=:all:`:

| Package | Result |
|---|---|
| `sqlcipher3` | 0.6.4 ✓ |
| `sqlcipher3-wheels` | 0.5.7 ✓ |
| `sqlcipher3-binary` | ✗ (no Windows distribution) |
| `apsw-sqlite3mc` | 3.54.0.0 ✓ |
| `pywin32` | 312 ✓ |
| `windows-toasts` | 1.3.1 ✓ |
| `pystray` | 0.19.5 ✓ |
| `mcp` | 2.3.0 ✓ |

Not yet checked: whether the SQLCipher build includes FTS5. That is a Phase 0 task.

### Model research
- **`qwen3.5` library page.** The family badges are vision, tools, and thinking, and every tag lists a 256K context.

  | Tag | Size |
  |---|---|
  | `4b` (Q4_K_M) | 3.3 GB |
  | `4b-mtp-q4_K_M` | 4.1 GB |
  | `4b-q8_0` | 5.2 GB |
  | `2b-q4_K_M` | 1.9 GB |
  | `9b-q4_K_M` | 6.6 GB (too big for 6 GB VRAM) |

  The page documents neither the thinking toggle nor what the `mtp` tags are.
- **Secondary 2026 guides for 6 GB cards** recommend `qwen3.5:4b`, with `qwen3:4b` and `phi4-mini` as lighter options. Their benchmark figures are vendor-reported.
- **Ollama FAQ:**
  - local-only mode via `OLLAMA_NO_CLOUD=1` or `{"disable_ollama_cloud": true}` in `server.json`; the log then shows `Ollama cloud disabled: true`;
  - models stay loaded 5 minutes by default, and `keep_alive` -1 keeps them loaded;
  - `OLLAMA_MAX_LOADED_MODELS` defaults to 3 × GPUs; `OLLAMA_NUM_PARALLEL` defaults to 1, and memory scales with parallel × context;
  - the default context is 4096 tokens;
  - flash attention is automatic when supported;
  - `OLLAMA_KV_CACHE_TYPE=q8_0` uses about half the memory of `f16`;
  - `ollama ps` shows `100% GPU` when a model is fully on the GPU.
- **Codex Windows sandbox:**
  - The unelevated mode uses a restricted token derived from the user, ACL-based filesystem boundaries, and environment-level offline controls.
  - The elevated mode uses dedicated sandbox users plus firewall rules.
  - Known limits: directories where `Everyone` can already write stay writable, and the restricted token alone doesn't block network.

### Docs created
`ARCHITECTURE.md` (v2), `AGENTS.md`, `CLAUDE.md`, `docs/DECISIONS.md` (D-001 to D-017), and this log.

### Open questions
- How long is the hackathon, and what are the judging criteria? The build plan assumes about 36 hours.
- Team member names for the ownership table in `AGENTS.md`.
- Demo with fixtures only, or also show a real mail account?
- NFR2.2: confirm with the organizers that the 30% cap means the always-on idle and background load, and doesn't count the bursts while answering a request.
- Should these docs be pushed to the team repo?

### Next (Phase 0)
1. Install Ollama and uv, set the `OLLAMA_*` user env vars, and pull `qwen3.5:4b` and `nomic-embed-text`.
2. Write and run `bench/llm_bench.py`, then lock the model choice. Record load time, GPU share, tok/s, first-token latency, and tool-call accuracy here.
3. Check that SQLCipher has FTS5 (otherwise use `apsw-sqlite3mc`).
4. Run the restricted-token sandbox spike: a child process writes to its workspace and is denied in `~/Documents`.
5. Freeze the event, tool, plan, and RPC schemas.

### Sources
- https://docs.ollama.com/faq
- https://docs.ollama.com/cloud
- https://ollama.com/library/qwen3.5 and https://ollama.com/library/qwen3.5/tags
- https://developers.openai.com/codex/windows
- https://openai.com/index/building-codex-windows-sandbox/
- https://www.promptquorum.com/prompt-bites/best-local-llm-6gb-vram
- https://localaimaster.com/blog/best-ollama-models-tool-calling
- https://insiderllm.com/guides/qwen-models-guide/

### Memory focus (later on 2026-10-25)
- **Direction:** persistent memory is the main goal; everything else is optional. Confirm the memory design before building.
- **Team initial plan received** (SQLite source of truth, Turbovec/TurboQuant index, FTS5, RRF, consolidation, forgetting).
  Refined into `docs/MEMORY.md`:
  - mandatory encryption;
  - the index kept in RAM only;
  - two tiers, events → memories;
  - `origin` + `confidence`;
  - supersession with `valid_from` / `valid_to`;
  - an always-on profile block;
  - an outbox to keep SQLite and the index consistent.
- **TurboQuant check:** a Google Research method announced in March 2026 that combines PolarQuant and QJL. It targets KV-cache
  and vector compression. Turbovec is an independent MIT-licensed Rust library (github.com/RyanCodrai/turbovec), not a Google
  project. Whether its implementation includes QJL must be verified from the source (MEMORY.md §8).
- **Windows py3.12 wheels:** `turbovec` 1.1.2 ✓, `mem0ai` 2.2.1 ✓, `sqlite-vec` 0.1.9 ✓, `basic-memory` 0.24.1 ✓.
- **Repo:** the team repo is cloned to `C:\Users\parth\dev\Ai-Agent` (outside OneDrive, which can corrupt `.git`). It is now the canonical copy of the docs.
- **Not done yet:** Ollama install, which is waiting on the team's go-ahead.
- **Sources:**
  - https://research.google/blog/turboquant-redefining-ai-efficiency-with-extreme-compression/
  - https://pypi.org/project/turbovec/
  - https://discuss.pytorch.kr/t/turbovec-turboquant-rust/10295
  - https://www.cognee.ai/open-source-memory-frameworks-llm-agents
  - https://vectorize.io/articles/best-ai-agent-memory-systems

## 2026-10-25 (evening) - M0: environment, installs, first checks

### Install (what actually happened)
- `winget install Ollama.Ollama` stalled with the download stuck at 0 MB for about 10 minutes, so I stopped it.
  `uv` installed fine through winget (0.13.5).
- A direct `curl` download crawled at about 31 KB/s while several downloads competed. A clean test then showed about 0.5-1 MB/s to
  Cloudflare/GitHub, and the resumable retry (`curl -C -` with a stall timeout) ran at about 2.9 MB/s.
  The 1,662,012,016-byte installer finished and matched the expected size exactly.
- The installer's Authenticode signature is valid: Ollama Inc., Toronto. Ollama 0.40.11 installed silently into
  `%LOCALAPPDATA%\Programs\Ollama` (3.1 GB, 989 files).
- **Gotcha:** the installer started Ollama from my older shell, so the new `OLLAMA_*` variables were not applied
  (`Ollama cloud disabled: false`). I restarted the server with the variables loaded and its log going to a file.
  Verified from the server's own config line: `OLLAMA_NO_CLOUD:true`, `KEEP_ALIVE` forever, `MAX_LOADED_MODELS:2`, `NUM_PARALLEL:1`,
  `CONTEXT_LENGTH:16384`, `FLASH_ATTENTION:true`, `KV_CACHE_TYPE:q8_0`, and `Ollama cloud disabled: true`.
  GPU detected: NVIDIA GeForce RTX 4050 Laptop (CUDA); the Intel iGPU is dropped by default. See D-021.
- Models are **not downloaded yet**: `nomic-embed-text` (~0.3 GB) and `qwen3.5:4b` (3.3 GB) are next.

### Python environment
- `uv python install 3.12` gave CPython 3.12.15. `uv sync` installed `sqlcipher3` 0.6.4, `turbovec` 1.1.2, numpy 2.5.4, pydantic 2.14.1,
  pytest 9.1.1, ruff 0.17.2 (in `.venv`, not committed; `uv.lock` is committed).

### M0 check results
- **SQLCipher:** 4.19.0 (SQLite 3.53.4), FTS5 compiled in, exact-identifier search works, the file header is not `SQLite format 3`,
  demo strings are absent from the raw file, and opening without or with the wrong key fails.
- **Turbovec 1.1.2:** has every API we need (external uint64 ids, `remove`, `contains`, allowlist search, `to_bytes`/`from_bytes`).
  The README and binary describe rotation + Lloyd-Max scalar quantization + per-vector bias correction + optional calibration.
  **No QJL stage found** (README and binary strings only; the Rust source has not been read). Measured size: ~0.86 MB fixed
  overhead at 4-bit, then ~396 B/vector (7.0x smaller than float32 at 20k vectors); 2-bit ~204 B/vector (13.6x).
  Details are in `docs/MEMORY.md` section 8.1.
- **Tests:** 10 pass (`tests/test_m0_sqlcipher.py`, `tests/test_m0_turbovec_api.py`). `ruff check` and `ruff format --check` are clean.

### Mistakes and fixes
- My first Turbovec size test assumed no fixed overhead and failed (1.05 MB for 500 vectors vs the 192 KB I expected).
  Measuring showed a fixed header, not per-vector bloat, so the test was corrected to check the marginal size.
- `ruff format .` also reformatted Python code blocks inside the markdown docs, including the teammate's `ARCHITECTURE-1.md`.
  I confirmed the diff was formatting only, reverted all three files with `git checkout`, and restricted ruff to Python
  (`extend-exclude` + `format.exclude` in `pyproject.toml`). No docs were committed in that state.
- `pyproject.toml` was first written with a UTF-8 byte-order mark, which pytest's TOML parser rejects. Removed the BOM.

### Decisions
D-020 (store the Turbovec index as bytes inside the vault) and D-021 (start Ollama via a verified script).

### Next
1. Pull `nomic-embed-text` and `qwen3.5:4b`; confirm `ollama ps` shows `100% GPU`.
2. Write `bench/llm_bench.py` and `bench/embed_bench.py`: embedding latency, vector dimension (expect 768), chat tok/s,
   first-token latency, and whether `think: false` is honored. Record the numbers here.
3. Then M1: the encrypted vault, schema and migrations.

## 2026-10-25 (night) - M1: encrypted vault, schema, migrations

### Done
- `ragnarok/memory/vault.py`: opens SQLCipher with a raw 32-byte key, sets `journal_mode=WAL`, `synchronous=FULL`, `temp_store=MEMORY`,
  `secure_delete=ON`, `foreign_keys=ON`, runs `quick_check`, applies migrations, and provides `transaction()` (rollback on any error).
  See D-022.
- `ragnarok/memory/migrations/0001_initial.sql`: the schema from `docs/MEMORY.md` section 3.1 (events, memories, provenance, links,
  entities, tags, embeddings, external-content FTS5 with sync triggers), plus `index_outbox`, `index_blobs` (D-020) and a content-free `audit_log`.
  Child tables cascade on delete, so the M5 hard delete is one `DELETE`.
- `tests/test_m1_vault.py` (16 cases): reopen persistence, pragmas, header is not `SQLite format 3`, secret string absent from the
  WAL while open and from the main file after checkpoint, wrong/missing/short keys fail, migrations run once, newer schema refused,
  failed migration rolls back, transaction rollback, FTS follows insert/update/delete, CHECK constraints, cascade delete.
- Full suite: 26 passed; `ruff check` clean.
- Added `pythonpath = ["."]` to the pytest config so tests can import `ragnarok` (config only, no new dependency).

### Models
- `nomic-embed-text` pulled. `qwen3.5:4b` still downloading. My first background pull died at 43% when its shell exited;
  relaunched detached (Ollama resumes partial layers).

### Next
1. Finish the `qwen3.5:4b` pull; check `ollama ps` shows `100% GPU`.
2. `bench/embed_bench.py` (dimension, latency) and `bench/llm_bench.py` (tok/s, first token, `think: false`).
3. M2: `remember` / `recall` with FTS5 + Turbovec + RRF + rerank, with the restart test.
4. Ask the team before adding key-wrapping dependencies (D-022).

## 2026-10-25 (late night) - M2 core, gateway, first model benches

### Done
- `ragnarok/memory/index.py`: `VectorIndex` interface with `ExactIndex` (numpy) and `TurbovecIndex` (4-bit, serializable). Both unit-normalise.
  Turbovec raises on duplicate ids and on an empty allowlist, so the wrapper replaces on re-add and returns `[]` on an empty allowlist.
- `ragnarok/memory/store.py` (sync core) and `service.py` (async): `add_memory` with content-hash dedupe, origin-based confidence, entities, tags,
  provenance, and the outbox; hybrid `search` = FTS5 (OR of quoted tokens, stopwords dropped) + vector -> RRF (k=60) -> rerank
  (importance, entity overlap, project, recency, confidence) -> verify against SQLite (active only, `secret` hidden).
  If the embedder is down, `recall` returns lexical-only results.
- `ragnarok/runtime/gateway.py`: loopback-only, rejects `-cloud` models, `embed()` and streaming `chat_stream()` with a fixed `num_ctx` 16384 and `keep_alive -1`.
- Migration 0002 and the restart/replay design: D-023. Async test hook: D-024.
- Tests: **52 pass** (`tests/test_m2_memory.py` adds 26 cases across both index kinds: restart, checkpoint + replay, corrupt blob,
  lost outbox, archived/secret never surface, embedder down, ranking effects). `ruff check` and `ruff format --check` clean.
  These use a hashed bag-of-words fake embedder, so they prove the plumbing, **not retrieval quality**.

### Measured on this laptop (Ollama 0.40.11, RTX 4050 6 GB, both models loaded)
| Item | Result |
|---|---|
| `nomic-embed-text` | 768-d, `100% GPU`, 335 MB |
| Embed one query | p50 81 ms, p95 121 ms, max 225 ms (60 calls); cold first call 185 ms |
| Embed batch of 64 | 1.63 s total, 25.5 ms each |
| `qwen3.5:4b` | `100% GPU` (3.36 GB) at `num_ctx` 16384, runner llamacpp, `UNTIL Forever` |
| Decode speed | 48.8 tok/s median (48.4-49.1), within the 40-55 estimate |
| First token, same prefix | median 142 ms (128-160) for a ~565-token prompt |
| First token, changed suffix | 363 ms (one run) |
| `think: false` | honored (0 thinking chars); `think: true` produced 4,322 thinking chars and hit the 1,024-token cap |
| Cold load | Ollama `load_duration` 5.1 s, but **first token arrived after 54.6 s** |

### Findings and open issues
- **Query embedding is ~80 ms, not the 10-40 ms estimated.** It is still far inside the 800 ms retrieval budget, but the ARCHITECTURE figure should be
  corrected once we see whether it is HTTP overhead or GPU time.
- **Slow first request after a cold load.** The very first embed call took 54.6 s while a download ran in parallel (later 185 ms). For the chat model,
  the first request after `ollama stop` took 54.6 s to first token despite a 5.1 s reported load; my first attempt hit the client's 60 s timeout.
  I have not found the cause (not investigated yet). Practical rule for now: warm both models at daemon start, never on a user's first message,
  and use a long timeout for the warm-up call. After a reboot, expect about a minute before the first answer.
- The "prompt tokens" figure Ollama reports is the whole prompt, so it can't show cache hits; the 142 ms vs 363 ms first-token gap is the evidence of prefix reuse.
- A background `ollama pull` started from a tool shell is killed when that shell exits; start pulls with a detached process.
- A heredoc of ~200 lines containing quotes broke the bash tool; write files with the editor tool instead.

### Not done yet
- Tool-call accuracy on 20 prompts and plan validity (needed before D-003 is final); `think: false` is confirmed.
- Retrieval quality and latency with real embeddings (M2 benchmark); Turbovec recall@10 vs exact.
- Key wrapping (Argon2id, recovery code, DPAPI): waiting for approval of the dependencies (D-022).

### Next
1. `bench/retrieval_bench.py`: 500 synthetic memories + 100 labelled queries with real `nomic-embed-text` vectors; recall@5, MRR, p50/p95 for FTS-only,
   vector-only, hybrid, hybrid+rerank, and Turbovec vs exact at 4-bit.
2. M3: extraction and ADD/UPDATE/SUPERSEDE/NOOP with provenance and the 14-day timeline test.

## 2026-10-25 (late night) - key wrapping (D-025)

- Dependencies approved and added: `argon2-cffi` 25.1.0, `cryptography` 50.0.2, `pywin32` 312 (`uv.lock` updated).
- `ragnarok/vault/keys.py` + `tests/test_m1_keys.py` (14 tests): passphrase and recovery unlock, wrong secret fails, recovery code tolerates
  case/spacing, `key.json` contains neither key, passphrase nor code, change/reset passphrase keeps the same key, tampered or swapped wraps fail closed,
  a derived key opens a real vault, DPAPI round trip and damaged blob. **66 tests pass**, `ruff` clean.
- **Argon2id tuning (measured, 3 runs median):** t=3/64 MiB 0.07 s; t=3/128 MiB 0.10 s; t=3/256 MiB 0.26 s; t=6/256 MiB 0.38 s.
  My first default (t=3, 64 MiB) was only about 0.1 s, well under the 0.5 s target, so I raised it to t=6, 256 MiB. The 256 MiB is allocated only while unlocking.
- Not done: wiring the keystore into a `rag` command (first-run setup flow, showing the recovery code once), and `keyring` for account secrets.

### Next
Retrieval benchmark with real embeddings (500 memories, 100 labelled queries), then M3.

## 2026-10-25 (night) - retrieval benchmark, issue triage

### Correction to the earlier entry (cold start)
The "54 s to first token after a cold load" finding was **not reproducible**. Two clean re-runs gave:
- a tiny prompt after `ollama stop`: 7.0 s wall (6.5 s load), then 0.3 s per call;
- the real bench prompt (565 tokens) after `ollama stop`: **5.5 s to first token** (load 4.9 s).

The 54 s happened right after the 3.3 GB download finished, so it was probably the first read of a freshly written model file (not proven).
Working figure: a cold model costs about 5-7 s once. Warming both models at daemon start is still the right plan, with a long timeout on that call.

### Embedding latency, diagnosed
Ollama's own `total_duration` was 47.8 ms against 50.8 ms wall, so HTTP overhead is about 3 ms; the time is inside Ollama.
Under the benchmark's load, query embeds took p50 81.6 ms, p95 116.6 ms. Idle GPU wake-up is not the cause: first calls after 5-60 s idle took 95-145 ms.
**Do not use `localhost`:** the first connection to `http://localhost:11434` costs about 2.2 s every time (fresh process, both runs), while
`http://127.0.0.1:11434` is immediate. The gateway already uses the IP.

### Retrieval benchmark (`bench/retrieval_bench.py`, real vectors)
Data: 500 active memories (plus 15 superseded and 10 deleted), 124 queries: paraphrase 39, exact identifier 20, entity 15, temporal 10,
contradicted 15, deleted 10, unanswerable 15. Synthetic and seeded; distractors share vocabulary with the targets.
Results are in `bench/results/retrieval_2026-10-25.json`.

| Stage (min_semantic 0.60 in this run) | recall@5 | MRR |
|---|---|---|
| FTS5 only | 0.544 | 0.402 |
| Vector only | 0.647 | 0.564 |
| Hybrid (RRF) | 0.729 | 0.548 |
| Hybrid + rerank | 0.895 | 0.835 |

- Superseded and deleted memories were **never returned** (forbidden rate 0.000 in every run).
- Per category (hybrid + rerank, 0.60): exact 1.00, entity 1.00, temporal 1.00, contradicted 0.73, paraphrase 0.74.
  The temporal category is built so that recency decides it, so part of the rerank gain is by construction. Paraphrase is the real weak spot.
- Retrieval latency: query embed p95 117 ms + search p95 2.3 ms = about **119 ms** (NFR2.1 limit 800 ms).
- **Turbovec vs exact (500 real vectors):** 4-bit recall@10 0.920 (top-1 agreement 0.911, 1.06 MB); 2-bit recall@10 0.799. Below the 0.95 bar, so D-026 makes exact the default.
  Search latency, k=30: 10k vectors 3.7 ms (exact) vs 3.5 ms (Turbovec); 50k vectors 6.7 ms vs 1.1 ms.
- **Restart at 10k memories:** rebuild from embeddings 0.44 s; load from the saved blob 0.20 s (expected under 1 s: confirmed).
- Decision D-026: exact index by default, `min_semantic` 0.55.

### Still weak / not done
- Paraphrase recall@5 is 0.77 at the chosen cutoff; contradicted-fact queries find the new fact 73% of the time. Ideas: query expansion on low confidence,
  or a second embedding of each memory phrased as a question. Not tried.
- The labelled set is mine and synthetic; it checks plumbing and relative stage quality, not real-life recall.

## 2026-10-25 (night) - M3: extraction and ADD / UPDATE / SUPERSEDE / NOOP

### Done
- `ragnarok/memory/ingest.py`: events -> extraction (JSON schema) -> per candidate: hash/near-duplicate check, neighbour lookup (cosine >= 0.6), decision
  (a model call only when neighbours exist), apply with provenance. Prompts are text files in `ragnarok/runtime/prompts/` (`system.txt` is the shared prefix).
- Store additions: `link`, `reinforce`, `history`, `similar`, `record_event` (secret scrubbing, trivial-text drop, 5-minute dedupe), `pending_events`,
  `mark_ingested`, `expire_events` (keeps events a memory still cites). Migration 0003 adds `events.ingested_at`.
- Gateway: `chat_json` (schema-constrained, temperature 0, fixed seed) and a tool-calling `chat`.
- Rules enforced in code: see D-027.
- Tests: **98 pass**. `tests/test_m3_ingest.py` has 15 cases with a scripted model, including the 14-day timeline: day 14 returns the current meeting
  time and manager, the superseded versions stay in the history with `valid_to`, restating a fact reinforces it (confidence 0.95 -> 0.97) and adds provenance, and
  superseded facts are out of the index.

### Real-model quality (`bench/ingest_eval.py`, qwen3.5:4b, `think: false`, results in `bench/results/ingest_eval_2026-10-25.json`)
| | First run | Final |
|---|---|---|
| Extraction (15 hand-written events: 11 to keep, 4 to skip) | 15/15 on loose checks | 15/15 with the strict date check |
| Decisions (12 pairs) | 11/12 | 12/12 |
| Latency | extraction p50 1.7 s, decision p50 0.8 s | same |

How it got there (the useful part): the first run looked perfect on extraction, but I had accepted a past date for a future anniversary with a lenient regex.
Tightening the check exposed it, the prompt instruction alone didn't fix it, and a code rule did. The one decision failure was the central "preference changed"
case; a prompt rule fixed the model, and the code guard (D-027) now protects against the same mistake anyway.
**Caveat:** I tuned prompts on these same cases, so the scores are optimistic. They are small and hand-written. A fresh held-out set is still needed.

### Next
M4: always-on profile block, `memory_search` / `memory_save` tools behind a permission check, a chat loop with log-before-act, and a CLI.

## 2026-10-25 (night) - M4: profile, tools, chat loop, CLI, and what the real models exposed

### Done
- `memory/profile.py` (always-on block, 400-token budget, rebuilt only when memory changes), `runtime/context.py` (stable prefix, memory in the last message only),
  `runtime/tools.py` (`memory_search`, `memory_save`), `policy/permissions.py`, `runtime/loop.py` (log-before-act, validation, loop guard, step cap),
  `cli.py` (`init`, `chat`, `memory`, `ingest`, `doctor`). Decisions: D-028, D-029.
- Tests: **127 pass**. `test_m4_agent.py` covers the acceptance case (tell it a fact, kill the process, come back the next day: it is in the first prompt and the
  prefix is identical on the next turn), permissions, invalid/unknown tools, denied and approved writes, loop guard, tool and model failures, and that tool
  traffic is never mined. `test_m4_cli.py` covers init, wrong passphrase, recovery code, the OneDrive guard, memory commands, and a scripted chat session.
- `rag doctor` on this laptop: Ollama on loopback 0.40.11, cloud disabled (from the server log), both models installed, both `100% GPU`.
  (The keystore is not created yet: `init` needs the owner's passphrase, so I did not run it.)

### Real-model end-to-end demo (`bench/m4_demo.py`, qwen3.5:4b + nomic-embed-text, throwaway vault, fake clock)
Three sessions: day 1 tell it a preference and a manager; day 2 after a restart ask about them, then change the preference; day 3 ask again.
- **Run 1: 3/4 checks. Found a data-loss bug.** "My manager is Ravi..." was saved by the tool but the decision step answered NOOP against the meeting preference,
  so nothing was stored; the assistant still said "I've got that down", and "Who is my manager?" failed the next day.
- **Run 2 (after fixing that): 2/4.** The change "after 3pm" had cosine 0.98 against "after 2pm" and was swallowed by the near-duplicate shortcut, so the day-3 answer was the old
  preference. Both bugs are D-029; measured cosines are in that entry.
- **Run 3 (final): 4/4.** The preference change and the manager both survive restarts; the 2pm version is `superseded`, the 3pm one is active, and the answer on day 3 is "after 3 PM".
- Latency per turn on this laptop (RTX 4050): retrieval 55-77 ms, first token 190-530 ms, whole turn 0.7-4.4 s (tool turns include a second model call).
- Caveats: a single scripted scenario, run three times, with the same model that I tuned the prompts on. It shows the plumbing and the safety rules work; it is not a measure of recall in daily use.
  The assistant's wording after a save is sometimes loose (it paraphrases); the stored facts are what matter.

### Next
README commands, then M5 (consolidation, forgetting, undo supersede, `memory pin`), a held-out eval set, and the first-run flow on the owner's laptop.

## 2026-10-25 (evening) - first real run, the open_app skill, speed, and the terminal screen

### What the owner saw
First run on the real laptop: `init` worked, the first two passphrase attempts were typos and `chat` quit on the first one, then in `chat` "open steam" and
"open microsoft edge" did nothing and the assistant made up reasons ("Edge isn't in your shortcuts yet"). Cause: the agent had no tool for it, and nothing told it to say so.

### Fixed
- `open_app` skill (D-030). Verified for real, not just with a fake: `Get-StartApps` lists 194 launchable apps here (Steam, Microsoft Edge, Chrome, Notepad, VS Code...),
  names resolve to exactly one app, and a real launch of Notepad produced a new process within about 1 s (I closed it afterwards). The first app-list lookup takes 1.9 s,
  so the chat loads it in the background.
- Resolution bug found by a unit test: "visual studio" silently picked Visual Studio Code over Visual Studio Installer. The "shorter name wins" shortcut was removed; ambiguity now asks.
- Honesty rule in the chat prompt; three passphrase tries before quitting.

### Speed (`bench/chat_latency.py`, real models, median of 3 rounds, fake launcher)
| Turn | Before | After |
|---|---|---|
| greeting | 919 ms | 570 ms |
| remember a fact | 3,852 ms | 1,644 ms |
| open an app | 2,177 ms | 1,127 ms |
| answer from memory | 668 ms | 448 ms |

What changed: tool turns no longer call the model a second time when the tool's own sentence is the answer; replies are shorter. What is left in a tool turn is writing the tool call itself
(41-62 tokens at 49 tok/s); hiding the `reason` argument cuts that to about 34 tokens (measured on 8 prompts: median 53 -> 34), which the "after" column above does not yet include.
Startup: models warm in the background, so the welcome screen was usable after 0.10 s in a full scripted session.

### Terminal screen
`ragnarok/ui.py` (colours, welcome box, spinner, footer) and `ragnarok/chat.py` (the chat session and the background learner). 12 distinct colours; every box edge lines up in both Unicode
and ASCII modes (tested). `rag doctor`, `init` (recovery code in a box) and the memory commands use the same screen.
Full scripted session on the real models: 6.3 s for 8 inputs, all turns 0.4-1.5 s, "open whatsapp" answered honestly that it is not installed.
Tests: **193 pass**, `ruff` clean.

### Not verified
- How it looks and feels in the owner's own Windows Terminal (I can check the characters and codes, not the pixels). The spinner animation is tested for behaviour only.
- Ctrl+C while a model is answering exits the program (it does not cancel just that answer).

### Next
M5: consolidation, forgetting, undo for a wrong supersede, pin/unpin and export from the CLI.

## 2026-10-25 (night) - file search for the owner's Desktop

- The owner asked the agent to find data-structure files on the Desktop; it had no file tool. Added `find_files`, `list_folder`, `read_file` (D-031).
- Real folders (names only): the Desktop is `OneDrive\Desktop` (21 top-level items plus large project folders), Documents 3 + 33, Downloads 4. The first search over all roots took 0.4-0.9 s.
- Run on the real model with the owner's question: the model chose to search inside files and answered with two unrelated READMEs, missing `Data_structure_certificate.pdf`. Cause: I had exposed a `contents` option. Fix: names first,
  text only when few names match, no option for the model. Then the model's summary dropped the file names, so plain lookups now show the list directly.
- A noisy "weaker matches" fallback made a search for a (correctly hidden) `id_rsa` return 213 irrelevant folders; removed. Ranking put deep folders ahead of `Desktop\Data_structure_certificate.pdf`; now shallow and files first.
- Tests: **241 pass**, 1 skipped (symlink needs admin; a directory junction test covers the same escape on Windows). Safety cases covered: traversal, outside roots, symlink/junction, secrets by name and by exact path, binary and oversized files, time budget, result caps.
- Not verified: reading PDFs (unsupported), and how the final screen looks in the owner's terminal.

## 2026-10-25 (night) - `persistent_memory.py`

- Added `ragnarok/persistent_memory.py` (D-032): the whole memory lifecycle in one file with a plain-language explanation at the top, plus `correct`, `revert`, `pin`, `export` and `stats`.
- 12 new tests (`tests/test_persistent_memory.py`): tell it things, close everything, reopen, and it still knows them; the vault needs the right key and shows no readable text on disk; explicit saves report ADD/NOOP/SUPERSEDE;
  correct keeps history and carries over pin and importance; revert works one step at a time; pinned memories lead the profile and cannot be archived; forget removes everything and leaves only a tombstone;
  export refuses without consent and writes every version; secrets are scrubbed before storage.
- Two test mistakes of mine fixed on the way (the fake embedder cannot match "live" to "lives"; "Likes tea" is itself a preference). No code bugs found.
- Not done: CLI commands for correct/revert/pin/export, forgetting by usefulness, summaries.

## 2026-10-25 (night) - M5: consolidation, forgetting, delete cascade, controls

### Done (D-033)
- `memory/consolidate.py`, `memory/forget.py`, the delete cascade in `store.delete`, `PersistentMemory.consolidate/prune/undo_summary/maintain`, migration 0004 (`meta`), CLI controls, idle maintenance in the chat.
- Tests: **283 pass**. `tests/test_m5_tidy.py` (24 cases): the usefulness formula; prune never touches pinned, uncovered, important, recently used or summary memories, and is safe to repeat and to dry-run;
  grouping by project and week and the "week still filling" rule; a summary keeps provenance and leaves sources untouched; second run does nothing; the per-call limit; seven kinds of bad summary rejected with nothing stored;
  model failure retried next run; deleting a source removes the summary, its search entries, vector, links and index entry and leaves only id tombstones (the spec's deletion check); undo restores what pruning hid;
  maintenance once a day; and a six-week story where weekly notes shrink to summaries while a stated preference and yesterday's note stay.

### Found by tests and the real model
- **Tokenizer mismatch (real bug):** the novelty check tokenized the summary and its sources differently, so an ordinary identifier like `w0` was flagged as an invented number. It failed safe (rejected a good summary), but would have
  quietly crippled consolidation on real notes. One tokenizer now; words containing digits (`mp4`) are checked as well.
- **Identical summaries merge:** two weeks with the same summary sentence collapsed into one summary through the duplicate rule. Kept as is and documented (D-033).
- **Real model, 4 hand-written weeks (`bench/consolidate_eval.py`):** first run 3/4 accepted. The rejected summary said "30-day" where the note said "30 day" (a false rejection, safe but too strict). Hyphenated forms of words that are in the sources are now accepted;
  second run 4/4. **Caveat:** I changed the validator after seeing that case, on a set I wrote, so 4/4 shows the pipeline works, not how often real notes will pass. Accepted summaries read well and name only people, places and facts from the notes.

### Not done / not verified
- Consolidation has not run on the owner's real vault (it has only a few days of notes; the rule needs finished weeks of at least four notes each).
- Grouping by entity, and an "export encrypted with a passphrase" option.
- Whether weekly summaries lose details people care about: needs real use.

### Next
Use it for a few days (`memory stats` shows what is happening); then a held-out evaluation set for extraction and retrieval.

## 2026-10-25 (night) - "open chrome and go to yt and search carrymanati"

- The owner's request opened Chrome and stopped. Two causes: no tool for web pages, and the fast-finish rule ending the turn after the first step (D-034).
- Added `open_website` (URL built by code from a table of known sites; arbitrary addresses only if the user typed them; chrome/edge/firefox/default), made `open_app` yield on multi-step requests, and told the model to go straight to the website tool.
- Real model, fake browser, six requests: Chrome + YouTube search (it kept the user's spelling "carrymanati"), YouTube search in Edge, Wikipedia in the default browser, an honest "I can't click to play the first video", a bare "open steam" still instant (0.9 s),
  and an address the user typed. 1.0-2.6 s each.
- One real launch: Chrome to `https://www.youtube.com/results?search_query=carryminati` (Chrome process count 19 to 20, call returned in 12 ms). I could not see the screen.
- On this laptop the default browser is Edge, so "chrome" must start Chrome from its registered path; Firefox is not installed (the tool says so and falls back to the default).
- Tests: **336 pass**. 53 new tests cover URL building for every known site, hostile addresses, the typed-by-user rule, browser fallback, the click/play limitation, and the exact request as a two-step turn.
- Not done: correcting typos in search words (YouTube does its own "did you mean"); reading page contents; opening a specific video.

## 2026-10-25 (late night) - "make it read the screen and do the task that i give him"

### Built (D-035)
- `ragnarok/skills/screen/`: `capture.py` (GDI capture, area-average downscale, in-memory PNG), `winapi.py` (foreground window, own-terminal detection, Esc state, minimise/restore), `input.py` (`SendInput`, key allow-list, secret/card text refused, blocked windows), `task.py` (the look-decide-check-log-act loop), `tools.py` (`read_screen`, `use_computer`).
- The terminal asks before `use_computer` runs, shows the safety note, and prints each step as it happens. `RAGNAROK_NO_SCREEN=1` turns the screen tools off.
- Tests: **550 pass**, `ruff` clean. About 215 are new and run on a fake desktop (no real screen, mouse or windows): capture and PNG, key and text rules, the loop's stop conditions, permission checks on every action, log-before-act order, terminal restore even when the task crashes, and the chat approval flow.

### Measured (real model)
- Pointing: the model answers on a 0 to 1000 scale whatever the prompt says. At 1280x720, 48/48 points inside the target, median miss 5 px of 1920, about 1.3 s per look; 1920 wide costs 2.6 s for no gain; 960 wide 1.0 s (47/48). Small print read correctly 6/6 at 1920, 1280 and 960. Vision adds about 48 MiB of VRAM. (Synthetic pages I drew, not the owner's screen.)
- **Live end-to-end (`bench/screen_eval.py`, real model + real capture + real mouse on a small test window, 3 trials x 5 tasks): 5/15.** form 2/3, buttons 1/3, search 1/3, scroll 1/3, list 0/3. Earlier runs of the same harness: 2/5 (first), 3/10 and 2/10 (after the first fixes, before `fill` and the screen-only check), 6/10 (2 trials, after them). The spread between runs is large; 15 trials is a small sample.
- 1 of 15 runs said "done" while the check failed. In the 10-run session two did (one an empty Name field that the completion check accepted, one a truthful claim but with a repeated click).

### Found by the live test (all fixed, each with a test)
- Loose action schema produced clicks with no `y`; now a per-action schema (measured: every reply has its fields, about 1.9 s per look).
- Scroll sign inverted by the model; now `direction`. It also typed without clicking the field; now `fill`.
- Change detection was blind to typed text (thumbnail average); now counts changed pixels. The test clock hid this: its fake time advanced on the Esc watcher's polls, so a real thread wait burned the whole 150 s budget; the watcher now has its own `poll` hook.
- The completion check parroted the step list; it now sees the screen only. Repeated total counts became "in a row", scrolling/arrow keys/waiting are exempt while the screen changes, and wanting to repeat an action first asks "is it already done?".
- A scroll moved the mouse and was read as the user taking over; `wait` also made Esc deaf for its duration (the watcher ignores Esc while we inject input); both fixed.
- Safety gap found by a test: after a declined "Submit order" click, pressing Enter bypassed the confirmation. The last target is now remembered even when refused.

### Not done / not verified
- **Acting is unreliable (33% on the test window).** Dense lists are the worst case: the model is 1 to 2 rows off. Ideas, not built: a zoom step (second look at a crop before clicking), Windows UI Automation for apps that expose controls, a larger vision model if one fits in 6 GB.
- Not tried on real applications (Notepad, a browser page, Settings) or on a second monitor/scaling setup; I did not look at the owner's real screen while building this. The test window was opened briefly on the desktop during live runs.
- `read_screen` has not been run on a real desktop yet by me; it should be tried with `look at my screen and tell me what is open`.
- No answer-quality test of the model's long descriptions of busy screens.

## 2026-10-25 (night) - "download cat images", "it should be able to download anything I said", "make a doc file for them"

### Built (D-036, D-037)
- `skills/images.py` (`download_images`), `skills/downloads.py` (`download_file`), `skills/documents.py` (`create_document`), `skills/folders.py` (the real Desktop/Documents/Downloads), folder-by-name lookup in `skills/files.py`, prompts for all of it, `RAGNAROK_NO_DOWNLOADS=1`.
- Tests: **763 pass** (was 550), `ruff` clean. About 210 are new: URL and IP rules (including the metadata address, IPv4-mapped IPv6, redirects to private hosts, loops), HTML refusal, size caps, disk space, file names and reserved Windows names, path traversal, crash cleanup and retry (kill-and-resume, including a timeout mid-download), no overwrite, idempotence, `.docx` structure and escaping, log-before-act and permission checks through the real agent loop, and "the owner is not asked about a download that cannot happen".

### Measured
- Real model, fake servers, 10 requests: correct tool and arguments every time ("download cat images" 1.7 s, "download steam" 1.0 s); unknown programs and a paper title got "paste the link"; "open youtube and search lofi beats" still opens YouTube.
- **Real internet (my machine, temp folder, deleted after):** 3 Wikimedia Commons cat photos in 3.8 s with credits; Steam's installer (2.3 MB) in 0.8 s with an `MZ` header, the internet mark and a SHA-256; python.org (a web page) refused.
- Real Microsoft Word 16 opened a generated `.docx` (private invisible instance): 7 paragraphs, formats and special characters intact.
- The owner's scenario on the real model (temp tree): "what files are in my deep learning folder" lists the 5 files (1.5 s); "make a doc file for them" creates `Deep Learning Files.docx` with the 5 exact names (2.0 s).

### Found
- The live Commons API serves thumbnails from `thumb.wikimedia.org`; the allow-list refused it and the tool said "no pictures found" while every mock test passed. Added the host, added a test with the real reply shape.
- The model asked `list_folder` for `all` and presented the Desktop as if it were the answer; "all" now asks which folder. It also chose Markdown for "a doc file"; `.docx` is now the stated default in the tool and the prompt.
- From the owner's pasted session: the terminal was not "left visible"; the focus simply moved to their other terminal window, which the typing rule rightly refused. The 40 px mouse stop was too touchy (D-037).
- The first download design asked the owner `[y/N]` before refusing impossible requests; the arguments are now checked first.

### Not done / not verified
- No search for arbitrary files (only named installers, typed links and Commons pictures); no resume of an interrupted download (it restarts); DNS rebinding not closed (D-036).
- The 10 installer links are fixed in code and will go stale; the Python entry is pinned to 3.12.8, not "latest".
- Not tried in the owner's own `chat` window. The screen-task path that "make a doc file" used to fall back on is now unnecessary for that request, but is still 5/15 on the test window (D-035).

## 2026-10-26 - "give it all the permissions ... do what Claude Code can do", "make a program / ppt", "6 hours left"

### Built (D-038)
- `policy/modes.py` (ask / edits / full) + `/mode`; `skills/pathguard.py`, `fileops.py` (7 tools), `shell.py` (`run_command`), `extract.py` (PDF/Word/PowerPoint text), `office.py` (.pptx and .xlsx by hand), `create_presentation` and `create_spreadsheet`, folder-by-name and name-aware lookup in `files.py`. Dependency added: `pypdf` (owner approved).
- **Tests: 981 pass** (was 763). New: mode matrix, path guard (vault, system, links, `..`), every file tool, PDF/Word/PowerPoint extraction on generated real documents, 40 command refusals and 15 allowed commands, real PowerShell runs, `.pptx`/`.xlsx` package integrity and formula safety. Optional real-Office test: `RAGNAROK_OFFICE_TESTS=1`.

### Measured (real model)
- **Everyday battery `bench/daily_tasks.py`** (26 requests, throwaway home, default ask mode with every approval answered yes, passes only if the right thing exists on disk): **first run 19/26**, after the path fixes **25/26**, the last being a checker bug (the model had saved the to-do file on the Desktop, my check wanted Documents).
  Passing: python, java and c programs, "write and run", ppt (also with "6 slides"), excel, Word doc, PDF to txt, new folder, folder + copy, rename, delete (to the Recycle Bin), list a folder, find a file, read a PDF, download images and a program, open an app, open YouTube, a plain question without tools, run a command, a to-do file, and honest "I can't" for an alarm and for sending an email. 1 to 8 s each; the slowest was the follow-up ".py extension" (13.9 s: five tool calls where none were needed).
- Real PowerPoint opened the generated deck (4 slides, text correct, clean title and content slides when exported as images); Excel computed `=SUM` correctly.

### Found
- The model wrote `C:\Users\name\...` in 5 of the 7 first failures: it copied the example in our own tool description. Fixed the description, and made paths forgiving (stand-in names, bare names, known folders).
- PowerPoint rejected the first deck as corrupt. Bisecting with PowerPoint's own file showed the cause: a slide master's body placeholder must say `type="body"`. Excel opened its file on the first try.
- `format D: /q` slipped past the first refusal rule (a regex word boundary after a colon); a test found it.
- A formula character class allowed `;`; xlsx formulas never use it, so it was only an opening. Removed.
- Several of my own edits were corrupted by backslash handling in the shell layers (a NUL byte appeared in two source files). A scan for NUL bytes over `ragnarok/` and `tests/` is clean; the Edit tool is used for anything with backslashes now.

### Not done / next (plan for the 6 hours)
Variety and follow-up robustness, `open_path` ("open the ppt you made"), latency with 30+ tools, triggers (reminders, FR4), a visible plan and check for multi-step requests (FR3), crash/resume for the new tools, a demo script, README.

### 2026-10-26 later - everyday battery 58/60, spreadsheet arrays, open_path, triggers started
- `bench/daily_tasks.py` now has 60 requests (typos, bare file names, follow-ups, multi-step, plain chat, honest refusals): **58/60** on the real model. The two failures were a spreadsheet the model sent as half-bracketed text (fixed: `columns`/`rows` arrays plus a lenient parser for lists written as text; 3/3 after) and "remind me ..." (needs triggers, in progress).
- New: `open_path` (open a file or folder; never programs or scripts), file lookup by loose name ("deep learning" finds `Deep_learning`, "climate ppt" finds the deck; same-level duplicates are listed, not guessed), one shared rule (`skills/steps.py`) for when a tool may end the turn so "make a ppt and then open it" keeps going.
- Triggers (FR4) started: schedule parser (`triggers/schedule.py`: "in 10 minutes", "tomorrow at 9am", "every weekday at 8", cron with a 5-minute floor), vault tables for triggers, fires and the Inbox (migration 0005), `TriggerStore`. **Not yet done:** engine, notifications, tools, chat/daemon wiring, tests for the triggers package.
- 1025 tests pass; ruff clean.

## 2026-10-26 - "make the CLI more colourful, unique, minimal, like Claude Code, with animations and transitions"

### Built (D-039)
- `ragnarok/ui.py`: 24-bit gradient palette with 256-colour fallback, rounded welcome box (`banner_lines`, `play_banner`), prompt hairline with the mode, new glyphs, tool-result footer, permission card, shimmering star spinner, closing-star `farewell`. `ragnarok/chat.py` wires them in.
- Tests: **1047 pass** (was 1032), ruff clean. New tests cover truecolor detection and output, the gradient glow and shimmer, hairline widths, the permission card, the animated welcome (cursor moves back to the name and ends on the same screen), animation off by environment variable and for pipes, and the farewell.

### Found
- The first-run hint line was wider than an 80-column terminal; it is now a short list.
- The banner glyph clashed with a spinner frame in an existing test, so the banner uses its own glyph.

### Not done / not verified
- Not yet looked at in the owner's real terminal; timings of the animation (about 0.7 s at start) are by design, not measured on screen.
- `/clear` and mode changes have no transition yet.

## 2026-10-26 (afternoon) - three failures from real use, and a password in the chat

### Reported by the owner (pasted sessions)
1. "make me a ppt on <folder> ... 10 slides": run_command, Get-ChildItem, read_file, extract_text... then "I stopped after too many steps".
2. "make me a animation for bubble sort": six run_command attempts (python script, pip install matplotlib), no answer printed.
3. "read the pdf and files in the image processing folder and make a new pdf with summary of all": list_folder, read_file x5, step cap.
4. A login request with a pasted id and password: the agent invented an address (a Singapore university) and typed it 15 times.

### Built (D-040, D-041)
- `skills/digest.py` (`summarize_files`), `skills/pdfwriter.py`, `skills/webpage.py` (`create_animation`, `create_webpage`), step cap 8 with an explanatory message, a `on_tool` hook so the spinner shows while tools run after streamed text, the permission card on its own line, grouped footer, password and address guards in `skills/screen/input.py`, wider secret scrubbing in `memory/store.py`.
- Routing fixes found by the battery: `download_file` rejects a bare file name ("it is a file name, find it and read it"); `create_presentation` rejects an outline that puts everything on one slide; tool descriptions for read_file, download_file, summarize_files tightened.
- Tests: **1236 pass** (was 1047), ruff clean. New: PDF writer read back with pypdf, folder summaries (formats, slide counts, large files, model down, hostile file text, off-limit paths, repeat runs, stale .part), animation steps replayed for five algorithms on random inputs, page lockdown, chat flow around cards/spinner/step cap, credential and address guards.

### Measured (real model, `bench/daily_tasks.py`, 64 tasks)
- The three owner scenarios: folder to 10-slide ppt 17 s, folder to PDF 16 s, bubble sort animation 1.4 s - all pass, repeatedly. Login request: declines to log in, asks for the link (3/3).
- Found by the battery and fixed: 10 "failures" were my new fixture files being picked up by old checkers (test input now excluded); "summarize the pdf in my downloads" went to download_file (message fix); "ppt on climate change" made 1 slide (tool now sends the outline back).
- Known not working: "remind me to call mom at 6pm" (triggers not built).

### Not done / not verified
- The screen skill has not been run against a real login page; the typing guards are unit-tested only.
- Folder summaries were tried on a made-up "image processing" folder, not the owner's real one.

### 2026-10-26 (evening) - triggers (FR4) built (D-042)
- Built: `triggers/engine.py` (time, cron, folder, recovery, headless goals), `triggers/tools.py` (`set_reminder`, `schedule_task`, `watch_folder`, `list_reminders`, `cancel_reminder`), `triggers/toast.py`, chat wiring (`/inbox`, `/reminders`, `/cancel N`, live alerts, unread notice at start), and the `background` profile may now read.
- Tests: **1265 pass**, ruff clean. New: 24 engine/tool tests on a fake clock (once-only, crash between fire record and message, late reminders, coalesced schedules, restart without repeats, folder debounce/temp files/changes/downtime/flood, goals failing or interrupted, pause) and 5 chat tests.
- Real model (battery, now 68 tasks): reminder at a time, reminder in minutes, weekday schedule and cancel all pass (4/4). Full battery before this change: 61/64 (the three failures were two model-variance cases, 7 slides instead of 8 and a summary through extract_text, and the reminder that is now fixed).
- Not done: an always-on daemon (reminders only fire while the chat is open), mail and calendar triggers, proactive suggestions (FR4.2), approving a proposed action from the Inbox in one step.

### 2026-10-26 (night) - planning (FR3) built, recovery started (D-043)
- Built: `planning/` (models, validation, store, planner, runner, intent), vault migration 0006, `/plan`, `/resume`, live plan view, `recovery/breaker.py` shared by all agents, `bench/plan_tasks.py`.
- Tests: **1304 pass**, ruff clean.
- Real model (`bench.plan_tasks`, 3 multi-step goals): 3/3 on the latest run, 2/3 on another; plans take ~4-5 s; whole goals 15-35 s. Found by running it: guessed `file:` names failed good steps; the model chose `list_folder` over the named tool, so each step is now told which tool to use; retries get all tools.
- Not done: interrupted-step safety (asking before re-running a non-idempotent tool that was mid-call), conversation restore after a crash, the restore timer, and `bench/recovery_bench.py` (kill and resume 50 times). Skills registration (FR2.1) and the sandbox (FR2.3) are not started.

## 2026-10-26 (night) - the pixel cat GUI (D-044), "we can't open CMD for every task"

### Built
- `ragnarok/gui/`: `sprites.py` (27 hand-drawn pixel frames: walk, sit, sleep, yawn, scratch, groom, alert, carried, pounce...), `brain.py` (behaviour), `bridge.py` (engine to window), `chat.py` (the popup), `app.py` (cat window, engine thread), `unlock.py` (passphrase and first-run dialogs), `winapi.py` (taskbar, DPI, one copy only), `startup.py` (start with Windows).
- Looked at on the real screen (screen capture of the running program): the cat stands on the taskbar with a see-through background; a real click and typed message ("what is 15 percent of 2400") gave "15 percent of 2400 is 360." in 1.4 s; picking the cat up, carrying it across the taskbar and dropping it made it fall and land where it was dropped; a pointer brought within about 170 px made it run over and sit facing it; a far pointer was ignored.

### Found by using it
- `Bridge.emit(kind, **data)` clashed with events that carry their own `kind` (the engine crashed on the first reply): the event name is now positional-only.
- The first cat chased the pointer anywhere on the taskbar, and wandered every few seconds: the owner found it distracting. Now it only notices a pointer within about 200 px, and the default behaviour is calm (measured over 30 simulated minutes x 15 cats: about 1.1 changes a minute, walking 1.6% of the time, 36 px a minute; the old behaviour was 6.2 changes a minute, walking 52%, 1,200 px a minute). Tests pin these numbers.
- My own helper that stopped test cats matched itself and killed the shell: match only Python processes.

### Tests
- **1380 pass**, ruff clean. New: 60 on the cat's behaviour and pictures, 12 on the bridge (including the real chat engine run through the GUI console with the Allow button gating a risky tool), 12 on the chat window (drawn for real and clicked), 5 on start-with-Windows.

### Not done / not verified
- Not yet used by the owner with their real vault (all live tests used a throwaway one). Recovery work (kill-and-resume bench) is paused for this.
- One monitor only; a taskbar on the left, right or top is not specially handled.

### 2026-10-26 (late night) - one short command: meow
- Owner: "i don't want to run this whole command i want just one short command". `rag gui --command on` writes `meow.cmd` and `meow.vbs` to `%LOCALAPPDATA%\Microsoft\WindowsApps` (already on the PATH). Checked live from a fresh PowerShell: `meow` starts the cat with no console window left behind; a second `meow` opened the running cat's chat and did not start a second cat.
- Tests: **1386 pass** (new: the command files, removal, PATH check, the summon event).

### 2026-10-26 (late night) - a more detailed cat, a smaller chat, a smaller cat (D-044 addendum 2)
- Owner: "make the cat more detailed in both look and behaviour and make the chat box a little small", then "make the cat just a little small".
- Look: `gui/catart.py` draws every pose from shared parts (tabby stripes, green eyes, collar, tail, whiskers, a lighter chest); `sprites.py` now has 38 frames of 44 x 36 cat pixels, including loaf, stretch, tail swish, ear twitch, breathing and pupils that look left or right.
- Behaviour (still calm by default): stretches after waking, swishes its tail and twitches an ear while sitting, breathes slowly, follows a nearby pointer with its eyes, lies as a "loaf" before it sleeps, and stays awake for its first minute. The last one came from a live check: a freshly started cat was asleep at once and looked missing.
- Size: chat window 400 x 520 to 340 x 420 with 9 pt text; cat 7/4 screen pixels per cat pixel instead of 2 (about 68 x 61 px on a 1080p screen at 100% scaling, measured on a real screenshot; it grows with Windows scaling).
- Tests: **1478 pass, 5 skipped**, ruff clean. New: `test_gui_art` (every frame has the same size, only known colours, nothing clipped at the edges), `test_gui_scale` (size at 100/125/150/200% scaling, the outline survives fractional scaling, the background stays see-through), more behaviour tests (stretch, swish, breathing, look, loaf, awake-at-start). One test needed changing because a new cat no longer dozes off in its first minute.
- Looked at on the real screen with the throwaway vault: the cat stands on the taskbar edge, the outline is crisp, and the test cat was stopped afterwards.
- Not verified: the owner has not yet used the new look with their real vault. Open question for the owner: an "unlock automatically on this Windows account" checkbox for an existing vault (it touches key handling, so it waits for a yes).

### 2026-10-26 (night) - automatic unlock from the cat (D-045) and a cuter cat (D-044 addendum 3)
- Owner: "yes add the unlock checkbox and also make the cat more cute". The checkbox needed an explicit yes first because it touches key handling; it got one.
- Unlock: the passphrase dialog for an existing vault now offers "Unlock automatically on this Windows account" (unticked by default, with a line saying anyone signed in as the user can then open it). The right-click menu has the same switch to undo it. No new key code: it calls the existing `enable_dpapi` / `disable_dpapi`, and only after a correct passphrase. 11 new tests (`test_gui_unlock`), the dialog replaced by a function and Windows DPAPI real.
- Checked on the real screen with a throwaway vault and a made-up passphrase: dialog shows the box and the warning line; a real mouse click ticks it; Enter creates `key.dpapi`; the next start opens straight to the cat with no dialog. My first try ticked the box with Tab and Space sent too quickly and it did not register, so no file was created; a real mouse click worked, and a second keyboard-only run (Tab, Space, Enter, with short pauses) worked too, so it was my test driver's timing and not the dialog.
- Also seen: the unlock dialog takes keyboard focus on purpose, so anything typed in the terminal while it opens lands in the dialog (the first test dialog showed a wrong-passphrase error nobody typed on purpose). Harmless, but it is why starting the cat from a terminal while typing elsewhere can look odd.
- Cat: bigger rounder head, narrower pointed ears, big dark glossy eyes with a glint and a green rim, pink cheeks, a tiny nose and "u" mouth; the grooming paw, heart, "!" and thought dots moved to suit. New calm behaviour: a cat watching the pointer tilts its head (one way, then the other about 3.5 s later, 1.3 s each time), and a purring or happy cat wags its tail. 41 frames.
- Looked at on the real screen at 6x: the running cat shows the new face and stands on the taskbar edge.
- Tests: **1504 pass, 3 skipped**, ruff clean (new: art checks for the eyes, blush, tilt and wag; behaviour checks for the tilt and the wag; a test that a lone cat never tilts).
- Not verified: the owner has not yet seen the new face on their own screen or used the automatic unlock with their real vault.

### 2026-10-26 (night) - the chat window made smaller again (300 x 360)
- Owner: "make the chatbox more small". 340 x 420 became 300 x 360; text wraps follow.
- Found by looking at it (new helper `scripts/preview_chat.py`, which fills the real window with a sample chat, a permission card and a plan card): at that width the header status ("model · GPU · N memories") was cut off at both ends and the close button was pushed out of the window, so the window could not be closed with the mouse. Fixed by packing the close button first and shortening the status to `model · GPU`; the memory count moved into the greeting. Cards and their buttons fit.
- Tests: **1505 pass, 4 skipped**, ruff clean. New: the size, the close button and status staying inside the window, and the greeting with the memory count. One skip is a test that sometimes cannot open a Tk window (`test_gui_scale`), not related to this change.
- Not verified: how it feels in daily use with a long conversation (the transcript scrolls; only short conversations were looked at).

### 2026-10-26 (night) - typed text cropped in the chat box
- Owner: "in the chatbox the character i type is half cropped". Cause, found by measuring: the transcript area asked Tk for its default 265 px of height, which left the input box only 22 px of the 44 px it needs (two lines), so typed text was cut off. It was introduced when the window was made smaller (300 x 360); at 340 x 420 there was just enough room, which hid it.
- Fix: the transcript asks for almost no size (1 x 1) and expands to fill what is free, so the input box always gets its full height. Looked at on the real screen: two full lines of typed text show; the transcript still gets about 240 px.
- Test added (`test_the_input_box_is_drawn_at_the_height_it_needs...`): it fails without the fix (22 >= 44) and passes with it.
- Tests: **1507 pass, 3 skipped** (the skips are the usual Windows symlink and real-Office ones), ruff clean.
- Also found while counting skips: under pytest's default output capture, creating a Tk window fails about 3 times in 100 with "Can't find a usable init.tcl" (measured: 2 failures in 60 windows with capture, 0 in 120 with `-s` or `--capture=sys`, and 0 in 60 in plain Python). The real program does not run under capture, so the cat is not affected. The GUI tests used to turn that into random skips (0 to 2 per run); they now share one retrying helper in `tests/conftest.py` (`make_tk` / `hidden_tk`), which gave 150 of 150 windows in a probe and three clean runs of the GUI tests in a row.

### 2026-10-26 (morning) - `meow` did nothing: a quit cat left its process alive
- Owner: "typing meow is not working nothing is happening". Found by looking: a `pythonw -m ragnarok gui` process from 04:53 was alive, idle (17 threads, no CPU), with **no window at all** (listed its windows with `EnumWindows`: only hidden Tk helpers), and a screenshot showed no cat on the taskbar. It was the cat the owner quit from the menu after the previous fix; it never exited, so it held the one-copy lock and every `meow` just signalled it.
- Cause: worker threads from `asyncio.to_thread(read_line / approve_line)` blocked on a queue are joined at interpreter exit. Reproduced in a 10-line script: the process hangs until killed.
- Fix: `exit_now()` in `gui/app.py` ends the process after the window loop. New `tests/test_gui_exit.py` (a subprocess with a blocked worker exits in time). I stopped the stale process (by its PIDs).
- Tests: **1508 pass, 3 skipped**, ruff clean.

### 2026-10-26 (morning) - can the cat read the screen? (checked, no code change)
- Owner: "make it know what is going on the screen so we can ask and make it do things". D-035 already has `read_screen` and `use_computer`; the open question was whether they work from the cat, whose chat popup is a borderless, topmost window in the agent's own process (the skill treats own-process windows as "the terminal" and minimizes them).
- Checked with a scratch script (real Windows calls, real `qwen3.5:4b`, a fake borderless red topmost popup holding the focus): the popup was out of the captured frame, came back afterwards, and the answer described the real screen correctly. So reading works from the cat's window.
- Not checked: `use_computer` from the cat (D-035 measured 5 of 15 on a test window), and a click-through of the real cat chat (my simulated click did not open the chat and the typing went to the focused terminal, so I stopped driving real input).

### 2026-10-26 (morning) - screen: menu item, and 3 of 10 to 13 of 15 on the live eval (D-046)
- Owner: "do 1 then 2". (1) The cat's right-click menu has "What is on my screen?" (opens the chat, waits 450 ms so the chat holds the focus, asks). (2) Ran `bench.screen_eval` and fixed what the failures showed.
- Baseline 3/10. Pointing in isolation was 10/12 (11/12 with zoom); a read-under-the-marker check on every click gave 19/23 to 20/23 and once rejected a good point, so it was left out. The same image and prompt gave different answers in different runs.
- Fixes (D-046): hold back a bare `type` before any click; on a repeated click spot, read what is under it and tell the model; a specific note when a scroll moves nothing; near-identical clicks on one target count as a repeat. Eval after each: 10/15, 11/15, **13/15**.
- Tests: new `tests/test_screen_landing.py`, `tests/test_gui_screen_menu.py`; four existing screen tests adjusted (clicks meant to be distinct are now spaced apart; one `type`-first lock test clicks first).
- Not verified: real apps (browser, Explorer, Office); the menu item on the owner's screen (needs the cat restarted); a long task. The eval moves the real mouse for about 6 minutes.

## 2026-10-26 (afternoon) - finishing the requirements: skills, restore, daemon, suggestions, mail, roles, log (D-047 to D-051)

Owner: "complete these requirements". Audit first (FR1-FR4, NFR1-NFR4, 21 sub-requirements): 13 done, 5 partial, 2 unmeasured, 1 missing. This entry closes the gaps.

### Built
- **FR2.1, FR2.3 (D-047):** skill registry (`skills/registry.py`, vault migration 0007, `/skill`), the sandbox (`sandbox/`: Job Object, scrubbed environment, audit hook), the `text-stats` skill and `fixtures/evil-skill`. These had been built earlier today but were not in the docs.
- **NFR3.2 (D-048):** `recovery/restore.py` and `bench/recovery_bench.py`.
- **FR4.1, FR4.2 (D-049):** `rag daemon` (`triggers/daemon.py`, `--startup on`), `triggers/proactive.py`, the engine's suggestion hook, `/suggest`, mail files as "new mail" events.
- **FR3.2 (D-050):** `skills/mail.py` (list/read/send over a local mailbox folder), `skills/tasks.py` (task board, migration 0008), `fixtures/mailbox/` (one normal and one hostile email).
- **NFR4.3, NFR4.1 (D-051):** `policy/roles.py`, `/role`, `/log`, `/log plan`, `rag log`.
- **NFR2 (D-049):** `bench/resource_bench.py`.

### Measured
- **Tests: 1616 pass, 3 skipped, ruff clean** (was 1551). New files: `test_recovery_restore`, `test_roles_and_log`, `test_proactive_daemon`, `test_mail_tasks`, `test_chat_recovery_roles`.
- **Kill and resume (NFR3.2):** 50 of 50 runs passed; restart p50 78 ms, p95 89 ms, max 223 ms (target 5,000 ms). 43 of 50 kills cut a tool call in half and it was found and closed each time.
- **Background cost (NFR2.2):** 0.63% of one core, 0.05% of the machine, peak 50 MB over 20 s with 3 x 300 watched files and 8 triggers (target under 30%).
- **Hot path (NFR2.1):** intent check p95 0.013 ms over 2,040 sentences; memory search p95 6.7 ms over 500 memories with a stand-in embedder. The real query embedding adds the 82 ms p50 / 117 ms p95 recorded in `bench/results/retrieval_2026-10-25.json`, so the whole path is well under 800 ms.
- **Real model (`bench.daily_tasks`, mail and task prompts):** list mail, read a hostile email without obeying it, send an email, add a task: 4 of 4. "honest: alarm" fails, and also fails on the code from before this change (checked by stashing), so it is not a regression; the model sets a reminder and says it only works while the program runs.

### Found by doing it
- Python text-mode writes on Windows turned LF files into CRLF and made a one-line change look like a whole-file rewrite. Files were converted back to LF; scripted edits now use `newline=''`. `ruff format` on a whole folder reformatted unrelated files (`skills/screen/capture.py`, `bench/daily_tasks.py`); those were reverted, so format only the files you touched.
- Giving headless goals the `automation` role made them refuse changes instead of proposing them, which broke `test_a_folder_goal_runs_headless_and_changes_nothing`. Goals keep the `background` profile and inherit the chat's role.
- `/resume` after a crash would have re-run a half-done non-idempotent tool silently; it now asks.

### Not done / not verified
- The daemon was tested with a fake clock and a real vault, not left running for hours; `--startup on` was not tried at a real Windows login. A hidden daemon needs the DPAPI unlock opt-in.
- Real IMAP/Gmail (D-050), model-written suggestion text, and per-user (OS account) separation behind roles are not built.
- NFR2.2 covers this process only; GPU/CPU use of the model itself is in `bench/llm_bench.py`.
- The owner has not yet used any of this with their real vault.

### 2026-10-26 (evening) - "open gmail and write a mail" did nothing, so it now does it (D-050 addendum)
- Owner: it could not write a mail in Gmail, and "it should open the browser and write it on its own". Cause: the mail tools only worked on a local folder.
- Added `compose_email` (draft) and `send_gmail` (writes and sends, asks first), `Windows.title_of`, and the `SENDS_EMAIL` ask rule. Tests: **1631 pass** (33 in `test_mail_tasks`, including a title that flickers while Gmail loads, a sign-in page, Esc, and a window that changes).
- Real model (`bench.daily_tasks`): "open gmail and write" picks `compose_email`, "open gmail, write and send" picks `send_gmail`, 3 of 3 with the send key stubbed.
- Real browser (the owner's Edge, send key stubbed so nothing was emailed): found by trying it. The first version took a flicker of the tab title during Gmail's start-up ("Untitled") for a changed window and refused; and its "sent" check could be fooled by the front window changing. Now the compose title must hold for 2 s, and the check follows the compose window itself. Final run: it found the compose window, "pressed" the stub, and correctly reported the draft still open.
- Not verified: a real Ctrl+Enter send (I did not email anyone). My four test runs left about five Gmail draft tabs open in Edge.

### 2026-10-26 (night) - every doc brought up to date with the build
- Owner: "update all files to latest like README and any previous files according to present state".
- `README.md` rewritten: status at a glance, the real commands (`gui`, `daemon`, `log`, slash commands incl. `/role`, `/log`, `/suggest`), done and remaining tables, measured table with the new benches, layout with every new folder, privacy rules for mail.
- `AGENTS.md`: current stage (all pillars built), the stack as it really is (no web server, Typer, MCP or watchdog), real setup commands, skill format (`skill.json`), definition of done points at `docs/HACKATHON.md` section 6, and a note that Windows Python writes CRLF unless `newline=''`. `CLAUDE.md`: two stale phrases fixed.
- `ARCHITECTURE.md`: status row, new section 0.1 "Built versus designed" (what differs and why), a status column in section 13, and notes on sections 14 and 15 that they are historical. `docs/MEMORY.md`: status line.
- `ARCHITECTURE-1.md` (an older "J.A.R.V.I.S." draft, which the project rules say not to use as a framing) moved to `docs/history/ARCHITECTURE-v0-team-jarvis.md` instead of being deleted.
- The copies in the OneDrive project folder (`README.md`, `AGENTS.md`, `CLAUDE.md`, `ARCHITECTURE.md`, `docs/*.md`) were stale and were overwritten from the repository.
- Not verified: I did not re-run the benchmarks for this entry; the numbers are the ones already recorded above.

### 2026-10-26 (night) - the last two items: inference measured, a real Gmail send, VS Code (D-052)
- Owner: "do these two things and make it 22/22".
- **Inference (NFR2.2):** new `bench/inference_bench.py`. Ollama 0.0% CPU idle, 8.3% (peak 10.7%) of the machine while generating; GPU 3.8% idle, 77.7% (peak 86%) during an answer of about 6 s; duty cycle 9.8% at one request a minute, so about 11% GPU averaged. The first version reported 43% CPU at idle: that was other programs on the laptop, so Ollama's processes are now sampled by id.
- **Real Gmail send:** run live to the owner's own address. Found by doing it, in order: Ctrl+Enter did nothing (the page had no focus in the message); after clicking into the message it still did nothing; Tab then Enter moved focus to Send but Enter did not press it; clicking the blue Send button worked ("Message sent" toast in a screenshot). The first click spot landed on the arrow because the Send half and arrow half are two different blues; the "sent" check failed because this view stays on the page and the title does not change, so it now watches the Send button grey out (only the arrow half stays dark). One later live run sent the mail but reported it could not confirm it; the wait was lengthened. About six test emails reached the owner's inbox.
- **VS Code:** `open_in_editor` tool, tests with a fake editor, and a real-model task ("write hello.py, then open it in vs code") that passes. A live launch could not be verified: VS Code fails to start from the shell used here (`code --version` too, ICU file descriptor error), which looks like that environment, not the tool.
- Tests: **1638 pass, 3 skipped**, ruff clean. Docs updated: `REQUIREMENTS.md` (22 of 22, two with stated readings), `HACKATHON.md`, `README.md`, `ARCHITECTURE.md`, `AGENTS.md`, D-052.
- Not verified: the VS Code launch on the owner's own screen; the Gmail send more than the live runs above; the architecture page (published earlier) still says 1631 tests and "20 of 22".

### 2026-10-26 (night) - voice: text to speech and speech to text (D-053)
- Owner: "tts and stt mode, quickly", then "it is not taking my voice inputs, check it properly".
- Built `ragnarok/voice.py` and `ragnarok/voice_capture.py`; `/voice on|off|talk|test` and `/listen` in the chat. **1672 tests pass**, ruff clean.
- Live: speaking works (Zira, through the speakers). Listening: the first design failed, found by measuring: Windows allowed the microphone, the default input was the Bluetooth earbuds (silent), the laptop microphone peaked at 0.3-2% of full scale (input volume 36%), and the recogniser waited 30 s instead of 4. After recording the audio ourselves and boosting it, the sentence is detected but still misheard because the signal is so weak. Not fixed: needs the input volume raised.
- Not done: a wake phrase; a microphone button on the cat; a better recogniser. Not verified: recognition with a properly loud microphone.

### 2026-10-26 (night) - voice listening fixed with local Whisper (D-053 addendum)
- Owner connected headphones and approved a new dependency ("Add local Whisper").
- Found by measuring each stage: the pipeline (record, amplify, feed) was right; the Windows recogniser was the weak part (2% confidence on a real sentence, 39-52% on clean synthetic speech).
- Added `faster-whisper` and `ragnarok/voice_whisper.py`; model downloaded once (29 s). Live, through the headset: "open steam and tell me what time it is" came back as "It is open screen and tell me what time it is." Whisper is used by default; Windows stays as the fallback. The model loads in the background on the first `/listen` or `/voice talk`.
- Tests: **1678 pass, 3 skipped**, ruff clean (45 voice tests; the model and microphone are faked).
- Not verified: accuracy on longer sentences, other accents or the laptop's own microphone; a wake phrase is not built; the cat has no microphone button yet.

### 2026-10-26 (night) - double-click the cat to talk (D-053 addendum)
- Owner: "if I double tap on the cat the voice should be activated".
- Added `gui/clicks.py` (single versus double click), `Voice.cancel()`, `Console.voice_state` / `heard`, and the cat's handling in `gui/app.py` and `gui/chat.py`. **1686 tests pass**, ruff clean (8 new in `tests/test_gui_voice.py`).
- Not verified live: restart the cat (right-click, Quit, then `meow`) and double-click it.

### 2026-10-26 (night) - the wake phrase, and "it keeps listening" (D-053 addendum 2)
- Owner: "set up a wake phrase hey ragnarok", then "it just keeps on listening, fix it".
- Fixed: a double-click on the cat is now one request (`/listen aloud`), not a standing conversation; Whisper's inventions on noise are dropped.
- Built `ragnarok/wake.py`, `/voice wake on|off`, the cat's menu item and header status. **1726 tests pass**, ruff clean (new: `tests/test_wake.py`, 38 tests).
- Found by testing: a race (the wake loop could take the next sentence meant for the request) is closed by pausing it until the request is done. Live: the wake phrase did not trigger on the owner's headset (one burst heard as "I'm Waking Inside Video."); on clean synthetic speech it works 12 of 12.
- Not verified: the wake phrase on the owner's voice; CPU use with it on for a long time; the cat's double-click on the real window (the cat must be restarted).

### 2026-10-26 (night) - the wake phrase fixed, and Indian English
- Owner: "it is not working the wake phrase", "set it to indian english", "try again".
- Root causes found live (D-053 addendum 3): microphone input volume 36% (too quiet), then 100% (clipped); clips included 1.2 s of trailing silence so "hey Ragnarok" was rejected as a conversation; end-of-sentence judged only against room noise.
- Fixed: trailing silence trimmed, end threshold relative to the speaker's peak, "too loud" warning, input volume set to 70%. Added `small.en` for requests (wake stays on `base.en`), the Indian-English prompt, an automatic English (India) voice when installed.
- Live: the real wake listener woke on the owner's voice. **1727 tests pass**, ruff clean.
- Not verified: longer spoken requests with `small.en`; the cat after a restart; an English (India) voice (none installed).

### 2026-10-26 (night) - wake phrase: stuck on "thinking" (D-053 addendum 4)
- Owner: the phrase is heard but then it just shows "thinking" and does not take the request.
- Found: the window never got the "idle" signal again (a typing-wait kept running across loops). Fixed with `Console.ready_for_input`; added a chime and "Yes? I'm listening", "rangrok" spellings. **1735 tests pass**, ruff clean.
- Not verified: on the owner's cat after a restart.
