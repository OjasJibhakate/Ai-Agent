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
