"""The chat while tools run: the permission card, the spinner between steps, step limits."""

import os
import re
from types import SimpleNamespace

import pytest

from ragnarok.chat import ChatApp
from ragnarok.memory.ingest import Ingestor
from ragnarok.policy.permissions import Risk
from ragnarok.runtime.gateway import ChatResult
from ragnarok.runtime.loop import MAX_STEPS, Agent, Session
from ragnarok.runtime.tools import Tool, ToolArgs, memory_tools
from ragnarok.ui import Console
from tests.test_m2_memory import make
from tests.test_m3_ingest import ScriptedLlm
from tests.test_m4_agent import ScriptedChat, say
from tests.test_m4_cli import FakeGateway
from tests.test_m4_ui import Stream

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


class RiskyArgs(ToolArgs):
    command: str
    timeout_seconds: int = 60


def with_text(text: str, name: str, **args) -> ChatResult:
    """A model reply that says something and then calls a tool, as the real model does."""
    return ChatResult(content=text, tool_calls=[{"function": {"name": name, "arguments": args}}])


@pytest.fixture()
def vault_key() -> bytes:
    return os.urandom(32)


def build_app(tmp_path, vault_key, replies, lines, answers=("y",), *, tools=None, tty=False):
    vault, store, svc, emb = make(tmp_path, vault_key, "exact")
    ing = Ingestor(store, emb, ScriptedLlm({}), sim_floor=0.4)
    rt = SimpleNamespace(
        vault=vault, store=store, svc=svc, ingestor=ing, gateway=FakeGateway(chat_replies=replies)
    )
    out = Stream(tty=tty)
    typed, answered = iter(lines), iter(answers)
    seen: list[bool] = []
    app: ChatApp

    async def risky(args: RiskyArgs) -> str:
        seen.append(bool(app._spinner and app._spinner.active))  # was the "working" spinner up?
        return "done"

    risky_tool = Tool(
        name="risky",
        description="Does something that needs approval.",
        args_model=RiskyArgs,
        risk=Risk.R2,
        idempotent=True,
        timeout_s=5,
        sandbox="workspace-write",
        handler=risky,
    )
    app = ChatApp(
        rt,
        Console(out, color=False),
        folder="C:\\data",
        read_line=lambda prompt: next(typed),
        approve_line=lambda prompt: next(answered),
        tools=tools or ([*memory_tools(svc, ing), risky_tool]),
        learn_delay=60.0,
    )
    return app, rt, out, seen


def shown(out) -> str:
    return ANSI.sub("", out.getvalue())


async def test_the_permission_card_starts_on_its_own_line_after_streamed_text(tmp_path, vault_key):
    app, rt, out, seen = build_app(
        tmp_path,
        vault_key,
        [
            with_text("Let me try that:", "risky", command="dir", timeout_seconds=60),
            say("All done."),
        ],
        ["do it", "/quit"],
    )
    await app.run()
    text = shown(out)
    assert re.search(r"Let me try that:\n {3}╭─ Permission needed", text)  # not on the same line
    assert re.search(r"timeout_seconds {2}60", text)  # a long name still has a gap before its value
    assert " ● All done." in text  # the follow-up starts a new message with its own marker
    assert seen == [True]  # a spinner was up while the tool ran, even though text had streamed
    rt.vault.close()


async def test_a_refused_tool_is_never_started_and_the_reply_still_gets_a_marker(
    tmp_path, vault_key
):
    app, rt, out, seen = build_app(
        tmp_path,
        vault_key,
        [with_text("Trying it.", "risky", command="dir"), say("Okay, I left it alone.")],
        ["do it", "/quit"],
        answers=("n",),
    )
    await app.run()
    assert seen == []  # the handler never ran
    assert " ● Okay, I left it alone." in shown(out)
    rt.vault.close()


async def test_running_out_of_steps_is_explained_even_after_text_was_shown(tmp_path, vault_key):
    replies = [
        with_text("Trying.", "memory_search", query=f"search number {i}") for i in range(MAX_STEPS)
    ]
    app, rt, out, _ = build_app(tmp_path, vault_key, replies, ["go on", "/quit"])
    await app.run()
    text = shown(out)
    assert "I ran out of steps before finishing." in text
    assert f"memory_search x{MAX_STEPS}" in text  # what was done, counted
    assert "Tell me to continue" in text
    assert f"memory_search ×{MAX_STEPS}" in text  # the footer groups the repeats
    rt.vault.close()


