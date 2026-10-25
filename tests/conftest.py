"""Run `async def` tests with asyncio.run, so we don't need the pytest-asyncio dependency.
Also a hidden Tk window for the GUI tests."""

import asyncio
import inspect
import time
import tkinter as tk

import pytest


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem):
    if inspect.iscoroutinefunction(pyfuncitem.obj):
        args = {name: pyfuncitem.funcargs[name] for name in pyfuncitem._fixtureinfo.argnames}
        asyncio.run(pyfuncitem.obj(**args))
        return True
    return None


def make_tk(tries: int = 6) -> tk.Tk:
    """A new Tk window. Under pytest's default output capture, Tk fails to find its own start-up
    files about 3 times in 100 ("Can't find a usable init.tcl"); with `-s` it never does, and the
    real program is not run under capture, so it is not affected. Try again before giving up."""
    last: tk.TclError | None = None
    for attempt in range(tries):
        try:
            return tk.Tk()
        except tk.TclError as error:
            last = error
            time.sleep(0.05 * (attempt + 1))
    assert last is not None
    raise last


@pytest.fixture
def hidden_tk():
    """A Tk window that is not shown; the test is skipped only if there is really no display."""
    try:
        root = make_tk()
    except tk.TclError as error:
        pytest.skip(f"no display to draw on: {error}")
    root.withdraw()
    yield root
    root.destroy()
