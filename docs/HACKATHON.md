# Ragnarok for the judges

One page to explain the project: what the hackathon asked for, the approach we took, what we achieved, how we can prove it, and what is not finished. Everything below is backed by a test, a benchmark or a file you can open. Numbers are from 2026-10-26.

Companion files: `docs/REQUIREMENTS.md` (the 22-item tracker), `ARCHITECTURE.md` (design), `docs/DECISIONS.md` (why we chose each thing, D-001 to D-051), `docs/WORKLOG.md` (what happened, including what broke). The architecture diagrams are in the published page "Ragnarok Architecture".

---

## 1. What the hackathon asks for

**The brief:** *Ragnarok: a Sovereign, Always-On Personal AI Agent.* A fully local assistant that remembers, uses tools, completes multi-step work and helps without being asked, with no data sent to cloud AI.

| Group | Requirement | In plain words |
|---|---|---|
| FR1 Persistent memory | FR1.1 episodic, FR1.2 semantic, FR1.3 consolidate and forget | Remember conversations and tasks, remember facts and preferences, merge and prune over time |
| FR2 Skills and tools | FR2.1 registration, FR2.2 dynamic selection, FR2.3 sandbox | Add reusable tools, pick the right one for a request, run them safely |
| FR3 Workflows | FR3.1 planning, FR3.2 cross-application, FR3.3 self-correction | Split a goal into steps, work with email and similar apps, check results and retry |
| FR4 Proactive | FR4.1 always-on triggers, FR4.2 contextual proactivity | Start work on a schedule, a file or an email; suggest things from memory |
| NFR1 Privacy | NFR1.1 local data, NFR1.2 encrypted memory, NFR1.3 local LLM | Nothing leaves the machine; the database is encrypted with keys the user controls; Ollama only |
| NFR2 Performance | NFR2.1 under 800 ms, NFR2.2 under 30% resources | Fast retrieval and intent; light background load |
| NFR3 Reliability | NFR3.1 graceful degradation, NFR3.2 restore within 5 s | One failed tool does not stop independent work; crash recovery |
| NFR4 Control | NFR4.1 explainability, NFR4.2 memory control, NFR4.3 permissions | Show what happened; view, edit, export, delete memories; roles and approval for risky actions |

**The brief's own warning:** the numbers are targets to *demonstrate*, not claim. So every number below comes from a benchmark you can re-run.

**Target machine:** HP OMEN 16, i5-13420H, 16 GB RAM, RTX 4050 with 6 GB, Windows 11, no WSL or Docker. This shaped the design: one small chat model (`qwen3.5:4b`) that fits fully on the GPU, no servers to install, everything in one Python process plus Ollama.

---

## 2. Our approach in one paragraph

A single Python 3.12 program talks only to Ollama on `127.0.0.1`. Every message and every tool call is written to one encrypted SQLCipher file (the vault) *before* it happens, so memory, plans, triggers and recovery all read the same truth. A small agent loop lets the model call tools; every call is validated, checked by one permission function (role, then mode, then risk tier) and only then run, in a sandbox when it is a user-added skill. Long goals become plans whose steps are verified and retried. Triggers run in the same engine, in the chat or in a background `rag daemon`. We spent our time on the five pillars the brief stresses (skills, planning, triggers, encryption, recovery) and measured each.

---

## 3. Requirement by requirement: approach, result, proof

Status: **Done** = built and covered by a test or measurement. **Partly** = the core works, a named piece does not.

### FR1 Persistent memory
| Req | Approach | Result | Proof |
|---|---|---|---|
| FR1.1 episodic | Every message, tool call and result is an event in the vault, with time and session | **Done** | `memory/store.py`; `/log`; tests `test_m2_*` |
| FR1.2 semantic | A background learner reads events and extracts facts and preferences with the local model; retrieval fuses full-text (FTS5) and vector search (RRF) and reranks | **Done** | `memory/ingest.py`, `memory/index.py`; `bench/ingest_eval.py`, `bench/retrieval_bench.py` |
| FR1.3 consolidate and forget | Related memories are merged into summaries (reversible); stale ones are archived, never silently lost | **Done** | `memory/consolidate.py`, `forget.py`; `bench/consolidate_eval.py` |

