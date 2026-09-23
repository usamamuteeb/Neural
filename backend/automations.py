"""
Cron-style automations: scheduled background agent runs. Task definitions
(name/cron/prompt/enabled/voice_trigger/last_run/last_result) persist to
a small JSON file so they survive restarts; APScheduler's
AsyncIOScheduler handles the actual timing. Each run publishes its
progress to the live activity feed (events.py) tagged with an
"automation:<name>" source, so it shows up alongside interactive chat
activity without being mistaken for it.

Voice-triggered automations: an automation can optionally carry a
voice_trigger phrase (e.g. "start my morning brief"). Saying (or typing)
that phrase runs the automation directly -- see match_voice_trigger()
below and its call site in main.py's ws_endpoint -- instead of going
through a full conversational turn with the agent. This is deliberately
a fast, deterministic path (a plain string match, no LLM call) rather
than routing every message through the model to decide "is this a
trigger phrase" first, which would add latency and an extra place for
the model to get creative/wrong about something that should be exact.
For a LESS exact phrasing ("hey, kick off my newsletter thing"), the
agent itself has a separate run_automation_now tool below that it can
call using its own judgment.
"""

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from claude_agent_sdk import (
    AssistantMessage,
    TextBlock,
    ToolUseBlock,
    create_sdk_mcp_server,
    tool,
)

from .agent import new_client, wrap_for_automation
from .events import bus

logger = logging.getLogger("neural.automations")

STORE_PATH = Path(__file__).resolve().parent.parent / "automations.json"

scheduler = AsyncIOScheduler()


