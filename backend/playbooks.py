"""
On-demand playbooks: Neural's specialist instructions, loaded only when a
task actually needs them.

Why this exists: the chat system prompt used to carry ~32k characters
(~8k tokens) of detailed how-tos -- Cap's CLI, Buffer's media quirks,
caption styling numbers, Vercel flags, meal planning -- on EVERY turn,
including "hey" and "what's the weather". Prompt caching makes repeat
reads cheaper, but they still count against the subscription's usage and
still have to be processed before the first word of each reply. Now the
chat prompt keeps only the always-relevant core plus a one-line index of
these playbooks; the model calls read_playbook(name) the first time a
task needs one, and the text then stays in that conversation's context.

This is deliberately an in-process tool rather than Claude Code "skills":
Neural's clients run with setting_sources=[] (on purpose -- see
agent.py) so CLI skill discovery is off, and automation/background-worker
runs must behave exactly as before without depending on the model
remembering to load anything. Those isolated runs therefore get the full
text inlined instead (see full_text() and agent.py's WORKER prompt).

Playbook files live in backend/playbooks/*.md. The ATS resume skill keeps
living where it always did (.claude/skills/ats-resume/) and is served from
there -- before this, setting_sources=[] meant chat could never actually
reach it.
"""

from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

_DIR = Path(__file__).resolve().parent / "playbooks"
_ATS_SKILL = Path(__file__).resolve().parent.parent / ".claude" / "skills" / "ats-resume" / "SKILL.md"

# name -> when to load it (shown to the model in the chat prompt's index)
PLAYBOOKS: dict[str, str] = {
    "social-posting": "posting to LinkedIn/Instagram/X/etc. through Buffer, post analytics, brand voice and brand kits, AI images for posts via ChatGPT, newsletters and podcast scripts",
    "screen-recording": "recording the screen (Cap or ffmpeg), exporting/uploading recordings, or reading a past recording's transcript",
    "video-editing": "trimming, captioning, color grading, narrating or composing a video, Pexels stock footage, and long renders",
    "image-editing": "editing an image file (resize, crop, text overlay, convert) with ImageMagick or Pillow",
    "vercel": "anything about a Vercel project: deploys, deployment status/logs, environment variables",
    "meal-planning": "meal plans, what to cook from the pantry, grocery lists",
    "job-search": "finding job listings with search_jobs (Adzuna) and presenting them",
    "ats-resume": "writing, updating, or tailoring a resume/CV (ATS-safe .docx)",
    "media-analysis": "the user shares a social media post or video link (YouTube, TikTok, Instagram, X, Reddit...) -- what it says/shows, and how to do a feature or technique it demos",
}


def _strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4 :].lstrip()
    return text


def load(name: str) -> "str | None":
    name = (name or "").strip().lower().removesuffix(".md")
    if name not in PLAYBOOKS:
        return None
    path = _ATS_SKILL if name == "ats-resume" else _DIR / f"{name}.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return _strip_frontmatter(text).strip()


def index_text() -> str:
    lines = "; ".join(f"{name} ({when})" for name, when in PLAYBOOKS.items())
    return (
        "Playbooks: detailed instructions for specialist tasks live outside this prompt so every turn "
        "stays fast. Before starting one of these, call read_playbook with its name -- once per "
        "conversation is enough, the text stays in your context afterward -- and follow it: "
        + lines
        + ". The rule to show the user exactly what you're about to send, post, publish, or deploy and "
        "get their go-ahead first applies everywhere, playbook loaded or not."
    )


def full_text() -> str:
    """Every playbook inlined -- for isolated automation/background runs,
    which must not depend on remembering to call read_playbook."""
    parts = []
    for name in PLAYBOOKS:
        body = load(name)
        if body:
            parts.append(body)
    return "\n\n".join(parts)


def _text(s: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": s}]}


@tool(
    "read_playbook",
    "Load Neural's detailed instructions for a specialist task before doing it. Names: "
    + ", ".join(PLAYBOOKS)
    + ". Call once per conversation per playbook; the text stays in context.",
    {"name": str},
)
async def read_playbook(args: dict) -> dict[str, Any]:
    body = load(args.get("name", ""))
    if body is None:
        return _text("No such playbook. Available: " + ", ".join(PLAYBOOKS) + ".")
    return _text(body)


PLAYBOOK_TOOLS = ["read_playbook"]
playbook_server = create_sdk_mcp_server(name="playbooks", version="1.0.0", tools=[read_playbook])
