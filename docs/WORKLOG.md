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
