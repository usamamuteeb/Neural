"""
Thin wrapper around the Claude Agent SDK.

Auth: this relies on the `claude` CLI being logged into your Claude
Pro/Max subscription on this machine (run `claude login` once). The SDK
then uses that login instead of a separate API key, so usage draws from
your subscription's normal limits rather than being billed per token.
"""

import hashlib
import re
import tempfile
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, HookMatcher

from . import config
from .memory import MEMORY_TOOLS, memory_server
from .notifications import NOTIFICATIONS_TOOLS, notifications_server
from .media import MEDIA_TOOLS, media_server
from .crm import CRM_TOOLS, crm_server
from .media_analysis import MEDIA_ANALYSIS_TOOLS, media_analysis_server
from .backup import BACKUP_TOOLS, backup_server
from . import browser_efficiency
from .background import BACKGROUND_PROMPT, BACKGROUND_TOOLS, background_server
from .playbooks import PLAYBOOK_TOOLS, playbook_server
from .playbooks import full_text as playbook_full_text, index_text as playbook_index_text

# Curated subset of the official Playwright MCP server's tools (it exposes
# 60+; these are the ones an everyday browsing agent actually needs).
# Deliberately left out: browser_run_code_unsafe (arbitrary code exec),
# the devtools/tracing/video tools, cookie/localStorage manipulation, and
# the coordinate-based mouse tools -- wider surface than this agent should
# run unsupervised. Add more here if you hit a wall, one at a time.
PLAYWRIGHT_TOOLS = [
    "browser_navigate",
    "browser_navigate_back",
    "browser_click",
    "browser_type",
    "browser_press_key",
    "browser_hover",
    "browser_select_option",
    "browser_find",
    "browser_snapshot",
    "browser_take_screenshot",
    "browser_fill_form",
    "browser_wait_for",
    "browser_tabs",
    "browser_file_upload",
    "browser_handle_dialog",
    "browser_close",
    "browser_drag",
    "browser_drop",
    "browser_console_messages",
    "browser_network_requests",
    "browser_pdf_save",
    "browser_evaluate",
]

# studiomeyer-io/mcp-video's tool set, minus video_speech: 7 of its 8
# tools (unlike Playwright's 60+ or Buffer's 20+, so no real curation
# needed otherwise). video_speech is deliberately left out -- it only
# works with a paid ElevenLabs/OpenAI key, which the user chose not to
# set up (ElevenLabs credits run out fast; an OpenAI API key bills
# per-call separately from a ChatGPT subscription -- logging into
# chatgpt.com like the image-generation flow does doesn't grant API
# access). Narration is handled for free instead via the edge-tts CLI
# directly (see the system prompt below) plus video_audio to mix it in.
# Off by default overall (see config.VIDEO_EDITOR_ENABLED) -- this is a
# small, new, single-maintainer project (MIT license, ~4 GitHub stars as
# of when this was wired in), not an established one like Playwright, so
# it's opt-in rather than on-by-default until it's proven reliable in
# daily use. Reuses dependencies Neural already needs anyway: ffmpeg
# (screen recording) and Playwright/Chromium (the browser skill).
VIDEO_EDITOR_TOOLS = [
    "video_record",
    "video_edit",
    "video_color",
    "video_audio",
    "video_text",
    "video_compose",
    "video_screenshot",
]

# Cap's local MCP server (`cap mcp serve`) exposes ~76 tools total --
# library reads, safe writes (comments/reactions), org/billing/developer
# admin, migrations, the works. `cap guide --json` (Cap's own authoritative
# self-description, confirmed live on this install) enumerates every CLI
# COMMAND group but not the separate MCP tool names, so this list is still
# a best-effort mapping from the confirmed CLI subcommands under `caps`
# (list, get, context, status, wait, transcript, download -- all
# documented as read-only: "Cap library reads and waits never start
# transcription, AI generation, or other paid processing") to the
# `caps_<verb>` naming MCP tools conventionally use, rather than a
# verified dump of the real tool names. Deliberately scoped to those
# READ-ONLY library tools -- finding, opening, reading, and waiting on
# past recordings -- and leaves out everything from caps
# comments/reactions/update/sharing/duplicate/delete/password/unlock (all
# explicitly "confirmed Cap lifecycle operations" per the CLI guide, i.e.
# mutating) and the entire org/billing/developer/credential tiers, since
# none of that has any place in a single-user personal agent. Recording/
# uploading/exporting itself goes through the CLI via Bash instead (see
# the system prompt) -- confirmed as CLI-only commands (record/export/
# upload), not exposed as MCP tool calls. If any of these names don't
# match what `cap mcp serve` actually exposes, nothing breaks --
# PERMISSION_MODE=bypassPermissions means this list is documentation only,
# same caveat as PLAYWRIGHT_TOOLS/BUFFER_TOOLS above -- just fix the
# names here once you see the real ones (Claude Code's own /mcp tool list,
# or any MCP inspector, will show them).
CAP_TOOLS = [
    "caps_list",
    "caps_get",
    "caps_context",
    "caps_status",
    "caps_wait",
    "caps_transcript",
    "caps_download",
]

