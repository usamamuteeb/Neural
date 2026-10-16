"""
Windows fix: uvicorn's --reload supervisor can leave the child process on
asyncio's SelectorEventLoop instead of ProactorEventLoop. Selector loops
cannot spawn subprocesses on Windows at all (raises NotImplementedError),
and the Agent SDK spawns the `claude` CLI as a subprocess -- so without
this, every connection fails with CLIConnectionError. Must run before
anything else touches the event loop.
"""
import sys

if sys.platform == "win32":
    import asyncio

    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import asyncio
import base64
import json
import logging
import re
from typing import Optional

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeSDKClient,
    ResultMessage,
    StreamEvent,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import attachments, automations, background, backup, config, media, observability, process_watchdog, push, session_state, stt, tts, utility
from .mood import extract_mood
from .speech_budget import SpeechBudget
from .speech_stream import TextStreamer
from .agent import new_client, wrap_for_voice
from .events import bus

# Without this, Python's logging defaults to WARNING at the root logger,
# so every logger.info() call in this codebase (including the push-
# notification diagnostics added below) is silently swallowed -- never
# printed anywhere, not even to this terminal -- and logger.error() calls
# only happen to appear via Python's fallback "no handler configured"
# last-resort handler. That silence is exactly what made "alerts aren't
# working" impossible to diagnose: nothing was actually surfacing whether
# push sends were succeeding, failing, or never firing at all. uvicorn's
# own default logging config doesn't set this up for anything outside its
# own "uvicorn.*" loggers, so it has to happen here.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

logger = logging.getLogger("neural.main")

app = FastAPI(title="Neural")

# One shared agent client per MODE ("chat" or "tutor"), not one per
# websocket.
#
# Originally each /ws connection got its own ClaudeSDKClient from a
# pre-warmed pool, so the agent's conversation context started fresh
# every time -- which meant desktop and mobile (or even two tabs) felt
# like talking to two different, disconnected instances of Neural with
# no shared memory of what was just discussed. A single shared client per
# mode makes every device in that SAME mode part of the same ongoing
# conversation, which is what "the same Neural" should feel like
# regardless of which screen you're using -- while keeping chat and Tutor
# mode's conversations, memory, and history completely separate from
# each other (see backend/tutor.py's module docstring for why that
# separation matters). Tutor's entry starts out None and stays that way
# until the user actually opens Tutor mode -- unlike chat, it's not
# pre-warmed at startup, since not every run of Neural will ever touch
# it (see ws_endpoint's _handle_set_mode for where it's warmed instead).
#
# Each mode's own lock in _chat_locks serializes actual turns (and
# connect/reconnect) within that mode so two devices sending messages to
# the SAME mode at the same moment can't interleave into the same
# in-flight query -- the second one just waits its turn, same as if one
# person were juggling two devices. Fine for a personal, single-user
# assistant. Chat and Tutor mode turns never block each other, since
# they're different locks entirely.
#
# Spinning up a client is slow -- it boots the `claude` CLI as a
# subprocess and (for chat mode) launches the Playwright MCP server
# through npx, which together took ~20 seconds when measured. Chat
# mode's cost lands once, at server startup (kicked off in the
# background so it doesn't block static-file/API serving); Tutor mode's
# lands once, the first time anyone opens Tutor mode.
_shared_clients: dict[str, "ClaudeSDKClient | None"] = {"chat": None, "tutor": None}
_chat_locks: dict[str, asyncio.Lock] = {"chat": asyncio.Lock(), "tutor": asyncio.Lock()}

# Set by an explicit "interrupt" request from any connection (see
# ws_endpoint's _reader) so _run_turn can shrink its own patience for a
# turn that's ALREADY supposed to be stopping, instead of only ever
# giving up after the full TURN_IDLE_TIMEOUT_SECONDS of true silence --
# see config.POST_INTERRUPT_GRACE_SECONDS and _iter_with_timeout's
# docstring for why client.interrupt() alone isn't enough to bound a
# turn stuck inside a long tool call. Cleared at the start of every new
# turn (see _run_turn) so a past interrupt never carries over and
# shortens a LATER, unrelated turn's patience.
_interrupt_events: dict[str, asyncio.Event] = {"chat": asyncio.Event(), "tutor": asyncio.Event()}

# How many currently-connected websocket tabs/devices have Tutor mode
# open right now, across the whole app -- not per connection. This is
# what defines a tutoring "session" boundary (see tutor.py's module
# docstring): it only reaches 0 when EVERY device has left Tutor mode,
# which is the moment _end_tutor_session below actually tears the shared
# tutor client down (after asking it to summarize itself first) rather
# than leaving a session other tabs are still using half-closed.
_tutor_active_count = 0


async def _new_client_resuming(mode: str) -> ClaudeSDKClient:
    """Build and connect a client for `mode`, resuming its last known-good
    session (see session_state.py) so a restart -- planned, or forced by
    _run_turn's idle-timeout recovery below -- picks the SAME underlying
    conversation back up instead of starting from zero. Falls back to a
    brand-new session if the saved one can't be resumed (a deleted/
    corrupted transcript, or any other reason the CLI rejects it) --
    continuity is a nice-to-have, a working Neural isn't optional."""
    saved = session_state.get_session_id(mode)
    client = new_client(mode=mode, resume=saved)
    try:
        await client.connect()
        return client
    except Exception as e:
        if not saved:
            raise
        logger.error(
            "Couldn't resume %s's saved session %s (%s) -- starting a fresh session instead.",
            mode, saved, e,
        )
        session_state.clear_session_id(mode)
        client = new_client(mode=mode)
        await client.connect()
        return client


async def _warm_shared_client(mode: str) -> None:
    lock = _chat_locks[mode]
    async with lock:
        if _shared_clients[mode] is not None:
            return
        try:
            _shared_clients[mode] = await _new_client_resuming(mode)
        except Exception as e:  # noqa: BLE001 -- warming is best effort
            logger.error("Failed to pre-warm the shared %s client: %s", mode, e)


