"""Terminal look and feel, in plain ANSI (no extra dependencies).

One hue per kind of thing, so the screen reads at a glance: violet for the assistant, sky blue for
you and for commands, teal for the model and tools, amber for memory, green for success, coral for
errors. Colour turns itself off when output is not a terminal or NO_COLOR is set. If the console
cannot print box-drawing characters (or RAGNAROK_ASCII=1) it falls back to plain ASCII.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import sys
from dataclasses import dataclass
from typing import TextIO

from ragnarok import __version__

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"  # braille spinner
ASCII_FRAMES = "|/-\\"

UNICODE = {
    "prompt": "›", "bot": "◆", "tool": "↳", "ok": "✓", "no": "✗",
    "dot": "·", "tl": "╭", "tr": "╮", "bl": "╰", "br": "╯",
    "h": "─", "v": "│", "ellipsis": "…",
}  # fmt: skip
PLAIN = {
    "prompt": ">", "bot": "*", "tool": "->", "ok": "+", "no": "x", "dot": "-", "tl": "+",
    "tr": "+", "bl": "+", "br": "+", "h": "-", "v": "|", "ellipsis": "...",
}  # fmt: skip

# 256-colour codes
SKY, VIOLET, TEAL, AMBER, GREEN, CORAL, STEEL, MINT = 75, 141, 80, 179, 114, 174, 61, 115
TITLE_GRADIENT = (141, 135, 105, 75, 45, 44, 43, 42)  # violet -> cyan
TYPE_COLOR = {  # memory types
    "preference": VIOLET, "semantic": TEAL, "project": SKY, "episodic": AMBER,
    "procedural": MINT, "summary": STEEL,
}  # fmt: skip


def visible_len(text: str) -> int:
    return len(_ANSI.sub("", text))


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
    ) -> None:
        self.out = out or sys.stdout
        self.tty = bool(getattr(self.out, "isatty", lambda: False)())
        self.color = (self.tty and "NO_COLOR" not in os.environ) if color is None else color
        if color is None and self.color and os.name == "nt":
            self.color = _enable_windows_vt()  # auto-detect only; an explicit colour=True wins
        wants_ascii = os.environ.get("RAGNAROK_ASCII") == "1" if ascii_only is None else ascii_only
        self.ascii = wants_ascii or not _can_print(self.out, "".join(UNICODE.values()) + FRAMES)
        self.g = PLAIN if self.ascii else UNICODE

    # ---- styling ---------------------------------------------------------------------------

    def paint(self, code: int | str, text: str, *, bold: bool = False, dim: bool = False) -> str:
        if not self.color or not text:
            return text
        attrs = ("1;" if bold else "") + ("2;" if dim else "")
        color = code if isinstance(code, str) else f"38;5;{code}"
        return f"\x1b[{attrs}{color}m{text}\x1b[0m"

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

    def gradient(self, text: str) -> str:
        """Letters shaded from violet to cyan; plain text when colour is off."""
        if not self.color:
            return text
        return "".join(
            self.paint(TITLE_GRADIENT[i * len(TITLE_GRADIENT) // len(text)], ch, bold=True)
            for i, ch in enumerate(text)
        )

    @property
    def width(self) -> int:
        return max(52, min(shutil.get_terminal_size((80, 24)).columns - 2, 78))

    # ---- output ----------------------------------------------------------------------------

    def write(self, text: str = "", end: str = "\n") -> None:
        self.out.write(text + end)
        self.out.flush()

    def banner(self, w: Welcome) -> None:
        g, width = self.g, self.width

        def row(content: str = "") -> str:
            pad = max(0, width - 4 - visible_len(content))
            return f"{self.dim(g['v'])} {content}{' ' * pad} {self.dim(g['v'])}"

        def label(text: str) -> str:
            return self.dim(f"{text:<7}")

        left = (
            self.dim(g["tl"] + g["h"] + " ")
            + self.gradient("Ragnarok")
            + self.dim(f" v{__version__} ")
        )
        top = left + self.dim(g["h"] * max(0, width - visible_len(left) - 1) + g["tr"])
        folder = (
            w.folder
            if len(w.folder) <= width - 13
            else g["ellipsis"] + w.folder[-(width - 13 - len(g["ellipsis"])) :]
        )
        gpu = self.good(w.gpu) if "GPU" in w.gpu else self.amber(w.gpu)
        count = f"{w.memories} memor{'y' if w.memories == 1 else 'ies'}"
        dot = self.dim(g["dot"])
        welcome = "Welcome." if w.first_run else "Welcome back."
        lines = [
            top,
            row(),
            row(self.bold(welcome)),
            row(self.faint("Your private assistant. Everything stays on this laptop.")),
            row(),
            row(f"{label('model')}{self.teal(w.model)} {dot} {self.faint('local')} {dot} {gpu}"),
            row(f"{label('memory')}{self.amber(count)} {dot} {self.good('encrypted')}"),
            row(f"{label('data')}{self.dim(folder)}"),
            row(),
            self.dim(g["bl"] + g["h"] * (width - 2) + g["br"]),
        ]
        self.write()
        for line in lines:
            self.write(" " + line)
        self.write()
        if w.first_run:
            hint = (
                f"Try {self.sky(chr(34) + 'remember that I prefer meetings after 2pm' + chr(34))}"
                f", {self.sky(chr(34) + 'open steam' + chr(34))}"
                f" or {self.sky(chr(34) + 'find my data structure files' + chr(34))}."
            )
        else:
            hint = "Ask anything, or tell me something to remember."
        self.write(" " + hint)
        self.write(
            " " + self.dim(f"{self.sky('/help')} commands {g['dot']} {self.sky('/quit')} to leave")
        )
        self.write()

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

    def prompt(self) -> str:
        return f" {self.paint(SKY, self.g['prompt'], bold=True)} "

    def assistant_marker(self, kind: str = "bot") -> str:
        if kind == "done":
            return f" {self.paint(GREEN, self.g['ok'], bold=True)} "
        if kind == "warn":
            return f" {self.paint(AMBER, self.g['bot'])} "
        return f" {self.paint(VIOLET, self.g['bot'])} "

    def footer(self, tools: list[tuple[str, bool]], seconds: float) -> None:
        """A dim line under an answer: which tools ran, and how long it took."""
        g = self.g
        parts = []
        for name, allowed in tools:
            parts.append(
                f"{self.teal(g['tool'])} {self.teal(name)}"
                if allowed
                else self.bad(f"{g['no']} {name} blocked")
            )
        parts.append(self.faint(f"{seconds:.1f}s"))
        self.write("   " + f" {self.dim(g['dot'])} ".join(parts))

    def notice(self, text: str) -> None:
        self.write(" " + self.faint(text))

    def error(self, text: str) -> None:
        self.write(" " + self.bad(f"{self.g['no']} {text}"))

    def success(self, text: str) -> None:
        self.write(" " + self.good(f"{self.g['ok']} {text}"))

    def section(self, title: str) -> None:
        self.write(" " + self.bold(title))

    def help(self) -> None:
        rows = [
            ("/memories", "what I remember about you"),
            ("/profile", "what is always in my prompt"),
            ("/forget N", "delete memory N for good"),
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

    def clear(self) -> None:
        if self.tty:
            self.out.write("\x1b[2J\x1b[H")
            self.out.flush()

    # ---- the "thinking" indicator ----------------------------------------------------------

    def spinner(self, label: str = "thinking") -> Spinner:
        return Spinner(self, label)


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
        i = 0
        while True:
            frame = self.c.violet(frames[i % len(frames)])
            self.c.out.write(f"\r {frame} {self.c.faint(self.label)}\x1b[K")
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
