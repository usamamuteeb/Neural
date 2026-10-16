import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "")

# Kill switch for ElevenLabs specifically -- separate from whether a key/
# voice id is even set. Defaults OFF: the account was hitting repeated
# 402 payment_required errors, which used to still be attempted on every
# single voice turn and spammed the Activity feed with error events. Flip
# to "true" once billing is sorted; the key/voice id above don't need to
# be touched. It now sits BEHIND edge-tts below in the fallback order --
# see EDGE_TTS_ENABLED -- rather than being the primary voice.
ELEVENLABS_TTS_ENABLED = os.getenv("ELEVENLABS_TTS_ENABLED", "false").lower() == "true"

# edge-tts -- free, no-signup neural voices via Microsoft Edge's "Read
# Aloud" backend (unofficial wrapper: https://github.com/rany2/edge-tts,
# `pip install edge-tts`). This is now the PRIMARY voice: genuinely close
# to ElevenLabs quality with zero setup and zero cost, replacing the paid
# voice that hit billing trouble. It's a reverse-engineered API, not an
# official one, so Microsoft occasionally tweaks its auth handshake and
# breaks it for a few days until the library catches up -- that's what
# the fallback chain (ElevenLabs if re-enabled, then the local voice) is
# for, in tts.py.
EDGE_TTS_ENABLED = os.getenv("EDGE_TTS_ENABLED", "true").lower() == "true"

# Pick a voice by actually listening -- character varies more than you'd
# expect between them. List every English voice with (after
# `pip install edge-tts`): `edge-tts --list-voices | findstr en-US` on
# Windows. Preview one before committing:
#   edge-tts --voice "en-US-AvaNeural" --text "Hello, testing." --write-media test.mp3
# Other well-regarded female voices worth trying: en-US-AriaNeural,
# en-US-JennyNeural, en-US-EmmaNeural, en-US-MichelleNeural.
EDGE_TTS_VOICE = os.getenv("EDGE_TTS_VOICE", "en-US-AvaNeural")

# Adzuna -- on-demand real job listings via Adzuna's own official API,
# not a RapidAPI wrapper. Replaced JSearch/RapidAPI here after that path
# went through two rounds of confirmed live bugs and then kept failing
# anyway with no clear cause -- see backend/jobs.py's module docstring
# for the full story. This is on-demand only -- a search_jobs tool the
# agent calls when asked, not a background automation.
# Signup is free and instant at https://developer.adzuna.com -- no
# credit card, an app_id/app_key pair is issued right away. Paste both
# below. See the README's "Job search (Adzuna)" section for the exact
# steps, including which countries Adzuna actually covers (a real
# limitation, not a bug -- it does NOT cover Pakistan, for example).
ADZUNA_APP_ID = os.getenv("ADZUNA_APP_ID", "")
ADZUNA_APP_KEY = os.getenv("ADZUNA_APP_KEY", "")
ADZUNA_ENABLED = bool(ADZUNA_APP_ID and ADZUNA_APP_KEY)

# Stock footage (Pexels) -- free, real video clips for content creation,
# used after three separate "free" AI video generators (Sora, then
# Google Veo/Gemini, then Kling AI) each turned out not to actually be
# free on an ongoing basis -- see backend/stock_footage.py's docstring
# for the full story. Confirmed against Pexels' own API docs: genuinely
# free forever, no card, 200 requests/hour, 20,000/month, no trial.
# Sign up instantly at https://www.pexels.com/api/.
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "")
PEXELS_ENABLED = bool(PEXELS_API_KEY)

