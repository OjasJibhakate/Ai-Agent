"""The chat: restore after a crash (NFR3.2), /role (NFR4.3), /log (NFR4.1), /suggest (FR4.2)."""

import json
import os
from datetime import UTC, datetime

import pytest

from ragnarok.planning.models import Plan, Step
from ragnarok.recovery.restore import remember_session
from tests.test_chat_flow import build_app, shown, with_text
from tests.test_m4_agent import say


@pytest.fixture()
def vault_key() -> bytes:
    return os.urandom(32)


def tool_call(tool, status="started", **args) -> str:
    return json.dumps(
        {"tool": tool, "args": args, "reason": "test", "rule": "default:R2", "status": status}
    )


async def test_the_chat_picks_up_the_last_conversation_after_a_crash(tmp_path, vault_key):
    app, rt, out, _ = build_app(tmp_path, vault_key, [], ["/quit"])
    rt.store.clock = lambda: datetime.now(UTC)  # the fake test clock is weeks old
    rt.store.record_event(
        "chat", "user_message", "I am building the Ragnarok demo", session_id="old"
    )
    rt.store.record_event("chat", "assistant_message", "Nice, the Ragnarok demo.", session_id="old")
    remember_session(rt.store, "old")

    await app.run()

    assert "Picked up our last conversation (2 messages)" in shown(out)
    assert [m["role"] for m in app.session.history] == ["user", "assistant"]
    assert rt.store.meta_get("chat_session") == app.session.id  # the new session is the live one
    rt.vault.close()


async def test_resume_asks_before_repeating_a_call_that_was_cut_off(tmp_path, vault_key):
    app, rt, out, _ = build_app(tmp_path, vault_key, [], ["/resume", "/quit"], answers=("n",))
    app.agent.tools["risky"].idempotent = False  # repeating it could repeat its effect
    plan_id = app.plans.create(Plan("move the files", [Step("s1", "move", ["risky"])]))
    app.plans.set_state(plan_id, "s1", "running", attempts=1)
    rt.store.record_event(
        "chat",
        "memory_tool_call",
        tool_call("risky", command="dir"),
        session_id="old",
        dedupe_seconds=None,
    )

    await app.run()
    text = shown(out)

    assert "risky was running when the program stopped" in text
    assert "A plan did not finish: move the files" in text
    assert "Permission needed" in text and "doing it again may repeat" in text
    assert "I left that plan where it was" in text
    assert app.plans.steps(plan_id)["s1"].state == "interrupted"  # nothing was re-run
    rt.vault.close()


async def test_role_viewer_blocks_a_risky_tool_and_going_back_up_asks(tmp_path, vault_key):
    app, rt, out, seen = build_app(
        tmp_path,
        vault_key,
        [with_text("Trying.", "risky", command="dir"), say("It was blocked.")],
        ["/role viewer", "run it", "/role owner", "/role", "/quit"],
        answers=("y",),  # the one question: really go back to owner?
    )
    await app.run()
    text = shown(out)

    assert "Role is now viewer" in text
    assert seen == []  # the tool never ran
    assert "gives me more rights" in text and "Role is now owner" in text
    assert app.role == "owner" and rt.store.meta_get("role") == "owner"
    rt.vault.close()


async def test_a_refused_role_change_keeps_the_role(tmp_path, vault_key):
    app, rt, out, _ = build_app(
        tmp_path, vault_key, [], ["/role viewer", "/role editor", "/role boss", "/quit"],
        answers=("n",),
    )  # fmt: skip
    await app.run()
    text = shown(out)
    assert "the role stays as it is" in text and "role must be one of" in text
    assert app.role == "viewer"
    rt.vault.close()


async def test_the_role_is_remembered_for_the_next_start(tmp_path, vault_key):
    app, rt, out, _ = build_app(tmp_path, vault_key, [], ["/role viewer", "/quit"])
    await app.run()
    rt.vault.close()
    app2, rt2, _, _ = build_app(tmp_path, vault_key, [], ["/quit"])  # same vault, next start
    assert app2.role == "viewer"
    rt2.vault.close()


async def test_log_lists_tools_with_their_rule_and_plan_steps(tmp_path, vault_key):
    app, rt, out, _ = build_app(
        tmp_path,
        vault_key,
        [with_text("Trying.", "risky", command="dir"), say("Done.")],
        ["do it", "/log", "/plan x", "/log plan", "/quit"],
    )
    plan_id = app.plans.create(Plan("goal", [Step("s1", "find", ["risky"])]))
    app.plans.set_state(plan_id, "s1", "done", output="ok")
    await app.run()
    text = shown(out)
    assert "risky(command='dir'" in text and "rule default:R2" in text
    assert "created" in text and "s1" in text  # the plan's own history
    rt.vault.close()


async def test_suggest_shows_ideas_from_memory(tmp_path, vault_key):
    app, rt, out, _ = build_app(tmp_path, vault_key, [], ["/suggest", "/suggest", "/quit"])
    await rt.svc.remember("Every morning I want a digest of my downloads", "preference", "chat")
    await app.run()
    text = shown(out)
    assert "Suggestion" in text and "on a schedule" in text
    rt.vault.close()


async def test_help_lists_the_new_commands(tmp_path, vault_key):
    app, rt, out, _ = build_app(tmp_path, vault_key, [], ["/help", "/quit"])
    await app.run()
    text = shown(out)
    assert "/role R" in text and "/log" in text and "/suggest" in text
    rt.vault.close()
