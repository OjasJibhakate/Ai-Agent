"""The safety rules for keys, typed text and target windows (pure functions, no desktop)."""

import ctypes
import sys

import pytest

from ragnarok.skills.screen.input import (
    InputRefused,
    WindowsInput,
    check_target_window,
    check_text,
    parse_keys,
)
from ragnarok.skills.screen.winapi import WindowInfo


@pytest.mark.parametrize(
    ("spec", "parts"),
    [
        ("enter", ["enter"]),
        ("ENTER", ["enter"]),
        ("ctrl+l", ["ctrl", "l"]),
        ("Ctrl + A", ["ctrl", "a"]),
        ("win", ["win"]),
        ("win+d", ["win", "d"]),
        ("alt+left", ["alt", "left"]),
        ("alt+tab", ["alt", "tab"]),
        ("ctrl+shift+t", ["ctrl", "shift", "t"]),
        ("ctrl+tab", ["ctrl", "tab"]),
        ("pagedown", ["pagedown"]),
        ("f5", ["f5"]),
        ("space", ["space"]),
    ],
)
def test_allowed_keys(spec, parts):
    assert parse_keys(spec) == parts


@pytest.mark.parametrize(
    "spec",
    [
        "alt+f4",  # closes the window, maybe with unsaved work
        "win+r",  # the Run box: runs any command
        "win+x",
        "win+l",
        "ctrl+alt+delete",
        "ctrl+shift+esc",  # Task Manager
        "ctrl+alt+t",
        "ctrl+q",
        "shift+f10",
        "ctrl+shift+n",
        "a",  # letters must go through `type`, where the secret check applies
        "5",
        "ctrl",  # a modifier alone does nothing useful
        "shift+alt",
        "enter+tab",
        "ctrl+ctrl+l",
        "banana",
        "ctrl+l+a",
        "",
        "+",
        "ctrl+alt+shift+win",
    ],
)
def test_refused_keys(spec):
    with pytest.raises(InputRefused):
        parse_keys(spec)


@pytest.mark.parametrize(
    "text", ["carryminati", "hello world", "line one\nline two", "naïve café 日本語", "9876543210"]
)
def test_ordinary_text_may_be_typed(text):
    check_text(text)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "x" * 301,
        "tab\x07bell",
        "my password is hunter22",
        "password: swordfish",
        "token=abcd1234efgh",
        "Authorization: Bearer abcdefghijklmnop1234",
        "AKIAABCDEFGHIJKLMNOP",
        "sk-abcdefghijklmnopqrstuv",
        "4111 1111 1111 1111",
        "4111-1111-1111-1111",
        "4111111111111111",
    ],
)
def test_secrets_and_junk_are_never_typed(text):
    with pytest.raises(InputRefused):
        check_text(text)


def win(process: str, title: str = "Some window", hwnd: int = 5) -> WindowInfo:
    return WindowInfo(hwnd, title, process, 99)


@pytest.mark.parametrize(
    "process",
    ["powershell.exe", "pwsh.exe", "cmd.exe", "windowsterminal.exe", "conhost.exe", "wsl.exe",
     "regedit.exe", "taskmgr.exe", "mmc.exe", "mshta.exe", "wscript.exe"],
)  # fmt: skip
def test_nothing_is_typed_into_a_shell_or_system_tool(process):
    with pytest.raises(InputRefused, match="run as a command"):
        check_target_window(win(process), "typing")


@pytest.mark.parametrize(
    "title", ["Run", "run", "Windows Security", "User Account Control", "Windows PowerShell"]
)
def test_nothing_is_typed_into_the_run_box_or_security_prompts(title):
    with pytest.raises(InputRefused):
        check_target_window(win("explorer.exe", title), "keys")


@pytest.mark.parametrize(
    "process", ["notepad.exe", "chrome.exe", "msedge.exe", "searchhost.exe", "code.exe"]
)
def test_ordinary_programs_may_receive_typing(process):
    check_target_window(win(process, "Untitled - Notepad"), "typing")
    check_target_window(None, "typing")  # no window in front: nothing to protect


def test_a_title_that_merely_contains_run_is_fine():
    check_target_window(win("chrome.exe", "How to run a marathon - Chrome"), "typing")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows input structures")
def test_the_windows_input_structure_has_the_size_windows_requires():
    from ragnarok.skills.screen.input import _INPUT

    assert ctypes.sizeof(_INPUT) == (40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows input")
def test_the_driver_rechecks_keys_and_refuses_before_sending_anything(monkeypatch):
    sent = []
    monkeypatch.setattr("ragnarok.skills.screen.input._send", lambda *a: sent.append(a))
    for bad in ("alt+f4", "win+r", "a", "ctrl+alt+delete"):
        with pytest.raises(InputRefused):
            WindowsInput().press(bad)
    assert sent == []  # nothing reached the keyboard


@pytest.mark.skipif(sys.platform != "win32", reason="Windows input")
def test_the_driver_sends_modifiers_down_then_up_in_reverse(monkeypatch):
    recorded = []
    monkeypatch.setattr(
        "ragnarok.skills.screen.input._send",
        lambda *inputs: recorded.append([(i.ki.wVk, bool(i.ki.dwFlags & 2)) for i in inputs]),
    )
    monkeypatch.setattr("time.sleep", lambda s: None)
    WindowsInput().press("ctrl+l")
    flat = [item for group in recorded for item in group]
    assert flat == [
        (0x11, False),
        (0x4C, False),
        (0x4C, True),
        (0x11, True),
    ]  # ctrl down, L down, L up, ctrl up


@pytest.mark.skipif(sys.platform != "win32", reason="Windows input")
def test_typed_text_goes_out_as_unicode_with_newlines_as_enter(monkeypatch):
    recorded = []
    monkeypatch.setattr(
        "ragnarok.skills.screen.input._send",
        lambda *inputs: recorded.extend((i.ki.wVk, i.ki.wScan, i.ki.dwFlags) for i in inputs),
    )
    monkeypatch.setattr("time.sleep", lambda s: None)
    WindowsInput().type_text("hi\né")
    scans = [(vk, scan) for vk, scan, flags in recorded if flags & 4]  # UNICODE events
    assert scans[0] == (0, ord("h")) and scans[2] == (0, ord("i"))
    assert (0, ord("é")) in scans
    assert any(vk == 0x0D for vk, _, _ in recorded)  # the newline became an Enter key
