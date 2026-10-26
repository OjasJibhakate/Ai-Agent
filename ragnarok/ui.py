"""Terminal look and feel, in plain ANSI (no extra dependencies).

One hue per kind of thing, so the screen reads at a glance: violet for the assistant, sky blue for
you and for commands, teal for the model and tools, amber for memory, green for success, coral for
errors. Chrome (rules, labels) is a quiet slate. Where the terminal supports 24-bit colour (Windows
Terminal, VS Code, anything that sets COLORTERM) the same hues are drawn from a finer palette with
a smooth violet-to-cyan gradient; elsewhere they fall back to 256 colours. Colour turns itself off
when output is not a terminal or NO_COLOR is set; RAGNAROK_256=1 forces the 256-colour palette. If
the console cannot print box-drawing characters (or RAGNAROK_ASCII=1) it falls back to plain ASCII.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import sys
import textwrap
import time
from dataclasses import dataclass
from typing import TextIO

from ragnarok import __version__

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
FRAMES = "✢✳✶✻✽✻✶✳"  # a star that opens and closes
ASCII_FRAMES = "|/-\\"

UNICODE = {
    "prompt": ">", "bot": "●", "tool": "⎿", "ok": "✓", "no": "✗", "live": "●", "spark": "✦",
    "idle": "○", "retry": "↻", "wait": "◐", "dot": "·", "tl": "╭", "tr": "╮", "bl": "╰", "br": "╯",
    "h": "─", "v": "│", "ellipsis": "…",
}  # fmt: skip
PLAIN = {
    "prompt": ">", "bot": "*", "tool": "|_", "ok": "+", "no": "x", "live": "*", "spark": "*",
    "idle": "o", "retry": "~", "wait": "?", "dot": "-", "tl": "+", "tr": "+", "bl": "+", "br": "+",
    "h": "-", "v": "|", "ellipsis": "...",
}  # fmt: skip

# 256-colour codes
SKY, VIOLET, TEAL, AMBER, GREEN, CORAL, STEEL, MINT = 75, 141, 80, 179, 114, 174, 61, 115
TITLE_GRADIENT = (141, 135, 105, 75, 45, 44, 43, 42)  # violet -> cyan
TYPE_COLOR = {  # memory types
    "preference": VIOLET, "semantic": TEAL, "project": SKY, "episodic": AMBER,
    "procedural": MINT, "summary": STEEL,
}  # fmt: skip

# The same hues as 24-bit colour, tuned to sit together on a dark background.
RGB = {
    SKY: (96, 165, 250), VIOLET: (167, 139, 250), TEAL: (45, 212, 191), AMBER: (232, 185, 95),
    GREEN: (120, 214, 150), CORAL: (240, 128, 128), STEEL: (100, 116, 139), MINT: (110, 231, 183),
}  # fmt: skip
GRADIENT_STOPS = ((167, 139, 250), (96, 165, 250), (34, 211, 238))  # violet -> blue -> cyan
BASE16 = (
    (0, 0, 0), (205, 49, 49), (13, 188, 121), (229, 229, 16), (36, 114, 200), (188, 63, 188),
    (17, 168, 205), (229, 229, 229), (102, 102, 102), (241, 76, 76), (35, 209, 139),
    (245, 245, 67), (59, 142, 234), (214, 112, 214), (41, 184, 219), (255, 255, 255),
)  # fmt: skip


ALERTS = {  # kind of thing that arrived by itself -> (label, colour)
    "reminder": ("Reminder", AMBER), "file": ("New file", SKY),
    "result": ("Done", GREEN), "failed": ("Problem", CORAL),
    "suggestion": ("Suggestion", VIOLET), "mail": ("New mail", SKY),
}  # fmt: skip


def visible_len(text: str) -> int:
    return len(_ANSI.sub("", text))


def _xterm_rgb(code: int) -> tuple[int, int, int]:
    """The RGB value a 256-colour code stands for (cube and grey ramp; the base 16 approximated)."""
    if code >= 232:
        level = 8 + 10 * (code - 232)
        return level, level, level
    if code >= 16:
        n = code - 16
        steps = (0, 95, 135, 175, 215, 255)
        return steps[n // 36], steps[n // 6 % 6], steps[n % 6]
    return BASE16[code]


def _supports_truecolor() -> bool:
    if os.environ.get("RAGNAROK_256") == "1":
        return False
    return (
        os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit")
        or "WT_SESSION" in os.environ
        or os.environ.get("TERM_PROGRAM") == "vscode"
    )


def _enable_windows_vt() -> bool:
    """Ask the Windows console to interpret ANSI escape codes."""
    try:
        import ctypes

        kernel = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not kernel.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:
        return False


def _can_print(stream: TextIO, text: str) -> bool:
    try:
        text.encode(getattr(stream, "encoding", None) or "ascii")
        return True
    except (UnicodeEncodeError, LookupError):
        return False


@dataclass(frozen=True)
class Welcome:
    model: str
    memories: int
    gpu: str  # "100% GPU", "loading", ...
    folder: str
    first_run: bool


class Console:
    def __init__(
        self,
        out: TextIO | None = None,
        *,
        color: bool | None = None,
        ascii_only: bool | None = None,
        truecolor: bool | None = None,
        animate: bool | None = None,
    ) -> None:
        self.out = out or sys.stdout
        self.tty = bool(getattr(self.out, "isatty", lambda: False)())
        self.color = (self.tty and "NO_COLOR" not in os.environ) if color is None else color
        if color is None and self.color and os.name == "nt":
            self.color = _enable_windows_vt()  # auto-detect only; an explicit colour=True wins
        if truecolor is None:  # auto-detect only; an explicit colour=True stays on 256 colours
            truecolor = self.color and color is None and _supports_truecolor()
        self.truecolor = truecolor and self.color
        wants_ascii = os.environ.get("RAGNAROK_ASCII") == "1" if ascii_only is None else ascii_only
        self.ascii = wants_ascii or not _can_print(self.out, "".join(UNICODE.values()) + FRAMES)
        self.g = PLAIN if self.ascii else UNICODE
        # Motion only on a real colour terminal, never into a log or a pipe.
        if animate is None:
            animate = self.tty and self.color and os.environ.get("RAGNAROK_NO_ANIM") != "1"
        self.animate = animate and self.color
        self.pace = 1.0  # multiplier on every animation delay (tests set 0)

    async def beat(self, seconds: float) -> None:
        """Wait one animation tick (without blocking the event loop)."""
        if self.pace:
            await asyncio.sleep(seconds * self.pace)

    # ---- styling ---------------------------------------------------------------------------

    def fg(self, code: int | str) -> str:
        """The SGR colour parameters for a palette code (or a ready-made string)."""
        if isinstance(code, str):
            return code
        if self.truecolor:
            r, g, b = RGB.get(code) or _xterm_rgb(code)
            return f"38;2;{r};{g};{b}"
        return f"38;5;{code}"

    def paint(self, code: int | str, text: str, *, bold: bool = False, dim: bool = False) -> str:
        if not self.color or not text:
            return text
        attrs = ("1;" if bold else "") + ("2;" if dim else "")
        return f"\x1b[{attrs}{self.fg(code)}m{text}\x1b[0m"

    def dim(self, t: str) -> str:
        return self.paint(STEEL, t)

    def faint(self, t: str) -> str:
        return self.paint("0", t, dim=True)

    def bold(self, t: str) -> str:
        return f"\x1b[1m{t}\x1b[0m" if self.color else t

    def sky(self, t: str) -> str:
        return self.paint(SKY, t)

    def violet(self, t: str) -> str:
        return self.paint(VIOLET, t)

    def teal(self, t: str) -> str:
        return self.paint(TEAL, t)

    def amber(self, t: str) -> str:
        return self.paint(AMBER, t)

    def good(self, t: str) -> str:
        return self.paint(GREEN, t)

    def bad(self, t: str) -> str:
        return self.paint(CORAL, t)

    def _ramp_rgb(self, t: float, strength: float = 1.0) -> tuple[int, int, int]:
        pos = min(1.0, max(0.0, t)) * (len(GRADIENT_STOPS) - 1)
        i = min(int(pos), len(GRADIENT_STOPS) - 2)
        frac = pos - i
        a, b = GRADIENT_STOPS[i], GRADIENT_STOPS[i + 1]
        r, g, bl = (round((a[k] + (b[k] - a[k]) * frac) * strength) for k in range(3))
        return r, g, bl

    def ramp(self, t: float, strength: float = 1.0) -> str:
        """The colour a fraction `t` (0 to 1) of the way along the violet-to-cyan gradient."""
        if not self.truecolor:
            steps = len(TITLE_GRADIENT)
            return f"38;5;{TITLE_GRADIENT[min(int(min(1.0, max(0.0, t)) * steps), steps - 1)]}"
        r, g, bl = self._ramp_rgb(t, strength)
        return f"38;2;{r};{g};{bl}"

    def gradient(self, text: str, glow: float | None = None) -> str:
        """Letters shaded from violet to cyan; plain text when colour is off.

        `glow` is a character position where a bright highlight sits (used to sweep light along
        the wordmark); None draws the plain gradient.
        """
        if not self.color:
            return text
        last = max(1, len(text) - 1)
        parts = []
        for i, ch in enumerate(text):
            if ch == " ":
                parts.append(ch)
                continue
            lift = 0.0 if glow is None else max(0.0, 1 - abs(i - glow) / 3)
            if self.truecolor:
                r, g, b = (round(c + (255 - c) * lift * 0.8) for c in self._ramp_rgb(i / last))
                code: int | str = f"38;2;{r};{g};{b}"
            else:
                code = 255 if lift > 0.5 else self.ramp(i / last)
            parts.append(self.paint(code, ch, bold=True))
        return "".join(parts)

    def shimmer(self, text: str, centre: float) -> str:
        """Quiet slate text with a soft highlight centred on character position `centre`."""
        if not self.color:
            return text
        parts = []
        for i, ch in enumerate(text):
            lift = max(0.0, 1 - abs(i - centre) / 4)
            if self.truecolor:
                r, g, b = (
                    round(lo + (hi - lo) * lift)
                    for lo, hi in zip((120, 132, 150), (240, 244, 255), strict=True)
                )
                parts.append(self.paint(f"38;2;{r};{g};{b}", ch))
            else:
                parts.append(self.paint(255 if lift > 0.5 else STEEL, ch))
        return "".join(parts)

    def rule(self, width: int | None = None) -> str:
        """A hairline: a faded gradient on 24-bit terminals, quiet slate elsewhere."""
        n = self.width if width is None else width
        line = self.g["h"] * n
        if not self.color:
            return line
        if not self.truecolor:
            return self.dim(line)
        return "".join(self.paint(self.ramp(i / max(1, n - 1), 0.5), self.g["h"]) for i in range(n))

    @property
    def width(self) -> int:
        return max(52, min(shutil.get_terminal_size((80, 24)).columns - 2, 78))

    # ---- output ----------------------------------------------------------------------------

    def write(self, text: str = "", end: str = "\n") -> None:
        self.out.write(text + end)
        self.out.flush()

    def _edge(self, left: str, right: str, width: int) -> list[str]:
        """The cells of a box's top or bottom edge, shaded along the gradient."""
        fill = width - 2
        cells = [left] + [self.g["h"]] * fill + [right]
        if not self.color:
            return cells
        if not self.truecolor:
            return [self.paint(VIOLET, c) for c in cells]
        return [self.paint(self.ramp(i / max(1, width - 1), 0.8), c) for i, c in enumerate(cells)]

    def banner_lines(self, w: Welcome, glow: float | None = None) -> list[str]:
        """The welcome screen as lines. `glow` lights the wordmark at one position (animation)."""
        g = self.g
        box = self.width - 1  # one column of margin keeps it inside the console width

        def row(content: str = "", right: str = "") -> str:
            gap = max(0, box - 4 - visible_len(content) - visible_len(right))
            side = self.paint(VIOLET, g["v"])
            return f" {side} {content}{' ' * gap}{right} {side}"

        def label(text: str) -> str:
            return self.dim(f"{text:<8}")

        room = box - 4 - 8
        folder = (
            w.folder
            if len(w.folder) <= room
            else g["ellipsis"] + w.folder[-(room - len(g["ellipsis"])) :]
        )
        live = g["live"]
        if "GPU" in w.gpu:
            gpu = self.good(f"{live} {w.gpu}")
        elif w.gpu == "offline":
            gpu = self.bad(f"{live} {w.gpu}")
        else:
            gpu = self.amber(f"{live} {w.gpu}")
        count = f"{w.memories} memor{'y' if w.memories == 1 else 'ies'}"
        dot = f" {self.dim(g['dot'])} "
        welcome = "Welcome to " if w.first_run else "Welcome back to "
        title = (
            self.paint(VIOLET, g["spark"], bold=True)
            + " "
            + self.bold(welcome)
            + self.gradient("Ragnarok", glow)
        )
        lines = [
            "",
            " " + "".join(self._edge(g["tl"], g["tr"], box)),
            row(title, self.dim(f"v{__version__}")),
            row(),
            row(self.faint("Private by design. Everything stays on this laptop.")),
            row(),
            row(f"{label('model')}{self.teal(w.model)}{dot}{self.faint('local')}{dot}{gpu}"),
            row(f"{label('memory')}{self.amber(count)}{dot}{self.good(live + ' encrypted')}"),
            row(f"{label('folder')}{self.dim(folder)}"),
            " " + "".join(self._edge(g["bl"], g["br"], box)),
            "",
        ]
        if w.first_run:
            q = chr(34)
            lines += [
                " " + self.bold("Tips for getting started"),
                f"  {self.dim('1.')} Tell me something to remember: "
                f"{self.sky(q + 'I prefer meetings after 2pm' + q)}",
                f"  {self.dim('2.')} Ask me to do things: {self.sky(q + 'open steam' + q)}, "
                f"{self.sky(q + 'find my data structure files' + q)}",
                f"  {self.dim('3.')} {self.sky('/help')} lists every command",
            ]
        else:
            lines += [
                "  Ask anything, or tell me something to remember.",
                "  "
                + self.dim(f"{self.sky('/help')} commands {g['dot']} {self.sky('/quit')} to leave"),
            ]
        return [*lines, ""]

    def banner(self, w: Welcome) -> None:
        for line in self.banner_lines(w):
            self.write(line)

    async def play_banner(self, w: Welcome) -> None:
        """The welcome screen with motion: the box draws itself, then light sweeps the name."""
        if not self.animate:
            self.banner(w)
            return
        lines = self.banner_lines(w)
        self.write(lines[0])
        top = self._edge(self.g["tl"], self.g["tr"], self.width - 1)
        self.out.write(" ")
        for i in range(0, len(top), 6):  # the top edge draws itself in a few strokes
            self.out.write("".join(top[i : i + 6]))
            self.out.flush()
            await self.beat(0.012)
        self.out.write("\n")
        for line in lines[2:]:
            self.write(line)
            await self.beat(0.035)
        up = len(lines) - 2  # from the line after the banner back to the title row
        for step in range(-3, len("Ragnarok") + 4):  # a highlight crosses the name
            self.out.write(f"\x1b[{up}A\r{self.banner_lines(w, glow=step)[2]}\x1b[{up}B\r")
            self.out.flush()
            await self.beat(0.04)

    def box(self, title: str, lines: list[str], *, tint: int = SKY) -> None:
        """A small rounded box (used for the recovery code)."""
        g = self.g
        inner = max(visible_len(title) + 4, *(visible_len(line) for line in lines)) + 2
        top = self.paint(tint, g["tl"] + g["h"] + " ") + self.bold(title) + " "
        top += self.paint(tint, g["h"] * max(0, inner - visible_len(title) - 3) + g["tr"])
        self.write(" " + top)
        for line in lines:
            pad = " " * (inner - visible_len(line) - 1)
            self.write(f" {self.paint(tint, g['v'])} {line}{pad}{self.paint(tint, g['v'])}")
        self.write(" " + self.paint(tint, g["bl"] + g["h"] * inner + g["br"]))

    def prompt(self, label: str = "") -> str:
        """A hairline (carrying `label` at its right end), then the input marker."""
        n = self.width - 1
        if label:
            line = self.rule(n - len(label) - 4) + self.dim(f" {label} ") + self.rule(2)
        else:
            line = self.rule(n)
        return f" {line}\n {self.paint(SKY, self.g['prompt'], bold=True)} "

    def assistant_marker(self, kind: str = "bot") -> str:
        if kind == "done":
            return f" {self.paint(GREEN, self.g['ok'], bold=True)} "
        if kind == "warn":
            return f" {self.paint(AMBER, self.g['bot'])} "
        return f" {self.paint(VIOLET, self.g['bot'])} "

    def footer(self, tools: list[tuple[str, bool]], seconds: float) -> None:
        """A dim line under an answer: which tools ran, and how long it took."""
        g = self.g
        counts: dict[tuple[str, bool], int] = {}
        for entry in tools:
            counts[entry] = counts.get(entry, 0) + 1
        times = "x" if self.ascii else "×"
        names = [
            (self.teal(name) if allowed else self.bad(f"{g['no']} {name} blocked"))
            + (self.dim(f" {times}{n}") if n > 1 else "")
            for (name, allowed), n in counts.items()
        ]
        parts = [f"{self.dim(g['tool'])}  " + ", ".join(names)] if names else []
        parts.append(self.faint(f"{seconds:.1f}s"))
        self.write("   " + f" {self.dim(g['dot'])} ".join(parts))

    def approval(self, name: str, args: dict, warning: str = "") -> str:
        """Draw the 'allow this?' card; returns the prompt text for its last line."""
        g = self.g
        side = f"   {self.amber(g['v'])} "
        shown = list(args.items())[:6]
        col = min(18, max([10] + [len(k) + 2 for k, _v in shown]))  # room for the longest name
        room = self.width - 6 - col
        self.write(f"   {self.amber(g['tl'] + g['h'])} {self.bold('Permission needed')}")
        self.write(side + self.teal(name))
        for key, value in shown:
            text = repr(value)
            if len(text) > room:
                text = text[: room - len(g["ellipsis"])] + g["ellipsis"]
            self.write(side + self.dim(f"{key:<{col}}") + text)
        if len(args) > 6:
            self.write(side + self.faint(f"{g['ellipsis']} {len(args) - 6} more"))
        for part in textwrap.wrap(warning, self.width - 6):
            self.write(side + self.faint(part))
        return f"   {self.amber(g['bl'] + g['h'])} Do you want to proceed? {self.dim('[y/N]')} "

    def notice(self, text: str) -> None:
        self.write(" " + self.faint(text))

    def error(self, text: str) -> None:
        self.write(" " + self.bad(f"{self.g['no']} {text}"))

    def success(self, text: str) -> None:
        self.write(" " + self.good(f"{self.g['ok']} {text}"))

    def section(self, title: str) -> None:
        """A bold title with a hairline running out to the right."""
        tail = max(0, self.width - visible_len(title) - 3)
        self.write(" " + self.bold(title) + " " + self.dim(self.g["h"] * tail))

    def help(self) -> None:
        rows = [
            ("/memories", "what I remember about you"),
            ("/profile", "what is always in my prompt"),
            ("/forget N", "delete memory N for good"),
            ("/plan GOAL", "show a step-by-step plan first, then run it"),
            ("/resume", "carry on with a plan that did not finish"),
            ("/inbox", "reminders and results that arrived while you were away"),
            ("/reminders", "what I will remind you of or watch for"),
            ("/cancel N", "stop reminder, schedule or watch N"),
            ("/skill", "list, add, test, disable or remove skills"),
            ("/mode M", "ask, edits or full: how much I do without asking"),
            ("/role R", "owner, editor, viewer or automation: what I may do at all"),
            ("/log", "which tools I used and why they were allowed (/log plan for steps)"),
            ("/suggest", "ideas from what I remember and what is going on"),
            ("/clear", "clear the screen"),
            ("/quit", "leave (I keep learning from this chat)"),
        ]
        self.write()
        self.section("Commands")
        for cmd, what in rows:
            self.write(f"   {self.sky(f'{cmd:<10}')} {self.faint(what)}")
        self.write()

    def memories(self, memories: list) -> None:
        if not memories:
            self.notice("Nothing remembered yet. Tell me something and ask me to remember it.")
            return
        room = self.width - 24
        self.write()
        for m in memories:
            text = m.content
            if len(text) > room:
                text = text[: room - len(self.g["ellipsis"])] + self.g["ellipsis"]
            tag = self.paint(TYPE_COLOR.get(m.memory_type, STEEL), f"{m.memory_type[:10]:<10}")
            self.write(f"   {self.amber(f'm{m.id:<3}')} {tag} {text}")
        self.write(f"   {self.faint(str(len(memories)) + ' shown')}")
        self.write()

    # ---- plans -----------------------------------------------------------------------------

    def plan_card(self, goal: str, rows: list[tuple[str, str, list[str], str, bool]]) -> str:
        """The plan before it runs: each step, what it waits for, and whether it will ask.

        `rows` are (id, title, after, highest risk, will ask). Returns the prompt for the question
        on its last line.
        """
        g = self.g
        side = f"   {self.violet(g['v'])} "
        self.write()
        self.write(
            f"   {self.violet(g['tl'] + g['h'])} {self.bold('Plan')} {self.dim(g['dot'])} "
            f"{len(rows)} steps"
        )
        goal_line = goal if len(goal) <= self.width - 8 else goal[: self.width - 9] + g["ellipsis"]
        self.write(side + self.faint(goal_line))
        for step_id, title, after, _risk, asks in rows:
            tags = []
            if after:
                tags.append(self.dim(f"after {','.join(after)}"))
            if asks:
                tags.append(self.amber("asks you"))
            tail = "  ".join(tags)
            room = self.width - 14 - visible_len(tail)
            shown = title if len(title) <= room else title[: room - 1] + g["ellipsis"]
            gap = " " * max(2, room - len(shown) + 2)
            self.write(f"{side}{self.dim(g['idle'])} {self.sky(f'{step_id:<3}')}{shown}{gap}{tail}")
        return f"   {self.violet(g['bl'] + g['h'])} Run this plan? {self.dim('[y/N]')} "

    def step_line(self, state: str, step_id: str, title: str, note: str = "") -> None:
        """One line as a step changes state."""
        g = self.g
        glyph, paint = {
            "running": (g["live"], self.sky), "done": (g["ok"], self.good),
            "failed": (g["no"], self.bad), "blocked": (g["dot"], self.dim),
            "retry": (g["retry"], self.amber), "waiting_approval": (g["wait"], self.amber),
            "replan": (g["spark"], self.violet),
        }.get(state, (g["idle"], self.dim))  # fmt: skip
        room = self.width - 16
        head = f"{step_id:<3}{title}" if title else f"{step_id:<3}"
        if len(head) > room:
            head = head[: room - 1] + g["ellipsis"]
        tail = f"  {self.faint(note[: self.width - 10 - len(head)])}" if note else ""
        self.write(f"   {paint(glyph)} {head}{tail}")

    # ---- things that arrive by themselves --------------------------------------------------

    def alert(self, kind: str, title: str, body: str = "") -> None:
        """Something happened on its own (a reminder came due, a file arrived)."""
        label, colour = ALERTS.get(kind, ("Inbox", VIOLET))
        sep = self.dim(self.g["dot"])
        self.write()
        self.write(
            " " + self.paint(colour, self.g["spark"], bold=True) + " " + self.bold(label)
            + f" {sep} " + title
        )  # fmt: skip
        shown = 0
        for line in body.splitlines():
            for part in textwrap.wrap(line, self.width - 6) or [""]:
                if shown < 8:
                    self.write("   " + self.faint(part))
                shown += 1
        if self.tty:
            self.out.write("\a")  # the terminal's own bell
            self.out.flush()

    def inbox(self, items: list) -> None:
        """The Inbox, oldest first, with what is new marked."""
        if not items:
            self.notice("Your Inbox is empty.")
            return
        self.write()
        for item in reversed(items):
            _label, colour = ALERTS.get(item.kind, ("Inbox", VIOLET))
            mark = self.paint(colour, self.g["live"]) if item.status == "unread" else " "
            when = item.created_at[5:16]
            room = self.width - 28
            title = (
                item.title
                if len(item.title) <= room
                else item.title[: room - 1] + self.g["ellipsis"]
            )
            self.write(f"   {mark} {self.dim(f'#{item.id:<3}')} {self.faint(when)}  {title}")
            for line in item.body.splitlines()[:3]:
                self.write("        " + self.faint(line[: self.width - 10]))
        self.write()

    def clear(self) -> None:
        if self.tty:
            self.out.write("\x1b[2J\x1b[H")
            self.out.flush()

    # ---- the "thinking" indicator ----------------------------------------------------------

    def spinner(self, label: str = "Thinking") -> Spinner:
        return Spinner(self, label)

    async def farewell(self, note: str) -> None:
        """The last line of a session; the star closes while light passes over the words."""
        self.write()
        if not (self.animate and not self.ascii):
            self.notice(note)
            self.write()
            return
        stars = "✻✶✳✢·"
        for i, star in enumerate(stars):
            colour = self.ramp(i / (len(stars) - 1))
            shimmer = self.shimmer(note, i * len(note) / (len(stars) - 1))
            self.out.write(f"\r {self.paint(colour, star)} {shimmer}\x1b[K")
            self.out.flush()
            await self.beat(0.08)
        self.out.write(f"\r {self.dim('·')} {self.faint(note)}\x1b[K\n\n")
        self.out.flush()


