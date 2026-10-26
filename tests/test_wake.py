"""The wake phrase "hey Ragnarok", and the one-shot listen behind the cat's double-click."""

import asyncio
import os
import threading
import time
from types import SimpleNamespace

import pytest

from ragnarok import voice_whisper as vw
from ragnarok.gui.bridge import Bridge, GuiConsole
from ragnarok.voice import Voice
from ragnarok.voice_capture import MicError, Recording
from ragnarok.wake import WakeListener, match_wake
from tests.test_chat_flow import build_app, shown
from tests.test_m4_agent import say
from tests.test_voice import FakeEngine, make_voice


@pytest.fixture()
def vault_key() -> bytes:
    return os.urandom(32)


# ---- recognising the phrase -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("heard", "rest"),
    [
        ("Hey Ragnarok.", ""),
        ("hey rag narok", ""),
        ("Hey Ragnarock, open steam", "open steam"),
        ("hey ragnar rock what time is it", "what time is it"),
        ("Hay Ragnarok", ""),
        ("a ragnarok", ""),
        ("Hey, Ragnarok! Open the downloads folder.", "Open the downloads folder"),
        ("OK Ragnarok play music", "play music"),
        ("hey rag a rock", ""),
        ("Hey Rangrok.", ""),
        ("hi rangrok what time is it", "what time is it"),
        ("rangrok open steam", "open steam"),
    ],
)
def test_the_phrase_is_found_however_it_is_usually_misheard(heard, rest):
    found = match_wake(heard)
    assert found is not None and found.rest == rest


@pytest.mark.parametrize(
    "heard",
    [
        "hey Roger, how are you",
        "what time is it",
        "the rock and roll band",
        "hey google",
        "I need to go to the market",
        "open steam and tell me the time",
        "hey there",
        "rock the boat",
        "Ragnar is a viking",
        "I rank rock bands",
        "hey there rock",
        "send me the report by friday",
        "",
    ],
)
def test_ordinary_speech_is_not_the_wake_phrase(heard):
    assert match_wake(heard) is None


# ---- the listener -----------------------------------------------------------------------------


def clip(text: str, seconds: float = 2.0) -> Recording:
    return Recording(pcm=text.encode(), heard_speech=True, device="Test mic", seconds=seconds)


SILENCE = Recording(device="Test mic", peak_percent=0.2)


class Mic:
    """A microphone that plays a script of clips, then silence."""

    def __init__(self, *clips):
        self.clips, self.calls = list(clips), 0

    def __call__(self, wait, max_seconds, device=None, stop=None) -> Recording:
        self.calls += 1
        if self.clips:
            return self.clips.pop(0)
        time.sleep(0.02)
        return SILENCE


def make_listener(mic, transcribe=None, paused=lambda: False):
    woken, errors = [], []

    async def on_wake(rest):
        woken.append(rest)

    listener = WakeListener(
        lock=asyncio.Lock(),
        transcribe=transcribe or (lambda pcm: pcm.decode()),
        on_wake=on_wake,
        on_error=errors.append,
        paused=paused,
        record=mic,
    )
    return listener, woken, errors


async def run_for(listener, seconds=0.5):
    listener.start()
    await asyncio.sleep(seconds)
    await listener.stop()


async def test_the_listener_wakes_on_the_phrase_and_ignores_everything_else():
    mic = Mic(clip("what a lovely day"), clip("hey ragnarok open steam"), clip("hello"))
    listener, woken, errors = make_listener(mic)
    await run_for(listener)
    assert woken == ["open steam"] and errors == []


async def test_a_long_burst_is_a_conversation_and_is_never_even_transcribed():
    seen = []

    def spy(pcm):
        seen.append(pcm)
        return "hey ragnarok"

    listener, woken, _ = make_listener(Mic(clip("hey ragnarok blah", seconds=6.1)), transcribe=spy)
    await run_for(listener, 0.3)
    assert seen == [] and woken == []


async def test_nothing_is_listened_to_while_it_is_paused():
    mic = Mic(clip("hey ragnarok"))
    listener, woken, _ = make_listener(mic, paused=lambda: True)
    await run_for(listener, 0.5)
    assert mic.calls == 0 and woken == []  # the microphone was never even opened


async def test_a_missing_microphone_turns_the_listener_off_with_a_message():
    def broken(*args):
        raise MicError("no microphone was found")

    listener, woken, errors = make_listener(broken)
    await run_for(listener, 0.3)
    assert errors == ["no microphone was found"] and not listener.active


async def test_a_failing_model_turns_the_listener_off_without_crashing():
    def bad(pcm):
        raise RuntimeError("model gone")

    listener, _woken, errors = make_listener(Mic(clip("anything")), transcribe=bad)
    await run_for(listener, 0.3)
    assert errors and "model gone" in errors[0] and not listener.active


# ---- in the chat ------------------------------------------------------------------------------


