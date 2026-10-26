"""Quitting the cat must end the process, or the one-copy lock stays held and meow does nothing."""

from __future__ import annotations

import subprocess
import sys
import textwrap

BLOCKED_WORKER = textwrap.dedent(
    """
    import asyncio, queue, threading, time
    q = queue.Queue()
    async def engine():
        await asyncio.to_thread(q.get)  # like read_line / approve_line, waiting for the window
    threading.Thread(target=lambda: asyncio.run(engine()), daemon=True).start()
    time.sleep(0.5)
    {leave}
    """
)


def run(leave: str) -> subprocess.CompletedProcess:
    code = BLOCKED_WORKER.format(leave=leave)
    return subprocess.run([sys.executable, "-c", code], timeout=15, capture_output=True)


# Checked once by hand: with `pass` instead of exit_now the same script hangs until killed.
def test_exit_now_ends_the_process_even_with_a_blocked_worker():
    done = run("from ragnarok.gui.app import exit_now; exit_now(0)")
    assert done.returncode == 0