async def _iter_with_timeout(
    aiter,
    timeout: float,
    interrupt_event: "asyncio.Event | None" = None,
    grace_seconds: float = 20,
    poll_interval: float = 1.0,
    overall_deadline: "float | None" = None,
):
    """Wrap an async iterator so a stalled upstream can't block forever.
    Used by _run_turn to bound how long it waits for the NEXT message
    from an in-progress turn (see config.TURN_IDLE_TIMEOUT_SECONDS).
    Raises asyncio.TimeoutError if nothing arrives within `timeout`
    seconds of the previous item (or of starting) -- OR if `overall_deadline`
    (an absolute asyncio loop-clock deadline) is reached, whichever comes
    first.

    Found live (Oct 2026): `timeout` alone only measures the GAP between
    messages, so a tool call that keeps producing periodic output --
    progress notifications from a video render/export, for instance --
    never looks "idle" no matter how long it runs for or whether it will
    ever actually finish; it would run forever, holding whatever lock the
    caller holds for the whole time. `overall_deadline` is the fix: a hard
    ceiling on the TOTAL time this generator is allowed to keep running,
    independent of how recently the last item arrived. See
    config.TURN_MAX_DURATION_SECONDS and _run_turn below, which computes
    this once per turn and passes it through.

    If `interrupt_event` is given, waiting for that NEXT item is polled
    in short `poll_interval` slices (via asyncio.shield, so the real
    underlying wait is never itself cancelled/restarted -- only this
    function's OWN patience for it is re-evaluated each slice) instead of
    one long wait_for. The moment the event becomes set, one fixed
    `grace_seconds` deadline is started and carried across *every* later
    progress message. This is important for video tools: progress output
    must not keep renewing an interrupt timeout forever.
    overall_deadline, when set, can only ever shrink this further, never
    extend past it -- a user interrupt still can't buy MORE time than the
    turn's own absolute ceiling already allows."""
    it = aiter.__aiter__()
    interrupt_deadline: float | None = None
    while True:
        next_task = asyncio.ensure_future(it.__anext__())
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        if (
            interrupt_event is not None
            and interrupt_event.is_set()
            and interrupt_deadline is None
        ):
            interrupt_deadline = loop.time() + grace_seconds
        if interrupt_deadline is not None and interrupt_deadline < deadline:
            deadline = interrupt_deadline
        if overall_deadline is not None and overall_deadline < deadline:
            deadline = overall_deadline
        try:
            if interrupt_event is None:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise asyncio.TimeoutError()
                item = await asyncio.wait_for(next_task, timeout=remaining)
            else:
                while True:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        raise asyncio.TimeoutError()
                    try:
                        item = await asyncio.wait_for(
                            asyncio.shield(next_task), timeout=min(remaining, poll_interval)
                        )
                        break
                    except asyncio.TimeoutError:
                        if interrupt_event.is_set() and interrupt_deadline is None:
                            interrupt_deadline = loop.time() + grace_seconds
                        if interrupt_deadline is not None and interrupt_deadline < deadline:
                            deadline = interrupt_deadline
                        continue
        except asyncio.TimeoutError:
            next_task.cancel()
            raise
        except StopAsyncIteration:
            return
        yield item


async def _run_turn(text: str, mode: str = "chat"):
    """Run one chat turn on the shared client for this mode, serialized
    against every other device/tab using the SAME mode (chat and tutor
    turns never block each other -- separate locks). Lazily (re)connects
    if that mode's shared client doesn't exist yet or died. Returns the
    async iterator of SDK messages for the caller to stream to its own
    websocket."""
    lock = _chat_locks[mode]
    async with lock:
        # Always define this before querying. A normal query that later
        # times out used to reach the recovery path below without a
        # snapshot, so its ffmpeg/Cap child could survive the reset.
        render_processes: list[dict] = []
        client = _shared_clients[mode]
        if client is None:
            client = await _new_client_resuming(mode)
            _shared_clients[mode] = client
        # A past interrupt (meant for whatever turn was previously running
        # on this mode's shared client) must never carry over and shorten
        # THIS brand-new turn's patience -- see _interrupt_events' own
        # comment above.
        _interrupt_events[mode].clear()
        try:
            await client.query(text)
        except Exception as e:
            logger.error("Shared %s client query failed, reconnecting: %s", mode, e)
            # Take an ownership snapshot while this client's ``claude``
            # subprocess still exists.  The immediate cleanup after
            # disconnect must never sweep every young ffmpeg/cap/node process
            # in the project: Tutor mode and scheduled automations may have a
            # separate, healthy render running at the same time.
            try:
                render_processes = process_watchdog.snapshot_client_render_processes(client)
            except Exception as exc:  # noqa: BLE001 -- conservative reaper remains the fallback
                logger.error("Could not snapshot timed-out client's render processes: %s", exc)
                render_processes = []

            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001
                pass
            client = await _new_client_resuming(mode)
            _shared_clients[mode] = client
            await client.query(text)

        turn_deadline = asyncio.get_running_loop().time() + config.TURN_MAX_DURATION_SECONDS
        try:
            async for msg in _iter_with_timeout(
                client.receive_response(),
                config.TURN_IDLE_TIMEOUT_SECONDS,
                interrupt_event=_interrupt_events[mode],
                grace_seconds=config.POST_INTERRUPT_GRACE_SECONDS,
                overall_deadline=turn_deadline,
            ):
                if (
                    isinstance(msg, ResultMessage)
                    and msg.session_id
                    and not _interrupt_events[mode].is_set()
                ):
                    # Only a turn that actually completed reaches here, so
                    # this can never checkpoint a wedged/broken session --
                    # see session_state.py.
                    session_state.save_session_id(mode, msg.session_id)
                yield msg
        except asyncio.TimeoutError:
            hit_overall_ceiling = asyncio.get_running_loop().time() >= turn_deadline - 1
            if _interrupt_events[mode].is_set():
                logger.error(
                    "%s was interrupted but stayed stuck (likely a tool call already "
                    "in flight that client.interrupt() can't preempt -- a recording, "
                    "render, or similar) and didn't wrap up within the %ds grace "
                    "period -- discarding it so the NEXT message gets a working "
                    "client instead of waiting out whatever that tool call was doing.",
                    mode, config.POST_INTERRUPT_GRACE_SECONDS,
                )
            elif hit_overall_ceiling:
                logger.error(
                    "%s turn ran past its %ds absolute ceiling (TURN_MAX_DURATION_SECONDS) "
                    "even though it was still producing output -- a tool call that keeps "
                    "emitting progress but never actually finishes (a wedged render/export/ "
                    "recording is the known case -- see process_watchdog.py) would otherwise "
                    "hold this lock forever since it never looks \"idle\". Discarding it so "
                    "the NEXT message gets a working client instead of queueing behind this "
                    "one indefinitely.",
                    mode, config.TURN_MAX_DURATION_SECONDS,
                )
            else:
                logger.error(
                    "%s produced no output for %ds mid-turn -- treating the session as "
                    "wedged (a stuck tool call, or a `claude` CLI subprocess that stopped "
                    "responding without exiting) and discarding it so the NEXT message "
                    "gets a working client instead of blocking on this same lock forever.",
                    mode, config.TURN_IDLE_TIMEOUT_SECONDS,
                )
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001
                pass
            _shared_clients[mode] = None

            # The SDK/conversation side is now recovered -- but whatever
            # OS process that stuck tool call was waiting on can outlive the
            # `claude` CLI subprocess just disconnected above. Kill only the
            # video descendants snapshotted from THIS client, never another
            # active mode or automation's render. The periodic watchdog still
            # catches genuinely old orphans that could not be snapshotted.
            try:
                killed = await asyncio.to_thread(
                    process_watchdog.kill_snapshotted_processes, render_processes
                )
                for item in killed:
                    bus.publish({"type": "watchdog_kill", "mode": mode, **item})
            except Exception as exc:  # noqa: BLE001 -- the reap is a bonus, never let it block recovery
                logger.error("process_watchdog sweep failed: %s", exc)

            raise RuntimeError(
                "That took too long to respond, so I had to reset this session. "
                "Please try again -- if it just spoke, that reply may have been cut short. "
                "If it was rendering or recording something, that job has been stopped -- "
                "it won't keep running in the background."
            ) from None


def _chat_history(mode: str = "chat", limit: int = 40) -> list[dict]:
    """Recent user/assistant turns from the shared EventBus history for
    ONE mode, so a device that just opened the app (or just switched into
    that mode) sees the same recent conversation another device already
    had in that same mode -- visual parity to match the shared agent
    context above. Messages are tagged with their mode when published
    (see ws_endpoint and deliver_assistant_message below); anything
    published before this existed has no "mode" key and is treated as
    "chat" so old history isn't silently dropped.
    """
    return [
        e
        for e in bus.history()
        if e.get("type") == "message" and e.get("mode", "chat") == mode
    ][-limit:]


