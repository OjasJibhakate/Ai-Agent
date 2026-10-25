"""Multi-step goals on the real model, run as plans and checked by what ends up on disk.

Run: uv run python -m bench.plan_tasks [name-filter]
Each goal runs in a throwaway home with the chat's tools. Reports how long the model took to plan,
the plan it wrote, what each step did, and whether the files that should exist do. Results go to
bench/results/plan_tasks_<date>.json.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from bench.daily_tasks import (
    Bin,
    FakeBrowsers,
    FakeCatalog,
    World,
    build_home,
)
from ragnarok.memory.ingest import GatewayLlm, Ingestor
from ragnarok.memory.service import MemoryService
from ragnarok.memory.store import MemoryStore
from ragnarok.memory.vault import Vault
from ragnarok.planning.runner import PlanEvent, PlanRunner, default_file_probe
from ragnarok.planning.store import PlanStore
from ragnarok.policy.modes import Mode
from ragnarok.runtime.gateway import Gateway
from ragnarok.runtime.loop import Agent
from ragnarok.runtime.tools import Tool, memory_tools
from ragnarok.skills.apps import app_tools
from ragnarok.skills.digest import digest_tools
from ragnarok.skills.documents import document_tools
from ragnarok.skills.fileops import file_op_tools
from ragnarok.skills.files import file_tools
from ragnarok.skills.shell import shell_tools
from ragnarok.skills.web import web_tools
from ragnarok.skills.webpage import webpage_tools


@dataclass
class Goal:
    name: str
    text: str
    check: object  # callable(home) -> str | None
    flaky: str | None = None  # a tool that times out on its first call


def made(home: Path, pattern: str) -> list[Path]:
    return sorted((home / "Documents" / "Ragnarok").glob(pattern))


FIXTURES = {"notes.txt", "old.txt", "backprop_notes.txt", "cnn_cheatsheet.md", "resume.docx"}


def new_notes(home: Path) -> list[Path]:
    """Text, Word or Markdown files the agent made anywhere (not the test input)."""
    found = []
    for p in home.rglob("*"):
        if p.suffix in (".txt", ".docx", ".md") and p.name not in FIXTURES:
            if "image processing" not in p.parts and "Deep_learning" not in p.parts:
                found.append(p)
    return found


def check_deck_and_note(home: Path) -> str | None:
    if not made(home, "*.pptx"):
        return "no deck was made"
    return None if new_notes(home) else "no note was made"


def check_folder_copy_note(home: Path) -> str | None:
    if not list((home / "Desktop" / "Reports").glob("report.pdf")):
        return "report.pdf is not in Desktop/Reports"
    return None if new_notes(home) else "no note"


GOALS = [
    Goal(
        "deck and note",
        "Look at the files in my image processing folder on the desktop, make a 6 slide "
        "presentation from them, and write a short note listing the file names",
        check_deck_and_note,
    ),
    Goal(
        "folder, copy, note",
        "Make a folder called Reports on my desktop, copy report.pdf from my downloads into it, "
        "and then write a note saying what is in the Reports folder",
        check_folder_copy_note,
    ),
    Goal(
        "deck with a flaky tool",
        "Make a 6 slide presentation from my image processing folder on the desktop and write a "
        "note listing the file names",
        check_deck_and_note,
        flaky="summarize_files",
    ),
]


def make_flaky(tool: Tool) -> Tool:
    real = tool.handler
    state = {"first": True}

    async def handler(args):
        if state["first"]:
            state["first"] = False
            return "error: timeout"
        return await real(args)

    return Tool(
        tool.name, tool.description, tool.args_model, tool.risk, tool.idempotent, tool.timeout_s,
        tool.sandbox, handler, tool.fs, tool.network, tool.approval_note,
    )  # fmt: skip


async def run_goal(gw: Gateway, goal: Goal) -> dict:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        home = build_home(root)
        vault = Vault.open(root / "v.db", os.urandom(32))
        store = MemoryStore(vault)
        store.start()
        svc = MemoryService(store, gw)
        ingestor = Ingestor(store, gw, GatewayLlm(gw))
        world = World(home, FakeCatalog(), FakeBrowsers(), Bin())
        mode = Mode("ask")
        roots = [home / "Desktop", home / "Documents", home / "Downloads"]
        tools = (
            memory_tools(svc, ingestor)
            + app_tools(world.catalog)
            + file_tools(roots, home, mode)
            + file_op_tools(roots, home, mode, world.bin, data_dirs=[root / "vault"])
            + shell_tools(home)
            + web_tools(world.browsers)
            + document_tools(home / "Documents")
            + digest_tools(gw, roots, home, mode, documents=home / "Documents", data_dirs=[root / "vault"])
            + webpage_tools(home / "Documents", opener=world.opened.append)
        )  # fmt: skip
        if goal.flaky:
            tools = [make_flaky(t) if t.name == goal.flaky else t for t in tools]
        by_name = {t.name: t for t in tools}
        events: list[str] = []

        async def approve(name, args, decision) -> bool:
            events.append(f"  approved {name}")
            return True

        async def emit(event: PlanEvent) -> None:
            events.append(
                f"  {event.kind:8s} {event.step_id or '-':3s} {event.state:16s} {event.text[:70]}"
            )

        def make_agent(subset, ask):
            return Agent(store, svc, ingestor, gw, profile=mode.profile, approve=ask, tools=subset)

        plans = PlanStore(vault)
        runner = PlanRunner(
            plans, gw, by_name, make_agent, approve, emit,
            file_probe=default_file_probe([home / "Documents" / "Ragnarok", *roots]),
        )  # fmt: skip
        started = time.perf_counter()
        info: dict = {"goal": goal.name}
        try:
            t0 = time.perf_counter()
            plan, _risks = await runner.propose(goal.text)
            info["plan_seconds"] = round(time.perf_counter() - t0, 1)
            info["plan"] = [(s.id, s.title, s.uses, s.after, s.done_when) for s in plan.steps]
            plan_id = plans.create(plan)
            result = await runner.run(plan_id)
            info.update(status=result.status, done=result.done, failed=result.failed,
                        blocked=result.blocked)  # fmt: skip
            why = (
                goal.check(home)
                if result.status in ("done", "partial")
                else f"plan {result.status}"
            )
        except Exception as exc:
            why = f"crashed: {type(exc).__name__}: {exc}"
        finally:
            vault.close()
        info.update(ok=why is None, why=why, seconds=round(time.perf_counter() - started, 1))
        info["events"] = events
        return info


async def main(pattern: str = "") -> None:
    gw = Gateway(timeout=300)
    rows = []
    try:
        await gw.warm_up()
        for goal in GOALS:
            if pattern and pattern.lower() not in goal.name.lower():
                continue
            row = await run_goal(gw, goal)
            rows.append(row)
            print(f"{'PASS' if row['ok'] else 'FAIL'} {row['goal']:26s} {row['seconds']:5.1f}s "
                  f"plan {row.get('plan_seconds', '-')}s status={row.get('status')}", flush=True)  # fmt: skip
            for step in row.get("plan", []):
                print(
                    f"   plan: {step[0]} {step[1][:60]} uses={step[2]} after={step[3]} {step[4][:30]}"
                )
            for line in row["events"]:
                print(line)
            if not row["ok"]:
                print(f"   why: {row['why']}", flush=True)
    finally:
        await gw.aclose()
    print(f"\n{sum(r['ok'] for r in rows)}/{len(rows)} plans done")
    out = Path("bench/results") / f"plan_tasks_{datetime.now(UTC):%Y-%m-%d}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else ""))
