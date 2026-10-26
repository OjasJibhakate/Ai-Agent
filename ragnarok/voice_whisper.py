"""Speech to text with Whisper (faster-whisper), running on this computer's CPU.

Why: Windows' built-in recogniser scored 2% on a clear sentence from a headset and 39% on clean
synthetic speech, so it cannot take free-form dictation. Whisper's `base.en` model understands
normal English speech and runs offline.

- The model (about 145 MB) is downloaded once, from the Hugging Face model hub, into
  `%LOCALAPPDATA%\\Ragnarok\\models`. That is a download of model weights, like `ollama pull`; no
  audio or text is ever sent anywhere. Telemetry is switched off, and after the first download the
  model is loaded with `local_files_only`, so listening works with no network at all.
- It runs on the CPU (int8), not the GPU: the 6 GB card is almost full with the chat model, so a
  second GPU model would force Ollama to reload. A three-second sentence takes about a second.
- The model stays in memory after the first use, so only the first listen is slow.

`RAGNAROK_STT=windows` switches back to the Windows recogniser; `RAGNAROK_WHISPER_MODEL` picks
another size (for example `tiny.en` for speed, `small.en` for accuracy).
"""

from __future__ import annotations

import importlib.util
import os
import threading
from pathlib import Path

WAKE_MODEL = "base.en"  # fast: looks at every short burst of speech for "hey Ragnarok"
COMMAND_MODEL = "small.en"  # more accurate (and better with accents): hears your actual request
# Whisper has no accent switch. What helps an Indian-English speaker: the larger model above, and
# a prompt that says what to expect (it makes the model expect this accent and these words).
INDIAN_PROMPT = (
    "Indian English speaker. Words may include Ragnarok, Steam, Gmail, WhatsApp, PowerPoint, "
    "Excel, Downloads, Desktop, Documents."
)
# What Whisper tends to "hear" in noise or silence. As a whole answer, none of these is a request.
INVENTED = {
    "you",
    "thank you",
    "thanks",
    "bye",
    "thanks for watching",
    "thank you for watching",
    "okay",
    "so",
}
MAX_NO_SPEECH = 0.6  # Whisper's own "this was not speech" probability, above which we drop it
MIN_LOGPROB = -1.2  # its average confidence (log scale); lower means it was guessing

_models: dict = {}
_lock = threading.Lock()


class WhisperUnavailable(Exception):
    """Whisper is not installed or its model could not be loaded."""


def enabled() -> bool:
    return os.environ.get("RAGNAROK_STT", "whisper") != "windows" and (
        importlib.util.find_spec("faster_whisper") is not None
    )


def models_dir() -> Path:
    from ragnarok.vault.keys import runtime_dir

    return Path(os.environ.get("RAGNAROK_HOME") or runtime_dir()) / "models"


def model_name(kind: str = "command") -> str:
    """The model for a job: `wake` (fast) or `command` (accurate); an env var overrides both."""
    chosen = os.environ.get("RAGNAROK_WHISPER_MODEL")
    return chosen or (WAKE_MODEL if kind == "wake" else COMMAND_MODEL)


def accent_prompt() -> str:
    """Indian English by default; `RAGNAROK_ACCENT=us` (or anything else) turns the hint off."""
    return INDIAN_PROMPT if os.environ.get("RAGNAROK_ACCENT", "indian").lower() == "indian" else ""


def _load(name: str | None = None):
    """The model, loaded once. Offline first; a one-time download only if it is missing."""
    name = name or model_name()
    with _lock:
        if name in _models:
            return _models[name]
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise WhisperUnavailable("faster-whisper is not installed (uv sync)") from exc
        root = models_dir()
        root.mkdir(parents=True, exist_ok=True)
        options = {"device": "cpu", "compute_type": "int8", "download_root": str(root)}
        try:
            model = WhisperModel(name, local_files_only=True, **options)
        except Exception:  # not downloaded yet: fetch the weights once
            try:
                model = WhisperModel(name, **options)
            except Exception as exc:
                raise WhisperUnavailable(
                    f"the speech model {name} is not downloaded and could not be fetched "
                    f"({type(exc).__name__}); connect once, or set RAGNAROK_STT=windows"
                ) from exc
        _models[name] = model
        return model


def transcribe(pcm: bytes, prompt: str | None = None, model: str | None = None) -> str:
    """Text of 16 kHz mono 16-bit audio, or "" if it was not speech. Blocks: run in a thread."""
    import numpy as np

    audio = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype=np.int16).astype(np.float32) / 32768.0
    if audio.size < 1600:  # under 0.1 s
        return ""
    hint = " ".join(p for p in (accent_prompt(), prompt) if p) or None
    segments, _info = _load(model).transcribe(
        audio,
        language="en",
        beam_size=1,
        vad_filter=False,  # the recorder already cut the sentence out
        condition_on_previous_text=False,
        temperature=0.0,
        initial_prompt=hint,  # the accent, and e.g. the wake phrase, so a name is spelled right
    )
    kept = [
        s.text.strip()
        for s in segments
        if s.no_speech_prob <= MAX_NO_SPEECH and s.avg_logprob >= MIN_LOGPROB and s.text.strip()
    ]
    text = " ".join(kept).strip()
    if text.lower().strip(" .!?,") in INVENTED or (hint and text.strip() == hint.strip()):
        return ""  # noise that Whisper turned into a stock phrase, or into the hint itself
    return text


def warm_up(*kinds: str) -> None:
    """Load the models now (seconds, plus a one-time download) so the first listen is fast."""
    for kind in kinds or ("command", "wake"):
        _load(model_name(kind))
