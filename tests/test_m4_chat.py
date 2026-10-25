"""The chat screen: welcome, streaming, markers, slash commands, and the background learner."""

import asyncio
import re
import time
from types import SimpleNamespace

import httpx

from ragnarok import chat as chat_mod
from ragnarok.chat import BackgroundLearner, ChatApp, gpu_status
from ragnarok.memory.ingest import Ingestor
from ragnarok.runtime.gateway import ChatResult, GatewayError
from ragnarok.runtime.tools import memory_tools
from ragnarok.ui import Console
from tests.test_m2_memory import key, make  # noqa: F401  (fixture)
from tests.test_m3_ingest import ScriptedLlm
from tests.test_m4_agent import call, say
from tests.test_m4_cli import FakeGateway
from tests.test_m4_ui import Stream

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def screen(tmp_path, key, replies=(), lines=(), *, color=False, learn_delay=60.0, ingestor=None):  # noqa: F811
    vault, store, svc, emb = make(tmp_path, key, "exact")
    gateway = FakeGateway(chat_replies=replies)
    ing = ingestor or Ingestor(store, emb, ScriptedLlm({}), sim_floor=0.4)
    rt = SimpleNamespace(vault=vault, store=store, svc=svc, ingestor=ing, gateway=gateway)
    out = Stream()
    typed = iter(lines)

    def read(prompt):
        return next(typed)

    app = ChatApp(
        rt,
        Console(out, color=color),
        folder="C:\\data",
        read_line=read,
        approve_line=lambda prompt: "n",
        tools=memory_tools(svc, ing),
        learn_delay=learn_delay,
    )
    return app, rt, out


def text(out) -> str:
    return ANSI.sub("", out.getvalue())


async def test_first_run_welcome_and_goodbye(tmp_path, key):  # noqa: F811
    app, rt, out = screen(tmp_path, key, lines=["/quit"])
    await app.run()
    shown = text(out)
    assert "Welcome." in shown and "0 memories" in shown and "encrypted" in shown
    assert "open steam" in shown  # the first-run hint
    assert "See you soon." in shown
    assert rt.gateway.warmed == 1  # the models were warmed in the background
    rt.vault.close()


async def test_returning_user_sees_their_memory_count(tmp_path, key):  # noqa: F811
    app, rt, out = screen(tmp_path, key, lines=["/quit"])
    await rt.svc.remember("Prefers meetings after 14:00", "preference", "chat")
    await app.run()
    shown = text(out)
    assert "Welcome back." in shown and "1 memory" in shown
    rt.vault.close()


async def test_answer_streams_with_marker_and_footer_with_the_time(tmp_path, key):  # noqa: F811
    app, rt, out = screen(
        tmp_path, key, replies=[say("Line one.\nLine two.")], lines=["hello", "/quit"]
    )
    await app.run()
    shown = text(out)
    assert " ◆ Line one.\n   Line two." in shown  # continuation lines are indented under the text
    assert re.search(r"^ {3}\d+\.\ds$", shown, re.M)  # a dim "0.0s" footer
    rt.vault.close()


async def test_finished_action_gets_a_check_mark_and_the_tool_in_the_footer(tmp_path, key):  # noqa: F811
    app, rt, out = screen(
        tmp_path,
        key,
        replies=[call("memory_save", content="Prefers tea", memory_type="preference")],
        lines=["remember I prefer tea", "/quit"],
    )
    await app.run()
    shown = text(out)
    assert " ✓ Saved: Prefers tea [m:1]" in shown
    assert "↳ memory_save" in shown
    rt.vault.close()


async def test_colour_output_has_the_check_in_green(tmp_path, key):  # noqa: F811
    app, rt, out = screen(
        tmp_path,
        key,
        replies=[call("memory_save", content="Prefers tea", memory_type="preference")],
        lines=["remember I prefer tea", "/quit"],
        color=True,
    )
    await app.run()
    assert "\x1b[1;38;5;114m✓" in out.getvalue()  # bold green check
    assert "\x1b[38;5;75m" in out.getvalue() or "\x1b[1;38;5;75m" in out.getvalue()  # sky prompt
    rt.vault.close()


async def test_model_failure_shows_a_warning_marker_and_keeps_going(tmp_path, key):  # noqa: F811
    app, rt, out = screen(
        tmp_path,
        key,
        replies=[GatewayError("down"), say("Back now.")],
        lines=["hi", "hello again", "/quit"],
    )
    await app.run()
    shown = text(out)
    assert "I can't reach my language model right now" in shown
    assert "Back now." in shown  # the next message still works
    rt.vault.close()


async def test_empty_reply_is_shown_as_such(tmp_path, key):  # noqa: F811
    app, rt, out = screen(tmp_path, key, replies=[ChatResult(content="")], lines=["hmm", "/quit"])
    await app.run()
    assert "(no reply)" in text(out)
    rt.vault.close()


