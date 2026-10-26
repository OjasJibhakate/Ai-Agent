"""The process that gets killed in `recovery_bench.py`: it works on a plan until it is stopped.

Usage: python bench/recovery_child.py VAULT_PATH KEY_HEX
It prints READY once a step is running, then keeps finishing and starting steps and logging tool
calls (each logged BEFORE its work, as in the real loop) until the parent kills it.
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ragnarok.memory.store import MemoryStore
from ragnarok.memory.vault import Vault
from ragnarok.planning.models import Plan, Step
from ragnarok.planning.store import PlanStore


def main() -> None:
    path, key = sys.argv[1], bytes.fromhex(sys.argv[2])
    vault = Vault.open(path, key)
    store = MemoryStore(vault)
    store.start()
    plans = PlanStore(vault)
    steps = [
        Step(f"s{i}", f"step {i}", ["create_file"], [f"s{i - 1}"] if i else []) for i in range(6)
    ]
    plan_id = plans.create(Plan("write six files", steps))
    session = "child"
    store.meta_set("chat_session", session)
    store.record_event("chat", "user_message", "write six files", session_id=session)
    first = True
    for step in steps:
        plans.set_state(plan_id, step.id, "running", attempts=1)
        store.record_event(
            "chat",
            "memory_tool_call",
            json.dumps(
                {
                    "tool": "create_file",
                    "args": {"name": step.id},
                    "reason": None,
                    "rule": "default:R1",
                    "status": "started",
                }
            ),
            session_id=session,
            dedupe_seconds=None,
        )
        if first:
            print("READY", flush=True)
            first = False
        time.sleep(0.05)  # the "work": the parent kills the process somewhere in here
        store.record_event(
            "chat", "memory_tool_result", f"create_file: made {step.id}",
            session_id=session, dedupe_seconds=None,
        )  # fmt: skip
        plans.set_state(plan_id, step.id, "done", output=f"made {step.id}")
    time.sleep(60)


if __name__ == "__main__":
    main()
