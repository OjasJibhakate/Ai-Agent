"""Planning (FR3): validation, running steps in order, checking them, and the failure ladder."""

import asyncio
import re

import pytest

from ragnarok.planning import planner
from ragnarok.planning.models import (
    MAX_STEPS,
    PLAN_SCHEMA,
    Plan,
    PlanError,
    Step,
    dependents,
    normalise_done_when,
    validate,
)
from ragnarok.planning.runner import MAX_ATTEMPTS, MAX_REPLANS, PlanEvent, PlanRunner, renumber
from ragnarok.planning.store import PlanStore
from ragnarok.policy.permissions import Risk
from ragnarok.runtime.gateway import GatewayError
from ragnarok.runtime.loop import ToolTrace, TurnResult
from ragnarok.runtime.tools import Tool, ToolArgs
from tests.test_m2_memory import key, make  # noqa: F401  (fixture)

# ---- fakes ---------------------------------------------------------------------------------------


def tool(name: str, risk: Risk = Risk.R1) -> Tool:
    async def handler(args):
        return "ok"

    return Tool(name, f"{name}. Does a thing.", ToolArgs, risk, True, 5, "read-only", handler)


TOOLS = {
    t.name: t
    for t in (
        tool("read_file", Risk.R0),
        tool("find_files", Risk.R0),
        tool("create_document"),
        tool("create_presentation"),
        tool("move_path", Risk.R2),
        tool("delete_path", Risk.R3),
        tool("memory_search", Risk.R0),
    )
}


def ok(text="Did it.", *names, result="Created a file.") -> TurnResult:
    trace = [ToolTrace(n, {}, "default:R1", True, result) for n in names]
    return TurnResult(text=text, trace=trace)


def tool_error(name="create_document") -> TurnResult:
    trace = [ToolTrace(name, {}, "default:R1", True, "error: invalid_args: title is required")]
    return TurnResult(text="It failed.", trace=trace)


def refused(name="move_path") -> TurnResult:
    return TurnResult(text="Not allowed.", trace=[ToolTrace(name, {}, "ask", False, "denied")])


def model_down() -> TurnResult:
    return TurnResult(text="I can't reach my language model right now.", stopped="model-error")


class Script:
    """What each step's agent does, in order; the last behaviour repeats."""

    def __init__(self, **steps):
        self.steps = {k: list(v) for k, v in steps.items()}
        self.prompts: list[tuple[str, str]] = []
        self.tools: dict[str, list[str]] = {}

    def agent(self, subset, approve):
        outer = self

        class Agent:
            async def turn(self, session, text, **kw):
                step = re.search(r"This step \((s\d+)\)", text).group(1)
                outer.prompts.append((step, text))
                outer.tools[step] = [t.name for t in subset]
                queue = outer.steps.get(step, [ok("Done.", "create_document")])
                behave = queue.pop(0) if len(queue) > 1 else queue[0]
                if callable(behave):
                    return await behave(approve)
                return behave

        return Agent()


class Json:
    """The model's JSON answers: plans, replans and verdicts, in order."""

    def __init__(self, plans=(), verdicts=()):
        self.plans = list(plans)
        self.verdicts = list(verdicts)
        self.calls: list[dict] = []

    async def chat_json(self, messages, schema, **kw):
        self.calls.append({"messages": messages, "schema": schema})
        if schema is PLAN_SCHEMA:
            reply = self.plans.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply
        return self.verdicts.pop(0) if self.verdicts else {"pass": True, "reason": ""}


def plan_json(*steps):
    return {
        "steps": [
            {"id": i, "title": t, "uses": u, "after": a, "done_when": d} for i, t, u, a, d in steps
        ]
    }


async def approve_yes(name, args, decision) -> bool:
    return True


@pytest.fixture
def world(tmp_path, key):  # noqa: F811
    vault, *_ = make(tmp_path, key, "exact")
    store = PlanStore(vault)
    yield store, vault
    vault.close()


