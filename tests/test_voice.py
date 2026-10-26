"""Voice: text to speech and speech to text with the Windows speech engine (no network)."""

import asyncio
import os

import pytest

from ragnarok.voice import (
    MAX_SPOKEN_SENTENCES,
    RECOGNIZE_SCRIPT,
    SPEAK_SCRIPT,
    Voice,
    VoiceError,
    is_stop,
    speakable,
)
from ragnarok.voice_capture import MicError, Recording
from tests.test_chat_flow import build_app, shown
from tests.test_m4_agent import say


@pytest.fixture()
def vault_key() -> bytes:
    return os.urandom(32)


class FakeEngine:
    """Stands in for PowerShell and the microphone: records what was asked and what was heard."""

    def __init__(self, heard=(), speak_code=0, listen_code=0, level=0.2):
        self.heard = list(heard)
        self.speak_code, self.listen_code, self.level = speak_code, listen_code, level
        self.log: list[tuple[str, str]] = []  # ("speak", text) or ("listen", "")
        self.envs: list[dict[str, str]] = []
        self.scripts: list[str] = []
        self.audio: list[bytes] = []

    def record(self, wait, max_seconds, device=None, stop=None) -> Recording:
        """The microphone: the next scripted sentence, or silence at the given level."""
        self.log.append(("listen", ""))
        if self.listen_code == 4:
            raise MicError("no microphone was found")
        text = self.heard[0] if self.heard else ""
        if not text:
            if self.heard:
                self.heard.pop(0)
            return Recording(device="Test mic", peak_percent=self.level)
        return Recording(
            pcm=b"pcm", heard_speech=True, device="Test mic", peak_percent=20, seconds=1.5
        )

    async def __call__(self, script, env, stdin=None):
        self.scripts.append(script)
        self.envs.append(env)
        if stdin is not None:  # the recogniser: it gets the audio, answers "confidence\ntext"
            self.audio.append(stdin)
            if self.listen_code:
                return self.listen_code, "", "boom"
            return 0, f"0.9\n{self.heard.pop(0)}", ""
        self.log.append(("speak", env["RAG_TTS_TEXT"]))
        return self.speak_code, "", "speech failed"


def make_voice(engine, **kw):
    return Voice(runner=engine, recorder=engine.record, **kw)


# ---- cleaning text for the ear ---------------------------------------------------------------


def test_code_links_paths_and_markdown_are_not_read_aloud():
    text = (
        "Here is **the** result:\n```python\nprint(1)\n```\nSee https://example.com/x and "
        "C:\\Users\\me\\notes.txt for `details`."
    )
    spoken = speakable(text)
    assert "print" not in spoken and "https" not in spoken and "Users" not in spoken
    assert "*" not in spoken and "`" not in spoken
    assert "a link" in spoken and "a file path" in spoken and "details" in spoken
    assert "the code is on the screen" in spoken


def test_a_long_answer_is_cut_to_a_few_sentences_and_a_length():
    text = " ".join(f"Sentence number {i} says something." for i in range(30))
    spoken = speakable(text)
    assert spoken.count("Sentence") == MAX_SPOKEN_SENTENCES
    assert len(speakable("word " * 400)) <= 601


def test_nothing_to_say_is_empty():
    assert speakable("   ") == ""
    assert speakable("✓ ● →") == ""


def test_stop_phrases():
    assert is_stop("Stop listening.") and is_stop("ok stop listening now")
    assert not is_stop("what is the capital of france")


# ---- speaking and listening ---------------------------------------------------------------------


async def test_say_passes_the_text_in_the_environment_never_in_the_script():
    engine = FakeEngine()
    voice = make_voice(engine, name="Microsoft Zira Desktop", rate=2)
    hostile = '"; Remove-Item C:\\ -Recurse #'
    await voice.say(f"Hello there. {hostile}")
    env = engine.envs[0]
    assert env["RAG_TTS_VOICE"] == "Microsoft Zira Desktop" and env["RAG_TTS_RATE"] == "2"
    assert "Hello there" in env["RAG_TTS_TEXT"]
    assert engine.scripts[0] == SPEAK_SCRIPT and "Remove-Item" not in engine.scripts[0]


async def test_a_failing_speech_engine_is_reported_not_raised_as_a_crash():
    voice = make_voice(FakeEngine(speak_code=1))
    with pytest.raises(VoiceError, match="could not speak"):
        await voice.say("hello")