async def test_a_silent_step_cap_still_says_something(tmp_path, vault_key):
    replies = [
        ChatResult(tool_calls=[{"function": {"name": "memory_search",
                                             "arguments": {"query": f"query {i} here"}}}])
        for i in range(MAX_STEPS)
    ]  # fmt: skip
    app, rt, out, _ = build_app(tmp_path, vault_key, replies, ["go", "/quit"])
    await app.run()
    assert "I ran out of steps" in shown(out)
    rt.vault.close()


async def test_the_spinner_is_not_left_running_after_the_turn(tmp_path, vault_key):
    app, rt, out, _ = build_app(
        tmp_path,
        vault_key,
        [with_text("Working.", "risky", command="dir"), say("Finished.")],
        ["go", "/quit"],
    )
    await app.run()
    assert app._spinner is None
    rt.vault.close()


# ---- the loop's hook and the step-limit message --------------------------------------------------


async def agent_for(tmp_path, vault_key, replies, approve, on_tool):
    vault, store, svc, emb = make(tmp_path, vault_key, "exact")
    ing = Ingestor(store, emb, ScriptedLlm({}), sim_floor=0.4)

    async def handler(args: RiskyArgs) -> str:
        return "done"

    risky = Tool(
        "risky", "Needs approval.", RiskyArgs, Risk.R2, True, 5, "workspace-write", handler
    )
    agent = Agent(
        store, svc, ing, ScriptedChat(replies),
        approve=approve, tools=[*memory_tools(svc, ing), risky], on_tool=on_tool,
    )  # fmt: skip
    return agent, vault


async def test_on_tool_is_told_only_about_tools_that_are_going_to_run(tmp_path, vault_key):
    told: list[str] = []

    async def on_tool(name: str) -> None:
        told.append(name)

    async def refuse(name, args, decision) -> bool:
        return False

    agent, vault = await agent_for(
        tmp_path,
        vault_key,
        [with_text("", "risky", command="x"), with_text("", "memory_search", query="tea please"),
         say("ok")],
        refuse,
        on_tool,
    )  # fmt: skip
    await agent.turn(Session(), "hello")
    assert told == ["memory_search"]  # the refused call never started
    vault.close()


async def test_the_step_limit_message_lists_what_ran_and_what_failed(tmp_path, vault_key):
    replies = [with_text("", "memory_search", query=f"query {i} here") for i in range(MAX_STEPS)]
    replies[2] = with_text("", "nonexistent_tool")
    agent, vault = await agent_for(tmp_path, vault_key, replies, None, None)
    result = await agent.turn(Session(), "go")
    assert result.stopped == "step-cap"
    assert result.text.startswith("I ran out of steps before finishing.")
    assert f"memory_search x{MAX_STEPS - 1}" in result.text
    assert "Tell me to continue" in result.text
    vault.close()


# ---- the card and the footer, on their own -----------------------------------------------


def test_footer_groups_repeated_tools_and_keeps_blocked_ones_apart():
    out = Stream()
    c = Console(out, color=False)
    c.footer([("run_command", True)] * 3 + [("read_file", True), ("run_command", False)], 36.3)
    line = out.getvalue().strip()
    assert line == "⎿  run_command ×3, read_file, ✗ run_command blocked · 36.3s"
    ascii_out = Stream()
    Console(ascii_out, color=False, ascii_only=True).footer([("a", True), ("a", True)], 1.0)
    assert ascii_out.getvalue().strip() == "|_  a x2 - 1.0s"


def test_card_keeps_a_gap_after_every_key_and_stays_inside_the_screen():
    out = Stream()
    c = Console(out, color=False)
    c.approval("run_command", {"command": "dir", "timeout_seconds": 60, "a": 1}, "Careful.")
    lines = out.getvalue().splitlines()
    assert any(re.search(r"│ command {10,}'dir'$", line) for line in lines)
    assert any(re.search(r"│ timeout_seconds {2}60$", line) for line in lines)
    assert all(len(line) <= c.width for line in lines)
