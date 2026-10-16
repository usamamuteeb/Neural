"""
Mood tags for Neural's animated avatar (frontend/avatar.js).

Both system prompts (agent.py for chat, tutor.py for Tutor mode) ask
Claude to open each reply with one short tag like ``[mood:happy]``. That
tag is how the avatar knows which face to make -- Claude picks the
expression itself, as part of the same turn, so it costs nothing extra
beyond a few tokens and needs no second model call or sentiment
classifier.

The tag must never reach anything the user reads or hears: it's
stripped here from every TextBlock before the text is shown, spoken by
TTS, saved to chat history, pushed as a notification, or stored as an
automation's last_result. Anything that looks like a tag but names an
unknown mood is still stripped (so a typo never gets read aloud), it just
doesn't change the face.
"""

import re
from typing import Optional

MOODS = {
    "neutral", "happy", "excited", "playful", "proud", "surprised",
    "curious", "worried", "sad", "embarrassed", "affectionate", "annoyed",
}

# Close-enough words the model might reach for instead of the exact list.
_ALIASES = {
    "joy": "happy", "joyful": "happy", "glad": "happy", "cheerful": "happy", "pleased": "happy",
    "thrilled": "excited", "hyped": "excited", "enthusiastic": "excited",
    "teasing": "playful", "cheeky": "playful", "mischievous": "playful", "amused": "playful",
    "smug": "proud", "confident": "proud", "satisfied": "proud",
    "shocked": "surprised", "amazed": "surprised", "astonished": "surprised",
    "thinking": "curious", "thoughtful": "curious", "intrigued": "curious", "interested": "curious",
    "concerned": "worried", "nervous": "worried", "apologetic": "worried", "sorry": "worried", "anxious": "worried",
    "unhappy": "sad", "disappointed": "sad",
    "shy": "embarrassed", "flustered": "embarrassed", "blushing": "embarrassed",
    "love": "affectionate", "loving": "affectionate", "warm": "affectionate", "caring": "affectionate", "sweet": "affectionate",
    "angry": "annoyed", "frustrated": "annoyed", "irritated": "annoyed", "grumpy": "annoyed",
    "calm": "neutral", "serious": "neutral", "focused": "neutral",
}

_TAG = re.compile(r"\[\s*mood\s*[:=]\s*([A-Za-z_-]{1,24})\s*\]\s*", re.IGNORECASE)


def normalize_mood(name: str) -> Optional[str]:
    name = (name or "").strip().lower()
    if name in MOODS:
        return name
    return _ALIASES.get(name)


def extract_mood(text: str) -> "tuple[str, Optional[str]]":
    """Returns (text with every mood tag removed, the LAST valid mood
    named in it or None)."""
    if not text or "[" not in text:
        return text, None
    found: list = []

    def _strip(m: "re.Match") -> str:
        mood = normalize_mood(m.group(1))
        if mood:
            found.append(mood)
        return ""

    return _TAG.sub(_strip, text), (found[-1] if found else None)