async def _end_tutor_session() -> None:
    """Runs once the LAST device/tab has left Tutor mode (see
    ws_endpoint's set_mode handling and disconnect cleanup below): asks
    the tutor's own shared client to summarize the session it just had
    (see tutor.SESSION_END_INSTRUCTION) and save that summary + any
    progress update via its own memory tools, then disconnects it and
    clears _shared_clients["tutor"] so the NEXT time someone opens Tutor
    mode, a fresh client is built with a fresh system prompt that reads
    back exactly what was just saved (see tutor.build_tutor_system_prompt).

    Debounced by a short sleep + a re-check of _tutor_active_count: if
    the user left and immediately came back (a stray double-tap, or
    switching modes twice in quick succession), the count will be back
    above 0 by the time this actually runs, and it backs off entirely
    rather than summarizing and tearing down a session the user is
    already back in.
    """
    await asyncio.sleep(2.0)
    if _tutor_active_count != 0:
        return

    lock = _chat_locks["tutor"]
    async with lock:
        if _tutor_active_count != 0:
            return
        client = _shared_clients["tutor"]
        if client is None:
            return
        summary_text = ""
        try:
            from .tutor import SESSION_END_INSTRUCTION

            await client.query(SESSION_END_INSTRUCTION)
            async for msg in _iter_with_timeout(
                client.receive_response(), config.TURN_IDLE_TIMEOUT_SECONDS
            ):
                if isinstance(msg, ResultMessage) and msg.session_id:
                    # Captured even though this client is about to be torn
                    # down below -- so the NEXT time Tutor mode opens, its
                    # fresh client can resume THIS conversation instead of
                    # starting blank (see session_state.py).
                    session_state.save_session_id("tutor", msg.session_id)
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            summary_text += block.text
        except Exception as e:  # noqa: BLE001 -- a failed summary shouldn't block closing the session
            logger.error("Failed to summarize/close the tutor session: %s", e)
        finally:
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001
                pass
            _shared_clients["tutor"] = None

    summary_text = extract_mood(summary_text)[0].strip()
    if summary_text:
        # Written straight to history, not broadcast live -- by the time
        # this fires, whoever ended the session has likely already
        # switched away from Tutor mode locally, so there's no tutor chat
        # log currently on screen to append it to. It'll show up as the
        # last message the next time Tutor mode's history loads, which
        # is exactly the continuity tutor.py's docstring describes.
        bus.publish(
            {
                "type": "message",
                "role": "assistant",
                "text": summary_text,
                "source": "tutor_session_end",
                "mode": "tutor",
            }
        )


# Every /ws connection registers its own _send(obj) here for as long as
# it's open, so output that doesn't originate from that connection's own
# turn -- specifically, an automation's result -- can still be pushed to
# every currently-open tab/device live, the same way a live turn's
# assistant/audio events are. See deliver_assistant_message below.
_broadcast_sinks: "set" = set()


async def _broadcast(obj: dict) -> None:
    for sink in list(_broadcast_sinks):
        try:
            await sink(obj)
        except Exception:  # noqa: BLE001 -- one dead/closing socket shouldn't block the rest
            pass


async def deliver_assistant_message(
    text: str, source: str = "automation", mode: str = "chat", mood: Optional[str] = None
) -> None:
    """Deliver a piece of assistant output that didn't come from an
    interactive /ws turn -- right now, only an automation's finished
    result (see automations.py's run_automation) -- through the exact
    same kind of pipeline a live chat turn uses: written into the shared
    chat history (so it shows up next time any device opens or
    reconnects, even if nothing was connected when it happened) and, for
    every device connected right now, shown in the chat log and spoken
    aloud immediately -- the same as a normal reply, whether the
    automation was fired by its cron schedule or started manually.

    Deliberately broadcast under its own automation_message/
    automation_audio/automation_audio_done event types rather than
    reusing assistant/audio/audio_done -- an automation can finish at any
    moment, including while a live turn (especially a manually-triggered
    "run now") is already streaming text/audio on this exact connection,
    and those events are keyed into shared per-turn state (assistantDiv,
    currentTurn's seq counters, isSpeaking) on the frontend. Reusing them
    would risk corrupting whichever one is in flight -- wrong text
    appended into the wrong bubble, audio chunks racing on the same
    sequence counter. A separate event namespace (and, client-side, a
    separate <audio> element and queue) keeps the two completely
    independent instead."""
    text, tag_mood = extract_mood(text)
    mood = mood or tag_mood
    text = text.strip()
    if not text:
        return

    bus.publish({"type": "message", "role": "assistant", "text": text, "source": source, "mode": mode})
    automation_event = {"type": "automation_message", "text": text, "mode": mode}
    if mood:
        automation_event["mood"] = mood
    await _broadcast(automation_event)

    sentences, tail = _split_sentences(text)
    if tail.strip():
        sentences = sentences + [tail]

    audio_seq = 0
    tts_tasks: list = []

    async def _speak(chunk: str, seq: int) -> None:
        result = await tts.synthesize(chunk)
        out: dict = {"type": "automation_audio", "seq": seq}
        if result:
            audio, mime = result
            out["mime"] = mime
            out["data"] = base64.b64encode(audio).decode("ascii")
        else:
            out["data"] = None
        await _broadcast(out)

    auto_budget = SpeechBudget()
    for sentence in sentences:
        sentence = auto_budget.take(sentence)
        if sentence:
            audio_seq += 1
            tts_tasks.append(asyncio.create_task(_speak(sentence, audio_seq)))
    if auto_budget.overflow_note():
        audio_seq += 1
        tts_tasks.append(asyncio.create_task(_speak(auto_budget.overflow_note(), audio_seq)))

    if tts_tasks:
        await asyncio.gather(*tts_tasks)

    await _broadcast({"type": "automation_audio_done", "count": audio_seq})


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_CLAUSE_SPLIT_RE = re.compile(r"(?<=[,;:])\s+")
_MIN_CLAUSE_CHARS = 40  # don't fragment short phrases into clipped-sounding audio


def _split_sentences(buf: str) -> tuple[list[str], str]:
    """Split buf into complete chunks ready for TTS plus a trailing
    incomplete fragment that should stay buffered until more text (or a
    final flush) arrives. Primary split is on sentence-ending punctuation
    (a crude heuristic -- an abbreviation like "Dr." can false-split;
    acceptable for how short these replies are). On top of that, once
    the still-incomplete tail gets past _MIN_CLAUSE_CHARS, it's also
    peeled at its last comma/semicolon/colon -- voice-mode replies are
    capped at 1-3 short sentences, so a single long sentence with no
    earlier full stop would otherwise make "sentence-level" streaming
    provide no real head start at all, defeating the point of speaking
    as the response is written rather than after it's fully generated."""
    parts = _SENTENCE_SPLIT_RE.split(buf)
    if len(parts) > 1:
        complete, tail = parts[:-1], parts[-1]
    else:
        complete, tail = [], buf

    if len(tail) > _MIN_CLAUSE_CHARS:
        clause_parts = _CLAUSE_SPLIT_RE.split(tail)
        if len(clause_parts) > 1:
            complete = complete + clause_parts[:-1]
            tail = clause_parts[-1]

    return complete, tail


