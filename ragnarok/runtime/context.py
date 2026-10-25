"""Builds the prompt for a chat turn. Stable parts come first so Ollama can reuse its cached prefix.

system message = shared prefix (system.txt) + chat rules (chat.txt) + <profile> block
                 -> identical on every turn until memory changes
history        = plain user/assistant text of this session
last user turn = <memory_context> (retrieved for this message, if any) + the message
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import cache
from pathlib import Path

from ragnarok.memory.models import MemoryHit, format_memory
from ragnarok.memory.profile import approx_tokens

PROMPTS = Path(__file__).parent / "prompts"
MEMORY_BUDGET_TOKENS = 1000
MAX_HISTORY_MESSAGES = 12


@cache
def _prompt(name: str) -> str:
    return (PROMPTS / name).read_text(encoding="utf-8").strip()


def system_message(profile_text: str) -> dict[str, str]:
    content = (
        f"{_prompt('system.txt')}\n\n{_prompt('chat.txt')}\n\n<profile>\n{profile_text}\n</profile>"
    )
    return {"role": "system", "content": content}


def memory_block(
    hits: Sequence[MemoryHit], skip_ids: Sequence[int] = (), budget: int = MEMORY_BUDGET_TOKENS
) -> str:
    """Retrieved memories as data lines with id, type, origin, confidence and date."""
    lines, used = [], 0
    for h in hits:
        m = h.memory
        if m.id in skip_ids:  # already in the profile block
            continue
        line = format_memory(m)
        cost = approx_tokens(line)
        if used + cost > budget:
            break
        lines.append(line)
        used += cost
    return "<memory_context>\n" + "\n".join(lines) + "\n</memory_context>" if lines else ""


def build_messages(
    profile_text: str,
    history: Sequence[dict[str, str]],
    user_text: str,
    hits: Sequence[MemoryHit] = (),
    profile_ids: Sequence[int] = (),
) -> list[dict[str, str]]:
    block = memory_block(hits, skip_ids=profile_ids)
    last = f"{block}\n\n{user_text}" if block else user_text
    return [
        system_message(profile_text),
        *history[-MAX_HISTORY_MESSAGES:],
        {"role": "user", "content": last},
    ]
