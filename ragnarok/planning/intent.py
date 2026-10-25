"""Is this request big enough to plan? (ARCHITECTURE 4.1)

A request goes to the planner when it asks for three or more different actions ("find the invoice,
summarize it, make a deck and put the dates in a spreadsheet"). Two actions are left to the normal
loop, which handles "make a ppt and open it" in one go without a plan to approve first. This is a
plain word check: no model call, so it costs nothing on the hot path (NFR2.1).
"""

from __future__ import annotations

import os
import re

ACTION_WORDS = (
    "find|search|read|open|summari[sz]e|make|create|write|build|generate|draft|save|copy|move|rename|"
    "delete|download|send|email|compare|analy[sz]e|extract|convert|translate|list|check|fix|test|"
    "rewrite|organi[sz]e|sort|collect|combine|merge|schedule|book|print|upload|compile|review"
)
_ACTION = re.compile(rf"\b({ACTION_WORDS})(?:s|es|d|ing)?\b", re.IGNORECASE)
MIN_ACTIONS = 3


def count_actions(text: str) -> int:
    return len({m.group(1).lower() for m in _ACTION.finditer(text)})


def looks_multi_step(text: str) -> bool:
    if os.environ.get("RAGNAROK_NO_PLAN") == "1" or text.lstrip().startswith("/"):
        return False
    if len(text) > 1500:
        return False  # a pasted document, not a to-do list
    return count_actions(text) >= MIN_ACTIONS
