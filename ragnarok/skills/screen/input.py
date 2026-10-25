"""Keyboard and mouse for the screen skill: the safety rules, then the Windows driver.

The rules are plain functions (easy to test without a desktop). They are applied by the task loop
*before* anything is sent:
  - only an allow-list of keys and combinations (no Alt+F4, Win+R, Ctrl+Alt+anything, ...);
  - text that looks like a secret (password, key, token, card number) is never typed;
  - nothing is typed or sent as a key into a shell, terminal, Run box or system tool, because there
    typed text is executed, not just entered.
"""

from __future__ import annotations

import ctypes
import re
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass

from ragnarok.memory.store import scrub_secrets
from ragnarok.skills.screen.winapi import TERMINAL_HOSTS, WindowInfo

MAX_TYPED_CHARS = 300
BLOCKED_PROCESSES = TERMINAL_HOSTS | frozenset(
    {"regedit.exe", "mmc.exe", "taskmgr.exe", "cscript.exe", "wscript.exe", "mshta.exe",
     "msiexec.exe", "secpol.msc", "consent.exe", "logonui.exe"}
)  # fmt: skip
BLOCKED_TITLES = re.compile(
    r"^(run|windows security|user account control|windows powershell.*)$", re.I
)
_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")


class InputRefused(ValueError):
    """An action the safety rules do not allow (the message is shown to the model and the user)."""


# ---- keys ----------------------------------------------------------------------------------------

VK = {
    "enter": 0x0D, "tab": 0x09, "esc": 0x1B, "backspace": 0x08, "delete": 0x2E, "space": 0x20,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pagedown": 0x22, "win": 0x5B, "ctrl": 0x11, "alt": 0x12, "shift": 0x10,
    **{f"f{i}": 0x6F + i for i in range(1, 13)},
    **{chr(c): c - 32 for c in range(ord("a"), ord("z") + 1)},
    **{str(d): 0x30 + d for d in range(10)},
}  # fmt: skip
EXTENDED = {"left", "up", "right", "down", "home", "end", "pageup", "pagedown", "delete", "win"}
MODIFIERS = ("ctrl", "alt", "shift", "win")

SINGLE_KEYS = frozenset(
    {"enter", "tab", "esc", "backspace", "delete", "space", "up", "down", "left", "right", "home",
     "end", "pageup", "pagedown", "f5", "f6", "f11", "win"}
)  # fmt: skip
# Single letters are not allowed as keys: text goes through `type`, where the secret check applies.
CTRL_KEYS = frozenset(set("acvxzyflntwrspod") | {"tab", "pageup", "pagedown", "home", "end"})
ALLOWED_COMBOS: dict[frozenset[str], frozenset[str]] = {
    frozenset({"ctrl"}): CTRL_KEYS,
    frozenset({"ctrl", "shift"}): frozenset({"t", "tab"}),
    frozenset({"alt"}): frozenset({"left", "right", "d", "tab"}),
    frozenset({"win"}): frozenset({"d"}),
}


def parse_keys(spec: str) -> list[str]:
    """Check a key spec such as "ctrl+l" or "enter" against the allow-list; return it split up."""
    parts = [p.strip().lower() for p in spec.replace(" ", "").split("+") if p.strip()]
    if not parts or len(parts) > 3:
        raise InputRefused(f"not a key I can press: {spec!r}")
    if any(p not in VK for p in parts):
        raise InputRefused(f"unknown key in {spec!r}")
    mods = [p for p in parts if p in MODIFIERS]
    mains = [p for p in parts if p not in MODIFIERS]
    if len(mains) > 1 or len(set(mods)) != len(mods):
        raise InputRefused(f"not a key I can press: {spec!r}")
    main = mains[0] if mains else None
    if main is None:
        if mods == ["win"]:
            return mods  # the Start menu
        raise InputRefused(f"a modifier on its own is not useful: {spec!r}")
    if not mods:
        if main in SINGLE_KEYS:
            return [main]
        raise InputRefused(f"use type for text; {main!r} is not a key I press on its own")
    if main in ALLOWED_COMBOS.get(frozenset(mods), frozenset()):
        return [*mods, main]
    raise InputRefused(f"I do not press {spec!r} (it can close, run or change things)")