# Vercel projects -- deploying, checking deployment status/logs, and
# managing environment variables, all driven straight through the
# official `vercel` CLI via Bash (already unconditionally available --
# see ALLOWED_TOOLS below), not a dedicated MCP server. Vercel's own
# remote MCP server (mcp.vercel.com) was considered first instead, but
# ruled out on inspection: its auth is interactive-OAuth-only with no
# static-token path at all, and its tool list has no environment-variable
# management tools whatsoever -- both non-starters for an agent that runs
# unattended (PERMISSION_MODE=bypassPermissions, no human around to click
# "allow" on an OAuth popup) and that needs real env-var read/write
# access. The CLI + a personal access token is Vercel's own documented
# non-interactive pattern instead -- see the official
# vercel-labs/agent-skills "vercel-cli-with-tokens" skill, which agent.py's
# system prompt follows.
#
# One-time setup:
#   1. `npm i -g vercel` on this machine if the `vercel` CLI isn't
#      already installed.
#   2. Create a personal access token at
#      https://vercel.com/account/tokens (it starts with `vca_`). Treat
#      it like a password -- Neural is instructed to always read it from
#      this environment variable and never pass it via a `--token` flag,
#      which would leak it into shell history and process listings.
#   3. Paste that token into VERCEL_TOKEN below.
# Optional, but skips an interactive `vercel link` prompt when Neural
# runs a command against a specific project: run `vercel link` once by
# hand inside that project's folder, then copy the orgId/projectId it
# writes to .vercel/project.json into VERCEL_ORG_ID/VERCEL_PROJECT_ID
# below (set both together or leave both blank -- one without the other
# is worse than neither). If you deploy under a team rather than your
# personal account, also set VERCEL_TEAM_SLUG (from the team's Vercel
# URL/settings) -- Neural passes it as --scope on every command so it
# hits the right account.
VERCEL_TOKEN = os.getenv("VERCEL_TOKEN", "")
VERCEL_ORG_ID = os.getenv("VERCEL_ORG_ID", "")
VERCEL_PROJECT_ID = os.getenv("VERCEL_PROJECT_ID", "")
VERCEL_TEAM_SLUG = os.getenv("VERCEL_TEAM_SLUG", "")
VERCEL_ENABLED = bool(VERCEL_TOKEN)

WAKE_PHRASE = os.getenv("WAKE_PHRASE", "Wake up")

# Technical Tutor mode -- how many days a cached, summarized doc page
# (see backend/docs_cache.py) is trusted before the next question about
# it triggers a re-fetch. No API key/account of any kind needed for
# Tutor mode's docs cache or session memory layers; the knowledge base
# layer (backend/knowledge_base.py) needs `pip install fastembed pypdf
# numpy` but also runs fully locally, no key either -- see that
# module's docstring.
TUTOR_DOCS_CACHE_DAYS = int(os.getenv("TUTOR_DOCS_CACHE_DAYS", "14"))

# Empty string = let the Agent SDK pick its own default model.
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "")

# Extended thinking for interactive chat turns: "off" (default) disables it
# -- spoken replies are short, and thinking mostly adds delay before the
# first word plus extra tokens. Background jobs and automations always keep
# the model's default. Set to "default" to let the CLI decide again.
CHAT_THINKING = os.getenv("CHAT_THINKING", "off").strip().lower()

# Managed background jobs (backend/background.py): long tasks the chat
# agent hands to a separate worker so the conversation stays free. Limits
# keep a stuck job from running forever: JOB_MAX_SECONDS total (kept under
# VIDEO_RENDER_MAX_SECONDS so the job's own cleanup always runs before the
# global reaper would), JOB_IDLE_TIMEOUT_SECONDS without any progress.
MAX_BACKGROUND_JOBS = int(os.getenv("MAX_BACKGROUND_JOBS", "2"))
JOB_MAX_SECONDS = int(os.getenv("JOB_MAX_SECONDS", str(25 * 60)))
JOB_IDLE_TIMEOUT_SECONDS = int(os.getenv("JOB_IDLE_TIMEOUT_SECONDS", str(10 * 60)))

# Same limits for scheduled automations, which previously had none at all
# (one stuck tool call could hang an automation run forever).
AUTOMATION_MAX_SECONDS = int(os.getenv("AUTOMATION_MAX_SECONDS", str(25 * 60)))
AUTOMATION_IDLE_TIMEOUT_SECONDS = int(os.getenv("AUTOMATION_IDLE_TIMEOUT_SECONDS", str(10 * 60)))