def runner_for(store, script, json=None, *, probe=None, approve=approve_yes):
    events: list[PlanEvent] = []

    async def emit(event):
        events.append(event)

    sleeps: list[float] = []

    async def sleep(seconds):
        sleeps.append(seconds)

    runner = PlanRunner(
        store, json or Json(), TOOLS, script.agent, approve, emit, file_probe=probe, sleep=sleep
    )
    return runner, events, sleeps


def three_steps(**over):
    return Plan(
        "make the report",
        [
            Step("s1", "Read the notes", ["read_file"], [], "tool_ok"),
            Step("s2", "Write the report", ["create_document"], ["s1"], over.get("d2", "tool_ok")),
            Step("s3", "File a copy", ["move_path"], ["s2"], "tool_ok"),
        ],
    )


def states_of(events, step_id):
    return [e.state for e in events if e.step_id == step_id and e.kind == "state"]


# ---- validation ----------------------------------------------------------------------------------


def risks():
    return {n: t.risk for n, t in TOOLS.items()}


def test_a_good_plan_gets_each_steps_highest_risk():
    plan = Plan(
        "g", [Step("s1", "a", ["read_file"]), Step("s2", "b", ["read_file", "delete_path"], ["s1"])]
    )
    assert validate(plan, risks()) == {"s1": Risk.R0, "s2": Risk.R3}


@pytest.mark.parametrize(
    "steps,message",
    [
        ([], "no steps"),
        ([Step("s1", "a", ["nope"])], "not an available tool"),
        ([Step("s1", "a", [], ["s2"]), Step("s2", "b")], "not an earlier step"),
        ([Step("s1", "a", [], ["s1"])], "not an earlier step"),
        ([Step("s1", "a"), Step("s1", "b")], "unique"),
        ([Step("step one", "a")], "s1, s2"),
        ([Step("s1", "  ")], "no title"),
        ([Step(f"s{i}", "x") for i in range(1, MAX_STEPS + 2)], "at most"),
    ],
)
def test_a_bad_plan_says_what_is_wrong_in_words_the_planner_can_use(steps, message):
    with pytest.raises(PlanError, match=message):
        validate(Plan("g", steps), risks())


def test_a_tool_that_exists_but_is_not_allowed_here_is_refused():
    with pytest.raises(PlanError, match="not an available tool"):
        validate(Plan("g", [Step("s1", "a", ["delete_path"])]), risks(), allowed={"read_file"})


def test_done_when_is_normalised_to_one_of_three_forms():
    assert normalise_done_when("") == "tool_ok" == normalise_done_when("OK")
    assert normalise_done_when("file: *.pptx") == "file:*.pptx"
    assert normalise_done_when("text:one slot per request") == "text:one slot per request"
    assert normalise_done_when("the deck has ten slides") == "text:the deck has ten slides"
    # guessed names and placeholder templates cannot be checked, so they are not made into checks
    assert normalise_done_when("file:notes.txt") == "tool_ok"
    assert normalise_done_when("file:Reports") == "tool_ok"
    assert normalise_done_when("file:report_??.pdf") == "file:report_??.pdf"
    assert normalise_done_when("text:<file names found>") == "tool_ok"
    assert normalise_done_when("<image content extracted>") == "tool_ok"
    assert normalise_done_when("text:") == "tool_ok"


def test_dependents_are_found_through_other_steps():
    plan = Plan("g", [Step("s1", "a"), Step("s2", "b", [], ["s1"]), Step("s3", "c", [], ["s2"]),
                      Step("s4", "d")])  # fmt: skip
    assert dependents(plan, "s1") == ["s2", "s3"] and dependents(plan, "s4") == []


def test_renumbering_continues_after_the_finished_steps_and_remaps_dependencies():
    new = [Step("s1", "x", [], ["s2"]), Step("s2", "y", [], ["s1"])]
    out = renumber(new, {"s1", "s2", "s3"})
    assert [s.id for s in out] == ["s4", "s5"]
    assert out[1].after == ["s4"]  # was s1
    assert out[0].after == ["s5"]  # a forward reference stays one, and validate() rejects it
    keep = [Step("s4", "n", [], ["s1"])]
    assert renumber(keep, {"s1", "s2", "s3"}) is keep  # already after the finished: untouched
    assert renumber([Step("s1", "a", [], ["s7"])], {"s1", "s2"})[0].after == []  # unknown: dropped
    assert renumber([Step("s1", "a", [], ["s9"])], {"s9"})[0].after == [
        "s9"
    ]  # a finished step stays


