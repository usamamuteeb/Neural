"""
Personal CRM: lightweight contact notes -- who someone is, when you last
talked, and what you still owe them -- so "did I ever get back to Sarah
about that proposal" has a real answer instead of relying on memory.

Same philosophy as backend/memory.py: plain markdown, one file per
person under Neural/memory/people/<slug>.md, readable and editable by
hand, no database. Deliberately reuses memory.py's MEMORY_DIR (so a
contact file lives right alongside preferences.md/facts.md/brand/) --
this is why it's part of the SAME backup sweep as normal memory (see
backend/backup.py) with no extra wiring needed.

Each file has two structured pieces on top of free-form notes, both
parsed with plain string/regex work (no LLM call needed to answer "what
do I need to follow up on"):

  - a "Last contact" line, updated by crm_log_contact whenever the user
    describes an actual interaction with that person
  - a "## Follow-ups" section of GitHub-style checkboxes, each optionally
    tagged "(due YYYY-MM-DD)", so crm_list_followups can answer "what's
    outstanding" or "what's due today" by just scanning every file --
    exactly what a daily reminder automation needs (see the README's
    Personal CRM section for the suggested automation prompt).
"""

import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from claude_agent_sdk import create_sdk_mcp_server, tool

from .memory import MEMORY_DIR

PEOPLE_DIR = MEMORY_DIR / "people"
PEOPLE_DIR.mkdir(parents=True, exist_ok=True)

_LAST_CONTACT_RE = re.compile(r"^\*\*Last contact:\*\* (.+)$", re.MULTILINE)
_FOLLOWUP_RE = re.compile(r"^- \[( |x|X)\] (.+?)(?: \(due (\S+)\))?\s*$", re.MULTILINE)


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "unknown"


def _path(name: str) -> Path:
    return PEOPLE_DIR / f"{_slugify(name)}.md"