# Social media / video link analysis (backend/media_analysis.py) -- free and
# local: yt-dlp fetches metadata + captions + a small copy of the video,
# Whisper transcribes on-device when there are no captions, ffmpeg/PyAV
# grabs still frames for vision. Needs `pip install -U yt-dlp
# faster-whisper` in the backend venv.
#   MEDIA_WHISPER_MODEL: tiny / base / small / medium -- bigger is more
#     accurate but slower on CPU. "base" is a good default.
#   MEDIA_MAX_TRANSCRIBE_MINUTES: skip Whisper for caption-less videos
#     longer than this (captions are always used when present).
#   MEDIA_MAX_DOWNLOAD_MINUTES: don't download longer videos just for
#     frames (explicit frame_times still work).
#   MEDIA_COOKIES_FROM_BROWSER / MEDIA_COOKIES_FILE: optional, for
#     login-gated posts (Instagram, Facebook, private videos). Set the
#     browser name (firefox, chrome, edge) or a Netscape cookies.txt path.
MEDIA_WHISPER_MODEL = os.getenv("MEDIA_WHISPER_MODEL", "base").strip() or "base"
MEDIA_MAX_TRANSCRIBE_MINUTES = int(os.getenv("MEDIA_MAX_TRANSCRIBE_MINUTES", "45"))
MEDIA_MAX_DOWNLOAD_MINUTES = int(os.getenv("MEDIA_MAX_DOWNLOAD_MINUTES", "60"))
MEDIA_COOKIES_FILE = os.getenv("MEDIA_COOKIES_FILE", "").strip()
MEDIA_COOKIES_FROM_BROWSER = os.getenv("MEDIA_COOKIES_FROM_BROWSER", "").strip().lower()

# Voice input for Neural Desktop (backend/stt.py, POST /api/transcribe).
# Electron has no working Web Speech API, so the desktop app records a short
# clip and Whisper transcribes it locally (same faster-whisper install as
# media analysis above -- one model in memory when the names match).
#   STT_WHISPER_MODEL: "base" is fast on CPU (~0.5-1.5s per sentence);
#     "small" is more accurate but roughly 3x slower.
#   STT_LANGUAGE: "en" (default) is fastest and most reliable for English;
#     set another code (e.g. "ur") or leave empty to auto-detect per clip.
#   STT_INITIAL_PROMPT: words Whisper should expect (names, jargon).
STT_WHISPER_MODEL = os.getenv("STT_WHISPER_MODEL", MEDIA_WHISPER_MODEL).strip() or "base"
STT_LANGUAGE = os.getenv("STT_LANGUAGE", "en").strip().lower()
STT_INITIAL_PROMPT = os.getenv("STT_INITIAL_PROMPT", "Neural, Claude, Choco, Vercel, Buffer, YouTube.").strip()

# Cheap "utility" tier for narrow, non-agentic background tasks (classify,
# summarize, filter) — never the main conversational loop and never given
# tools. See backend/utility.py and the plan doc's Core Engine section for
# why this replaces a wholesale swap to Haiku.
UTILITY_MODEL = os.getenv("UTILITY_MODEL", "claude-haiku-4-5-20251001")

# Qwen is an optional *utility* provider, not a replacement for the Claude
# Agent SDK.  It only receives isolated one-shot text requests from
# utility.py; it has no tools, no conversation/session ownership, and no path
# to start, inspect, or resume a job.  In auto mode Qwen is used when a valid
# key is configured and Claude Haiku remains the safe fallback if Qwen is
# unavailable.  Set UTILITY_PROVIDER=claude to keep the existing behavior,
# or qwen to prefer Qwen while still retaining the Claude fallback.
UTILITY_PROVIDER = os.getenv("UTILITY_PROVIDER", "auto").strip().lower()
if UTILITY_PROVIDER not in {"auto", "claude", "qwen"}:
    UTILITY_PROVIDER = "auto"