# ---- asking the model ----------------------------------------------------------------------------


async def test_propose_retries_with_the_problem_and_returns_a_valid_plan(world):
    store, _ = world
    json = Json(
        plans=[
            plan_json(("s1", "Do it", ["no_such_tool"], [], "tool_ok")),
            plan_json(("s1", "Read it", ["read_file"], [], "tool_ok")),
        ]
    )
    runner, *_ = runner_for(store, Script(), json)
    plan, risk_by_step = await runner.propose("read my notes")
    assert [s.id for s in plan.steps] == ["s1"] and risk_by_step == {"s1": Risk.R0}
    assert "rejected" in json.calls[1]["messages"][-1]["content"]
    assert "no_such_tool" in json.calls[1]["messages"][-1]["content"]
    assert "<goal>\nread my notes\n</goal>" in json.calls[0]["messages"][-1]["content"]
    assert "use_computer" not in json.calls[0]["messages"][0]["content"]  # never planned


async def test_propose_gives_up_after_three_bad_plans(world):
    store, _ = world
    bad = plan_json(("s1", "x", ["no_such_tool"], [], "tool_ok"))
    runner, *_ = runner_for(store, Script(), Json(plans=[bad, bad, bad]))
    with pytest.raises(PlanError, match="couldn't make a workable plan"):
        await runner.propose("anything")


async def test_propose_reports_an_unreachable_model(world):
    store, _ = world
    runner, *_ = runner_for(store, Script(), Json(plans=[GatewayError("down")] * 3))
    with pytest.raises(PlanError, match="language model"):
        await runner.propose("anything")


async def test_judge_trusts_the_step_when_the_judge_itself_is_down():
    class Down:
        async def chat_json(self, *a, **k):
            raise GatewayError("down")

    assert await planner.judge(Down(), "the deck exists", "made it") == (
        True,
        "could not check; trusting the step",
    )


# ---- running -------------------------------------------------------------------------------------


async def test_steps_run_in_order_each_gets_the_results_it_needs_and_only_its_tools(world):
    store, vault = world
    script = Script(
        s1=[ok("Read 3 pages about convolution.", "read_file", result="3 pages")],
        s2=[ok("Wrote the report.", "create_document", result="Created Report.docx")],
        s3=[ok("Moved it.", "move_path")],
    )
    runner, events, _ = runner_for(store, script)
    plan = three_steps()
    pid = store.create(plan)
    result = await runner.run(pid)
    assert result.status == "done" and result.done == ["s1", "s2", "s3"]
    assert [s for s, _ in script.prompts] == ["s1", "s2", "s3"]
    s2_prompt = dict(script.prompts)["s2"]
    assert "Overall goal: make the report" in s2_prompt and "Write the report" in s2_prompt
    assert "s1: Read 3 pages about convolution." in s2_prompt  # the earlier result, passed on
    assert "s3" not in s2_prompt.split("Results of the earlier steps")[1]
    # what it asked for, plus the read-only tools every step gets; not the ones that change things
    assert set(script.tools["s1"]) == {"read_file", "memory_search", "find_files"}
    assert "create_document" not in script.tools["s1"] and "delete_path" not in script.tools["s1"]
    assert states_of(events, "s2") == ["running", "done"]
    assert store.get(pid).status == "done"
    rows = store.steps(pid)
    assert all(r.state == "done" and r.attempts == 1 for r in rows.values())
    assert "Created Report.docx" in rows["s2"].output
    kinds = [k for _, k, *_ in store.history(pid)]
    assert kinds[0] == "created" and kinds[-1] == "finished"


async def test_independent_steps_do_not_wait_for_each_other(world):
    store, _ = world
    plan = Plan("g", [Step("s1", "A", ["create_document"]), Step("s2", "B", ["create_document"]),
                      Step("s3", "C", ["create_document"], ["s1", "s2"])])  # fmt: skip
    script = Script()
    runner, *_ = runner_for(store, script)
    result = await runner.run(store.create(plan))
    assert result.status == "done" and [s for s, _ in script.prompts] == ["s1", "s2", "s3"]


