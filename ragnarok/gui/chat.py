"""The chat popup: your messages, Ragnarok's answers, and the cards it needs you to answer.

It only draws. Everything it shows arrives as an event from the engine (see bridge.py), and the
only things it sends back are the lines you type and the yes/no on a card.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from typing import Any

from ragnarok.gui import theme as t
from ragnarok.gui.bridge import Bridge

WIDTH, HEIGHT = 300, 360
WRAP = WIDTH - 90  # how wide a bubble's text may get
DOTS = ["Thinking", "Thinking.", "Thinking..", "Thinking..."]
QUIT_WORDS = ("/quit", "/exit", "/q")


class ChatWindow:
    def __init__(
        self,
        root: tk.Tk,
        bridge: Bridge,
        *,
        on_busy: Callable[[bool], None],
        on_close: Callable[[], None],
    ) -> None:
        self.bridge = bridge
        self.on_busy = on_busy
        self.on_close = on_close
        self.busy = False
        self.ready = False  # the engine is waiting for a line
        self._queued: list[str] = []  # lines typed before the engine was ready
        self._assistant: tk.Label | None = None  # the bubble being streamed into
        self._dots = 0
        self._wake_on = False
        self._voice_on = False
        self._status = ""

        top = self.top = tk.Toplevel(root)
        top.withdraw()
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        top.configure(bg=t.BORDER)
        body = tk.Frame(top, bg=t.BG)
        body.pack(fill="both", expand=True, padx=1, pady=1)

        # header: draggable, with the title, the status and a close button
        header = tk.Frame(body, bg=t.PANEL, height=32)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, text="✦", fg=t.VIOLET, bg=t.PANEL, font=t.TITLE).pack(
            side="left", padx=(12, 4)
        )
        tk.Label(header, text="Ragnarok", fg=t.TEXT, bg=t.PANEL, font=t.TITLE).pack(side="left")
        self.status = tk.Label(header, text="", fg=t.DIM, bg=t.PANEL, font=t.SMALL)
        close = tk.Label(header, text="✕", fg=t.DIM, bg=t.PANEL, font=t.UI, cursor="hand2")
        close.pack(side="right", padx=12)  # packed first, so a long status can never push it out
        self.status.pack(side="left", padx=8)
        close.bind("<Button-1>", lambda _e: self.hide())
        close.bind("<Enter>", lambda _e: close.config(fg=t.CORAL))
        close.bind("<Leave>", lambda _e: close.config(fg=t.DIM))
        for widget in (header, *header.winfo_children()[:3]):
            widget.bind("<ButtonPress-1>", self._drag_start)
            widget.bind("<B1-Motion>", self._drag_move)

        # the transcript: a scrolling column of message widgets
        middle = tk.Frame(body, bg=t.BG)
        middle.pack(fill="both", expand=True)
        # (it asks for almost no size and expands: Tk's default 265 px squeezed the input box)
        self.canvas = tk.Canvas(middle, bg=t.BG, highlightthickness=0, bd=0, width=1, height=1)
        self.column = tk.Frame(self.canvas, bg=t.BG)
        self._window = self.canvas.create_window((0, 0), window=self.column, anchor="nw")
        self.canvas.pack(fill="both", expand=True)
        self.column.bind("<Configure>", self._resized)
        self.canvas.bind(
            "<Configure>", lambda e: self.canvas.itemconfig(self._window, width=e.width)
        )
        for widget in (self.canvas, self.column):
            widget.bind("<MouseWheel>", self._wheel)

        # the line under the transcript that says it is working
        self.thinking = tk.Label(body, text="", fg=t.VIOLET, bg=t.BG, font=t.SMALL, anchor="w")
        self.thinking.pack(fill="x", padx=14)

        # the input
        footer = tk.Frame(body, bg=t.PANEL)
        footer.pack(fill="x")
        send = tk.Label(
            footer, text="➤", fg=t.BG, bg=t.VIOLET, font=("Segoe UI", 12), width=3, cursor="hand2"
        )
        send.pack(side="right", padx=(0, 10), pady=10, fill="y")
        send.bind("<Button-1>", lambda _e: self._send())
        self.entry = tk.Text(
            footer, height=2, wrap="word", bg="#1a2030", fg=t.TEXT, insertbackground=t.CYAN,
            relief="flat", font=t.UI, padx=8, pady=6, highlightthickness=1,
            highlightbackground=t.BORDER, highlightcolor=t.VIOLET, bd=0,
        )  # fmt: skip
        self.entry.pack(side="left", fill="x", expand=True, padx=(10, 6), pady=10)
        self.entry.bind("<Return>", self._enter)
        self.entry.bind("<Shift-Return>", lambda _e: None)
        self.placeholder()

    # ---- showing and hiding ----------------------------------------------------------------

    def show(self, center_x: int, bottom_y: int, screen: tuple[int, int]) -> None:
        x = int(min(max(center_x - WIDTH // 2, 8), screen[0] - WIDTH - 8))
        y = int(max(bottom_y - HEIGHT - 6, 8))
        self.top.geometry(f"{WIDTH}x{HEIGHT}+{x}+{y}")
        self.top.deiconify()
        self.top.lift()
        self.top.after(60, self._focus)

    def follow(self, center_x: int, bottom_y: int, screen: tuple[int, int]) -> None:
        """Move with the cat (no focus change, no flicker)."""
        x = int(min(max(center_x - WIDTH // 2, 8), screen[0] - WIDTH - 8))
        y = int(max(bottom_y - HEIGHT - 6, 8))
        self.top.geometry(f"+{x}+{y}")

    def _focus(self) -> None:
        self.top.focus_force()
        self.entry.focus_set()

    def hide(self) -> None:
        self.top.withdraw()
        self.on_close()

    @property
    def visible(self) -> bool:
        return self.top.state() == "normal"

    def _drag_start(self, event: tk.Event) -> None:
        self._grab = (event.x_root - self.top.winfo_x(), event.y_root - self.top.winfo_y())

    def _drag_move(self, event: tk.Event) -> None:
        dx, dy = self._grab
        self.top.geometry(f"+{event.x_root - dx}+{event.y_root - dy}")

    # ---- input ---------------------------------------------------------------------------------

    def placeholder(self) -> None:
        self.entry.delete("1.0", "end")

    def _enter(self, _event: tk.Event) -> str:
        self._send()
        return "break"  # no new line in the box

    def _send(self) -> None:
        text = self.entry.get("1.0", "end").strip()
        if not text:
            return
        self.entry.delete("1.0", "end")
        if text.lower() in QUIT_WORDS:
            self.hide()
            return
        self.ask(text)

    def ask(self, text: str) -> None:
        """Send a message as if it had been typed (the cat's menu uses this)."""
        if self.busy:
            self.note("Still working on the last one; I'll take this next.", t.DIM)
        self.bubble(text, me=True)
        self.set_busy(True)
        self.bridge.send(text)

    def set_busy(self, busy: bool) -> None:
        if busy == self.busy:
            return
        self.busy = busy
        if not busy:
            self.thinking.config(text="")
        self.on_busy(busy)

    def tick(self) -> None:
        """Animate the 'Thinking...' line (called by the app a few times a second)."""
        if self.busy and self._assistant is None:
            self._dots = (self._dots + 1) % len(DOTS)
            self.thinking.config(text=DOTS[self._dots])
        elif self.busy:
            self.thinking.config(text="")

    # ---- scrolling ---------------------------------------------------------------------------

    def _resized(self, _event: tk.Event) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _wheel(self, event: tk.Event) -> None:
        self.canvas.yview_scroll(-1 * (event.delta // 120), "units")

    def _to_bottom(self) -> None:
        self.canvas.update_idletasks()
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self.canvas.yview_moveto(1.0)

    # ---- things that go in the transcript ------------------------------------------------------

    def _row(self, anchor: str = "w", pady: tuple[int, int] = (3, 3)) -> tk.Frame:
        row = tk.Frame(self.column, bg=t.BG)
        row.pack(fill="x", padx=10, pady=pady)
        row.anchor = anchor  # type: ignore[attr-defined]
        for widget in (row,):
            widget.bind("<MouseWheel>", self._wheel)
        return row

    def bubble(self, text: str, *, me: bool = False, colour: str | None = None) -> tk.Label:
        row = self._row()
        label = tk.Label(
            row, text=text, justify="left", anchor="w", wraplength=WRAP,
            bg=t.BUBBLE_ME if me else t.BUBBLE_BOT, fg=colour or t.TEXT, font=t.UI, padx=11, pady=7,
        )  # fmt: skip
        label.pack(side="right" if me else "left")
        label.bind("<MouseWheel>", self._wheel)
        self._to_bottom()
        return label

    def note(self, text: str, colour: str = t.DIM, *, mono: bool = False, indent: int = 0) -> None:
        row = self._row(pady=(0, 0))
        label = tk.Label(
            row, text=text, justify="left", anchor="w", fg=colour, bg=t.BG,
            font=t.MONO if mono else t.SMALL, wraplength=WIDTH - 52 - indent,
        )  # fmt: skip
        label.pack(side="left", padx=(8 + indent, 0))
        label.bind("<MouseWheel>", self._wheel)
        self._to_bottom()

    def card(self, accent: str) -> tk.Frame:
        """A framed block with a coloured edge, for things that need your attention."""
        row = self._row(pady=(6, 6))
        outer = tk.Frame(row, bg=accent)
        outer.pack(fill="x")
        inner = tk.Frame(outer, bg=t.PANEL)
        inner.pack(fill="x", padx=(3, 0))
        return inner

    # ---- events from the engine --------------------------------------------------------------------

    def handle(self, kind: str, data: dict[str, Any]) -> None:
        method = getattr(self, f"on_{kind}", None)
        if method is not None:
            method(**data)
        if kind != "assistant_chunk":
            self._to_bottom()

    def on_welcome(self, model: str, memories: int, gpu: str, first_run: bool) -> None:
        self._status = f"{model} · {gpu}"  # short: the window is narrow
        self.status.config(text=self._status)
        if first_run:
            self.bubble(
                "Hi! I'm Ragnarok. Everything runs on this laptop.\n\nTry: “remember that I prefer "
                "meetings after 2pm”, “open steam”, or “make slides from my notes folder”."
            )
        elif memories:
            things = "1 thing" if memories == 1 else f"{memories} things"
            self.bubble(f"Welcome back. I remember {things} about you. What can I do for you?")
        else:
            self.bubble("Welcome back. What can I do for you?")

    def on_closed(self) -> None:
        """The engine is gone: stop waiting for it, and say so."""
        self.ready = False
        self.set_busy(False)
        self.note(
            "Ragnarok is not running. Quit it from the cat's menu and start it again.", t.CORAL
        )

    def on_wake(self, on: bool) -> None:
        """The wake phrase was turned on or off."""
        self._wake_on = on
        self.on_voice(self._voice_on)

    def on_voice(self, on: bool) -> None:
        """The microphone is open for a request (or a hands-free conversation), or closed again."""
        self._voice_on = on
        if on:
            self.status.config(text="● listening", fg=t.VIOLET)
        elif self._wake_on:
            self.status.config(text="● wake: hey Ragnarok", fg=t.VIOLET)
        else:
            self.status.config(text=self._status, fg=t.DIM)

    def on_heard(self, text: str) -> None:
        """A sentence the microphone understood: shown like something typed."""
        self.bubble(text, me=True)
        self.set_busy(True)

    def on_idle(self) -> None:
        self.ready = True
        self._assistant = None
        self.set_busy(False)

    def on_assistant_start(self, kind: str = "bot") -> None:
        self._assistant = self.bubble("", colour=t.GREEN if kind == "done" else None)
        self.thinking.config(text="")

    def on_assistant_chunk(self, text: str) -> None:
        if self._assistant is None:
            self._assistant = self.bubble("")
        self._assistant.config(text=self._assistant.cget("text") + text)
        self._to_bottom()

    def on_tool(self, text: str) -> None:
        self._assistant = None
        self.note("⎿  " + text, t.DIM, mono=True, indent=6)

    def on_footer(self, tools: list, seconds: float) -> None:
        names = ", ".join(n if ok else f"✗ {n} blocked" for n, ok in tools)
        self.note(f"{names + ' · ' if names else ''}{seconds:.1f}s", t.DIM, indent=6)
        self._assistant = None

    def on_notice(self, text: str) -> None:
        self.note(text, t.DIM)

    def on_error(self, text: str) -> None:
        self.note("✗ " + text, t.CORAL)

    def on_success(self, text: str) -> None:
        self.note("✓ " + text, t.GREEN)

    def on_section(self, text: str) -> None:
        self.note(text, t.TEXT)

    def on_note(self, text: str) -> None:
        self.note(text.rstrip(), t.DIM, mono=True)

    def on_clear(self) -> None:
        for child in self.column.winfo_children():
            child.destroy()
        self._assistant = None

    def on_alert(self, kind: str, title: str, body: str = "") -> None:
        label, colour = t.ALERT.get(kind, ("Inbox", t.VIOLET))
        card = self.card(colour)
        tk.Label(card, text=f"✦ {label}", fg=colour, bg=t.PANEL, font=t.UI_BOLD).pack(
            anchor="w", padx=10, pady=(8, 0)
        )
        tk.Label(
            card, text=title, fg=t.TEXT, bg=t.PANEL, font=t.UI, wraplength=WRAP, justify="left"
        ).pack(anchor="w", padx=10)
        if body:
            tk.Label(
                card, text=body, fg=t.DIM, bg=t.PANEL, font=t.SMALL, wraplength=WRAP, justify="left"
            ).pack(anchor="w", padx=10, pady=(0, 8))

    def on_step(self, state: str, id: str, title: str, note: str = "") -> None:
        glyph, colour = t.STEP_GLYPH.get(state, ("○", t.DIM))
        text = f"{glyph}  {id}  {title}" + (f"   {note}" if note else "")
        self.note(text.strip(), colour, indent=6)

    def on_approve(self, type: str, **card: Any) -> None:
        if type == "plan":
            self._plan_card(card["goal"], card["rows"])
        else:
            self._permission_card(card["tool"], card["args"], card["note"])

    # ---- cards you answer ----------------------------------------------------------------------

    def _answer_buttons(self, card: tk.Frame, yes: str, no: str) -> None:
        buttons = tk.Frame(card, bg=t.PANEL)
        buttons.pack(fill="x", padx=10, pady=(8, 10))

        def choose(ok: bool) -> None:
            for child in buttons.winfo_children():
                child.destroy()
            tk.Label(
                buttons, text="✓ Allowed" if ok else "✗ Declined",
                fg=t.GREEN if ok else t.CORAL, bg=t.PANEL, font=t.SMALL,
            ).pack(anchor="w")  # fmt: skip
            self.bridge.answer(ok)

        for text, ok, colour, fg in ((yes, True, t.VIOLET, t.BG), (no, False, t.BORDER, t.TEXT)):
            button = tk.Label(
                buttons, text=f" {text} ", bg=colour, fg=fg, font=t.UI_BOLD, cursor="hand2", pady=3
            )
            button.pack(side="left", padx=(0, 8))
            button.bind("<Button-1>", lambda _e, ok=ok: choose(ok))

    def _permission_card(self, tool: str, args: dict, note: str) -> None:
        card = self.card(t.AMBER)
        tk.Label(card, text="Permission needed", fg=t.AMBER, bg=t.PANEL, font=t.UI_BOLD).pack(
            anchor="w", padx=10, pady=(8, 0)
        )
        tk.Label(card, text=tool, fg=t.CYAN, bg=t.PANEL, font=t.MONO).pack(anchor="w", padx=10)
        for key, value in args.items():
            line = value if len(value) < 90 else value[:87] + "…"
            tk.Label(
                card,
                text=f"{key}  {line}",
                fg=t.TEXT,
                bg=t.PANEL,
                font=t.MONO,
                wraplength=WRAP,
                justify="left",
            ).pack(anchor="w", padx=10)
        if note:
            tk.Label(
                card, text=note, fg=t.DIM, bg=t.PANEL, font=t.SMALL, wraplength=WRAP, justify="left"
            ).pack(anchor="w", padx=10, pady=(4, 0))
        self._answer_buttons(card, "Allow", "Deny")
        self._assistant = None

    def _plan_card(self, goal: str, rows: list) -> None:
        card = self.card(t.VIOLET)
        tk.Label(
            card, text=f"Plan · {len(rows)} steps", fg=t.VIOLET, bg=t.PANEL, font=t.UI_BOLD
        ).pack(anchor="w", padx=10, pady=(8, 0))
        tk.Label(
            card, text=goal, fg=t.DIM, bg=t.PANEL, font=t.SMALL, wraplength=WRAP, justify="left"
        ).pack(anchor="w", padx=10)
        for step_id, title, after, _risk, asks in rows:
            tail = (f"  (after {', '.join(after)})" if after else "") + (
                "  · asks you" if asks else ""
            )
            tk.Label(
                card, text=f"○ {step_id}  {title}{tail}", fg=t.AMBER if asks else t.TEXT, bg=t.PANEL,
                font=t.SMALL, wraplength=WRAP, justify="left",
            ).pack(anchor="w", padx=10)  # fmt: skip
        self._answer_buttons(card, "Run plan", "Cancel")
        self._assistant = None