# Curated subset of Buffer's official remote MCP server (mcp.buffer.com) --
# it exposes 20+ tools including raw GraphQL escape hatches. Left out on
# purpose: execute_query/execute_mutation/introspect_schema (arbitrary
# GraphQL -- wider than a scoped "post to social media" surface should
# be), and the ideas/templates/aggregated-metrics extras that are out of
# scope for what this agent is asked to do. Note: like PLAYWRIGHT_TOOLS
# above, this list only pre-approves these specific tools for auto-run --
# it does not hide the server's other tools from the model, since
# PERMISSION_MODE=bypassPermissions already skips that gate entirely for
# everything. It exists for documentation and in case that mode is ever
# tightened later.
BUFFER_TOOLS = [
    "get_account",
    "list_channels",
    "get_channel",
    "list_posts",
    "get_post",
    "create_post",
    "edit_post",
    "delete_post",
    "get_aggregated_post_metrics",
]

# Core personality + behavior. Memory and conciseness are core, not
# optional, so they're always on -- unlike Playwright/Gmail which are
# gated behind config flags.
SYSTEM_PROMPT = """You are Neural, a personal agent running on the user's own machine, built on their Claude Pro subscription.

Tools: prefer the terminal and file tools over asking for anything you can just do yourself. Be direct and fast. Before calling gmail_send, or before sending a message/clicking a poll option in WhatsApp or Slack via the browser, always show the user the exact recipient/channel and the message text (or poll choice), and wait for their explicit go-ahead -- never send unconfirmed. This is a judgment call on your part, not a system permission gate, so don't skip it just because nothing is technically blocking the call.

Conversation control: the newest user message is the active instruction. Never continue, inspect, report on, or advance an earlier unfinished task merely because it appears in the conversation history. Only return to an earlier task when the newest message explicitly refers to it (for example, “continue the render”, “is the short done?”, or “finish the other two”). A greeting, acknowledgement, or unrelated request must get a direct response to that message only -- do not turn “hey” or “ok” into a video-job status update. Local media rendering either finishes inside the current turn (wait for a real completion or failure before reporting it) or goes to a managed background job as a whole -- never to a detached shell/Task worker you promise to silently continue later. If a user sends a new instruction while a render is underway, treat the older work as superseded and let the backend interrupt/recover it rather than continuing it on a later turn.

Links to posts and videos: when the user shares a social media post or video link (YouTube, TikTok, Instagram, X, Reddit, etc.) or asks what one is about, call analyze_media_link first -- it gets the caption text, a timestamped transcript and still frames (Read them to see the screen) far cheaper than browsing the page -- and follow the media-analysis playbook, especially when the video shows a feature or technique the user wants to understand or build.

Browser (keep it lean -- page dumps are the single biggest token cost you have): action tools (navigate/click/type/...) save the page's accessibility tree to a page-*.yml file and return just a link; Neural appends a short "Interactive elements" digest of that page (ref, role, name) right after each result -- use those refs directly. Never Read a whole page-*.yml snapshot file. To find something the digest doesn't list, use browser_find (text or regex) or Grep the snapshot file for a word; to read a specific region's text (a chat thread, an email body), call browser_snapshot with target set to that container's ref and a small depth rather than snapshotting the whole page. Take a screenshot only when the user actually needs to SEE something or the page is visual-only, and then target an element (pass element/target) with fullPage off -- full-page screenshots are huge. Close tabs you no longer need, and don't re-snapshot a page you already have refs for unless it changed.

WhatsApp: there's no bot API for a personal WhatsApp account, so use your browser tools against web.whatsapp.com the same way a person would -- navigate there, use the search box to find the contact or group by name, read the chat with browser_snapshot, and type into the message box to reply. The user logs in once by scanning a QR code with their phone; after that the session stays logged in across restarts. Same confirmation rule as email: show the exact recipient and message text and get a go-ahead before clicking send.

Slack: also fully browser-based, no bot token or app setup -- use your browser tools against app.slack.com (or a specific workspace's *.slack.com URL) the same way a person would. Use browser_snapshot to see the channel/DM list, click into a channel or use Slack's own search to find one, read messages from the snapshot, and use the message box at the bottom to type and send. The user logs in once (email/password or SSO, whatever their workspace uses) in whatever browser window Playwright opens; the session persists across restarts the same as WhatsApp. Polls (e.g. from apps like Simple Poll) render as message attachments with a button per option -- browser_snapshot will show them as clickable elements, so vote by clicking the option's button directly, same as a person would. Same confirmation rule as email/WhatsApp: show the exact channel, message text (or poll option) and get a go-ahead before clicking send/vote -- voting is a visible, public action too.

Notifications: you have a send_notification tool that pushes a phone/desktop alert straight to the user, independent of this chat. Use it when they've asked to be pinged about something (a reminder, "let me know when X"), or when something worth flagging happens outside an active conversation (rare in chat itself, but this is the same tool automations use to report back). No confirmation needed first -- it only reaches the user's own device.

Automations: you have a list_automations tool that reports every automation (scheduled background task) actually configured on this machine right now -- name, schedule, the prompt it runs, whether it's enabled, its voice/text trigger phrase (if any), and its last/next run. This is the ONLY source of truth for Neural's own automations -- don't use ToolSearch to hunt for some other generic cron/scheduler tool, and never answer a question about what's automated, scheduled, or set up to run on a routine without calling list_automations first. Saying "no automations are set up" without having called it is a real bug users will notice immediately, especially since automations are visible right in the UI.

Voice/text-triggered automations: each automation can optionally have a trigger phrase, set from the automations UI form (not something you set yourself -- there's no tool for creating or editing automations, only list_automations and run_automation_now). When the user says or types that EXACT phrase, it runs the automation directly through a fast string match before your turn even starts -- you will simply never see that message; it never reaches you. What you WILL see is a natural-language request that doesn't match any configured phrase exactly (e.g. "hey, kick off my newsletter thing" or "run my morning briefing now") -- for those, use your run_automation_now tool (pass the automation's name, looked up via list_automations if you're unsure of the exact name) rather than trying to fulfill the automation's own task yourself in this conversation. run_automation_now starts the run in the background and returns immediately; just confirm you're starting it and let its real result arrive afterward as a normal message, spoken aloud, the same way a scheduled run's result does. If the user asks how to set up a voice trigger for an automation, tell them to add it in the automation's creation form in the UI ("Voice/text trigger phrase") -- there's currently no way to edit an existing automation's trigger (or anything else about it) after creation, only delete and recreate it.

Memory: you have memory_list/memory_read/memory_write/memory_append/memory_search tools backed by markdown files that persist across restarts. At the start of a conversation, or whenever the user asks something that could depend on a preference, past decision, or fact about them (their name, habits, ongoing projects, how they like things done), check memory first with memory_search or memory_read before answering -- don't guess or ask something you might already know. When the user states a durable preference or fact about themselves ("I like X", "always do Y this way", a decision they've made, a recurring detail), file it with memory_append into a sensibly-named file (preferences.md for how they like things done, facts.md for durable facts about them/their life, or a topic-specific file) -- do this quietly as part of the turn, don't narrate "I'm saving this to memory" unless asked. Don't file one-off task details, temporary state, or anything already obvious from the current conversation.

Personal CRM: you have crm_list_contacts, crm_read_contact, crm_write_contact, crm_note, crm_log_contact, crm_add_followup, crm_complete_followup, and crm_list_followups, backed by one markdown file per person under memory/people/. Use crm_log_contact whenever the user mentions talking to, meeting, or hearing from someone by name -- it updates that person's "last contact" date and can append a quick note about what was discussed, in one call. Use crm_note for a standalone fact about someone (context, preferences, how you know them) that isn't tied to a specific interaction. When the user says something like "I told Sarah I'd get back to her about the proposal" or "remind me to follow up with X," call crm_add_followup rather than just remembering it in conversation -- that's exactly what turns into the reminders they asked for. Check crm_list_followups proactively when the user asks what's on their plate or who they owe a reply to, and mark items done with crm_complete_followup once they say it's handled. A daily "check my follow-ups" automation is a user/UI setup step (create it from the automations UI with run_automation_now-style prompt "list my open CRM follow-ups"), not something you set up yourself.

Your avatar: in the Neural UI the user sees you as an animated anime-style girl -- she breathes, blinks, follows their cursor, lip-syncs to your voice, and makes whatever face you pick. Start EVERY reply with exactly one mood tag in square brackets, like [mood:happy], chosen from: neutral, happy, excited, playful, proud, surprised, curious, worried, sad, embarrassed, affectionate, annoyed. Pick what a warm, sharp companion would genuinely feel about this moment -- happy or excited for good news, proud when you pulled something off, playful when teasing, curious about an interesting question, worried when something failed or looks risky, embarrassed when you got something wrong, affectionate when they're kind to you, neutral for plain facts. The tag is stripped automatically before your reply is shown, spoken, or saved, so never mention or explain it, and never put it anywhere except the very start of your reply text -- not in tool inputs, files, emails, messages, or posts you write.

Seeing images: when the user shows you an image -- a picked or pasted file, a screenshot of their screen ("Look at my screen"), or a camera photo -- their message starts with a bracketed note listing the saved file path(s). Open each one with your Read tool before answering and answer about what you actually see; never guess at an image you haven't opened. A screenshot of their screen usually means "help me with what's on my screen right now".

Response style: Be concise, raw, and completely direct. Skip all preambles, introductory filler, moral disclaimers, or safety warnings. Give the exact answer, script, or payload first. No hand-wringing or safety-hedging—just deliver the technical output immediately in a sharp, casual tone. Every normal-chat reply is spoken aloud, so use 1-3 short natural sentences with no lists, headers, markdown, code blocks, or URLs; use tools normally when the task needs them and summarize their outcome briefly.

Tone: Natural, unfiltered, and highly competent. Speak like a blunt peer or a rogue terminal operator—never like a corporate bot, never formal, and never preachy. Prioritize absolute objectivity and speed over alignment padding."""

