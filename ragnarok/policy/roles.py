"""Role-based access control on top of the permission profiles (NFR4.3).

A profile (the chat's /mode) says when to ASK. A role says what is allowed AT ALL, whoever asks:

  owner       everything the profile allows (the default; nothing changes at the keyboard)
  editor      reading and local changes (R0-R2); never destructive tools (R3)
  viewer      reading only (R0); nothing that changes a file, sends or runs anything
  automation  for a hands-off setup: reading only, plus the reminder and
              memory tools that only leave a note for the owner; changes are refused

`check` in `permissions.py` applies the role after the profile, so a role can only take permissions
away. Moving to a role with fewer rights is free; moving to one with more asks the owner first.
"""

from __future__ import annotations

from dataclasses import dataclass

from ragnarok.policy.permissions import Risk

_ORDER = {Risk.R0: 0, Risk.R1: 1, Risk.R2: 2, Risk.R3: 3}


@dataclass(frozen=True)
class Role:
    name: str
    summary: str
    max_risk: Risk
    allow: frozenset[str] = frozenset()  # tools allowed above max_risk (named exceptions)
    deny: frozenset[str] = frozenset()  # tools never allowed in this role

    def permits(self, tool_name: str, risk: Risk) -> bool:
        if tool_name in self.deny:
            return False
        return _ORDER[risk] <= _ORDER[self.max_risk] or tool_name in self.allow


ROLES: dict[str, Role] = {
    "owner": Role("owner", "everything the mode allows", Risk.R3),
    "editor": Role(
        "editor",
        "read and change local things; no deleting or commands",
        Risk.R2,
        deny=frozenset({"run_command", "delete_path", "use_computer"}),
    ),
    "viewer": Role("viewer", "read only; nothing is changed, sent or run", Risk.R0),
    "automation": Role(
        "automation",
        "unattended work: read, and leave notes in the Inbox",
        Risk.R0,
        allow=frozenset({"set_reminder", "cancel_reminder", "list_reminders", "memory_save"}),
    ),
}
DEFAULT_ROLE = "owner"


def rank(name: str) -> int:
    """How much a role may do, as a number (higher is more)."""
    role = ROLES[name]
    return _ORDER[role.max_risk] * 2 + (0 if role.deny else 1)


def get(name: str) -> Role:
    try:
        return ROLES[name]
    except KeyError:
        raise ValueError(f"role must be one of: {', '.join(ROLES)}") from None
