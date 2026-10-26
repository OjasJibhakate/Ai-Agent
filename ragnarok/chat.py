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
import os
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Protocol

import httpx

from ragnarok.memory.ingest import Ingestor
from ragnarok.memory.service import MemoryService
from ragnarok.memory.store import MemoryStore
from ragnarok.planning.intent import looks_multi_step
from ragnarok.planning.models import Plan, PlanError
from ragnarok.planning.runner import PlanEvent, PlanRunner, default_file_probe
from ragnarok.planning.store import PlanStore
from ragnarok.policy.modes import MODES, SUMMARY, Mode
from ragnarok.policy.permissions import Decision
from ragnarok.policy.permissions import check as check_permission
from ragnarok.policy.roles import DEFAULT_ROLE, ROLES, rank
from ragnarok.recovery.breaker import CircuitBreaker
from ragnarok.recovery.restore import remember_session, restore_state
from ragnarok.runtime.explain import latest_plan_id, plan_steps, recent_tool_calls
from ragnarok.runtime.gateway import CHAT_MODEL, OLLAMA_URL, Gateway
from ragnarok.runtime.loop import Agent, Session
from ragnarok.runtime.tools import PROGRESS, Tool, memory_tools
from ragnarok.skills.apps import WindowsApps, app_tools
from ragnarok.skills.digest import digest_tools
from ragnarok.skills.documents import document_tools
from ragnarok.skills.downloads import download_tools
from ragnarok.skills.fileops import file_op_tools
from ragnarok.skills.files import default_roots, file_tools
from ragnarok.skills.images import image_tools
from ragnarok.skills.mail import mail_tools
from ragnarok.skills.registry import SkillError, SkillRegistry, load_skill
from ragnarok.skills.screen.tools import screen_tools
from ragnarok.skills.shell import shell_tools
from ragnarok.skills.tasks import TaskBoard, task_tools
from ragnarok.skills.web import web_tools
from ragnarok.skills.webpage import webpage_tools
from ragnarok.triggers.daemon import mail_root
from ragnarok.triggers.engine import Delivered, TriggerEngine
from ragnarok.triggers.proactive import Proactive
from ragnarok.triggers.store import TriggerStore
from ragnarok.triggers.toast import toast
from ragnarok.triggers.tools import describe, trigger_tools
from ragnarok.ui import Console, Welcome