def check_text(text: str) -> None:
    if not text or len(text) > MAX_TYPED_CHARS:
        raise InputRefused(f"I type between 1 and {MAX_TYPED_CHARS} characters at a time")
    if any(ord(c) < 32 and c not in "\n\t" for c in text):
        raise InputRefused("that text contains control characters")
    if scrub_secrets(text) != text or _CARD.search(text):
        raise InputRefused("that looks like a password, key or card number, so I will not type it")


def check_target_window(window: WindowInfo | None, what: str) -> None:
    """Typing and keys go to the focused window; refuse windows that would run what is typed."""
    if window is None:
        return
    if window.process in BLOCKED_PROCESSES or BLOCKED_TITLES.match(window.title.strip()):
        raise InputRefused(
            f"I will not send {what} to {window.process or 'that window'} ({window.title[:30]!r}): "
            "typed text would be run as a command there"
        )


# ---- the Windows driver --------------------------------------------------------------------------

ULONG_PTR = ctypes.c_size_t
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x0008, 0x0010
MOUSEEVENTF_WHEEL = 0x0800
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x0001, 0x0002, 0x0004
INPUT_MOUSE, INPUT_KEYBOARD = 0, 1


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR),
    ]  # fmt: skip


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR),
    ]  # fmt: skip


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT)]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


def _send(*inputs: _INPUT) -> None:
    user32 = ctypes.windll.user32
    user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
    array = (_INPUT * len(inputs))(*inputs)
    if user32.SendInput(len(inputs), array, ctypes.sizeof(_INPUT)) != len(inputs):
        raise OSError("Windows did not accept the keyboard or mouse input")


def _key(vk: int = 0, scan: int = 0, flags: int = 0) -> _INPUT:
    return _INPUT(type=INPUT_KEYBOARD, ki=_KEYBDINPUT(vk, scan, flags, 0, 0))


def _mouse(flags: int, data: int = 0) -> _INPUT:
    return _INPUT(type=INPUT_MOUSE, mi=_MOUSEINPUT(0, 0, data & 0xFFFFFFFF, flags, 0, 0))


@dataclass
class WindowsInput:
    """Sends real keyboard and mouse input. Only call it after the rules above have passed."""

    key_delay: float = 0.012  # between typed characters
    click_delay: float = 0.06

    def click(self, x: int, y: int, button: str = "left", count: int = 1) -> None:
        down, up = (
            (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP)
            if button == "right"
            else (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP)
        )
        ctypes.windll.user32.SetCursorPos(int(x), int(y))
        time.sleep(self.click_delay)
        for _ in range(count):
            _send(_mouse(down))
            time.sleep(0.03)
            _send(_mouse(up))
            time.sleep(0.09)

    def type_text(self, text: str) -> None:
        for ch in text:
            if ch == "\n":
                self.press("enter")
            elif ch == "\t":
                self.press("tab")
            else:
                units = ch.encode("utf-16-le")
                for i in range(0, len(units), 2):
                    code = int.from_bytes(units[i : i + 2], "little")
                    _send(_key(0, code, KEYEVENTF_UNICODE))
                    _send(_key(0, code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
            time.sleep(self.key_delay)

    def press(self, spec: str) -> None:
        names = parse_keys(spec)  # re-checked here: the driver never trusts its caller
        flags = [KEYEVENTF_EXTENDEDKEY if n in EXTENDED else 0 for n in names]
        for name, flag in zip(names, flags, strict=True):
            _send(_key(VK[name], 0, flag))
            time.sleep(0.02)
        for name, flag in reversed(list(zip(names, flags, strict=True))):
            _send(_key(VK[name], 0, flag | KEYEVENTF_KEYUP))
            time.sleep(0.02)

    def scroll(self, amount: int, at: tuple[int, int] | None = None) -> None:
        if at is not None:
            ctypes.windll.user32.SetCursorPos(int(at[0]), int(at[1]))
            time.sleep(self.click_delay)
        notches = max(-20, min(20, int(amount)))
        for _ in range(abs(notches)):
            _send(_mouse(MOUSEEVENTF_WHEEL, -120 if notches > 0 else 120))  # positive = down
            time.sleep(0.03)


def real_input_available() -> bool:
    return sys.platform == "win32"
