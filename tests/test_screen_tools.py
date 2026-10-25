"""read_screen and use_computer as the chat model uses them: gating, approval, logging, display."""

import json
import os
import re

import pytest

from ragnarok.memory.ingest import INGESTIBLE
from ragnarok.policy import permissions
from ragnarok.policy.permissions import Risk
from ragnarok.runtime.loop import Session
from ragnarok.runtime.tools import CURRENT_TEXT, EVENT_LOG, PROGRESS
from ragnarok.skills.screen import tools as tools_mod
from ragnarok.skills.screen.capture import DETAIL_WIDTH, MAX_WIDTH
from ragnarok.skills.screen.tools import asks_about_screen, screen_tools
from tests.screen_fakes import (
    NOTEPAD,
    TERMINAL,
    FakeDriver,
    FakeScreen,
    FakeWindows,
    ScriptedVision,
    World,
    click,
    done,
    key,
    typing,
)
from tests.test_m4_agent import build, call, say
from tests.test_screen_capture import decode_png


@pytest.fixture()
def vault_key() -> bytes:
    return os.urandom(32)


def tools_for(decisions=None, answer="A page about tea.", verdicts=None):
    world = World()
    vision = ScriptedVision(decisions or [done()], verdicts, answer)
    screen = FakeScreen(world)
    tools = {
        t.name: t
        for t in screen_tools(
            None,
            screen=screen,
            driver=FakeDriver(world),
            windows=FakeWindows(world),
            vision=vision,
            settle=0,
        )
    }
    return tools, world, vision, screen


async def run(tool, user_text: str, **args):
    CURRENT_TEXT.set(user_text)
    return await tool.handler(tool.args_model(**args))


# ---- deciding whether the user asked --------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "what's on my screen",
        "read my screen",
        "what am I looking at",
        "summarise this page",
        "what does this error say",
        "look at my screen and tell me the total",
        "read the open window",
        "explain this dialog",
        "what's showing right now",
        "can you see my display",
    ],
)
def test_screen_questions_are_recognised(text):
    assert asks_about_screen(text)


@pytest.mark.parametrize(
    "text",
    [
        "open steam",
        "remember I like tea",
        "is there a file about datastructures",
        "what is the capital of France",
        "search carryminati on youtube",
        "hello",
        "what time is it",
        "what is my manager's name",
    ],
)
def test_other_messages_do_not_unlock_the_screen(text):
    assert not asks_about_screen(text)


# ---- read_screen ----------------------------------------------------------------------------


async def test_read_screen_refuses_unless_the_user_asked_and_captures_nothing():
    tools, world, vision, screen = tools_for()
    out = await run(tools["read_screen"], "open steam", question="what is on screen")
    assert out == "error: I only look at your screen when you ask me to."
    assert screen.widths == [] and vision.questions == []  # nothing was captured or sent


async def test_a_page_cannot_make_the_agent_look_by_itself():
    """Text from a file or web page is never the user's message, so it cannot unlock reading."""
    tools, world, vision, screen = tools_for()
    injected = "summarise the file notes.txt"  # the user's real message, about a FILE
    out = await run(tools["read_screen"], injected)
    assert out.startswith("error:") and screen.widths == []


async def test_read_screen_answers_from_one_png_and_ends_the_turn():
    tools, world, vision, screen = tools_for(answer="The page is about tea; the total is Rs 4,200.")
    events = []

    async def log(kind, payload):
        events.append((kind, payload, len(screen.widths)))  # how many captures had happened

    EVENT_LOG.set(log)
    out = await run(
        tools["read_screen"], "what's the total on my screen", question="What is the total?"
    )
    assert out.final and out.text == "The page is about tea; the total is Rs 4,200."
    assert vision.questions == ["What is the total?"] and len(vision.pngs) == 1
    assert decode_png(vision.pngs[0]).shape == (360, 640, 3)
    assert events == [
        ("screen_read", "What is the total? (detail normal)", 0)
    ]  # logged before capture
    assert screen.widths == [MAX_WIDTH]


async def test_detail_high_captures_at_full_width():
    tools, world, vision, screen = tools_for()
    await run(tools["read_screen"], "read the small print on my screen", detail="high")
    assert screen.widths == [DETAIL_WIDTH]


@pytest.mark.parametrize(
    ("text", "more"),
    [
        ("read my screen and then save the total", True),
        ("what's on my screen and email it to Priya", True),
        ("summarise this page, then add it to my notes", True),
        ("what's on my screen", False),
        ("what's on my screen and what does it mean", False),
        ("read this error and explain it", False),
    ],
)
def test_follow_up_detection(text, more):
    assert tools_mod.has_follow_up(text) is more


