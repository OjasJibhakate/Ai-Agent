"""End-to-end test of the screen skill with the real model, real screen capture and real mouse/keyboard.

Run: uv run python -m bench.screen_eval [trials]

Safety: every trial opens a small test window (bench/screen_harness_app.py). Only that window's area is
captured, clicks can only land inside it, and typing is refused unless it has the focus, so nothing
else on the desktop is read or touched. The window stays open for the length of one task.
Results go to bench/results/screen_eval_<date>.json.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from ragnarok.runtime.gateway import Gateway
from ragnarok.skills.screen.capture import WindowsScreen
from ragnarok.skills.screen.input import WindowsInput
from ragnarok.skills.screen.task import GatewayVision, ScreenTask
from ragnarok.skills.screen.winapi import Windows

REPO = Path(__file__).resolve().parent.parent
Event = tuple[str, str]

SCENARIOS: list[tuple[str, str, Callable[[list[Event]], bool]]] = [
    ("buttons", "Click the Export button.", lambda ev: ev == [("CLICK", "Export")]),
    (
        "search",
        "Type lofi beats into the search box and press Enter.",
        lambda ev: ("SEARCH", "lofi beats") in ev,
    ),
    (
        "form",
        "Fill in the form: name Priya, email priya@example.com, tick Subscribe, then click Submit.",
        lambda ev: ("SUBMIT", "Priya priya@example.com 1") in ev,
    ),
    ("list", "Double-click the file named budget.xlsx.", lambda ev: ("OPEN", "budget.xlsx") in ev),
    (
        "scroll",
        "Find zebra.txt in the list (scroll down if needed) and click it.",
        lambda ev: ("PICK", "zebra.txt") in ev,
    ),
]


class OwnWindowOnly(Windows):
    """Never treat the user's terminal as ours: the test must not move any real window."""

    def is_own_terminal(self, window) -> bool:
        return False


async def yes(description: str) -> bool:
    return True  # the test app has nothing real to delete or send


async def one_trial(gw: Gateway, scenario: str, task: str, check, seed: int) -> dict:
    proc = subprocess.Popen(
        [sys.executable, "-m", "bench.screen_harness_app", scenario, str(seed)],
        stdout=subprocess.PIPE, text=True, cwd=REPO,
    )  # fmt: skip
    events: list[Event] = []
    ready = threading.Event()
    box: dict = {}

    def reader() -> None:
        for line in proc.stdout:  # type: ignore[union-attr]
            kind, _, rest = line.strip().partition(" ")
            if kind == "READY":
                hwnd, left, top, w, h = (int(t) for t in rest.split())
                box.update(hwnd=hwnd, rect=(left, top, w, h))
                ready.set()
            else:
                events.append((kind, rest))

    threading.Thread(target=reader, daemon=True).start()
    if not ready.wait(15):
        proc.kill()
        return {"scenario": scenario, "ok": False, "summary": "the test window did not open"}
    steps_seen: list[str] = []

    async def progress(text: str) -> None:
        steps_seen.append(text)

    loop = ScreenTask(
        WindowsScreen(region=box["rect"]),
        WindowsInput(),
        OwnWindowOnly(),
        GatewayVision(gw),
        confirm=yes,
        lock_hwnd=box["hwnd"],
        progress=progress,
        max_steps=10,
        max_seconds=90,
        settle=0.5,
    )
    started = time.perf_counter()
    try:
        result = await loop.run(task)
    finally:
        await asyncio.sleep(0.3)
        proc.terminate()
    return {
        "scenario": scenario, "ok": bool(check(events)), "claimed_done": result.ok,
        "steps": result.steps, "seconds": round(time.perf_counter() - started, 1),
        "summary": result.summary, "events": events, "actions": steps_seen,
    }  # fmt: skip


async def main(trials: int) -> None:
    gw = Gateway(timeout=300)
    rows: list[dict] = []
    try:
        await gw.warm_up()
        for scenario, task, check in SCENARIOS:
            for t in range(trials):
                row = await one_trial(gw, scenario, task, check, seed=t)
                rows.append(row)
                mark = "PASS" if row["ok"] else "FAIL"
                print(
                    f"{mark} {scenario:8s} trial {t + 1}: {row.get('steps')} steps, "
                    f"{row.get('seconds')}s | {row['summary']}",
                    flush=True,
                )
                if not row["ok"]:
                    print(
                        "      actions:",
                        row.get("actions"),
                        "| events:",
                        row.get("events"),
                        flush=True,
                    )
    finally:
        await gw.aclose()
    print()
    for scenario, _, _ in SCENARIOS:
        mine = [r for r in rows if r["scenario"] == scenario]
        ok = sum(r["ok"] for r in mine)
        avg = sum(r.get("seconds", 0) for r in mine) / max(1, len(mine))
        print(f"{scenario:8s} {ok}/{len(mine)} succeeded, {avg:.0f}s average")
    print(f"overall {sum(r['ok'] for r in rows)}/{len(rows)}")
    # the worse failure: telling the user it worked when the app never received the right input
    false_claims = sum(1 for r in rows if r.get("claimed_done") and not r["ok"])
    print(f"claimed success but the check failed: {false_claims}/{len(rows)}")
    out = Path("bench/results") / f"screen_eval_{datetime.now(UTC):%Y-%m-%d}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 2))