async def _notification_bridge() -> None:
    """Forward select EventBus events to Web Push, so the phone gets
    alerted for things that matter even when the PWA isn't open in the
    foreground: automation results (success or failure) and real tool/
    voice errors. Deliberately narrow -- routine tool_use/tool_result
    chatter during an interactive chat session stays in the Activity
    feed only, or every automation run and TTS hiccup would spam the
    user's lock screen. Runs for the app's lifetime; started once at
    startup."""
    queue = bus.subscribe()
    try:
        while True:
            event = await queue.get()
            event_type = event.get("type")
            if event_type == "automation_run" and event.get("status") in ("completed", "error"):
                status = event["status"]
                title = f"Automation: {event.get('name') or '?'}"
                body = event.get("result") or ("Failed" if status == "error" else "Done")
                sent = await asyncio.to_thread(
                    push.send_notification, title, body[:180], f"automation-{event.get('id')}"
                )
                # Loud on purpose: push failing silently (0 reached, or an
                # exception this thread would otherwise swallow) is exactly
                # what made "no alerts arrive" impossible to diagnose before
                # -- this line is what you should watch for in this
                # terminal right after an automation completes.
                logger.info(
                    "Push: automation %r -> %d device(s) reached (%d subscribed).",
                    event.get("name"), sent, push.subscription_count(),
                )
            elif event_type == "background_job" and event.get("status") in ("completed", "error"):
                ok = event["status"] == "completed"
                sent = await asyncio.to_thread(
                    push.send_notification,
                    ("Done: " if ok else "Problem: ") + str(event.get("name") or "background job"),
                    str(event.get("result") or ("Finished." if ok else "It failed."))[:180],
                    f"job-{event.get('id')}",
                )
                logger.info("Push: background job %r -> %d device(s) reached.", event.get("name"), sent)
            elif event_type == "watchdog_kill":
                sent = await asyncio.to_thread(
                    push.send_notification,
                    "Neural cleaned up a stuck process",
                    f"A {event.get('kind')} process was still running after "
                    f"{event.get('age_seconds', 0) // 60} min past its limit (following a "
                    f"{event.get('mode', 'chat')} turn that timed out) -- it's been stopped, "
                    "it won't keep running in the background.",
                    "watchdog-kill",
                )
                logger.info("Push: stuck-process cleanup alert -> %d device(s) reached.", sent)
            elif event_type == "tts_error":
                # tts.py reports each kind of problem at most once per 30 min,
                # and only marks it fatal when no voice engine worked at all.
                # A fallback voice taking over is not worth a phone alert.
                message = event.get("message", "")
                if message and event.get("fatal"):
                    sent = await asyncio.to_thread(
                        push.send_notification, "Neural voice error", message[:180], "tts-error"
                    )
                    logger.info("Push: voice error alert -> %d device(s) reached.", sent)
    except asyncio.CancelledError:
        raise
    finally:
        bus.unsubscribe(queue)


# Results of background jobs (background.py) that finished since the chat
# agent's last turn. They're already shown/spoken to the user; this just
# lets the next turn KNOW about them, so "what did you find?" works.
_pending_job_notes: list = []


async def _deliver_job_result(job: dict) -> None:
    title = job["title"]
    status = job["status"]
    detail = job.get("result") if status == "completed" else job.get("error")
    _pending_job_notes.append(f'"{title}" ({status}): {detail or ""}'[:700])
    del _pending_job_notes[:-6]
    if status == "completed":
        spoken, mood = job.get("result") or f'Your background job "{title}" just finished.', job.get("mood")
    elif status == "cancelled":
        spoken, mood = f'Stopped the background job "{title}".', "neutral"
    else:
        spoken, mood = f'The background job "{title}" ran into a problem: {(detail or "unknown error")[:300]}', "worried"
    await deliver_assistant_message(spoken, source=f"job:{title}", mood=mood)


@app.on_event("startup")
async def _on_startup() -> None:
    background.set_result_handler(_deliver_job_result)
    automations.start_scheduler()
    backup.schedule_backup()
    # Only chat mode is pre-warmed at startup -- Tutor mode's shared
    # client is created lazily the first time someone actually opens
    # Tutor mode (see ws_endpoint's set_mode handling), since not every
    # run of Neural touches it and there's no reason to pay its ~20s
    # subprocess-boot cost on every single startup regardless.
    asyncio.create_task(_warm_shared_client("chat"))
    asyncio.create_task(_notification_bridge())
    asyncio.create_task(asyncio.to_thread(tts.warm))  # load the local voice so the first reply isn't slow
    # Visible proof at every boot that push is (or isn't) actually wired up
    # -- print this in your own terminal instead of guessing: 0 subscribed
    # means the ALERTS toggle has never successfully completed on any
    # device (check the Mobile access section in README.md for the
    # HTTPS/Tailscale requirement), while >0 means subscriptions exist and
    # a failure to actually receive one is either a delivery-side issue
    # (watch for the "Push: automation ... -> N device(s) reached" line
    # this now logs every time one fires) or an OS/browser notification
    # setting blocking the toast itself.
    logger.info(
        "Push notifications: %d device(s) subscribed, VAPID key ready.",
        push.subscription_count(),
    )
    # Apple's push service (web.push.apple.com) rejects a VAPID sub claim
    # on a reserved/placeholder domain with 403 "BadJwtToken" -- while
    # Google's FCM accepts the exact same value without complaint. That
    # mismatch is what makes this bug so easy to miss: push looks like
    # it's working (desktop/Android succeed) while iPhones silently get
    # nothing, with no error pointing at this specific value. Warn loudly
    # at every boot instead of letting that happen quietly again.
    _reserved_contact_suffixes = ("@localhost", "@example.com", ".invalid", ".test")
    if config.VAPID_CONTACT_EMAIL.rstrip("/").lower().endswith(_reserved_contact_suffixes):
        logger.warning(
            "VAPID_CONTACT_EMAIL (%s) looks like a placeholder -- Apple's push service "
            "will likely reject it with 403 BadJwtToken, so iPhone push will silently fail "
            "even though desktop/Android push works fine. Set it to a real mailto: address "
            "or a real https:// URL you control in .env and restart.",
            config.VAPID_CONTACT_EMAIL,
        )


@app.on_event("shutdown")
async def _on_shutdown() -> None:
    try:
        await background.shutdown()
    except Exception:  # noqa: BLE001
        pass
    for client in _shared_clients.values():
        if client is not None:
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001
                pass


@app.middleware("http")
async def no_cache(request, call_next):
    """Dev-mode: never let the browser cache the frontend. Without this,
    changing app.js/index.html and reloading can silently keep running the
    OLD cached script -- which looks exactly like a broken app."""
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response


def _event_from_message(msg, text_buffer: list, source: str = "chat"):
    """Turn one SDK message into a small JSON-able event for the frontend,
    and publish tool activity to the shared EventBus so every open tab's
    activity feed sees it (not just the tab that sent the message).

    Also appends any assistant text to text_buffer in place, so the
    caller can synthesize speech for the whole turn once it's done.
    """
    if isinstance(msg, AssistantMessage):
        blocks = []
        mood = None
        for block in msg.content:
            if isinstance(block, TextBlock):
                # [mood:...] tags drive the avatar's face (see mood.py) and
                # must never be shown, spoken, or saved to history.
                clean, block_mood = extract_mood(block.text)
                mood = block_mood or mood
                blocks.append({"kind": "text", "text": clean})
                text_buffer.append(clean)
            elif isinstance(block, ToolUseBlock):
                blocks.append(
                    {"kind": "tool_use", "tool": block.name, "input": block.input}
                )
                bus.publish(
                    {
                        "type": "tool_use",
                        "tool": block.name,
                        "input": block.input,
                        "tool_use_id": getattr(block, "id", None),
                        "source": source,
                    }
                )
        if not blocks:
            return None
        event = {"type": "assistant", "blocks": blocks}
        if mood:
            event["mood"] = mood
        return event

    if isinstance(msg, UserMessage):
        for block in getattr(msg, "content", []) or []:
            if isinstance(block, ToolResultBlock):
                bus.publish(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.tool_use_id,
                        "is_error": getattr(block, "is_error", False),
                        "source": source,
                    }
                )
        # The browser does not render raw tool results. Some media tools
        # return very large payloads, and forwarding them over the chat
        # socket can delay the actual assistant completion behind a queue
        # of invisible data. Activity/audit events above retain the useful
        # success/failure signal without the payload.
        return None

    if isinstance(msg, ResultMessage):
        bus.publish(
            {
                "type": "turn_result",
                "cost_usd": getattr(msg, "total_cost_usd", None),
                "usage": getattr(msg, "usage", None),
                "duration_ms": getattr(msg, "duration_ms", None),
                "is_error": getattr(msg, "is_error", False),
                "source": source,
            }
        )
        return {"type": "result", "cost_usd": getattr(msg, "total_cost_usd", None)}

    return None