BUNDLED_SKILLS = Path(__file__).resolve().parent.parent / "skills"  # `/skill add text-stats`
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
        mode: Mode | None = None,
        role: str | None = None,
    ) -> None:
        self.rt = rt
        self.mode = mode or Mode.from_env(os.environ.get("RAGNAROK_MODE"))
        self.c = console
        self.folder = folder
        self.read_line = read_line
        self.approve_line = approve_line
        self._catalog = WindowsApps() if tools is None else None
        self.triggers = TriggerStore(rt.store.vault)
        self.breaker = CircuitBreaker()  # shared: a tool paused for one agent is paused for all
        self.plans = PlanStore(rt.store.vault)
        self.role = rt.store.meta_get("role") or DEFAULT_ROLE if role is None else role
        if self.role not in ROLES:
            self.role = DEFAULT_ROLE
        self._plan_id: str | None = None
        self._plan_titles: dict[str, str] = {}
        if tools is None:
            tools = (
                memory_tools(rt.svc, rt.ingestor)
                + trigger_tools(self.triggers, mode=self.mode)
                + app_tools(self._catalog)
                + file_tools(mode=self.mode)
                + file_op_tools(mode=self.mode)
                + shell_tools()
                + web_tools()
                + document_tools()
                + digest_tools(rt.gateway, mode=self.mode)
                + mail_tools(mail_root(Path(folder)))
                + task_tools(TaskBoard(rt.store.vault))
                + webpage_tools()
                + (
                    []
                    if os.environ.get("RAGNAROK_NO_DOWNLOADS") == "1"
                    else image_tools() + download_tools()
                )
                + ([] if os.environ.get("RAGNAROK_NO_SCREEN") == "1" else screen_tools(rt.gateway))
            )
        # Registered skills (FR2.1) become tools next to the built-in ones. Their names may not
        # shadow a built-in, and a skill whose files changed on disk is switched off here.
        builtin = {t.name for t in tools}
        self.skills = SkillRegistry(rt.store.vault, Path(folder), reserved=lambda: set(builtin))
        self._skill_names: set[str] = set()
        self._skill_notes: list[str] = []
        if self._catalog is not None:  # the default tool set; tests pass their own
            extra, self._skill_notes = self.skills.tools()
            tools = tools + extra
            self._skill_names = {t.name for t in extra}
        self.agent = Agent(
            rt.store,
            rt.svc,
            rt.ingestor,
            rt.gateway,
            profile=self.mode.profile,
            approve=self._approve,
            tools=tools,
            on_tool=self._tool_started,
            breaker=self.breaker,
            role=self.role,
        )
        self.session = Session()
        self.learner = BackgroundLearner(
            rt.ingestor,
            LEARN_AFTER_SECONDS if learn_delay is None else learn_delay,
            maintenance,
        )
        self._background: list[asyncio.Task] = []
        self.proactive = Proactive(rt.store, self.plans, self.triggers)
        self.engine = TriggerEngine(
            self.triggers,
            goal_runner=self._run_goal,
            listener=self._delivered,
            idle=lambda: self._spinner is None,  # background work waits for the user's own turn
            suggester=lambda: self.proactive.run_once(),
        )
        self._proposals: list[tuple[str, dict]] = []
        self._restored = None  # what restore_state found at the start (recovery/restore.py)
        self._spinner = None  # the "thinking" animation of the turn in progress
        self._open_line = False  # the cursor is in the middle of a streamed line
        self._need_marker = False  # the next streamed text starts a new message
        self._shown: list[str] = []  # everything streamed this turn

    # ---- approvals ---------------------------------------------------------------------------

    async def _between(self) -> None:
        """Make room for something that is not the answer (a question, a progress line): stop
        the animation, finish the line being streamed, and start a new message afterwards."""
        await self._pause_spinner()
        if self._open_line:
            self.c.write()
            self._open_line = False
        if self._spinner is None or not self._spinner.active:
            self._need_marker = True

    async def _tool_started(self, name: str) -> None:
        """A tool is about to run: show that work is going on, even after some text was shown."""
        spinner = self._spinner
        if spinner is None:
            return
        await self._between()
        if spinner.active:
            spinner.resume()
        else:
            spinner.label = "Working"
            spinner.start()
            self._need_marker = False  # the spinner prints the marker when text comes

    async def _approve(self, name: str, args: dict, decision: Decision) -> bool:
        await self._between()  # the animation would draw over the question
        tool = self.agent.tools.get(name)
        warning = (tool.approval_note if tool else "") or decision.reason
        prompt = self.c.approval(name, args, warning)
        try:
            answer = await asyncio.to_thread(self.approve_line or input, prompt)
        finally:
            self._resume_spinner()
        return answer.strip().lower() in ("y", "yes")

    # ---- things that happen by themselves (triggers) ------------------------------------------

    async def _delivered(self, item: Delivered) -> None:
        """A reminder came due, or a watched file arrived: tell the user now."""
        if self._spinner is not None:
            await self._between()
        self.c.alert(item.kind, item.title, item.body)
        if self.c.tty and self.c.color:
            self._background.append(asyncio.create_task(toast(item.title, item.body)))

    async def _headless_approve(self, name: str, args: dict, decision: Decision) -> bool:
        self._proposals.append((name, args))  # never runs: it waits for the owner
        return False

    async def _run_goal(self, goal: str, event: dict) -> str:
        """Work on a goal with nobody watching: read and think freely, change nothing."""
        helper = Agent(
            self.rt.store,
            self.rt.svc,
            self.rt.ingestor,
            self.rt.gateway,
            profile="background",
            approve=self._headless_approve,
            tools=list(self.agent.tools.values()),
            breaker=self.breaker,
            role=self.role,
        )
        self._proposals = []
        result = await helper.turn(Session(), goal)
        text = result.text.strip() or "(nothing to report)"
        if self._proposals:
            lines = [f"- {name}({', '.join(f'{k}={v!r}' for k, v in args.items())})"
                     for name, args in self._proposals]  # fmt: skip
            text += "\nWaiting for your OK (ask me and I will do it):\n" + "\n".join(lines)
        return text

    async def _pause_spinner(self) -> None:
        if self._spinner is not None:
            await self._spinner.pause()

    def _resume_spinner(self) -> None:
        if self._spinner is not None:
            self._spinner.resume()

    async def _note(self, text: str) -> None:
        """A progress line from a long-running tool (each step of a screen task)."""
        await self._between()
        self.c.write("   " + self.c.dim(self.c.g["tool"]) + "  " + self.c.faint(text))
        self._resume_spinner()

    # ---- plans (FR3) --------------------------------------------------------------------------

    def _plan_runner(self) -> PlanRunner:
        def make_agent(subset: list[Tool], ask) -> Agent:
            return Agent(
                self.rt.store, self.rt.svc, self.rt.ingestor, self.rt.gateway,
                profile=self.mode.profile, approve=ask, tools=subset, breaker=self.breaker,
                role=self.role,
            )  # fmt: skip

        return PlanRunner(
            self.plans,
            self.rt.gateway,
            self.agent.tools,
            make_agent,
            self._approve,
            self._plan_event,
            file_probe=default_file_probe(default_roots()),
        )

    async def _plan_event(self, event: PlanEvent) -> None:
        """Show a step changing state. The spinner is stopped for the line and restarted if
        the next thing is more work."""
        await self._between()
        if self._spinner is not None:
            self._spinner.active = False
            self._spinner = None
        sid = event.step_id or ""
        if event.kind == "replan" and self._plan_id:
            row = await asyncio.to_thread(self.plans.get, self._plan_id)
            if row is not None:
                self._plan_titles = {s.id: s.title for s in row.plan.steps}
            self.c.step_line("replan", "", "changing the plan", event.text)
            return
        if event.kind == "finished":
            return
        title = self._plan_titles.get(sid, "")
        state = "retry" if event.kind == "retry" else event.state
        self.c.step_line(state, sid, title, event.text)
        if state == "running":
            self._spinner = self.c.spinner(f"Working on {sid}")
            self._spinner.start()

    async def _plan_mode(self, goal: str, *, fallback: bool) -> bool:
        """Plan a goal, show the plan, and run it if the owner says yes. Returns True when this
        handled the request; False means the caller should answer it the ordinary way."""
        await self.learner.cancel()
        runner = self._plan_runner()
        self._spinner = self.c.spinner("Planning")
        self._spinner.start()
        try:
            plan, risks = await runner.propose(goal)
        except PlanError as exc:
            await self._between()
            self._spinner = None
            if fallback:
                self.c.notice("(I couldn't make a plan for that, so I'll just do it directly.)")
                return False
            self.c.error(str(exc))
            return True
        await self._between()
        self._spinner = None
        rows = []
        for step in plan.steps:
            asks = any(
                (t := self.agent.tools.get(n)) is not None
                and check_permission(self.mode.profile, n, t.risk, self.role).ask
                for n in step.uses
            )
            rows.append((step.id, step.title, step.after, risks[step.id].value, asks))
        prompt = self.c.plan_card(goal, rows)
        answer = await asyncio.to_thread(self.approve_line or input, prompt)
        if answer.strip().lower() not in ("y", "yes"):
            self.c.notice("Okay, I won't run it." if not fallback else "Okay, doing it directly.")
            return not fallback
        self._plan_titles = {s.id: s.title for s in plan.steps}
        self._plan_id = await asyncio.to_thread(self.plans.create, plan, self.session.id)
        self.c.write()
        await self._execute_plan(runner, goal, self._plan_id)
        return True

    async def _execute_plan(self, runner: PlanRunner, goal: str, plan_id: str) -> None:
        started = time.perf_counter()
        try:
            result = await runner.run(plan_id)
        finally:
            await self._between()
            self._spinner = None
        row = await asyncio.to_thread(self.plans.get, plan_id)
        summary = result.summary(row.plan if row else Plan(goal, []))
        first, _, rest = summary.partition("\n")
        marker = self.c.assistant_marker("done" if result.status == "done" else "warn")
        self.c.write()
        self.c.write(marker + first)
        for line in rest.splitlines():
            self.c.write("   " + line)
        self.c.write()
        self.c.footer([], time.perf_counter() - started)
        self.c.write()
        self.session.history += [
            {"role": "user", "content": goal},
            {"role": "assistant", "content": summary},
        ]
        self.learner.schedule()

    async def _resume_command(self) -> None:
        unfinished = await asyncio.to_thread(self.plans.unfinished)
        if not unfinished:
            self.c.notice("There is no unfinished plan.")
            return
        row = unfinished[-1]
        self._plan_id = row.id
        self._plan_titles = {s.id: s.title for s in row.plan.steps}
        steps = await asyncio.to_thread(self.plans.steps, row.id)
        left = [s for s in row.plan.steps if steps[s.id].state != "done"]
        risky = self._restored.risky if self._restored else []
        for call in risky:  # a call that was cut off and is not safe to repeat: ask first
            prompt = self.c.approval(
                call.tool,
                call.args,
                "it was running when the program stopped; doing it again may repeat its effect",
            )
            answer = await asyncio.to_thread(self.approve_line or input, prompt)
            if answer.strip().lower() not in ("y", "yes"):
                self.c.notice("Okay, I left that plan where it was.")
                return
        if self._restored:
            self._restored.in_flight.clear()
        self.c.notice(f"Carrying on with: {row.goal[:70]} ({len(left)} steps left)")
        await self._execute_plan(self._plan_runner(), row.goal, row.id)

    async def _restore(self) -> None:
        """After a crash or power loss: put the work and the conversation back (NFR3.2)."""
        idempotent = {name: t.idempotent for name, t in self.agent.tools.items()}
        report = await asyncio.to_thread(restore_state, self.rt.store, self.plans, idempotent)
        await asyncio.to_thread(remember_session, self.rt.store, self.session.id)
        self._restored = report
        if report.history:
            self.session.history = report.history
            self.c.notice(
                f"Picked up our last conversation ({len(report.history)} messages) "
                f"in {report.elapsed_ms:.0f} ms."
            )
        for call in report.risky:
            self.c.notice(
                f"{call.tool} was running when the program stopped and may be half done. "
                "I will ask before a plan does it again."
            )

    # ---- the session -------------------------------------------------------------------------

    async def run(self) -> None:
        self._start_background_work()
        count = await asyncio.to_thread(self.rt.store.count)
        gpu = await gpu_status()
        await self.c.play_banner(Welcome(CHAT_MODEL, count, gpu, self.folder, first_run=count == 0))
        if self.mode.name != "ask":
            self.c.notice(f"Mode: {self.mode.name} - {SUMMARY[self.mode.name]}. /mode changes it.")
        await self._restore()
        self.learner.schedule()  # catch up on earlier conversations while the user reads
        unread = await asyncio.to_thread(self.triggers.unread_count)
        if unread:
            self.c.notice(f"{unread} new in your Inbox. Type /inbox to read.")
        unfinished = await asyncio.to_thread(self.plans.unfinished)
        if unfinished:
            self.c.notice(
                f"A plan did not finish: {unfinished[-1].goal[:60]}. Type /resume to go on."
            )
        for note in self._skill_notes:
            self.c.notice(note)
        self.engine.start()

        try:
            while True:
                try:
                    line = (
                        await asyncio.to_thread(
                            self.read_line or input, self.c.prompt(f"{self.mode.name} mode")
                        )
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
                if looks_multi_step(line) and await self._plan_mode(line, fallback=True):
                    continue
                await self._turn(line)
        finally:
            await self.engine.stop()
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
        spinner = self._spinner = self.c.spinner()
        spinner.start()
        PROGRESS.set(self._note)

        self._open_line = False
        self._need_marker = False
        self._shown = []

        async def show(chunk: str) -> None:
            if spinner.active:
                done = bool(self.agent.current and self.agent.current.final_tool)
                await spinner.stop("done" if done else "bot")
            elif self._need_marker:
                self.c.out.write(self.c.assistant_marker("bot"))
            self._need_marker = False
            self.c.out.write(chunk.replace("\n", "\n   "))
            self.c.out.flush()
            self._shown.append(chunk)
            self._open_line = not chunk.endswith("\n")

        try:
            result = await self.agent.turn(self.session, text, on_delta=show)
        finally:
            if spinner.active and spinner._task is not None:
                await spinner.pause()  # never leave an animation running if the turn failed
        if spinner.active:  # nothing more was streamed (an error, or an empty reply)
            await spinner.stop("warn" if result.stopped else "bot")
            self.c.out.write(result.text or self.c.faint("(no reply)"))
        elif result.stopped and result.text.strip() not in "".join(self._shown):
            # the turn was cut short after some text had already been shown: say why
            if self._open_line:
                self.c.write()
            self.c.out.write(self.c.assistant_marker("warn") + result.text)
        self._spinner = None
        self._open_line = False
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
        elif cmd == "/plan":
            if arg.strip():
                await self._plan_mode(arg.strip(), fallback=False)
            else:
                self.c.notice("Say what you want done: /plan find my notes, make slides, save them")
        elif cmd == "/resume":
            await self._resume_command()
        elif cmd == "/inbox":
            items = await asyncio.to_thread(self.triggers.inbox)
            self.c.inbox(items)
            await asyncio.to_thread(self.triggers.mark_read)
        elif cmd == "/reminders":
            active = await asyncio.to_thread(self.triggers.list)
            if not active:
                self.c.notice("Nothing is scheduled. Say 'remind me to ... at ...' to add one.")
            for trigger in active:
                self.c.write("   " + describe(trigger))
        elif cmd == "/cancel" and arg.strip().isdigit():
            if await asyncio.to_thread(self.triggers.cancel, int(arg)):
                self.c.success(f"Cancelled #{int(arg)}.")
            else:
                self.c.error("No such active reminder.")
        elif cmd == "/skill":
            await self._skill_command(arg)
        elif cmd == "/clear":
            self.c.clear()
        elif cmd == "/mode":
            self._mode_command(arg.strip())
        elif cmd == "/role":
            await self._role_command(arg.strip())
        elif cmd == "/log":
            await self._log_command(arg.strip())
        elif cmd == "/suggest":
            await self._suggest_command()
        else:
            self.c.error(f"Unknown command {cmd}. Try /help.")
        return False

    # ---- skills (FR2.1) ---------------------------------------------------------------------

    async def _refresh_skills(self) -> None:
        extra, notes = await asyncio.to_thread(self.skills.tools)
        for name in self._skill_names:
            self.agent.tools.pop(name, None)
        for tool in extra:
            self.agent.tools[tool.name] = tool
        self._skill_names = {t.name for t in extra}
        for note in notes:
            self.c.notice(note)

    async def _skill_command(self, arg: str) -> None:
        action, _, rest = arg.strip().partition(" ")
        rest = rest.strip().strip('"')
        usage = "/skill list | add PATH | test NAME | disable NAME | enable NAME | remove NAME"
        if action in ("", "list"):
            rows = await asyncio.to_thread(self.skills.list)
            if not rows:
                self.c.notice("No skills yet. Try /skill add text-stats, or /skill add PATH.")
            for r in rows:
                state = "on" if r.enabled else f"off ({r.note or 'disabled'})"
                self.c.write(f"   {r.name} v{r.version} [{r.risk}] {state} - {r.description[:70]}")
        elif action == "add" and rest:
            await self._skill_add(rest)
        elif action == "test" and rest:
            try:
                skill = await asyncio.to_thread(load_skill, self.skills.root / rest)
            except SkillError as exc:
                self.c.error(f"{rest}: {exc}")
                return
            for o in await self.skills.run_tests(skill):
                (self.c.success if o.ok else self.c.error)(f"{o.tool}: {o.detail}")
        elif action in ("disable", "enable") and rest:
            done = await asyncio.to_thread(self.skills.set_enabled, rest, action == "enable")
            (self.c.success if done else self.c.error)(
                f"{action}d {rest}." if done else f"No skill called {rest}."
            )
            await self._refresh_skills()
        elif action == "remove" and rest:
            done = await asyncio.to_thread(self.skills.remove, rest)
            (self.c.success if done else self.c.error)(
                f"Removed {rest}." if done else f"No skill called {rest}."
            )
            await self._refresh_skills()
        else:
            self.c.notice(f"Usage: {usage}")

    async def _skill_add(self, where: str) -> None:
        source = Path(where)
        if not source.exists() and (BUNDLED_SKILLS / where).is_dir():
            source = BUNDLED_SKILLS / where
        if not source.is_dir():
            self.c.error(f"No skill folder at {where}.")
            return

        async def approve(summary: str) -> bool:
            await self._between()
            prompt = self.c.approval("skill add", {"folder": source.name}, summary)
            try:
                answer = await asyncio.to_thread(self.approve_line or input, prompt)
            finally:
                self._resume_spinner()
            return answer.strip().lower() in ("y", "yes")

        self.c.notice("Testing the skill in the sandbox first...")
        try:
            record = await self.skills.add(source, approve)
        except SkillError as exc:
            self.c.error(str(exc))
            return
        await self._refresh_skills()
        self.c.success(f"Added {record.name}: tools {', '.join(record.tools)}.")

    def _mode_command(self, wanted: str) -> None:
        if not wanted:
            self.c.write()
            self.c.section(f"Mode: {self.mode.name}")
            for name in MODES:
                marker = self.c.good(self.c.g["live"]) if name == self.mode.name else " "
                self.c.write(
                    f"   {marker} {self.c.sky(f'{name:<6}')} {self.c.faint(SUMMARY[name])}"
                )
            self.c.write()
            self.c.notice("Change it with /mode ask, /mode edits or /mode full.")
            return
        try:
            self.mode.set(wanted)
        except ValueError as exc:
            self.c.error(str(exc))
            return
        self.agent.profile_name = self.mode.profile
        self.c.success(f"Mode is now {self.mode.name}: {SUMMARY[self.mode.name]}.")

    # ---- roles, the log, suggestions (NFR4.3, NFR4.1, FR4.2) ----------------------------------

    async def _role_command(self, wanted: str) -> None:
        if not wanted:
            self.c.write()
            self.c.section(f"Role: {self.role}")
            for name, role in ROLES.items():
                marker = self.c.good(self.c.g["live"]) if name == self.role else " "
                self.c.write(
                    f"   {marker} {self.c.sky(f'{name:<10}')} {self.c.faint(role.summary)}"
                )
            self.c.write()
            self.c.notice("Change it with /role viewer. Going to a role with more rights asks you.")
            return
        if wanted not in ROLES:
            self.c.error(f"role must be one of: {', '.join(ROLES)}")
            return
        if rank(wanted) > rank(self.role):
            why = f"gives me more rights: {ROLES[wanted].summary}"
            prompt = self.c.approval("role", {"to": wanted}, why)
            answer = await asyncio.to_thread(self.approve_line or input, prompt)
            if answer.strip().lower() not in ("y", "yes"):
                self.c.notice("Okay, the role stays as it is.")
                return
        self.role = self.agent.role = wanted
        await asyncio.to_thread(self.rt.store.meta_set, "role", wanted)
        self.c.success(f"Role is now {wanted}: {ROLES[wanted].summary}.")

    async def _log_command(self, arg: str) -> None:
        self.c.write()
        if arg == "plan":
            plan_id = await asyncio.to_thread(latest_plan_id, self.plans)
            lines = await asyncio.to_thread(plan_steps, self.plans, plan_id) if plan_id else []
            for line in lines or ["No plan yet."]:
                self.c.write("   " + self.c.faint(line))
        else:
            calls = await asyncio.to_thread(recent_tool_calls, self.rt.store, 12)
            if not calls:
                self.c.notice("No tool calls recorded yet.")
            for call in calls:
                shown = ", ".join(f"{k}={v!r:.24}" for k, v in list(call.args.items())[:3])
                why = f' - "{call.reason}"' if call.reason else ""
                outcome = (call.result or call.status)[:50]
                self.c.write(f"   {self.c.sky(call.tool)}({shown}){self.c.faint(why)}")
                self.c.write("      " + self.c.faint(f"rule {call.rule} | {outcome}"))
        self.c.write()

    async def _suggest_command(self) -> None:
        found = await asyncio.to_thread(self.proactive.candidates)
        if not found:
            self.c.notice("Nothing to suggest right now.")
        for item in found[:5]:
            self.c.alert("suggestion", item.title, item.body)

    async def _goodbye(self) -> None:
        pending = await asyncio.to_thread(self.rt.store.pending_events, 1)
        note = "I'll finish learning from this chat next time." if pending else "See you soon."
        await self.c.farewell(note)
