"""
How much of a reply gets read aloud.

The whole reply always appears on screen, but only the opening (by
default the first 2 sentences, about 300 characters) is spoken. That
keeps voice conversational: Neural states the main point and then stops
talking, so you can reply, instead of it reading a long answer top to
bottom. When a meaningful part of the reply was left unspoken, one short
closing line ("The rest is on screen.") says so.
"""

import re

from . import config
from .mood import extract_mood

_SENTENCE_END = re.compile(r"[.!?…][\"')\]]*$")


class SpeechBudget:
    def __init__(self, max_sentences: int = None, max_chars: int = None):
        self.max_sentences = max(1, max_sentences or config.SPEECH_MAX_SENTENCES)
        self.max_chars = max(60, max_chars or config.SPEECH_MAX_CHARS)
        self.sentences = 0
        self.chars = 0
        self.full = False
        self.dropped_chars = 0

    def take(self, chunk: str) -> str:
        """Return the part of `chunk` to speak ("" means nothing). Chunks
        are sentences, or long clauses split at a comma."""
        text = extract_mood(chunk or "")[0].strip()
        if not text:
            return ""
        if self.full:
            self.dropped_chars += len(text)
            return ""
        if self.chars and self.chars + len(text) > self.max_chars:
            self.full = True
            self.dropped_chars += len(text)
            return ""
        if len(text) > self.max_chars:  # one huge opening sentence: cut at a word boundary
            cut = text[: self.max_chars].rsplit(" ", 1)[0].rstrip(",;:- ")
            self.dropped_chars += len(text) - len(cut)
            text = cut + "."
            self.full = True
        self.chars += len(text)
        if _SENTENCE_END.search(text):
            self.sentences += 1
            if self.sentences >= self.max_sentences:
                self.full = True
        return text

    def overflow_note(self) -> str:
        """The closing line to speak, or "" when little or nothing was cut."""
        if self.dropped_chars >= 40 and config.SPEECH_OVERFLOW_NOTE:
            return config.SPEECH_OVERFLOW_NOTE
        return ""
