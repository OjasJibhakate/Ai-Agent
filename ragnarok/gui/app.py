"""The cat: a pixel cat that lives on the taskbar. Click it and a small chat opens.

Two threads. This one (the window) draws and listens; a second runs the chat engine, the same one the
terminal uses, and the two only talk through the Bridge's queues. So the cat keeps walking while
the model thinks, and a slow tool can never freeze the window.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
from fractions import Fraction
from pathlib import Path

import httpx

from ragnarok.gui import sprites, unlock, winapi
from ragnarok.gui import theme as t
from ragnarok.gui.brain import CatBrain
from ragnarok.gui.bridge import Bridge, GuiConsole
from ragnarok.gui.chat import ChatWindow
from ragnarok.gui.clicks import DoubleClick

CAT_SCALE = Fraction(7, 4)  # screen pixels per cat pixel (7/4 keeps every outline pixel visible)
FRAME_MS = 40
SCREEN_QUESTION = "What is on my screen right now? Tell me briefly what I am looking at."
SCREEN_ASK_DELAY_MS = 450  # long enough for the chat to take the focus first
SINK = 2  # cat pixels the paws dip into the taskbar, so the cat stands on it rather than floats


def cat_ratio(dpi_scale: float = 1.0) -> Fraction:
    """Screen pixels per cat pixel: 7/4 at normal size, more on a screen with Windows scaling."""
    return CAT_SCALE * Fraction(dpi_scale).limit_denominator(4)


def scaled(image: tk.PhotoImage, ratio: Fraction) -> tk.PhotoImage:
    """A picture at a fractional size: enlarge by the numerator, then keep every denominator-th
    pixel. Every cat pixel stays at least one pixel wide, so the thin outline never disappears."""
    big = image.zoom(ratio.numerator, ratio.numerator)
    return big.subsample(ratio.denominator, ratio.denominator)


def ensure_ollama(bridge: Bridge) -> None:
    """The model server must be running; start it (hidden) if it is not."""
    url = "http://127.0.0.1:11434/api/version"
    try:
        httpx.get(url, timeout=1.5)
        return
    except httpx.HTTPError:
        pass
    script = Path(__file__).resolve().parents[2] / "scripts" / "start_ollama.ps1"
    if not script.exists():
        bridge.emit(
            "error", text="The model server is not running, and I cannot find how to start it."
        )
        return
    bridge.emit("notice", text="Starting the local model server...")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        creationflags=flags, capture_output=True, timeout=90, check=False,
    )  # fmt: skip


class CatApp:
    def __init__(self, root: tk.Tk, key: bytes, home: Path) -> None:
        self.root, self.key, self.home = root, key, home
        self.bridge = Bridge()
        scale = winapi.dpi_scale()
        self.ratio = cat_ratio(scale)  # grows with Windows scaling
        self.w, self.h = round(sprites.W * self.ratio), round(sprites.H * self.ratio)
        left, _top, right, bottom = winapi.work_area()
        self.floor = bottom
        self.screen = winapi.screen_size()
        self.brain = CatBrain(
            left, max(left + 1, right - self.w), floor=bottom, width=self.w, height=self.h
        )
        self.images = self._build_images()
        self.unseen = False  # something happened while the chat was closed
        self.voice_active = False  # hands-free voice mode is on
        self.app = None  # the ChatApp, once the engine thread has made it
        self.clicks = DoubleClick(
            root.after, root.after_cancel, self.toggle_chat, self.toggle_voice
        )

        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-transparentcolor", sprites.KEY)
        root.configure(bg=sprites.KEY)
        self.canvas = tk.Canvas(
            root, width=self.w, height=self.h, bg=sprites.KEY, highlightthickness=0, bd=0,
            cursor="hand2",
        )  # fmt: skip
        self.canvas.pack()
        first = self.images[("stand", 1)]
        self.item = self.canvas.create_image(0, 0, anchor="nw", image=first)
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._motion)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Button-3>", self._menu)
        self.chat = ChatWindow(root, self.bridge, on_busy=self._busy, on_close=self._chat_closed)
        self.menu = tk.Menu(
            root,
            tearoff=0,
            bg=t.PANEL,
            fg=t.TEXT,
            activebackground=t.VIOLET,
            activeforeground=t.BG,
            bd=0,
        )
        self.chase_var = tk.BooleanVar(value=True)
        self.lively_var = tk.BooleanVar(value=False)
        self.menu.add_command(label="Open chat", command=self.open_chat)
        self.menu.add_command(label="What's on my screen?", command=self.ask_about_screen)
        self.menu.add_command(label="Talk to me (double-click)", command=self.toggle_voice)
        self.wake_var = tk.BooleanVar(value=False)
        self.menu.add_checkbutton(
            label='Wake phrase: "hey Ragnarok"', variable=self.wake_var, command=self._toggle_wake
        )
        self.menu.add_command(label="Say hi", command=self.brain.poke)
        self.menu.add_command(label="Sleep here", command=self.brain.sleep_now)
        self.menu.add_checkbutton(
            label="Chase my cursor", variable=self.chase_var, command=self._toggle_chase
        )
        self.menu.add_checkbutton(
            label="Playful (busier cat)", variable=self.lively_var, command=self._toggle_lively
        )
        self.auto_var = tk.BooleanVar(value=unlock.remembered(home))
        self.menu.add_checkbutton(
            label="Unlock without passphrase (this Windows account)", variable=self.auto_var,
            command=self._toggle_auto_unlock,
        )  # fmt: skip
        self.menu.add_separator()
        self.menu.add_command(label="Quit Ragnarok", command=self.quit)

        self.summons = winapi.Summons()  # a second launch asks this copy to open the chat
        self._shown = ("", 0)
        self._last_geo = (-1, -1)
        self._last_tick = time.monotonic()
        self._press_at: tuple[int, int] | None = None
        self._moved = False
        self._was_moving = False
        self._slow = 0.0
        self._raise_at = 0.0
        self.engine = threading.Thread(target=self._run_engine, name="ragnarok-engine", daemon=True)

    # ---- pictures --------------------------------------------------------------------------------

    def _build_images(self) -> dict[tuple[str, int], tk.PhotoImage]:
        out: dict[tuple[str, int], tk.PhotoImage] = {}
        for name, rows in sprites.frames().items():
            for facing, grid in ((1, rows), (-1, sprites.mirror(rows))):
                image = tk.PhotoImage(width=sprites.W, height=sprites.H)
                lines = []
                for row in grid:
                    lines.append(
                        "{" + " ".join(sprites.PALETTE.get(ch) or sprites.KEY for ch in row) + "}"
                    )
                image.put(" ".join(lines))
                out[(name, facing)] = scaled(image, self.ratio)
        return out

    # ---- running ---------------------------------------------------------------------------------

    def start(self) -> None:
        self.engine.start()
        self.root.after(FRAME_MS, self._tick)

    def _run_engine(self) -> None:
        try:
            asyncio.run(self._engine())
        except Exception as exc:  # the cat must say what went wrong, not just vanish
            self.bridge.emit("error", text=f"Ragnarok stopped: {type(exc).__name__}: {exc}"[:300])
            self.bridge.emit("closed")

    async def _engine(self) -> None:
        from ragnarok.chat import ChatApp
        from ragnarok.cli import open_runtime

        ensure_ollama(self.bridge)
        rt = open_runtime(self.home, self.key)
        app = ChatApp(
            rt, GuiConsole(self.bridge), folder=str(self.home),
            read_line=self.bridge.read_line, approve_line=self.bridge.approve_line,
        )  # fmt: skip
        self.app = app
        try:
            await app.run()
        finally:
            rt.close()
            self.bridge.emit("closed")

    def quit(self) -> None:
        self.bridge.shutdown()
        self.engine.join(timeout=6)
        self.root.destroy()

    # ---- every frame -----------------------------------------------------------------------------

    def _tick(self) -> None:
        now = time.monotonic()
        dt = min(now - self._last_tick, 0.25)
        self._last_tick = now
        self.brain.set_cursor(*winapi.cursor_pos())
        pose = self.brain.step(dt)
        frame = pose.frame
        if pose.look and (f"{frame}@{pose.look}", 1) in self.images:
            frame = f"{frame}@{pose.look}"  # its eyes turn toward the pointer
        if (frame, pose.facing) != self._shown:
            self._shown = (frame, pose.facing)
            self.canvas.itemconfig(self.item, image=self.images[self._shown])
        x = int(pose.x)
        y = int(self.floor - self.h + SINK * float(self.ratio) - pose.lift)
        if (x, y) != self._last_geo:
            self._last_geo = (x, y)
            self.root.geometry(f"{self.w}x{self.h}+{x}+{y}")
        self._carry_the_chat(pose.mode)
        for kind, data in self.bridge.drain():
            self._event(kind, data)
        if self.summons.pending():
            self.open_chat()
        self._slow += dt
        if self._slow >= 0.4:
            self._slow = 0.0
            self.chat.tick()
        if now - self._raise_at > 2.0:  # the taskbar likes to sit on top of us
            self._raise_at = now
            self.root.attributes("-topmost", True)
            self.root.lift()
        self.root.after(FRAME_MS, self._tick)

    def _event(self, kind: str, data: dict) -> None:
        self.chat.handle(kind, data)
        hidden = not self.chat.visible
        if kind == "wake":
            self.wake_var.set(bool(data.get("on")))
        if kind == "voice":
            self.voice_active = bool(data.get("on"))
            if self.voice_active:
                self.brain.alert()  # ears up while it listens
            else:
                self.brain.clear_alert()
        if kind == "alert" and hidden:
            self.unseen = True
            self.brain.alert()
        elif kind in ("assistant_start", "approve", "error") and hidden:
            self.unseen = True
        elif kind == "idle" and hidden and self.unseen:
            self.brain.alert()  # it finished (or needs you) while you were not looking

    # ---- mouse -------------------------------------------------------------------------------------

    def _press(self, event: tk.Event) -> None:
        self._press_at = (event.x_root, event.y_root)
        self._moved = False

    def _motion(self, event: tk.Event) -> None:
        """Carry it: it hangs from the pointer, wherever along the taskbar you take it."""
        if self._press_at is None:
            return
        if abs(event.x_root - self._press_at[0]) > 5 or abs(event.y_root - self._press_at[1]) > 5:
            self._moved = True
        if self._moved:
            self.canvas.config(cursor="fleur")
            held_by = event.y_root + 0.7 * self.h  # the pointer holds it by the scruff
            self.brain.drag_to(event.x_root - self.w / 2, self.floor - held_by)

    def _release(self, _event: tk.Event) -> None:
        if self._moved:
            self.brain.drop()  # it falls from where you let go, bounces, and lands
        else:
            self.clicks.click()  # one click: the chat; two clicks: a voice conversation
        self.canvas.config(cursor="hand2")
        self._press_at = None

    def _toggle_lively(self) -> None:
        self.brain.lively = self.lively_var.get()

    def _toggle_chase(self) -> None:
        self.brain.chase = self.chase_var.get()

    def _toggle_auto_unlock(self) -> None:
        """On: seal the key to this Windows account. Off: ask for the passphrase again next time."""
        if self.auto_var.get():
            self.auto_var.set(unlock.remember_on_this_account(self.home, self.key))
        else:
            unlock.forget_this_account(self.home)

    def _carry_the_chat(self, mode: str) -> None:
        """If the cat is moved while the chat is open, the chat goes with it."""
        moving = mode in ("carried", "fall")
        if self.chat.visible and (moving or self._was_moving):
            self.chat.follow(self.root.winfo_x() + self.w // 2, self.root.winfo_y(), self.screen)
        self._was_moving = moving

    def _menu(self, event: tk.Event) -> None:
        self.auto_var.set(unlock.remembered(self.home))  # the file may have changed since last time
        self.menu.tk_popup(event.x_root, event.y_root)

    # ---- the chat -------------------------------------------------------------------------------------

    def toggle_chat(self) -> None:
        if self.chat.visible:
            self.chat.hide()
        else:
            self.open_chat()

    def open_chat(self) -> None:
        self.unseen = False
        self.brain.clear_alert()
        self.brain.poke()
        self.brain.anchored = True
        self.chat.show(self.root.winfo_x() + self.w // 2, self.root.winfo_y(), self.screen)

    def ask_about_screen(self) -> None:
        """Open the chat and ask what is on the screen, without the user having to word it.

        The question waits a moment so the chat holds the focus: the screen skill moves the focused
        window of this program out of the picture before it looks."""
        self.open_chat()
        self.root.after(SCREEN_ASK_DELAY_MS, lambda: self.chat.ask(SCREEN_QUESTION))

    def _toggle_wake(self) -> None:
        self.bridge.send("/voice wake on" if self.wake_var.get() else "/voice wake off")

    def toggle_voice(self) -> None:
        """Double-click: open the chat and take ONE spoken request (answered aloud), or cancel it."""
        if self.voice_active:
            if self.app is not None:
                self.app.voice.cancel()  # the engine ends voice mode at its next step
            return
        self.open_chat()
        self.bridge.send("/listen aloud")  # one request, answered aloud, then it stops listening

    def _chat_closed(self) -> None:
        self.brain.anchored = False

    def _busy(self, busy: bool) -> None:
        self.brain.set_busy(busy)


def main() -> int:
    winapi.make_dpi_aware()
    if not winapi.only_one_copy():
        winapi.summon()  # the cat is already here: ask it to open the chat
        return 0
    from ragnarok.cli import home_dir

    root = tk.Tk()
    root.withdraw()
    try:
        home = home_dir()
    except Exception as exc:
        from tkinter import messagebox

        messagebox.showerror("Ragnarok", str(exc))
        return 1
    key = unlock.unlock(root, home)
    if key is None:
        return 0
    app = CatApp(root, key, home)
    root.deiconify()
    app.start()
    root.mainloop()
    exit_now(0)
    return 0


def exit_now(code: int) -> None:
    """End the process even if a worker thread is still blocked on the chat queue.

    A quit that leaves the process alive keeps the one-copy lock, so the next `meow` only signals a
    cat that has no window. The vault is closed by the engine before this runs (and is crash-safe)."""
    sys.stdout and sys.stdout.flush()
    sys.stderr and sys.stderr.flush()
    os._exit(code)


if __name__ == "__main__":
    sys.exit(main())
