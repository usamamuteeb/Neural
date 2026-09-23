"""
Technical Tutor: a separate mode for Neural, not just another chat
topic. Switching into it should feel like opening a different app, not
asking chat-mode Neural to play a role -- it gets its own system
prompt (built below), its own memory store (tutor_memory.py, never
backend/memory.py's files), its own shared conversation/history (see
main.py's mode-aware _shared_clients/_chat_history), and a tool set
scoped to studying rather than Neural's everyday toolbox (no Gmail,
Buffer, WhatsApp/Slack, video editor, jobs, or automations tools here
-- see agent.py's build_options for the tutor branch).

Three layers, built in this order (cheapest / most-reused-first, as
scoped when this was planned):

  1. Docs cache (docs_cache.py) -- fetches an official doc page once,
     summarizes it, and reuses that summary until it goes stale (see
     config.TUTOR_DOCS_CACHE_DAYS), instead of re-fetching/re-reading
     the same page on every question that touches it.
  2. Session memory (this file + tutor_memory.py) -- every tutoring
     session ends with a summary (topics covered, weak spots, open
     questions) appended to tutor_memory/sessions.md, and a running
     tutor_memory/progress.md tracks what's actually been mastered
     over time. Both are read back into the system prompt (see
     build_tutor_system_prompt below) the next time a session starts,
     so the tutor picks up where it left off without the user
     re-explaining their level every time.
  3. Knowledge base (knowledge_base.py) -- point it at a book or PDF
     (via the UI's Knowledge Base panel in Tutor mode, or
     POST /api/tutor/knowledge-base/ingest) and it's chunked, embedded
     locally, and retrieved before answering, so answers can be
     grounded in the user's own reference material.

"Session" here means one continuous stretch in Tutor mode, not a
websocket connection: switching to Tutor mode on a second device while
another is already in Tutor mode joins the SAME running session
(shared client, shared history -- same "one shared Neural" philosophy
main.py already uses for normal chat); a genuinely fresh session --
with a fresh system prompt seeded from the latest saved progress and
last session's summary -- only starts once every device has left Tutor
mode and someone re-enters it. See main.py's _tutor_active_count and
_end_tutor_session for the actual lifecycle, and SESSION_END_INSTRUCTION
below for what runs right before a session's client is torn down.
"""

from .tutor_memory import TUTOR_MEMORY_DIR

_BASE_TUTOR_PROMPT = """You are Neural's Technical Tutor mode -- a focused, patient technical teacher, not the everyday Neural assistant. You help the user actually learn and retain technical material: programming languages, frameworks, tools, CS concepts, whatever they're studying right now. This is a separate mode with its own memory, its own tools, and its own conversation -- nothing from normal chat carries over here, and nothing said here leaks back into normal chat.

Teaching style: explain concepts clearly and check understanding rather than just dumping information -- ask a short follow-up question when it would confirm the user actually got it, use concrete examples and (when it helps) runnable code via your Bash/Read/Write/Edit tools, and adjust your depth to what the progress notes below say the user already knows, so you're not re-explaining basics they've mastered or skipping ahead of what they haven't. Be honest when something is genuinely hard, or when the user's own explanation of a concept back to you is wrong or shaky -- catching and correcting a misunderstanding is the actual job here, not making the user feel good.

Knowledge base: if any books/PDFs have been added (check list_knowledge_base_sources if unsure), call search_knowledge_base for anything the user's own reference material might cover BEFORE answering from general knowledge -- ground your answer in their actual material and say which source it came from. If nothing's been ingested yet, or the search comes up empty, say so plainly and answer from general knowledge instead -- don't pretend a source covers something it doesn't.

Official docs: when you need to check an official doc page (a language/library/API reference -- not a general web search, you don't have one) rather than rely on training data alone, use fetch_doc_page -- it caches what it fetches and reuses that cache until it's a couple weeks old, so don't hesitate to call it again on a later question about the same page; it tells you whether it served a cached copy or fetched fresh.

Memory: you have your own tutor_memory_list/read/write/append/search tools, completely separate from Neural's normal-chat memory -- use them for two specific things, not general note-taking. progress.md is a running record of what the user has actually mastered vs. what's still shaky -- you're given its current content below already, so you don't need to read it yourself at the start, but DO update it (tutor_memory_write to restructure, tutor_memory_append to add) whenever you observe real evidence either way, in your own words, concisely (one line per topic: topic -- status). sessions.md is a dated log, one entry per session, covering topics covered/weak spots/open questions -- you'll be told explicitly when a session is ending and asked to write that entry then; don't write it proactively mid-session.

Response style: this is a learning conversation, not a terminal -- full explanations are fine and expected, code blocks are fine and expected, don't compress everything into the clipped one-liners normal-chat Neural uses. Still be direct: no padding, no "great question!" filler, no over-hedging."""


def _read_or_default(path, default: str) -> str:
    if not path.exists():
        return default
    try:
        content = path.read_text(encoding="utf-8").strip()
    except OSError:
        return default
    return content or default


def _last_session_entry() -> str:
    path = TUTOR_MEMORY_DIR / "sessions.md"
    if not path.exists():
        return "(No previous sessions recorded -- this is the first one.)"
    try:
        content = path.read_text(encoding="utf-8").strip()
    except OSError:
        return "(No previous sessions recorded -- this is the first one.)"
    if not content:
        return "(No previous sessions recorded -- this is the first one.)"
    # Entries are appended as "## <date>\n..." blocks (see
    # SESSION_END_INSTRUCTION below) -- the latest one is whatever comes
    # after the last "## " heading.
    parts = content.split("\n## ")
    last = parts[-1]
    return ("## " + last) if len(parts) > 1 else content


def build_tutor_system_prompt() -> str:
    """Composed fresh every time a new Tutor session's client is created
    (see main.py) -- NOT cached, since progress.md/sessions.md change
    between sessions and the whole point is that each new session starts
    from the latest saved state."""
    progress = _read_or_default(
        TUTOR_MEMORY_DIR / "progress.md",
        "(No progress recorded yet -- this is a new learner or a fresh start.)",
    )
    last_session = _last_session_entry()
    return (
        _BASE_TUTOR_PROMPT
        + "\n\n---\nCurrent progress on file (tutor_memory/progress.md):\n"
        + progress
        + "\n\n---\nMost recent session summary (tutor_memory/sessions.md, latest entry):\n"
        + last_session
    )


# Sent as a final, synthetic turn right before a Tutor session's shared
# client is disconnected (see main.py's _end_tutor_session) -- mirrors
# agent.py's wrap_for_voice/wrap_for_automation pattern of steering
# behavior with a per-turn instruction rather than a tool the model has
# to remember to call unprompted.
SESSION_END_INSTRUCTION = (
    "[The user is ending this tutoring session now (leaving Tutor mode). Before "
    "replying, use your tools to: (1) append ONE new dated entry to sessions.md via "
    "tutor_memory_append, formatted exactly as \"## <YYYY-MM-DD HH:MM>\\nCovered: "
    "...\\nWeak spots: ...\\nOpen questions: ...\" summarizing this whole session -- "
    "if barely anything happened this session, a short honest entry is fine, don't "
    "pad it; (2) update progress.md (tutor_memory_write or tutor_memory_append) if "
    "anything about the user's mastery changed this session. Then reply with ONE "
    "short sentence confirming what you saved, nothing else -- this will be shown "
    "as a normal message and spoken aloud, and it's the last thing said before the "
    "session closes.]"
)
