"""Record one spoken sentence from a microphone (Windows waveIn), with no extra package.

Why not let the recogniser open the microphone itself? Because then Windows' default input is the
only choice (often a Bluetooth headset that is not being worn) and a quiet microphone cannot be
helped. Recording here means:

- the microphone can be chosen (`RAGNAROK_MIC` = a device number, or part of its name);
- quiet speech is amplified before recognition (up to 40x), which is what makes a laptop's
  low-gain array microphone usable;
- the sentence ends by itself, 1.2 s after you stop talking;
- when nothing is heard, the levels say why (a silent microphone is different from a quiet room).

The audio stays in memory: it is handed to the offline recogniser through a pipe and never written
to a file.
"""

from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
from array import array
from collections import deque
from collections.abc import Callable
from ctypes import wintypes as w
from dataclasses import dataclass

RATE = 16000
FRAME_SECONDS = 0.1
FRAME_SAMPLES = int(RATE * FRAME_SECONDS)
BUFFERS = 12
WAVE_MAPPER = 0xFFFFFFFF
WHDR_DONE = 0x1
END_SILENCE_FRAMES = 12  # 1.2 s of quiet ends the sentence
LEAD_FRAMES = 3  # keep 0.3 s before the first loud frame, so the first word is not clipped
END_FRACTION = 0.06  # the sentence ends when it falls below 6% of its own peak (and the noise)
TRAIL_FRAMES = 3  # and keep only 0.3 s of the quiet that ended the sentence
MIN_THRESHOLD = 0.006  # a frame must reach 0.6% of full scale to count as speech
TARGET_PEAK = 0.6
MAX_GAIN = 40.0


class MicError(Exception):
    """The microphone could not be used (the text is shown to the user)."""


@dataclass
class Recording:
    pcm: bytes = b""  # 16-bit mono 16 kHz, amplified; empty when no speech was heard
    heard_speech: bool = False
    device: str = ""
    peak_percent: float = 0.0  # loudest moment, % of full scale, before amplification
    noise_percent: float = 0.0  # the room's own level, same scale
    seconds: float = 0.0  # length of the recorded sentence
    gain: float = 1.0


class _Fmt(ctypes.Structure):
    _fields_ = [
        ("tag", w.WORD), ("channels", w.WORD), ("rate", w.DWORD), ("avg", w.DWORD),
        ("align", w.WORD), ("bits", w.WORD), ("size", w.WORD),
    ]  # fmt: skip


class _Hdr(ctypes.Structure):
    pass


_Hdr._fields_ = [
    ("data", ctypes.c_void_p), ("length", w.DWORD), ("recorded", w.DWORD),
    ("user", ctypes.c_void_p), ("flags", w.DWORD), ("loops", w.DWORD),
    ("next", ctypes.c_void_p), ("reserved", ctypes.c_void_p),
]  # fmt: skip


class _Caps(ctypes.Structure):
    _fields_ = [
        ("mid", w.WORD), ("pid", w.WORD), ("version", w.DWORD), ("name", ctypes.c_wchar * 32),
        ("formats", w.DWORD), ("channels", w.WORD), ("reserved", w.WORD),
    ]  # fmt: skip


def devices() -> list[str]:
    """Names of the microphones, in Windows' numbering."""
    if sys.platform != "win32":
        return []
    winmm = ctypes.windll.winmm  # type: ignore[attr-defined]
    names = []
    for i in range(winmm.waveInGetNumDevs()):
        caps = _Caps()
        winmm.waveInGetDevCapsW(i, ctypes.byref(caps), ctypes.sizeof(caps))
        names.append(caps.name)
    return names


def choose_device(wanted: str | None = None) -> int:
    """The device to record from: RAGNAROK_MIC (a number or part of a name), else the default."""
    wanted = wanted if wanted is not None else os.environ.get("RAGNAROK_MIC", "")
    wanted = wanted.strip()
    if not wanted:
        return WAVE_MAPPER
    names = devices()
    if wanted.isdigit() and int(wanted) < len(names):
        return int(wanted)
    for i, name in enumerate(names):
        if wanted.lower() in name.lower():
            return i
    raise MicError(
        f"no microphone matches RAGNAROK_MIC={wanted!r}; found: {', '.join(names) or 'none'}"
    )


def amplify(samples: array, peak: int) -> tuple[array, float]:
    """Scale quiet speech up to a good level (never more than MAX_GAIN, never clipping)."""
    if peak <= 0:
        return samples, 1.0
    gain = max(1.0, min(MAX_GAIN, TARGET_PEAK * 32767 / peak))
    if gain == 1.0:
        return samples, 1.0
    return array("h", (max(-32768, min(32767, int(s * gain))) for s in samples)), gain


