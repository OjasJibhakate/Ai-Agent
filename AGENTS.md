# AGENTS.md — Ragnarok project context

Read this before changing anything. Codex reads this file directly; Claude Code reads it through `CLAUDE.md`.

## What we're building

Ragnarok is a local, always-on personal agent for the IIIT hackathon. Every model call goes to Ollama on this laptop.

- **Requirements:** `Ragnarok A Sovereign, Always-On Personal AI Agent.pdf` (FR1–FR4, NFR1–NFR4)
- **Design:** `ARCHITECTURE.md`
- **Why we chose things:** `docs/DECISIONS.md`
- **What happened:** `docs/WORKLOG.md`
- **Team repo:** https://github.com/OjasJibhakate/Ai-Agent

**Focus: the five pillars the PDF stresses.** These are **skills (FR2), planning (FR3), triggers (FR4), encryption (NFR1.2),
and recovery (NFR3)**. Memory, UI, and everything else exist to support them. When in doubt, spend the time on a pillar.

**Current stage:** design only. No code exists yet. The commands below are the planned interface.

## Hard rules (never break)

1. **Local models only.** All LLM and embedding calls go through `ragnarok/runtime/gateway.py` to `127.0.0.1:11434`. No cloud APIs, no API keys,
   no `-cloud` models, no telemetry SDKs.
2. **No plaintext user content on disk.** Persist only through the vault (`ragnarok/vault/`). Debug logs carry ids, timings, and error
   types only. Runtime data lives in `%LOCALAPPDATA%\Ragnarok\`, never in this OneDrive folder.
3. **Every tool call goes through `ragnarok/policy/permissions.py`.** That includes calls from triggers, skills, and plan steps. There is no back door.
4. **Every tool declares** a JSON Schema, `risk` (R0–R3), `idempotent`, `timeout`, `sandbox`, and its `fs`/`network` needs.
5. **Log before acting.** Commit the `tool_call` event before running a tool with side effects, and pass an idempotency key to tools that support one.
6. **Never block the event loop.** No sync network, disk, or heavy numpy work in async code. Use the DB writer thread or `asyncio.to_thread`.
7. **One chat model, fixed load options.** Don't add a second chat model or vary `num_ctx` per request: either one causes Ollama reloads, which means visible lag.
8. **No new infrastructure** (Docker, WSL, Redis, Postgres, Electron, vector-DB servers) without a `docs/DECISIONS.md` entry.

## Target machine

HP OMEN 16 · i5-13420H (8C/12T) · 16 GB RAM · RTX 4050 Laptop 6 GB · Windows 11 Home · no WSL/Docker.
Models must show `100% GPU` in `ollama ps`. Keep the laptop in hybrid (Optimus) graphics mode.

## Stack

- **Python:** 3.12 via uv. Don't use the Microsoft Store Python 3.13.
- **Runtime:** asyncio · Starlette/FastAPI + WebSocket JSON-RPC · httpx
- **Storage:** `sqlcipher3` (SQLCipher + FTS5) · numpy
- **Windows integration:** `pywin32` (sandbox tokens, Job Objects, DPAPI) · `argon2-cffi` + `cryptography` (key wrapping) · `keyring` · `pystray` · `windows-toasts`
- **Tools and triggers:** `mcp` · `watchdog`
- **CLI:** Typer + Rich
- **UI:** React + Vite + Tailwind
- **Quality:** pytest · ruff
- **Models:** `qwen3.5:4b` (chat, final pick after `bench/llm_bench.py`) · `nomic-embed-text` (embeddings)

## Setup and commands (planned)

```powershell
winget install Ollama.Ollama
winget install astral-sh.uv
# set the OLLAMA_* user env vars from ARCHITECTURE.md §10, then restart Ollama
ollama pull qwen3.5:4b
ollama pull nomic-embed-text
uv python install 3.12
uv sync
uv run rag doctor              # Ollama local-only, GPU share, vault, sandbox
uv run rag daemon              # daemon + UI on http://127.0.0.1:8765
uv run rag                     # interactive CLI
uv run pytest -q
uv run ruff check . ; uv run ruff format .
uv run python bench/llm_bench.py
uv run python bench/recovery_bench.py
cd ui ; npm install ; npm run build      # outputs to ragnarok/ui_dist
```

## Dev environment notes (this laptop)

- **Ollama** is installed at `%LOCALAPPDATA%\Programs\Ollama\ollama.exe` (not on PATH in older shells). Start it with
  `powershell -ExecutionPolicy Bypass -File scripts\start_ollama.ps1`; it applies the `OLLAMA_*` settings and prints them from the server log.
- **uv** is at the winget path; open a new shell or refresh PATH if `uv` isn't found. The project venv is `.venv` (Python 3.12).
- Run tests with `uv run pytest -q`. Don't run `ruff format` on markdown; the config already excludes it.
- The repo clone lives at `C:\Users\parth\dev\Ai-Agent` (outside OneDrive on purpose).
- **Current focus: persistent memory** (`docs/MEMORY.md`, milestones M0-M6). Other pillars are optional.

## Where things go

- **Pillars:** `ragnarok/skills/` + `ragnarok/sandbox/` (skills) · `ragnarok/planning/` · `ragnarok/triggers/` ·
  `ragnarok/vault/` (encryption) · `ragnarok/recovery/`
- **Runtime:** loop, context, hot path, and gateway in `ragnarok/runtime/`. Prompts are text files in `ragnarok/runtime/prompts/`.
- **Skill content:** user-facing skills in `skills/<name>/SKILL.md`. Our MCP servers in `mcp_servers/`.
- **Test data:** test fixtures (mailbox, calendar, invoices, demo repo, evil skill) in `fixtures/`. Never use real accounts in tests.

Full tree: `ARCHITECTURE.md` §12.

## Conventions

- Type hints everywhere. Pydantic models for events, tool schemas, plans, and RPC messages.
- A tool's description is what the model sees: at most 2 concrete sentences, saying when to use it.
- Keep the shared system-prompt prefix identical across call types, because prompt-cache reuse depends on it.
- **Tests:**
  - every tool gets a schema test and a permission test;
  - every skill ships with its own tests in `SKILL.md`;
  - anything touching side effects gets a kill-and-resume test.
- If you touch the hot path (retrieval, intent, gateway), run `bench/` and record the numbers in `docs/WORKLOG.md`.
- Keep PRs small, and have the area owner review.

## Docs and logs (keep them current)

- Made a design decision? Add an entry to `docs/DECISIONS.md` (next number, date, context, decision, consequences).
- Finished, measured, or broke something? Add a dated entry to `docs/WORKLOG.md`.
- Changed the design? Update `ARCHITECTURE.md` in the same PR.
- Never delete old design docs; move them to `docs/history/`.

## Ownership

| Area | Owner |
|---|---|
| Runtime + planning | P1 — _name_ |
| Encryption + recovery + memory | P2 — _name_ |
| Skills + sandbox + permissions | P3 — _name_ |
| Triggers + UI/CLI + Proof panel + bench + demo | P4 — _name_ |

## Definition of done

- Tests pass and `ruff` is clean.
- No hard rule is broken.
- Docs and logs are updated.
- The demo still works: `rag demo reset`, then walk the `ARCHITECTURE.md` §15 script.
