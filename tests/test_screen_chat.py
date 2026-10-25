"""The chat screen during a screen task: progress lines, the approval question, and the spinner."""

import os
import re
import time
from types import SimpleNamespace

import pytest

from ragnarok.chat import ChatApp
from ragnarok.memory.ingest import Ingestor
from ragnarok.runtime.tools import memory_tools
from ragnarok.skills.screen.tools import screen_tools
from ragnarok.ui import FRAMES, Console
from tests.screen_fakes import (
    FakeDriver,
    FakeScreen,
    FakeWindows,
    ScriptedVision,
    World,
    done,
    key,
    typing,
)
from tests.test_m2_memory import make
from tests.test_m3_ingest import ScriptedLlm
from tests.test_m4_agent import call, say
from tests.test_m4_cli import FakeGateway
from tests.test_m4_ui import Stream

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
FRAME_CHARS = set(FRAMES)


@pytest.fixture()
def vault_key() -> bytes:
    return os.urandom(32)


def app_with_screen(
    tmp_path, vault_key, replies, lines, answers, *, color=False, tty=False, decisions=None
):
    vault, store, svc, emb = make(tmp_path, vault_key, "exact")
    gateway = FakeGateway(chat_replies=replies)
    ing = Ingestor(store, emb, ScriptedLlm({}), sim_floor=0.4)
    rt = SimpleNamespace(vault=vault, store=store, svc=svc, ingestor=ing, gateway=gateway)
    out = Stream(tty=tty)
    world = World()
    vision = ScriptedVision(decisions or [done()])
    tools = memory_tools(svc, ing) + screen_tools(
        None,
        screen=FakeScreen(world),
        driver=FakeDriver(world),
        windows=FakeWindows(world),
        vision=vision,
        settle=0,
    )
    typed, answered = iter(lines), iter(answers)

    def approve_line(prompt):
        out.write(prompt)
        time.sleep(0.35)  # long enough for a running spinner to draw several frames
        out.write("<<answered>>")
        return next(answered)

    app = ChatApp(
        rt,
        Console(out, color=color),
        folder="C:\\data",
        read_line=lambda prompt: next(typed),
        approve_line=approve_line,
        tools=tools,
        learn_delay=60,
    )
    return app, rt, out, world


def text(out) -> str:
    return ANSI.sub("", out.getvalue())


async def test_an_approved_task_shows_the_warning_the_steps_and_the_result(tmp_path, vault_key):
    app, rt, out, world = app_with_screen(
        tmp_path,
        vault_key,
        [call("use_computer", task="open notepad and type hi")],
        ["open notepad and type hi", "/quit"],
        ["y"],
        decisions=[key("win"), typing("notepad"), key("enter"), done("opened Notepad")],
    )
    await app.run()
    shown = text(out)
    squeezed = re.sub(r"\s*│\s*|\s+", " ", shown)  # the card wraps the warning over lines
    assert "Hold Esc or move the mouse to stop me" in squeezed  # it comes with the question
    assert "Permission needed" in shown and "use_computer" in shown
    assert "Do you want to proceed? [y/N]" in shown
    steps = re.findall(r"step \d+: .*", shown)
    assert steps == [
        "step 1: press win",
        "step 2: type 'notepad' into the box",
        "step 3: press enter",
    ]
    assert shown.index("[y/N]") < shown.index("step 1:") < shown.index("Done: opened Notepad")
    assert " ✓ Done: opened Notepad (4 steps" in shown
    assert [e[0] for e in world.events if e[0] in ("key", "type")] == ["key", "type", "key"]
    rt.vault.close()


async def test_saying_no_to_the_question_leaves_the_computer_alone(tmp_path, vault_key):
    app, rt, out, world = app_with_screen(
        tmp_path,
        vault_key,
        [call("use_computer", task="open notepad"), say("Okay, I will not touch it.")],
        ["open notepad", "/quit"],
        ["n"],
    )
    await app.run()
    shown = text(out)
    assert "Okay, I will not touch it." in shown and "step 1" not in shown
    assert [e for e in world.events if e[0] in ("key", "type", "click", "grab")] == []
    rt.vault.close()


async def test_the_spinner_stands_still_while_the_question_is_on_screen(tmp_path, vault_key):
    app, rt, out, world = app_with_screen(
        tmp_path,
        vault_key,
        [call("use_computer", task="open notepad")],
        ["open notepad", "/quit"],
        ["y"],
        color=True,
        tty=True,
        decisions=[key("win"), done("ok")],
    )
    await app.run()
    raw = ANSI.sub("", out.getvalue())
    asked = raw.index("[y/N]")
    answered = raw.index("<<answered>>")
    between = raw[asked:answered]
    assert not (FRAME_CHARS & set(between))  # no animation frame was drawn over the question
    assert FRAME_CHARS & set(raw[:asked])  # (it was animating before, so the check means something)
    assert FRAME_CHARS & set(raw[answered:])  # ...and it carries on afterwards
    rt.vault.close()


async def test_progress_lines_do_not_leave_the_spinner_stuck_on_the_line(tmp_path, vault_key):
    app, rt, out, world = app_with_screen(
        tmp_path,
        vault_key,
        [call("use_computer", task="two steps")],
        ["two steps", "/quit"],
        ["y"],
        color=True,
        tty=True,
        decisions=[key("win"), key("enter"), done("ok")],
    )
    await app.run()
    lines = text(out).splitlines()
    step_lines = [ln for ln in lines if "step 1:" in ln or "step 2:" in ln]
    assert len(step_lines) == 2
    for ln in step_lines:  # a progress line is a clean line, not mixed with spinner frames
        assert not (FRAME_CHARS & set(ln.split("\r")[-1]))
    rt.vault.close()


async def test_nothing_animates_when_output_is_not_a_terminal(tmp_path, vault_key):
    app, rt, out, world = app_with_screen(
        tmp_path,
        vault_key,
        [call("use_computer", task="x y z")],
        ["x y z", "/quit"],
        ["y"],
        decisions=[key("win"), done("ok")],
    )
    await app.run()
    assert not (FRAME_CHARS & set(out.getvalue())) and "\x1b" not in out.getvalue()
    rt.vault.close()