async def test_a_transient_failure_is_retried_after_a_pause(world):
    store, _ = world
    script = Script(s1=[model_down(), ok("Read it.", "read_file")])
    runner, events, sleeps = runner_for(store, script)
    pid = store.create(Plan("g", [Step("s1", "Read it", ["read_file"])]))
    result = await runner.run(pid)
    assert result.status == "done" and store.steps(pid)["s1"].attempts == 2
    assert sleeps == [0.5]  # a short backoff, then the retry
    assert any(e.kind == "retry" and e.text.startswith("retry:") for e in events)


async def test_a_wrong_result_is_fixed_by_trying_again_with_what_was_wrong(world):
    store, _ = world
    script = Script(s1=[tool_error(), ok("Made it.", "create_document")])
    runner, events, sleeps = runner_for(store, script)
    pid = store.create(Plan("g", [Step("s1", "Make the doc", ["create_document"])]))
    result = await runner.run(pid)
    assert result.status == "done" and sleeps == []  # no pause for a fix
    second = [p for s, p in script.prompts][1]
    assert "Your last try did not work" in second and "title is required" in second
    assert any(e.text.startswith("fix:") for e in events if e.kind == "retry")


async def test_a_file_check_passes_only_when_a_matching_file_was_made(world):
    store, _ = world
    made = {"yes": False}

    def probe(pattern, since):
        assert pattern == "*.pptx"
        return made["yes"]

    async def make_it(approve):
        made["yes"] = True
        return ok("Made the deck.", "create_presentation")

    script = Script(s1=[ok("Said I made it.", "create_presentation"), make_it])
    runner, events, _ = runner_for(store, script, probe=probe)
    pid = store.create(
        Plan("g", [Step("s1", "Make the deck", ["create_presentation"], [], "file:*.pptx")])
    )
    result = await runner.run(pid)
    assert result.status == "done" and store.steps(pid)["s1"].attempts == 2
    assert any("no file matching *.pptx" in e.text for e in events if e.kind == "retry")


async def test_a_text_check_asks_the_model_and_a_no_means_try_again(world):
    store, _ = world
    json = Json(
        verdicts=[{"pass": False, "reason": "only 3 slots, not 4"}, {"pass": True, "reason": ""}]
    )
    script = Script(s1=[ok("Found 3 slots.", "find_files"), ok("Found 4 slots.", "find_files")])
    runner, *_ = runner_for(store, script, json)
    pid = store.create(Plan("g", [Step("s1", "Find slots", ["find_files"], [], "text:four slots")]))
    result = await runner.run(pid)
    assert result.status == "done" and store.steps(pid)["s1"].attempts == 2
    judged = [c for c in json.calls if c["schema"] is not PLAN_SCHEMA]
    assert "four slots" in judged[0]["messages"][-1]["content"]
    assert "only 3 slots" in script.prompts[1][1]


async def test_a_step_with_no_tool_use_when_it_needed_one_fails_the_check(world):
    store, _ = world
    script = Script(
        s1=[
            ok(
                "I could do that.",
            ),
            ok("Done.", "create_document"),
        ]
    )
    runner, events, _ = runner_for(store, script)
    pid = store.create(Plan("g", [Step("s1", "Make the doc", ["create_document"])]))
    assert (await runner.run(pid)).status == "done"
    assert store.steps(pid)["s1"].attempts == 2


# ---- the ladder ----------------------------------------------------------------------------------