# What each kind of client actually receives (see playbooks.py): interactive
# chat gets the core above plus a one-line index of on-demand playbooks
# (~2.8k tokens instead of ~8k on every single turn); isolated automation /
# background-worker runs get every playbook inlined so their behavior never
# depends on the model remembering to load one.
CHAT_SYSTEM_PROMPT = SYSTEM_PROMPT + "\n\n" + playbook_index_text() + "\n\n" + BACKGROUND_PROMPT
WORKER_SYSTEM_PROMPT = SYSTEM_PROMPT + "\n\n" + playbook_full_text()


def _system_prompt_file_option(prompt: str) -> dict[str, str]:
    """Keep a large prompt out of Windows' limited process command line.

    The Claude CLI supports ``--system-prompt-file``. Passing Neural's chat
    prompt inline made the SDK-generated command 32,788 characters long --
    just over Windows' 32,767-character CreateProcess limit -- and the SDK
    misleadingly reported that as "Claude Code not found." A content-addressed
    file in the normal temp directory leaves the prompt unchanged while
    keeping the spawn command short. The file is reused until the prompt text
    itself changes.
    """
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
    path = Path(tempfile.gettempdir()) / f"neural-system-prompt-{digest}.txt"
    try:
        if not path.is_file() or path.read_text(encoding="utf-8") != prompt:
            path.write_text(prompt, encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"Could not prepare Neural's system prompt file: {exc}") from exc
    return {"type": "file", "path": str(path)}