async def test_slash_commands(tmp_path, key):  # noqa: F811
    app, rt, out = screen(
        tmp_path, key, lines=["/help", "/memories", "/profile", "/forget 99", "/nope", "", "/quit"]
    )
    await rt.svc.remember("Dentist is Dr Rao", "episodic", "chat")
    await app.run()
    shown = text(out)
    assert "Commands" in shown and "/forget" in shown
    assert "m1" in shown and "episodic" in shown and "Dentist is Dr Rao" in shown
    assert "No such memory." in shown
    assert "Unknown command /nope" in shown
    rt.vault.close()


async def test_forget_deletes_for_good(tmp_path, key):  # noqa: F811
    app, rt, out = screen(tmp_path, key, lines=["/forget 1", "/memories", "/quit"])
    await rt.svc.remember("Dentist is Dr Rao", "episodic", "chat")
    await app.run()
    shown = text(out)
    assert "Forgot m1." in shown and "Nothing remembered yet" in shown
    assert rt.store.get(1) is None
    rt.vault.close()


async def test_a_blocked_tool_is_shown_in_the_footer(tmp_path, key):  # noqa: F811
    app, rt, out = screen(
        tmp_path,
        key,
        replies=[
            call("memory_save", content="x y z", memory_type="preference"),
            say("I could not."),
        ],
        lines=["remember xyz", "/quit"],
    )
    app.agent.profile_name = "background"  # writes are not allowed unasked in this profile
    await app.run()
    assert "memory_save blocked" in text(out) and rt.store.list_memories() == []
    rt.vault.close()


# ---- the background learner ------------------------------------------------------------------


async def test_learner_runs_while_the_user_is_idle(tmp_path, key):  # noqa: F811
    app, rt, out = screen(
        tmp_path, key, replies=[say("Hi!")], lines=["hello", "idle", "/quit"], learn_delay=0.02
    )
    original = app.read_line

    def slow(prompt):
        line = original(prompt)
        if line == "idle":
            time.sleep(0.3)
            line = "/memories"
        return line

    app.read_line = slow
    await app.run()
    assert app.learner.runs >= 1 and rt.store.pending_events() == []
    assert "See you soon." in text(out)
    rt.vault.close()


async def test_typing_cancels_the_learner_so_the_user_never_waits(tmp_path, key):  # noqa: F811
    started, cancelled = asyncio.Event(), []

    class SlowIngestor:
        async def ingest_pending(self, limit=40):
            started.set()
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                cancelled.append(True)
                raise

    learner = BackgroundLearner(SlowIngestor(), delay=0.01)
    learner.schedule()
    await asyncio.wait_for(started.wait(), 2)
    t = time.perf_counter()
    await learner.cancel()  # what a new message does first
    assert cancelled == [True] and time.perf_counter() - t < 1


async def test_leaving_with_unlearned_events_says_so(tmp_path, key):  # noqa: F811
    app, rt, out = screen(
        tmp_path, key, replies=[say("Hi!")], lines=["hello", "/quit"], learn_delay=60
    )
    await app.run()
    assert "finish learning from this chat next time" in text(out)
    assert len(rt.store.pending_events()) == 2  # nothing was lost by leaving quickly
    rt.vault.close()


async def test_nothing_waits_on_the_learner_at_exit(tmp_path, key):  # noqa: F811
    class Never:
        async def ingest_pending(self, limit=40):
            await asyncio.sleep(60)

    app, rt, out = screen(
        tmp_path,
        key,
        replies=[say("Hi!")],
        lines=["hello", "/quit"],
        learn_delay=0.01,
        ingestor=Never(),
    )
    t = time.perf_counter()
    await asyncio.wait_for(app.run(), 5)
    assert time.perf_counter() - t < 3
    rt.vault.close()


# ---- gpu status --------------------------------------------------------------------------------


async def test_gpu_status_variants(monkeypatch):
    real = httpx.AsyncClient

    def with_models(models):
        def handler(request):
            return httpx.Response(200, json={"models": models})

        monkeypatch.setattr(
            chat_mod.httpx,
            "AsyncClient",
            lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
        )

    with_models([{"name": "qwen3.5:4b", "size": 100, "size_vram": 100}])
    assert await gpu_status("http://x") == "100% GPU"
    with_models([{"name": "qwen3.5:4b", "size": 100, "size_vram": 60}])
    assert await gpu_status("http://x") == "60% GPU"
    with_models([{"name": "nomic-embed-text:latest", "size": 10, "size_vram": 10}])
    assert await gpu_status("http://x") == "loading"
    monkeypatch.setattr(chat_mod.httpx, "AsyncClient", real)
    assert await gpu_status("http://127.0.0.1:9") == "offline"


async def test_maintenance_runs_after_learning_and_its_failures_stay_quiet():
    calls = []

    class Ing:
        async def ingest_pending(self, limit=40):
            calls.append("learn")

    async def maintenance():
        calls.append("maintain")

    learner = BackgroundLearner(Ing(), delay=0.01, maintenance=maintenance)
    learner.schedule()
    await asyncio.sleep(0.15)
    assert calls == ["learn", "maintain"] and learner.runs == 1

    async def broken():
        raise RuntimeError("model down")

    failing = BackgroundLearner(Ing(), delay=0.01, maintenance=broken)
    failing.schedule()
    await asyncio.sleep(0.15)
    assert failing.runs == 1  # the chat was never disturbed
