"""
Local speech-to-text for Neural Desktop's voice input (and the shared
Whisper loader media_analysis.py uses for caption-less videos).

Why this exists: the desktop app runs in Electron, and Electron has no
working Web Speech API (Chromium's speech service needs Google's private
API keys, so `webkitSpeechRecognition` just errors with "network"). So the
desktop records your voice itself and posts a short WAV clip here, and
Whisper transcribes it on this PC -- free, offline after a one-time model
download, no API key, no subscription usage.

Needs `pip install -U faster-whisper` (openai-whisper works as a fallback).
Models are cached per name, so if STT_WHISPER_MODEL and MEDIA_WHISPER_MODEL
are the same (both "base" by default) only one copy is ever loaded.
"""

import io
import logging
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import Optional

from . import config

logger = logging.getLogger("neural.stt")

MAX_SECONDS = 60          # longest clip /api/transcribe accepts
MAX_BYTES = 12 * 1024 * 1024

# Whisper "hears" these in silence/noise. Dropped only when they're the
# whole transcript of a short clip -- see is_probably_noise().
_HALLUCINATIONS = {
    "you", "thank you", "thanks", "thank you very much", "thanks for watching",
    "thank you for watching", "bye", "okay", "ok", "so", "uh", "um", "hmm",
    "subtitles by the amara.org community", "please subscribe", ".", "",
}

_lock = threading.Lock()
_models: dict = {}  # name -> (kind, model)


class STTUnavailable(Exception):
    """No Whisper engine installed -- message carries the install command."""


def _pip_hint(packages: str) -> str:
    return f'"{sys.executable}" -m pip install -U {packages}'


def available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        try:
            import whisper  # noqa: F401
            return True
        except ImportError:
            return False


def get_model(name: str):
    """(kind, model) for a Whisper model name, loading it once."""
    with _lock:
        if name in _models:
            return _models[name]
        try:
            from faster_whisper import WhisperModel

            entry = ("faster-whisper", WhisperModel(name, device="cpu", compute_type="int8"))
        except ImportError:
            try:
                import whisper  # openai-whisper

                entry = ("openai-whisper", whisper.load_model(name))
            except ImportError:
                raise STTUnavailable(
                    "Local speech recognition isn't installed yet. Install it (free, offline) with: "
                    + _pip_hint("faster-whisper")
                    + " -- then restart Neural. (If that fails on this Python version: "
                    + _pip_hint("openai-whisper") + ")"
                ) from None
        _models[name] = entry
        return entry


def warm() -> bool:
    """Load the voice model ahead of the first clip (it takes a few seconds)."""
    try:
        get_model(config.STT_WHISPER_MODEL)
        return True
    except STTUnavailable:
        return False
    except Exception as exc:  # noqa: BLE001
        logger.error("Couldn't load the speech model: %s", exc)
        return False


def wav_to_float32(data: bytes):
    """16-bit PCM WAV bytes -> (mono float32 numpy array at 16 kHz, seconds)."""
    import numpy as np

    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            channels, width, rate, frames = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            if width != 2:
                raise ValueError("expected 16-bit PCM WAV")
            if frames > rate * MAX_SECONDS:
                raise ValueError(f"clip longer than {MAX_SECONDS}s")
            raw = w.readframes(frames)
    except (wave.Error, EOFError) as exc:
        raise ValueError(f"not a valid WAV file ({exc})") from None
    audio = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    if rate != 16000 and len(audio):
        # Linear resample -- plenty for speech; the desktop already sends 16 kHz.
        n = int(round(len(audio) * 16000 / rate))
        audio = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype(np.float32)
    return audio, len(audio) / 16000.0


def is_probably_noise(text: str, seconds: float) -> bool:
    norm = "".join(ch for ch in text.lower() if ch.isalnum() or ch == " ").strip()
    if not any(ch.isalpha() for ch in norm):
        return True
    return norm in _HALLUCINATIONS and seconds < 2.5


def transcribe_audio(data: bytes, mime: str = "audio/wav", language: Optional[str] = None) -> dict:
    """Blocking. Returns {"text", "language", "seconds", "ms", "noise"}."""
    if not data:
        raise ValueError("empty audio")
    if len(data) > MAX_BYTES:
        raise ValueError("audio too large")
    started = time.monotonic()
    kind, model = get_model(config.STT_WHISPER_MODEL)
    language = (language if language is not None else config.STT_LANGUAGE) or None

    tmp_path: Optional[Path] = None
    seconds = 0.0
    if "wav" in (mime or "").lower() or data[:4] == b"RIFF":
        source, seconds = wav_to_float32(data)
    else:
        # Anything else (webm/ogg from a recorder) goes through a temp file
        # so Whisper's own decoder (PyAV / ffmpeg) can read it.
        suffix = ".webm" if "webm" in mime else ".ogg" if "ogg" in mime else ".bin"
        fd = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        fd.write(data)
        fd.close()
        tmp_path = Path(fd.name)
        source = str(tmp_path)
    try:
        if kind == "faster-whisper":
            segs, info = model.transcribe(
                source, language=language, beam_size=1, vad_filter=False,
                condition_on_previous_text=False, initial_prompt=config.STT_INITIAL_PROMPT or None,
            )
            parts = [s.text.strip() for s in segs if s.text.strip()]
            detected = getattr(info, "language", language or "?")
            if not seconds:
                seconds = float(getattr(info, "duration", 0.0) or 0.0)
        else:
            result = model.transcribe(source, language=language, fp16=False,
                                      initial_prompt=config.STT_INITIAL_PROMPT or None)
            parts = [result.get("text", "").strip()]
            detected = result.get("language", language or "?")
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink()
            except OSError:
                pass
    text = " ".join(p for p in parts if p).strip()
    return {
        "text": text,
        "language": detected,
        "seconds": round(seconds, 2),
        "ms": int((time.monotonic() - started) * 1000),
        "noise": is_probably_noise(text, seconds),
    }