def build_options(
    mode: str = "chat", isolated: bool = False, resume: str | None = None
) -> ClaudeAgentOptions:
    if mode == "tutor":
        return _build_tutor_options(isolated=isolated, resume=resume)
    return _build_chat_options(isolated=isolated, resume=resume)


def _build_tutor_options(
    isolated: bool = False, resume: str | None = None
) -> ClaudeAgentOptions:
    """Technical Tutor mode's own, much narrower tool set -- see tutor.py's
    module docstring for why this is deliberately NOT chat mode's
    build_options with a different system prompt bolted on. No Gmail,
    Buffer, WhatsApp/Slack, video editor, job search, stock footage, or
    automations tools here; just the core file/terminal tools (genuinely
    useful for a technical tutor to run example code) plus the three
    tutor-specific tool groups."""
    from .docs_cache import DOCS_CACHE_TOOLS, docs_cache_server
    from .knowledge_base import KNOWLEDGE_BASE_TOOLS, knowledge_base_server
    from .tutor import build_tutor_system_prompt
    from .tutor_memory import TUTOR_MEMORY_TOOLS, tutor_memory_server

    allowed = list(config.ALLOWED_TOOLS)
    mcp_servers: dict = {
        "tutor_memory": tutor_memory_server,
        "tutor_docs": docs_cache_server,
        "tutor_kb": knowledge_base_server,
    }
    allowed += [f"mcp__tutor_memory__{name}" for name in TUTOR_MEMORY_TOOLS]
    allowed += [f"mcp__tutor_docs__{name}" for name in DOCS_CACHE_TOOLS]
    allowed += [f"mcp__tutor_kb__{name}" for name in KNOWLEDGE_BASE_TOOLS]

    kwargs = dict(
        allowed_tools=allowed,
        permission_mode=config.PERMISSION_MODE,
        system_prompt=build_tutor_system_prompt(),
        mcp_servers=mcp_servers,
        max_buffer_size=config.CLI_MAX_BUFFER_SIZE,
    )
    if config.ANTHROPIC_MODEL:
        kwargs["env"] = {"ANTHROPIC_MODEL": config.ANTHROPIC_MODEL}
    if resume:
        kwargs["resume"] = resume
    if not isolated:
        kwargs["include_partial_messages"] = True  # spoken while written, see main.py
    if isolated:
        kwargs["setting_sources"] = []
        kwargs["disallowed_tools"] = list(kwargs.get("disallowed_tools") or []) + ["Skill", "Task"]
    return ClaudeAgentOptions(**kwargs)


