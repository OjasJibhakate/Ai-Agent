"""Running a plan: ready steps in order, each as a small focused agent, each one checked.

A step runs when everything it `after` is done. Each runs as a fresh sub-agent with a short context
(the goal, the step, the results of the steps it needs) and only the tools it asked for, so on a 4B
model every call stays small and on task. After a step the verifier checks `done_when`; a failure
climbs a ladder (ARCHITECTURE 4.5):

  retry    a transient problem (the model or a tool timed out): same step again, with a pause
  fix      the step ran but was wrong or incomplete: again, told what was wrong
  re-plan  the step still fails: the planner writes new steps for the work that is left
  degrade  nothing more to try: the step is `failed`, what depends on it is `blocked`, and every
           step that does not depend on it still runs (NFR3.1)

A step the owner refused is never retried. Steps run one at a time: the model has one lane anyway,
and approval questions must not arrive in parallel. Every state change is written to the vault
before the work it announces, so a stopped program leaves an exact record (NFR3.2).
"""

from __future__ import annotations

import asyncio
import glob
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from ragnarok.planning import planner
from ragnarok.planning.models import Plan, PlanError, Step, validate
from ragnarok.planning.store import PlanStore
from ragnarok.policy.permissions import Risk
from ragnarok.runtime.loop import Agent, Session, TurnResult
from ragnarok.runtime.tools import Tool

MAX_ATTEMPTS = 3  # the first try plus two retries or fixes
MAX_REPLANS = 2
BACKOFF = 0.5
CONTEXT_PER_STEP = 700  # characters of an earlier step's result handed to a later one
# Every step can look things up, whatever the planner listed: a step that must "save the file names"
# cannot know them unless it can list the folder.
CORE_TOOLS = ("memory_search", "find_files", "list_folder", "read_file")
CHANGES_THINGS = re.compile(
    r"\b(save|write|create|make|copy|move|rename|delete|remove|send|download|generate|build)\b",
    re.IGNORECASE,
)

Approve = Callable[[str, dict, object], Awaitable[bool]]
AgentFactory = Callable[[list[Tool], Approve], Agent]


@dataclass(frozen=True)
class PlanEvent:
    kind: str  # plan | state | retry | replan | finished
    step_id: str | None
    text: str
    state: str = ""


@dataclass
class Outcome:
    """What one attempt at a step produced."""

    text: str = ""
    denied: bool = False
    transient: bool = False
    error: str = ""
    used: list[str] = field(default_factory=list)
    report: str = ""
    unavailable: bool = False  # a tool is paused after repeated crashes: retrying cannot help


@dataclass
class PlanResult:
    plan_id: str
    status: str  # done | partial | failed
    done: list[str]
    failed: dict[str, str]
    blocked: list[str]
    outputs: dict[str, str]

    def summary(self, plan: Plan) -> str:
        titles = {s.id: s.title for s in plan.steps}
        lines = [f"Plan {self.status}: {len(self.done)} of {len(plan.steps)} steps done."]
        for sid in self.done:
            lines.append(
                f"- {titles.get(sid, sid)}: {self.outputs.get(sid, '').splitlines()[0][:150]}"
            )
        for sid, why in self.failed.items():
            lines.append(f"- FAILED {titles.get(sid, sid)}: {why[:150]}")
        for sid in self.blocked:
            lines.append(f"- skipped {titles.get(sid, sid)} (it needed a step that failed)")
        return "\n".join(lines)


def default_file_probe(roots: list[Path]) -> Callable[[str, float], bool]:
    """Did a file matching the pattern appear or change in these folders since `since`?"""

    def probe(pattern: str, since: float) -> bool:
        for root in roots:
            for hit in glob.glob(str(root / "**" / pattern), recursive=True):
                try:  # a folder made or a file written during the step both count
                    if Path(hit).stat().st_mtime >= since - 1:
                        return True
                except OSError:
                    continue
        return False

    return probe


