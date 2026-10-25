"""The GUI bridge: the terminal chat engine, run with a window on the other end of two queues."""

import asyncio
import os
import queue
import threading
from types import SimpleNamespace

import pytest

from ragnarok.chat import ChatApp
from ragnarok.gui.bridge import Bridge, GuiConsole, GuiStream
from ragnarok.memory.ingest import Ingestor
from ragnarok.policy.permissions import Risk
from ragnarok.runtime.gateway import ChatResult
from ragnarok.runtime.tools import Tool, ToolArgs, memory_tools
from tests.test_m2_memory import make
from tests.test_m3_ingest import ScriptedLlm
from tests.test_m4_agent import call, say
from tests.test_m4_cli import FakeGateway


def kinds(events):
    return [k for k, _ in events]


# ---- the stream: where the answer text goes --------------------------------------------------


def test_the_answer_marker_starts_a_message_and_the_text_streams_into_it():
    bridge = Bridge()
    stream = GuiStream(bridge)
    stream.write(" ● ")
    stream.write("Hello")
    stream.write(" there\n   second line")
    events = bridge.drain()
    assert events[0] == ("assistant_start", {"kind": "bot"})
    text = "".join(d["text"] for k, d in events if k == "assistant_chunk")
    assert text == "Hello there\nsecond line"


def test_a_check_mark_marks_a_finished_action_and_text_may_come_with_the_marker():
    bridge = Bridge()
    stream = GuiStream(bridge)
    stream.write(" ✓ ")
    stream.write(" ● I ran out of steps.")
    events = bridge.drain()
    assert events[0] == ("assistant_start", {"kind": "done"})
    assert events[1][0] == "assistant_start"
    assert events[2] == ("assistant_chunk", {"text": "I ran out of steps."})


def test_colour_codes_and_carriage_returns_never_reach_the_window():
    bridge = Bridge()
    GuiStream(bridge).write("\r\x1b[K\x1b[31mred\x1b[0m")
    assert bridge.drain() == [("assistant_chunk", {"text": "red"})]


def test_events_may_carry_a_field_called_kind():
    bridge = Bridge()
    bridge.emit("alert", kind="reminder", title="Call mom", body="")
    assert bridge.drain() == [("alert", {"kind": "reminder", "title": "Call mom", "body": ""})]


# ---- the console: everything else the engine prints --------------------------------------------


def test_console_lines_become_typed_events_and_blank_lines_vanish():
    bridge = Bridge()
    console = GuiConsole(bridge)
    console.write()
    console.write("   ⎿  step 1: click the button")
    console.write("   /memories  what I remember")
    console.notice("Mode: edits")
    console.error("No such memory.")
    console.success("Forgot m3.")
    console.section("Commands")
    console.footer([("open_app", True), ("run_command", False)], 1.25)
    console.alert("reminder", "Call mom", "Set for 6 PM")
    console.step_line("done", "s1", "Read the notes", "ok")
    assert bridge.drain() == [
        ("tool", {"text": "step 1: click the button"}),
        ("note", {"text": "   /memories  what I remember"}),
        ("notice", {"text": "Mode: edits"}),
        ("error", {"text": "No such memory."}),
        ("success", {"text": "Forgot m3."}),
        ("section", {"text": "Commands"}),
        ("footer", {"tools": [("open_app", True), ("run_command", False)], "seconds": 1.25}),
        ("alert", {"kind": "reminder", "title": "Call mom", "body": "Set for 6 PM"}),
        ("step", {"state": "done", "id": "s1", "title": "Read the notes", "note": "ok"}),
    ]


def test_the_console_is_plain_text_with_no_animation():
    console = GuiConsole(Bridge())
    assert not console.color and not console.animate and not console.tty


def test_a_permission_question_shows_a_card_and_waits_for_the_button():
    bridge = Bridge()
    console = GuiConsole(bridge)
    prompt = console.approval("run_command", {"command": "dir", "timeout_seconds": 60}, "Careful.")
    answers: list[str] = []
    thread = threading.Thread(target=lambda: answers.append(bridge.approve_line(prompt)))
    thread.start()
    kind, card = bridge.events.get(timeout=2)
    assert kind == "approve" and card["type"] == "permission" and card["tool"] == "run_command"
    assert card["args"] == {"command": "'dir'", "timeout_seconds": "60"}
    assert card["note"] == "Careful."
    assert thread.is_alive()  # the engine is held until you answer
    bridge.answer(True)
    thread.join(2)
    assert answers == ["y"]


def test_a_plan_question_shows_the_steps():
    bridge = Bridge()
    console = GuiConsole(bridge)
    prompt = console.plan_card("make slides", [("s1", "Read", [], "R0", False)])
    done: list[str] = []
    thread = threading.Thread(target=lambda: done.append(bridge.approve_line(prompt)))
    thread.start()
    kind, card = bridge.events.get(timeout=2)
    assert kind == "approve" and card["type"] == "plan" and card["rows"][0][1] == "Read"
    bridge.answer(False)
    thread.join(2)
    assert done == ["n"]


