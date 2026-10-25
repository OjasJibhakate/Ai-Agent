"""Triggers inside the chat: things that arrive by themselves, /inbox, /reminders, /cancel."""

import re
import time

from ragnarok.runtime.gateway import ChatResult
from ragnarok.ui import Console
from tests.test_chat_flow import build_app, shown, vault_key  # noqa: F401  (fixture)
from tests.test_m4_agent import say
from tests.test_m4_ui import Stream


def slow_lines(*lines, wait_before=None, wait=0.0):
    """Typed lines, optionally pausing before one so background work gets a turn."""
    it = iter(lines)
    count = [0]

    def read(prompt):
        count[0] += 1
        if wait_before is not None and count[0] == wait_before:
            time.sleep(wait)
        return next(it)

    return read


async def test_a_reminder_that_came_due_while_closed_is_announced_at_start(tmp_path, vault_key):  # noqa: F811
    app, rt, out, _ = build_app(tmp_path, vault_key, [], ["/quit"])
    app.triggers.add("date", "2000-01-01 09:00:00", "notify", "call mom", "long ago")
    app.read_line = slow_lines("/quit", wait_before=1, wait=0.6)  # let the engine have a turn
    await app.run()
    text = shown(out)
    flat = re.sub(r"\s+", " ", text)  # the alert wraps long lines
    assert "Reminder · call mom" in flat and "I was not running then" in flat
    assert app.triggers.unread_count() == 1
    rt.vault.close()


async def test_unread_inbox_items_are_mentioned_and_inbox_lists_then_marks_them_read(
    tmp_path,
    vault_key,  # noqa: F811
):
    app, rt, out, _ = build_app(tmp_path, vault_key, [], ["/inbox", "/inbox", "/quit"])
    app.triggers.post("reminder", "Pay the bill", "Set for today at 6:00 PM")
    app.triggers.post("file", "New file: invoice.pdf", "C:/Users/x/Downloads/invoice.pdf")
    await app.run()
    text = shown(out)
    assert "2 new in your Inbox. Type /inbox to read." in text
    assert "Pay the bill" in text and "New file: invoice.pdf" in text
    assert "C:/Users/x/Downloads/invoice.pdf" in text
    assert app.triggers.unread_count() == 0  # reading marks them read
    rt.vault.close()


async def test_reminders_and_cancel_commands(tmp_path, vault_key):  # noqa: F811
    app, rt, out, _ = build_app(
        tmp_path,
        vault_key,
        [],
        ["/reminders", "/cancel 1", "/cancel 1", "/cancel x", "/reminders", "/quit"],
    )
    app.triggers.add("date", "2999-01-01 18:00:00", "notify", "call mom", "far away")
    await app.run()
    text = shown(out)
    assert "#1 reminder: call mom - far away" in text
    assert "Cancelled #1." in text and "No such active reminder." in text
    assert "Unknown command /cancel" in text  # a non-number is not a cancel
    assert "Nothing is scheduled" in text
    rt.vault.close()


async def test_a_folder_goal_runs_headless_and_changes_nothing(tmp_path, vault_key):  # noqa: F811
    """A trigger's goal can read and think; whatever it wanted to change waits for the owner."""
    call = ChatResult(
        tool_calls=[{"function": {"name": "risky", "arguments": {"command": "move invoice"}}}]
    )
    replies = [call, say("The invoice is from ACME, 4200 rupees.")]
    app, rt, out, ran = build_app(tmp_path, vault_key, replies, ["/quit"])
    result = await app._run_goal("Look at the new file and file it", {"path": "x"})
    assert "ACME" in result
    assert "Waiting for your OK" in result and "risky(command='move invoice'" in result
    assert ran == []  # the risky tool never ran
    rt.vault.close()


async def test_the_help_lists_the_trigger_commands(tmp_path, vault_key):  # noqa: F811
    out = Stream()
    Console(out, color=False).help()
    text = re.sub(r"\s+", " ", out.getvalue())
    for cmd in ("/inbox", "/reminders", "/cancel N"):
        assert cmd in text
