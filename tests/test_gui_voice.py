"""Double-clicking the cat starts a voice conversation (and a second double-click ends it)."""

import os

import pytest

from ragnarok.gui.bridge import Bridge, GuiConsole
from ragnarok.gui.chat import ChatWindow
from ragnarok.gui.clicks import DOUBLE_CLICK_MS, DoubleClick
from tests.test_chat_flow import build_app
from tests.test_m4_agent import say
from tests.test_voice import FakeEngine, make_voice


class Clock:
    """A stand-in for Tk's `after`: jobs run when the test says time has passed."""

    def __init__(self) -> None:
        self.jobs: dict[int, tuple[int, object]] = {}
        self.now, self.next_id = 0, 0

    def after(self, ms, fn):
        self.next_id += 1
        self.jobs[self.next_id] = (self.now + ms, fn)
        return self.next_id

    def cancel(self, job):
        self.jobs.pop(job, None)

    def advance(self, ms):
        self.now += ms
        for job, (due, fn) in sorted(self.jobs.items()):
            if due <= self.now and job in self.jobs:
                del self.jobs[job]
                fn()


def clicker():
    clock, seen = Clock(), []
    clicks = DoubleClick(
        clock.after, clock.cancel, lambda: seen.append("single"), lambda: seen.append("double")
    )
    return clock, clicks, seen


def test_one_click_is_a_single_click_after_the_wait():
    clock, clicks, seen = clicker()
    clicks.click()
    assert seen == []  # it waits to see whether a second click follows
    clock.advance(DOUBLE_CLICK_MS + 1)
    assert seen == ["single"]


def test_two_quick_clicks_are_one_double_click_and_no_single_click():
    clock, clicks, seen = clicker()
    clicks.click()
    clock.advance(120)
    clicks.click()
    clock.advance(1000)
    assert seen == ["double"]


def test_two_slow_clicks_are_two_single_clicks():
    clock, clicks, seen = clicker()
    clicks.click()
    clock.advance(DOUBLE_CLICK_MS + 50)
    clicks.click()
    clock.advance(DOUBLE_CLICK_MS + 50)
    assert seen == ["single", "single"]


def test_three_quick_clicks_are_a_double_then_a_new_click():
    clock, clicks, seen = clicker()
    for _ in range(3):
        clicks.click()
        clock.advance(80)
    clock.advance(1000)
    assert seen == ["double", "single"]


def test_the_chat_window_shows_voice_mode_and_what_was_heard(hidden_tk):
    bridge = Bridge()
    busy: list[bool] = []
    chat = ChatWindow(hidden_tk, bridge, on_busy=busy.append, on_close=lambda: None)
    chat.handle("welcome", {"model": "qwen", "memories": 0, "gpu": "100% GPU", "first_run": False})
    chat.handle("voice", {"on": True})
    assert "listening" in chat.status.cget("text")
    chat.handle("heard", {"text": "open steam"})
    assert chat.busy and busy == [True]  # a heard sentence starts a turn, like typing
    chat.handle("voice", {"on": False})
    assert chat.status.cget("text") == "qwen · 100% GPU"


@pytest.fixture()
def vault_key() -> bytes:
    return os.urandom(32)


async def test_the_console_tells_the_window_when_voice_mode_starts_and_ends(tmp_path, vault_key):
    app, rt, _out, _ = build_app(tmp_path, vault_key, [say("Four.")], ["/voice talk", "/quit"])
    bridge = Bridge()
    app.c = GuiConsole(bridge)
    app.voice = make_voice(FakeEngine(heard=["what is two plus two", "stop listening"]))
    await app.run()
    kinds = [(k, d.get("on", d.get("text"))) for k, d in bridge.drain() if k in ("voice", "heard")]
    assert kinds == [("voice", True), ("heard", "what is two plus two"), ("voice", False)]
    rt.vault.close()


async def test_a_second_double_click_ends_voice_mode_even_while_listening(tmp_path, vault_key):
    app, rt, out, _ = build_app(tmp_path, vault_key, [], ["/voice talk", "/quit"])
    engine = FakeEngine(heard=["never used"])
    voice = make_voice(engine)
    original = engine.record

    def record_then_cancel(wait, max_seconds, device=None, stop=None):
        voice.cancel()  # the cat is double-clicked while the microphone is open
        assert stop is not None and stop.is_set()  # the recorder is told to stop at once
        return original.__self__.__class__.record(engine, wait, max_seconds, device, stop)

    voice.recorder = record_then_cancel
    app.voice = voice
    await app.run()
    assert not app._handsfree
    rt.vault.close()


def test_the_cat_app_wires_a_double_click_to_voice():
    import inspect
    import re

    from ragnarok.gui import app

    source = re.sub(r"\s+", " ", inspect.getsource(app.CatApp))
    assert (
        "DoubleClick( root.after, root.after_cancel, self.toggle_chat, self.toggle_voice" in source
    )
    assert "self.clicks.click()" in source  # a release without a drag goes through the counter
    assert 'self.bridge.send("/listen aloud")' in source  # one request, not a standing conversation
