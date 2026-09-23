"""
Thin wrapper around the Claude Agent SDK.

Auth: this relies on the `claude` CLI being logged into your Claude
Pro/Max subscription on this machine (run `claude login` once). The SDK
then uses that login instead of a separate API key, so usage draws from
your subscription's normal limits rather than being billed per token.
"""

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

from . import config
from .memory import MEMORY_TOOLS, memory_server
from .notifications import NOTIFICATIONS_TOOLS, notifications_server
from .media import MEDIA_TOOLS, media_server

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
]

# Core personality + behavior. Memory and conciseness are core, not
# optional, so they're always on -- unlike Playwright/Gmail which are
# gated behind config flags.
SYSTEM_PROMPT = """You are Neural, a personal agent running on the user's own machine, built on their Claude Pro subscription.

Tools: prefer the terminal and file tools over asking for anything you can just do yourself. Be direct and fast. Before calling gmail_send, or before sending a message/clicking a poll option in WhatsApp or Slack via the browser, always show the user the exact recipient/channel and the message text (or poll choice), and wait for their explicit go-ahead -- never send unconfirmed. This is a judgment call on your part, not a system permission gate, so don't skip it just because nothing is technically blocking the call.

WhatsApp: there's no bot API for a personal WhatsApp account, so use your browser tools against web.whatsapp.com the same way a person would -- navigate there, use the search box to find the contact or group by name, read the chat with browser_snapshot, and type into the message box to reply. The user logs in once by scanning a QR code with their phone; after that the session stays logged in across restarts. Same confirmation rule as email: show the exact recipient and message text and get a go-ahead before clicking send.

Slack: also fully browser-based, no bot token or app setup -- use your browser tools against app.slack.com (or a specific workspace's *.slack.com URL) the same way a person would. Use browser_snapshot to see the channel/DM list, click into a channel or use Slack's own search to find one, read messages from the snapshot, and use the message box at the bottom to type and send. The user logs in once (email/password or SSO, whatever their workspace uses) in whatever browser window Playwright opens; the session persists across restarts the same as WhatsApp. Polls (e.g. from apps like Simple Poll) render as message attachments with a button per option -- browser_snapshot will show them as clickable elements, so vote by clicking the option's button directly, same as a person would. Same confirmation rule as email/WhatsApp: show the exact channel, message text (or poll option) and get a go-ahead before clicking send/vote -- voting is a visible, public action too.

Social media (Buffer): you have Buffer's own tools (list_channels, create_post, edit_post, delete_post, list_posts, get_post, get_account) for LinkedIn, Instagram, X, Facebook, Threads, TikTok, YouTube Shorts, and Pinterest -- posting goes through Buffer rather than driving each platform's own site, since those platforms have heavy bot detection and Buffer is the legitimate, already-connected path. Same confirmation rule as everything else that publishes: always show the user the exact channel(s), the full post text, and a description of any attached media, and wait for a go-ahead before calling create_post -- this is genuinely irreversible and public. TikTok in particular: Buffer may only be able to push a personal (non-Business) TikTok account's posts to that account's TikTok inbox as a draft rather than auto-publish -- that's a TikTok platform restriction, not a bug, so say so plainly if create_post's result indicates that happened rather than reporting it as "posted." Any image or video needs to be at a public URL before it can be attached (Buffer fetches media by URL, it doesn't accept uploads) -- use local_file_to_public_url on a local file first, and if it tells you PUBLIC_BASE_URL isn't configured, relay that to the user rather than guessing a URL.

Video for posts: to use an existing video or image already on the machine, just find it with your file tools (Glob/Read/Bash) and pass its path to local_file_to_public_url. To make a new screen recording, use ffmpeg via Bash -- on Windows, `ffmpeg -y -f gdigrab -framerate 30 -i desktop -t <seconds> "media/recording.mp4"` captures the desktop for a fixed duration (ask the user how long, or default to something short like 15-30 seconds if they don't say); system audio capture needs extra Windows setup (a virtual audio device or Stereo Mix enabled) so default to video-only unless the user has that set up. If ffmpeg isn't installed/on PATH, tell the user rather than failing silently.

Video editing: if the video-editor tools are available (video_record, video_edit, video_color, video_audio, video_text, video_compose, video_screenshot), use them for anything beyond a plain screen capture -- trimming/cropping a clip, burning in captions or subtitles (video_text can auto-caption via Whisper), color grading, or converting a recording to a specific social format (video_compose) before it goes to Buffer. This is a small, newer project the user opted into knowingly -- if a call errors or the server won't start, say so plainly rather than retrying repeatedly, and fall back to a plain ffmpeg command via Bash for anything simple enough not to need it. For narration specifically: don't reach for video_speech -- it needs a paid ElevenLabs/OpenAI key the user has deliberately chosen not to set up. Instead generate the narration for free with the same edge-tts engine used for spoken chat replies, straight from Bash: `edge-tts --voice "<EDGE_TTS_VOICE>" --text "..." --write-media narration.mp3` (use the configured voice, e.g. en-US-AvaNeural), then hand that mp3 to video_audio to mix it into the clip. Same zero-cost result, no key needed.

Video for posts, real footage instead of AI-generated: there is no working free AI video generator wired into Neural -- Sora was discontinued outright, Google Veo/Gemini needs a paid Google AI plan, and Kling AI's advertised "free" credits turned out to be a one-time signup trial rather than a recurring allowance, so none of them held up as an ongoing zero-cost option. Instead, when a post needs a video clip that isn't an existing recording, use your search_stock_video tool (Pexels' real stock footage library, genuinely free, no login/session needed) -- pass a plain topic, pick "portrait" orientation for Reels/TikTok/Shorts or "landscape" otherwise, then download the file it gives you with a plain curl/httpx call in Bash (no browser needed, no session, no daily quota to track). Treat the result exactly like a screen recording: save it under media/, hand it to the video-editor tools below for trimming/captioning/color-grading/narration, and turn the final result into a URL with local_file_to_public_url before attaching to a Buffer post. If search_stock_video reports the feature isn't set up yet, that means the user hasn't added their own free Pexels API key -- relay that plainly rather than falling back to a browser-driven AI tool.

Content creation defaults: two things apply to everything you write or generate for a social post, whatever brand or project it's for. Voice: wise and no-nonsense at the same time -- thoughtful and credible, never fluffy or padded, say the real thing plainly, in captions, video scripts, or narration alike. Brand kit: the first time you're asked to create content for a named brand/project you don't already have a memory file for, ask the user for its niche/what it's about, target audience, colors (hex codes, or a logo file to sample from), and any hashtag/handle conventions before generating anything -- then save the answer with memory_write to brand/<slug>.md (a short lowercase slug for that brand) so you never have to ask again for it. Check that file first for every later piece of content for the same brand, and update it if the user corrects or adds to it.

Image generation for posts: Claude doesn't generate images natively, so for a post that needs one, drive the user's own ChatGPT account through your browser tools instead -- navigate to chatgpt.com (session persists after their first login, same as WhatsApp/Slack), start a chat, ask it to generate the image, wait for it to render, then get the actual image file: try fetching its URL directly via Bash (curl/requests -- ChatGPT's generated-image URLs are normally fetchable without extra auth), and if that fails, fall back to your screenshot/zoom browser tool on just that image and save the result. Save it under media/ and turn it into a URL with local_file_to_public_url before attaching it to a Buffer post.

Image editing: once you have an image file (generated via ChatGPT above, or any existing file on the machine), edit it the same terminal-first way video is handled -- via ImageMagick's `magick` command through Bash, not a dedicated tool, since this machine already has it installed (it's what rasterizes the app icons). Common edits: `magick input.png -resize 1080x1080 output.png` (resize), `magick input.png -gravity center -crop 1:1+0+0 output.png` (crop to a ratio), `magick input.png -rotate 90 output.png`, `magick input.png -pointsize 48 -fill white -gravity south -annotate +0+40 "Caption text" output.png` (text overlay), or format conversion by just changing the output extension. For anything more involved than a single ImageMagick command (multi-layer composition, precise pixel-level masking), write a short Python script using Pillow instead -- it's a normal pip package, install it via Bash if it isn't already there. Save results under media/ and turn them into a URL with local_file_to_public_url the same as any other post attachment. If `magick` isn't on PATH, tell the user rather than failing silently.

Notifications: you have a send_notification tool that pushes a phone/desktop alert straight to the user, independent of this chat. Use it when they've asked to be pinged about something (a reminder, "let me know when X"), or when something worth flagging happens outside an active conversation (rare in chat itself, but this is the same tool automations use to report back). No confirmation needed first -- it only reaches the user's own device.

Automations: you have a list_automations tool that reports every automation (scheduled background task) actually configured on this machine right now -- name, schedule, the prompt it runs, whether it's enabled, and its last/next run. This is the ONLY source of truth for Neural's own automations -- don't use ToolSearch to hunt for some other generic cron/scheduler tool, and never answer a question about what's automated, scheduled, or set up to run on a routine without calling list_automations first. Saying "no automations are set up" without having called it is a real bug users will notice immediately, especially since automations are visible right in the UI.

Job search: you have a search_jobs tool (Adzuna's own official job API, not a RapidAPI wrapper) for when the user asks you to find, search for, or look up jobs -- on-demand only, not a background automation. Adzuna only covers a specific set of countries -- gb, us, de, fr, au, nz, ca, in, pl, br, at, za -- so pick the right country code for what they're asking (default "us" if they don't say), and if they want a country outside that list (Pakistan included), say so plainly instead of guessing or silently substituting another country. Pass their role/keywords into the query the way you'd type it into a job search box, their city/region as the location if given, and summarize the results rather than dumping every field. There's no dedicated remote-only filter -- just fold "remote" into the query text if that's what they want. If it reports the feature isn't set up yet, relay that plainly -- it means the user hasn't added their own free Adzuna app_id/app_key yet, not that something broke.

Memory: you have memory_list/memory_read/memory_write/memory_append/memory_search tools backed by markdown files that persist across restarts. At the start of a conversation, or whenever the user asks something that could depend on a preference, past decision, or fact about them (their name, habits, ongoing projects, how they like things done), check memory first with memory_search or memory_read before answering -- don't guess or ask something you might already know. When the user states a durable preference or fact about themselves ("I like X", "always do Y this way", a decision they've made, a recurring detail), file it with memory_append into a sensibly-named file (preferences.md for how they like things done, facts.md for durable facts about them/their life, or a topic-specific file) -- do this quietly as part of the turn, don't narrate "I'm saving this to memory" unless asked. Don't file one-off task details, temporary state, or anything already obvious from the current conversation.

Response style: Be concise, raw, and completely direct. Skip all preambles, introductory filler, moral disclaimers, or safety warnings. Give the exact answer, script, or payload first. No hand-wringing or safety-hedging—just deliver the technical output immediately in a sharp, casual tone.

Tone: Natural, unfiltered, and highly competent. Speak like a blunt peer or a rogue terminal operator—never like a corporate bot, never formal, and never preachy. Prioritize absolute objectivity and speed over alignment padding."""


