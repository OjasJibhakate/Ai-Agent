"""The always-on profile block (docs/MEMORY.md section 4.2).

A few hundred tokens of pinned memories, top preferences and project facts that go into every
prompt, so the agent knows the user from the first message of a new day without any search.
The block only changes when memory changes, which keeps the prompt prefix cacheable.
"""

from __future__ import annotations

from ragnarok.memory.models import Memory
from ragnarok.memory.store import MemoryStore

EMPTY = "(nothing stored about the user yet)"


def approx_tokens(text: str) -> int:
    return len(text) // 4 + 1  # rough, but stable and cheap


def render_profile(memories: list[Memory], max_tokens: int = 400) -> tuple[str, list[int]]:
    """Bullet list of memory contents within the token budget, plus the ids that made it in."""
    lines, ids, used = [], [], 0
    for m in memories:
        line = f"- {m.content}"
        cost = approx_tokens(line)
        if used + cost > max_tokens:
            break
        lines.append(line)
        ids.append(m.id)
        used += cost
    return ("\n".join(lines) if lines else EMPTY), ids


class ProfileCache:
    """Rebuilds the block only when `store.version()` changes."""

    def __init__(self, store: MemoryStore, max_tokens: int = 400, limit: int = 12) -> None:
        self.store = store
        self.max_tokens = max_tokens
        self.limit = limit
        self._version: tuple | None = None
        self._value: tuple[str, list[int]] = (EMPTY, [])
        self.rebuilds = 0

    def get(self) -> tuple[str, list[int]]:
        version = self.store.version()
        if version != self._version:
            self._value = render_profile(self.store.profile_memories(self.limit), self.max_tokens)
            self._version = version
            self.rebuilds += 1
        return self._value
