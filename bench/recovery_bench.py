"""Kill and resume (NFR3.2): stop a working program with no warning, restart, time the restore.

    uv run python bench/recovery_bench.py            # 50 kills
    uv run python bench/recovery_bench.py --runs 10

Each run starts `recovery_child.py` (a program working through a six-step plan and logging tool
calls), waits until a step is running, kills it the hard way (TerminateProcess: no cleanup, like a
power cut for the process), then reopens the vault and calls `restore_state`. A run passes when:

- the vault opens and passes its integrity check,
- no step is left `running` and the plan is `interrupted`,
- a tool call that was cut off is found, and closed so it is not reported twice,
- the working memory (the user's message) comes back,
- and the whole restart (open the vault + restore) took under 5 seconds.

Results go to bench/results/recovery_<date>.json.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ragnarok.memory.store import MemoryStore  # noqa: E402
from ragnarok.memory.vault import Vault  # noqa: E402
from ragnarok.planning.store import PlanStore  # noqa: E402
from ragnarok.recovery.restore import RESTORE_BUDGET_S, restore_state  # noqa: E402

CHILD = ROOT / "bench" / "recovery_child.py"


def kill_and_restore(workdir: Path, key: bytes, delay: float) -> dict:
    """One run. `delay` is how long the child works after its first step starts."""
    path = workdir / "vault.db"
    proc = subprocess.Popen(
        [sys.executable, str(CHILD), str(path), key.hex()],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        cwd=ROOT,
    )
    try:
        assert proc.stdout is not None
        if proc.stdout.readline().strip() != "READY":
            raise RuntimeError("the child did not start")
        time.sleep(delay)
    finally:
        proc.kill()  # TerminateProcess on Windows: nothing in the child gets to clean up
        proc.wait()

    started = time.perf_counter()
    vault = Vault.open(path, key)  # runs the integrity check too
    try:
        store = MemoryStore(vault)
        store.start()
        plans = PlanStore(vault)
        report = restore_state(store, plans, {"create_file": False})
        restart_ms = (time.perf_counter() - started) * 1000
        rows = [p for p in plans.unfinished()]
        states = [s.state for p in rows for s in plans.steps(p.id).values()]
        again = restore_state(store, plans, {"create_file": False})
        return {
            "restart_ms": round(restart_ms, 1),
            "restore_ms": round(report.elapsed_ms, 1),
            "no_running_steps": "running" not in states,
            "plan_interrupted": bool(rows) and all(p.status == "interrupted" for p in rows),
            "history_back": any(m["content"] == "write six files" for m in report.history),
            "in_flight_found": len(report.in_flight),
            "reported_twice": len(again.in_flight),
            "within_5s": restart_ms <= RESTORE_BUDGET_S * 1000,
        }
    finally:
        vault.close()


def passed(run: dict) -> bool:
    return (
        run["within_5s"]
        and run["no_running_steps"]
        and run["plan_interrupted"]
        and run["history_back"]
        and run["reported_twice"] == 0
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=50)
    args = parser.parse_args()
    key = os.urandom(32)
    rng = random.Random(7)
    runs = []
    for _ in range(args.runs):
        with tempfile.TemporaryDirectory() as tmp:
            runs.append(kill_and_restore(Path(tmp), key, delay=rng.uniform(0.0, 0.4)))
    times = sorted(r["restart_ms"] for r in runs)
    summary = {
        "date": date.today().isoformat(),
        "runs": len(runs),
        "passed": sum(passed(r) for r in runs),
        "cut_off_calls_found": sum(1 for r in runs if r["in_flight_found"]),
        "restart_ms": {
            "p50": statistics.median(times),
            "p95": times[int(0.95 * (len(times) - 1))],
            "max": times[-1],
        },
        "budget_ms": RESTORE_BUDGET_S * 1000,
    }
    out = ROOT / "bench" / "results" / f"recovery_{summary['date']}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "runs": runs}, indent=1), encoding="utf-8")
    print(json.dumps(summary, indent=1))
    return 0 if summary["passed"] == summary["runs"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
