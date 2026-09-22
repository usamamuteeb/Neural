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

# JSearch (RapidAPI) -- on-demand real job listings aggregated from
# LinkedIn, Indeed, Glassdoor, and more via Google for Jobs, without
# browser-automating LinkedIn directly (and risking the account getting
# flagged). This is on-demand only -- a search_jobs tool the agent calls
# when asked, not a background automation -- see backend/jobs.py.
# Requires a free RapidAPI account + a JSearch subscription (the
# free/Basic plan) -- account creation isn't something Neural can do on
# your behalf, so sign up yourself at rapidapi.com, subscribe to JSearch,
# and paste the resulting X-RapidAPI-Key here. See the README's "Job
# search (JSearch)" section for the exact steps. NOTE: JSearch's precise
# free-tier request quota wasn't independently verified while building
# this integration -- check your own RapidAPI dashboard for the current
# number rather than trusting a remembered figure.
JSEARCH_API_KEY = os.getenv("JSEARCH_API_KEY", "")
JSEARCH_ENABLED = bool(JSEARCH_API_KEY)

WAKE_PHRASE = os.getenv("WAKE_PHRASE", "Wake up")

# Empty string = let the Agent SDK pick its own default model.
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "")

# Cheap "utility" tier for narrow, non-agentic background tasks (classify,
# summarize, filter) — never the main conversational loop and never given
# tools. See backend/utility.py and the plan doc's Core Engine section for
# why this replaces a wholesale swap to Haiku.
UTILITY_MODEL = os.getenv("UTILITY_MODEL", "claude-haiku-4-5-20251001")

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
# VAPID keypair itself on first run. This contact address is only used
# inside the signed VAPID JWT some push services log for abuse contact --
# a real address is a courtesy, not a requirement, for personal use.
#
# Deliberately `os.getenv(...) or default`, NOT `os.getenv(key, default)`:
# .env.example (and the .env generated from it) ships this key present
# but blank (VAPID_CONTACT_EMAIL=), same as the other optional keys in
# this file -- but unlike those (where blank correctly means "feature
# off", since they're all gated through bool(KEY)), this value is fed
# straight into every push's VAPID claims unconditionally. os.getenv's
# default only kicks in when a key is completely ABSENT, not when it's
# present-but-empty, so the plain form silently produced sub="" here --
# which pywebpush's own VAPID signing rejects outright ("Missing 'sub'
# from claims"), and every single push failed with that on every send.
# That's what made alerts never arrive at all despite valid
# subscriptions -- confirmed live via the diagnostic logging added
# above. `or` falls back on any falsy value, empty string included.
VAPID_CONTACT_EMAIL = os.getenv("VAPID_CONTACT_EMAIL") or "mailto:neural@localhost"