_DISCONNECTED = object()  # sentinel put on in_queue when the socket closes

DESKTOP_CONTEXT_MAX_CHARS = 8000


def _clean_desktop_context(value) -> str:
    """Neural Desktop's per-message context (active window, and clipboard
    text when the user mentions it). Keeps line breaks (clipboard code
    stays readable), drops other control characters, and caps the size."""
    if not value:
        return ""
    text = str(value).replace("\r\n", "\n")
    text = "".join(ch for ch in text if ch in "\n\t" or ord(ch) >= 32)
    text = text.strip()
    if len(text) > DESKTOP_CONTEXT_MAX_CHARS:
        text = text[:DESKTOP_CONTEXT_MAX_CHARS] + " ...(truncated)"
    return text


async def _await_unless_superseded(tasks: list, superseded) -> bool:
    """Wait for a turn's remaining TTS tasks, but give up (cancelling them)
    the moment a newer message arrives. Previously the loop always awaited
    every sentence's synthesis before it could even look at the next
    message -- so interrupting Neural mid-reply still made you wait for the
    rest of the OLD reply's audio to be generated. Returns False if it gave
    up."""
    pending = {t for t in tasks if not t.done()}
    while pending:
        if superseded():
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            return False
        _done, pending = await asyncio.wait(pending, timeout=0.12)
    return not superseded()


_ACK_REPLIES = {
    "punct": ["I'm here -- what do you need?", "Yep, listening. What's up?"],
    "greet": ["Hey! What's up?", "Hi! What are we doing?", "Hey there -- what can I do for you?"],
    "thanks": ["Anytime!", "You got it.", "Happy to help!"],
    "ok": ["Cool. What's next?", "Alright -- anything else?", "Got it."],
}


