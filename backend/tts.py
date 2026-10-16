"""
Text-to-speech, in priority order:

  1. edge-tts -- free neural voices via Microsoft Edge's "Read Aloud"
     backend. Unofficial, so Microsoft sometimes changes its token
     handshake and every request gets a 403 until the library catches up
     (October 2026 is one of those times). A circuit breaker handles
     that: after a failure edge-tts is skipped for EDGE_TTS_RETRY_MINUTES
     instead of being retried (and failing, and logging) on every
     sentence.
  2. ElevenLabs -- off by default; a paid tier if you ever turn it back on.
  3. Piper -- a free, local neural voice (`pip install piper-tts`). It
     runs fully offline and sounds far better than the Windows voice. The
     voice model downloads once (~60 MB) into Neural/voices/.
  4. Windows' built-in voice (System.Speech) in a separate PowerShell
     process, so it never freezes the server. pyttsx3 runs in a
     background thread as a final fallback.

Problems are reported ONCE when they start (a "tts_error" event with
fatal=True only when no voice worked at all), not once per sentence.
That stops the error and notification flood.
"""

import asyncio
import base64
import io
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import Optional, Tuple

import httpx

from . import config
from .events import bus

logger = logging.getLogger("neural.tts")

ELEVENLABS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"

_edge_down_until = 0.0
_piper_down_until = 0.0
_notified: dict = {}  # key -> last time we told the user about it
_NOTICE_EVERY = 30 * 60


def _notice(key: str, message: str, *, fatal: bool = False) -> None:
    """Log and publish a voice problem at most once per 30 minutes per kind."""
    now = time.time()
    if now - _notified.get(key, 0.0) < _NOTICE_EVERY:
        return
    _notified[key] = now
    (logger.error if fatal else logger.warning)("%s", message)
    bus.publish({"type": "tts_error", "message": message, "fatal": fatal, "key": key})


def status() -> dict:
    now = time.time()
    return {
        "edge": "off" if not config.EDGE_TTS_ENABLED else ("paused" if now < _edge_down_until else "ok"),
        "edge_retry_in_s": max(0, round(_edge_down_until - now)),
        "piper": "off" if not config.PIPER_ENABLED else (
            "not installed" if not _piper_importable() else ("paused" if now < _piper_down_until else "ok")),
        "piper_voice": config.PIPER_VOICE,
    }


async def synthesize(text: str) -> Optional[Tuple[bytes, str]]:
    """Return (audio_bytes, mime) from the first tier that works, or None."""
    global _edge_down_until
    text = (text or "").strip()
    if not text:
        return None

    if config.EDGE_TTS_ENABLED and time.time() >= _edge_down_until:
        try:
            audio = await asyncio.wait_for(_synthesize_edge(text), timeout=15)
            if _edge_down_until:
                logger.info("edge-tts is working again.")
                _edge_down_until = 0.0
            return audio, "audio/mpeg"
        except Exception as e:  # noqa: BLE001 -- unofficial API; fall back on any failure
            if time.time() >= _edge_down_until:  # first failure of this outage
                _edge_down_until = time.time() + config.EDGE_TTS_RETRY_MINUTES * 60
                short = str(e).split(",")[0][:120] or type(e).__name__
                _notice("edge", f"Online voice (edge-tts) is down ({short}). Using the local voice; "
                                f"retrying in {config.EDGE_TTS_RETRY_MINUTES} min.")

    if config.ELEVENLABS_TTS_ENABLED and config.ELEVENLABS_API_KEY and config.ELEVENLABS_VOICE_ID:
        try:
            return await _synthesize_elevenlabs(text), "audio/mpeg"
        except httpx.HTTPStatusError as e:
            _notice("elevenlabs", f"ElevenLabs {e.response.status_code}: {e.response.text[:150]}")
        except Exception as e:  # noqa: BLE001
            _notice("elevenlabs", f"ElevenLabs error: {e}")

    piper = await _synthesize_piper(text)
    if piper:
        return piper, "audio/wav"

    local = await _synthesize_windows(text)
    if local:
        return local, "audio/wav"

    _notice("all", "Neural couldn't produce any voice (all voice engines failed). Replies are text-only "
                   "until this is fixed. See the backend log.", fatal=True)
    return None


# ---------------------------------------------------------------- edge-tts

async def _synthesize_edge(text: str) -> bytes:
    import edge_tts

    communicate = edge_tts.Communicate(text, config.EDGE_TTS_VOICE)
    chunks = bytearray()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            chunks.extend(chunk["data"])
    if not chunks:
        raise RuntimeError("edge-tts returned no audio data")
    return bytes(chunks)


# -------------------------------------------------------------- ElevenLabs

async def _synthesize_elevenlabs(text: str) -> bytes:
    url = ELEVENLABS_URL.format(voice_id=config.ELEVENLABS_VOICE_ID)
    headers = {"xi-api-key": config.ELEVENLABS_API_KEY, "Content-Type": "application/json"}
    payload = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        return resp.content


# ------------------------------------------------------------------- Piper

_piper_voice = None
_piper_lock = threading.Lock()
_piper_import_ok: Optional[bool] = None


def _piper_importable() -> bool:
    global _piper_import_ok
    if _piper_import_ok is None:
        try:
            import piper  # noqa: F401
            _piper_import_ok = True
        except Exception:  # noqa: BLE001
            _piper_import_ok = False
    return _piper_import_ok


