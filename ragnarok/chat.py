"""The interactive chat screen: welcome, prompt, streamed answers, slash commands.

Speed choices (each one measured, see docs/WORKLOG.md):
  - the models warm up in the background while the welcome screen is already usable;
  - the Start-menu app list loads in the background (it takes about 2 s the first time);
  - learning from the conversation happens while you are idle and steps aside the moment you
    type, so leaving is instant;
  - an action tool's own sentence ends the turn without a second model call.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from typing import Protocol

import httpx

from ragnarok.memory.ingest import Ingestor
from ragnarok.memory.service import MemoryService
from ragnarok.memory.store import MemoryStore
from ragnarok.policy.permissions import Decision
from ragnarok.runtime.gateway import CHAT_MODEL, OLLAMA_URL, Gateway
from ragnarok.runtime.loop import Agent, Session
from ragnarok.runtime.tools import Tool, memory_tools
from ragnarok.skills.apps import WindowsApps, app_tools
from ragnarok.skills.files import file_tools
from ragnarok.ui import Console, Welcome

LEARN_AFTER_SECONDS = 15.0  # idle time before the background learner starts (read at run time)


class ChatRuntime(Protocol):
    store: MemoryStore
    svc: MemoryService
    ingestor: Ingestor
    gateway: Gateway


class BackgroundLearner:
    """Turns the conversation into memories during idle time; cancelled when you type again.

    Cancelling is safe: events are marked done only after a batch finishes, and learning the same
    event twice is recognised and reinforced instead of duplicated.
    """

    def __init__(
        self,
        ingestor: Ingestor,
        delay: float,
        maintenance: Callable[[], Awaitable[object]] | None = None,
    ) -> None:
        self.ingestor = ingestor
        self.delay = delay
        self.maintenance = maintenance  # tidy-up (summaries, pruning); decides itself if it is due
        self._task: asyncio.Task | None = None
        self.runs = 0

    def schedule(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        await asyncio.sleep(self.delay)
        await self.ingestor.ingest_pending()
        if self.maintenance is not None:
            with contextlib.suppress(Exception):  # tidying must never disturb the chat
                await self.maintenance()
        self.runs += 1

    async def cancel(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        self._task = None


async def gpu_status(base_url: str = OLLAMA_URL, model: str = CHAT_MODEL) -> str:
    """'100% GPU' once the chat model is loaded and fully on the GPU, else a short reason."""
    try:
        async with httpx.AsyncClient(timeout=0.6) as client:
            loaded = (await client.get(f"{base_url}/api/ps")).json().get("models", [])
    except Exception:
        return "offline"
    for m in loaded:
        if m["name"].split(":")[0] == model.split(":")[0] and m["size"]:
            return f"{100 * m['size_vram'] / m['size']:.0f}% GPU"
    return "loading"


class ChatApp:
    def __init__(
        self,
        rt: ChatRuntime,
        console: Console,
        *,
        folder: str,
        read_line: Callable[[str], str] | None = None,
        approve_line: Callable[[str], str] | None = None,
        tools: list[Tool] | None = None,
        learn_delay: float | None = None,
        maintenance: Callable[[], Awaitable[object]] | None = None,
    ) -> None:
        self.rt = rt
        self.c = console
        self.folder = folder
        self.read_line = read_line
        self.approve_line = approve_line
        self._catalog = WindowsApps() if tools is None else None
        if tools is None:
            tools = memory_tools(rt.svc, rt.ingestor) + app_tools(self._catalog) + file_tools()
        self.agent = Agent(
            rt.store, rt.svc, rt.ingestor, rt.gateway, approve=self._approve, tools=tools
        )
        self.session = Session()
        self.learner = BackgroundLearner(
            rt.ingestor,
            LEARN_AFTER_SECONDS if learn_delay is None else learn_delay,
            maintenance,
        )
        self._background: list[asyncio.Task] = []

    # ---- approvals ---------------------------------------------------------------------------

    async def _approve(self, name: str, args: dict, decision: Decision) -> bool:
        detail = ", ".join(f"{k}={v!r}" for k, v in args.items())
        prompt = f"   {self.c.amber('?')} allow {self.c.teal(name)} {self.c.faint(detail)} [y/N] "
        answer = await asyncio.to_thread(self.approve_line or input, prompt)
        return answer.strip().lower() in ("y", "yes")

    # ---- the session -------------------------------------------------------------------------

    async def run(self) -> None:
        self._start_background_work()
        count = await asyncio.to_thread(self.rt.store.count)
        gpu = await gpu_status()
        self.c.banner(Welcome(CHAT_MODEL, count, gpu, self.folder, first_run=count == 0))
        self.learner.schedule()  # catch up on earlier conversations while the user reads

        try:
            while True:
                try:
                    line = (
                        await asyncio.to_thread(self.read_line or input, self.c.prompt())
                    ).strip()
                except (EOFError, KeyboardInterrupt):
                    self.c.write()
                    break
                if not line:
                    continue
                if line.startswith("/"):
                    if await self._slash(line):
                        break
                    continue
                await self._turn(line)
        finally:
            await self.learner.cancel()
            for task in self._background:
                task.cancel()
        await self._goodbye()

    def _start_background_work(self) -> None:
        async def warm() -> None:
            with contextlib.suppress(Exception):
                await self.rt.gateway.warm_up()

        async def preload_apps() -> None:
            if self._catalog is not None:
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(self._catalog.list_apps)

        self._background = [asyncio.create_task(warm()), asyncio.create_task(preload_apps())]

    async def _turn(self, text: str) -> None:
        await self.learner.cancel()  # you first: stop any background learning
        spinner = self.c.spinner()
        spinner.start()

        async def show(chunk: str) -> None:
            if spinner.active:
                done = bool(self.agent.current and self.agent.current.final_tool)
                await spinner.stop("done" if done else "bot")
            self.c.out.write(chunk.replace("\n", "\n   "))
            self.c.out.flush()

        result = await self.agent.turn(self.session, text, on_delta=show)
        if spinner.active:  # nothing was streamed (an error, or an empty reply)
            await spinner.stop("warn" if result.stopped else "bot")
            self.c.out.write(result.text or self.c.faint("(no reply)"))
        self.c.write()
        self.c.footer([(t.tool, t.allowed) for t in result.trace], result.total_ms / 1000)
        self.c.write()
        self.learner.schedule()

    # ---- slash commands ----------------------------------------------------------------------

    async def _slash(self, line: str) -> bool:
        cmd, _, arg = line.partition(" ")
        store = self.rt.store
        if cmd in ("/quit", "/exit", "/q"):
            return True
        if cmd == "/help":
            self.c.help()
        elif cmd == "/memories":
            self.c.memories(await asyncio.to_thread(store.list_memories, limit=30))
        elif cmd == "/profile":
            self.c.write()
            for text_line in self.agent.profile.get()[0].splitlines():
                self.c.write("   " + self.c.violet(text_line))
            self.c.write()
        elif cmd == "/forget" and arg.strip().isdigit():
            gone = await asyncio.to_thread(store.delete, int(arg))
            if gone:
                self.c.success(f"Forgot m{int(arg)}.")
            else:
                self.c.error("No such memory.")
        elif cmd == "/clear":
            self.c.clear()
        else:
            self.c.error(f"Unknown command {cmd}. Try /help.")
        return False

    async def _goodbye(self) -> None:
        pending = await asyncio.to_thread(self.rt.store.pending_events, 1)
        note = "I'll finish learning from this chat next time." if pending else "See you soon."
        self.c.write()
        self.c.notice(note)
        self.c.write()
