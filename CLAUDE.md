@AGENTS.md

## Claude Code notes

- Before saying a task is done, run `uv run pytest -q` and `uv run ruff check .`.
- Ask before adding a dependency, changing the permission profile defaults in `ragnarok/policy/permissions.py`, or touching vault key handling.
- Record decisions in `docs/DECISIONS.md` and progress in `docs/WORKLOG.md` as part of the same change.
- Prefer working on the five pillars (skills, planning, triggers, encryption, recovery) over polish.