def _load() -> dict[str, dict[str, Any]]:
    if not STORE_PATH.exists():
        return {}
    try:
        return json.loads(STORE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save(tasks: dict[str, dict[str, Any]]) -> None:
    STORE_PATH.write_text(json.dumps(tasks, indent=2), encoding="utf-8")


def _schedule(task_id: str, task: dict[str, Any]) -> None:
    if scheduler.get_job(task_id):
        scheduler.remove_job(task_id)
    scheduler.add_job(
        run_automation,
        CronTrigger.from_crontab(task["cron"]),
        id=task_id,
        args=[task_id],
        replace_existing=True,
    )


def list_automations() -> list[dict[str, Any]]:
    tasks = _load()
    out = []
    for task_id, task in tasks.items():
        job = scheduler.get_job(task_id)
        entry = {**task, "id": task_id}
        entry["next_run"] = (
            job.next_run_time.isoformat() if job and job.next_run_time else None
        )
        out.append(entry)
    return sorted(out, key=lambda t: t.get("name", ""))


_PUNCT_RE = re.compile(r"[^\w\s]")
_SPACE_RE = re.compile(r"\s+")


def _normalize_phrase(s: str) -> str:
    # Lowercase, strip punctuation, and collapse whitespace, so a spoken
    # "Start my Morning Brief!" (or an STT transcript with odd
    # capitalization/stray punctuation) still matches a trigger phrase
    # saved as plain text.
    s = s.lower().strip()
    s = _PUNCT_RE.sub("", s)
    s = _SPACE_RE.sub(" ", s)
    return s.strip()


def match_voice_trigger(text: str) -> Optional[dict[str, Any]]:
    # Return the (enabled) automation whose voice_trigger phrase matches
    # this text, or None. Matching is normalized (case/punctuation-
    # insensitive) and tolerant of trailing words -- an STT transcript
    # often has filler or trailing noise ("start my morning brief
    # please"), so a match requires the trigger phrase to appear at the
    # very START of the utterance (exact match, or immediately followed
    # by a word boundary), never merely mentioned somewhere inside a
    # longer, unrelated sentence -- "start my morning briefing on today's
    # news" should NOT fire a trigger phrase of "start my morning brief"
    # followed by unrelated text with no word boundary between them.
    #
    # This intentionally also fires for typed text, not just voice -- the
    # backend never distinguishes the two (see main.py's ws_endpoint),
    # and there's no reason typing the exact trigger phrase shouldn't
    # work too.
    norm_text = _normalize_phrase(text)
    if not norm_text:
        return None
    tasks = _load()
    for task_id, task in tasks.items():
        if not task.get("enabled"):
            continue
        trigger = (task.get("voice_trigger") or "").strip()
        if not trigger:
            continue
        norm_trigger = _normalize_phrase(trigger)
        if not norm_trigger:
            continue
        if norm_text == norm_trigger or norm_text.startswith(norm_trigger + " "):
            return {**task, "id": task_id}
    return None


def _text(s: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": s}]}


@tool(
    "list_automations",
    "The single source of truth for every automation (scheduled background task) "
    "actually configured on this machine right now -- name, cron schedule, the "
    "prompt it runs, whether it's enabled, its voice/text trigger phrase (if any), "
    "and when it last ran / will next run. ALWAYS call this -- never a generic/"
    "unrelated cron or scheduler tool, and never a guess from memory -- whenever "
    "the user asks what automations, scheduled tasks, or routines exist, whether "
    "something specific (like a daily weather briefing) is set up, what phrase "
    "triggers one, or anything else about Neural's own schedule.",
    {},
)
async def list_automations_tool(args: dict) -> dict[str, Any]:
    tasks = list_automations()
    if not tasks:
        return _text("No automations are configured on this machine right now.")
    lines = []
    for t in tasks:
        name = t.get("name", "?")
        status = "enabled" if t.get("enabled") else "disabled"
        next_run = t.get("next_run") or ("not scheduled" if not t.get("enabled") else "unknown")
        last_run = t.get("last_run") or "never run yet"
        cron = t.get("cron", "?")
        prompt = t.get("prompt", "?")
        trigger = t.get("voice_trigger") or None
        trigger_note = f", voice trigger: \"{trigger}\"" if trigger else ", no voice trigger set"
        lines.append(
            "- \"" + name + "\" (" + status + ") -- schedule: " + repr(cron)
            + ", prompt: " + repr(prompt) + trigger_note
            + ", next run: " + str(next_run) + ", last run: " + str(last_run)
        )
    return _text("Configured automations:\n" + "\n".join(lines))


@tool(
    "run_automation_now",
    "Manually trigger one of the user's saved automations to run immediately, by "
    "name -- for when the user asks you (in natural language, not necessarily the "
    "exact configured voice/text trigger phrase -- e.g. \"kick off my newsletter "
    "thing\") to run, start, or trigger one of their existing automations right "
    "now. Look the name up with list_automations first if you're not sure of the "
    "exact name -- this matches by exact name first, then falls back to a loose "
    "substring match. This starts the run in the background and returns right "
    "away -- the automation's own real output (and any errors) follows shortly "
    "afterward as a normal chat message, spoken aloud, exactly like a scheduled "
    "run would. Don't try to fulfill the automation's task yourself in this same "
    "turn -- just confirm you're starting it.",
    {"name": str},
)
async def run_automation_now_tool(args: dict) -> dict[str, Any]:
    name = (args.get("name") or "").strip()
    if not name:
        return _text("Need the automation's name to run it.")
    tasks = _load()
    match_id = next(
        (tid for tid, t in tasks.items() if (t.get("name") or "").strip().lower() == name.lower()),
        None,
    )
    if not match_id:
        # Loose fallback so "morning brief" still finds an automation
        # literally named "Morning Briefing".
        match_id = next(
            (tid for tid, t in tasks.items() if name.lower() in (t.get("name") or "").strip().lower()),
            None,
        )
    if not match_id:
        return _text(f'No automation named "{name}" found. Use list_automations to see what exists.')
    real_name = tasks[match_id]["name"]
    asyncio.create_task(run_automation(match_id))
    return _text(f'Started "{real_name}" -- its result will follow shortly as a normal message.')


AUTOMATIONS_TOOLS = ["list_automations", "run_automation_now"]
automations_server = create_sdk_mcp_server(
    name="automations", version="1.0.0", tools=[list_automations_tool, run_automation_now_tool]
)


def create_automation(
    name: str,
    cron: str,
    prompt: str,
    enabled: bool = True,
    voice_trigger: Optional[str] = None,
) -> dict[str, Any]:
    tasks = _load()
    task_id = uuid.uuid4().hex[:8]
    tasks[task_id] = {
        "name": name,
        "cron": cron,
        "prompt": prompt,
        "enabled": enabled,
        "voice_trigger": (voice_trigger or "").strip() or None,
        "last_run": None,
        "last_result": None,
    }
    _save(tasks)
    if enabled:
        _schedule(task_id, tasks[task_id])
    return {**tasks[task_id], "id": task_id}


def delete_automation(task_id: str) -> bool:
    tasks = _load()
    if task_id not in tasks:
        return False
    del tasks[task_id]
    _save(tasks)
    if scheduler.get_job(task_id):
        scheduler.remove_job(task_id)
    return True


def toggle_automation(task_id: str, enabled: bool) -> Optional[dict[str, Any]]:
    tasks = _load()
    if task_id not in tasks:
        return None
    tasks[task_id]["enabled"] = enabled
    _save(tasks)
    if enabled:
        _schedule(task_id, tasks[task_id])
    elif scheduler.get_job(task_id):
        scheduler.remove_job(task_id)
    return {**tasks[task_id], "id": task_id}


async def run_automation(task_id: str) -> None:
    tasks = _load()
    task = tasks.get(task_id)
    if not task:
        return
    name = task["name"]
    bus.publish({"type": "automation_run", "name": name, "status": "started", "id": task_id})

    result_text = ""
    error = None
    try:
        # isolated=True: see agent.py's build_options for why -- a plain
        # new_client() here once let the model reach for an unrelated
        # "schedule a cloud agent" capability instead of just doing the
        # task, because it inherits every skill/plugin the user's own
        # `claude` CLI has installed. wrap_for_automation() is the second,
        # prompt-level layer of the same fix: it tells the model this
        # prompt's "every morning at 8am..." phrasing describes a routine
        # that's ALREADY scheduled and running right now, not a new
        # scheduling request.
        client = new_client(isolated=True)
        await client.connect()
        try:
            await client.query(wrap_for_automation(task["prompt"]))
            async for msg in client.receive_response():
                # Only AssistantMessage/TextBlock is the model's own final
                # answer. The previous version here summed getattr(block,
                # "text", None) across every message type without checking
                # what kind of block it was -- which, live, ended up
                # concatenating unrelated tool/skill content (e.g. a loaded
                # skill's own instructions) into "last_result" right along
                # with (or instead of) the real answer.
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            result_text += block.text
                        elif isinstance(block, ToolUseBlock):
                            bus.publish(
                                {
                                    "type": "tool_use",
                                    "tool": block.name,
                                    "input": block.input,
                                    "source": f"automation:{name}",
                                }
                            )
        finally:
            await client.disconnect()
    except Exception as exc:  # noqa: BLE001 -- surface any failure to the feed instead of crashing the scheduler
        error = str(exc)

    result_text = result_text.strip()

    tasks = _load()
    if task_id in tasks:
        tasks[task_id]["last_run"] = datetime.now(timezone.utc).isoformat()
        tasks[task_id]["last_result"] = error or (result_text[:500] or "(no text output)")
        _save(tasks)

    bus.publish(
        {
            "type": "automation_run",
            "name": name,
            "status": "error" if error else "completed",
            "id": task_id,
            "result": (error or result_text)[:500],
        }
    )

    # Deliver the actual result into the normal chat pipeline -- written
    # AND spoken, exactly like an interactive reply -- regardless of
    # whether this run fired on its own cron schedule, was started
    # manually from the UI's "run now" button, or was kicked off by a
    # voice/text trigger phrase or the run_automation_now tool. Lazy
    # import: main.py imports this module at its own top level, so
    # importing back at this module's top would be circular; by the time
    # any run actually happens the app has finished starting and main is
    # fully loaded.
    from .main import deliver_assistant_message

    if error:
        spoken = f'Your "{name}" automation hit an error: {error[:300]}'
    else:
        spoken = result_text or f'Your "{name}" automation just finished, but produced no output.'
    try:
        await deliver_assistant_message(spoken, source=f"automation:{name}")
    except Exception as exc:  # noqa: BLE001 -- delivery failing shouldn't crash the scheduler
        logger.error("Failed to deliver automation result to chat: %s", exc)


def start_scheduler() -> None:
    """Load persisted tasks, schedule the enabled ones, and start the
    scheduler. Call once at app startup."""
    tasks = _load()
    for task_id, task in tasks.items():
        if task.get("enabled"):
            _schedule(task_id, task)
    if not scheduler.running:
        scheduler.start()