async def test_listen_returns_the_sentence_and_waits_for_speech_to_finish():
    engine = FakeEngine(heard=["  open steam  "])
    voice = make_voice(engine)
    voice.say_in_background("a long answer is being read")
    assert await voice.listen() == "open steam"
    assert [kind for kind, _ in engine.log] == ["speak", "listen"]  # never listens to itself
    assert engine.scripts[-1] == RECOGNIZE_SCRIPT and engine.audio == [b"pcm"]


@pytest.mark.parametrize(
    ("code", "words"), [(4, "no microphone"), (3, "no speech recogniser"), (9, "could not listen")]
)
async def test_listen_explains_why_it_could_not(code, words):
    with pytest.raises(VoiceError, match=words):
        await make_voice(FakeEngine(heard=["hello"], listen_code=code)).listen()


async def test_silence_is_an_empty_string():
    assert await make_voice(FakeEngine()).listen() == ""


async def test_a_new_answer_cuts_the_old_one_short():
    started, finished = [], []

    async def slow(script, env):
        started.append(env["RAG_TTS_TEXT"])
        await asyncio.sleep(0.3)
        finished.append(env["RAG_TTS_TEXT"])
        return 0, "", ""

    voice = Voice(runner=lambda script, env, stdin=None: slow(script, env))
    voice.say_in_background("first answer")
    await asyncio.sleep(0.05)
    voice.say_in_background("second answer")
    await voice.finish_speaking()
    assert started == ["first answer", "second answer"] and finished == ["second answer"]


def test_voice_can_be_turned_off_completely(monkeypatch):
    monkeypatch.setenv("RAGNAROK_NO_VOICE", "1")
    assert not make_voice(FakeEngine()).available


async def test_unavailable_voice_says_so(monkeypatch):
    monkeypatch.setenv("RAGNAROK_NO_VOICE", "1")
    with pytest.raises(VoiceError, match="not available"):
        await make_voice(FakeEngine()).say("hello")


# ---- in the chat ---------------------------------------------------------------------------------


def with_voice(app, engine):
    app.voice = make_voice(engine)
    return app


async def test_voice_on_reads_answers_aloud_and_voice_off_stops(tmp_path, vault_key):
    app, rt, out, _ = build_app(
        tmp_path, vault_key, [say("It is **Paris**, see https://x.org."), say("Quiet now.")],
        ["/voice on", "capital of france", "/voice off", "again", "/quit"],
    )  # fmt: skip
    engine = FakeEngine()
    with_voice(app, engine)
    await app.run()
    await app.voice.finish_speaking()
    spoken = [text for kind, text in engine.log if kind == "speak"]
    assert len(spoken) == 1 and "Paris" in spoken[0] and "https" not in spoken[0]
    assert "I will read my answers aloud" in shown(out) and "Voice output is off" in shown(out)
    rt.vault.close()


async def test_listen_turns_one_spoken_sentence_into_a_message(tmp_path, vault_key):
    app, rt, out, _ = build_app(
        tmp_path, vault_key, [say("Four.")], ["/listen", "/quit"]
    )  # fmt: skip
    engine = FakeEngine(heard=["what is two plus two"])
    with_voice(app, engine)
    await app.run()
    text = shown(out)
    assert "Listening... speak now." in text and "heard: what is two plus two" in text
    assert "Four." in text and ("listen", "") in engine.log
    assert any(m["content"] == "what is two plus two" for m in app.session.history)
    rt.vault.close()


async def test_listen_with_silence_says_so_and_sends_nothing(tmp_path, vault_key):
    app, rt, out, _ = build_app(tmp_path, vault_key, [], ["/listen", "/quit"])
    with_voice(app, FakeEngine())
    await app.run()
    assert "almost silent" in shown(out) and app.session.history == []
    rt.vault.close()


async def test_hands_free_listens_answers_aloud_then_listens_again_until_you_say_stop(
    tmp_path, vault_key
):
    app, rt, out, _ = build_app(
        tmp_path, vault_key, [say("Four."), say("Nine.")], ["/voice talk", "/quit"]
    )  # fmt: skip
    engine = FakeEngine(heard=["what is two plus two", "and three times three", "stop listening"])
    with_voice(app, engine)
    await app.run()
    kinds = [kind for kind, _ in engine.log]
    assert kinds == [
        "listen",
        "speak",
        "listen",
        "speak",
        "listen",
    ]  # speaks BEFORE listening again
    assert "Voice mode is off." in shown(out) and not app._handsfree
    rt.vault.close()


async def test_two_silences_in_a_row_end_hands_free_mode(tmp_path, vault_key):
    app, rt, out, _ = build_app(tmp_path, vault_key, [], ["/voice talk", "/quit"])
    engine = FakeEngine(heard=["", ""])
    with_voice(app, engine)
    await app.run()
    assert [k for k, _ in engine.log].count("listen") == 2
    assert "heard nothing twice" in shown(out) and not app._handsfree
    rt.vault.close()