def _direct_acknowledgement(text: str) -> str | None:
    """Handle input that cannot safely mean "continue the last agent plan".

    A bare punctuation reply (``.``, ``??``) has no task semantics. Sending it
    to a long-lived agent session used to invite the model to continue whatever
    media plan it had last seen. Respond locally instead: this is instant, costs
    no model turn, and creates a hard boundary between an acknowledgement and
    an unfinished render.
    """
    import random

    stripped = text.strip()
    if re.fullmatch(r"[.?!,;:…]+", stripped):
        return random.choice(_ACK_REPLIES["punct"])
    lowered = stripped.lower().rstrip("!.")
    if lowered in {"hey", "hi", "hello", "yo", "hey neural", "hi neural"}:
        return random.choice(_ACK_REPLIES["greet"])
    if lowered in {"thanks", "thank you", "thx", "ty", "thanks neural"}:
        return random.choice(_ACK_REPLIES["thanks"])
    if lowered in {"ok", "okay", "k", "cool", "got it", "alright"}:
        return random.choice(_ACK_REPLIES["ok"])
    return None


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()

    # Which mode ("chat" or "tutor") THIS connection is currently
    # showing -- distinct from _shared_clients' keys, which are the
    # actual per-mode agent clients shared across every connection in
    # that mode. Changed only via a {"type": "set_mode"} message (see
    # _reader below); everything else in this function reads it fresh
    # each turn so a mode switch mid-connection takes effect on the very
    # next message with no reconnect needed.
    conn_mode = "chat"

    # Every reply now always talks (not just when the mic was used to ask
    # it) and speaks AS the response is written, not after it's fully
    # generated: as assistant text streams in, complete sentences are
    # peeled off and handed to TTS the moment they're done, each one
    # sent to the frontend the moment ITS audio is ready -- so playback
    # of the first sentence can start while later sentences are still
    # being generated/synthesized, instead of waiting for the whole
    # reply. Chunks race each other to finish synthesis (network latency
    # varies), so each carries a "seq" and the frontend plays them back
    # in seq order, not arrival order.
    #
    # Those per-sentence TTS calls run as background tasks so they never
    # block pulling the next chunk of text off the SDK's response stream
    # -- text keeps appearing in the chat log at full speed regardless of
    # how long speech synthesis takes. But a raw websocket connection
    # only safely supports ONE writer at a time, so the main loop and
    # every background TTS task funnel their sends through one queue,
    # drained by a single dedicated writer coroutine below, rather than
    # calling websocket.send_text() concurrently from multiple tasks.
    out_queue: "asyncio.Queue[str]" = asyncio.Queue()

    async def _writer() -> None:
        while True:
            out_queue_item = await out_queue.get()
            await websocket.send_text(out_queue_item)

    async def _send(obj: dict) -> None:
        await out_queue.put(json.dumps(obj))

    # Registered for as long as this connection is open so
    # deliver_assistant_message (automation results) can push live events
    # to this tab/device too, not just whichever tab happens to be
    # mid-turn -- see its definition above.
    _broadcast_sinks.add(_send)

    # Recent chat history from any device IN THIS CONNECTION'S CURRENT
    # MODE, so a newly (re)opened app looks like the same ongoing
    # conversation instead of a blank slate -- see _chat_history()/the
    # shared-client comment above. A fresh connection always starts in
    # "chat" mode (see conn_mode above), so this is chat history; a later
    # mode switch sends its own "history" event with "reset": true (see
    # _handle_set_mode below) to swap the frontend's chat log over.
    await _send({"type": "history", "mode": conn_mode, "messages": _chat_history(conn_mode)})

    # Incoming messages are read by their OWN dedicated task, not inline
    # in the main loop below. Without this, an "interrupt" sent while a
    # turn is still streaming would just sit unread until that turn's
    # response loop finished and came back around to read the socket
    # again -- by which point the whole point of interrupting (barge-in:
    # stop the old reply, start the new one right away) is already lost.
    # Running the reader concurrently means interrupt() fires the moment
    # it arrives, regardless of what the main loop is doing.
    in_queue: "asyncio.Queue[dict]" = asyncio.Queue()
    # Updated by the reader as soon as a message arrives, even while the
    # previous turn is still inside a video tool. This lets the response
    # loop suppress stale output before it can be written to the socket.
    received_generation = 0

    async def _handle_set_mode(new_mode: str) -> None:
        """A tab/device switched between Chat and Tutor mode. Updates the
        shared Tutor-session counter (see its definition above
        _end_tutor_session) and, if this just emptied or just started
        Tutor mode, kicks off the matching background lifecycle step --
        then sends this connection a fresh, mode-scoped history so its
        chat log can be swapped over without a full reconnect."""
        nonlocal conn_mode
        old_mode = conn_mode
        if new_mode == old_mode:
            return
        conn_mode = new_mode

        global _tutor_active_count
        if old_mode == "tutor":
            _tutor_active_count = max(0, _tutor_active_count - 1)
            if _tutor_active_count == 0:
                asyncio.create_task(_end_tutor_session())
        if new_mode == "tutor":
            _tutor_active_count += 1
            if _shared_clients.get("tutor") is None:
                # Pre-warm in the background so the user's first tutor
                # message doesn't eat the ~20s client-boot cost -- same
                # reasoning as chat mode's startup pre-warm, just
                # triggered on demand instead of at app launch.
                asyncio.create_task(_warm_shared_client("tutor"))

        await _send({"type": "history", "mode": new_mode, "messages": _chat_history(new_mode), "reset": True})

    async def _reader() -> None:
        nonlocal received_generation
        try:
            while True:
                raw = await websocket.receive_text()
                payload = json.loads(raw)
                ptype = payload.get("type")
                if ptype == "interrupt":
                    client = _shared_clients.get(conn_mode)
                    if client is not None:
                        try:
                            await client.interrupt()
                        except Exception as e:
                            logger.error("interrupt() failed: %s", e)
                    # Also flips a flag _run_turn checks on its NEXT wait,
                    # so a turn stuck inside a tool call client.interrupt()
                    # can't actually preempt still gets forcibly cut off
                    # within a short grace period instead of running for
                    # however long that tool call takes -- see
                    # config.POST_INTERRUPT_GRACE_SECONDS.
                    _interrupt_events[conn_mode].set()
                    continue
                if ptype == "set_mode":
                    new_mode = payload.get("mode")
                    if new_mode in ("chat", "tutor"):
                        await _handle_set_mode(new_mode)
                    continue
                # Tag the mode THIS message was actually sent in, rather
                # than letting the main loop re-read conn_mode whenever it
                # eventually gets dequeued -- a message queued in chat mode
                # must still be processed as a chat turn even if the user
                # switches to Tutor mode before the main loop gets to it.
                received_generation += 1
                payload["_mode"] = conn_mode
                payload["_generation"] = received_generation
                await in_queue.put(payload)
        except WebSocketDisconnect:
            pass
        finally:
            await in_queue.put(_DISCONNECTED)

    writer_task = asyncio.create_task(_writer())
    reader_task = asyncio.create_task(_reader())

    try:
        while True:
            payload = await in_queue.get()
            if payload is _DISCONNECTED:
                break

            # Coalesce: if more messages already piled up behind this one
            # before we got to it, only the LAST one still matters. This
            # only ever happens when an earlier turn is still running when
            # further messages arrive (a slow recording/render, or a
            # wedged session) -- app.js's bargeIn()/sendMessage() already
            # advances activeTurnId the instant it queues a newer message,
            # so the frontend would silently discard every earlier one's
            # reply anyway once it finally streamed back (see its turn_id
            # filter). Skipping straight to the newest instead of actually
            # running _run_turn for each superseded one in between avoids
            # spending a full turn's worth of tokens generating output
            # nobody will ever see -- the real fix for credits still
            # burning even after a stuck task: previously EVERY queued
            # message got a full, real, paid turn regardless of whether
            # anyone would ever see the answer.
            while not in_queue.empty():
                nxt = in_queue.get_nowait()
                if nxt is _DISCONNECTED:
                    payload = _DISCONNECTED
                    break
                payload = nxt
            if payload is _DISCONNECTED:
                break

            text = (payload.get("text") or "").strip()
            # Neural Desktop (desktop/) tells Neural which window the user is
            # looking at, so "what does this error mean?" has a referent. It
            # rides along with the prompt only -- never shown, never saved
            # to history. "speak": false (desktop's mute toggle, or a quiet
            # quick action) skips TTS for this turn entirely.
            desktop_context = _clean_desktop_context(payload.get("context"))
            speak_enabled = payload.get("speak", True) is not False
            # Images shown to Neural from the chat UI (see attachments.py
            # and app.js's "Vision" section): saved under media/uploads/
            # and handed to Claude as file paths to open with its Read
            # tool. An image with no text is a perfectly good message.
            saved_images: list = []
            if payload.get("attachments"):
                try:
                    saved_images = await asyncio.to_thread(attachments.save_attachments, payload["attachments"])
                except Exception as e:  # noqa: BLE001 -- a bad upload must never kill the connection
                    logger.error("Couldn't save attachments: %s", e)
            if not text and not saved_images:
                continue
            display_text = text
            if saved_images:
                marker = attachments.display_marker(saved_images)
                display_text = (text + " " + marker) if text else marker
            # The mode THIS message was actually sent in (tagged by
            # _reader at enqueue time), not whatever conn_mode happens to
            # be NOW -- a message queued in chat mode must still run as a
            # chat turn even if the user switched to Tutor mode before the
            # main loop got around to dequeuing it. Falls back to conn_mode
            # only for defensiveness (every message _reader enqueues is
            # already tagged).
            mode = payload.get("_mode", conn_mode)
            # Echoed back on every event this turn produces, so the
            # frontend can tell a barge-in's new turn apart from stray
            # leftover events a just-interrupted older turn is still
            # finishing up server-side -- see app.js's sendMessage.
            turn_id = payload.get("turn_id")
            turn_generation = payload.get("_generation", 0)

            def _superseded() -> bool:
                return turn_generation < received_generation

            bus.publish({"type": "message", "role": "user", "text": display_text, "source": "chat", "mode": mode})

            direct_reply = _direct_acknowledgement(text)
            if direct_reply:
                # Do not route acknowledgements into the model at all. In
                # particular, they must never become a reason to poll or
                # resume an earlier render.
                await _send(
                    {
                        "type": "assistant",
                        "blocks": [{"kind": "text", "text": direct_reply}],
                        "mood": "happy",
                        "turn_id": turn_id,
                    }
                )
                bus.publish(
                    {
                        "type": "message",
                        "role": "assistant",
                        "text": direct_reply,
                        "source": "chat",
                        "mode": mode,
                    }
                )
                await _send({"type": "result", "turn_id": turn_id})
                # Spoken too (these used to be silent text) -- still no model
                # turn, just one short TTS clip.
                spoken = 0
                try:
                    clip = await tts.synthesize(direct_reply) if speak_enabled else None
                except Exception:  # noqa: BLE001
                    clip = None
                if clip and not _superseded():
                    spoken = 1
                    await _send({
                        "type": "audio", "seq": 1, "turn_id": turn_id, "mime": clip[1],
                        "data": base64.b64encode(clip[0]).decode("ascii"),
                    })
                await _send({"type": "audio_done", "count": spoken, "turn_id": turn_id})
                continue

            # Voice-triggered automations: a saved automation can carry a
            # trigger phrase (set in the automation form), and saying (or
            # typing) it runs that automation directly instead of going
            # through a full conversational turn with the agent -- a
            # fast, deterministic string match rather than an LLM call,
            # so it's instant and never misfires into a different
            # interpretation. Checked regardless of mode (chat or tutor)
            # since it's a fast intercept before any mode-specific
            # dispatch -- automations themselves stay chat-scoped (see
            # deliver_assistant_message's default mode="chat"), but
            # saying a trigger phrase while in Tutor mode still fires it
            # rather than being swallowed as a tutoring question. This
            # short-circuits the rest of the loop body entirely: no
            # _run_turn, no per-sentence TTS for this message. The
            # matched automation's own real output (and any errors)
            # arrives shortly afterward through automations.run_automation
            # -> deliver_assistant_message, the exact same pipeline a
            # scheduled or "run now" firing already uses.
            matched = automations.match_voice_trigger(text) if not saved_images else None
            if matched:
                asyncio.create_task(automations.run_automation(matched["id"]))
                await deliver_assistant_message(
                    f'Starting your "{matched["name"]}" automation now -- '
                    "I'll let you know once it's done.",
                    source=f'voice_trigger:{matched["name"]}',
                )
                continue

            text_buffer: list = []
            speech_pending = ""
            audio_seq = 0
            tts_tasks: list = []
            # Only the opening of a reply is spoken (see speech_budget.py);
            # the full text is always on screen.
            spoken_budget = SpeechBudget()

            async def _speak(chunk: str, seq: int) -> None:
                chunk = extract_mood(chunk)[0].strip() if speak_enabled else ""
                result = await tts.synthesize(chunk) if chunk else None
                out: dict = {"type": "audio", "seq": seq, "turn_id": turn_id}
                if result:
                    audio, mime = result
                    out["mime"] = mime
                    out["data"] = base64.b64encode(audio).decode("ascii")
                else:
                    # Every TTS tier failed for this one sentence -- tell
                    # the frontend so it can skip this seq rather than
                    # waiting forever for audio that's never coming.
                    out["data"] = None
                await _send(out)

            try:
                prompt = text
                if saved_images:
                    prompt = attachments.prompt_note(saved_images) + (text or "What do you make of this?")
                if desktop_context:
                    prompt = (
                        "[Desktop context, gathered automatically by Neural Desktop -- not typed by the user]\n"
                        + desktop_context + "\n[/Desktop context]\n\n" + prompt
                    )
                if mode == "chat" and _pending_job_notes:
                    notes = "\n".join(f"- {n}" for n in _pending_job_notes)
                    _pending_job_notes.clear()
                    prompt = (
                        "[Background job results that arrived since your last reply -- already shown and "
                        "spoken to the user, so don't repeat them unless asked:\n" + notes + "]\n\n" + prompt
                    )
                # Speak while generating: text deltas from the partial-message
                # stream go to the chat bubble and to TTS immediately, so the
                # first sentence starts playing while the rest is still being
                # written (see speech_stream.py). The complete AssistantMessage
                # that follows only contributes what wasn't streamed (tool
                # calls, or everything if streaming isn't available).
                streamer = TextStreamer()
                async for msg in _run_turn(wrap_for_voice(prompt, mode=mode), mode=mode):
                    if isinstance(msg, StreamEvent):
                        if msg.parent_tool_use_id:
                            continue
                        piece, stream_mood = streamer.feed(msg.event or {})
                        if (not piece and not stream_mood) or _superseded():
                            continue
                        out = {"type": "assistant", "blocks": [], "turn_id": turn_id}
                        if stream_mood:
                            out["mood"] = stream_mood
                        if piece:
                            out["blocks"].append({"kind": "text", "text": piece})
                            text_buffer.append(piece)
                            speech_pending += piece
                        await _send(out)
                        if piece:
                            sentences, speech_pending = _split_sentences(speech_pending)
                            for sentence in sentences:
                                sentence = spoken_budget.take(sentence)
                                if sentence:
                                    audio_seq += 1
                                    tts_tasks.append(asyncio.create_task(_speak(sentence, audio_seq)))
                        continue

                    already_streamed = isinstance(msg, AssistantMessage) and streamer.text_already_streamed(
                        getattr(msg, "message_id", None)
                    )
                    message_text: list = []
                    event = _event_from_message(msg, message_text)
                    if event is None:
                        continue
                    if already_streamed and event["type"] == "assistant":
                        event["blocks"] = [b for b in event["blocks"] if b["kind"] != "text"]
                        event.pop("mood", None)
                        if not event["blocks"]:
                            continue
                    else:
                        text_buffer.extend(message_text)
                    # A newer prompt has already arrived. Continue draining
                    # the SDK until its interrupt/reset path releases the
                    # shared client, but never attach the old render's final
                    # text (or a late ResultMessage) to the new prompt.
                    if _superseded():
                        continue
                    event["turn_id"] = turn_id
                    await _send(event)
                    if event["type"] == "assistant":
                        for block in event["blocks"]:
                            if block["kind"] == "text":
                                speech_pending += block["text"]
                        sentences, speech_pending = _split_sentences(speech_pending)
                        for sentence in sentences:
                            sentence = spoken_budget.take(sentence)
                            if sentence:
                                audio_seq += 1
                                tts_tasks.append(asyncio.create_task(_speak(sentence, audio_seq)))
            except Exception as e:
                # A turn can fail outright rather than just produce a
                # normal reply -- most notably _run_turn's own idle-timeout
                # recovery (see there) discarding a wedged session, or the
                # client failing to (re)connect at all. Surface it as an
                # ordinary spoken reply through the SAME text_buffer/TTS
                # pipeline below instead of leaving the user staring at
                # silence, or letting an uncaught exception tear down this
                # whole connection.
                logger.error("Turn failed for mode %s: %s", mode, e)
                if _superseded():
                    # The interruption is already being handled by the
                    # next request. Do not place a reset/error reply into
                    # its chat stream.
                    for task in tts_tasks:
                        task.cancel()
                    if tts_tasks:
                        await asyncio.gather(*tts_tasks, return_exceptions=True)
                    text_buffer.clear()
                    speech_pending = ""
                    continue
                error_text = str(e) or "Something went wrong on that turn -- please try again."
                text_buffer.append(error_text)
                await _send(
                    {"type": "assistant", "blocks": [{"kind": "text", "text": error_text}], "turn_id": turn_id}
                )
                audio_seq += 1
                tts_tasks.append(asyncio.create_task(_speak(error_text, audio_seq)))

            # A stale turn's TTS work is invisible to the user and can hold
            # the queue behind a completed render. Cancel it immediately;
            # any audio already sent is still harmlessly dropped by the
            # frontend's turn-id guard.
            if _superseded():
                for task in tts_tasks:
                    task.cancel()
                if tts_tasks:
                    await asyncio.gather(*tts_tasks, return_exceptions=True)
                text_buffer.clear()
                speech_pending = ""

            # Flush whatever's left in the buffer (a reply that doesn't
            # end on ./!/? -- common for short spoken replies) as the
            # final chunk.
            tail = spoken_budget.take(speech_pending)
            if tail and not _superseded():
                audio_seq += 1
                tts_tasks.append(asyncio.create_task(_speak(tail, audio_seq)))
            overflow_note = spoken_budget.overflow_note()
            if overflow_note and speak_enabled and not _superseded():
                audio_seq += 1
                tts_tasks.append(asyncio.create_task(_speak(overflow_note, audio_seq)))

            if text_buffer and not _superseded():
                bus.publish(
                    {
                        "type": "message",
                        "role": "assistant",
                        "text": extract_mood("".join(text_buffer))[0].strip(),
                        "source": "chat",
                        "mode": mode,
                    }
                )

            if tts_tasks and not _superseded():
                await _await_unless_superseded(tts_tasks, _superseded)
            # Tells the frontend exactly how many audio chunks to expect
            # for this turn, so it knows when the reply is fully spoken
            # even though chunks arrive (and finish synthesizing) out of
            # order -- see the frontend's audio queue for the other half
            # of this.
            if not _superseded():
                await _send({"type": "audio_done", "count": audio_seq, "turn_id": turn_id})
    finally:
        # No client-disconnect here -- each mode's agent client is shared
        # across every device/tab currently in that mode, so one
        # connection closing must not tear down the conversation for the
        # others (only actual app shutdown does that, see _on_shutdown
        # above). If THIS connection was showing Tutor mode when it
        # closed, though, it still counts as "leaving" Tutor mode for the
        # session-boundary bookkeeping -- same as an explicit switch back
        # to chat mode (see _handle_set_mode above), just via disconnect
        # instead. Just stop this connection's own reader/writer.
        _broadcast_sinks.discard(_send)
        if conn_mode == "tutor":
            global _tutor_active_count
            _tutor_active_count = max(0, _tutor_active_count - 1)
            if _tutor_active_count == 0:
                asyncio.create_task(_end_tutor_session())
        reader_task.cancel()
        writer_task.cancel()
        for task in (reader_task, writer_task):
            try:
                await task
            except asyncio.CancelledError:
                pass