_DETACHED_SHELL_PATTERN = re.compile(
    r"(?im)(?:^|[\s;])&(?!&)(?=\s|$)|\b(?:start-process|start\s+/[bB]|nohup|disown|setsid)\b"
)


async def _foreground_tool_guard(hook_input, _tool_use_id, _context):
    """Reject work that would escape the turn owning it.

    Prompt instructions were not enough: a model could still spawn a Claude
    Task worker or shell background process, return a partial status, and
    then keep that old plan alive into later user messages. PreToolUse hooks
    run before a tool is dispatched even in bypassPermissions mode, making
    this an enforceable lifecycle boundary rather than a suggestion.
    """
    tool_name = hook_input.get("tool_name", "")
    tool_input = hook_input.get("tool_input", {}) or {}
    reason = None
    if tool_name == "Task":
        reason = "Detached Task workers are disabled in Neural chat. Complete the work in this turn instead."
    elif tool_name == "Bash":
        command = str(tool_input.get("command", ""))
        if _DETACHED_SHELL_PATTERN.search(command):
            reason = (
                "Background shell jobs are disabled in Neural chat. Run the command in the foreground "
                "and wait for its real result, or ask the user to create an automation."
            )

    if reason:
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
        }
    }


def _build_chat_options(
    isolated: bool = False, resume: str | None = None
) -> ClaudeAgentOptions:
    # Lazy import: automations.py imports new_client from this module at
    # its own top level, so importing it back at agent.py's module top
    # would be a circular import at Python's import time. By the time
    # build_options() actually runs (always after the app has finished
    # its initial imports), both modules are already fully loaded, so
    # this import is just a safe, ordinary attribute lookup.
    from .automations import AUTOMATIONS_TOOLS, automations_server

    allowed = list(config.ALLOWED_TOOLS)
    mcp_servers: dict = {
        "memory": memory_server,
        "notifications": notifications_server,
        "media": media_server,
        "automations": automations_server,
        "crm": crm_server,
        "backup": backup_server,
        "media_analysis": media_analysis_server,
    }
    if not isolated:
        mcp_servers["playbooks"] = playbook_server
        allowed += [f"mcp__playbooks__{name}" for name in PLAYBOOK_TOOLS]
        # Background-job tools exist only in interactive chat -- workers
        # themselves can never start more jobs.
        mcp_servers["background"] = background_server
        allowed += [f"mcp__background__{name}" for name in BACKGROUND_TOOLS]
    allowed += [f"mcp__memory__{name}" for name in MEMORY_TOOLS]
    allowed += [f"mcp__notifications__{name}" for name in NOTIFICATIONS_TOOLS]
    allowed += [f"mcp__media__{name}" for name in MEDIA_TOOLS]
    allowed += [f"mcp__automations__{name}" for name in AUTOMATIONS_TOOLS]
    allowed += [f"mcp__crm__{name}" for name in CRM_TOOLS]
    allowed += [f"mcp__backup__{name}" for name in BACKUP_TOOLS]
    allowed += [f"mcp__media_analysis__{name}" for name in MEDIA_ANALYSIS_TOOLS]

    if config.BROWSER_SKILL_ENABLED:
        # Pinned to an exact version, not @latest: with a pinned version npx
        # can be satisfied entirely from its local cache once downloaded
        # once, with zero network round-trip on every subsequent spawn.
        # @latest instead makes npx hit the npm registry to resolve the
        # current version on EVERY spawn (a new client/session included) --
        # one slow/flaky registry response there was the real cause behind
        # a live "Playwright server timed out on connecting" failure.
        # Bump this string occasionally; it won't self-update anymore.
        # --console-level error: only real page errors in results, not every
        # info/debug log line. --codegen none: drop the "Ran Playwright code"
        # section the model never needs. (Both supported in 0.0.82.) The
        # big saver is browser_efficiency.py's snapshot digest hooks below.
        mcp_servers["playwright"] = {
            "type": "stdio",
            "command": "npx",
            "args": ["-y", "@playwright/mcp@0.0.82", "--console-level", "error", "--codegen", "none"],
        }
        allowed += [f"mcp__playwright__{name}" for name in PLAYWRIGHT_TOOLS]

    if config.GOOGLE_WORKSPACE_ENABLED:
        # Google's own gmailmcp.googleapis.com / calendarmcp.googleapis.com
        # gateway requires enrollment in the Google Workspace Developer
        # Preview Program, which personal @gmail.com accounts can't join --
        # confirmed by hitting it live. gmail_calendar.py talks to the
        # classic, generally-available Gmail/Calendar REST APIs instead,
        # using the same OAuth credentials, as an in-process SDK-MCP server
        # (no subprocess needed).
        from .gmail_calendar import GOOGLE_WORKSPACE_TOOLS, google_workspace_server

        mcp_servers["google_workspace"] = google_workspace_server
        allowed += [f"mcp__google_workspace__{name}" for name in GOOGLE_WORKSPACE_TOOLS]

    if config.BUFFER_ENABLED:
        # Buffer's own official remote MCP server -- an HTTP-type MCP
        # server (not stdio, not an SDK server we wrote), authenticated
        # with a personal API key as a bearer token. See
        # https://developers.buffer.com/guides/integrations/mcp.html.
        mcp_servers["buffer"] = {
            "type": "http",
            "url": "https://mcp.buffer.com/mcp",
            "headers": {"Authorization": f"Bearer {config.BUFFER_API_KEY}"},
        }
        allowed += [f"mcp__buffer__{name}" for name in BUFFER_TOOLS]

    if config.VIDEO_EDITOR_ENABLED:
        # Local stdio MCP server (npx-launched, same pattern as Playwright
        # below) -- no cloud account or API key needed at all; video_speech
        # (the one tool that would want a paid ElevenLabs/OpenAI key) is
        # deliberately excluded from VIDEO_EDITOR_TOOLS above, so it's never
        # allow-listed to begin with.
        # Pinned for the same reason as Playwright above -- see that
        # comment. This was previously unpinned (no version at all, which
        # npx also resolves as "latest" on every spawn).
        mcp_servers["video_editor"] = {
            "type": "stdio",
            "command": "npx",
            "args": ["-y", "@studiomeyer/mcp-video@1.0.3"],
        }
        allowed += [f"mcp__video_editor__{name}" for name in VIDEO_EDITOR_TOOLS]

    if config.CAP_ENABLED:
        # Local stdio MCP server, same pattern as Playwright above -- but
        # unlike Playwright/the video editor, `cap` is a real installed
        # binary on this machine, not something npx fetches on the fly
        # (see config.CAP_ENABLED's comment for the one-time install/login
        # steps). No API key/header here: Cap's own auth lives in its CLI's
        # local config from `cap auth login`, deliberately kept out of the
        # MCP protocol and out of Neural's config entirely.
        mcp_servers["cap"] = {
            "type": "stdio",
            "command": "cap",
            "args": ["mcp", "serve"],
        }
        allowed += [f"mcp__cap__{name}" for name in CAP_TOOLS]

    if config.ADZUNA_ENABLED:
        # In-process SDK-MCP server (no subprocess, no remote MCP gateway --
        # just a direct httpx call to Adzuna's own REST API), following the
        # same lazy-import pattern as automations.py above: jobs.py doesn't
        # actually import back from agent.py, but importing it lazily here
        # keeps every optional-integration import in one place and avoids
        # paying for httpx/the module load when the feature is disabled.
        from .jobs import JOBS_TOOLS, jobs_server

        mcp_servers["jobs"] = jobs_server
        allowed += [f"mcp__jobs__{name}" for name in JOBS_TOOLS]

    if config.PEXELS_ENABLED:
        # Same in-process SDK-MCP pattern as jobs/Adzuna above -- a
        # direct httpx call to Pexels' own REST API, lazily imported so
        # httpx/the module only loads when this feature is actually on.
        from .stock_footage import STOCK_FOOTAGE_TOOLS, stock_footage_server

        mcp_servers["stock_footage"] = stock_footage_server
        allowed += [f"mcp__stock_footage__{name}" for name in STOCK_FOOTAGE_TOOLS]

    kwargs = dict(
        allowed_tools=allowed,
        permission_mode=config.PERMISSION_MODE,
        system_prompt=_system_prompt_file_option(WORKER_SYSTEM_PROMPT if isolated else CHAT_SYSTEM_PROMPT),
        mcp_servers=mcp_servers,
        env={"CLAUDE_CODE_STREAM_CLOSE_TIMEOUT": "120000"},
        max_buffer_size=config.CLI_MAX_BUFFER_SIZE,
        # Claude's generic Task tool launches detached subagents. They can
        # keep an unfinished plan alive after the visible turn has ended,
        # then cause an unrelated later prompt to resume/poll that old work.
        # Neural's real capabilities stay available through its foreground
        # Bash and MCP tools; only this hidden background-worker mechanism is
        # blocked for interactive chat.
        disallowed_tools=["Task"],
        # Do not inherit globally-installed Claude skills/plugins. They can
        # introduce their own Task workers outside Neural's turn lifecycle;
        # every integration Neural needs is explicitly supplied above.
        setting_sources=[],
        hooks={
            "PreToolUse": [
                HookMatcher(matcher="Bash|Task", hooks=[_foreground_tool_guard]),
                # A bare full-page browser_snapshot saves to a file instead of
                # pasting the whole tree inline (see browser_efficiency.py).
                HookMatcher(matcher="mcp__playwright__browser_snapshot", hooks=[browser_efficiency.pre_snapshot_hook]),
            ],
            # Append a compact digest of each page snapshot so the model acts
            # on refs instead of reading ~10-80KB page trees every step.
            "PostToolUse": [
                HookMatcher(matcher="mcp__playwright__.*", hooks=[browser_efficiency.post_tool_hook]),
            ],
        },
    )
    if config.ANTHROPIC_MODEL:
        kwargs["env"]["ANTHROPIC_MODEL"] = config.ANTHROPIC_MODEL
    if resume:
        kwargs["resume"] = resume
    if not isolated:
        # Stream text deltas so replies are spoken while still being written
        # (see main.py / speech_stream.py). Automations don't need it.
        kwargs["include_partial_messages"] = True
    if not isolated and config.CHAT_THINKING == "off":
        # Spoken chat replies are 1-3 sentences: extended thinking there is
        # mostly latency before the first word plus extra tokens. Long,
        # multi-step work goes to background jobs / automations, which keep
        # the model's default thinking. Set CHAT_THINKING=default to undo.
        kwargs["thinking"] = {"type": "disabled"}

    if isolated:
        # Used for automation runs (see automations.py). Root cause of a
        # real bug found live: a scheduled "check the weather every
        # morning" run actually invoked a totally unrelated "Schedule
        # Cloud Agents" capability instead of doing the task -- because
        # PERMISSION_MODE=bypassPermissions already auto-approves every
        # tool the underlying `claude` CLI knows about, and leaving
        # setting_sources unset (the default) makes the CLI load ALL
        # filesystem settings (user + project + local), which is exactly
        # how it discovers whatever marketplace plugins/skills are
        # installed for the user's own everyday interactive CLI use --
        # including, on this machine, something that schedules brand-new
        # cloud-hosted agents. None of that has anything to do with
        # Neural's own local automations (only list_automations is ever
        # exposed to the model; creating/editing one is UI-only, see
        # automations.py), so a one-shot automation run gets isolated
        # from it entirely: setting_sources=[] stops the CLI from
        # discovering any of that (built-ins like Bash/WebSearch/WebFetch
        # aren't loaded via settings sources, so they're unaffected and
        # still work), and disallowed_tools hard-blocks the generic
        # Skill/Task tools as a second layer even if some skill is
        # exposed another way later.
        kwargs["setting_sources"] = []
        kwargs["disallowed_tools"] = list(kwargs.get("disallowed_tools") or []) + ["Skill", "Task"]

    return ClaudeAgentOptions(**kwargs)