async def test_a_missing_microphone_is_explained_and_the_chat_carries_on(tmp_path, vault_key):
    app, rt, out, _ = build_app(
        tmp_path, vault_key, [say("Hi.")], ["/voice talk", "hello", "/quit"]
    )  # fmt: skip
    with_voice(app, FakeEngine(listen_code=4))
    await app.run()
    text = shown(out)
    assert "no microphone was found" in text and "Hi." in text and not app._handsfree
    rt.vault.close()


async def test_a_speech_failure_turns_voice_off_without_breaking_the_chat(tmp_path, vault_key):
    app, rt, out, _ = build_app(
        tmp_path, vault_key, [say("One."), say("Two.")], ["/voice talk", "/quit"]
    )  # fmt: skip
    engine = FakeEngine(heard=["hello"], speak_code=1)
    with_voice(app, engine)
    await app.run()
    assert "could not speak" in shown(out) and not app._handsfree and not app.voice.speaking
    rt.vault.close()


async def test_the_microphone_is_never_opened_unless_asked(tmp_path, vault_key):
    app, rt, out, _ = build_app(
        tmp_path, vault_key, [say("Hello!")], ["hi there", "/voice on", "/quit"]
    )  # fmt: skip
    engine = FakeEngine()
    with_voice(app, engine)
    await app.run()
    assert ("listen", "") not in engine.log
    rt.vault.close()


async def test_help_lists_the_voice_commands(tmp_path, vault_key):
    app, rt, out, _ = build_app(
        tmp_path, vault_key, [], ["/help", "/voice", "/voice what", "/quit"]
    )
    await app.run()
    text = shown(out)
    assert "/voice M" in text and "/listen" in text and "Usage: /voice on | off | talk" in text
    rt.vault.close()


# ---- what the microphone delivered -------------------------------------------------------------


async def test_a_low_confidence_result_is_dropped():
    engine = FakeEngine(heard=["mumble"])

    async def unsure(script, env, stdin=None):
        return 0, "0.05\nmumble", ""

    voice = Voice(runner=unsure, recorder=engine.record)
    assert await voice.listen() == ""


async def test_a_silent_microphone_is_told_apart_from_a_quiet_room():
    silent = make_voice(FakeEngine(level=0.1))
    assert await silent.listen() == ""
    assert "almost silent" in silent.why_nothing() and "RAGNAROK_MIC" in silent.why_nothing()
    room = make_voice(FakeEngine(level=3.0))
    assert await room.listen() == ""
    assert "picked up sound" in room.why_nothing() and "no speech started" in room.why_nothing()


async def test_speech_that_could_not_be_read_says_so():
    engine = FakeEngine(heard=["words"])

    async def unsure(script, env, stdin=None):
        return 0, "0.05\nwords", ""

    voice = Voice(runner=unsure, recorder=engine.record)
    await voice.listen()
    assert "could not make out the words" in voice.why_nothing()


async def test_diagnose_reports_the_microphone_level_and_the_text():
    engine = FakeEngine(heard=["open steam"])
    report = await make_voice(engine).diagnose()
    assert "microphones:" in report and 'understood: "open steam"' in report
    assert "heard 1.5s of speech" in report


async def test_voice_test_in_the_chat(tmp_path, vault_key):
    app, rt, out, _ = build_app(tmp_path, vault_key, [], ["/voice test", "/quit"])
    with_voice(app, FakeEngine(level=0.1))
    await app.run()
    text = shown(out)
    assert "Microphone test" in text and "almost silent" in text and "understood: nothing" in text
    rt.vault.close()


# ---- recording and speech detection (no microphone needed) ---------------------------------------

import struct as _struct  # noqa: E402

from ragnarok import voice_capture as vc  # noqa: E402