@app.websocket("/events")
async def events_endpoint(websocket: WebSocket):
    """Read-only live activity feed. Any number of tabs can connect; each
    gets recent history on connect, then a live stream of everything
    published to the EventBus afterwards (chat tool use, automation runs,
    etc.) -- decoupled from the interactive /ws chat socket so it never
    blocks on, or is blocked by, an in-flight chat turn."""
    await websocket.accept()
    for event in bus.history():
        await websocket.send_text(json.dumps(event))
    queue = bus.subscribe()
    try:
        while True:
            event = await queue.get()
            await websocket.send_text(json.dumps(event))
    except WebSocketDisconnect:
        pass
    finally:
        bus.unsubscribe(queue)


class AutomationIn(BaseModel):
    name: str
    cron: str
    prompt: str
    enabled: bool = True
    voice_trigger: Optional[str] = None


class AutomationToggle(BaseModel):
    enabled: bool


@app.get("/api/config")
async def api_config():
    """Non-secret settings the frontend needs -- e.g. the wake phrase for
    continuous voice listening. Never put API keys or tokens here."""
    return {"wake_phrase": config.WAKE_PHRASE, "utility": utility.utility_status()}


class SpeakIn(BaseModel):
    text: str


@app.post("/api/transcribe")
async def api_transcribe(request: Request):
    """Neural Desktop's voice input: a short WAV clip in, text out, using
    local Whisper (backend/stt.py). Optional ?language=xx overrides
    STT_LANGUAGE for this clip (empty = auto-detect)."""
    data = await request.body()
    mime = request.headers.get("content-type", "audio/wav")
    language = request.query_params.get("language")
    try:
        return await asyncio.to_thread(stt.transcribe_audio, data, mime, language)
    except stt.STTUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Bad audio: {e}")


