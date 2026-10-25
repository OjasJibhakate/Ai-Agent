"""Colours and fonts for the windows. One dark theme, the same hues as the terminal."""

from __future__ import annotations

BG = "#0e1118"  # window
PANEL = "#151926"  # header, input area, cards
BUBBLE_ME = "#3a3477"  # your messages
BUBBLE_BOT = "#1d2335"  # Ragnarok's messages
BORDER = "#2b3247"
TEXT = "#e8ecf4"
DIM = "#7d8aa3"
VIOLET = "#a78bfa"
CYAN = "#22d3ee"
AMBER = "#e8b95f"
GREEN = "#78d696"
CORAL = "#f08080"
SKY = "#60a5fa"

UI = ("Segoe UI", 10)
UI_BOLD = ("Segoe UI Semibold", 10)
SMALL = ("Segoe UI", 8)
MONO = ("Consolas", 9)
TITLE = ("Segoe UI Semibold", 11)

STEP_GLYPH = {  # a plan step's state -> (glyph, colour)
    "running": ("●", CYAN),
    "done": ("✓", GREEN),
    "failed": ("✗", CORAL),
    "retry": ("↻", AMBER),
    "blocked": ("·", DIM),
    "waiting_approval": ("◐", AMBER),
    "replan": ("✦", VIOLET),
}
ALERT = {  # what arrived by itself -> (label, colour)
    "reminder": ("Reminder", AMBER),
    "file": ("New file", SKY),
    "result": ("Done", GREEN),
    "failed": ("Problem", CORAL),
}
