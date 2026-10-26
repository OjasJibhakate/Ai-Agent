"""Between the chat engine and the window.

The engine (ChatApp) was written for a terminal: it prints through a Console, reads a line with
`input()`, and asks permission with a [y/N] question. Rather than write a second engine, the window
gets a Console that turns everything the engine prints into small structured events, and two
blocking calls (`read_line`, `approve_line`) that wait for the window. The engine thread and the
window thread only ever talk through queues, so neither can stall the other.

Events are (kind, data) pairs:
  welcome, assistant_start, assistant_chunk, tool, footer, notice, error, success, section, note,
  step, alert, approve (a permission or plan card), idle (waiting for you to type)
"""

from __future__ import annotations

import queue
import re
from typing import Any

from ragnarok.ui import Console, Welcome

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_MARKER = re.compile(r"^ ([●✓*+]) (.*)$", re.DOTALL)  # assistant_marker(), maybe with text after


class Bridge:
    def __init__(self) -> None:
        self.events: queue.Queue[tuple[str, dict[str, Any]]] = queue.Queue()
        self.inputs: queue.Queue[str] = queue.Queue()
        self.answers: queue.Queue[str] = queue.Queue()
        self.pending_card: dict[str, Any] | None = None

    def emit(self, event: str, /, **data: Any) -> None:
        """Queue an event. (`event` is positional-only: events may carry a `kind` field.)"""
        self.events.put((event, data))

    # ---- the engine thread calls these (they block) ---------------------------------------

    def read_line(self, prompt: str = "") -> str:
        self.emit("idle")
        return self.inputs.get()

    def approve_line(self, prompt: str = "") -> str:
        card, self.pending_card = self.pending_card, None
        self.emit(
            "approve", **(card or {"type": "permission", "tool": "?", "args": {}, "note": ""})
        )
        return self.answers.get()

    # ---- the window thread calls these -------------------------------------------------------

    def send(self, text: str) -> None:
        self.inputs.put(text)

    def answer(self, yes: bool) -> None:
        self.answers.put("y" if yes else "n")

    def drain(self, limit: int = 200) -> list[tuple[str, dict[str, Any]]]:
        out = []
        while len(out) < limit:
            try:
                out.append(self.events.get_nowait())
            except queue.Empty:
                break
        return out

    def shutdown(self) -> None:
        """Let a blocked engine thread finish: refuse any open question, then say goodbye."""
        self.answers.put("n")
        self.inputs.put("/quit")


class GuiStream:
    """Where the engine's streamed answer text goes. (Everything else uses Console methods.)"""

    encoding = "utf-8"

    def __init__(self, bridge: Bridge) -> None:
        self.bridge = bridge

    def isatty(self) -> bool:
        return False

    def flush(self) -> None:
        pass

    def write(self, text: str) -> int:
        text = _ANSI.sub("", text).replace("\r", "")
        if not text:
            return 0
        match = _MARKER.match(text)
        if match and text.startswith(" ") and len(text) < 4 + 100000:
            self.bridge.emit("assistant_start", kind="done" if match.group(1) in "✓+" else "bot")
            text = match.group(2)
        if text:
            self.bridge.emit("assistant_chunk", text=text.replace("\n   ", "\n"))
        return len(text)


class GuiConsole(Console):
    """The engine's Console, with the terminal replaced by events."""

    def __init__(self, bridge: Bridge) -> None:
        super().__init__(GuiStream(bridge), color=False, ascii_only=False, animate=False)
        self.bridge = bridge

    # ---- plain lines -----------------------------------------------------------------------

    def write(self, text: str = "", end: str = "\n") -> None:
        line = _ANSI.sub("", text).rstrip()
        if not line.strip():
            return
        stripped = line.strip()
        if stripped.startswith(self.g["tool"]):
            self.bridge.emit("tool", text=stripped[len(self.g["tool"]) :].strip())
        else:
            self.bridge.emit("note", text=line)

    def notice(self, text: str) -> None:
        self.bridge.emit("notice", text=text)

    def ready_for_input(self) -> None:
        self.bridge.emit("idle")

    def voice_state(self, on: bool) -> None:
        self.bridge.emit("voice", on=on)

    def wake_state(self, on: bool) -> None:
        self.bridge.emit("wake", on=on)

    def heard(self, text: str) -> None:
        self.bridge.emit("heard", text=text)

    def error(self, text: str) -> None:
        self.bridge.emit("error", text=text)

    def success(self, text: str) -> None:
        self.bridge.emit("success", text=text)

    def section(self, title: str) -> None:
        self.bridge.emit("section", text=title)

    # ---- the welcome, the footer, things that arrive by themselves ---------------------------

    def banner(self, w: Welcome) -> None:
        self.bridge.emit(
            "welcome", model=w.model, memories=w.memories, gpu=w.gpu, first_run=w.first_run
        )

    async def play_banner(self, w: Welcome) -> None:
        self.banner(w)

    async def farewell(self, note: str) -> None:
        self.bridge.emit("notice", text=note)

    def footer(self, tools: list[tuple[str, bool]], seconds: float) -> None:
        self.bridge.emit("footer", tools=tools, seconds=seconds)

    def alert(self, kind: str, title: str, body: str = "") -> None:
        self.bridge.emit("alert", kind=kind, title=title, body=body)

    def clear(self) -> None:
        self.bridge.emit("clear")

    # ---- questions: a card now, the answer comes from the buttons ----------------------------

    def approval(self, name: str, args: dict, warning: str = "") -> str:
        self.bridge.pending_card = {
            "type": "permission",
            "tool": name,
            "args": {k: repr(v) for k, v in list(args.items())[:6]},
            "note": warning,
        }
        return ""

    def plan_card(self, goal: str, rows: list) -> str:
        self.bridge.pending_card = {"type": "plan", "goal": goal, "rows": rows}
        return ""

    def step_line(self, state: str, step_id: str, title: str, note: str = "") -> None:
        self.bridge.emit("step", state=state, id=step_id, title=title, note=note)