class Spinner:
    """An animated marker shown until the first word of the answer arrives."""

    def __init__(self, console: Console, label: str) -> None:
        self.c = console
        self.label = label
        self._task: asyncio.Task | None = None
        self.active = False

    def start(self) -> None:
        self.active = True
        if not (self.c.tty and self.c.color):
            return  # no animation when output isn't a colour terminal
        self._task = asyncio.create_task(self._spin())

    async def _spin(self) -> None:
        frames = ASCII_FRAMES if self.c.ascii else FRAMES
        text = self.label + self.c.g["ellipsis"]
        began = time.monotonic()
        i = 0
        while True:
            sweep = abs((i % 50) / 25 - 1)  # 1 -> 0 -> 1: the colour glides along the gradient
            star = frames[(i // 2) % len(frames)] if not self.c.ascii else frames[i % len(frames)]
            frame = self.c.paint(self.c.ramp(sweep), star)
            centre = (i * 0.7) % (len(text) + 10) - 5  # light passes over the words, then rests
            waited = time.monotonic() - began
            suffix = self.c.faint(f" ({waited:.0f}s)") if waited >= 1 else ""
            self.c.out.write(f"\r {frame} {self.c.shimmer(text, centre)}{suffix}\x1b[K")
            self.c.out.flush()
            i += 1
            await asyncio.sleep(0.08)

    async def pause(self) -> None:
        """Stop animating and clear the line, so something else can be printed (still active)."""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
            self.c.out.write("\r\x1b[K")
            self.c.out.flush()

    def resume(self) -> None:
        if self.active and self._task is None and self.c.tty and self.c.color:
            self._task = asyncio.create_task(self._spin())

    async def stop(self, marker: str = "bot") -> None:
        """Remove the indicator and print the assistant marker in its place."""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
            self.c.out.write("\r\x1b[K")
        self.c.out.write(self.c.assistant_marker(marker))
        self.c.out.flush()
        self.active = False
