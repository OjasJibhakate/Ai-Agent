"""A fake desktop for testing the screen skill: no real screen, mouse, keyboard or windows."""

from __future__ import annotations

import asyncio
from typing import Any

import numpy as np

from ragnarok.runtime.gateway import GatewayError
from ragnarok.skills.screen.capture import Frame
from ragnarok.skills.screen.winapi import WindowInfo

W, H = 1280, 720
NOTEPAD = WindowInfo(hwnd=111, title="Untitled - Notepad", process="notepad.exe", pid=10)
TERMINAL = WindowInfo(hwnd=222, title="Ragnarok", process="windowsterminal.exe", pid=20)
POWERSHELL = WindowInfo(hwnd=333, title="Windows PowerShell", process="powershell.exe", pid=30)


class FakeClock:
    """Time that only moves when something sleeps, so loops with waits run instantly."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds
        await asyncio.sleep(0)


class World:
    """The pretend desktop: the picture changes whenever an action is performed (unless frozen)."""

    def __init__(self) -> None:
        self.level = 40
        self.frozen = False
        self.events: list[tuple[str, Any]] = []  # everything that happened, in order
        self.cursor = (0, 0)
        self.escape = False
        self.foreground: WindowInfo | None = NOTEPAD
        self.hidden: list[int] = []
        self.restored: list[int] = []
        self.fail_restore = False

    def bump(self) -> None:
        if not self.frozen:
            self.level += 40  # a clear change: more than the per-pixel step used to compare frames


class FakeScreen:
    def __init__(self, world: World, left: int = 0, top: int = 0) -> None:
        self.world, self.left, self.top = world, left, top
        self.widths: list[int] = []

    def grab(self, max_width: int = 1280) -> Frame:
        self.widths.append(max_width)
        rgb = np.full((H // 2, W // 2, 3), self.world.level % 256, np.uint8)
        self.world.events.append(("grab", self.world.level))
        return Frame(rgb, self.left, self.top, W, H)


class FakeDriver:
    def __init__(self, world: World) -> None:
        self.world = world

    def click(self, x: int, y: int, button: str = "left", count: int = 1) -> None:
        self.world.events.append(("click", (x, y, button, count)))
        self.world.cursor = (x, y)
        self.world.bump()

    def type_text(self, text: str) -> None:
        self.world.events.append(("type", text))
        self.world.bump()

    def press(self, spec: str) -> None:
        self.world.events.append(("key", spec))
        self.world.bump()

    def scroll(self, amount: int, at: tuple[int, int] | None = None) -> None:
        self.world.events.append(("scroll", (amount, at)))
        if at is not None:
            self.world.cursor = at  # the real driver moves the mouse over the thing it scrolls
        self.world.bump()


class FakeWindows:
    def __init__(self, world: World) -> None:
        self.world = world

    def foreground(self) -> WindowInfo | None:
        return self.world.foreground

    def is_own_terminal(self, window: WindowInfo | None) -> bool:
        return window is not None and window.hwnd == TERMINAL.hwnd

    def cursor(self) -> tuple[int, int]:
        return self.world.cursor

    def escape_down(self) -> bool:
        return self.world.escape

    def minimize(self, hwnd: int) -> None:
        self.world.hidden.append(hwnd)
        self.world.events.append(("minimize", hwnd))

    def restore(self, hwnd: int) -> None:
        self.world.restored.append(hwnd)
        self.world.events.append(("restore", hwnd))


class ScriptedVision:
    """Answers decide() from a list of dicts (or functions of the prompt). Records what it saw."""

    def __init__(
        self, decisions: list[Any], verdicts: list[Any] | None = None, answer: str = ""
    ) -> None:
        self.decisions = list(decisions)
        self.verdicts = list(verdicts if verdicts is not None else [{"complete": True}])
        self.answer_text = answer
        self.prompts: list[str] = []
        self.verify_prompts: list[str] = []
        self.pngs: list[bytes] = []
        self.questions: list[str] = []
        self.marked: list[bytes] = []
        self.under: list[str] = [""]

    async def decide(self, png: bytes, prompt: str) -> dict[str, Any]:
        self.prompts.append(prompt)
        self.pngs.append(png)
        item = self.decisions.pop(0) if len(self.decisions) > 1 else self.decisions[0]
        item = item(prompt) if callable(item) else item
        if isinstance(item, Exception):
            raise item
        return item

    async def verify(self, png: bytes, prompt: str) -> dict[str, Any]:
        self.verify_prompts.append(prompt)
        item = self.verdicts.pop(0) if len(self.verdicts) > 1 else self.verdicts[0]
        if isinstance(item, Exception):
            raise item
        return item

    async def read_marked(self, png: bytes, prompt: str) -> dict[str, Any]:
        """What is under a marked point: scripted through `under` (empty text means "unsure")."""
        self.marked.append(png)
        under = self.under
        return {"under_marker": under.pop(0) if len(under) > 1 else under[0]}

    async def answer(self, png: bytes, question: str) -> str:
        self.questions.append(question)
        self.pngs.append(png)
        return self.answer_text


def click(x: int, y: int, target: str = "the button") -> dict[str, Any]:
    return {"action": "click", "x": x, "y": y, "target": target}


def typing(text: str, target: str = "the box") -> dict[str, Any]:
    return {"action": "type", "text": text, "target": target}


def fill(x: int, y: int, text: str, target: str = "the field") -> dict[str, Any]:
    return {"action": "fill", "x": x, "y": y, "text": text, "target": target}


def key(keys: str) -> dict[str, Any]:
    return {"action": "key", "keys": keys}


def done(note: str = "finished") -> dict[str, Any]:
    return {"action": "done", "note": note}


GATEWAY_DOWN = GatewayError("model down")