def detect(
    frames: Callable[[], bytes | None],
    wait: float,
    max_seconds: float,
    stop: threading.Event | None,
) -> tuple[list[array], float, float, bool]:
    """Pure speech detection over a source of 100 ms frames (None = no frame yet).

    Returns (the frames of the sentence, loudest peak %, noise %, whether speech started).
    """
    start = time.monotonic()
    lead: deque[array] = deque(maxlen=LEAD_FRAMES)
    quiet_levels: list[int] = []
    sentence: list[array] = []
    speaking, quiet_run, loudest, loudest_in_sentence = False, 0, 0, 0
    threshold = 0
    while time.monotonic() - start < (max_seconds if speaking else wait):
        if stop is not None and stop.is_set():
            break
        raw = frames()
        if raw is None:
            time.sleep(0.02)
            continue
        frame = array("h")
        frame.frombytes(raw[: len(raw) // 2 * 2])
        level = max((abs(s) for s in frame), default=0)
        loudest = max(loudest, level)
        if not speaking:
            if len(quiet_levels) < 4:  # the first 0.4 s sets the room's noise floor
                quiet_levels.append(level)
                lead.append(frame)
                continue
            if not threshold:
                noise = sorted(quiet_levels)[len(quiet_levels) // 2]
                threshold = max(3 * noise, MIN_THRESHOLD * 32767)
            if level >= threshold:
                speaking = True
                sentence.extend(lead)
                sentence.append(frame)
                start = time.monotonic()
            else:
                if len(quiet_levels) < 40:
                    quiet_levels.append(level)
                lead.append(frame)
            continue
        sentence.append(frame)
        loudest_in_sentence = max(loudest_in_sentence, level)
        # quiet means well below this speaker's own loudness, not only below the room noise
        quiet_run = (
            0 if level >= max(threshold, END_FRACTION * loudest_in_sentence) else quiet_run + 1
        )
        if quiet_run >= END_SILENCE_FRAMES:
            break
    if (
        speaking and quiet_run > TRAIL_FRAMES
    ):  # the pause that ended it is not part of what was said
        del sentence[len(sentence) - (quiet_run - TRAIL_FRAMES) :]
    noise_level = sorted(quiet_levels)[len(quiet_levels) // 2] if quiet_levels else 0
    return sentence, 100 * loudest / 32768, 100 * noise_level / 32768, speaking


def record(
    wait: float = 7.0,
    max_seconds: float = 15.0,
    device: int | None = None,
    stop: threading.Event | None = None,
) -> Recording:
    """Wait up to `wait` seconds for speech, then record the sentence. Blocks: run in a thread."""
    if sys.platform != "win32":
        raise MicError("recording needs Windows")
    winmm = ctypes.windll.winmm  # type: ignore[attr-defined]
    dev = choose_device() if device is None else device
    names = devices()
    if not names:
        raise MicError("no microphone was found (check Settings > Privacy > Microphone)")
    handle = ctypes.c_void_p()
    fmt = _Fmt(1, 1, RATE, RATE * 2, 2, 16, 0)
    if winmm.waveInOpen(ctypes.byref(handle), dev, ctypes.byref(fmt), 0, 0, 0):
        raise MicError("the microphone could not be opened (another program may be using it)")
    name = names[dev] if dev < len(names) else "default microphone"
    buffers = [ctypes.create_string_buffer(FRAME_SAMPLES * 2) for _ in range(BUFFERS)]
    headers = [
        _Hdr(ctypes.cast(b, ctypes.c_void_p), FRAME_SAMPLES * 2, 0, None, 0, 0, None, None)
        for b in buffers
    ]
    for hdr in headers:
        winmm.waveInPrepareHeader(handle, ctypes.byref(hdr), ctypes.sizeof(hdr))
        winmm.waveInAddBuffer(handle, ctypes.byref(hdr), ctypes.sizeof(hdr))
    position = 0

    def next_frame() -> bytes | None:
        nonlocal position
        hdr = headers[position]
        if not hdr.flags & WHDR_DONE:
            return None
        data = ctypes.string_at(hdr.data, hdr.recorded)
        winmm.waveInAddBuffer(handle, ctypes.byref(hdr), ctypes.sizeof(hdr))
        position = (position + 1) % BUFFERS
        return data

    winmm.waveInStart(handle)
    try:
        sentence, peak, noise, heard = detect(next_frame, wait, max_seconds, stop)
    finally:
        winmm.waveInStop(handle)
        winmm.waveInReset(handle)
        for hdr in headers:
            winmm.waveInUnprepareHeader(handle, ctypes.byref(hdr), ctypes.sizeof(hdr))
        winmm.waveInClose(handle)
    rec = Recording(device=name, peak_percent=round(peak, 2), noise_percent=round(noise, 2))
    if not heard:
        return rec
    samples = array("h")
    for frame in sentence:
        samples.extend(frame)
    loudest = max((abs(s) for s in samples), default=0)
    boosted, gain = amplify(samples, loudest)
    rec.pcm, rec.heard_speech, rec.gain = boosted.tobytes(), True, round(gain, 1)
    rec.seconds = round(len(samples) / RATE, 1)
    return rec


def level_report(rec: Recording) -> str:
    """One plain sentence saying what the microphone delivered (for when nothing was understood)."""
    if rec.peak_percent >= 97.0:
        return (
            f'"{rec.device}" is too loud (it reached {rec.peak_percent}% of full scale, so the '
            "sound is distorted): lower its input volume in Settings > System > Sound > Input "
            "to about 60-70%."
        )
    if not rec.heard_speech and rec.peak_percent < 1.0:
        return (
            f'"{rec.device}" is almost silent (loudest {rec.peak_percent}% of full scale). '
            "Check it is not muted or out of reach, raise its volume in "
            "Settings > System > Sound > Input, "
            "or pick another microphone with RAGNAROK_MIC=<number> (/voice test lists them)."
        )
    if not rec.heard_speech:
        return (
            f'"{rec.device}" picked up sound (loudest {rec.peak_percent}%) but no speech started.'
        )
    said = (
        f'"{rec.device}" heard {rec.seconds}s of speech '
        f"(loudest {rec.peak_percent}%, boosted {rec.gain}x)."
    )
    if rec.peak_percent < 5.0:
        said += (
            " That is very quiet, so the words are likely to be misheard: raise the input volume "
            "in Settings > System > Sound > Input to near 100%, or speak closer to the microphone."
        )
    return said