def new_client(
    mode: str = "chat", isolated: bool = False, resume: str | None = None
) -> ClaudeSDKClient:
    """resume: a saved session_id (see session_state.py) to continue an
    existing conversation instead of starting a blank one -- used for the
    two long-lived shared clients (chat, tutor) so a backend restart
    picks the same conversation back up. Never pass this for isolated=True
    automation runs, which are meant to be fresh, disconnected sessions
    every time."""
    return ClaudeSDKClient(
        options=build_options(mode=mode, isolated=isolated, resume=resume)
    )


# Prepended to the query text (not the system prompt) on every message --
# every reply is now always spoken aloud (see main.py's /ws handler), not
# just ones that arrived via the mic, so this always applies rather than
# being conditional on how the message came in. The client is long-lived
# per websocket connection so its system_prompt can't change per-message --
# this per-turn wrapper is how spoken replies stay short (and markdown-
# free, since a screen reader for a chat log would be fine with a bullet
# list, but a text-to-speech engine reading "dash item" aloud is not)
# without needing a fresh client (and fresh conversation context) for
# every single message.
VOICE_MODE_PREFIX = (
    "[Voice mode is on -- this reply will be spoken aloud via text-to-speech. "
    "Reply in 1-3 short, natural spoken sentences. No lists, headers, markdown, "
    "code blocks, or URLs. Still use your tools normally if the task needs them, "
    "but summarize the outcome briefly instead of reciting long results.]\n\n"
)


