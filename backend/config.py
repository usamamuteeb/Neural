import os

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

# Cheap "utility" tier for narrow, non-agentic background tasks (classify,
# summarize, filter) — never the main conversational loop and never given
# tools. See backend/utility.py and the plan doc's Core Engine section for
# why this replaces a wholesale swap to Haiku.
UTILITY_MODEL = os.getenv("UTILITY_MODEL", "claude-haiku-4-5-20251001")

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
