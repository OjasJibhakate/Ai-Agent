"""The one place that decides whether a tool call may run (AGENTS.md hard rule 3).

Same rule order as Claude Code and Codex: deny, then ask, then allow, then the default for the
tool's risk tier (ARCHITECTURE section 9). Profiles are code for now; a YAML file comes with the
policy work for skills.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal


class Risk(StrEnum):
    R0 = "R0"  # read-only
    R1 = "R1"  # local, reversible write
    R2 = "R2"  # external or hard to undo
    R3 = "R3"  # destructive


@dataclass(frozen=True)
class Profile:
    name: str
    sandbox: str
    allow: frozenset[str]
    ask: frozenset[str]
    deny: frozenset[str]
    r1: Literal["allow", "ask"]  # default for R1 tools


@dataclass(frozen=True)
class Decision:
    allowed: bool  # may run now
    ask: bool  # needs an explicit approval first
    rule: str  # which rule decided (recorded in the decision trace)
    reason: str


PROFILES: dict[str, Profile] = {
    # the owner at the keyboard
    "interactive": Profile(
        "interactive",
        "workspace-write",
        frozenset({"memory_search"}),
        frozenset(),
        frozenset(),
        "allow",
    ),
    # triggers and headless runs: never prompts mid-run, so anything with a side effect waits
    "background": Profile(
        "background",
        "read-only",
        frozenset({"memory_search"}),
        frozenset({"*"}),
        frozenset(),
        "ask",
    ),
    # Used only inside a screen task the user has just approved (the `use_computer` tool): it
    # allows exactly the screen actions and nothing else. Every action is still checked against it.
    "screen": Profile(
        "screen",
        "full-access",
        frozenset({"screen_click", "screen_type", "screen_key", "screen_scroll", "screen_wait"}),
        frozenset(),
        frozenset(),
        "ask",
    ),
}


def check(profile: str, tool_name: str, risk: Risk) -> Decision:
    try:
        p = PROFILES[profile]
    except KeyError:
        return Decision(False, False, "unknown-profile", f"no such profile: {profile}")

    if tool_name in p.deny:
        return Decision(False, False, f"deny:{tool_name}", "denied by profile")
    if tool_name in p.allow:
        return Decision(True, False, f"allow:{tool_name}", "allowed by profile")
    if tool_name in p.ask:
        return Decision(False, True, f"ask:{tool_name}", "profile asks before running this tool")
    if "*" in p.ask:
        return Decision(False, True, "ask:*", "profile asks before running any other tool")

    if risk is Risk.R0:
        return Decision(True, False, "default:R0", "read-only tools run freely")
    if risk is Risk.R1:
        if p.r1 == "allow":
            return Decision(True, False, "default:R1", "local reversible write")
        return Decision(False, True, "default:R1", "writes need approval in this profile")
    return Decision(False, True, f"default:{risk.value}", "external or destructive: ask every time")