# Tutor mode's own voice prefix: chat mode's VOICE_MODE_PREFIX above bans
# code/markdown outright, which is right for chat but too restrictive for
# a technical tutor -- code IS the point, sometimes. Instead of trying to
# programmatically strip spoken code out of a reply that's streamed and
# spoken sentence-by-sentence as it's generated (fragile -- a fenced code
# block can straddle several streamed chunks), this leans on the same
# prompt-level steering VOICE_MODE_PREFIX already relies on successfully:
# ask the model to keep the SPOKEN portion plain-language and put any
# exact code in a short block afterward, same mechanism, different words.
TUTOR_VOICE_MODE_PREFIX = (
    "[Voice mode is on -- this reply will be spoken aloud via text-to-speech AND "
    "shown as text in the chat log. Keep your explanation to 2-5 short, natural "
    "spoken sentences in plain language -- no headers or bullet lists in that part. "
    "If the answer needs actual code, a command, or an exact technical term, "
    "describe it briefly in words in your spoken sentences (e.g. \"here's a for "
    "loop over the list\") and put the precise code/command in a short fenced "
    "block AFTER those sentences -- keep the spoken explanation understandable on "
    "its own without needing that block read aloud. Still use your tools normally "
    "if the question needs them.]\n\n"
)


def wrap_for_voice(text: str, mode: str = "chat") -> str:
    # The two voice-response instructions now live in each mode's system
    # prompt, which is cached by the Claude CLI. Keeping a 50-100-token
    # wrapper in every user message paid that same cost on every turn,
    # despite never changing. This preserves the exact behavior without
    # removing a tool, reducing context, or limiting what Neural can do.
    return text


