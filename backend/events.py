"""
Lightweight in-process pub/sub so tool activity (bash commands, file edits,
browser/gmail/calendar calls, memory writes, automation runs) can stream to
every open browser tab -- not just the one connected to whichever /ws chat
session triggered it. Deliberately simple: an asyncio.Queue per subscriber,
a short in-memory history for tabs that connect late, no persistence, no
external broker. A server restart clears it, which is fine for a live feed.
"""

import asyncio
import time
from typing import Any


class EventBus:
    def __init__(self, max_history: int = 200) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._history: list[dict[str, Any]] = []
        self._max_history = max_history

    def subscribe(self) -> "asyncio.Queue[dict[str, Any]]":
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: "asyncio.Queue[dict[str, Any]]") -> None:
        self._subscribers.discard(q)

    def history(self) -> list[dict[str, Any]]:
        return list(self._history)

    def publish(self, event: dict[str, Any]) -> None:
        full = {"ts": time.time(), **event}
        self._history.append(full)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history :]
        for q in list(self._subscribers):
            q.put_nowait(full)
        # Persist a compact, redacted audit record independently of the live
        # feed. Import lazily to keep the event bus usable during startup even
        # if diagnostics storage is temporarily unavailable.
        try:
            from .observability import record_event

            record_event(full)
        except Exception:
            pass


bus = EventBus()
