"""
Managed background jobs -- long work that runs while Neural keeps talking.

The problem: chat runs on ONE shared Claude session per mode. While it was
busy with a five-minute browser task or a render, Neural couldn't take
another turn -- and talking to her (barge-in, or just sending a new
message) interrupted and threw away the work. Earlier attempts at
"background" work went through detached shell jobs or Claude's Task
workers, which escaped every lifecycle Neural tracks and kept old plans
alive into unrelated later turns; those stay blocked (agent.py's
_foreground_tool_guard).

This is the managed version. The chat agent calls start_background_job
with a title and self-contained instructions; that starts a separate,
isolated Claude session (the same kind automations use) that:
  - is tracked here (list/cancel tools, live activity feed, ops panel),
  - has a hard time limit and an idle limit, and on timeout or cancel
    kills its own ffmpeg/Cap/video processes (process_watchdog snapshots
    of THIS client's process tree only),
  - can't start jobs of its own (the job tools aren't given to workers),
  - is capped at config.MAX_BACKGROUND_JOBS running at once,
  - delivers its result exactly like an automation does: written into
    chat, spoken aloud, pushed to the phone, AND noted into the chat
    agent's next turn so the conversation knows what happened.
Interrupting Neural's speech never touches a running job.

run_isolated() is also what automations now use, which gives them the
time limits and process cleanup they never had (a stuck automation used
to hang forever).
"""

import asyncio
import logging
import time
import uuid
from typing import Any, Awaitable, Callable, Optional

from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    create_sdk_mcp_server,
    tool,
)

from . import config, process_watchdog
from .events import bus
from .mood import extract_mood

logger = logging.getLogger("neural.background")

_jobs: dict = {}
_result_handler: "Optional[Callable[[dict], Awaitable[None]]]" = None
_HISTORY_KEEP = 20

JOB_PREFIX = (
    '[Background job "{title}" -- started by Neural (you) from the chat on the user\'s behalf. The '
    "user isn't watching this run and can't answer questions, so do the whole task now, end to end, "
    "with your tools, making sensible assumptions (mention any you made). Only send, post, publish, "
    "deploy, buy, or delete something if the instructions below explicitly say the user already "
    "approved that exact action. If the browser won't start because its profile is already in use, "
    "wait about 20 seconds and try once more before giving up. When you're done, reply with a short "
    "spoken-style summary of the outcome for the user -- 1-4 plain sentences, no markdown or lists, "
    "starting with a [mood:...] tag.]\n\nInstructions:\n"
)


def set_result_handler(fn: "Callable[[dict], Awaitable[None]]") -> None:
    """main.py registers how finished jobs are delivered to the chat."""
    global _result_handler
    _result_handler = fn


async def _iterate(client, idle_seconds: float, deadline: float):
    it = client.receive_response().__aiter__()
    loop = asyncio.get_running_loop()
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise asyncio.TimeoutError()
        try:
            msg = await asyncio.wait_for(it.__anext__(), timeout=min(idle_seconds, remaining))
        except StopAsyncIteration:
            return
        yield msg


def _span(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 60} minutes" if seconds >= 120 else f"{seconds} seconds"


def _snapshot(client) -> list:
    try:
        return process_watchdog.snapshot_client_render_processes(client)
    except Exception as exc:  # noqa: BLE001 -- the periodic reaper remains the fallback
        logger.error("Couldn't snapshot worker processes: %s", exc)
        return []