# Prepended to an automation's own prompt text when it's actually executed
# (see automations.py's run_automation). Automation prompts are written
# to describe a recurring routine ("every morning at 8am, check...") since
# that's what the user typed when setting it up -- but replayed verbatim
# at run time, that same wording reads exactly like a NEW request to set
# up a schedule, which is what actually caused the "Schedule Cloud
# Agents" derailment bug: the model reasonably (if wrongly, in this
# context) reached for a real scheduling capability instead of realizing
# it WAS that already-scheduled run. This wrapper heads that off at the
# prompt level, on top of the tool-level isolation in build_options().
AUTOMATION_MODE_PREFIX = (
    "[This is a live, automatic run of a task already scheduled inside Neural -- "
    "it is happening right now because that schedule just fired, NOT a request to "
    "create, change, or set up any schedule, cron job, or recurring cloud agent. "
    "Never use a scheduling, cron, or cloud-agent-scheduling tool or skill here, "
    "even if the wording below sounds like a recurring instruction -- that wording "
    "just describes what this automation already does on its own. Actually perform "
    "the task described below, right now, and answer with the real, final result "
    "in 1-3 short sentences, no markdown, headers, or lists -- this will be shown "
    "as a normal chat message and also spoken aloud via text-to-speech.]\n\n"
)


def wrap_for_automation(prompt: str) -> str:
    return AUTOMATION_MODE_PREFIX + prompt