### FR2 Skills and tools
| Req | Approach | Result | Proof |
|---|---|---|---|
| FR2.1 registration | A skill is a folder (`SKILL.md` + `skill.json` + scripts). `/skill add` runs the skill's own tests in the sandbox, shows what it declares, asks approval, pins a content hash; a changed file switches the skill off | **Done** | `skills/registry.py` (D-047); `tests/test_skills_registry.py` |
| FR2.2 dynamic selection | The model sees each tool's schema and a two-sentence description and chooses; bad arguments go back to the model | **Done** | `bench/daily_tasks.py` on the real model checks the *result on disk*, not what the model says |
| FR2.3 sandbox | Three layers: a Windows Job Object (memory and process caps, kill on close), a scrubbed environment, and an audit hook that refuses the vault, `ctypes`, spawning and writes outside the run folder | **Done** | `sandbox/`; `fixtures/evil-skill`; `tests/test_sandbox.py` (12 attacks, all stopped) |

### FR3 Workflows
| Req | Approach | Result | Proof |
|---|---|---|---|
| FR3.1 planning | The model writes JSON steps (max 8) naming tools and dependencies; the plan is validated (tools exist, no cycles, risk per step), shown as a card, approved, then saved | **Done** | `planning/`; `bench/plan_tasks.py` 3/3 on the latest run (2/3 on another) |
| FR3.2 cross-application | Files, Office documents, app launcher, shell, web pages; email as a local mailbox folder plus **Gmail in the browser** (`compose_email` writes a draft, `send_gmail` writes and sends it with no help); **`open_in_editor` opens code in VS Code**; a task board in the vault | **Done** | `skills/mail.py`, `skills/fileops.py`, `skills/tasks.py`. **A real Gmail send was run live and Gmail showed "Message sent"**; real-model checks pass. No IMAP and no external project-management product; the VS Code launch itself was not verified live |
| FR3.3 self-correction | Each step has a typed check; a failure gets up to 3 tries with the failure fed back, then a re-plan (max 2) that keeps finished steps, then the step is marked failed | **Done** | `planning/runner.py`; `tests/test_planning.py` |

### FR4 Proactive
| Req | Approach | Result | Proof |
|---|---|---|---|
| FR4.1 always-on | Date, cron, folder and mailbox triggers. Each fire has a unique key written before the action, so it happens once even across a crash or with two processes running. `rag daemon` runs it with no window; goals run read-only and propose changes to the Inbox | **Done** | `triggers/`; `tests/test_triggers.py`, `tests/test_proactive_daemon.py`. Not soak-tested for hours |
| FR4.2 contextual | Rules over the vault (no model call): an unfinished plan, a remembered deadline with no reminder, a habit with no schedule, the last project in the morning. Max 3 per run, the same idea once a week, never from secret memories | **Done** | `triggers/proactive.py` (D-049). Four patterns only; text is fixed, not model-written |

### NFR1 Privacy
| Req | Approach | Result | Proof |
|---|---|---|---|
| NFR1.1 local | Data lives in `%LOCALAPPDATA%\Ragnarok`; the program refuses a home folder inside OneDrive; logs hold ids and timings, not content | **Done** | `cli.home_dir`; no cloud client in the code |
| NFR1.2 encrypted | SQLCipher 4. A random vault key is wrapped by a passphrase (Argon2) and by a one-time recovery code; Windows DPAPI unlock is opt-in and shown as a trade-off | **Done** | `vault/keys.py`; tests check no plaintext reaches the file, including task titles |
| NFR1.3 local LLM | One gateway to `127.0.0.1:11434`; no API keys, no cloud models | **Done** | `runtime/gateway.py`; `rag doctor` |

### NFR2 Performance
| Req | Approach | Result | Proof |
|---|---|---|---|
| NFR2.1 under 800 ms | Retrieval needs no model call except one embedding; intent is a keyword rule | **Done: about 125 ms** | `bench/retrieval_bench.py`, `bench/resource_bench.py` |
| NFR2.2 under 30% | One polling engine, a worker thread for folders, no heavy work on the event loop; one small model that stays on the GPU | **Done, reading stated** | Background engine **0.05% of the machine**; Ollama **0.0% CPU idle, 8.3% while generating**; GPU **3.8% idle, 78% during a ~6 s answer**, about 11% time-averaged at one request a minute (`bench/inference_bench.py`). GPU is above 30% during each answer, by design |

