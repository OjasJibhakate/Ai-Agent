"""Role-based access control (NFR4.3) and the explanation log (NFR4.1)."""

import json

import pytest

from ragnarok.planning.models import Plan, Step
from ragnarok.planning.store import PlanStore
from ragnarok.policy import permissions
from ragnarok.policy.permissions import Risk
from ragnarok.policy.roles import DEFAULT_ROLE, ROLES, get, rank
from ragnarok.runtime.explain import latest_plan_id, plan_steps, recent_tool_calls
from tests.test_m2_memory import key, make  # noqa: F401  (fixture)


def test_the_default_role_changes_nothing():
    assert DEFAULT_ROLE == "owner"
    for profile in ("interactive", "edits", "full"):
        for risk in Risk:
            assert permissions.check(profile, "some_tool", risk) == permissions.check(
                profile, "some_tool", risk, "owner"
            )


@pytest.mark.parametrize(
    ("role", "risk", "allowed"),
    [
        ("viewer", Risk.R0, True),
        ("viewer", Risk.R1, False),
        ("viewer", Risk.R3, False),
        ("editor", Risk.R1, True),
        ("editor", Risk.R2, True),
        ("editor", Risk.R3, False),
        ("automation", Risk.R0, True),
        ("automation", Risk.R2, False),
    ],
)
def test_a_role_limits_the_risk_of_tools(role, risk, allowed):
    decision = permissions.check("full", "some_tool", risk, role)  # "full" never asks
    assert decision.allowed is allowed
    if not allowed:
        assert decision.rule == f"role:{role}" and not decision.ask


def test_a_role_can_only_take_rights_away():
    # the background profile asks for everything; a role cannot turn that into an allow
    assert permissions.check("background", "move_path", Risk.R2, "editor").ask
    assert not permissions.check("interactive", "x", Risk.R3, "viewer").allowed


def test_named_tools_are_denied_or_allowed_by_role():
    assert not permissions.check("full", "run_command", Risk.R1, "editor").allowed
    assert permissions.check("full", "set_reminder", Risk.R1, "automation").allowed
    assert not permissions.check("full", "send_mail", Risk.R2, "automation").allowed


def test_an_unknown_role_is_refused():
    assert permissions.check("full", "x", Risk.R0, "root").rule == "unknown-role"
    with pytest.raises(ValueError):
        get("root")


def test_rank_orders_the_roles():
    order = sorted(ROLES, key=rank)
    assert order[0] in ("viewer", "automation") and order[-1] == "owner"
    assert rank("viewer") < rank("editor") < rank("owner")


def call_event(tool, status="started", reason=None, **args) -> str:
    return json.dumps(
        {"tool": tool, "args": args, "reason": reason, "rule": "default:R1", "status": status}
    )


def test_the_log_shows_each_tool_its_reason_rule_and_result(tmp_path, key):  # noqa: F811
    vault, store, _svc, _emb = make(tmp_path, key, "exact")
    kw = {"session_id": "a", "dedupe_seconds": None}
    store.record_event("chat", "memory_tool_call", call_event("find_files", reason="look"), **kw)
    store.record_event("chat", "memory_tool_result", "find_files: 2 files", **kw)
    store.record_event("chat", "memory_tool_call", call_event("delete_path", "denied"), **kw)
    store.record_event("chat", "memory_tool_call", call_event("move_path", a="x"), **kw)

    calls = recent_tool_calls(store, 10)

    assert [c.tool for c in calls] == ["find_files", "delete_path", "move_path"]
    assert calls[0].reason == "look" and calls[0].result == "2 files"
    assert calls[1].status == "denied" and calls[1].result == ""
    assert calls[2].result == "" and calls[2].args == {"a": "x"}  # still running, or cut off
    assert len(recent_tool_calls(store, 2)) == 2
    vault.close()


def test_the_log_of_a_plan_lists_its_steps(tmp_path, key):  # noqa: F811
    vault, _store, _svc, _emb = make(tmp_path, key, "exact")
    plans = PlanStore(vault)
    assert latest_plan_id(plans) is None
    plan_id = plans.create(Plan("tidy", [Step("s1", "find", ["find_files"])]))
    plans.set_state(plan_id, "s1", "running", attempts=1)
    plans.set_state(plan_id, "s1", "done", output="ok")
    assert latest_plan_id(plans) == plan_id
    lines = plan_steps(plans, plan_id)
    assert any("created" in line for line in lines)
    assert any("s1" in line and "done" in line for line in lines)
    vault.close()
