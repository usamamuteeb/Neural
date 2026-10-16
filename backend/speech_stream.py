"""
Turns the Agent SDK's partial-message stream into speakable text as it's
generated, so Neural starts talking on the FIRST sentence instead of after
the whole reply (or the whole text block) has been written.

With ClaudeAgentOptions(include_partial_messages=True) the SDK yields
StreamEvent objects carrying raw Messages-API stream events
(message_start, content_block_start, content_block_delta with text_delta,
content_block_stop, ...) and THEN still yields the complete
AssistantMessage for the same message. TextStreamer:

  - returns each text delta as soon as it's safe to show/speak it,
  - holds back only the very start of each text block until it knows
    whether it begins with a [mood:...] tag (see mood.py), so the tag is
    never shown or spoken but its mood still reaches the avatar right away,
  - remembers which message ids it already streamed, so main.py can drop
    the duplicate text from the complete AssistantMessage that follows
    (tool_use blocks in that message are kept -- they're never streamed
    as text).

If partial messages are ever unavailable, nothing is streamed and every
AssistantMessage is simply handled the old way -- no duplicates, no gaps.

Pure Python, no SDK import: unit-testable on its own.
"""

from typing import Optional

from .mood import extract_mood

_MAX_TAG_HEAD = 48  # a [mood:...] tag is always shorter than this


class TextStreamer:
    def __init__(self) -> None:
        self._msg_id: Optional[str] = None
        self._streamed_ids: set = set()
        self._any_since_assistant = False
        self._in_text = False
        self._head: Optional[str] = None  # buffered start of the current text block

    def feed(self, event: dict) -> "tuple[str, Optional[str]]":
        """One raw stream event -> (text safe to show/speak now, mood or None)."""
        etype = event.get("type")
        if etype == "message_start":
            self._msg_id = (event.get("message") or {}).get("id")
            return "", None
        if etype == "content_block_start":
            block = event.get("content_block") or {}
            self._in_text = block.get("type") == "text"
            self._head = "" if self._in_text else None
            initial = block.get("text") if self._in_text else None
            if initial:
                return self._absorb(initial)
            return "", None
        if etype == "content_block_delta":
            delta = event.get("delta") or {}
            if not self._in_text or delta.get("type") != "text_delta":
                return "", None
            text = delta.get("text") or ""
            return self._absorb(text) if text else ("", None)
        if etype == "content_block_stop":
            result = ("", None)
            if self._in_text and self._head is not None:
                result = self._flush_head()
            self._in_text = False
            self._head = None
            return result
        return "", None

    def _absorb(self, text: str) -> "tuple[str, Optional[str]]":
        self._any_since_assistant = True
        if self._msg_id:
            self._streamed_ids.add(self._msg_id)
        if self._head is None:
            return text, None
        self._head += text
        stripped = self._head.lstrip()
        if not stripped:
            return "", None
        if stripped.startswith("[") and "]" not in stripped and len(stripped) < _MAX_TAG_HEAD:
            return "", None  # might still be a mood tag -- wait for the "]"
        return self._flush_head()

    def _flush_head(self) -> "tuple[str, Optional[str]]":
        head = self._head or ""
        self._head = None
        clean, mood = extract_mood(head)
        if mood:
            clean = clean.lstrip()
        return clean, mood

    def text_already_streamed(self, message_id: Optional[str]) -> bool:
        """Call once per complete AssistantMessage: True if its text was
        already delivered through the stream (so it must not be sent again)."""
        if message_id:
            covered = message_id in self._streamed_ids
        else:
            covered = self._any_since_assistant
        self._any_since_assistant = False
        return covered