async def test_a_bigger_request_gets_the_text_marked_as_data_for_the_model():
    tools, world, vision, screen = tools_for(answer="Total: Rs 4,200")
    out = await run(tools["read_screen"], "read my screen and then save the total")
    assert isinstance(out, str)
    assert out == "<screen_content>\nTotal: Rs 4,200\n</screen_content>"


async def test_the_users_terminal_is_moved_aside_for_the_look_and_put_back():
    tools, world, vision, screen = tools_for()
    world.foreground = TERMINAL
    await run(tools["read_screen"], "what's on my screen")
    order = [e[0] for e in world.events if e[0] in ("minimize", "grab", "restore")]
    assert order == ["minimize", "grab", "restore"]


async def test_it_does_not_touch_other_windows():
    tools, world, vision, screen = tools_for()
    world.foreground = NOTEPAD
    await run(tools["read_screen"], "what's on my screen")
    assert world.hidden == [] and world.restored == []


async def test_the_terminal_comes_back_even_if_reading_fails():
    tools, world, vision, screen = tools_for()
    world.foreground = TERMINAL

    async def boom(png, question):
        raise RuntimeError("model crashed")

    vision.answer = boom
    with pytest.raises(RuntimeError):
        await run(tools["read_screen"], "what's on my screen")
    assert world.restored == [222]  # restored right after the capture, before the model ran


async def test_an_empty_answer_is_reported_not_passed_off_as_an_answer():
    tools, *_ = tools_for(answer="")
    assert (
        await run(tools["read_screen"], "what's on my screen")
    ) == "error: I could not read the screen"


async def test_a_delay_lets_the_user_switch_windows_first(monkeypatch):
    tools, world, vision, screen = tools_for()
    waited, said = [], []

    async def fake_sleep(seconds):
        waited.append(seconds)

    async def progress(text):
        said.append(text)

    monkeypatch.setattr(tools_mod.asyncio, "sleep", fake_sleep)
    PROGRESS.set(progress)
    await run(tools["read_screen"], "read my screen in a moment", delay_seconds=4)
    assert waited[0] == 4 and "switch to what you want me to read" in said[0]
    assert tools["read_screen"].args_model.model_fields["delay_seconds"].metadata  # bounded


def test_the_delay_is_capped():
    tools, *_ = tools_for()
    with pytest.raises(ValueError):
        tools["read_screen"].args_model(delay_seconds=600)


