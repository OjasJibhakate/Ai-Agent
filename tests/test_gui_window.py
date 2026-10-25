"""The chat window drawn for real (hidden) and clicked: bubbles, cards, buttons, the input box."""

import tkinter as tk

import pytest

from ragnarok.gui.bridge import Bridge
from ragnarok.gui.chat import HEIGHT, WIDTH, ChatWindow


@pytest.fixture
def window():
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display to draw on")
    root.withdraw()
    bridge = Bridge()
    busy: list[bool] = []
    closed: list[bool] = []
    chat = ChatWindow(root, bridge, on_busy=busy.append, on_close=lambda: closed.append(True))
    chat.busy_log, chat.closed_log = busy, closed  # type: ignore[attr-defined]
    yield chat, bridge, root
    root.destroy()


def labels(widget: tk.Misc) -> list[tk.Label]:
    found = []
    for child in widget.winfo_children():
        if isinstance(child, tk.Label):
            found.append(child)
        found.extend(labels(child))
    return found


def texts(chat: ChatWindow) -> list[str]:
    return [label.cget("text") for label in labels(chat.column)]


def click(label: tk.Label) -> None:
    label.event_generate("<Button-1>", x=2, y=2)


def test_the_welcome_fills_the_status_and_greets_a_new_user(window):
    chat, _bridge, _root = window
    chat.handle(
        "welcome", {"model": "qwen3.5:4b", "memories": 0, "gpu": "100% GPU", "first_run": True}
    )
    assert "qwen3.5:4b" in chat.status.cget("text") and "100% GPU" in chat.status.cget("text")
    assert any("Hi! I'm Ragnarok" in t for t in texts(chat))


def test_a_streamed_answer_grows_inside_one_bubble(window):
    chat, _bridge, _root = window
    chat.handle("assistant_start", {"kind": "bot"})
    chat.handle("assistant_chunk", {"text": "It is "})
    chat.handle("assistant_chunk", {"text": "360."})
    chat.handle("footer", {"tools": [("memory_search", True)], "seconds": 1.4})
    chat.handle("assistant_start", {"kind": "done"})
    chat.handle("assistant_chunk", {"text": "Saved."})
    all_text = texts(chat)
    assert "It is 360." in all_text and "Saved." in all_text
    assert any("memory_search · 1.4s" in t for t in all_text)


def test_typing_a_line_and_pressing_enter_sends_it_and_shows_it(window):
    chat, bridge, _root = window
    chat.entry.insert("1.0", "what is 15 percent of 2400")
    chat._enter(None)  # type: ignore[arg-type]
    assert bridge.inputs.get_nowait() == "what is 15 percent of 2400"
    assert "what is 15 percent of 2400" in texts(chat)
    assert chat.busy and chat.busy_log == [True]
    assert chat.entry.get("1.0", "end").strip() == ""  # the box is cleared


def test_an_empty_line_sends_nothing_and_slash_quit_closes_the_window(window):
    chat, bridge, _root = window
    chat.entry.insert("1.0", "   ")
    chat._send()
    assert bridge.inputs.empty() and not chat.busy
    chat.entry.insert("1.0", "/quit")
    chat._send()
    assert bridge.inputs.empty() and chat.closed_log == [True]


def test_the_engine_becoming_idle_ends_the_busy_state_and_clears_the_thinking_line(window):
    chat, _bridge, _root = window
    chat.set_busy(True)
    chat.tick()
    assert chat.thinking.cget("text").startswith("Thinking")
    chat.handle("idle", {})
    assert not chat.busy and chat.thinking.cget("text") == "" and chat.busy_log == [True, False]


def test_a_permission_card_lists_the_action_and_the_buttons_answer_the_engine(window):
    chat, bridge, _root = window
    card = {
        "type": "permission",
        "tool": "run_command",
        "args": {"command": "'dir'"},
        "note": "Careful.",
    }
    chat.handle("approve", card)
    shown = texts(chat)
    assert "Permission needed" in shown and "run_command" in shown and "Careful." in shown
    assert any("command  'dir'" in t for t in shown)
    allow = next(label for label in labels(chat.column) if label.cget("text").strip() == "Allow")
    click(allow)
    assert bridge.answers.get_nowait() == "y"
    assert "✓ Allowed" in texts(chat)  # the buttons are replaced by what you chose
    assert not any(t.strip() == "Allow" for t in texts(chat))


def test_denying_answers_no(window):
    chat, bridge, _root = window
    chat.handle("approve", {"type": "permission", "tool": "x", "args": {}, "note": ""})
    deny = next(label for label in labels(chat.column) if label.cget("text").strip() == "Deny")
    click(deny)
    assert bridge.answers.get_nowait() == "n" and "✗ Declined" in texts(chat)