def frame(level: int, n: int = vc.FRAME_SAMPLES) -> bytes:
    return _struct.pack(f"<{n}h", *([level, -level] * (n // 2)))


def feed(levels):
    items = iter(frame(level) for level in levels)
    return lambda: next(items, None)


def test_speech_after_a_quiet_start_is_found_and_ends_after_a_pause():
    quiet, loud = 40, 8000
    levels = [quiet] * 6 + [loud] * 8 + [quiet] * 14 + [loud] * 5  # the last burst comes too late
    sentence, peak, noise, heard = vc.detect(feed(levels), wait=5, max_seconds=5, stop=None)
    assert heard and noise < 0.5 and 20 < peak < 30
    assert 8 <= len(sentence) <= 8 + vc.LEAD_FRAMES + vc.END_SILENCE_FRAMES


def test_a_quiet_room_is_not_speech():
    sentence, peak, noise, heard = vc.detect(feed([40] * 200), wait=0.5, max_seconds=5, stop=None)
    assert not heard and sentence == [] and peak < 0.5


def test_quiet_speech_is_amplified_but_never_past_the_limit_or_into_clipping():
    from array import array

    samples = array("h", [100, -120, 90] * 100)
    boosted, gain = vc.amplify(samples, 120)
    assert 1 < gain <= vc.MAX_GAIN and max(abs(x) for x in boosted) <= 32767
    loud = array("h", [30000, -30000])
    assert vc.amplify(loud, 30000)[1] == 1.0
    assert vc.amplify(array("h", [0, 0]), 0)[1] == 1.0


def test_the_microphone_is_chosen_by_number_or_by_name(monkeypatch):
    monkeypatch.setattr(vc, "devices", lambda: ["Headset (Buds)", "Microphone Array (Intel)"])
    assert vc.choose_device("") == vc.WAVE_MAPPER
    assert vc.choose_device("1") == 1
    assert vc.choose_device("array") == 1
    with pytest.raises(MicError, match="no microphone matches"):
        vc.choose_device("usb")


def test_the_level_report_names_the_microphone():
    silent = Recording(device="Buds", peak_percent=0.2)
    assert "almost silent" in vc.level_report(silent) and "Buds" in vc.level_report(silent)
    spoke = Recording(device="Mic", heard_speech=True, seconds=2.0, peak_percent=30, gain=2.0)
    assert "heard 2.0s of speech" in vc.level_report(spoke)


def test_very_quiet_speech_gets_a_volume_warning():
    quiet = Recording(device="Mic", heard_speech=True, seconds=2.0, peak_percent=1.2, gain=40.0)
    assert "very quiet" in vc.level_report(quiet) and "input volume" in vc.level_report(quiet)
    fine = Recording(device="Mic", heard_speech=True, seconds=2.0, peak_percent=30, gain=2.0)
    assert "very quiet" not in vc.level_report(fine)


# ---- Whisper ------------------------------------------------------------------------------

from types import SimpleNamespace as _NS  # noqa: E402

from ragnarok import voice_whisper as vw  # noqa: E402


async def test_whisper_text_is_used_when_a_transcriber_is_set():
    engine = FakeEngine(heard=["ignored by the windows recogniser"])
    voice = Voice(runner=engine, recorder=engine.record, transcriber=lambda pcm: "  open steam  ")
    assert await voice.listen() == "open steam"
    assert engine.audio == []  # the Windows recogniser was not asked


async def test_a_model_that_cannot_load_is_reported_plainly():
    def broken(pcm):
        raise vw.WhisperUnavailable("the speech model is not downloaded")

    engine = FakeEngine(heard=["hello"])
    voice = Voice(runner=engine, recorder=engine.record, transcriber=broken)
    with pytest.raises(VoiceError, match="not downloaded"):
        await voice.listen()


def fake_model(*segments):
    class Model:
        def transcribe(self, audio, **options):
            Model.options = options
            return iter(segments), None

    return Model()


def segment(text, no_speech=0.01, logprob=-0.2):
    return _NS(text=text, no_speech_prob=no_speech, avg_logprob=logprob)


def test_whisper_keeps_real_speech_and_drops_guesses_and_silence(monkeypatch):
    model = fake_model(
        segment(" Open steam."), segment(" thanks for watching", no_speech=0.9),
        segment(" mmm", logprob=-2.5), segment(" and tell me the time."),
    )  # fmt: skip
    monkeypatch.setattr(vw, "_load", lambda name=None: model)
    pcm = b"\x10\x00" * 16000
    assert vw.transcribe(pcm) == "Open steam. and tell me the time."
    assert model.options["language"] == "en" and model.options["vad_filter"] is False


def test_whisper_ignores_a_blip_shorter_than_a_tenth_of_a_second(monkeypatch):
    monkeypatch.setattr(
        vw, "_load", lambda name=None: (_ for _ in ()).throw(AssertionError("not needed"))
    )
    assert vw.transcribe(b"\x01\x00" * 100) == ""


def test_whisper_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("RAGNAROK_STT", "windows")
    assert not vw.enabled()
    monkeypatch.setenv("RAGNAROK_STT", "whisper")
    assert vw.enabled()  # faster-whisper is installed in this project


def test_the_model_is_stored_outside_onedrive(monkeypatch, tmp_path):
    monkeypatch.setenv("RAGNAROK_HOME", str(tmp_path))
    assert vw.models_dir() == tmp_path / "models"