def _voices_dir() -> Path:
    d = Path(config.PIPER_VOICES_DIR)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _piper_urls(voice: str) -> Tuple[str, str]:
    # en_US-amy-medium -> en/en_US/amy/medium/en_US-amy-medium.onnx
    lang, name, quality = voice.split("-", 2)
    base = f"https://huggingface.co/rhasspy/piper-voices/resolve/main/{lang.split('_')[0]}/{lang}/{name}/{quality}/{voice}"
    return base + ".onnx", base + ".onnx.json"


def _download(url: str, dest: Path) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    with httpx.stream("GET", url, follow_redirects=True, timeout=120) as resp:
        resp.raise_for_status()
        with open(tmp, "wb") as f:
            for block in resp.iter_bytes(1 << 16):
                f.write(block)
    os.replace(tmp, dest)


def _load_piper():
    global _piper_voice
    if _piper_voice is not None:
        return _piper_voice
    from piper import PiperVoice

    voice = config.PIPER_VOICE
    model = _voices_dir() / f"{voice}.onnx"
    cfg = _voices_dir() / f"{voice}.onnx.json"
    if not model.exists() or not cfg.exists():
        logger.info("Downloading Piper voice %s (one time)...", voice)
        model_url, cfg_url = _piper_urls(voice)
        _download(cfg_url, cfg)
        _download(model_url, model)
    _piper_voice = PiperVoice.load(str(model), config_path=str(cfg))
    logger.info("Piper voice %s loaded.", voice)
    return _piper_voice


def _piper_wav(text: str) -> bytes:
    with _piper_lock:  # one synth at a time; Piper is fast, and this keeps memory flat
        voice = _load_piper()
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav:
            if hasattr(voice, "synthesize_wav"):  # piper-tts >= 1.3
                voice.synthesize_wav(text, wav)
            else:  # piper-tts 1.2
                voice.synthesize(text, wav)
        data = buf.getvalue()
    if len(data) <= 44:
        raise RuntimeError("Piper produced no audio")
    return data


async def _synthesize_piper(text: str) -> Optional[bytes]:
    global _piper_down_until
    if not config.PIPER_ENABLED or time.time() < _piper_down_until or not _piper_importable():
        return None
    try:
        return await asyncio.to_thread(_piper_wav, text)
    except Exception as e:  # noqa: BLE001
        _piper_down_until = time.time() + 10 * 60
        _notice("piper", f"Local neural voice (Piper) failed: {str(e)[:150]}. Using the Windows voice.")
        return None


def warm() -> None:
    """Load (and if needed download) the Piper voice at startup so the
    first reply isn't delayed. Safe to call when Piper isn't installed."""
    if config.PIPER_ENABLED and _piper_importable():
        try:
            _load_piper()
        except Exception as e:  # noqa: BLE001
            logger.warning("Piper warm-up failed: %s", e)


# ---------------------------------------------------------- Windows voice

_SAPI_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$want = $env:NEURAL_SAPI_VOICE
$v = $null
if ($want) { $v = $s.GetInstalledVoices() | Where-Object { $_.Enabled -and $_.VoiceInfo.Name -like "*$want*" } | Select-Object -First 1 }
if (-not $v) { $v = $s.GetInstalledVoices() | Where-Object { $_.Enabled -and $_.VoiceInfo.Gender -eq 'Female' } | Select-Object -First 1 }
if ($v) { $s.SelectVoice($v.VoiceInfo.Name) }
$s.Volume = 100
$s.Rate = 1
$s.SetOutputToWaveFile($env:NEURAL_SAPI_OUT)
$s.Speak([Console]::In.ReadToEnd())
$s.Dispose()
"""
# Passed as -EncodedCommand (base64 UTF-16LE) so no quoting can break it.
_SAPI_ENCODED = base64.b64encode(_SAPI_SCRIPT.encode("utf-16-le")).decode("ascii")


def _sapi_wav(text: str) -> bytes:
    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        env = dict(os.environ, NEURAL_SAPI_OUT=path, NEURAL_SAPI_VOICE=config.WINDOWS_TTS_VOICE)
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-EncodedCommand", _SAPI_ENCODED],
            input=text.encode("utf-8"), capture_output=True, timeout=30, env=env, creationflags=flags,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.decode("utf-8", "replace").strip()[:200] or "PowerShell voice failed")
        with open(path, "rb") as f:
            data = f.read()
        if len(data) <= 44:
            raise RuntimeError("Windows voice produced no audio")
        return data
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def _pyttsx3_wav(text: str) -> bytes:
    try:
        import pythoncom  # COM must be initialised in this worker thread
        pythoncom.CoInitialize()
    except Exception:  # noqa: BLE001
        pass
    import pyttsx3

    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        engine = pyttsx3.init()
        engine.setProperty("volume", 1.0)
        engine.save_to_file(text, path)
        engine.runAndWait()
        with open(path, "rb") as f:
            return f.read()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


async def _synthesize_windows(text: str) -> Optional[bytes]:
    if sys.platform == "win32":
        try:
            return await asyncio.to_thread(_sapi_wav, text)
        except Exception as e:  # noqa: BLE001
            _notice("sapi", f"Windows voice failed: {str(e)[:150]}. Trying pyttsx3.")
    try:
        return await asyncio.to_thread(_pyttsx3_wav, text)
    except Exception as e:  # noqa: BLE001
        _notice("pyttsx3", f"pyttsx3 voice failed: {str(e)[:150]}")
        return None