def wake_voice(spoken, done, mic):
    """A Voice whose microphone plays `mic` and whose speech model reads the clips back as text."""

    async def runner(script, env, stdin=None):
        spoken.append(env["RAG_TTS_TEXT"])
        done.set()
        return 0, "", ""

    def text(pcm):
        return pcm.decode()

    return Voice(runner=runner, recorder=mic, transcriber=text, wake_transcriber=text)


def reader(first, done):
    """Typed input: one line to switch things on, then it waits for the voice request to finish."""
    lines = [first]

    def read(prompt):
        if lines:
            return lines.pop(0)
        done.wait(10)
        time.sleep(0.2)
        return "/quit"

    return read


async def test_hey_ragnarok_with_a_request_is_answered_aloud(tmp_path, vault_key):
    done, spoken = threading.Event(), []
    app, rt, out, _ = build_app(tmp_path, vault_key, [say("It is noon.")], [])
    app.read_line = reader("/voice wake on", done)
    app.voice = wake_voice(spoken, done, Mic(clip("hey ragnarok what time is it")))
    await app.run()
    text = shown(out)
    assert 'Listening for "hey Ragnarok"' in text and 'Heard "hey Ragnarok".' in text
    assert "heard: what time is it" in text and "It is noon." in text
    assert spoken and "noon" in spoken[0]  # asked by voice, so answered aloud
    assert rt.store.meta_get("voice_wake") == "1"  # remembered for next time
    rt.vault.close()


async def test_hey_ragnarok_alone_listens_for_the_request(tmp_path, vault_key):
    done, spoken = threading.Event(), []
    app, rt, out, _ = build_app(tmp_path, vault_key, [say("Opening it.")], [])
    app.read_line = reader("/voice wake on", done)
    app.voice = wake_voice(spoken, done, Mic(clip("hey ragnarok"), clip("open steam please")))
    await app.run()
    text = shown(out)
    assert "Yes? I'm listening" in text  # it says so, so you know to go on
    assert "heard: open steam please" in text and "Opening it." in text and spoken
    assert any(m["content"] == "open steam please" for m in app.session.history)
    rt.vault.close()


async def test_an_ordinary_conversation_in_the_room_does_nothing(tmp_path, vault_key):
    app, rt, out, _ = build_app(tmp_path, vault_key, [], [])
    lines = ["/voice wake on"]

    def read(prompt):
        if lines:
            return lines.pop(0)
        time.sleep(0.8)
        return "/quit"

    app.read_line = read
    app.voice = wake_voice([], threading.Event(), Mic(clip("did you see the game last night")))
    await app.run()
    assert app.session.history == [] and 'Heard "hey Ragnarok"' not in shown(out)
    rt.vault.close()


async def test_wake_off_stops_listening_and_is_remembered(tmp_path, vault_key):
    lines = ["/voice wake on", "/voice wake off", "/quit"]
    app, rt, out, _ = build_app(tmp_path, vault_key, [], lines)
    app.voice = wake_voice([], threading.Event(), Mic())
    await app.run()
    assert "The wake phrase is off." in shown(out)
    assert rt.store.meta_get("voice_wake") == "0" and not app._wake.active
    rt.vault.close()


async def test_the_wake_phrase_starts_again_by_itself_if_it_was_left_on(tmp_path, vault_key):
    app, rt, out, _ = build_app(tmp_path, vault_key, [], ["/quit"])
    rt.store.meta_set("voice_wake", "1")
    app.voice = wake_voice([], threading.Event(), Mic())
    await app.run()
    assert 'Listening for "hey Ragnarok"' in shown(out)
    rt.vault.close()


async def test_the_wake_phrase_explains_when_it_cannot_run(tmp_path, vault_key, monkeypatch):
    monkeypatch.setenv("RAGNAROK_NO_VOICE", "1")
    app, rt, out, _ = build_app(tmp_path, vault_key, [], ["/voice wake on", "/quit"])
    await app.run()
    assert "needs voice and the local Whisper model" in shown(out) and app._wake is None
    rt.vault.close()


async def test_the_cat_window_is_told_when_the_wake_phrase_is_on(tmp_path, vault_key):
    lines = ["/voice wake on", "/voice wake off", "/quit"]
    app, rt, _out, _ = build_app(tmp_path, vault_key, [], lines)
    bridge = Bridge()
    app.c = GuiConsole(bridge)
    app.voice = wake_voice([], threading.Event(), Mic())
    await app.run()
    states = [d["on"] for k, d in bridge.drain() if k == "wake"]
    assert states[:2] == [True, False]
    rt.vault.close()


# ---- the cat's double-click is ONE request, then it stops listening ---------------------------


async def test_listen_aloud_takes_one_request_then_closes_the_microphone(tmp_path, vault_key):
    replies = [say("It is noon."), say("Never asked.")]
    app, rt, _out, _ = build_app(tmp_path, vault_key, replies, ["/listen aloud", "/quit"])
    engine = FakeEngine(heard=["what time is it", "this is never listened to"])
    app.voice = make_voice(engine)
    bridge = Bridge()
    app.c = GuiConsole(bridge)
    await app.run()
    await app.voice.finish_speaking()
    assert [k for k, _ in engine.log].count("listen") == 1  # it did not keep listening
    assert [text for kind, text in engine.log if kind == "speak"] == ["It is noon."]
    flags = [d["on"] for k, d in bridge.drain() if k == "voice"]
    assert flags == [True, False] and not app._handsfree
    rt.vault.close()


