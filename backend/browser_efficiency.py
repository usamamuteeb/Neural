"""
Token diet for browser work (Playwright MCP).

Where the tokens actually went: every browser action (navigate, click,
type, ...) saves the page's full accessibility tree to a
.playwright-mcp/page-*.yml file and returns a link to it. To know what it
could click next, the model then read that file -- typically ~10KB
(~3k tokens) and up to ~86KB (~22k tokens) for a busy page like X or
WhatsApp, on EVERY step of a multi-step task, and each copy then stayed in
the conversation for the rest of the session. A ten-click task could
easily burn 50-100k tokens just re-reading page trees.

What this module does instead:
  - A PostToolUse hook (see agent.py) appends a compact digest of the
    snapshot to the tool result -- interactive elements only (ref, role,
    name, state), deduplicated and capped -- so the model can act on refs
    directly without ever reading the whole file. For an explicit
    browser_snapshot call the digest also includes the page's headings and
    visible text lines (still capped), since the point of that call is
    usually to read something.
  - A PreToolUse hook makes a bare browser_snapshot (no target, no
    filename) save to a file too, instead of pasting the entire tree
    inline; the digest above then covers it. A snapshot scoped with
    target/depth is left alone -- that's already the efficient path.
  - prune_artifacts() keeps .playwright-mcp from growing forever (it had
    reached ~1000 files / 49MB): page/snapshot YAML and console logs older
    than a couple of days are deleted. Anything else in there (PDFs,
    screenshots the user may want) is never touched.

Nothing here imports the Agent SDK, so it can be unit-tested on its own.
Every hook is wrapped so a parsing problem can only ever mean "no digest
this time", never a failed tool call.
"""

import logging
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("neural.browser")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ARTIFACT_DIR = PROJECT_ROOT / ".playwright-mcp"
ARTIFACT_KEEP_DAYS = 2
_PRUNABLE = re.compile(r"^(page|snapshot|console)-.*\.(yml|yaml|log)$", re.I)

INTERACTIVE_ROLES = {
    "button", "link", "textbox", "searchbox", "combobox", "checkbox", "radio", "switch",
    "menuitem", "menuitemcheckbox", "menuitemradio", "tab", "option", "slider",
    "spinbutton", "listbox", "treeitem",
}
TEXT_ROLES = {"heading", "paragraph", "text", "cell", "listitem", "article", "blockquote", "caption", "term", "definition", "status", "alert"}
_FLAG_WORDS = ("active", "checked", "disabled", "expanded", "selected", "pressed")

_LINE = re.compile(
    r'^(?P<indent>\s*)- (?P<role>[a-z][a-z0-9_-]*)'
    r'(?: "(?P<name>(?:[^"\\]|\\.)*)")?'
    r'(?P<attrs>(?: \[[^\]]*\])*)'
    r'(?::\s*(?P<text>.*))?$'
)
_REF = re.compile(r"\[ref=([^\]\s]+)\]")
_PATH_IN_LINK = re.compile(r"\]\(([^()\s]+?\.ya?ml)\)")
_BARE_PATH = re.compile(r"((?:[A-Za-z]:[\\/]|\.{0,2}[\\/]|\.playwright-mcp[\\/])[^\s\"'<>|()\[\]]*?\.ya?ml)")
_INLINE_YAML = re.compile(r"```ya?ml\s*\n(.*?)```", re.S)

MAX_SNAPSHOT_BYTES = 3_000_000


def _clip(s: str, n: int) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "\u2026"


def digest_snapshot(yaml_text: str, include_text: bool = False, max_chars: int = 5000) -> str:
    """Compact, model-friendly summary of a Playwright aria snapshot."""
    elements: list[str] = []
    texts: list[str] = []
    seen: dict[tuple, int] = {}
    hidden = 0
    for raw in yaml_text.splitlines():
        m = _LINE.match(raw)
        if not m:
            continue
        role = m.group("role")
        name = (m.group("name") or "").replace('\\"', '"')
        attrs = m.group("attrs") or ""
        inline = (m.group("text") or "").strip()
        ref_m = _REF.search(attrs)
        if role in INTERACTIVE_ROLES and ref_m:
            label = name or inline
            key = (role, label)
            count = seen.get(key, 0)
            seen[key] = count + 1
            if count >= 3:  # e.g. 40 identical "Like" buttons: keep the first few
                hidden += 1
                continue
            flags = [w for w in _FLAG_WORDS if f"[{w}" in attrs]
            line = f"{ref_m.group(1)} {role}"
            if label:
                line += f' "{_clip(label, 80)}"'
            if flags:
                line += " (" + ", ".join(flags) + ")"
            elements.append(line)
        elif include_text and (role in TEXT_ROLES or (role == "generic" and inline)):
            content = name or inline
            if content and not content.startswith("/url"):
                prefix = "# " if role == "heading" else ""
                texts.append(prefix + _clip(content, 160))

    out: list[str] = []
    budget = max_chars
    if include_text and texts:
        out.append("Page text:")
        text_budget = budget // 2
        used = 0
        for t in texts:
            if used + len(t) > text_budget:
                out.append(f"... ({len(texts) - texts.index(t)} more text lines)")
                break
            out.append("  " + t)
            used += len(t) + 3
        budget -= used
    if elements:
        out.append("Interactive elements (ref role \"name\"):")
        used = 0
        for i, e in enumerate(elements):
            if used + len(e) > budget:
                out.append(f"... ({len(elements) - i} more not shown)")
                break
            out.append("  " + e)
            used += len(e) + 3
    if hidden:
        out.append(f"({hidden} more repeated elements with the same label omitted)")
    return "\n".join(out)


