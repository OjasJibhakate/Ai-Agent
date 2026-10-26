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

**Current stage (2026-10-26):** all five pillars and the memory milestones M0-M5 are built, tested and measured (1735 tests); all 22 checklist items have a test or a measurement. Open gaps: VS Code launch unverified live, no IMAP or external project-management product, GPU above 30% during each answer. Evidence per requirement: `docs/REQUIREMENTS.md`; judge guide: `docs/HACKATHON.md`.

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
- **Runtime:** asyncio · httpx · pydantic (no web server; the interfaces are the terminal chat and the tkinter pixel cat)
- **Storage:** `sqlcipher3` (SQLCipher + FTS5) · numpy
- **Windows integration:** `pywin32` (DPAPI, Job Objects, window titles) · `argon2-cffi` + `cryptography` (key wrapping) · toasts through PowerShell (no extra library)
- **Voice:** Windows `System.Speech` for speaking; `faster-whisper` (CPU, `base.en`, downloaded once) for listening, with the Windows recogniser as fallback
- **Tools and triggers:** standard library only: a polling folder watcher (no `watchdog`), `email`/`smtplib` for mail (no `mcp`); `pypdf` for PDFs
- **CLI:** argparse + our own `Console` (no Typer or Rich)
- **UI:** the pixel cat (`ragnarok/gui/`, tkinter). No web UI, tray icon or Proof panel was built
- **Quality:** pytest · ruff
- **Models:** `qwen3.5:4b` (chat, final pick after `bench/llm_bench.py`) · `nomic-embed-text` (embeddings)

## Setup and commands

```powershell
winget install Ollama.Ollama
winget install astral-sh.uv
# set the OLLAMA_* user env vars from ARCHITECTURE.md §10, then restart Ollama
ollama pull qwen3.5:4b
ollama pull nomic-embed-text
uv python install 3.12
uv sync
uv run python -m ragnarok doctor   # Ollama local-only, GPU share, vault, encryption
uv run python -m ragnarok init     # once: passphrase and recovery code
uv run python -m ragnarok chat     # interactive chat (slash commands: /help)
uv run python -m ragnarok gui      # the pixel cat (after `gui --command on`, type `meow`)
uv run python -m ragnarok daemon   # background triggers with no chat open
uv run python -m ragnarok log      # what the agent did and why it was allowed
uv run pytest -q
uv run ruff check .                # run `ruff format` only on files you changed
uv run python -m bench.llm_bench   # run benches as modules so `ragnarok` imports
uv run python bench/recovery_bench.py   # NFR3.2, kill and resume
uv run python bench/resource_bench.py   # NFR2, background cost and hot-path latency
uv run python bench/inference_bench.py  # NFR2.2, CPU and GPU while the model answers
```

## Dev environment notes (this laptop)

- **Ollama** is installed at `%LOCALAPPDATA%\Programs\Ollama\ollama.exe` (not on PATH in older shells). Start it with
  `powershell -ExecutionPolicy Bypass -File scripts\start_ollama.ps1`; it applies the `OLLAMA_*` settings and prints them from the server log.
- **uv** is at the winget path; open a new shell or refresh PATH if `uv` isn't found. The project venv is `.venv` (Python 3.12).
- Run tests with `uv run pytest -q`. Don't run `ruff format` on markdown; the config already excludes it.
- The repo clone lives at `C:\Users\parth\dev\Ai-Agent` (outside OneDrive on purpose).
- **Current focus:** the five pillars are built; what is left is in `docs/REQUIREMENTS.md` (VS Code launch check, IMAP).
- Files are written with LF line endings. On Windows, Python's text-mode `open(..., 'w')` writes CRLF; pass `newline=''`.

## Where things go

- **Pillars:** `ragnarok/skills/` + `ragnarok/sandbox/` (skills) · `ragnarok/planning/` · `ragnarok/triggers/` ·
  `ragnarok/vault/` (encryption) · `ragnarok/recovery/`
- **Runtime:** loop, context, hot path, and gateway in `ragnarok/runtime/`. Prompts are text files in `ragnarok/runtime/prompts/`.
- **Skill content:** user-facing skills in `skills/<name>/` (`SKILL.md` + `skill.json` + scripts); registered with `/skill add`. No MCP servers were built (mail, tasks and the other tools are in `ragnarok/skills/`).
- **Test data:** test fixtures (`mailbox`, `evil-skill`; calendar, invoices and a demo repo were not needed) in `fixtures/`. Never use real accounts in tests.

Full tree: `ARCHITECTURE.md` §12.

## Conventions

- Type hints everywhere. Pydantic models for events, tool schemas, plans, and RPC messages.
- A tool's description is what the model sees: at most 2 concrete sentences, saying when to use it.
- Keep the shared system-prompt prefix identical across call types, because prompt-cache reuse depends on it.
- **Tests:**
  - every tool gets a schema test and a permission test;
  - every skill ships with its own tests in `skill.json` (they run in the sandbox before it registers);
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
- The demo still works: walk the script in `docs/HACKATHON.md` section 6 (there is no `rag demo reset`).