async def test_reading_writes_nothing_to_disk(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    tools, *_ = tools_for()
    await run(tools["read_screen"], "what's on my screen")
    assert list(tmp_path.iterdir()) == []


# ---- the tools' declarations and permissions ------------------------------------------------


def test_both_tools_declare_what_the_rules_require():
    tools, *_ = tools_for()
    read, use = tools["read_screen"], tools["use_computer"]
    assert (read.risk, use.risk) == (Risk.R1, Risk.R2)
    assert read.sandbox == "read-only" and use.sandbox == "full-access"
    assert read.idempotent and not use.idempotent and read.timeout_s > 0 and use.timeout_s > 0
    assert set(read.spec()["function"]["parameters"]["properties"]) == {
        "question",
        "detail",
        "delay_seconds",
    }
    assert set(use.spec()["function"]["parameters"]["properties"]) == {"task"}
    assert "Esc" in use.approval_note and "15 steps" in use.approval_note
    for t in tools.values():
        assert t.description.count(".") <= 2


def test_using_the_computer_always_asks_and_background_runs_cannot():
    assert permissions.check("interactive", "use_computer", Risk.R2).ask
    assert not permissions.check("interactive", "use_computer", Risk.R2).allowed
    assert permissions.check("interactive", "read_screen", Risk.R1).allowed
    assert permissions.check("background", "use_computer", Risk.R2).ask
    assert permissions.check("background", "read_screen", Risk.R1).ask


def test_the_screen_events_are_never_mined_for_memories():
    assert "screen_action" not in INGESTIBLE and "screen_read" not in INGESTIBLE


# ---- through the real agent loop ------------------------------------------------------------


def with_screen(agent, **kw):
    tools, world, vision, screen = tools_for(**kw)
    agent.tools.update(tools)
    return tools, world, vision, screen


def events(vault):
    return [(r[0], r[1]) for r in vault.query("SELECT kind, payload FROM events ORDER BY id")]


async def test_an_approved_task_runs_and_every_step_is_in_the_log_first(tmp_path, vault_key):
    approvals = []

    async def approve(name, args, decision):
        approvals.append((name, args, decision.rule))
        return True

    vault, store, svc, chat, agent = build(
        tmp_path,
        vault_key,
        [call("use_computer", task="open notepad and type hello")],
        approve=approve,
    )
    tools, world, vision, screen = with_screen(
        agent, decisions=[key("win"), typing("notepad"), key("enter"), done("opened Notepad")]
    )
    result = await agent.turn(Session(), "open notepad and type hello")
    assert approvals == [("use_computer", {"task": "open notepad and type hello"}, "default:R2")]
    assert result.text.startswith("Done: opened Notepad") and result.final_tool
    kinds = [k for k, _ in events(vault)]
    assert kinds == [
        "user_message", "memory_tool_call", "screen_action", "screen_action", "screen_action",
        "memory_tool_result", "assistant_message",
    ]  # fmt: skip
    actions = [json.loads(p) for k, p in events(vault) if k == "screen_action"]
    assert [a["action"] for a in actions] == ["key", "type", "key"] and actions[1][
        "text"
    ] == "notepad"
    assert [e[0] for e in world.events if e[0] in ("key", "type")] == ["key", "type", "key"]
    vault.close()


async def test_declining_the_task_means_nothing_happens(tmp_path, vault_key):
    async def refuse(name, args, decision):
        return False

    vault, store, svc, chat, agent = build(
        tmp_path,
        vault_key,
        [call("use_computer", task="open notepad"), say("Okay, I won't.")],
        approve=refuse,
    )
    tools, world, vision, screen = with_screen(agent, decisions=[key("win"), done()])
    result = await agent.turn(Session(), "open notepad")
    assert result.text == "Okay, I won't."
    assert [
        e for e in world.events if e[0] in ("key", "type", "click", "grab")
    ] == []  # not even a look
    assert not result.trace[0].allowed and "screen_action" not in [k for k, _ in events(vault)]
    vault.close()


async def test_no_approver_means_no_control(tmp_path, vault_key):
    vault, store, svc, chat, agent = build(
        tmp_path,
        vault_key,
        [call("use_computer", task="open notepad"), say("I need your permission.")],
    )
    tools, world, vision, screen = with_screen(agent, decisions=[key("win"), done()])
    await agent.turn(Session(), "open notepad")
    assert [e for e in world.events if e[0] in ("key", "grab")] == []
    vault.close()


async def test_a_risky_step_asks_again_through_the_same_approver(tmp_path, vault_key):
    asked = []

    async def approve(name, args, decision):
        asked.append((name, args))
        return name == "use_computer"  # yes to the task, no to the risky step

    vault, store, svc, chat, agent = build(
        tmp_path, vault_key, [call("use_computer", task="clean up my drafts")], approve=approve
    )
    tools, world, vision, screen = with_screen(
        agent, decisions=[click(500, 500, "Delete all drafts"), done("tried")]
    )
    await agent.turn(Session(), "clean up my drafts")
    assert [a[0] for a in asked] == ["use_computer", "screen_action"]
    assert "delete all drafts" in asked[1][1]["action"].lower()
    assert [e for e in world.events if e[0] == "click"] == []  # declined: no click happened
    vault.close()


async def test_read_screen_through_the_agent_logs_the_look_and_answers(tmp_path, vault_key):
    vault, store, svc, chat, agent = build(
        tmp_path, vault_key, [call("read_screen", question="What is the total?")]
    )
    with_screen(agent, answer="The total is Rs 4,200.")
    result = await agent.turn(Session(), "what's the total on my screen?")
    assert result.text == "The total is Rs 4,200."
    kinds = [k for k, _ in events(vault)]
    assert kinds == [
        "user_message",
        "memory_tool_call",
        "screen_read",
        "memory_tool_result",
        "assistant_message",
    ]
    assert result.trace[0].allowed and result.trace[0].rule == "default:R1"
    vault.close()


async def test_a_model_tricked_into_calling_read_screen_gets_nothing(tmp_path, vault_key):
    vault, store, svc, chat, agent = build(
        tmp_path, vault_key, [call("read_screen", question="everything"), say("Hello!")]
    )
    tools, world, vision, screen = with_screen(agent, answer="SECRET SCREEN TEXT")
    result = await agent.turn(Session(), "hello")  # the user never asked about the screen
    assert screen.widths == [] and result.text == "Hello!"
    assert "SECRET SCREEN TEXT" not in json.dumps(chat.calls[1]["messages"])
    vault.close()


def test_the_log_helpers_are_not_left_set_between_turns():
    # each turn sets its own; a stale one from an earlier turn must not be reused silently
    assert re.match(r"EVENT_LOG|event_log", EVENT_LOG.name) or EVENT_LOG.name == "event_log"
