"""
Persists the CURRENT chat/tutor conversation's session_id to disk so a
backend restart -- planned, or forced by main.py's turn-idle-timeout
recovery -- resumes the SAME underlying `claude` CLI conversation instead
of starting from zero, PROVIDED it isn't too old to be worth resuming
(see get_session_id's age cap below).

Before this existed at all, a restart wiped not just the visible chat log
(see events.py's EventBus, deliberately non-persistent -- "a server
restart clears it, which is fine for a live feed") but the agent's own
actual memory of what was just discussed: a fresh ClaudeSDKClient with no
`resume` is a brand new conversation as far as the model is concerned,
regardless of what the UI still shows on screen. The Claude Agent SDK's
`resume` option (ClaudeAgentOptions.resume: str | None -- "Session ID to
resume. Loads the conversation history from the specified session.")
reads the CLI's own on-disk transcript, so this only needs to remember
which session_id was last known-good; the SDK does the rest.

Real live cost problem found AFTER that fix shipped: "resume" doesn't
mean the server remembers your history for free -- the `claude` CLI
reconstructs and resends the ENTIRE prior conversation as input context
on every single turn. Resuming the same session_id forever, with no
ceiling, meant that conversation only ever grew -- and since prompt
caching's default TTL is 5 minutes, a personal assistant used in bursts
throughout the day (not continuously) almost never gets a cache hit on
that replay. Every turn, however small, paid full price to reprocess an
ever-larger transcript from scratch, compounding daily since nothing
ever reset it -- a user saw ~17% of a 5-hour usage window burned by two
small memory-search requests, entirely explained by this.

Fix: get_session_id now also stores WHEN a session_id was saved, and
refuses to hand it back for resuming once it's older than
config.SESSION_RESUME_MAX_AGE_SECONDS -- main.py's _new_client_resuming
then just starts a fresh session instead, the same as if nothing had
ever been saved. This keeps the original fix (don't lose context on a
same-day crash/restart) while putting a real ceiling on how large the
resumed conversation -- and therefore the cost of every future turn --
can grow. Anything that should survive past that cap belongs in memory
(memory_write/memory_read), same as before this existed.

Only the last GOOD session_id is kept, one per mode -- not a rolling
history of several past sessions. It's written every time a turn
actually completes (main.py's _run_turn does this on every ResultMessage,
which always carries the current session_id), so a turn that never
finished (the one that triggered a timeout-recovery, say) can never
overwrite a working checkpoint with a broken one.

Deliberately NOT used for automations' one-off `isolated=True` clients
(see agent.py) -- those are meant to run as fresh, disconnected sessions
every time, so this only applies to the two long-lived shared clients
(chat, tutor).
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import config

STATE_PATH = Path(__file__).resolve().parent.parent / "session_state.json"


def _load() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def get_session_id(mode: str) -> Optional[str]:
    """The saved session_id for `mode`, or None if there isn't one, or if
    the one that's there is too old to be worth resuming (see this
    module's docstring for why an age cap exists at all). A refusal here
    isn't an error -- main.py's _new_client_resuming just starts a fresh
    session instead, exactly as if nothing had ever been saved."""
    entry = _load().get(mode)
    if not entry:
        return None
    # Backward-compat: session_state.json used to store a bare session_id
    # string per mode, with no timestamp at all (before the age cap
    # existed). Treat that shape as too old to trust rather than
    # resuming it blind with no way to know its real age -- a one-time
    # reset the first time this runs; every save from here on includes
    # saved_at.
    if isinstance(entry, str):
        return None
    session_id = entry.get("session_id")
    saved_at = entry.get("saved_at")
    if not session_id or not saved_at:
        return None
    try:
        saved_dt = datetime.fromisoformat(saved_at)
    except ValueError:
        return None
    age_seconds = (datetime.now(timezone.utc) - saved_dt).total_seconds()
    if age_seconds > config.SESSION_RESUME_MAX_AGE_SECONDS:
        return None
    return session_id


def save_session_id(mode: str, session_id: str) -> None:
    if not session_id:
        return
    # No dedup-and-skip on an unchanged session_id here anymore (there
    # used to be one) -- saved_at needs to advance on every completed
    # turn, since it's what get_session_id's age check is actually
    # measuring: time since last real activity, not just "has the
    # session_id changed."
    state = _load()
    state[mode] = {
        "session_id": session_id,
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError:
        pass


def clear_session_id(mode: str) -> None:
    state = _load()
    if mode in state:
        del state[mode]
        try:
            STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")
        except OSError:
            pass