@app.post("/api/transcribe/warm")
async def api_transcribe_warm():
    """Loads the voice model in the background so the first clip is fast."""
    ready = await asyncio.to_thread(stt.warm)
    return {"ready": ready, "available": stt.available()}


@app.post("/api/speak")
async def api_speak(body: SpeakIn):
    """Text in, Neural's voice out (same TTS chain as chat replies). Lets
    the desktop app speak instant local answers -- the time, "opening
    Spotify", a reminder -- in the same voice without a model turn."""
    text = extract_mood(body.text or "")[0].strip()[:1500]
    if not text:
        raise HTTPException(status_code=400, detail="Nothing to say.")
    clip = await tts.synthesize(text)
    if not clip:
        raise HTTPException(status_code=503, detail="Text-to-speech is unavailable right now.")
    return Response(content=clip[0], media_type=clip[1])


@app.get("/api/observability")
async def api_observability():
    """Local, sanitized runtime health/audit snapshot for the dashboard."""
    return observability.dashboard_snapshot()


@app.api_route("/media/{token}", methods=["GET", "HEAD"])
async def get_media(token: str):
    """Serves a file registered by the media_tools MCP tool, e.g. so
    Buffer's servers can fetch a local screen recording or generated
    image by URL (see backend/media.py -- Buffer attaches media by URL,
    not by upload). Files are exposed by random token, not by real path,
    and the token expires after 24h; this is not a general file server."""
    # Accept an optional file extension ("/media/<token>.mp4") -- some
    # services (Buffer/YouTube) sniff the media type from the URL suffix.
    path = media.resolve(token.rsplit(".", 1)[0] if "." in token else token)
    if not path:
        raise HTTPException(status_code=404, detail="not found or expired")
    return FileResponse(path)


class PushSubscriptionIn(BaseModel):
    endpoint: str
    keys: dict[str, str]
    expirationTime: float | None = None


@app.get("/api/push/vapid-public-key")
async def api_push_vapid_key():
    return {"key": push.VAPID_PUBLIC_KEY}


@app.post("/api/push/subscribe")
async def api_push_subscribe(body: PushSubscriptionIn):
    push.add_subscription(body.model_dump(exclude_none=True))
    return {"ok": True}


@app.post("/api/push/unsubscribe")
async def api_push_unsubscribe(body: dict):
    ok = push.remove_subscription(body.get("endpoint", ""))
    return {"ok": ok}


@app.get("/api/automations")
async def api_list_automations():
    return automations.list_automations()


@app.post("/api/automations")
async def api_create_automation(body: AutomationIn):
    # create_automation raises ValueError on an unparseable cron string --
    # see its own docstring for the live bug this closes (a bad cron used
    # to get saved anyway and crash the ENTIRE backend on the next
    # restart). Surface it as a clean 400 instead of an unhandled 500 so
    # the UI can actually show the user what's wrong with the schedule
    # they typed.
    try:
        return automations.create_automation(
            name=body.name,
            cron=body.cron,
            prompt=body.prompt,
            enabled=body.enabled,
            voice_trigger=body.voice_trigger,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/automations/{task_id}")
async def api_delete_automation(task_id: str):
    ok = automations.delete_automation(task_id)
    return {"deleted": ok}


@app.post("/api/automations/{task_id}/toggle")
async def api_toggle_automation(task_id: str, body: AutomationToggle):
    try:
        result = automations.toggle_automation(task_id, body.enabled)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result or {"error": "not found"}


@app.post("/api/automations/{task_id}/run")
async def api_run_automation_now(task_id: str):
    asyncio.create_task(automations.run_automation(task_id))
    return {"started": True}


class KBIngestIn(BaseModel):
    path: str


@app.get("/api/tutor/knowledge-base")
async def api_kb_list():
    """Every book/PDF currently ingested into Tutor mode's knowledge base
    (see backend/knowledge_base.py) -- drives the Knowledge Base panel in
    the UI's Tutor mode. Returns an empty list (not an error) if the
    optional fastembed/pypdf/numpy dependencies aren't installed yet."""
    from . import knowledge_base

    return knowledge_base.list_sources()


@app.post("/api/tutor/knowledge-base/ingest")
async def api_kb_ingest(body: KBIngestIn):
    """Ingest one file, or every .pdf/.txt/.md file directly inside one
    folder, from THIS machine's filesystem -- see knowledge_base.py's
    ingest_path. Runs synchronously (embedding a whole book can take a
    little while); the UI shows an "Ingesting..." state while it waits."""
    from . import knowledge_base

    try:
        return await knowledge_base.ingest_path(body.path)
    except Exception as e:  # noqa: BLE001 -- surface the real reason to the UI rather than a bare 500
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/tutor/knowledge-base/{source_id}")
async def api_kb_delete(source_id: str):
    from . import knowledge_base

    ok = knowledge_base.delete_source(source_id)
    return {"deleted": ok}


app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