async def test_the_owner_saying_no_is_final_dependents_wait_and_independent_steps_finish(world):
    store, _ = world
    plan = Plan("g", [Step("s1", "Read", ["read_file"]), Step("s2", "Move it", ["move_path"], ["s1"]),
                      Step("s3", "Tell me", ["create_document"], ["s2"]),
                      Step("s4", "Write a note", ["create_document"])])  # fmt: skip
    script = Script(s2=[refused()])
    runner, events, _ = runner_for(store, script, Json(plans=[AssertionError("never replans")]))
    pid = store.create(plan)
    result = await runner.run(pid)
    assert result.status == "partial"
    assert (
        result.done == ["s1", "s4"] and list(result.failed) == ["s2"] and result.blocked == ["s3"]
    )
    assert result.failed["s2"] == "you said no"
    assert store.steps(pid)["s2"].attempts == 1  # no retry after a refusal
    assert [s for s, _ in script.prompts] == ["s1", "s2", "s4"]  # s3 never ran, s4 did
    assert states_of(events, "s3") == ["blocked"]
    text = result.summary(plan)
    assert "Plan partial: 2 of 4 steps done" in text and "FAILED Move it: you said no" in text
    assert "skipped Tell me" in text


async def test_a_step_that_keeps_failing_is_replanned_then_the_new_steps_run(world):
    store, _ = world
    plan = Plan(
        "g",
        [
            Step("s1", "Read", ["read_file"]),
            Step("s2", "Use the broken tool", ["create_document"], ["s1"]),
        ],
    )
    script = Script(
        s2=[tool_error()] * MAX_ATTEMPTS,
        s3=[ok("Made it another way.", "create_presentation")],
    )
    json = Json(
        plans=[plan_json(("s3", "Make a deck instead", ["create_presentation"], ["s1"], "tool_ok"))]
    )
    runner, events, _ = runner_for(store, script, json)
    pid = store.create(plan)
    result = await runner.run(pid)
    assert result.status == "done" and result.done == [
        "s1",
        "s3",
    ]  # new step numbered after s2... s1 kept
    assert store.get(pid).replans == 1
    assert [s.id for s in store.get(pid).plan.steps] == ["s1", "s3"]  # s2 was replaced by s3
    assert any(e.kind == "replan" for e in events)
    replan_call = [c for c in json.calls if c["schema"] is PLAN_SCHEMA][0]["messages"][-1][
        "content"
    ]
    assert "Use the broken tool" in replan_call and "Already done" in replan_call
    assert "title is required" in replan_call
    assert [k for _, k, *_ in store.history(pid)].count("replan") == 1


async def test_when_replanning_also_fails_the_step_is_degraded_and_the_rest_continues(world):
    store, _ = world
    plan = Plan("g", [Step("s1", "Broken", ["create_document"]), Step("s2", "Needs s1", ["read_file"], ["s1"]),
                      Step("s3", "Unrelated", ["read_file"])])  # fmt: skip
    script = Script(s1=[tool_error()] * 9)
    bad = plan_json(("s1", "x", ["no_such_tool"], [], "tool_ok"))
    runner, events, _ = runner_for(store, script, Json(plans=[bad, bad]))
    result = await runner.run(store.create(plan))
    assert result.status == "partial" and result.done == ["s3"]
    assert list(result.failed) == ["s1"] and result.blocked == ["s2"]
    assert states_of(events, "s1")[-1] == "failed"


async def test_replans_are_limited(world):
    store, _ = world
    plan = Plan("g", [Step("s1", "Broken", ["create_document"])])
    script = Script(**{f"s{i}": [tool_error()] * 9 for i in range(1, 12)})
    again = plan_json(("s1", "Try again", ["create_document"], [], "tool_ok"))
    runner, *_ = runner_for(store, script, Json(plans=[again] * 5))
    pid = store.create(plan)
    result = await runner.run(pid)
    assert result.status == "failed" and store.get(pid).replans == MAX_REPLANS


# ---- approvals, persistence, stopping ------------------------------------------------------------


async def test_a_step_waiting_for_approval_is_marked_and_resumes_running(world):
    store, _ = world
    asked = []

    async def approve(name, args, decision):
        asked.append(name)
        return True

    async def needs_approval(ask):
        assert await ask("move_path", {"source": "a"}, None) is True
        return ok("Moved it.", "move_path")

    script = Script(s1=[needs_approval])
    runner, events, _ = runner_for(store, script, approve=approve)
    pid = store.create(Plan("g", [Step("s1", "Move it", ["move_path"])]))
    await runner.run(pid)
    assert asked == ["move_path"]
    assert states_of(events, "s1") == ["running", "waiting_approval", "done"]
    logged = [d for _, k, s, d in store.history(pid) if k == "step_state" and s == "s1"]
    assert logged == ["running", "waiting_approval", "running", "done"]