# Qwen's own examples call this DASHSCOPE_API_KEY. Accept it as an alias so
# a key copied from Model Studio works without renaming it, while QWEN_API_KEY
# remains the clearer Neural-specific spelling.
QWEN_API_KEY = os.getenv("QWEN_API_KEY") or os.getenv("DASHSCOPE_API_KEY", "")
QWEN_BASE_URL = os.getenv("QWEN_BASE_URL", "").rstrip("/")
QWEN_UTILITY_MODEL = os.getenv("QWEN_UTILITY_MODEL", "qwen-flash")
QWEN_ENABLED = bool(QWEN_API_KEY and QWEN_BASE_URL)
UTILITY_TIMEOUT_SECONDS = float(os.getenv("UTILITY_TIMEOUT_SECONDS", "45"))

# Qwen pricing is model/region-specific and changes independently of Neural.
# Leave these at zero for usage-only dashboard reporting, or enter the live
# USD-per-million-token rates shown in the Model Studio console to record an
# estimate beside Claude's SDK-reported costs. They never affect routing.
QWEN_INPUT_USD_PER_MILLION = float(os.getenv("QWEN_INPUT_USD_PER_MILLION", "0"))
QWEN_OUTPUT_USD_PER_MILLION = float(os.getenv("QWEN_OUTPUT_USD_PER_MILLION", "0"))

# How long main.py's _run_turn will wait for the NEXT message from an
# in-progress turn before deciding the underlying session is wedged (a
# stuck tool call, or a `claude` CLI subprocess that stopped responding
# without exiting) and discarding it. Chat/tutor use ONE shared client
# across every device, serialized by a per-mode lock (see main.py) --
# without a bound here, a single hang holds that lock forever and every
# device is stuck until the whole backend is restarted by hand. 5
# minutes is generous enough not to false-trigger on legitimately slow
# tool calls (a video re-encode, a slow page load) while still making a
# real hang self-heal instead of requiring a manual restart.
TURN_IDLE_TIMEOUT_SECONDS = int(os.getenv("TURN_IDLE_TIMEOUT_SECONDS", "300"))

# Found live (Oct 2026): a video render/export/screen-recording tool call
# that keeps producing periodic output -- progress notifications from
# mcp-video's render, or `cap caps wait`/`cap export` streaming NDJSON
# progress lines -- never looks "idle" to TURN_IDLE_TIMEOUT_SECONDS above,
# because that one only measures the GAP between messages, not the
# turn's total length. A tool that's actively ticking but will never
# actually finish (a wedged ffmpeg filter graph, a Cap job stuck
# server-side) could therefore hold the shared per-mode lock forever --
# every other device/message just queues up behind it, looking to the
# user like Neural is "still rendering" no matter how long they wait or
# how many times they ask, since their new message never even starts
# being processed. This is an ABSOLUTE ceiling on one turn's total
# duration, independent of how much (or how little) silence there's
# been -- see main.py's _iter_with_timeout's overall_deadline parameter.
# Generous enough for a real multi-clip render with captions/narration,
# but bounded so a wedged one self-heals same-session instead of taking
# days.
TURN_MAX_DURATION_SECONDS = int(os.getenv("TURN_MAX_DURATION_SECONDS", str(20 * 60)))

