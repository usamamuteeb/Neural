"""
File-based, persistent memory for Neural. Plain markdown files under
Neural/memory/, one file per subject (preferences.md, facts.md, or
whatever topic names the agent picks) -- readable and editable by hand
too, unlike a vector DB. No embeddings/Chroma yet (that's a possible
upgrade for fuzzier recall later); for a single user's personal facts,
keyword search across a handful of files is plenty and has zero extra
infrastructure.

This is what makes facts and preferences survive across restarts and
days, on top of whatever a single chat session already remembers on its
own.
"""

from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

MEMORY_DIR = Path(__file__).resolve().parent.parent / "memory"
MEMORY_DIR.mkdir(exist_ok=True)


def _safe_path(rel_path: str) -> Path:
    """Resolve a user-given relative path and refuse anything that escapes
    MEMORY_DIR (no absolute paths, no ../ traversal)."""
    candidate = (MEMORY_DIR / rel_path).resolve()
    if MEMORY_DIR not in candidate.parents and candidate != MEMORY_DIR:
        raise ValueError(f"path escapes the memory directory: {rel_path}")
    return candidate


def _text(s: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": s}]}


@tool(
    "memory_list",
    "List every memory file, with its first line as a one-line description of what it holds.",
    {},
)
async def memory_list(args: dict) -> dict[str, Any]:
    files = sorted(MEMORY_DIR.rglob("*.md"))
    if not files:
        return _text("No memory files yet.")
    lines = []
    for f in files:
        rel = f.relative_to(MEMORY_DIR)
        try:
            first_line = f.read_text(encoding="utf-8").strip().splitlines()[0]
        except (IndexError, OSError):
            first_line = "(empty)"
        lines.append(f"- {rel}: {first_line}")
    return _text("\n".join(lines))


@tool(
    "memory_read",
    "Read one memory file's full content by its relative path (e.g. 'preferences.md').",
    {"path": str},
)
async def memory_read(args: dict) -> dict[str, Any]:
    try:
        p = _safe_path(args["path"])
    except ValueError as e:
        return _text(str(e))
    if not p.exists():
        return _text(f"No such memory file: {args['path']}")
    return _text(p.read_text(encoding="utf-8"))


@tool(
    "memory_write",
    "Create or fully overwrite a memory file with new content. Use for restructuring "
    "or replacing a file's content wholesale; use memory_append to just add a line.",
    {"path": str, "content": str},
)
async def memory_write(args: dict) -> dict[str, Any]:
    try:
        p = _safe_path(args["path"])
    except ValueError as e:
        return _text(str(e))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(args["content"], encoding="utf-8")
    return _text(f"Wrote {args['path']}.")


@tool(
    "memory_append",
    "Add a line (or block of text) to the end of a memory file, creating it if it "
    "doesn't exist yet. Prefer this for adding one new fact.",
    {"path": str, "content": str},
)
async def memory_append(args: dict) -> dict[str, Any]:
    try:
        p = _safe_path(args["path"])
    except ValueError as e:
        return _text(str(e))
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        if p.stat().st_size > 0:
            f.write("\n")
        f.write(args["content"])
    return _text(f"Appended to {args['path']}.")


@tool(
    "memory_search",
    "Case-insensitive keyword search across every memory file. Returns matching lines with their file and line number.",
    {"query": str},
)
async def memory_search(args: dict) -> dict[str, Any]:
    query = args["query"].lower()
    hits = []
    for f in sorted(MEMORY_DIR.rglob("*.md")):
        rel = f.relative_to(MEMORY_DIR)
        try:
            for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                if query in line.lower():
                    hits.append(f"{rel}:{i}: {line.strip()}")
        except OSError:
            continue
    if not hits:
        return _text(f"No matches for '{args['query']}'.")
    return _text("\n".join(hits))


MEMORY_TOOLS = [
    "memory_list",
    "memory_read",
    "memory_write",
    "memory_append",
    "memory_search",
]

memory_server = create_sdk_mcp_server(
    name="memory",
    version="1.0.0",
    tools=[memory_list, memory_read, memory_write, memory_append, memory_search],
)
