"""Voice: Ragnarok speaks its answers (text to speech) and listens to you (speech to text).

Both use the speech engine that is part of Windows (`System.Speech`, run through PowerShell), so
there is no new package, no model download and no network: the microphone audio is recognised on
this machine and never leaves it. It is not the online "Windows speech recognition" service.

- Speaking: `/voice on` reads each answer aloud (a shortened, cleaned-up version: no code blocks,
  no addresses, no markdown). `/voice off` stops, and typing a new message cuts the speech short.
- Listening: `/listen` records one sentence and sends it as your message. `/voice talk` is a
  hands-free conversation: it listens, answers aloud, listens again, and ends when you say "stop
  listening", or after two silences in a row.

Ragnarok records the sentence itself (`voice_capture.py`: it can choose the microphone with
`RAGNAROK_MIC`, amplifies quiet speech and ends the sentence when you stop talking) and hands the
audio to the recogniser through a pipe, in memory. `/voice test` shows the levels it sees.

The microphone is only opened by those commands, never by a tool or a model, and the screen says
"Listening..." while it is open. Text and audio go through environment variables, never through
the command line, so nothing a person says can become a command. `RAGNAROK_NO_VOICE=1` turns
voice off completely. `RAGNAROK_VOICE` picks the voice (default: Zira); `RAGNAROK_VOICE_RATE`
is the speed, -10 to 10.

The offline recogniser is older and less accurate than a Whisper-sized model: it is meant for
short everyday sentences in English, and it may need a quiet room. A better recogniser would need a
new dependency and a model download, which needs a decision first.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import sys
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from ragnarok import voice_capture, voice_whisper
from ragnarok.voice_capture import MicError, Recording

MAX_SPOKEN_CHARS = 600
MAX_SPOKEN_SENTENCES = 5
STOP_PHRASES = ("stop listening", "stop voice", "end voice", "exit voice")
LISTEN_WAIT_SECONDS = 7  # how long to wait for you to start speaking
LISTEN_MAX_SECONDS = 15  # the longest single sentence
MIN_CONFIDENCE = 0.2  # the recogniser's own score below which a result is dropped

SPEAK_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$name = $env:RAG_TTS_VOICE
if (-not $name) {  # no voice chosen: an Indian English voice if Windows has one, else Zira
  $in = $s.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -eq 'en-IN' } | Select-Object -First 1
  if ($in) { $name = $in.VoiceInfo.Name } else { $name = 'Microsoft Zira Desktop' }
}
try { $s.SelectVoice($name) } catch {}
$s.Rate = [int]$env:RAG_TTS_RATE
$s.Speak($env:RAG_TTS_TEXT)
"""