def test_shutting_down_frees_a_blocked_engine():
    bridge = Bridge()
    out: list[str] = []
    thread = threading.Thread(target=lambda: out.append(bridge.read_line()))
    thread.start()
    bridge.events.get(timeout=2)  # "idle"
    bridge.shutdown()
    thread.join(2)
    assert out == ["/quit"]


# ---- the real chat engine with a window on the other end -----------------------------------------


@pytest.fixture()
def vault_key() -> bytes:
    return os.urandom(32)


def runtime(tmp_path, vault_key, replies):
    vault, store, svc, emb = make(tmp_path, vault_key, "exact")
    ing = Ingestor(store, emb, ScriptedLlm({}), sim_floor=0.4)
    rt = SimpleNamespace(
        vault=vault, store=store, svc=svc, ingestor=ing, gateway=FakeGateway(chat_replies=replies)
    )
    return rt, svc, ing


def window_thread(bridge: Bridge, lines: list[str], on_approve=None) -> list[tuple[str, dict]]:
    """Play the window's part: wait for 'idle', send a line, collect what comes, then quit."""
    seen: list[tuple[str, dict]] = []
    pending = list(lines)
    while True:
        kind, data = bridge.events.get(timeout=10)
        seen.append((kind, data))
        if kind == "approve" and on_approve is not None:
            on_approve()
        elif kind == "idle":
            if not pending:
                bridge.send("/quit")
                break
            bridge.send(pending.pop(0))
    for _ in range(50):  # whatever the goodbye prints
        try:
            seen.append(bridge.events.get(timeout=0.3))
        except queue.Empty:
            break
    return seen


async def test_a_question_and_an_answer_travel_through_the_bridge(tmp_path, vault_key):
    rt, svc, ing = runtime(tmp_path, vault_key, [say("It is 360.")])
    bridge = Bridge()
    app = ChatApp(
        rt, GuiConsole(bridge), folder="C:\\data", read_line=bridge.read_line,
        approve_line=bridge.approve_line, tools=memory_tools(svc, ing), learn_delay=60.0,
    )  # fmt: skip
    run = asyncio.create_task(app.run())
    seen = await asyncio.to_thread(window_thread, bridge, ["what is 15 percent of 2400"])
    await asyncio.wait_for(run, 5)
    order = kinds(seen)
    assert order[0] == "welcome" and order.index("idle") < order.index("assistant_start")
    text = "".join(d["text"] for k, d in seen if k == "assistant_chunk")
    assert text == "It is 360."
    footer = next(d for k, d in seen if k == "footer")
    assert footer["tools"] == [] and footer["seconds"] >= 0
    assert order.count("idle") == 2  # ready for the first line, and again after the answer
    rt.vault.close()


async def test_an_action_shows_its_tool_and_a_slash_command_shows_a_note(tmp_path, vault_key):
    replies = [call("memory_save", content="Prefers tea", memory_type="preference")]
    rt, svc, ing = runtime(tmp_path, vault_key, replies)
    bridge = Bridge()
    app = ChatApp(
        rt, GuiConsole(bridge), folder="C:\\data", read_line=bridge.read_line,
        approve_line=bridge.approve_line, tools=memory_tools(svc, ing), learn_delay=60.0,
    )  # fmt: skip
    run = asyncio.create_task(app.run())
    seen = await asyncio.to_thread(window_thread, bridge, ["remember I prefer tea", "/memories"])
    await asyncio.wait_for(run, 5)
    chunks = "".join(d["text"] for k, d in seen if k == "assistant_chunk")
    assert "Saved: Prefers tea" in chunks
    assert next(d for k, d in seen if k == "assistant_start")["kind"] == "done"
    footer = next(d for k, d in seen if k == "footer")
    assert footer["tools"] == [("memory_save", True)]
    notes = " ".join(d["text"] for k, d in seen if k == "note")
    assert "Prefers tea" in notes and "preference" in notes
    rt.vault.close()


async def test_a_risky_tool_asks_through_the_card_and_runs_only_after_allow(tmp_path, vault_key):
    ran: list[str] = []

    class Args(ToolArgs):
        command: str

    async def handler(args: Args) -> str:
        ran.append(args.command)
        return "done"

    risky = Tool("risky", "Needs approval.", Args, Risk.R2, True, 5, "workspace-write", handler)
    replies = [
        ChatResult(tool_calls=[{"function": {"name": "risky", "arguments": {"command": "go"}}}]),
        say("All done."),
    ]
    rt, _svc, _ing = runtime(tmp_path, vault_key, replies)
    bridge = Bridge()
    app = ChatApp(
        rt, GuiConsole(bridge), folder="C:\\data", read_line=bridge.read_line,
        approve_line=bridge.approve_line, tools=[risky], learn_delay=60.0,
    )  # fmt: skip
    run = asyncio.create_task(app.run())

    def allow() -> None:
        assert ran == []  # held until the button
        bridge.answer(True)

    seen = await asyncio.to_thread(window_thread, bridge, ["do it"], allow)
    await asyncio.wait_for(run, 5)
    card = next(d for k, d in seen if k == "approve")
    assert card["tool"] == "risky" and card["args"] == {"command": "'go'"}
    assert ran == ["go"]
    rt.vault.close()