async def run_isolated(
    prompt: str,
    source: str,
    max_seconds: float,
    idle_seconds: float,
    job: Optional[dict] = None,
) -> "tuple[str, Optional[str]]":
    """One-shot isolated Claude session with hard limits. Returns
    (assistant text, error or None). Raises CancelledError if cancelled
    (after cleaning up)."""
    from .agent import new_client  # lazy: agent.py imports this module

    client = new_client(isolated=True)
    if job is not None:
        job["client"] = client
    text = ""
    error: Optional[str] = None
    leftovers: list = []
    try:
        await client.connect()
        await client.query(prompt)
        deadline = asyncio.get_running_loop().time() + max_seconds
        async for msg in _iterate(client, idle_seconds, deadline):
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        text += block.text
                    elif isinstance(block, ToolUseBlock):
                        bus.publish({"type": "tool_use", "tool": block.name, "input": block.input, "source": source})
            elif isinstance(msg, ResultMessage) and getattr(msg, "is_error", False) and not text:
                error = str(getattr(msg, "result", "") or "the run ended with an error")
    except asyncio.TimeoutError:
        error = f"stopped -- it ran longer than {_span(max_seconds)} or made no progress for {_span(idle_seconds)}"
        leftovers = _snapshot(client)
    except asyncio.CancelledError:
        leftovers = _snapshot(client)
        raise
    finally:
        try:
            await client.disconnect()
        except Exception:  # noqa: BLE001
            pass
        if job is not None:
            job["client"] = None
        if leftovers:
            try:
                await asyncio.to_thread(process_watchdog.kill_snapshotted_processes, leftovers)
            except Exception as exc:  # noqa: BLE001
                logger.error("Worker process cleanup failed: %s", exc)
    return text, error


def running_jobs() -> list:
    return [j for j in _jobs.values() if j["status"] == "running"]


def _public(job: dict) -> dict:
    return {k: v for k, v in job.items() if k not in ("task", "client")}


def _trim_history() -> None:
    finished = sorted((j for j in _jobs.values() if j["status"] != "running"), key=lambda j: j.get("finished") or 0)
    for j in finished[:-_HISTORY_KEEP]:
        _jobs.pop(j["id"], None)


async def _run_job(job: dict) -> None:
    title = job["title"]
    bus.publish({"type": "background_job", "id": job["id"], "name": title, "status": "started"})
    text, error, status = "", None, "completed"
    try:
        text, error = await run_isolated(
            JOB_PREFIX.format(title=title.replace('"', "'")) + job["instructions"],
            source=f"job:{title}",
            max_seconds=config.JOB_MAX_SECONDS,
            idle_seconds=config.JOB_IDLE_TIMEOUT_SECONDS,
            job=job,
        )
        status = "error" if error else "completed"
    except asyncio.CancelledError:
        error, status = "cancelled", "cancelled"
    except Exception as exc:  # noqa: BLE001 -- a failed job must never crash the backend
        logger.exception("Background job %s failed", job["id"])
        error, status = str(exc) or exc.__class__.__name__, "error"

    clean, mood = extract_mood(text)
    job.update(
        status=status,
        finished=time.time(),
        result=clean.strip()[:2000],
        error=error,
        mood=mood,
    )
    bus.publish({
        "type": "background_job", "id": job["id"], "name": title, "status": status,
        "result": (error if status != "completed" else job["result"])[:500],
    })
    if _result_handler is not None:
        try:
            await _result_handler(_public(job))
        except Exception as exc:  # noqa: BLE001
            logger.error("Delivering background job result failed: %s", exc)
    _trim_history()


def start_job(title: str, instructions: str) -> dict:
    title = " ".join((title or "").split())[:80] or "Background task"
    instructions = (instructions or "").strip()
    if not instructions:
        return {"ok": False, "error": "instructions are empty"}
    running = running_jobs()
    if len(running) >= config.MAX_BACKGROUND_JOBS:
        names = ", ".join(f'"{j["title"]}" ({j["id"]})' for j in running)
        return {"ok": False, "error": f"already running {len(running)} job(s): {names}"}
    job = {
        "id": uuid.uuid4().hex[:6],
        "title": title,
        "instructions": instructions,
        "status": "running",
        "started": time.time(),
        "finished": None,
        "result": "",
        "error": None,
        "mood": None,
        "client": None,
    }
    _jobs[job["id"]] = job
    job["task"] = asyncio.create_task(_run_job(job))
    job["task"].add_done_callback(lambda t, j=job: _finalize_if_cancelled_early(j, t))
    return {"ok": True, "job": _public(job)}


def _finalize_if_cancelled_early(job: dict, task: "asyncio.Task") -> None:
    """A job cancelled before its task ever started running never reaches
    _run_job's own cancellation handling -- without this it would show as
    'running' forever and keep occupying a concurrency slot."""
    if not task.cancelled() or job["status"] != "running":
        return
    job.update(status="cancelled", finished=time.time(), error="cancelled", client=None)
    bus.publish({"type": "background_job", "id": job["id"], "name": job["title"], "status": "cancelled", "result": "cancelled"})
    if _result_handler is not None:
        try:
            asyncio.get_running_loop().create_task(_result_handler(_public(job)))
        except RuntimeError:
            pass


