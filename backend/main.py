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
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import automations, config, media, push, tts
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

# How many currently-connected websocket tabs/devices have Tutor mode
# open right now, across the whole app -- not per connection. This is
# what defines a tutoring "session" boundary (see tutor.py's module
# docstring): it only reaches 0 when EVERY device has left Tutor mode,
# which is the moment _end_tutor_session below actually tears the shared
# tutor client down (after asking it to summarize itself first) rather
# than leaving a session other tabs are still using half-closed.
_tutor_active_count = 0


async def _warm_shared_client(mode: str) -> None:
    lock = _chat_locks[mode]
    async with lock:
        if _shared_clients[mode] is not None:
            return
        try:
            client = new_client(mode=mode)
            await client.connect()
            _shared_clients[mode] = client
        except Exception as e:  # noqa: BLE001 -- warming is best effort
            logger.error("Failed to pre-warm the shared %s client: %s", mode, e)


async def _run_turn(text: str, mode: str = "chat"):
    """Run one chat turn on the shared client for this mode, serialized
    against every other device/tab using the SAME mode (chat and tutor
    turns never block each other -- separate locks). Lazily (re)connects
    if that mode's shared client doesn't exist yet or died. Returns the
    async iterator of SDK messages for the caller to stream to its own
    websocket."""
    lock = _chat_locks[mode]
    async with lock:
        client = _shared_clients[mode]
        if client is None:
            client = new_client(mode=mode)
            await client.connect()
            _shared_clients[mode] = client
        try:
            await client.query(text)
        except Exception as e:
            logger.error("Shared %s client query failed, reconnecting: %s", mode, e)
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001
                pass
            client = new_client(mode=mode)
            await client.connect()
            _shared_clients[mode] = client
            await client.query(text)
        async for msg in client.receive_response():
            yield msg


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
            async for msg in client.receive_response():
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

    summary_text = summary_text.strip()
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


async def deliver_assistant_message(text: str, source: str = "automation", mode: str = "chat") -> None:
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
    text = text.strip()
    if not text:
        return

    bus.publish({"type": "message", "role": "assistant", "text": text, "source": source, "mode": mode})
    await _broadcast({"type": "automation_message", "text": text, "mode": mode})

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

    for sentence in sentences:
        sentence = sentence.strip()
        if sentence:
            audio_seq += 1
            tts_tasks.append(asyncio.create_task(_speak(sentence, audio_seq)))

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
            elif event_type == "tts_error":
                message = event.get("message", "")
                # Skip the routine "not configured" notice -- that fires on
                # every voice turn until ElevenLabs is set up, which is an
                # expected state, not an alert-worthy failure.
                if message and not message.startswith("ElevenLabs not configured"):
                    sent = await asyncio.to_thread(
                        push.send_notification, "Neural voice error", message[:180], "tts-error"
                    )
                    logger.info("Push: voice error alert -> %d device(s) reached.", sent)
    except asyncio.CancelledError:
        raise
    finally:
        bus.unsubscribe(queue)


@app.on_event("startup")
async def _on_startup() -> None:
    automations.start_scheduler()
    # Only chat mode is pre-warmed at startup -- Tutor mode's shared
    # client is created lazily the first time someone actually opens
    # Tutor mode (see ws_endpoint's set_mode handling), since not every
    # run of Neural touches it and there's no reason to pay its ~20s
    # subprocess-boot cost on every single startup regardless.
    asyncio.create_task(_warm_shared_client("chat"))
    asyncio.create_task(_notification_bridge())
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
        for block in msg.content:
            if isinstance(block, TextBlock):
                blocks.append({"kind": "text", "text": block.text})
                text_buffer.append(block.text)
            elif isinstance(block, ToolUseBlock):
                blocks.append(
                    {"kind": "tool_use", "tool": block.name, "input": block.input}
                )
                bus.publish(
                    {
                        "type": "tool_use",
                        "tool": block.name,
                        "input": block.input,
                        "source": source,
                    }
                )
        return {"type": "assistant", "blocks": blocks} if blocks else None

    if isinstance(msg, UserMessage):
        blocks = []
        for block in getattr(msg, "content", []) or []:
            if isinstance(block, ToolResultBlock):
                blocks.append(
                    {
                        "kind": "tool_result",
                        "tool_use_id": block.tool_use_id,
                        "content": block.content,
                        "is_error": getattr(block, "is_error", False),
                    }
                )
                bus.publish(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.tool_use_id,
                        "is_error": getattr(block, "is_error", False),
                        "source": source,
                    }
                )
        return {"type": "tool_results", "blocks": blocks} if blocks else None

    if isinstance(msg, ResultMessage):
        return {"type": "result", "cost_usd": getattr(msg, "total_cost_usd", None)}

    return None