# How long a turn gets to actually wrap up once the user explicitly
# interrupts it (barge-in, or typing a new message while an old one is
# still running -- see app.js's bargeIn/sendMessage), before main.py
# forcibly tears the whole client down instead of continuing to wait on
# it. This is NOT the same as TURN_IDLE_TIMEOUT_SECONDS above: that one
# only fires after true silence (no message at all from the SDK for the
# full timeout), but a turn stuck inside a long-running tool call --
# a screen recording, a video render, anything that shells out and
# blocks -- can keep emitting periodic tool_use/tool_result messages
# that reset the idle timer indefinitely, so it would otherwise run to
# completion no matter how long that takes even after being told to
# stop, because client.interrupt() only asks the MODEL to stop
# generating -- it cannot preempt a tool call the CLI has already
# dispatched and is waiting on. Once an interrupt is requested, this
# much shorter window replaces the long idle timeout for the REST of
# that turn, so a stuck tool call gets forcibly cut off (same recovery
# path as an idle timeout: the client is torn down and rebuilt fresh)
# well within seconds of the user actually asking to move on, instead of
# silently burning tokens on a reply the frontend has already committed
# to discarding (see app.js's turn_id filtering, and this fix's sibling
# in ws_endpoint's message loop that stops even STARTING a turn for a
# message that's already been superseded by a newer one).
POST_INTERRUPT_GRACE_SECONDS = int(os.getenv("POST_INTERRUPT_GRACE_SECONDS", "12"))

# backend/process_watchdog.py's safety net underneath BOTH timeouts
# above: even once a wedged turn is discarded at the SDK/conversation
# level, the actual OS process it was waiting on (ffmpeg, the mcp-video
# node process, or a `cap` CLI invocation) is a CHILD of the `claude` CLI
# subprocess and is NOT guaranteed to die when that subprocess is torn
# down -- disconnecting a parent process doesn't reliably kill still-
# alive descendants, especially on Windows. Left alone, that orphan
# keeps running (and consuming CPU) for however long it takes to fail on
# its own, which for a truly wedged render/export can be indefinitely --
# the literal mechanism behind "it's been rendering for days" even after
# the chat itself has already recovered. VIDEO_RENDER_MAX_SECONDS is how
# old one of Neural's own render/record/export processes (identified by
# name + confirmed by its cwd being THIS project -- see
# process_watchdog.py for why that check matters) has to be before the
# watchdog kills its whole process tree; REAP_INTERVAL_SECONDS is how
# often that sweep runs (see automations.py's start_scheduler). The
# sweep also runs immediately, out of band, every time a turn is
# discarded above, and once at startup, so a restart cleans up anything
# already stuck without waiting for the next tick.
VIDEO_RENDER_MAX_SECONDS = int(os.getenv("VIDEO_RENDER_MAX_SECONDS", str(30 * 60)))
REAP_INTERVAL_SECONDS = int(os.getenv("REAP_INTERVAL_SECONDS", str(5 * 60)))

# The claude-agent-sdk's own hard cap (ClaudeAgentOptions.max_buffer_size)
# on a single JSON message read from the `claude` CLI's stdout before it
# gives up parsing and raises CLIJSONDecodeError -- "JSON message exceeded
# maximum buffer size of 1048576 bytes" (see
# claude_agent_sdk/_internal/transport/subprocess_cli.py:
# _DEFAULT_MAX_BUFFER_SIZE = 1024 * 1024, i.e. 1 MiB, if left unset).
# Confirmed live: a Playwright browser_take_screenshot call on a busy page
# (e.g. YouTube Studio's channel customization screen) can return enough
# inline base64 image data in one tool_result to blow straight past that
# 1 MiB default, which crashes the ENTIRE turn outright (this error
# surfaces as the literal chat reply, not a caught/handled failure) rather
# than just that one tool call. 1 MiB is the SDK's own conservative
# default, not something tuned for an agent doing real screen automation,
# so this raises it well clear of what a single large screenshot/file
# read can produce. This does NOT fix the underlying cost of a huge
# image riding along in context -- see agent.py's system prompt for
# guidance on preferring smaller/targeted screenshots -- it only stops
# that case from crashing the turn outright.
CLI_MAX_BUFFER_SIZE = int(os.getenv("CLI_MAX_BUFFER_SIZE", str(20 * 1024 * 1024)))

