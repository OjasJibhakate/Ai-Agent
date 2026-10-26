"""The wake phrase "hey Ragnarok": say it, and Ragnarok starts listening.

This keeps the microphone open in the background, so it is **opt-in** (`/voice wake on`, or the
cat's right-click menu), it is remembered across restarts, and it can be turned off at any time.
What happens to what it hears:

- the microphone is read in short bursts; a burst is only looked at if it is speech of about five
  seconds or less (a conversation in the room is ignored, and so is music or silence);
- a short burst is turned into text on this computer with the local Whisper model, which is given
  the phrase as a hint so the name is recognised;
- the text is thrown away unless it contains "hey Ragnarok". Nothing is stored, logged or sent, and
  the audio is never written to a file;
- when the phrase is heard, whatever was said after it ("hey Ragnarok, open steam") is used as the
  message, or, if nothing followed, Ragnarok listens for one sentence and answers aloud.

It pauses while Ragnarok is busy, speaking, or already in a voice conversation, so it never hears
itself. Everything it does goes through the same permission checks as typing.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from difflib import SequenceMatcher

from ragnarok import voice_capture
from ragnarok.voice_capture import MicError, Recording

PHRASE = "hey ragnarok"
TARGETS = ("heyragnarok", "heyrangrok")  # how it is said (and the name alone is accepted too)
NAMES = ("ragnarok", "rangrok")
HINT = "Hey Ragnarok."  # given to the model so it spells the name correctly
BURST_WAIT_SECONDS = 5.0  # how long one look at the microphone waits for speech
MAX_BURST_SECONDS = 6.0  # longer speech is a conversation, not a wake phrase
MIN_RATIO = 0.74  # how close the heard words must be to "heyragnarok"
LETTERS = re.compile(r"[a-z]+")


@dataclass(frozen=True)
class WakeMatch:
    rest: str  # what was said after the phrase ("" if nothing)


def _squash(text: str) -> tuple[str, list[int]]:
    """The letters of `text` run together, and where each came from in the original."""
    letters, where = [], []
    for i, ch in enumerate(text.lower()):
        if "a" <= ch <= "z":
            letters.append(ch)
            where.append(i)
    return "".join(letters), where


def match_wake(text: str) -> WakeMatch | None:
    """Does `text` contain "hey Ragnarok" (allowing for how it is usually misheard)?"""
    squashed, where = _squash(text)
    best_ratio, best_end = 0.0, -1
    for target in TARGETS:
        for size in range(len(target) - 3, len(target) + 4):
            for start in range(0, max(0, len(squashed) - size) + 1):
                window = squashed[start : start + size]
                if len(window) < size:
                    break
                ratio = SequenceMatcher(None, window, target).ratio()
                if ratio > best_ratio:
                    best_ratio, best_end = ratio, start + size
    # "ragnarok" alone is a name nobody says by accident: accept it without the "hey"
    if best_ratio < MIN_RATIO:
        for name in NAMES:
            for size in range(len(name) - 1, len(name) + 3):
                for start in range(0, max(0, len(squashed) - size) + 1):
                    window = squashed[start : start + size]
                    if len(window) == size and SequenceMatcher(None, window, name).ratio() >= 0.9:
                        best_ratio, best_end = 1.0, start + size
                        break
                if best_ratio >= 1.0:
                    break
            if best_ratio >= 1.0:
                break
    if best_ratio < MIN_RATIO or best_end <= 0:
        return None
    cut = where[min(best_end, len(where)) - 1] + 1
    rest = text[cut:].strip(" ,.!?;:-" + chr(0x2014) + chr(0x2013))
    return WakeMatch(rest)


class WakeListener:
    """Watches the microphone for the wake phrase until stopped."""

    def __init__(
        self,
        *,
        lock: asyncio.Lock,
        transcribe: Callable[[bytes], str],
        on_wake: Callable[[str], Awaitable[None]],
        on_error: Callable[[str], None],
        paused: Callable[[], bool] = lambda: False,
        record: Callable[..., Recording] | None = None,
    ) -> None:
        self.lock, self.transcribe = lock, transcribe
        self.on_wake, self.on_error, self.paused = on_wake, on_error, paused
        self.record = record or voice_capture.record
        self.active = False
        self._stop = threading.Event()
        self._task: asyncio.Task | None = None
        self.heard_phrases = 0

    def start(self) -> None:
        if self._task is None or self._task.done():
            self.active = True
            self._stop.clear()
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self.active = False
        self._stop.set()  # a recording in progress ends at once
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    def interrupt(self) -> None:
        """Let go of the microphone now (someone else wants it); listening resumes on its own."""
        self._stop.set()

    async def _run(self) -> None:
        while self.active:
            if self.paused():
                await asyncio.sleep(0.4)
                continue
            async with self.lock:
                if not self.active or self.paused():
                    continue
                self._stop.clear()
                try:
                    rec = await asyncio.to_thread(
                        self.record, BURST_WAIT_SECONDS, MAX_BURST_SECONDS, None, self._stop
                    )
                except MicError as exc:
                    self.active = False
                    self.on_error(str(exc))
                    return
            if not rec.heard_speech or rec.seconds > MAX_BURST_SECONDS - 0.4:
                await asyncio.sleep(0.05)
                continue
            try:
                text = await asyncio.to_thread(self.transcribe, rec.pcm)
            except Exception as exc:  # a failing model must not take the chat down
                self.active = False
                self.on_error(f"the wake phrase stopped: {exc}")
                return
            found = match_wake(text) if text else None
            if found is not None:
                self.heard_phrases += 1
                await self.on_wake(found.rest)