def _collect_text(obj: Any, out: list) -> None:
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, dict):
        for k in ("text", "content", "result", "output"):
            if k in obj:
                _collect_text(obj[k], out)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            _collect_text(item, out)


def _resolve(candidate: str) -> "Path | None":
    candidate = candidate.strip().strip("'\"")
    p = Path(candidate)
    tries = [p] if p.is_absolute() else [PROJECT_ROOT / p, ARTIFACT_DIR / p.name]
    for t in tries:
        try:
            if t.is_file():
                return t
        except OSError:
            continue
    return None


def snapshot_paths(text: str) -> list:
    found = []
    for rx in (_PATH_IN_LINK, _BARE_PATH):
        for m in rx.finditer(text):
            path = _resolve(m.group(1))
            if path and path not in found:
                found.append(path)
    return found


def build_digest_for_result(tool_name: str, tool_response: Any) -> "str | None":
    parts: list = []
    _collect_text(tool_response, parts)
    text = "\n".join(parts)
    if not text:
        return None
    explicit = tool_name.endswith("browser_snapshot")
    paths = snapshot_paths(text)
    if paths:
        path = paths[-1]
        try:
            if path.stat().st_size > MAX_SNAPSHOT_BYTES:
                return None
            yaml_text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        source = f"from {path.name}; Grep that file for anything not listed -- never Read it whole"
    else:
        m = _INLINE_YAML.search(text)
        if not m or len(m.group(1)) < 4000:
            return None  # small inline snapshot: already cheap, nothing to add
        yaml_text = m.group(1)
        source = "digest of the snapshot above"
    digest = digest_snapshot(yaml_text, include_text=explicit, max_chars=7000 if explicit else 4500)
    if not digest:
        return None
    return f"[Neural page digest -- {source}]\n{digest}"


async def post_tool_hook(input_data, _tool_use_id, _context):
    """PostToolUse hook for mcp__playwright__* tools (see agent.py)."""
    try:
        tool_name = str(input_data.get("tool_name", ""))
        if not tool_name.startswith("mcp__playwright__"):
            return {}
        digest = build_digest_for_result(tool_name, input_data.get("tool_response"))
        if not digest:
            return {}
        return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": digest}}
    except Exception as exc:  # noqa: BLE001 -- a digest is a bonus, never a failure
        logger.warning("browser digest skipped: %s", exc)
        return {}


async def pre_snapshot_hook(input_data, _tool_use_id, _context):
    """PreToolUse hook: a bare browser_snapshot saves to a file instead of
    pasting the entire tree inline (the post hook then digests it)."""
    try:
        if not str(input_data.get("tool_name", "")).endswith("browser_snapshot"):
            return {}
        tool_input = dict(input_data.get("tool_input") or {})
        if tool_input.get("filename") or tool_input.get("target") or tool_input.get("ref"):
            return {}
        tool_input["filename"] = time.strftime("snapshot-%Y%m%d-%H%M%S.yml")
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "updatedInput": tool_input,
            }
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("snapshot redirect skipped: %s", exc)
        return {}


def prune_artifacts(now: "float | None" = None, keep_days: float = ARTIFACT_KEEP_DAYS) -> int:
    if not ARTIFACT_DIR.is_dir():
        return 0
    cutoff = (now or time.time()) - keep_days * 86400
    removed = 0
    for f in ARTIFACT_DIR.iterdir():
        try:
            if f.is_file() and _PRUNABLE.match(f.name) and f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        except OSError:
            continue
    if removed:
        logger.info("Pruned %d old Playwright snapshot/log files.", removed)
    return removed