class PlanRunner:
    def __init__(
        self,
        store: PlanStore,
        gateway: planner.JsonModel,
        tools: dict[str, Tool],
        make_agent: AgentFactory,
        approve: Approve,
        emit: Callable[[PlanEvent], Awaitable[None]],
        *,
        file_probe: Callable[[str, float], bool] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.store = store
        self.gateway = gateway
        self.tools = tools
        self.make_agent = make_agent
        self.approve = approve
        self.emit = emit
        self.file_probe = file_probe or (lambda _p, _t: False)
        self.sleep = sleep

    # ---- making and checking the plan ---------------------------------------------------------

    def risks(self) -> dict[str, Risk]:
        return {name: tool.risk for name, tool in self.tools.items()}

    async def propose(self, goal: str, *, attempts: int = 3) -> tuple[Plan, dict[str, Risk]]:
        """Ask for a plan until it validates; the error goes back to the planner each time."""
        problem = ""
        last: PlanError | None = None
        for _ in range(attempts):
            try:
                plan = await planner.make_plan(
                    self.gateway,
                    goal,
                    [t for t in self.tools.values() if t.name not in planner.HIDDEN_FROM_PLANS],
                    problem=problem,
                )
                return plan, validate(plan, self.risks(), allowed=self._plannable())
            except PlanError as exc:
                problem, last = str(exc), exc
        raise PlanError(f"I couldn't make a workable plan ({last})")

    def _plannable(self) -> set[str]:
        return {n for n in self.tools if n not in planner.HIDDEN_FROM_PLANS}

    # ---- running it ---------------------------------------------------------------------------

    async def run(self, plan_id: str) -> PlanResult:
        row = self.store.get(plan_id)
        if row is None:
            raise PlanError("no such plan")
        plan = row.plan
        goal = row.goal
        session = Session()
        # A step still marked running belongs to a program that was killed: it is interrupted.
        for sid, stored in self.store.steps(plan_id).items():
            if stored.state in ("running", "waiting_approval"):
                await asyncio.to_thread(self.store.set_state, plan_id, sid, "interrupted")
        if row.status != "running":
            await asyncio.to_thread(self.store.set_status, plan_id, "running", "resumed")
        try:
            while True:
                states = {sid: s.state for sid, s in self.store.steps(plan_id).items()}
                await self._block_dependents_of_failures(plan_id, plan, states)
                states = {sid: s.state for sid, s in self.store.steps(plan_id).items()}
                ready = next(
                    (
                        s
                        for s in plan.steps
                        if states.get(s.id) in ("pending", "interrupted")
                        and all(states.get(d) == "done" for d in s.after)
                    ),
                    None,
                )
                if ready is None:
                    break
                replanned = await self._run_step(plan_id, goal, plan, ready, session)
                if replanned is not None:
                    plan = replanned
        except asyncio.CancelledError:
            await asyncio.shield(self._interrupted(plan_id))
            raise
        return await self._finish(plan_id, plan)

    async def _interrupted(self, plan_id: str) -> None:
        for sid, step in self.store.steps(plan_id).items():
            if step.state in ("running", "waiting_approval"):
                await asyncio.to_thread(self.store.set_state, plan_id, sid, "interrupted")
        await asyncio.to_thread(self.store.set_status, plan_id, "interrupted", "stopped")

    async def _block_dependents_of_failures(self, plan_id: str, plan: Plan, states: dict) -> None:
        for step in plan.steps:
            if states.get(step.id) == "pending" and any(
                states.get(d) in ("failed", "blocked", "skipped") for d in step.after
            ):
                await asyncio.to_thread(self.store.set_state, plan_id, step.id, "blocked")
                await self.emit(PlanEvent("state", step.id, "needs a step that failed", "blocked"))
                states[step.id] = "blocked"

    async def _finish(self, plan_id: str, plan: Plan) -> PlanResult:
        rows = self.store.steps(plan_id)
        done = [s.id for s in plan.steps if rows[s.id].state == "done"]
        failed = {s.id: rows[s.id].error for s in plan.steps if rows[s.id].state == "failed"}
        blocked = [s.id for s in plan.steps if rows[s.id].state == "blocked"]
        status = "done" if len(done) == len(plan.steps) else "partial" if done else "failed"
        await asyncio.to_thread(
            self.store.set_status, plan_id, status, f"{len(done)}/{len(plan.steps)} steps"
        )
        outputs = {sid: rows[sid].output for sid in done}
        await self.emit(PlanEvent("finished", None, status))
        return PlanResult(plan_id, status, done, failed, blocked, outputs)

    # ---- one step -----------------------------------------------------------------------------

    async def _run_step(
        self, plan_id: str, goal: str, plan: Plan, step: Step, session: Session
    ) -> Plan | None:
        """Run one step through the ladder. Returns a new plan if it had to be re-planned."""
        feedback = ""
        reason = ""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            await asyncio.to_thread(
                self.store.set_state, plan_id, step.id, "running", attempts=attempt, error=""
            )
            await self.emit(
                PlanEvent("state", step.id, f"attempt {attempt}" if attempt > 1 else "", "running")
            )
            started = time.time()
            outcome = await self._attempt(plan_id, goal, plan, step, feedback, widen=attempt > 1)
            ok, reason = await self._verify(step, outcome, started)
            if ok:
                await asyncio.to_thread(
                    self.store.set_state, plan_id, step.id, "done", output=outcome.report
                )
                await self.emit(PlanEvent("state", step.id, outcome.text[:90], "done"))
                return None
            if outcome.denied:
                reason = "you said no"
                break
            if attempt == MAX_ATTEMPTS or outcome.unavailable:
                break  # a paused tool is not worth retrying: go straight to re-plan or degrade
            rung = "retry" if outcome.transient else "fix"
            await asyncio.to_thread(self.store.log, plan_id, rung, step.id, reason)
            used = ", ".join(dict.fromkeys(outcome.used)) or "no tool"
            await self.emit(
                PlanEvent("retry", step.id, f"{rung}: {reason[:70]} (it used {used})", "running")
            )
            feedback = reason
            if outcome.transient:
                await self.sleep(BACKOFF * attempt)

        await asyncio.to_thread(
            self.store.set_state, plan_id, step.id, "failed", error=reason or "it did not work"
        )
        await self.emit(PlanEvent("state", step.id, reason[:90], "failed"))
        if reason == "you said no":
            return None  # the owner's refusal is final: no re-plan around it
        return await self._replan(plan_id, goal, plan, step, reason)

    async def _replan(
        self, plan_id: str, goal: str, plan: Plan, failed: Step, reason: str
    ) -> Plan | None:
        row = self.store.get(plan_id)
        if row is None or row.replans >= MAX_REPLANS:
            return None
        rows = self.store.steps(plan_id)
        done = {sid: r.output for sid, r in rows.items() if r.state == "done"}
        try:
            fresh = await planner.replan(
                self.gateway, goal, self.tools.values(), done, failed.title, reason
            )
            kept = [s for s in plan.steps if rows[s.id].state == "done"]
            merged = Plan(goal, kept + renumber(fresh, {s.id for s in kept}))
            validate(merged, self.risks(), allowed=self._plannable())
        except PlanError as exc:
            await asyncio.to_thread(self.store.log, plan_id, "replan_failed", failed.id, str(exc))
            return None
        await asyncio.to_thread(
            self.store.replace_steps, plan_id, merged, f"after {failed.id}: {reason}"
        )
        await self.emit(
            PlanEvent("replan", failed.id, f"{len(merged.steps) - len(kept)} new steps", "")
        )
        return merged

    # ---- one attempt --------------------------------------------------------------------------

    async def _attempt(
        self, plan_id: str, goal: str, plan: Plan, step: Step, feedback: str, widen: bool = False
    ) -> Outcome:
        # The first try gets what the planner asked for; a retry gets every tool, because the
        # planner may have left out the one the step needs.
        names = list(dict.fromkeys([*step.uses, *CORE_TOOLS]))
        if widen:
            names = list(dict.fromkeys([*names, *self._plannable()]))
        subset = [self.tools[n] for n in names if n in self.tools]

        async def ask(name: str, args: dict, decision: object) -> bool:
            await asyncio.to_thread(self.store.set_state, plan_id, step.id, "waiting_approval")
            await self.emit(PlanEvent("state", step.id, f"wants to run {name}", "waiting_approval"))
            try:
                return await self.approve(name, args, decision)
            finally:
                await asyncio.to_thread(self.store.set_state, plan_id, step.id, "running")

        agent = self.make_agent(subset, ask)
        try:
            result = await agent.turn(Session(), self._prompt(plan_id, goal, plan, step, feedback))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # a step failing is a result, never a crash of the plan
            return Outcome(error=type(exc).__name__, transient=True)
        return self._read(result, step)

    def _prompt(self, plan_id: str, goal: str, plan: Plan, step: Step, feedback: str) -> str:
        rows = self.store.steps(plan_id)
        context = "\n".join(
            f"- {dep}: {rows[dep].output[:CONTEXT_PER_STEP]}" for dep in step.after if dep in rows
        )
        text = (
            "You are carrying out ONE step of a larger plan. Do only this step, with your tools.\n"
            f"Overall goal: {goal}\nThis step ({step.id}): {step.title}\n"
        )
        if step.uses:
            text += f"Do it with: {', '.join(step.uses)} (other tools only to look things up).\n"
        if context:
            text += f"Results of the earlier steps you need:\n{context}\n"
        if feedback:
            text += f"Your last try did not work: {feedback}\nDo it properly this time.\n"
        return text + (
            "Do not ask the user anything: find what you need with your tools. "
            "Finish with one sentence saying what you did and the key result."
        )

    def _read(self, result: TurnResult, step: Step) -> Outcome:
        allowed = [t for t in result.trace if t.allowed]
        denied = any((not t.allowed) and t.result == "denied" for t in result.trace)
        last: dict[str, str] = {}
        for t in allowed:
            last[t.tool] = t.result
        failing = {name: out for name, out in last.items() if out.startswith("error")}
        transient = result.stopped == "model-error" or any(
            out.startswith("error: timeout") for out in failing.values()
        )
        error = ""
        if result.stopped:
            error = result.text or result.stopped
        elif failing:
            name, out = next(iter(failing.items()))
            error = f"{name} failed: {out[:160]}"
        elif step.uses and not allowed:
            error = "no tool was used for this step"
        report = result.text.strip()
        extras = "; ".join(f"{t.tool}: {t.result[:200]}" for t in allowed[-3:])
        if extras and extras not in report:
            report = f"{report}\n{extras}".strip()
        down = any(out.startswith("error: unavailable") for out in failing.values())
        return Outcome(
            result.text, denied, transient, error, [t.tool for t in allowed], report, down
        )

    # ---- checking the result ------------------------------------------------------------------

    async def _verify(self, step: Step, outcome: Outcome, started: float) -> tuple[bool, str]:
        if outcome.denied:
            return False, "the owner refused a step"
        if outcome.error:
            return False, outcome.error
        kind, _, arg = step.done_when.partition(":")
        if kind == "file":
            found = await asyncio.to_thread(self.file_probe, arg.strip(), started)
            return (True, "") if found else (False, f"no file matching {arg.strip()} was made")
        if kind == "text":
            ok, why = await planner.judge(self.gateway, arg.strip(), outcome.report)
            return ok, why or "it does not meet the condition"
        if not step.uses and not outcome.text.strip():
            return False, "the step produced no answer"
        if CHANGES_THINGS.search(step.title) and not any(
            self.tools[n].risk is not Risk.R0 for n in outcome.used if n in self.tools
        ):
            return False, "this step is to make or change something, but only read tools ran"
        return True, ""


def renumber(steps: list[Step], taken: set[str]) -> list[Step]:
    """Make sure new steps have ids that do not collide with the finished ones.

    The planner is told to number them after the finished steps, so normally nothing changes. If
    it numbered them from s1 again they are renumbered here and their dependencies remapped.
    """
    if not any(s.id in taken for s in steps):
        return steps
    highest = max((int(i[1:]) for i in taken), default=0)
    mapping = {s.id: f"s{highest + n}" for n, s in enumerate(steps, start=1)}
    out = []
    for s in steps:
        after = [mapping.get(d, d) for d in s.after if d in mapping or d in taken]
        out.append(Step(mapping[s.id], s.title, s.uses, after, s.done_when))
    return out
