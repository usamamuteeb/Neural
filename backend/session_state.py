"""
Persists the CURRENT chat/tutor conversation's session_id to disk so a
backend restart -- planned, or forced by main.py's turn-idle-timeout
recovery -- resumes the SAME underlying `claude` CLI conversation instead
of starting from zero.

Before this, a restart wiped not just the visible chat log (see
events.py's EventBus, deliberately non-persistent -- "a server restart
clears it, which is fine for a live feed") but the agent's own actual
memory of what was just discussed: a fresh ClaudeSDKClient with no
`resume` is a brand new conversation as far as the model is concerned,
regardless of what the UI still shows on screen. The Claude Agent SDK's
`resume` option (ClaudeAgentOptions.resume: str | None -- "Session ID to
resume. Loads the conversation history from the specified session.")
reads the CLI's own on-disk transcript, so this only needs to remember
which session_id was last known-good; the SDK does the rest.

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
from pathlib import Path
from typing import Optional

STATE_PATH = Path(__file__).resolve().parent.parent / "session_state.json"


def _load() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def get_session_id(mode: str) -> Optional[str]:
    return _load().get(mode)


def save_session_id(mode: str, session_id: str) -> None:
    if not session_id:
        return
    state = _load()
    if state.get(mode) == session_id:
        return
    state[mode] = session_id
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
