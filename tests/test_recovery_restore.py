"""State recovery (NFR3.2): a stopped program's work and conversation come back, fast."""

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bench.recovery_bench import kill_and_restore, passed
from ragnarok.planning.models import Plan, Step
from ragnarok.planning.store import PlanStore
from ragnarok.recovery.restore import RESTORE_BUDGET_S, remember_session, restore_state
from tests.test_m2_memory import Clock, key, make  # noqa: F401  (fixture)


def world(tmp_path, key):  # noqa: F811
    vault, store, _svc, _emb = make(tmp_path, key, "exact")
    return vault, store, PlanStore(vault)


def call(tool: str, status: str = "started", **args) -> str:
    return json.dumps(
        {"tool": tool, "args": args, "reason": None, "rule": "default:R1", "status": status}
    )


def make_plan(plans: PlanStore) -> str:
    steps = [Step("s1", "find it", ["find_files"]), Step("s2", "move it", ["move_path"], ["s1"])]
    return plans.create(Plan("tidy up", steps))


def test_a_running_step_becomes_interrupted_and_the_plan_can_resume(tmp_path, key):  # noqa: F811
    vault, store, plans = world(tmp_path, key)
    plan_id = make_plan(plans)
    plans.set_state(plan_id, "s1", "done", output="found it")
    plans.set_state(plan_id, "s2", "running", attempts=1)

    report = restore_state(store, plans)

    assert (report.plans, report.steps) == (1, 1)
    rows = plans.steps(plan_id)
    assert rows["s1"].state == "done" and rows["s1"].output == "found it"
    assert rows["s2"].state == "interrupted"
    assert plans.get(plan_id).status == "interrupted"
    assert [p.id for p in plans.unfinished()] == [plan_id]
    vault.close()


def test_a_waiting_step_is_interrupted_too(tmp_path, key):  # noqa: F811
    vault, store, plans = world(tmp_path, key)
    plan_id = make_plan(plans)
    plans.set_state(plan_id, "s1", "waiting_approval")
    restore_state(store, plans)
    assert plans.steps(plan_id)["s1"].state == "interrupted"
    vault.close()


def test_a_cut_off_call_is_reported_once_and_ones_that_finished_are_not(tmp_path, key):  # noqa: F811
    vault, store, plans = world(tmp_path, key)
    kw = {"session_id": "a", "dedupe_seconds": None}
    store.record_event("chat", "memory_tool_call", call("find_files"), **kw)
    store.record_event("chat", "memory_tool_result", "find_files: 3 files", **kw)
    store.record_event("chat", "memory_tool_call", call("move_path", src="a", dst="b"), **kw)

    report = restore_state(store, plans, {"find_files": True, "move_path": False})

    assert [c.tool for c in report.in_flight] == ["move_path"]
    assert [c.tool for c in report.risky] == ["move_path"]
    assert report.in_flight[0].args == {"src": "a", "dst": "b"}
    assert restore_state(store, plans).in_flight == []  # closed: not reported again
    vault.close()


def test_a_cut_off_idempotent_call_is_safe_to_repeat(tmp_path, key):  # noqa: F811
    vault, store, plans = world(tmp_path, key)
    store.record_event(
        "chat", "memory_tool_call", call("find_files"), session_id="a", dedupe_seconds=None
    )
    report = restore_state(store, plans, {"find_files": True})
    assert report.in_flight and not report.risky
    vault.close()


def test_a_denied_call_is_not_a_cut_off_call(tmp_path, key):  # noqa: F811
    vault, store, plans = world(tmp_path, key)
    store.record_event(
        "chat",
        "memory_tool_call",
        call("delete_path", "denied"),
        session_id="a",
        dedupe_seconds=None,
    )
    assert restore_state(store, plans).in_flight == []
    vault.close()


def test_the_last_conversation_comes_back_as_working_memory(tmp_path, key):  # noqa: F811
    clock = Clock()
    vault, store, _svc, _emb = make(tmp_path, key, "exact", clock=clock)
    plans = PlanStore(vault)
    store.record_event("chat", "user_message", "my project is the hackathon agent", session_id="s1")
    store.record_event("chat", "assistant_message", "Got it, the hackathon agent.", session_id="s1")
    store.record_event("chat", "user_message", "unrelated, other session", session_id="s2")
    remember_session(store, "s1")

    report = restore_state(store, plans, now=clock.now + timedelta(minutes=5))

    assert report.history == [
        {"role": "user", "content": "my project is the hackathon agent"},
        {"role": "assistant", "content": "Got it, the hackathon agent."},
    ]
    vault.close()


def test_an_old_conversation_is_left_alone(tmp_path, key):  # noqa: F811
    clock = Clock()
    vault, store, _svc, _emb = make(tmp_path, key, "exact", clock=clock)
    store.record_event("chat", "user_message", "yesterday's talk", session_id="s1")
    remember_session(store, "s1")
    later = clock.now + timedelta(days=2)
    assert restore_state(store, PlanStore(vault), now=later).history == []
    vault.close()


def test_with_nothing_to_restore_it_is_instant_and_quiet(tmp_path, key):  # noqa: F811
    vault, store, plans = world(tmp_path, key)
    report = restore_state(store, plans, now=datetime.now(UTC))
    assert (report.plans, report.steps, report.in_flight, report.history) == (0, 0, [], [])
    assert report.within_budget and report.elapsed_ms < RESTORE_BUDGET_S * 1000
    vault.close()


def test_kill_the_process_for_real_then_restore(tmp_path, key):  # noqa: F811
    """The same thing the bench does, a few times: TerminateProcess in the middle of the work."""
    for delay in (0.0, 0.1, 0.25):
        run_dir = tmp_path / f"run{delay}"
        run_dir.mkdir()
        result = kill_and_restore(run_dir, key, delay)
        assert passed(result), result
        assert result["restart_ms"] < RESTORE_BUDGET_S * 1000


def test_the_bench_child_runs_standalone():
    assert Path("bench/recovery_child.py").exists()
    assert subprocess.run([sys.executable, "-c", "import bench.recovery_bench"]).returncode == 0
