"""A circuit breaker for tools that keep crashing (NFR3.1, ARCHITECTURE 7.4).

A tool that fails three times in a row is "open" for 60 seconds: calls to it fail at once with a
clear message instead of waiting on something that is down, so a plan fails that step fast, blocks
only what depends on it, and carries on with everything else. After the pause one trial call is
allowed; if it works the tool is trusted again, if not the pause starts over.

Only real faults count (a crash or a timeout), not a tool saying "no such folder": that is the
user's mistake, not a broken tool.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class _State:
    failures: int = 0
    opened_at: float | None = None
    trial: bool = False  # one call is in flight after the pause


class CircuitBreaker:
    def __init__(
        self,
        threshold: int = 3,
        cooldown: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.threshold = threshold
        self.cooldown = cooldown
        self.clock = clock
        self._tools: dict[str, _State] = {}

    def _state(self, name: str) -> _State:
        return self._tools.setdefault(name, _State())

    def blocked_for(self, name: str) -> float:
        """Seconds until `name` may be tried again; 0 when calls are allowed."""
        state = self._state(name)
        if state.opened_at is None:
            return 0.0
        waited = self.clock() - state.opened_at
        if waited < self.cooldown:
            return self.cooldown - waited
        if state.trial:
            return 1.0  # someone is already testing it
        state.trial = True  # half-open: let exactly one call through
        return 0.0

    def success(self, name: str) -> None:
        self._tools[name] = _State()

    def failure(self, name: str) -> None:
        state = self._state(name)
        state.failures += 1
        state.trial = False
        if state.failures >= self.threshold or state.opened_at is not None:
            state.opened_at = self.clock()  # (re)open: the pause starts from now

    def is_open(self, name: str) -> bool:
        state = self._state(name)
        return state.opened_at is not None and self.clock() - state.opened_at < self.cooldown