async def test_a_plan_can_be_read_back_from_the_vault_exactly(world):
    store, vault = world
    runner, *_ = runner_for(
        store, Script(s2=[tool_error()] * 9), Json(plans=[GatewayError("x")] * 2)
    )
    pid = store.create(three_steps(), session_id="abc")
    await runner.run(pid)
    fresh = PlanStore(vault)  # a new program on the same vault
    row = fresh.get(pid)
    assert row.session_id == "abc" and row.plan.as_json() == three_steps().as_json()
    assert {k: v.state for k, v in fresh.steps(pid).items()} == {
        "s1": "done", "s2": "failed", "s3": "blocked",
    }  # fmt: skip
    assert row.status == "partial" and "title is required" in fresh.steps(pid)["s2"].error


async def test_secrets_in_the_goal_and_outputs_are_not_stored(world):
    store, _ = world
    plan = Plan(
        "log in with password: Qz7Lm2Xv9T#KP@4 and read it", [Step("s1", "Read", ["read_file"])]
    )
    script = Script(s1=[ok("The token is fine. password is hunter22xyz", "read_file")])
    runner, *_ = runner_for(store, script)
    pid = store.create(plan)
    await runner.run(pid)
    assert "Qz7Lm2Xv9T" not in store.get(pid).goal
    assert "hunter22xyz" not in store.steps(pid)["s1"].output


async def test_stopping_mid_plan_leaves_the_exact_state_for_a_later_resume(world):
    store, _ = world
    started = asyncio.Event()

    async def hang(approve):
        started.set()
        await asyncio.sleep(30)

    script = Script(s2=[hang])
    runner, *_ = runner_for(store, script)
    pid = store.create(three_steps())
    task = asyncio.create_task(runner.run(pid))
    await asyncio.wait_for(started.wait(), 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert {k: v.state for k, v in store.steps(pid).items()} == {
        "s1": "done", "s2": "interrupted", "s3": "pending",
    }  # fmt: skip
    assert store.get(pid).status == "interrupted"
    assert [p.id for p in store.unfinished()] == [pid]
    # a later run picks up where it stopped and does not redo the finished step
    script2 = Script()
    runner2, *_ = runner_for(store, script2)
    result = await runner2.run(pid)
    assert [s for s, _ in script2.prompts] == ["s2", "s3"] and result.status == "done"


async def test_a_retry_gets_every_tool_because_the_planner_may_have_left_one_out(world):
    store, _ = world
    script = Script(s1=[ok("Listed them.", "find_files"), ok("Saved them.", "create_document")])
    runner, events, _ = runner_for(store, script)
    pid = store.create(
        Plan("g", [Step("s1", "List the files and save them to a note", ["find_files"])])
    )
    result = await runner.run(pid)
    assert result.status == "done" and store.steps(pid)["s1"].attempts == 2
    assert any("only read tools ran" in e.text for e in events if e.kind == "retry")
    first, second = script.prompts[0][1], script.prompts[1][1]
    assert "Your last try did not work" in second and "only read tools" in second
    assert set(script.tools["s1"]) >= {"create_document", "move_path", "find_files"}  # widened
    assert "Do not ask the user anything" in first


async def test_a_step_that_says_to_save_but_only_read_does_not_pass_as_done(world):
    store, _ = world
    script = Script(s1=[ok("Found them.", "find_files")])  # the same answer every time
    runner, *_ = runner_for(store, script, Json(plans=[GatewayError("x")] * 2))
    pid = store.create(Plan("g", [Step("s1", "Save the list to a file", ["find_files"])]))
    result = await runner.run(pid)
    assert result.status == "failed" and "only read tools ran" in result.failed["s1"]


async def test_a_reading_step_is_fine_with_read_tools(world):
    store, _ = world
    script = Script(s1=[ok("Found 3.", "find_files")])
    runner, *_ = runner_for(store, script)
    pid = store.create(Plan("g", [Step("s1", "Look through the notes folder", ["find_files"])]))
    assert (await runner.run(pid)).status == "done"