def test_a_plan_card_shows_every_step_and_the_buttons_run_or_cancel(window):
    chat, bridge, _root = window
    rows = [("s1", "Read the notes", [], "R0", False), ("s2", "Write the doc", ["s1"], "R1", True)]
    chat.handle("approve", {"type": "plan", "goal": "make a report", "rows": rows})
    shown = " | ".join(texts(chat))
    assert "Plan · 2 steps" in shown and "Read the notes" in shown and "after s1" in shown
    assert "asks you" in shown
    run = next(label for label in labels(chat.column) if label.cget("text").strip() == "Run plan")
    click(run)
    assert bridge.answers.get_nowait() == "y"


def test_plan_progress_and_alerts_and_problems_are_shown(window):
    chat, _bridge, _root = window
    chat.handle("step", {"state": "running", "id": "s1", "title": "Read the notes", "note": ""})
    chat.handle("step", {"state": "done", "id": "s1", "title": "Read the notes", "note": "3 pages"})
    chat.handle("step", {"state": "failed", "id": "s2", "title": "Write it", "note": "timeout"})
    chat.handle("alert", {"kind": "reminder", "title": "Call mom", "body": "Set for 6 PM"})
    chat.handle("error", {"text": "No such memory."})
    chat.handle("success", {"text": "Forgot m3."})
    chat.handle("tool", {"text": "step 1: click the button"})
    shown = " | ".join(texts(chat))
    for expected in ("●  s1", "✓  s1", "3 pages", "✗  s2", "✦ Reminder", "Call mom", "Set for 6 PM",
                     "✗ No such memory.", "✓ Forgot m3.", "⎿  step 1"):  # fmt: skip
        assert expected in shown, expected


def test_a_dead_engine_is_reported_and_stops_the_waiting(window):
    chat, _bridge, _root = window
    chat.set_busy(True)
    chat.handle("closed", {})
    assert not chat.busy and any("not running" in t for t in texts(chat))


def test_clearing_removes_the_conversation(window):
    chat, _bridge, _root = window
    chat.handle("notice", {"text": "hello"})
    assert texts(chat)
    chat.handle("clear", {})
    assert texts(chat) == []


def test_the_window_opens_inside_the_screen_above_the_cat_even_at_the_edges(window):
    chat, _bridge, root = window
    chat.show(5, 600, (1920, 1080))  # a cat at the far left
    root.update_idletasks()
    assert chat.top.winfo_x() >= 8
    chat.show(1915, 600, (1920, 1080))  # and the far right
    root.update_idletasks()
    assert chat.top.winfo_x() + WIDTH <= 1920 - 8 + 1
    chat.show(900, 100, (1920, 1080))  # a cat near the top of a short screen
    root.update_idletasks()
    assert chat.top.winfo_y() >= 8
    chat.hide()
    assert chat.closed_log[-1] is True and not chat.visible


# ---- a small window -----------------------------------------------------------------------------


def test_the_window_is_small_and_the_close_button_and_status_still_fit_inside_it(window):
    chat, _bridge, root = window
    assert (WIDTH, HEIGHT) == (300, 360)
    welcome = {"model": "qwen3.5:4b", "memories": 1234, "gpu": "100% GPU", "first_run": False}
    chat.handle("welcome", welcome)
    chat.top.geometry(f"{WIDTH}x{HEIGHT}+40+40")
    chat.top.deiconify()
    root.update()
    close = next(label for label in labels(chat.top) if label.cget("text") == "✕")
    right_edge = chat.top.winfo_rootx() + WIDTH
    assert close.winfo_ismapped() and close.winfo_rootx() + close.winfo_width() <= right_edge
    assert chat.status.winfo_width() >= chat.status.winfo_reqwidth()  # the status is not cut off
    assert chat.status.winfo_rootx() + chat.status.winfo_width() <= close.winfo_rootx()


def test_a_returning_user_is_told_how_much_is_remembered_only_when_it_is_something(window):
    chat, _bridge, _root = window
    base = {"model": "m", "gpu": "100% GPU", "first_run": False}
    chat.handle("welcome", {**base, "memories": 12})
    chat.handle("welcome", {**base, "memories": 1})
    chat.handle("welcome", {**base, "memories": 0})
    shown = texts(chat)
    assert "Welcome back. I remember 12 things about you. What can I do for you?" in shown
    assert "Welcome back. I remember 1 thing about you. What can I do for you?" in shown
    assert "Welcome back. What can I do for you?" in shown
    assert (
        chat.status.cget("text") == "m · 100% GPU"
    )  # the count is in the greeting, not the header