# How far back start_scheduler() will look, at every restart, for an
# automation whose scheduled time already passed while Neural wasn't
# running -- see automations.py's start_scheduler docstring. APScheduler
# is using its default in-memory job store here (not a persistent one),
# so it has NO memory across a restart of what SHOULD have fired while
# the process was down -- every job is rebuilt from scratch on startup,
# and a freshly (re)built CronTrigger only ever looks FORWARD from "now,"
# so a missed 9am run was previously just silently skipped straight to
# tomorrow's 9am with nothing to show for it. 7 days is generous enough
# to catch "the laptop was off all weekend" without reaching back far
# enough to fire something that was due weeks ago and isn't relevant
# anymore. Only the single MOST RECENT missed occurrence is ever caught
# up, never one run per day missed -- see start_scheduler.
AUTOMATION_CATCHUP_LOOKBACK_DAYS = int(os.getenv("AUTOMATION_CATCHUP_LOOKBACK_DAYS", "7"))

# How old a saved chat/tutor session_id (see session_state.py) can be
# before it stops getting resumed and main.py starts a fresh conversation
# instead. Exists specifically to stop unbounded usage growth: "resume"
# doesn't mean the server remembers your history for free -- the `claude`
# CLI reconstructs and resends the ENTIRE prior conversation as input
# context on every single turn. Left uncapped, that conversation only
# ever grows, and since prompt caching's default TTL is 5 minutes, a
# personal assistant used in bursts throughout the day (not continuously)
# almost never gets a cache hit on that replay -- every turn, however
# small, pays full price to reprocess an ever-larger transcript, forever.
# This is what actually explained a user seeing ~17% of a 5-hour usage
# window burned by two small memory-search requests.
# 4 hours: long enough that a crash-and-quick-restart -- the original
# problem session_state.py was built to fix -- still keeps its context;
# short enough that a conversation realistically resets about once a day
# instead of growing for weeks. memory_write/memory_read remain the right
# place for anything that needs to survive past this cap.
SESSION_RESUME_MAX_AGE_SECONDS = int(
    os.getenv("SESSION_RESUME_MAX_AGE_SECONDS", str(4 * 60 * 60))
)

# Claude-standard toolset only: a terminal plus file read/write/edit and
# search. No task-specific tools — see the plan doc's "Tools & Skills
# Philosophy" section for why.
ALLOWED_TOOLS = ["Bash", "Read", "Write", "Edit", "Grep", "Glob"]

# "bypassPermissions" skips every tool-permission prompt entirely -- Bash,
# file edits, browser automation, web search, all of it. This is a
# single-user local agent with no one watching a terminal to answer an
# interactive "allow this?" prompt, so a stricter mode (e.g. "acceptEdits")
# doesn't add safety here, it just leaves tool calls permanently stuck
# with no way to approve them. The one deliberate exception is
# gmail_send, which asks for confirmation in the chat itself (see
# agent.py's system prompt) -- that's a prompt-level guardrail, not an
# SDK permission gate, so it still works under bypassPermissions.
# See the README's Safety note before changing this.
PERMISSION_MODE = os.getenv("PERMISSION_MODE", "bypassPermissions")

# Browser automation via the official Playwright MCP server. Needs
# Node.js/npx on this machine. Curated tool subset lives in agent.py.
BROWSER_SKILL_ENABLED = os.getenv("BROWSER_SKILL_ENABLED", "true").lower() == "true"

# Gmail/Calendar via Google's first-party remote MCP gateway
# (gmailmcp.googleapis.com / calendarmcp.googleapis.com). Requires a
# Google Cloud project + OAuth client under the personal Gmail account —
# see backend/google_auth.py. Off until all three are set.
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REFRESH_TOKEN = os.getenv("GOOGLE_REFRESH_TOKEN", "")
GOOGLE_WORKSPACE_ENABLED = bool(
    GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and GOOGLE_REFRESH_TOKEN
)

# Slack has no config of its own: like WhatsApp, it's driven entirely
# through the Playwright browser tools -- see agent.py's system prompt.

