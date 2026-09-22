"""
Text-to-speech, in priority order:

  1. edge-tts -- free, neural-quality voices via Microsoft Edge's "Read
     Aloud" backend (see EDGE_TTS_ENABLED/EDGE_TTS_VOICE in config.py).
     The primary voice now: no cost, no signup, close to ElevenLabs
     quality. It's an unofficial/reverse-engineered API though, so it
     can intermittently break for a few days when Microsoft changes its
     auth handshake -- that's exactly what the tiers below are for.
  2. ElevenLabs -- off by default (past billing issues); can be
     re-enabled as a paid middle tier if you want it back.
  3. A local engine (pyttsx3) -- the true last resort: no network, no
     external dependency, always works, but sounds robotic.

Only ever called when the frontend sent voice_mode: true for that turn
-- see main.py -- so plain text chat never spends any TTS quota at all.

Failures are NOT swallowed silently: they're logged AND published to the
live activity feed (a "tts_error" event) so a bad key/voice id/quota/
network issue shows up in the UI immediately instead of just quietly
falling back and leaving you wondering why a voice "isn't working".
"""

import logging
from typing import Optional, Tuple

import httpx

from . import config
from .events import bus

logger = logging.getLogger("neural.tts")

ELEVENLABS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"


async def synthesize(text: str) -> Optional[Tuple[bytes, str]]:
    """Try edge-tts, then ElevenLabs, then a local engine, or give up quietly.

    Returns (audio_bytes, mime_type) so the frontend builds the right kind
    of Blob -- edge-tts and ElevenLabs both return mp3, the local fallback
    returns wav, and mislabeling one as the other can make a browser
    refuse to play it at all, which looks exactly like "voice isn't
    working".
    """
    if config.EDGE_TTS_ENABLED:
        try:
            audio = await _synthesize_edge(text)
            return audio, "audio/mpeg"
        except Exception as e:  # noqa: BLE001 -- unofficial API, fall back on any failure but be visible
            logger.error("edge-tts failed: %s", e)
            bus.publish({"type": "tts_error", "message": f"edge-tts error: {e} -- falling back."})

    if config.ELEVENLABS_TTS_ENABLED:
        if config.ELEVENLABS_API_KEY and config.ELEVENLABS_VOICE_ID:
            try:
                audio = await _synthesize_elevenlabs(text)
                return audio, "audio/mpeg"
            except httpx.HTTPStatusError as e:
                detail = e.response.text[:300]
                logger.error("ElevenLabs TTS failed: %s %s", e.response.status_code, detail)
                bus.publish(
                    {
                        "type": "tts_error",
                        "message": f"ElevenLabs {e.response.status_code}: {detail}",
                    }
                )
            except Exception as e:  # noqa: BLE001 -- any failure should still fall back, but be visible
                logger.error("ElevenLabs TTS failed: %s", e)
                bus.publish({"type": "tts_error", "message": f"ElevenLabs error: {e}"})
        else:
            bus.publish(
                {
                    "type": "tts_error",
                    "message": "ElevenLabs not configured (missing API key or voice id) -- using local fallback voice.",
                }
            )
    # else: ElevenLabs is turned off (config.ELEVENLABS_TTS_ENABLED=false) --
    # skip it entirely with no attempt and no activity-feed noise, straight
    # to the local voice. See config.py for how to turn it back on.
    local = _synthesize_local(text)
    return (local, "audio/wav") if local else None


async def _synthesize_edge(text: str) -> bytes:
    """edge-tts streams audio in chunks over a websocket to Microsoft's
    backend; concatenate the audio-type chunks into one mp3. Raises on
    any failure (network, or the library's auth handshake breaking after
    a Microsoft-side change) -- the caller in synthesize() handles that
    by falling through to the next tier."""
    import edge_tts

    communicate = edge_tts.Communicate(text, config.EDGE_TTS_VOICE)
    chunks = bytearray()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            chunks.extend(chunk["data"])
    if not chunks:
        raise RuntimeError("edge-tts returned no audio data")
    return bytes(chunks)


async def _synthesize_elevenlabs(text: str) -> bytes:
    url = ELEVENLABS_URL.format(voice_id=config.ELEVENLABS_VOICE_ID)
    headers = {
        "xi-api-key": config.ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
    }
    payload = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        return resp.content


def _synthesize_local(text: str) -> Optional[bytes]:
    """
    Offline backup using the system's SAPI voices via pyttsx3 -- no API
    key, no network, no ElevenLabs quota spent. It's a plain system
    voice, not the chosen ElevenLabs one; it exists so Neural can still
    talk when ElevenLabs is down, rate-limited, or you want a free run.
    """
    try:
        import tempfile

        import pyttsx3

        engine = pyttsx3.init()
        # Max out volume explicitly -- some SAPI voices/devices default to
        # noticeably less than full output, which read as "the speaker
        # sound seems very low" even though the audio itself plays fine.
        engine.setProperty("volume", 1.0)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            path = f.name
        engine.save_to_file(text, path)
        engine.runAndWait()
        with open(path, "rb") as f:
            return f.read()
    except Exception as e:  # noqa: BLE001
        logger.error("Local TTS fallback also failed: %s", e)
        bus.publish({"type": "tts_error", "message": f"Local voice fallback also failed: {e}"})
        return None
