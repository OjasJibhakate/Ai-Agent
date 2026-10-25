"""The owner's permission mode: how much the agent may do before it asks.

Same idea as Claude Code's modes, switchable while chatting (`/mode edits`, `/mode full`):

  ask    the default. Reading and creating new things runs; overwriting, moving, downloading files,
         deleting and running commands each ask first.
  edits  also overwrite, move and download without asking. Deleting and commands still ask.
  full   nothing asks (the mouse-and-keyboard task still does). The hard limits stay on in every
         mode: the vault and system folders are off limits, deletes only go to the Recycle Bin,
         and a short list of destructive commands is refused.

`ask` reads Desktop, Documents and Downloads. `edits` and `full` read and write anywhere in the
user's profile (except the places no mode may touch).
"""

from __future__ import annotations

MODES = ("ask", "edits", "full")
PROFILE_OF = {"ask": "interactive", "edits": "edits", "full": "full"}
SUMMARY = {
    "ask": "asks before overwriting, moving, deleting, downloading files and running commands",
    "edits": "overwrites, moves and downloads without asking; deleting and commands still ask",
    "full": "asks nothing (hard limits stay on: vault, system folders, destructive commands)",
}


class Mode:
    """The mode in force right now; shared by the chat, the agent and the tools that care."""

    def __init__(self, name: str = "ask") -> None:
        self.name = "ask"
        self.set(name)

    def set(self, name: str) -> None:
        name = name.strip().lower()
        if name not in MODES:
            raise ValueError(f"mode must be one of: {', '.join(MODES)}")
        self.name = name

    @property
    def profile(self) -> str:
        return PROFILE_OF[self.name]

    @property
    def wide(self) -> bool:
        """May the file tools reach the whole user profile, not only Desktop/Documents/Downloads?"""
        return self.name != "ask"

    @classmethod
    def from_env(cls, value: str | None) -> Mode:
        """The starting mode from RAGNAROK_MODE; anything unrecognised means the safe default."""
        try:
            return cls(value or "ask")
        except ValueError:
            return cls("ask")