# Video editing (studiomeyer-io/mcp-video, npx-launched local MCP server) --
# trim/caption/color-grade/compose video clips before posting, on top of
# the plain ffmpeg screen recording that's always available. No account
# or API key needed at all: video_speech, the one tool of its eight that
# wants a paid ElevenLabs/OpenAI key, is deliberately left out of the
# allowlist in agent.py -- narration is generated for free instead via
# the edge-tts CLI (same engine as spoken chat replies) and mixed in with
# video_audio. Off by default: unlike Playwright, this is a small, new,
# single-maintainer project (~4 GitHub stars at the time this was wired
# in) rather than an established one, so it's opt-in until it's proven
# reliable rather than on by default. Flip to true once you've confirmed
# `npx -y @studiomeyer/mcp-video` runs on this machine (needs Node 18+
# and ffmpeg, both already required elsewhere in Neural).
VIDEO_EDITOR_ENABLED = os.getenv("VIDEO_EDITOR_ENABLED", "false").lower() == "true"

# Cap (cap.so, open-source CapSoftware/Cap) -- screen recording built with
# a real CLI and local MCP server FOR agents specifically (cap.so/agents),
# unlike the plain ffmpeg gdigrab capture used above: Cap gives you actual
# recording lifecycle management (start/stop/wait-for-processing),
# automatic transcription and AI summaries of what was recorded, and a
# whole existing library of past recordings the model can search and read
# transcripts/summaries from -- ffmpeg just dumps an unedited .mp4 and
# knows nothing about anything recorded before.
#
# Requires a real install on this machine first -- unlike Playwright/the
# video editor above, this is NOT an npx-launched zero-install package:
#   1. In an elevated PowerShell: irm https://cap.so/install-cli.ps1 | iex
#      (installs the `cap` CLI and Cap Desktop if it isn't already there --
#      the CLI ships bundled with Cap Desktop so both stay in version
#      lockstep). Open a new terminal, then confirm with `cap version --json`.
#   2. One-time browser login: `cap auth login --json` (or set the
#      CAP_API_KEY / CAP_AGENT_TOKEN env var instead for a fully headless
#      setup -- either is honored). This is deliberately NOT a Neural
#      config value below: Cap's own docs specifically keep credentials
#      out of the MCP protocol entirely and out of any agent's hands --
#      `cap auth login` persists its own token in Cap's own local config,
#      untouched by anything in this file or in Neural's .env.
#   3. `cap doctor --json` to confirm this machine is actually ready to
#      record (display access, permissions) before flipping this flag on.
# Screen capture itself needs an active, unlocked display session the same
# way ffmpeg's gdigrab does -- it is not something that can run against a
# locked/logged-out Windows session. Off by default: this is a genuinely
# new integration (Cap's own agent support launched in 2026) that needs
# that manual install/login step first, so it's opt-in the same way
# VIDEO_EDITOR_ENABLED is, not on-by-default the way Playwright is.
CAP_ENABLED = os.getenv("CAP_ENABLED", "false").lower() == "true"

# Buffer (buffer.com) -- posting/scheduling to LinkedIn, Instagram, X,
# Facebook, Threads, TikTok, YouTube Shorts, and Pinterest, all through
# Buffer's own OFFICIAL remote MCP server rather than a hand-rolled API
# client: https://developers.buffer.com/guides/integrations/mcp.html.
# One personal API key (from https://publish.buffer.com/settings/api)
# covers every channel you've connected inside Buffer's own dashboard --
# see the README for the one-time Buffer signup/connect-channels steps.
# Why go through Buffer at all instead of driving each platform's own
# web app like WhatsApp/Slack: LinkedIn/Instagram/TikTok/etc. have some
# of the most aggressive bot-detection on the web, and Buffer is a
# legitimate, ToS-sanctioned publishing path with real OAuth to each
# platform -- browser-automating your own social accounts directly would
# risk them getting flagged or banned.
BUFFER_API_KEY = os.getenv("BUFFER_API_KEY", "")
BUFFER_ENABLED = bool(BUFFER_API_KEY)