### NFR3 Reliability
| Req | Approach | Result | Proof |
|---|---|---|---|
| NFR3.1 degradation | A circuit breaker pauses a tool that crashes 3 times; its step fails fast and only its dependents are blocked | **Done** | `recovery/breaker.py`; `tests/test_breaker.py`, `tests/test_planning.py` |
| NFR3.2 restore in 5 s | `restore_state` reads the vault back: running steps become interrupted, cut-off tool calls are closed and flagged, the last conversation returns. `/resume` asks before repeating a call that is not safe to repeat | **Done: p95 89 ms** | `bench/recovery_bench.py`: 50 random hard kills, 50 of 50 restored |

### NFR4 Control
| Req | Approach | Result | Proof |
|---|---|---|---|
| NFR4.1 explainability | `/log` and `rag log` show each tool call: arguments, the model's stated reason, the permission rule that decided, and the result; `/log plan` shows a plan's steps | **Done** | `runtime/explain.py` |
| NFR4.2 memory control | `/memories`, `/forget N`, `memory correct` (keeps history), `memory export` (refuses plaintext unless told) | **Done** | `persistent_memory.py`, `cli.py` |
| NFR4.3 permissions | Risk tiers R0 to R3, three modes (ask, edits, full), four roles (owner, editor, viewer, automation). **Sending mail asks in every mode.** | **Done** | `policy/` (D-051). Roles are a local switch, not separate user accounts |

**Score: 22 of the brief's 22 checklist items done and measured**, two with a stated reading or gap (the GPU is above 30% for the few seconds of each answer; the VS Code launch was not verified live). Detail in `docs/REQUIREMENTS.md`.

---

## 4. The design choices worth explaining

1. **Log before acting.** The "started" record of a tool call is saved before the tool runs. That one rule gives us recovery, explainability and exactly-once triggers from the same data.
2. **One permission function.** Every call, from chat, plan steps, triggers and skills, goes through `permissions.check`. There is no back door, and a role can only remove rights.
3. **Local mailbox instead of an email password.** Ragnarok never holds an account password. Gmail is driven through the user's own signed-in browser, and sending always asks.
4. **Rules, not the model, for suggestions.** They cost no GPU time and cannot be talked into anything.
5. **One chat model with fixed load options.** Switching models or context size makes Ollama reload, which looks like lag on a 6 GB card.
6. **Sandbox in layers, honestly.** The audit hook is defence in depth; the Job Object and cleaned environment are what Windows enforces.
7. **Measure everything the brief gives a number for.** Three benchmarks, each re-runnable.

---

## 5. Numbers to quote

| Target from the brief | Our measurement | Command |
|---|---|---|
| Retrieval + intent under 800 ms | about 125 ms (embedding p95 117 ms + search p95 6.7 ms + intent 0.013 ms) | `uv run python bench/resource_bench.py` |
| Background and inference under 30% | engine 0.05%; Ollama 0.0% CPU idle, 8.3% generating; GPU 3.8% idle, 78% during an answer, about 11% averaged | `uv run python bench/resource_bench.py` and `uv run python bench/inference_bench.py` |
| Restore within 5 s | restart p95 89 ms, max 223 ms, 50 of 50 | `uv run python bench/recovery_bench.py` |
| Tests | 1735 pass, 3 skipped, lint clean | `uv run pytest -q` and `uv run ruff check .` |
| Real-model tasks | mail, task and Gmail prompts pick the right tool and finish | `uv run python -m bench.daily_tasks "mail,task"` |
| Screen control (experimental) | 13 of 15 on a live test window | `bench/screen_eval.py` |

---

## 6. Live demo (about 6 minutes, all local)

Setup: Ollama running with `qwen3.5:4b` and `nomic-embed-text`; vault created with `rag init`.

