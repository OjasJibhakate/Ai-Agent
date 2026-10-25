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
