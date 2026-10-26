# Requirements tracker (the PDF's 22-item checklist)

Honest status as of 2026-10-26. **Works** = built and covered by a test or a measurement you can re-run. **Partly** = the core works, a named piece does not. **Unproven** = built or plausible, but no measurement or live check backs the claim.

Re-run everything: `uv run pytest -q` (1631 pass, 3 skipped), `uv run ruff check .`, and the benches named below.

## Persistent memory (FR1, NFR4.2, NFR1.2)

| # | Checklist item | Status | Evidence | What is not proven |
|---|---|---|---|---|
| 1 | Chronological history of conversations and tasks | Works | Every message and tool call is an event in the vault (`events` table, `memory/store.py`); `tests/test_m2_*`, `/log` | Events expire after 30 days by design; older history survives only as memories |
| 2 | Store facts and preferences | Works | `memory/ingest.py`; `bench/ingest_eval.py` | Extraction quality depends on the 4B model |
| 3 | Consolidate and prune old memories | Works | `memory/consolidate.py`, `memory/forget.py`; `bench/consolidate_eval.py`, `rag memory consolidate/prune` | |
| 4 | View, edit, export, delete memories | Works | `/memories`, `/forget N`, `rag memory correct/export/forget` (export refuses plaintext unless `--yes-plaintext`) | "Edit" makes a new version and keeps the old one in history, by design |
| 5 | Encrypt stored memory | Works | SQLCipher vault, passphrase + recovery code, optional DPAPI (`vault/keys.py`); tests check no plaintext is in the file | Anyone signed in to Windows can open a vault that has DPAPI unlock turned on (opt-in, shown to the user) |

## AI and skills (FR2, FR3, NFR1.3)

| # | Checklist item | Status | Evidence | What is not proven |
|---|---|---|---|---|
| 6 | All reasoning and embeddings local via Ollama | Works | Single gateway to `127.0.0.1:11434` (`runtime/gateway.py`); `rag doctor` | |
| 7 | Register reusable scripts and tools | Works | `/skill add` tests the skill in the sandbox, asks approval, pins a hash (`skills/registry.py`; D-047); `tests/test_skills_registry.py` | Only Python/command skills; no API-wrapper skill shipped |
| 8 | Select tools from the request | Works | `bench/daily_tasks.py` on the real model; latest filtered mail/task runs 4 of 4 | The full battery has known model-variance failures (e.g. "honest: alarm" fails, also before this work) |
| 9 | Run skills in a sandbox | Works | Job Object + scrubbed environment + audit hook (`sandbox/`); `fixtures/evil-skill` and 12 tests stop each attack | The audit hook is defence in depth, not a boundary; native code can get round it |
| 10 | Break requests into multi-step plans | Works | `planning/`; `bench/plan_tasks.py` 3/3 on the latest run (2/3 on another) | Small sample, noisy model |
| 11 | Verify results and recover from tool failures | Works | Typed checks, retry, re-plan (`planning/runner.py`, `tests/test_planning.py`) | |

## Automation (FR3.2, FR4)

| # | Checklist item | Status | Evidence | What is not proven |
|---|---|---|---|---|
| 12 | Integrate with approved applications | **Partly** | Files, Office documents, app launcher, shell, web pages, local mailbox folder, Gmail in the browser (`compose_email`, `send_gmail`), task board | No real IMAP/Gmail API, no IDE or external project-management integration. `send_gmail` found the compose window in the real Edge but a **real send was never tested** |
| 13 | Monitor user-configured background triggers | Works | Date, cron, folder and mailbox-folder triggers; `rag daemon`; `tests/test_triggers.py`, `tests/test_proactive_daemon.py` | The daemon was never left running for hours or started at a real login |
| 14 | Proactively suggest useful actions | Works | `triggers/proactive.py`, `/suggest` (D-049) | Rule-based, four patterns only; wording is fixed text, not model-written |

## Safety and reliability (NFR1, NFR3, NFR4)

| # | Checklist item | Status | Evidence | What is not proven |
|---|---|---|---|---|
| 15 | Keep raw data and logs local | Works | Data in `%LOCALAPPDATA%\Ragnarok`, refused inside OneDrive (`cli.home_dir`); no cloud client in the code | The browser tools (Gmail, `open_website`) act through your own browser, which of course talks to those sites |
| 16 | Ask permission for high-risk actions | Works | Profiles + risk tiers (`policy/permissions.py`); sending mail asks in every mode; roles owner/editor/viewer/automation (`policy/roles.py`, D-051) | Roles are a local switch for one person, not separate accounts |
| 17 | Show execution logs and explanations | Works | `/log`, `/log plan`, `rag log`: tool, reason, rule, result (`runtime/explain.py`) | The "reason" is the model's own words, so it can be thin |
| 18 | Recover incomplete work after a restart | Works | `recovery/restore.py`; `/resume` asks before repeating a cut-off, non-idempotent call | A reboot with a passphrase-only vault waits for the person to type it |
| 19 | Continue independent tasks when a tool fails | Works | Shared circuit breaker (`recovery/breaker.py`); a failed step blocks only its dependents; `tests/test_breaker.py`, `tests/test_planning.py` | |

## Performance (NFR2, NFR3.2): numbers you can re-run

| # | Checklist item | Status | Measurement | What is not proven |
|---|---|---|---|---|
| 20 | Retrieval + intent classification under 800 ms | Works | `bench/resource_bench.py`: intent p95 0.013 ms; memory search p95 6.7 ms over 500 memories. Real query embedding p95 117 ms (`bench/results/retrieval_2026-10-25.json`). Total about 125 ms | The intent check is a keyword rule, not a model; the search figure used a stand-in embedder, so the embedding latency is added from the earlier real run |
| 21 | Background CPU/GPU against the 30% target | **Partly** | `bench/resource_bench.py`: the always-on engine (3 folders x 300 files, 8 triggers, suggestions) used **0.05% of the 12-thread machine**, peak 50 MB | **Local inference was not measured against 30%.** While the model generates, the GPU is busy by design; only that it fits fully on the GPU (`ollama ps`, `rag doctor`) is checked |
| 22 | Recovery against the 5-second target | Works | `bench/recovery_bench.py`: 50 kills with TerminateProcess, 50 of 50 restored, restart p95 89 ms, max 223 ms | Measured with a raw key; excludes typing a passphrase and Ollama warm-up |

## Summary

- **Works, backed by a test or a measurement:** 20 of 22.
- **Partly:** 2. Item 12 (integrations: no real IMAP or IDE/project-management tool, and a real Gmail send was never tried) and item 21 (the background engine is measured at 0.05%, but model inference is not measured against the 30% target).
- **Nothing here is claimed without a test or measurement behind it.** Still to do: IMAP, IDE and project-management integration, measuring inference against the 30% target, a real Gmail send, a long soak of the daemon, and use with the owner's real vault.