# The public HTTPS origin this server is reachable at (a tunnel --
# Tailscale/Cloudflare Tunnel/ngrok -- see the README's Mobile access
# section; the same tunnel already used for phone push notifications
# works here). Buffer attaches media to a post BY URL, not by upload, so
# posting anything with an image or video needs this set to something
# Buffer's servers can actually fetch -- never localhost, never a bare
# LAN IP. Text-only posts don't need this at all. See backend/media.py.
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")

# Web Push (VAPID) for phone/desktop alerts -- automation results, real
# tool/voice errors, and anything the agent flags via send_notification.
# No key to generate by hand: backend/push.py creates and persists a
# VAPID keypair itself on first run.
#
# This is NOT just a courtesy contact -- despite what it looks like, it's
# an actual functional requirement on iOS specifically. Two real, live
# bugs were found here, one after the other:
#   1. Deliberately `os.getenv(...) or default`, NOT `os.getenv(key,
#      default)`: .env shipped this key present but blank
#      (VAPID_CONTACT_EMAIL=), which os.getenv's default does NOT cover
#      (that only triggers when a key is fully ABSENT) -- so every push
#      went out with sub="", which pywebpush's own VAPID signing flatly
#      rejects. Every single push failed, every time.
#   2. Once fixed with a non-empty value, the *placeholder* value used
#      here (mailto:neural@localhost) turned out to be its own bug:
#      Google's FCM accepts a @localhost sub claim without complaint,
#      but Apple's push service (web.push.apple.com) validates it
#      strictly and rejects reserved/placeholder domains (@localhost,
#      @example.com, anything ending .invalid/.test) with a 403
#      "BadJwtToken" -- confirmed against real, documented cases of this
#      exact error. That's what made it look like push was "working"
#      (desktop/Android succeeded) while iPhones silently never got
#      anything, with no error obviously pointing at this value.
# Bottom line: this MUST be a real mailto: address or a real https://
# URL you actually control, not a placeholder -- see main.py's startup
# check below, which warns loudly if this still looks like one.
VAPID_CONTACT_EMAIL = os.getenv("VAPID_CONTACT_EMAIL") or "mailto:neural@localhost"


# ---- Voice: how much is spoken, and the free fallback voices ------------
# Only the opening of each reply is read aloud; the full text is always on
# screen. Keeps voice conversational instead of reading long answers top to
# bottom. Set SPEECH_OVERFLOW_NOTE= (empty) to drop the closing line.
SPEECH_MAX_SENTENCES = int(os.getenv("SPEECH_MAX_SENTENCES", "2"))
SPEECH_MAX_CHARS = int(os.getenv("SPEECH_MAX_CHARS", "300"))
SPEECH_OVERFLOW_NOTE = os.getenv("SPEECH_OVERFLOW_NOTE", "The rest is on screen.")

# After edge-tts fails (e.g. Microsoft changing its token, a 403), skip it
# for this long instead of failing on every sentence.
EDGE_TTS_RETRY_MINUTES = int(os.getenv("EDGE_TTS_RETRY_MINUTES", "30"))

# Piper: free, offline neural voice (pip install piper-tts). The voice
# downloads once into PIPER_VOICES_DIR. Female voices: en_US-amy-medium,
# en_US-lessac-medium, en_US-hfc_female-medium, en_GB-jenny_dioco-medium.
PIPER_ENABLED = os.getenv("PIPER_ENABLED", "true").lower() == "true"
PIPER_VOICE = os.getenv("PIPER_VOICE", "en_US-amy-medium")
PIPER_VOICES_DIR = os.getenv("PIPER_VOICES_DIR") or str(Path(__file__).resolve().parent.parent / "voices")

# Windows built-in voice (last resort). Part of a name, e.g. "Zira";
# empty picks the first female voice installed.
WINDOWS_TTS_VOICE = os.getenv("WINDOWS_TTS_VOICE", "")
