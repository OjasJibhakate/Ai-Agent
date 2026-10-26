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
    r2: Literal["allow", "ask"] = "ask"  # external or hard to undo
    r3: Literal["allow", "ask"] = "ask"  # destructive


@dataclass(frozen=True)
class Decision:
    allowed: bool  # may run now
    ask: bool  # needs an explicit approval first
    rule: str  # which rule decided (recorded in the decision trace)
    reason: str


# Mail leaves the machine and cannot be taken back: it asks in every mode, even `full` (NFR4.3).
SENDS_EMAIL = frozenset({"send_gmail", "send_mail"})

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
    # The owner at the keyboard, with the permission modes of Claude Code (the chat's /mode):
    #   "interactive" (ask)  R0/R1 run; R2 and R3 ask every time
    #   "edits"              R0-R2 run (move, overwrite, download); only R3 (delete, commands) asks
    #   "full"               nothing asks; the hard limits in each tool (vault, system folders,
    #                        destructive commands) still apply. Moving the real mouse always asks.
    "edits": Profile(
        "edits",
        "workspace-write",
        frozenset({"memory_search"}),
        SENDS_EMAIL,
        frozenset(),
        "allow",
        r2="allow",
    ),
    "full": Profile(
        "full",
        "full-access",
        frozenset({"memory_search"}),
        frozenset({"use_computer"}) | SENDS_EMAIL,
        frozenset(),
        "allow",
        r2="allow",
        r3="allow",
    ),
    # triggers and headless runs: never prompts mid-run, so anything with a side effect waits
    "background": Profile(
        "background",
        "read-only",
        # reading is allowed (ARCHITECTURE 9.1); everything else waits for the owner
        frozenset({"memory_search", "find_files", "list_folder", "read_file", "list_reminders"}),
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


def check(profile: str, tool_name: str, risk: Risk, role: str = "owner") -> Decision:
    """Role first (what is allowed at all), then the profile (when to ask). See `roles.py`."""
    from ragnarok.policy import roles  # late: roles imports Risk from this module

    try:
        granted = roles.get(role)
    except ValueError:
        return Decision(False, False, "unknown-role", f"no such role: {role}")
    if not granted.permits(tool_name, risk):
        return Decision(False, False, f"role:{role}", f"the {role} role may not use this tool")
    return _check_profile(profile, tool_name, risk)


def _check_profile(profile: str, tool_name: str, risk: Risk) -> Decision:
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
    setting = p.r2 if risk is Risk.R2 else p.r3
    if setting == "allow":
        return Decision(True, False, f"default:{risk.value}", f"{risk.value} runs in this mode")
    return Decision(False, True, f"default:{risk.value}", "external or destructive: ask every time")
