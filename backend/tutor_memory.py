"""
Tutor mode's OWN memory store -- plain markdown files under
Neural/tutor_memory/, completely separate from backend/memory.py's
Neural/memory/ directory used by normal chat. Same reasoning as that
module (readable/editable by hand, zero extra infrastructure for a
single user's notes), duplicated here on purpose rather than shared:
Tutor mode is meant to feel like a different app with its own state,
not chat with a costume on, so its facts/progress/session log must
never end up mixed into (or read from) normal chat's memory files.

Two files carry special meaning for Tutor mode specifically (layer 2 of
the three-layer design -- see tutor.py's module docstring), on top of
whatever other topic files the tutor creates:

  - progress.md -- a running record of what the user has actually
    mastered vs. what's still shaky, read back into the system prompt
    at the start of every tutoring session (see tutor.py) and updated
    by the tutor as it observes real evidence either way.
  - sessions.md -- a dated log, one entry per finished session (topics
    covered, weak spots, open questions), appended when a session ends
    and read back (latest entry) at the start of the next one.

Both are just markdown files written through the same tools below --
there's nothing structurally special about them beyond the convention
the tutor's system prompt asks it to follow.
"""

from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

TUTOR_MEMORY_DIR = Path(__file__).resolve().parent.parent / "tutor_memory"
TUTOR_MEMORY_DIR.mkdir(exist_ok=True)


def _safe_path(rel_path: str) -> Path:
    """Resolve a user-given relative path and refuse anything that escapes
    TUTOR_MEMORY_DIR (no absolute paths, no ../ traversal)."""
    candidate = (TUTOR_MEMORY_DIR / rel_path).resolve()
    if TUTOR_MEMORY_DIR not in candidate.parents and candidate != TUTOR_MEMORY_DIR:
        raise ValueError(f"path escapes the tutor memory directory: {rel_path}")
    return candidate


def _text(s: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": s}]}


@tool(
    "tutor_memory_list",
    "List every Tutor-mode memory file, with its first line as a one-line description. "
    "This is Tutor mode's own memory, separate from Neural's normal-chat memory.",
    {},
)
async def tutor_memory_list(args: dict) -> dict[str, Any]:
    files = sorted(TUTOR_MEMORY_DIR.rglob("*.md"))
    if not files:
        return _text("No Tutor-mode memory files yet.")
    lines = []
    for f in files:
        rel = f.relative_to(TUTOR_MEMORY_DIR)
        try:
            first_line = f.read_text(encoding="utf-8").strip().splitlines()[0]
        except (IndexError, OSError):
            first_line = "(empty)"
        lines.append(f"- {rel}: {first_line}")
    return _text("\n".join(lines))


@tool(
    "tutor_memory_read",
    "Read one Tutor-mode memory file's full content by its relative path (e.g. "
    "'progress.md' or 'sessions.md').",
    {"path": str},
)
async def tutor_memory_read(args: dict) -> dict[str, Any]:
    try:
        p = _safe_path(args["path"])
    except ValueError as e:
        return _text(str(e))
    if not p.exists():
        return _text(f"No such Tutor-mode memory file: {args['path']}")
    return _text(p.read_text(encoding="utf-8"))


@tool(
    "tutor_memory_write",
    "Create or fully overwrite a Tutor-mode memory file with new content. Use for "
    "restructuring or replacing a file's content wholesale; use tutor_memory_append "
    "to just add a line or a new session entry.",
    {"path": str, "content": str},
)
async def tutor_memory_write(args: dict) -> dict[str, Any]:
    try:
        p = _safe_path(args["path"])
    except ValueError as e:
        return _text(str(e))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(args["content"], encoding="utf-8")
    return _text(f"Wrote {args['path']}.")


@tool(
    "tutor_memory_append",
    "Add a line (or block of text) to the end of a Tutor-mode memory file, creating "
    "it if it doesn't exist yet. Use this for progress.md updates and for appending "
    "a new dated entry to sessions.md.",
    {"path": str, "content": str},
)
async def tutor_memory_append(args: dict) -> dict[str, Any]:
    try:
        p = _safe_path(args["path"])
    except ValueError as e:
        return _text(str(e))
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        if p.stat().st_size > 0:
            f.write("\n\n")
        f.write(args["content"])
    return _text(f"Appended to {args['path']}.")


@tool(
    "tutor_memory_search",
    "Case-insensitive keyword search across every Tutor-mode memory file. Returns "
    "matching lines with their file and line number.",
    {"query": str},
)
async def tutor_memory_search(args: dict) -> dict[str, Any]:
    query = args["query"].lower()
    hits = []
    for f in sorted(TUTOR_MEMORY_DIR.rglob("*.md")):
        rel = f.relative_to(TUTOR_MEMORY_DIR)
        try:
            for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                if query in line.lower():
                    hits.append(f"{rel}:{i}: {line.strip()}")
        except OSError:
            continue
    if not hits:
        return _text(f"No matches for '{args['query']}'.")
    return _text("\n".join(hits))


TUTOR_MEMORY_TOOLS = [
    "tutor_memory_list",
    "tutor_memory_read",
    "tutor_memory_write",
    "tutor_memory_append",
    "tutor_memory_search",
]

tutor_memory_server = create_sdk_mcp_server(
    name="tutor_memory",
    version="1.0.0",
    tools=[
        tutor_memory_list,
        tutor_memory_read,
        tutor_memory_write,
        tutor_memory_append,
        tutor_memory_search,
    ],
)