async def test_a_plain_listen_does_not_read_the_answer_aloud(tmp_path, vault_key):
    app, rt, _out, _ = build_app(tmp_path, vault_key, [say("It is noon.")], ["/listen", "/quit"])
    engine = FakeEngine(heard=["what time is it"])
    app.voice = make_voice(engine)
    await app.run()
    await app.voice.finish_speaking()
    assert [text for kind, text in engine.log if kind == "speak"] == []
    rt.vault.close()


async def test_a_cancelled_listen_sends_nothing(tmp_path, vault_key):
    app, rt, out, _ = build_app(tmp_path, vault_key, [], ["/listen aloud", "/quit"])
    engine = FakeEngine(heard=["never used"])
    voice = make_voice(engine)
    original = voice.recorder

    def cancelled(wait, max_seconds, device=None, stop=None):
        voice.cancel()  # the cat is double-clicked while it is listening
        return original(wait, max_seconds, device, stop)

    voice.recorder = cancelled
    app.voice = voice
    await app.run()
    assert "I stopped listening" in shown(out) and app.session.history == []
    rt.vault.close()


# ---- noise is not a request -------------------------------------------------------------------


def model_saying(text):
    segment = SimpleNamespace(text=text, no_speech_prob=0.1, avg_logprob=-0.3)
    return SimpleNamespace(transcribe=lambda audio, **kw: (iter([segment]), None))


@pytest.mark.parametrize("invented", ["Thank you.", "you", "Thanks for watching!", " Bye. "])
def test_what_whisper_invents_from_noise_is_dropped(monkeypatch, invented):
    monkeypatch.setattr(vw, "_load", lambda name=None: model_saying(invented))
    assert vw.transcribe(b"\x10\x00" * 16000) == ""


def test_a_real_short_request_is_kept(monkeypatch):
    monkeypatch.setattr(vw, "_load", lambda name=None: model_saying(" Open steam."))
    assert vw.transcribe(b"\x10\x00" * 16000, prompt="Hey Ragnarok.") == "Open steam."


async def test_a_short_phrase_followed_by_a_pause_is_not_mistaken_for_a_conversation():
    """The quiet that ends a sentence is trimmed, so "hey ragnarok" stays about 1.5 s, not 3 s."""
    from ragnarok import voice_capture as vc
    from tests.test_voice import feed

    levels = [40] * 6 + [8000] * 15 + [40] * 20  # 1.5 s of speech, then a long pause
    sentence, _peak, _noise, heard = vc.detect(feed(levels), wait=5, max_seconds=6, stop=None)
    assert heard
    assert len(sentence) <= vc.LEAD_FRAMES + 15 + vc.TRAIL_FRAMES  # the pause is cut to 0.3 s
    assert len(sentence) * vc.FRAME_SECONDS < 2.5


async def test_the_chime_is_silent_in_tests_and_when_voice_is_off(monkeypatch):
    voice = Voice(runner=lambda *a: None)
    await voice.chime("wake")  # no runner-less sound in a test
    monkeypatch.setenv("RAGNAROK_NO_VOICE", "1")
    await Voice().chime("wake")  # voice switched off: no sound either


async def test_nothing_after_the_phrase_says_so_instead_of_staying_silent(tmp_path, vault_key):
    done = threading.Event()
    app, rt, out, _ = build_app(tmp_path, vault_key, [], [])
    lines = ["/voice wake on"]

    def read(prompt):
        if lines:
            return lines.pop(0)
        time.sleep(1.0)
        return "/quit"

    app.read_line = read
    app.voice = wake_voice([], done, Mic(clip("hey ragnarok")))  # then silence
    await app.run()
    text = shown(out)
    assert "Yes? I'm listening" in text and "I did not hear a request" in text
    rt.vault.close()


async def test_the_cat_window_is_told_it_is_idle_again_after_a_voice_request(tmp_path, vault_key):
    """Regression: after a wake request the window stayed on "thinking" for ever, because the
    typing wait kept running and so never announced that the engine was ready again."""
    done = threading.Event()
    app, rt, _out, _ = build_app(tmp_path, vault_key, [say("It is noon.")], [])
    bridge = Bridge()
    app.c = GuiConsole(bridge)
    lines = ["/voice wake on"]

    def read(prompt):
        if lines:
            return lines.pop(0)
        done.wait(10)
        time.sleep(0.3)
        return "/quit"

    app.read_line = read
    app.voice = wake_voice([], done, Mic(clip("hey ragnarok what time is it")))
    await app.run()
    kinds = [k for k, _ in bridge.drain()]
    assert "heard" in kinds and "assistant_start" in kinds
    after_answer = kinds[kinds.index("assistant_start") :]
    assert "idle" in after_answer  # the window can stop thinking and take the next message
    rt.vault.close()
