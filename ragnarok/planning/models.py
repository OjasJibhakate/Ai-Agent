"""A plan: a goal broken into steps that name their tools and what must be true when they are done.

Steps are listed in dependency order and may only depend on EARLIER steps, so a plan has no
cycles by construction (ARCHITECTURE 4.2). `done_when` is one string:

  tool_ok          every tool the step used worked (the default)
  file:<pattern>   a file matching the pattern was made or changed during the step
  text:<what>      a model-judged statement, used only when nothing typed fits
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ragnarok.policy.permissions import Risk

MAX_STEPS = 8
RISK_ORDER = {Risk.R0: 0, Risk.R1: 1, Risk.R2: 2, Risk.R3: 3}
STATES = (
    "pending", "running", "waiting_approval", "done", "failed", "blocked", "skipped", "interrupted",
)  # fmt: skip
_STEP_ID = re.compile(r"^s\d{1,2}$")


class PlanError(ValueError):
    """The plan cannot be run (the message goes back to the planner, or to the user)."""


@dataclass
class Step:
    id: str
    title: str
    uses: list[str] = field(default_factory=list)
    after: list[str] = field(default_factory=list)
    done_when: str = "tool_ok"

    def as_json(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "uses": self.uses,
            "after": self.after,
            "done_when": self.done_when,
        }


@dataclass
class Plan:
    goal: str
    steps: list[Step]

    def step(self, step_id: str) -> Step:
        return next(s for s in self.steps if s.id == step_id)

    def as_json(self) -> dict:
        return {"goal": self.goal, "steps": [s.as_json() for s in self.steps]}

    @classmethod
    def from_json(cls, data: dict) -> Plan:
        return cls(
            str(data.get("goal", "")),
            [
                Step(
                    str(s["id"]),
                    str(s["title"]),
                    [str(u) for u in s.get("uses", [])],
                    [str(a) for a in s.get("after", [])],
                    str(s.get("done_when") or "tool_ok"),
                )
                for s in data.get("steps", [])
            ],
        )


PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "uses": {"type": "array", "items": {"type": "string"}},
                    "after": {"type": "array", "items": {"type": "string"}},
                    "done_when": {"type": "string"},
                },
                "required": ["id", "title", "uses", "after", "done_when"],
            },
        }
    },
    "required": ["steps"],
}


def normalise_done_when(text: str) -> str:
    """Anything the planner wrote becomes one of the three forms; unknown text is a model check."""
    text = (text or "").strip()
    low = text.lower()
    if low in ("", "tool_ok", "ok", "success", "tools ok"):
        return "tool_ok"
    if low.startswith("file:") and text[5:].strip():
        pattern = text[5:].strip()
        # A file check is only trustworthy as a wildcard on the kind of file ("*.pptx"): a plain
        # name is a guess at something the planner cannot know, and would fail good work.
        return f"file:{pattern}" if "*" in pattern or "?" in pattern else "tool_ok"
    condition = text[5:].strip() if low.startswith("text:") else text
    if not condition or re.search(r"<[^>]*>", condition):
        return "tool_ok"  # a template with a placeholder in it is not a condition
    return "text:" + condition


def validate(
    plan: Plan, tools: dict[str, Risk], *, allowed: set[str] | None = None
) -> dict[str, Risk]:
    """Check a plan before anything runs. Returns each step's risk (the highest of its tools).

    Raises PlanError with a message the planner can act on.
    """
    if not plan.steps:
        raise PlanError("the plan has no steps")
    if len(plan.steps) > MAX_STEPS:
        raise PlanError(f"a plan has at most {MAX_STEPS} steps; split the goal")
    seen: set[str] = set()
    risks: dict[str, Risk] = {}
    for step in plan.steps:
        if not _STEP_ID.match(step.id) or step.id in seen:
            raise PlanError(f"step ids are s1, s2, ... and must be unique (got {step.id!r})")
        if not step.title.strip():
            raise PlanError(f"step {step.id} has no title")
        for dep in step.after:
            if dep not in seen:
                raise PlanError(f"step {step.id} depends on {dep!r}, which is not an earlier step")
        step.done_when = normalise_done_when(step.done_when)
        highest = Risk.R0
        for name in step.uses:
            if name not in tools or (allowed is not None and name not in allowed):
                raise PlanError(f"step {step.id} uses {name!r}, which is not an available tool")
            if RISK_ORDER[tools[name]] > RISK_ORDER[highest]:
                highest = tools[name]
        risks[step.id] = highest
        seen.add(step.id)
    return risks


def dependents(plan: Plan, step_id: str) -> list[str]:
    """Every step that depends on `step_id`, directly or through others."""
    found: set[str] = set()
    changed = True
    while changed:
        changed = False
        for step in plan.steps:
            if step.id not in found and any(d == step_id or d in found for d in step.after):
                found.add(step.id)
                changed = True
    return [s.id for s in plan.steps if s.id in found]