def _text(s: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": s}]}


def _skeleton(name: str) -> str:
    return f"# {name.strip()}\n\n**Last contact:** (not logged yet)\n\n## Notes\n\n## Follow-ups\n"


def _read_or_create(name: str) -> str:
    p = _path(name)
    if not p.exists():
        content = _skeleton(name)
        p.write_text(content, encoding="utf-8")
        return content
    return p.read_text(encoding="utf-8")


def _write(name: str, content: str) -> None:
    _path(name).write_text(content, encoding="utf-8")


def _insert_under_heading(content: str, heading: str, line: str) -> str:
    """Insert `line` as the last item of the "## {heading}" section,
    creating that heading at the end of the file if it isn't there yet."""
    marker = f"## {heading}"
    if marker not in content:
        return content.rstrip("\n") + f"\n\n{marker}\n{line}\n"
    idx = content.index(marker) + len(marker)
    next_heading = content.find("\n## ", idx)
    if next_heading == -1:
        before, section, after = content[:idx], content[idx:], ""
    else:
        before, section, after = content[:idx], content[idx:next_heading], content[next_heading:]
    section = section.rstrip("\n") + f"\n{line}\n"
    return before + section + after


def _set_last_contact(content: str, when: str) -> str:
    line = f"**Last contact:** {when}"
    if _LAST_CONTACT_RE.search(content):
        return _LAST_CONTACT_RE.sub(line, content, count=1)
    return content.rstrip("\n") + f"\n\n{line}\n"


def _parse_followups(content: str) -> list[dict[str, Any]]:
    marker = "## Follow-ups"
    if marker not in content:
        return []
    idx = content.index(marker)
    next_heading = content.find("\n## ", idx + len(marker))
    section = content[idx:] if next_heading == -1 else content[idx:next_heading]
    out = []
    for m in _FOLLOWUP_RE.finditer(section):
        out.append({"done": m.group(1).lower() == "x", "task": m.group(2).strip(), "due": m.group(3)})
    return out


def _due_on_or_before(due: Optional[str], cutoff: date) -> bool:
    if not due:
        return False
    try:
        return datetime.strptime(due, "%Y-%m-%d").date() <= cutoff
    except ValueError:
        return True  # unparseable date -- surface it rather than silently drop it


@tool(
    "crm_list_contacts",
    "List every contact in the personal CRM, with their last-contact date and how "
    "many open follow-ups each has.",
    {},
)
async def crm_list_contacts(args: dict) -> dict[str, Any]:
    files = sorted(PEOPLE_DIR.glob("*.md"))
    if not files:
        return _text("No contacts saved yet.")
    lines = []
    for f in files:
        content = f.read_text(encoding="utf-8")
        name_match = re.match(r"^# (.+)$", content, re.MULTILINE)
        name = name_match.group(1) if name_match else f.stem
        last_contact_match = _LAST_CONTACT_RE.search(content)
        last_contact = last_contact_match.group(1) if last_contact_match else "not logged"
        open_count = sum(1 for fu in _parse_followups(content) if not fu["done"])
        lines.append(f"- {name} -- last contact: {last_contact} -- {open_count} open follow-up(s)")
    return _text("Contacts:\n" + "\n".join(lines))


@tool(
    "crm_read_contact",
    "Read one contact's full CRM file by name (notes, last contact date, and "
    "follow-ups, done and open).",
    {"name": str},
)
async def crm_read_contact(args: dict) -> dict[str, Any]:
    name = (args.get("name") or "").strip()
    if not name:
        return _text("Need a name.")
    p = _path(name)
    if not p.exists():
        return _text(f'No contact file for "{name}" yet -- use crm_note or crm_log_contact to create one.')
    return _text(p.read_text(encoding="utf-8"))


@tool(
    "crm_write_contact",
    "Fully overwrite a contact's CRM file -- use for restructuring or correcting "
    "it wholesale; use crm_note/crm_add_followup to just add one thing.",
    {"name": str, "content": str},
)
async def crm_write_contact(args: dict) -> dict[str, Any]:
    name = (args.get("name") or "").strip()
    if not name:
        return _text("Need a name.")
    _write(name, args.get("content") or "")
    return _text(f"Wrote {_path(name).name}.")


@tool(
    "crm_note",
    "Add a note about a contact (who they are, what they do, something they "
    "mentioned) -- does NOT update their last-contact date; use crm_log_contact "
    "for that. Creates the contact if they don't exist yet.",
    {"name": str, "note": str},
)
async def crm_note(args: dict) -> dict[str, Any]:
    name = (args.get("name") or "").strip()
    note = (args.get("note") or "").strip()
    if not name or not note:
        return _text("Need both a name and a note.")
    content = _read_or_create(name)
    content = _insert_under_heading(content, "Notes", f"- {note}")
    _write(name, content)
    return _text(f"Noted for {name}.")


@tool(
    "crm_log_contact",
    "Record that you actually talked to/met/heard from someone today (or on a "
    "given date) -- use this specifically when the user describes a real "
    "interaction (\"talked to Sarah today\", \"just got off a call with James\"), "
    "not for a fact that doesn't involve contact.",
    {"name": str, "when": str},
)
async def crm_log_contact(args: dict) -> dict[str, Any]:
    name = (args.get("name") or "").strip()
    if not name:
        return _text("Need a name.")
    when = (args.get("when") or "").strip() or date.today().isoformat()
    content = _read_or_create(name)
    content = _set_last_contact(content, when)
    _write(name, content)
    return _text(f"Logged contact with {name} on {when}.")


@tool(
    "crm_add_followup",
    "Add an open follow-up for a contact -- use whenever the user says or implies "
    "they owe someone something (\"I'll get back to her about the proposal\", "
    "\"remind me to reply to this\"). Pass a due date (YYYY-MM-DD) if one's "
    "implied or stated; leave it blank for an open-ended follow-up.",
    {"name": str, "task": str, "due": str},
)
async def crm_add_followup(args: dict) -> dict[str, Any]:
    name = (args.get("name") or "").strip()
    task = (args.get("task") or "").strip()
    if not name or not task:
        return _text("Need both a name and what the follow-up is.")
    due = (args.get("due") or "").strip()
    line = f"- [ ] {task}" + (f" (due {due})" if due else "")
    content = _read_or_create(name)
    content = _insert_under_heading(content, "Follow-ups", line)
    _write(name, content)
    return _text(f"Added follow-up for {name}: {task}" + (f" (due {due})" if due else ""))


@tool(
    "crm_complete_followup",
    "Mark a contact's follow-up as done, matched by a substring of its task text.",
    {"name": str, "task": str},
)
async def crm_complete_followup(args: dict) -> dict[str, Any]:
    name = (args.get("name") or "").strip()
    task_substring = (args.get("task") or "").strip().lower()
    if not name or not task_substring:
        return _text("Need both a name and enough of the follow-up's text to match it.")
    p = _path(name)
    if not p.exists():
        return _text(f'No contact file for "{name}".')
    content = p.read_text(encoding="utf-8")

    def _mark(m: "re.Match") -> str:
        if m.group(1).lower() != "x" and task_substring in m.group(2).lower():
            _mark.matched = True
            due_part = f" (due {m.group(3)})" if m.group(3) else ""
            return f"- [x] {m.group(2)}{due_part}"
        return m.group(0)

    _mark.matched = False
    new_content = _FOLLOWUP_RE.sub(_mark, content)
    if not _mark.matched:
        return _text(f'No open follow-up matching "{args.get("task")}" found for {name}.')
    _write(name, new_content)
    return _text(f"Marked done for {name}.")


@tool(
    "crm_list_followups",
    "List open follow-ups across every contact (or one, if name is given). Pass "
    "due_within_days (e.g. 0 for due today/overdue, 3 for due within 3 days) to "
    "filter to what's actually pressing -- omit it to see everything outstanding, "
    "including open-ended ones with no due date.",
    {"name": str, "due_within_days": int},
)
async def crm_list_followups(args: dict) -> dict[str, Any]:
    name_filter = (args.get("name") or "").strip()
    due_within_days = args.get("due_within_days")
    cutoff = None
    if due_within_days is not None:
        try:
            cutoff = date.today() + timedelta(days=int(due_within_days))
        except (TypeError, ValueError):
            cutoff = None

    files = [_path(name_filter)] if name_filter else sorted(PEOPLE_DIR.glob("*.md"))
    results = []
    for p in files:
        if not p.exists():
            continue
        content = p.read_text(encoding="utf-8")
        name_match = re.match(r"^# (.+)$", content, re.MULTILINE)
        display_name = name_match.group(1) if name_match else p.stem
        for fu in _parse_followups(content):
            if fu["done"]:
                continue
            if cutoff is not None and not _due_on_or_before(fu["due"], cutoff):
                continue
            results.append({"name": display_name, **fu})

    if not results:
        return _text("Nothing outstanding." if cutoff is None else "Nothing due in that window.")

    results.sort(key=lambda r: (r["due"] is None, r["due"] or ""))
    lines = [
        f"- {r['name']}: {r['task']}" + (f" (due {r['due']})" if r["due"] else " (no due date)")
        for r in results
    ]
    return _text("Open follow-ups:\n" + "\n".join(lines))


CRM_TOOLS = [
    "crm_list_contacts",
    "crm_read_contact",
    "crm_write_contact",
    "crm_note",
    "crm_log_contact",
    "crm_add_followup",
    "crm_complete_followup",
    "crm_list_followups",
]

crm_server = create_sdk_mcp_server(
    name="crm",
    version="1.0.0",
    tools=[
        crm_list_contacts,
        crm_read_contact,
        crm_write_contact,
        crm_note,
        crm_log_contact,
        crm_add_followup,
        crm_complete_followup,
        crm_list_followups,
    ],
)