1. **Privacy and encryption.** `uv run python -m ragnarok doctor`: Ollama local, model on GPU, vault file encrypted. Open `vault.db` in a hex viewer to show there is no readable text.
2. **Memory.** In `rag chat`: "I prefer meetings after 2 pm." Then `/memories` to show it was stored, and `rag memory export` to show the owner controls it.
3. **Skills and sandbox.** `/skill add text-stats` (tests run in the sandbox, approval card, tool appears). Then `uv run pytest tests/test_sandbox.py -q` to show the 12 hostile attempts being stopped.
4. **Planning and self-correction.** Ask for a multi-step goal (for example "find my notes on X, make slides, save them"). Show the plan card, approve it, show the steps finishing, then `/log plan`.
5. **Permissions and cross-app work.** "Open Gmail and send a mail to <address> saying <text>": the approval card appears, you approve, the browser opens, writes and sends it (verified live to the owner's own address; Gmail needs to be signed in, and the browser window must stay in front for the ~20 s it takes). Then "open hello.py in vs code".
6. **Triggers.** "Remind me in 2 minutes to ..." and leave the chat; show it fires from `rag daemon`. Drop a file in a watched folder for the Inbox event.
7. **Recovery.** Start a task, kill the process in Task Manager, restart: "Picked up our last conversation ... in N ms" and `/resume`. Then run `bench/recovery_bench.py --runs 10` for the numbers.
8. **Transparency.** `/log` for tool, reason, rule and result; `/role viewer` to show a risky tool being blocked.

Keep a recorded video of this as a backup.

---

## 7. Questions judges may ask

**Is anything sent to the cloud?** No model or embedding call leaves `127.0.0.1`. The only external traffic is what the user's own browser does when it opens a website or Gmail.

**How do you know the 5-second recovery is real?** We kill a working process with `TerminateProcess` 50 times at random moments and time the restart. The honest limit: it excludes typing the passphrase and Ollama warm-up.

**Is the sandbox really secure?** It is layered, not a boundary. Windows enforces the Job Object and the cleaned environment; the audit hook can in principle be bypassed by native code. We say so in the docs.

**What if the model is wrong?** Tool arguments are validated by schema, risky actions need approval, plan steps are verified by typed checks, and a crashing tool is paused. The tracker lists the cases where the 4B model still varies.

**Why a 4B model?** It is the largest that stays 100% on a 6 GB GPU with room for context; `bench/llm_bench.py` chose it.

**What is not finished?** See the next section.

---

## 8. Honest limits

- **No real IMAP/Gmail API.** Gmail works through the user's browser session; mail otherwise uses a local folder.
- **Gmail sending drives the screen.** It was verified live ("Message sent"), but it clicks the blue Send button it finds on screen, so it depends on Gmail's layout and on the browser window staying in front. Once it sent the mail and still reported that it could not confirm it.
- **The VS Code launch was not verified live:** VS Code would not start from the shell used for testing (a problem of that environment); the tool, its checks and the model's choice of it are tested.
- **No external project-management product** (only the vault task board).
- **GPU use is above 30% during each answer** (about 78% for ~6 s). Averaged over time it is about 11%; CPU never exceeds about 11%.
- **The daemon was not left running for hours** and was not started at a real Windows login. A hidden daemon needs the DPAPI unlock option.
- **Suggestions are rule-based** (four patterns, fixed wording).
- **Roles are a local switch** for one person, not separate accounts.
- **The "honest: alarm" check in `bench/daily_tasks.py` fails** (it also failed before this work): the model sets a reminder and notes it only works while the program runs.
- The owner has not yet used the newest features with a real, long-lived vault.

---

## 9. Where to look

| Pillar | Code | Tests and benches |
|---|---|---|
| Skills + sandbox | `ragnarok/skills/`, `ragnarok/sandbox/`, `skills/`, `fixtures/evil-skill` | `test_skills_registry`, `test_sandbox` |
| Planning | `ragnarok/planning/` | `test_planning`, `bench/plan_tasks.py` |
| Triggers | `ragnarok/triggers/` | `test_triggers`, `test_proactive_daemon` |
| Encryption | `ragnarok/memory/vault.py`, `ragnarok/vault/keys.py` | `test_m1_keys`, `test_m1_vault` |
| Recovery | `ragnarok/recovery/` | `test_recovery_restore`, `bench/recovery_bench.py` |
| Memory | `ragnarok/memory/` | `test_m2_*`, `test_m3_ingest` |
| Permissions + log | `ragnarok/policy/`, `ragnarok/runtime/explain.py` | `test_roles_and_log` |
| Interfaces | `ragnarok/gui/`, `ragnarok/chat.py`, `ragnarok/cli.py` | `test_gui_*`, `test_chat_*` |

Run: `uv run python -m ragnarok chat` (terminal), `uv run python -m ragnarok gui` (the pixel cat, or `meow` once set up), `uv run python -m ragnarok daemon` (background).
