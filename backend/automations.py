"""
Cron-style automations: scheduled background agent runs. Task definitions
(name/cron/prompt/enabled/last_run/last_result) persist to a small JSON
file so they survive restarts; APScheduler's AsyncIOScheduler handles the
actual timing. Each run publishes its progress to the live activity feed
(events.py) tagged with an "automation:<name>" source, so it shows up
alongside interactive chat activity without being mistaken for it.
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from claude_agent_sdk import create_sdk_mcp_server, tool

from .agent import new_client
from .events import bus

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


def _text(s: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": s}]}


@tool(
    "list_automations",
    "The single source of truth for every automation (scheduled background task) "
    "actually configured on this machine right now -- name, cron schedule, the "
    "prompt it runs, whether it's enabled, and when it last ran / will next run. "
    "ALWAYS call this -- never a generic/unrelated cron or scheduler tool, and "
    "never a guess from memory -- whenever the user asks what automations, "
    "scheduled tasks, or routines exist, whether something specific (like a daily "
    "weather briefing) is set up, or anything else about Neural's own schedule.",
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
        lines.append(
            "- \"" + name + "\" (" + status + ") -- schedule: " + repr(cron)
            + ", prompt: " + repr(prompt) + ", next run: " + str(next_run)
            + ", last run: " + str(last_run)
        )
    return _text("Configured automations:\n" + "\n".join(lines))


AUTOMATIONS_TOOLS = ["list_automations"]
automations_server = create_sdk_mcp_server(
    name="automations", version="1.0.0", tools=[list_automations_tool]
)


def create_automation(name: str, cron: str, prompt: str, enabled: bool = True) -> dict[str, Any]:
    tasks = _load()
    task_id = uuid.uuid4().hex[:8]
    tasks[task_id] = {
        "name": name,
        "cron": cron,
        "prompt": prompt,
        "enabled": enabled,
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
        client = new_client()
        await client.connect()
        try:
            await client.query(task["prompt"])
            async for msg in client.receive_response():
                for block in getattr(msg, "content", []) or []:
                    text = getattr(block, "text", None)
                    if text:
                        result_text += text
                    tool_name = getattr(block, "name", None)
                    if tool_name:
                        bus.publish(
                            {
                                "type": "tool_use",
                                "tool": tool_name,
                                "input": getattr(block, "input", {}),
                                "source": f"automation:{name}",
                            }
                        )
        finally:
            await client.disconnect()
    except Exception as exc:  # noqa: BLE001 -- surface any failure to the feed instead of crashing the scheduler
        error = str(exc)

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


def start_scheduler() -> None:
    """Load persisted tasks, schedule the enabled ones, and start the
    scheduler. Call once at app startup."""
    tasks = _load()
    for task_id, task in tasks.items():
        if task.get("enabled"):
            _schedule(task_id, task)
    if not scheduler.running:
        scheduler.start()