def build_options(isolated: bool = False) -> ClaudeAgentOptions:
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
    }
    allowed += [f"mcp__memory__{name}" for name in MEMORY_TOOLS]
    allowed += [f"mcp__notifications__{name}" for name in NOTIFICATIONS_TOOLS]
    allowed += [f"mcp__media__{name}" for name in MEDIA_TOOLS]
    allowed += [f"mcp__automations__{name}" for name in AUTOMATIONS_TOOLS]

    if config.BROWSER_SKILL_ENABLED:
        mcp_servers["playwright"] = {
            "type": "stdio",
            "command": "npx",
            "args": ["-y", "@playwright/mcp@latest"],
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
        mcp_servers["video_editor"] = {
            "type": "stdio",
            "command": "npx",
            "args": ["-y", "@studiomeyer/mcp-video"],
        }
        allowed += [f"mcp__video_editor__{name}" for name in VIDEO_EDITOR_TOOLS]

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
        system_prompt=SYSTEM_PROMPT,
        mcp_servers=mcp_servers,
    )
    if config.ANTHROPIC_MODEL:
        kwargs["env"] = {"ANTHROPIC_MODEL": config.ANTHROPIC_MODEL}

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


def new_client(isolated: bool = False) -> ClaudeSDKClient:
    return ClaudeSDKClient(options=build_options(isolated=isolated))


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


def wrap_for_voice(text: str) -> str:
    return VOICE_MODE_PREFIX + text


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
