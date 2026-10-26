"""Telling a single click from a double click on the cat.

A single click opens or closes the chat; a double click starts (or ends) a voice conversation. The
single click waits a moment to see whether a second one follows, so a double click does not also
open and close the chat. The window system's clock is passed in, so this can be tested.
"""

from __future__ import annotations

from collections.abc import Callable

DOUBLE_CLICK_MS = 300


class DoubleClick:
    def __init__(
        self,
        schedule: Callable[[int, Callable[[], None]], object],
        cancel: Callable[[object], None],
        on_single: Callable[[], None],
        on_double: Callable[[], None],
        delay_ms: int = DOUBLE_CLICK_MS,
    ) -> None:
        self._schedule, self._cancel = schedule, cancel
        self.on_single, self.on_double, self.delay_ms = on_single, on_double, delay_ms
        self._job: object | None = None

    def click(self) -> None:
        if self._job is not None:  # a second click inside the window
            self._cancel(self._job)
            self._job = None
            self.on_double()
        else:
            self._job = self._schedule(self.delay_ms, self._fire)

    def _fire(self) -> None:
        self._job = None
        self.on_single()