_DISCONNECTED = object()  # sentinel put on in_queue when the socket closes


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
                    continue
                if ptype == "set_mode":
                    new_mode = payload.get("mode")
                    if new_mode in ("chat", "tutor"):
                        await _handle_set_mode(new_mode)
                    continue
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

            text = payload.get("text", "")
            if not text:
                continue
            # Snapshot the mode for this one turn -- if the user switches
            # modes again while this turn is still running, THIS turn
            # still finishes against the mode it started in; the switch
            # takes effect for the next message, same as conn_mode being
            # read fresh each time through this loop already implies.
            mode = conn_mode
            # Echoed back on every event this turn produces, so the
            # frontend can tell a barge-in's new turn apart from stray
            # leftover events a just-interrupted older turn is still
            # finishing up server-side -- see app.js's sendMessage.
            turn_id = payload.get("turn_id")

            bus.publish({"type": "message", "role": "user", "text": text, "source": "chat", "mode": mode})

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
            matched = automations.match_voice_trigger(text)
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

            async def _speak(chunk: str, seq: int) -> None:
                result = await tts.synthesize(chunk)
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

            async for msg in _run_turn(wrap_for_voice(text, mode=mode), mode=mode):
                event = _event_from_message(msg, text_buffer)
                if event is None:
                    continue
                event["turn_id"] = turn_id
                await _send(event)
                if event["type"] == "assistant":
                    for block in event["blocks"]:
                        if block["kind"] == "text":
                            speech_pending += block["text"]
                    sentences, speech_pending = _split_sentences(speech_pending)
                    for sentence in sentences:
                        sentence = sentence.strip()
                        if sentence:
                            audio_seq += 1
                            tts_tasks.append(asyncio.create_task(_speak(sentence, audio_seq)))

            # Flush whatever's left in the buffer (a reply that doesn't
            # end on ./!/? -- common for short spoken replies) as the
            # final chunk.
            tail = speech_pending.strip()
            if tail:
                audio_seq += 1
                tts_tasks.append(asyncio.create_task(_speak(tail, audio_seq)))

            if text_buffer:
                bus.publish(
                    {
                        "type": "message",
                        "role": "assistant",
                        "text": "".join(text_buffer),
                        "source": "chat",
                        "mode": mode,
                    }
                )

            if tts_tasks:
                await asyncio.gather(*tts_tasks)
            # Tells the frontend exactly how many audio chunks to expect
            # for this turn, so it knows when the reply is fully spoken
            # even though chunks arrive (and finish synthesizing) out of
            # order -- see the frontend's audio queue for the other half
            # of this.
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
    return {"wake_phrase": config.WAKE_PHRASE}


@app.get("/media/{token}")
async def get_media(token: str):
    """Serves a file registered by the media_tools MCP tool, e.g. so
    Buffer's servers can fetch a local screen recording or generated
    image by URL (see backend/media.py -- Buffer attaches media by URL,
    not by upload). Files are exposed by random token, not by real path,
    and the token expires after 24h; this is not a general file server."""
    path = media.resolve(token)
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
    return automations.create_automation(
        name=body.name,
        cron=body.cron,
        prompt=body.prompt,
        enabled=body.enabled,
        voice_trigger=body.voice_trigger,
    )


@app.delete("/api/automations/{task_id}")
async def api_delete_automation(task_id: str):
    ok = automations.delete_automation(task_id)
    return {"deleted": ok}


@app.post("/api/automations/{task_id}/toggle")
async def api_toggle_automation(task_id: str, body: AutomationToggle):
    result = automations.toggle_automation(task_id, body.enabled)
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