def cancel_job(job_id: str) -> bool:
    job = _jobs.get((job_id or "").strip())
    if not job or job["status"] != "running" or not job.get("task"):
        return False
    job["task"].cancel()
    return True


async def shutdown() -> None:
    tasks = [j["task"] for j in running_jobs() if j.get("task")]
    for t in tasks:
        t.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


# ---------------------------------------------------------------- tools


def _text(s: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": s}]}


def _ago(ts: float) -> str:
    secs = int(time.time() - ts)
    return f"{secs // 60}m{secs % 60:02d}s" if secs >= 60 else f"{secs}s"


@tool(
    "start_background_job",
    "Run a long task (more than ~1 minute of tool work: multi-step browsing, a video edit/render, "
    "multi-site research, batch file work) in a separate worker so the conversation stays free. "
    "The worker can't see this chat: instructions must be complete and self-contained. Its result "
    "is shown and spoken to the user automatically when done -- don't poll it.",
    {"title": str, "instructions": str},
)
async def start_background_job(args: dict) -> dict[str, Any]:
    res = start_job(args.get("title", ""), args.get("instructions", ""))
    if not res["ok"]:
        return _text(f"Couldn't start it: {res['error']}. Tell the user, or offer to cancel one first.")
    job = res["job"]
    return _text(
        f'Started background job {job["id"]} ("{job["title"]}"). It runs on its own; the user will see and hear '
        "the result when it finishes, and you'll get a note about it on your next turn. Tell the user in one "
        "short sentence that you're on it, then carry on with the conversation."
    )


@tool("list_background_jobs", "List running and recently finished background jobs.", {})
async def list_background_jobs(args: dict) -> dict[str, Any]:
    if not _jobs:
        return _text("No background jobs yet.")
    lines = []
    for j in sorted(_jobs.values(), key=lambda j: j["started"], reverse=True):
        if j["status"] == "running":
            lines.append(f'- {j["id"]} "{j["title"]}": running for {_ago(j["started"])}')
        else:
            detail = j["error"] if j["status"] != "completed" else j["result"]
            lines.append(f'- {j["id"]} "{j["title"]}": {j["status"]} {_ago(j["finished"])} ago -- {(detail or "")[:200]}')
    return _text("\n".join(lines))


@tool("cancel_background_job", "Stop a running background job by its id.", {"job_id": str})
async def cancel_background_job(args: dict) -> dict[str, Any]:
    ok = cancel_job(args.get("job_id", ""))
    return _text("Cancelling it now." if ok else "No running job with that id.")


BACKGROUND_TOOLS = ["start_background_job", "list_background_jobs", "cancel_background_job"]
background_server = create_sdk_mcp_server(
    name="background",
    version="1.0.0",
    tools=[start_background_job, list_background_jobs, cancel_background_job],
)


BACKGROUND_PROMPT = (
    "Background jobs: for anything that will clearly take more than about a minute of tool work -- a "
    "multi-step browser task, a video edit or render, research across several sites, a batch of files -- "
    "don't keep the user waiting on the line: call start_background_job with a short title and complete, "
    "self-contained instructions (the worker can't see this conversation, so include every detail it needs: "
    "names, links, file paths, and the exact wording the user approved), then tell them in one short sentence "
    "that it's running. Keep talking normally meanwhile. The result is shown and spoken to the user "
    "automatically when it finishes and noted to you on your next turn -- never poll or bring it up "
    "unprompted. Only include an action that sends, posts, publishes, deploys, buys, or deletes something "
    "after the user explicitly approved that exact action, and say so in the instructions. If the job needs "
    "the browser and you've used the browser in this conversation, call browser_close first, and don't use "
    "the browser yourself until the job finishes (you share one browser profile). Quick things (one or two "
    "tool calls) you just do yourself. Use list_background_jobs / cancel_background_job when the user asks "
    "about or wants to stop one."
)