RECOGNIZE_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$info = [System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers() | Select-Object -First 1
if (-not $info) { [Console]::Error.WriteLine('no speech recogniser is installed'); exit 3 }
$ms = New-Object System.IO.MemoryStream
[Console]::OpenStandardInput().CopyTo($ms)
$ms.Position = 0
$e = New-Object System.Speech.Recognition.SpeechRecognitionEngine($info)
$e.LoadGrammar((New-Object System.Speech.Recognition.DictationGrammar))
$f = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
$e.SetInputToAudioStream($ms, $f)
$r = $e.Recognize()
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
if ($r) { [Console]::Out.Write(([string]$r.Confidence) + "`n" + $r.Text) }
"""


class VoiceError(Exception):
    """Voice could not be used (no microphone, no recogniser, turned off); the text is shown."""


# ---- cleaning text for the ear -------------------------------------------------------------------

_CODE_BLOCK = re.compile(r"```.*?```", re.S)
_INLINE_CODE = re.compile(r"`([^`]*)`")
_URL = re.compile(r"https?://\S+|www\.\S+")
_PATH = re.compile(r"\b[A-Za-z]:\\[^\s]*")
_MARKDOWN = re.compile(r"[*_#>|~]+")
_MARKER = re.compile(r"[✓✗●●•→⎿▸◆✦]+")
_SPACES = re.compile(r"[ \t]+")


def speakable(text: str) -> str:
    """The part of an answer worth reading aloud: no code, links, paths or markdown, and short."""
    text = _CODE_BLOCK.sub(" (the code is on the screen) ", text)
    text = _INLINE_CODE.sub(r"\1", text)
    text = _URL.sub("a link", text)
    text = _PATH.sub("a file path", text)
    text = _MARKER.sub(" ", _MARKDOWN.sub(" ", text))
    text = re.sub(r"\n+", ". ", text)
    text = _SPACES.sub(" ", text).strip()
    text = re.sub(r"(\. ){2,}", ". ", text)
    sentences = re.split(r"(?<=[.!?])\s+", text)
    short = " ".join(sentences[:MAX_SPOKEN_SENTENCES])
    if len(short) > MAX_SPOKEN_CHARS:
        short = short[:MAX_SPOKEN_CHARS].rsplit(" ", 1)[0] + "."
    return short.strip()


def is_stop(text: str) -> bool:
    lowered = text.lower().strip(" .!?")
    return any(phrase in lowered for phrase in STOP_PHRASES)


# ---- the engine ----------------------------------------------------------------------------------

Runner = Callable[[str, dict[str, str], bytes | None], Awaitable[tuple[int, str, str]]]
Recorder = Callable[..., Recording]


async def run_powershell(
    script: str, env: dict[str, str], timeout: float, stdin: bytes | None = None
) -> tuple[int, str, str]:
    """Run a PowerShell script; text goes in through `env` (audio through stdin), never the command line."""
    proc = await asyncio.create_subprocess_exec(
        "powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script,
        env={**os.environ, **env},
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )  # fmt: skip
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin), timeout)
    except asyncio.CancelledError:
        proc.kill()  # cutting speech short (the user typed) or leaving voice mode
        raise
    except TimeoutError:
        proc.kill()
        raise VoiceError("the speech engine did not answer in time") from None
    return proc.returncode or 0, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


@dataclass
class Voice:
    runner: Runner | None = None
    recorder: Recorder | None = None  # records one sentence (voice_capture.record by default)
    transcriber: Callable[[bytes], str] | None = None  # audio -> text (Whisper by default)
    wake_transcriber: Callable[[bytes], str] | None = None  # the same, hinted with the wake phrase
    last: Recording | None = None  # what the microphone delivered on the last listen
    last_error: str = ""  # why Whisper was not used last time
    speaking: bool = False  # `/voice on`: answers are read aloud
    name: str = ""
    rate: int = 0
    _task: asyncio.Task | None = None
    _warm: asyncio.Task | None = None
    stop_event: threading.Event = field(default_factory=threading.Event)

    def __post_init__(self) -> None:
        self.name = self.name or os.environ.get("RAGNAROK_VOICE", "")  # "" = automatic
        with contextlib.suppress(ValueError):
            self.rate = max(-10, min(10, int(os.environ.get("RAGNAROK_VOICE_RATE", self.rate))))

    @property
    def available(self) -> bool:
        return (sys.platform == "win32" or self.runner is not None) and os.environ.get(
            "RAGNAROK_NO_VOICE"
        ) != "1"

    def _need(self) -> None:
        if not self.available:
            raise VoiceError(
                "voice is not available here (Windows only, and RAGNAROK_NO_VOICE is off)"
            )

    async def _run(
        self, script: str, env: dict[str, str], timeout: float, stdin: bytes | None = None
    ) -> tuple[int, str, str]:
        if self.runner is not None:
            return await self.runner(script, env, stdin)
        return await run_powershell(script, env, timeout, stdin)

    # ---- speaking ----------------------------------------------------------------------------

    async def say(self, text: str) -> None:
        """Read `text` aloud (cleaned and shortened) and wait until it has finished."""
        self._need()
        words = speakable(text)
        if not words:
            return
        code, _out, err = await self._run(
            SPEAK_SCRIPT,
            {"RAG_TTS_TEXT": words, "RAG_TTS_VOICE": self.name, "RAG_TTS_RATE": str(self.rate)},
            timeout=90,
        )
        if code != 0:
            raise VoiceError(f"could not speak: {err.strip()[:120] or 'speech engine failed'}")

    def say_in_background(self, text: str) -> None:
        """Start reading `text` without waiting; a newer call or `hush()` cuts it short."""
        self.hush()

        async def go() -> None:
            with contextlib.suppress(VoiceError, asyncio.CancelledError):
                await self.say(text)

        self._task = asyncio.create_task(go())

    def hush(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = None

    async def finish_speaking(self) -> None:
        if self._task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    # ---- listening ---------------------------------------------------------------------------

    async def listen(self, wait: float = LISTEN_WAIT_SECONDS) -> str:
        """Record one sentence from the microphone and return what was said ("" if nothing)."""
        self._need()
        await self.finish_speaking()  # never listen to our own voice
        record = self.recorder or voice_capture.record
        stop = self.stop_event  # set by cancel(): the recording ends at once
        try:
            rec = await asyncio.to_thread(record, wait, LISTEN_MAX_SECONDS, None, stop)
        except asyncio.CancelledError:
            stop.set()  # leaving voice mode: let the recording thread finish at once
            raise
        except MicError as exc:
            raise VoiceError(str(exc)) from exc
        self.last = rec
        if not rec.heard_speech:
            return ""
        if self.transcriber is not None or (self.runner is None and voice_whisper.enabled()):
            try:
                text = await asyncio.to_thread(
                    self.transcriber or voice_whisper.transcribe, rec.pcm
                )
                return text.strip()
            except voice_whisper.WhisperUnavailable as exc:
                if self.transcriber is not None:
                    raise VoiceError(str(exc)) from exc
                self.last_error = str(exc)  # fall back to the Windows recogniser below
        code, out, err = await self._run(RECOGNIZE_SCRIPT, {}, timeout=60, stdin=rec.pcm)
        if code == 3:
            raise VoiceError("Windows has no speech recogniser installed for this language")
        if code != 0:
            raise VoiceError(f"could not listen: {err.strip()[:120] or 'speech engine failed'}")
        confidence, _, text = out.strip().partition("\n")
        try:
            if float(confidence) < MIN_CONFIDENCE:
                return ""
        except ValueError:
            text = out.strip()
        return text.strip()

    @property
    def busy_speaking(self) -> bool:
        return self._task is not None and not self._task.done()

    def can_wake(self) -> bool:
        """The wake phrase needs a speech model that is good enough to hear a name."""
        return self.available and (
            self.wake_transcriber is not None or (self.runner is None and voice_whisper.enabled())
        )

    def wake_text(self, pcm: bytes) -> str:
        from ragnarok.wake import HINT

        if self.wake_transcriber is not None:
            return self.wake_transcriber(pcm)
        return voice_whisper.transcribe(pcm, prompt=HINT, model=voice_whisper.model_name("wake"))

    async def chime(self, kind: str = "wake") -> None:
        """A short sound so you know it heard you ("wake": two rising tones; "nothing": one low one)."""
        if self.runner is not None or sys.platform != "win32" or not self.available:
            return  # tests, other systems, voice switched off
        tones = ((660, 110), (880, 140)) if kind == "wake" else ((330, 220),)

        def play() -> None:
            import winsound

            for hz, ms in tones:
                winsound.Beep(hz, ms)

        with contextlib.suppress(Exception):
            await asyncio.to_thread(play)

    def cancel(self) -> None:
        """Stop listening now (safe to call from another thread, e.g. the cat's window)."""
        self.stop_event.set()

    def take_cancel(self) -> bool:
        """Was a stop requested since the last check? Clears the request."""
        asked = self.stop_event.is_set()
        self.stop_event.clear()
        return asked

    def prepare(self) -> None:
        """Start loading the speech model in the background, so the first listen is quick."""
        if self.transcriber is None and self.runner is None and voice_whisper.enabled():
            if self._warm is None or self._warm.done():
                self._warm = asyncio.create_task(self._warm_up())

    async def _warm_up(self) -> None:
        with contextlib.suppress(voice_whisper.WhisperUnavailable, OSError):
            await asyncio.to_thread(voice_whisper.warm_up)

    def why_nothing(self) -> str:
        """A sentence about why the last listen produced no text (for the screen)."""
        if self.last is None:
            return "I did not hear anything."
        if self.last.heard_speech:
            return (
                f"I heard {self.last.seconds}s of speech but could not make out the words. "
                "Speak a little slower and closer to the microphone."
            )
        return voice_capture.level_report(self.last)

    async def diagnose(self) -> str:
        """Record a few seconds and report the microphone, its level and what was understood."""
        self._need()
        lines = [
            f"microphones: {', '.join(f'{i}={n}' for i, n in enumerate(voice_capture.devices()))}"
        ]
        text = await self.listen(wait=8)
        lines.append(voice_capture.level_report(self.last) if self.last else self.why_nothing())
        lines.append(f'understood: "{text}"' if text else "understood: nothing")
        return "\n".join(lines)
