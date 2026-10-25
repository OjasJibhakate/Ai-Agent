"""When may a tool's own sentence end the turn?

A tool that succeeds can answer the user itself ("Created Talk.pptx in ..."), which saves a model
call of one to three seconds. But "make a ppt and then open it" has a second step, and answering
after the first would silently drop it. So a result is final only when the request has no word that
adds a step; otherwise the model sees the result and carries on.
"""

from __future__ import annotations

import re

from ragnarok.runtime.tools import CURRENT_TEXT, ToolResult

ADDS_A_STEP = re.compile(
    r"\b(and|then|after that|afterwards|also|next|before|once|plus|later)\b", re.IGNORECASE
)


def more_steps(user_text: str | None = None) -> bool:
    return bool(ADDS_A_STEP.search(CURRENT_TEXT.get() if user_text is None else user_text))


FINISHED_NOTE = (
    "\n[Done. Unless the user asked for another step, tell them this result; do not list, open "
    "or re-check what was made.]"
)


def reply(text: str) -> str | ToolResult:
    """`text` as the final answer, or as plain output for the model when more steps follow."""
    return text + FINISHED_NOTE if more_steps() else ToolResult(text, final=True)
