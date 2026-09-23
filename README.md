# Neural

A personal agent built on the Claude Agent SDK, running against the user's
Claude Pro subscription (no separate API billing). See the "Neural — Agent
Architecture Plan" doc for the full design and open questions.

## Layout

- `backend/` — FastAPI app + Claude Agent SDK integration.
  - `/ws` — interactive chat: sends a message, streams back assistant
    text/tool-use/tool-result events, and audio -- every reply is always
    spoken now, synthesized and streamed back sentence-by-sentence as the
    model writes it rather than as one clip after the whole reply is
    done (see the Voice section below). Every device/tab shares ONE
    underlying agent client and conversation (serialized behind a lock,
    not run in parallel) rather than getting its own -- so asking
    something on desktop and following up from your phone actually has
    the earlier context, instead of two disconnected sessions. A newly
    (re)opened tab also gets replayed the recent chat
    history so it doesn't start on a blank screen.
  - `/events` — read-only live activity feed (recent history + a live
    stream), decoupled from `/ws` so it never blocks on an in-flight chat
    turn. Every tab that opens the UI sees the same feed.
  - `agent.py` — builds the SDK client: tool allowlist, MCP servers
    (memory, notifications, media, automations, jobs, Playwright, Google
    Workspace, Buffer, video-editor), system prompt (memory usage +
    conciseness + WhatsApp/Slack/Buffer/screen-recording/ChatGPT-image/
    ImageMagick instructions), and the voice-mode query-text wrapper.
  - `memory.py` — persistent markdown-file memory (`Neural/memory/`),
    exposed as SDK-MCP tools (`memory_list/read/write/append/search`).
    Always on.
  - `automations.py` — cron-scheduled background agent runs (APScheduler),
    persisted to `Neural/automations.json`, with REST CRUD under
    `/api/automations` for the UI. Also exposes a `list_automations`
    SDK-MCP tool so Neural itself can see what's actually scheduled --
    without it, the agent had no way to answer "what automations do I
    have" and would wrongly say none existed.
  - `gmail_calendar.py` / `google_auth.py` — Gmail + Calendar via the
    classic REST APIs (not Google's Workspace-only MCP gateway, which
    personal accounts can't use) using your existing OAuth credentials.
  - `tts.py` — three-tier voice: `edge-tts` (free neural voices, primary),
    ElevenLabs (optional paid middle tier, off by default -- past billing
    issues), then local `pyttsx3` as the always-works last resort.
  - `push.py` — Web Push (VAPID) to the phone/desktop: generates and
    persists its own VAPID keypair on first run, stores browser
    subscriptions in `Neural/push_subscriptions.json`, sends via
    `pywebpush`. See Notifications below.
  - `notifications.py` — the `send_notification` SDK-MCP tool, always on,
    so Neural itself can push you an alert ("let me know when...").
  - `media.py` — the `local_file_to_public_url` SDK-MCP tool: turns a local
    file into a public, token-gated, expiring URL (`Neural/media/`), for
    handing to services (Buffer) that fetch media by URL rather than
    accepting uploads.
  - `jobs.py` — the `search_jobs` SDK-MCP tool: on-demand job search via
    Adzuna's own official API (switched from JSearch/RapidAPI -- see Job
    search below for why) -- no browser automation against LinkedIn
    itself, no scheduled runs, just a tool the agent calls when you ask
    it to find jobs. Off until you add your own `ADZUNA_APP_ID`/
    `ADZUNA_APP_KEY`. Covers a specific set of countries only -- see
    below.
- `frontend/` — a plain HTML/JS Progressive Web App, no build step: a chat
  column plus a side rail with a live Activity feed and an Automations
  panel, now including an ALERTS toggle for push notifications. On narrow
  screens the side rail becomes a slide-over drawer (hamburger button in
  the top bar) instead of squeezing beside chat, with safe-area padding,
  larger touch targets, and a bottom-sheet automation modal. Served
  straight from `frontend/` by the backend.

WhatsApp and Slack both have no integration file of their own -- rather
than a bot API/token for each, Neural drives their real web clients
(web.whatsapp.com, app.slack.com) with the same Playwright browser tools
it uses for everything else (see `agent.py`'s system prompt). You log in
once -- scan the WhatsApp QR code with your phone, or sign into Slack
however your workspace requires (email/password or SSO) -- in whatever
browser window Playwright opens; the session persists across restarts on
its own (Playwright MCP keeps a persistent Chrome profile by default), so
you shouldn't need to log in again. Driving the real Slack web client
(rather than the Bot API) also means Neural can click things a bot
couldn't, like voting on a poll message.

LinkedIn/Instagram/X/Facebook/Threads/TikTok/YouTube/Pinterest go through
Buffer instead of browser automation, on purpose: those platforms have
some of the most aggressive bot detection on the web, and automating your
own accounts directly against them risks getting flagged or banned. Buffer
is a legitimate publishing tool with real OAuth to each platform, and it
ships its own official remote MCP server (`https://mcp.buffer.com/mcp`) --
so `agent.py` just registers that as an HTTP-type MCP server with your
personal Buffer API key, no bespoke client code at all. See Social posting
below.

## Setup

1. Make sure the `claude` CLI is installed and logged into your Pro/Max
   subscription on this machine (`claude login`). The Agent SDK uses that
   login — no separate API key needed, and usage draws from your
   subscription's normal limits.
2. `cd backend && python -m venv .venv && source .venv/bin/activate`
   (on Windows: `.venv\Scripts\activate`)
3. `pip install -r requirements.txt` (re-run this after pulling changes —
   `pywebpush` was just added for push notifications).
4. Copy `.env.example` to `.env` and fill in the values you're using
   (ElevenLabs voice, Google OAuth, model overrides, Buffer). Nothing to
   configure for WhatsApp or Slack -- see below.
   - **Voice**: the primary voice is now `edge-tts` -- free, no signup,
     on by default (`EDGE_TTS_ENABLED=true`). Pick which voice by actually
     listening to a few (see the Voice section below); the default is
     `en-US-AvaNeural`. ElevenLabs is now just an optional paid middle
     tier, **off by default** (`ELEVENLABS_TTS_ENABLED=false`) after
     hitting repeated `402 payment_required` errors -- your API key and
     voice ID can stay in `.env` either way, they're just not used while
     this is off.
   - **Buffer**: create a free account at buffer.com if you don't have one,
     connect each social channel you want Neural to post to from Buffer's
     own dashboard (Settings → Channels -- this is where each platform's
     real OAuth login happens, once, inside Buffer, not in Neural), then
     grab a personal API key from https://publish.buffer.com/settings/api
     and put it in `BUFFER_API_KEY`. If you'll ever post an image or video
     (not just text), also set up a tunnel (see Mobile access below) and
     put its HTTPS URL in `PUBLIC_BASE_URL` -- Buffer fetches media by URL,
     it can't accept a direct upload from Neural.
   - **Screen recordings**: install `ffmpeg` and make sure it's on PATH if
     you want Neural to be able to record your screen for a post (it
     shells out to ffmpeg directly, no Python wrapper library). On Windows,
     open PowerShell and run `winget install -e --id Gyan.FFmpeg`, then open
     a **new** terminal (PATH changes don't apply to already-open ones,
     including the one running Neural's server -- restart it too) and
     confirm with `ffmpeg -version`.
   - **WhatsApp**: nothing to configure. The first time you ask Neural to
     do something on WhatsApp, it opens web.whatsapp.com in its
     Playwright browser; scan the QR code with your phone once and the
     login persists after that. Note this drives your personal WhatsApp
     account through its real web client rather than an official API
     (none exists for personal accounts) -- fine for occasional use, but
     don't point it at high-volume/bulk messaging.
   - **Slack**: also nothing to configure -- no app, no bot token. The
     first time you ask Neural to do something on Slack, it opens
     app.slack.com (or your workspace's URL) in its Playwright browser;
     sign in once however your workspace normally requires and the
     login persists after that. Because this drives the real web client
     as you would, Neural can see/do anything your own account can,
     including clicking poll options -- something a Bot API integration
     couldn't do.
5. From the `Neural/` root: `uvicorn backend.main:app --port 8787`
   (Don't use `--reload` on Windows -- its process supervision leaves the worker on the wrong asyncio event loop, which breaks the Agent SDK's ability to spawn the `claude` CLI subprocess at all, failing every connection with `CLIConnectionError`. Restart the server manually after code changes instead.)
6. Open `http://localhost:8787` in a browser.

## Mobile access

The UI is responsive: on a phone-width screen the Activity/Automations
rail becomes a slide-over drawer (tap the hamburger icon top-right), the
automation form opens as a bottom sheet, and layout respects notch/home-
indicator safe areas.

A PWA only installs and gets push notifications over a secure origin
(HTTPS) or `localhost` — a plain LAN IP won't qualify on most phones. From
the same PC, `http://localhost:8787` satisfies that (so desktop
notifications work with zero extra setup). From an actual phone, the
recommended path is Tailscale, since it's private (only your own devices
can reach it, unlike a public tunnel) and free:

1. Install Tailscale on this PC: `winget install -e --id Tailscale.Tailscale`,
   or download from [tailscale.com/download](https://tailscale.com/download).
   Sign in (a Google/Microsoft/GitHub login works, no separate account
   needed) -- this creates your free personal tailnet.
2. Install the Tailscale app on your phone (App Store/Play Store) and sign
   in with the same account. Your phone and PC can now reach each other
   directly, wherever they are, without any port forwarding.
3. With Neural's server running (`uvicorn backend.main:app --port 8787`),
   open a terminal on the PC and run `tailscale serve 8787`. First time
   only, it prints a link to approve enabling HTTPS certificates for your
   tailnet -- open that link and approve it, then re-run the command.
4. It prints a URL like `https://your-pc-name.your-tailnet.ts.net` --
   that's Neural, reachable only by devices signed into your tailnet.
   (`tailscale serve status` shows it again later.)
5. On your phone (with the Tailscale app installed and connected), open
   that URL in the browser. Add it to your home screen for the full PWA
   experience, and tap the ALERTS pill to turn on push notifications.

`tailscale serve` is private to your tailnet by design -- fine for using
Neural from your own phone, but Buffer's servers aren't on your tailnet,
so it won't satisfy `PUBLIC_BASE_URL` for posting images/video (see Social
posting below). If you want that too, run `tailscale funnel 8787` instead
(same idea, but reachable from the public internet) and put the URL it
gives you in `PUBLIC_BASE_URL`. Cloudflare Tunnel or ngrok are the
alternatives if you'd rather not use Tailscale at all.

## Voice

**Every reply is spoken, always** -- not just ones sent from the mic. Typing
a message gets you the same short, spoken-style reply and audio as asking
out loud; there's no silent/text-only mode. This also means every reply
(typed or spoken) is instructed to stay to 1-3 short sentences with no
markdown/lists/URLs (see `agent.py`'s `VOICE_MODE_PREFIX`) -- necessary
since a screen full of bullet points read aloud verbatim sounds broken.

**Speaks as it writes, not after.** Rather than waiting for the whole
reply to finish generating and then synthesizing the whole thing (which
is what made voice replies feel laggy before), `backend/main.py`'s `/ws`
handler peels off each complete sentence as it streams in from the model
and sends it to TTS immediately, so playback of the first sentence can
start while later sentences are still being written/synthesized -- and
since voice replies are capped at 1-3 short sentences, a single long
sentence with no earlier full stop also gets split at its last
comma/semicolon once it runs past ~40 characters, so even a one-sentence
reply doesn't wait for the whole thing before speaking starts. Chunks
are tagged with a sequence number and the frontend plays them back in
that order even if a later chunk's speech synthesis finishes first
(network timing varies) -- see `tryPlayNextChunk`/`onAudioChunk` in
`app.js`.

**Barge-in.** Talking over Neural (or typing a new message) while it's
still speaking cuts the old reply off immediately -- both the audio
locally and, via an `interrupt` sent to the backend, the agent turn
actually generating it, not just muted playback while it keeps working
in the background. A `turn_id` on every message and event lets the
frontend tell a barge-in's new turn apart from any stray leftovers the
old, just-interrupted turn is still finishing up server-side, so they
don't bleed into the new turn's text or audio. This needed wake-word
listening to stay live through playback instead of pausing while Neural
talks (it used to) -- the real risk there is the mic picking up Neural's
own voice from the speakers as a false "you're talking" trigger; modern
Chrome/Edge apply real echo cancellation and this held up in testing, but
if it misfires for you, turn **WAKE WORD** off during playback or use the
push-to-talk mic button instead.

**Voice engine**: `backend/tts.py` tries three tiers in order --
1. **edge-tts** (primary, free) -- neural-quality voices via Microsoft
   Edge's "Read Aloud" backend, no signup, no API key. `pip install
   edge-tts` (already in `requirements.txt`). It's an unofficial API, so
   it can occasionally break for a few days if Microsoft changes its auth
   handshake -- that's what the tiers below are for, not something to
   worry about day to day.
2. **ElevenLabs** -- optional, off by default (`ELEVENLABS_TTS_ENABLED=false`
   in `.env`), since the paid plan was hitting `402 payment_required`
   errors. Flip it back on if you resubscribe.
3. **Local (`pyttsx3`)** -- the true last resort: always works, no
   network, but sounds robotic. Volume is set to max to avoid adding to
   the "too quiet" problem below.

**Picking a voice**: character varies more between edge-tts voices than
you'd expect, so listen before settling rather than trusting a name.
After `pip install edge-tts`:
```
edge-tts --list-voices | findstr en-US
edge-tts --voice "en-US-AvaNeural" --text "Hello, testing." --write-media test.mp3
```
Swap `en-US-AvaNeural` for any candidate (`en-US-AriaNeural`,
`en-US-JennyNeural`, `en-US-EmmaNeural`, `en-US-MichelleNeural` are also
well regarded) to hear it before committing, then set `EDGE_TTS_VOICE` in
`.env` to whichever you like. The default is `en-US-AvaNeural`.

**Wake word**: toggle **WAKE WORD ON** and Neural listens continuously
from then on -- say anything and it's sent as a command straight away,
without needing to repeat "Wake up" before every single thing you say.
The toggle itself is the "start listening" action; the wake phrase (still
configurable via `WAKE_PHRASE`) is now optional -- say it out of habit and
it's stripped off, or skip it and talk directly. Turn the toggle off (or
just use the push-to-talk mic button instead) when you don't want
everything the mic picks up sent to Neural -- this trades the old
repeat-the-wake-phrase friction for that risk, on purpose, per how you
actually wanted to use it.

**Volume**: playback is boosted beyond the browser's normal ceiling (a Web
Audio gain stage on top of the `<audio>` element, ~2.5x). If it's still
too quiet, `TTS_VOLUME_BOOST` in `frontend/app.js`'s `playAudio` section
is the one number to raise.

## Voice-triggered automations

Any automation can have a trigger phrase attached to it. Say (with
WAKE WORD on) or type that exact phrase and the automation runs
immediately -- no need to wait for its schedule, and no need to describe
what it does in your own words each time.

**Setup:** in the automations panel, click **+ New** and fill in the
optional **"Voice/text trigger phrase"** field alongside the name and
prompt -- e.g. name "Morning Briefing", trigger phrase "start my morning
brief". Leave it blank for an automation that should only run on its
schedule or via the ▶ button.

**How it actually works:** this is a plain string match, not the LLM
deciding whether your message sounds like a trigger -- when a message
(voice-transcribed or typed, the backend treats them identically) starts
with the exact trigger phrase, ignoring case/punctuation and tolerating
trailing words ("start my morning brief" matches "Start my morning
brief, please!"), the automation runs right away and you get an instant
"Starting your ... automation now" acknowledgment while it works in the
background -- the automation's real output (or an error) follows shortly
after as a normal spoken/written reply, exactly like a scheduled run. The
match has to be at the START of what you said, not just mentioned
somewhere in a longer sentence, so ordinary conversation that happens to
contain similar words won't misfire it.

**A looser natural-language option too:** if you don't say the exact
phrase -- "hey, can you kick off my newsletter thing" -- the agent has
its own `run_automation_now` tool and can start the right automation
by name using its own judgment, rather than trying to redo the
automation's task itself in that conversation.

**Current limitation:** there's no way to edit an existing automation's
trigger phrase (or its name/schedule/prompt) after creation -- only
delete and recreate it. Fine for now given how rarely these change once
set, but worth knowing before you go hunting for an edit button.

## Notifications

Tap the **ALERTS** pill in the top bar once; your browser asks for
notification permission, then Neural subscribes that browser/device to
push. No key setup needed -- `push.py` generates and persists its own
VAPID keypair the first time the server runs (`Neural/vapid_private_key.pem`,
gitignored; don't delete it, or every existing subscription breaks and
everyone has to re-enable ALERTS). Subscriptions live in
`Neural/push_subscriptions.json`.

What triggers a push:
- An automation finishes (success or failure) -- title names the
  automation, body is its result or the error.
- A real voice/TTS failure (not the routine "ElevenLabs isn't configured"
  notice, which fires on every voice turn until you set it up and would
  otherwise spam you).
- Neural itself, any time it calls its `send_notification` tool -- ask it
  to ping you about something ("let me know when...") and it will.

**Automation output now also lands in chat, not just as a push.** Every
automation result -- whether it fired on its own schedule or you clicked
"run now" -- is written into the chat log and spoken aloud on every
device that's currently open, the same as a normal reply, in addition to
the push notification above. That's the reliable path: a push can always
get lost to a browser/OS notification setting or a phone that's asleep at
the wrong moment, but the chat message is there waiting the next time you
open the app either way (it's saved into the same shared history as
everything else).

**Three real bugs were found and fixed here** (found by reading the
actual code and the actual data, not guessed at):
1. `push.py` called `webpush()` without a `ttl`, which the library
   defaults to `0` -- meaning "if the device isn't reachable *right this
   instant*, drop the message instead of queuing it." A phone that's
   asleep or backgrounded at the exact moment an automation fires (which
   is most of the time) would never get it, silently, with no error
   anywhere. Now sent with a 24h TTL so the push service holds it for
   the device to pick up later.
2. Nothing in this app ever called `logging.basicConfig(...)`, so every
   `logger.info(...)` call anywhere in the codebase was silently
   swallowed by Python's logging defaults -- there was no way to see
   from the terminal whether a push actually succeeded, failed, or never
   fired at all. Fixed in `main.py`; you'll now see a line like
   `Push notifications: 3 device(s) subscribed, VAPID key ready.` at
   every startup, and `Push: automation 'X' -> N device(s) reached.`
   every time one completes -- watch for these in the terminal running
   `uvicorn` if a push still doesn't show up, since they tell you whether
   the problem is delivery (0 reached) or the OS/browser just not
   showing the toast (N reached, but nothing appeared -- check Windows
   Settings > System > Notifications for the browser, and Focus
   Assist/Do Not Disturb).
3. A scheduled "check the weather every morning" automation's actual
   output, found live in `automations.json`, turned out to be the raw
   instructions of an unrelated "Schedule Cloud Agents" capability, not
   a weather report -- the model had reached for that instead of doing
   the task. Root cause: automations ran with the full, unrestricted
   tool surface of whatever `claude` CLI is installed on this machine
   (every plugin/skill it has, including something that schedules brand
   new cloud-hosted agents, unrelated to Neural's own local automations),
   because `PERMISSION_MODE=bypassPermissions` auto-approves everything
   and the default settings-loading behavior discovers all of that. Fixed
   two ways at once: automation runs now use an isolated client
   (`setting_sources=[]` so none of that gets discovered at all, plus
   `disallowed_tools=["Skill", "Task"]` as a second layer) and the
   automation's prompt is wrapped with an explicit "this is a live run of
   an already-scheduled task, not a request to schedule anything" note.
   `run_automation()`'s own result-parsing was also fixed to only ever
   capture the model's actual final text, not any stray tool/skill
   content that happened to have a `.text`-like attribute.

**A fourth bug, found once #1-3 above were already fixed:** desktop/
Android push started working, but iPhone still got nothing, now failing
with `403 {"reason":"BadJwtToken"}` from `web.push.apple.com`
specifically. Cause: `VAPID_CONTACT_EMAIL`'s own placeholder default,
`mailto:neural@localhost`. Google's FCM accepts a `@localhost` sub claim
without complaint, but Apple's push service validates it strictly and
rejects reserved/placeholder domains (`@localhost`, `@example.com`,
anything ending `.invalid`/`.test`) outright -- confirmed against real,
documented reports of this exact error from other projects hitting the
same thing. That mismatch is what made it so easy to miss: push looked
like it was working (desktop/Android succeeded) with nothing pointing at
this value as the reason iPhones got nothing. **This value has to be a
real `mailto:` address or a real `https://` URL you actually control,
not a placeholder** -- set to your own email now, and the app also warns
loudly at startup if this ever regresses to a placeholder-looking value.

**To verify push is actually reaching a device right now**, just ask
Neural in chat: "send me a test notification." It uses the exact same
`send_notification` -> `push.py` path an automation result does, and
tells you plainly how many devices it reached (0 means no working
subscription on that device yet -- re-tap ALERTS; check the Mobile
access section above if it's a phone over a plain LAN address).

## Social posting (Buffer)

Ask Neural to post/schedule something for LinkedIn, Instagram, X, Facebook,
Threads, TikTok, YouTube Shorts, or Pinterest and it uses Buffer's official
tools (`list_channels`, `create_post`, `edit_post`, `delete_post`,
`list_posts`) -- text goes straight through; an image or video first gets
turned into a public URL via `local_file_to_public_url` (see `media.py`
above), since Buffer attaches media by URL rather than accepting an upload.
Neural always shows you the exact channel(s), the full post text, and what
media (if any) is attached, and waits for your go-ahead before actually
calling `create_post` -- same rule as email/Slack/WhatsApp, just more
public and harder to undo.

Sourcing a video: point it at a file already on your machine (it'll find it
with its normal file tools), or ask it to record your screen -- that's a
plain `ffmpeg` command run via Bash, not a bespoke recorder, so it needs
ffmpeg installed (see Setup above). System-audio capture needs extra
Windows configuration Neural won't set up for you, so recordings default to
video only.

Editing a video: trimming, captions/subtitles, color grading, mixing in
music or narration, or converting a clip to a specific social format --
see Video editing (mcp-video) below. Off by default, and only worth
turning on once you want more than a plain unedited recording.

Sourcing an image: Claude doesn't generate images itself, so for a
generated (not existing) image, Neural drives your own ChatGPT account
through the browser the same way it drives WhatsApp/Slack -- log in once at
chatgpt.com, and it'll ask ChatGPT to generate the image, download the
result, and attach it. This is why the earlier "which subscription" choice
landed where it did: Buffer needs no AI subscription at all (it's a plain
API), and ChatGPT's role is specifically image generation, not posting.

Editing an image: once there's an image file (generated via ChatGPT above,
or an existing one), Neural edits it with ImageMagick's `magick` command
via Bash -- the same tool this project already uses to rasterize its own
app icons, so nothing new to install. Resize, crop, rotate, add a text
caption, or convert formats, all as plain one-line `magick` commands; for
anything more involved (multi-layer composition, precise masking) it'll
reach for Python + Pillow instead. No dedicated tool file for this, same
philosophy as screen recording -- a terminal command, not a bespoke
integration.

Known platform caveats, not Neural bugs:
- **TikTok**: a personal (non-Business) TikTok account connected to Buffer
  may only receive posts as a draft in TikTok's own inbox rather than
  auto-publishing -- that's a TikTok platform restriction on Buffer's API
  access, and Neural will tell you if that's what happened.
- **YouTube**: Buffer supports YouTube Shorts, not regular long-form video
  uploads.

## Job search (Adzuna)

Ask Neural to find jobs -- "find me remote senior python roles", "any
marketing manager openings in Chicago posted this week" -- and it calls
`search_jobs`, a single on-demand tool. This is deliberately on-demand
only, not a background automation -- it runs when you ask, not on a
schedule.

**Switched from JSearch/RapidAPI to Adzuna's own official API.** JSearch
went through two rounds of confirmed, real bugs (wrong endpoint path,
wrong param names/values) that got fixed live against RapidAPI's own
test console -- and then kept failing anyway with no clear cause visible
from the code or logs. Adzuna is the job board's own directly-documented
API rather than a third-party wrapper layered on top of one, so there's
one fewer thing that can be subtly wrong or go down independently of
anything in this codebase.

**Real limitation, not a bug: Adzuna only covers a specific set of
countries** -- `gb`, `us`, `de`, `fr`, `au`, `nz`, `ca`, `in`, `pl`, `br`,
`at`, `za` (check [developer.adzuna.com/docs/regional](https://developer.adzuna.com/docs/regional)
for the current list -- it has expanded before and may again). It does
**not** cover Pakistan, or most of Asia, the Middle East, or Eastern
Europe. `search_jobs` defaults to `us` unless you specify a different
supported country, and if you ask for one Adzuna doesn't cover, Neural
will say so plainly rather than guessing or quietly substituting
something else. There's also no dedicated remote-only filter -- just say
"remote" as part of what you're searching for.

One-time setup (Neural can't do this part for you -- creating accounts
on your behalf isn't something it will do):
1. Sign up for a free account at [developer.adzuna.com](https://developer.adzuna.com)
   -- instant, no credit card needed.
2. Your `app_id` and `app_key` are issued right away on your dashboard.
3. Paste both into `.env` as `ADZUNA_APP_ID=...` and `ADZUNA_APP_KEY=...`,
   then restart the server.

Until both are set, `search_jobs` just tells you (and Neural) that job
search isn't configured yet -- it won't error out or pretend to have
results. Free tier is capped at a few hundred calls/day -- check your own
developer.adzuna.com dashboard for the current number rather than
trusting a remembered figure here.

## Video editing (mcp-video)

Beyond the plain `ffmpeg` screen recording described above, Neural can use
[mcp-video](https://github.com/studiomeyer-io/mcp-video) (by StudioMeyer)
for real editing: trimming/cropping, color grading (22 LUT presets),
burning in captions or Whisper auto-subtitles, compositing clips
together, and converting a clip to a specific social format -- 7 tools
(`video_record`, `video_edit`, `video_color`, `video_audio`, `video_text`,
`video_compose`, `video_screenshot`).

**No account or API key needed at all.** The package exposes an eighth
tool, `video_speech`, for TTS narration via a paid ElevenLabs or OpenAI
key -- deliberately left out of Neural's allowlist, since ElevenLabs
credits run out quickly and an OpenAI API key bills separately from a
ChatGPT subscription (logging into chatgpt.com, the way image generation
does, doesn't grant API access). Instead, narration is free: Neural
generates it itself with the same `edge-tts` engine used for spoken chat
replies (`edge-tts --voice "..." --text "..." --write-media narration.mp3`
via Bash) and mixes the result in with `video_audio`. Same end result,
zero cost, nothing new to sign up for.

**Off by default**, unlike Playwright or Buffer. This was a deliberate
call, not an oversight: it's a small, newer, single-maintainer project
(MIT license, roughly 4 GitHub stars at the time it was wired in) rather
than an established one, so it's worth trying before trusting it with
real posts. To turn it on:

1. Make sure Node.js 18+ and `ffmpeg`/`ffprobe` are installed (both are
   already needed elsewhere in Neural, so most setups already have them).
2. Test it runs standalone: `npx -y @studiomeyer/mcp-video` should start
   without errors (Ctrl+C to stop) -- note the `@studiomeyer/` scope, a
   plain `npx -y mcp-video` resolves to an unrelated package and fails
   with "could not determine executable to run." It'll also need `npx
   playwright install chromium` the same way the browser skill does, if
   that hasn't already been run.
3. Set `VIDEO_EDITOR_ENABLED=true` in `.env` and restart the server.

If this proves flaky in daily use, set `VIDEO_EDITOR_ENABLED=false` again
-- nothing else depends on it.

## Stock footage (Pexels)

For a post that needs a video clip that isn't already on the machine,
Neural uses a `search_stock_video` tool backed by Pexels' own official
Video Search API -- a plain API call, no browser, no login session, no
daily-quota bookkeeping.

**Why real stock footage instead of AI-generated video:** three
different "free" AI video options were tried here, in order, and each
one turned out not to actually be free on an ongoing basis:

1. **Sora** (OpenAI) -- fully discontinued. Web/app shut down April 26,
   2026; API access followed on September 24, 2026. Not a cost
   trade-off, it's simply gone.
2. **Google Veo/Gemini** -- Google's own support docs say video
   generation needs a paid Google AI plan (at least Google AI Plus,
   ~$5-8/mo), contradicting some blog posts that claimed a free tier.
3. **Kling AI** -- looked promising (a genuinely free, no-card sign-up
   via Google/Apple/email, confirmed live), but the advertised "66
   credits" turned out, on checking the actual account, to be a
   one-time signup trial with an expiry date, not a recurring daily
   allowance. After it lapses, Kling is a paid subscription like the
   rest.

The pattern: every "free" AI video generator treats free as a one-time
signup hook to get you trying the paid product, not a real ongoing
tier -- not worth chasing a fourth vendor on the same premise. Pexels'
API, by contrast, is verifiably free forever (checked directly against
their own docs): no card, 200 requests/hour, 20,000/month, no trial
period, been offered this way for years.

**Setup:** free, instant signup at [pexels.com/api](https://www.pexels.com/api/)
-- no credit card, an API key is issued immediately. Paste it into
`.env` as `PEXELS_API_KEY`. Leave it blank to keep the feature off.

**How it fits together:** `search_stock_video` returns a direct,
downloadable file URL per result (fetched with a plain curl/httpx call,
no session needed), plus duration, resolution, and a credit link
(Pexels doesn't require attribution, but it's a nice touch). Neural
downloads the clip, then hands it to the video editor above for
trimming, captions, color grading, or narration, exactly like a screen
recording -- real footage is the raw material, the editor is what turns
it into finished content.

## Content creation (brand kit)

For any content aimed at a specific brand/project (as opposed to a
one-off ad hoc post), Neural keeps two things consistent automatically:

- **Voice default:** wise and no-nonsense at the same time -- thoughtful
  and credible, never fluffy or padded. Applies to every caption, video
  script, or narration line unless you say otherwise for a specific
  post.
- **Brand kit, built the first time you need it:** the first time you
  ask Neural to create content for a named brand it doesn't already
  know, it'll ask for the brand's niche, target audience, colors (hex
  codes or a logo file), and hashtag/handle conventions, then save the
  answer to `memory/brand/<slug>.md` so it's never asked again for that
  brand -- just tell it once, at the start of that project's first
  request.

No setup needed for this -- it's driven entirely by Neural's existing
memory tools (see the Layout section) the first time you brief it on a
brand.

## Working now

- Real-time chat over the same small toolset Claude Code itself uses —
  Bash, Read, Write, Edit, Grep, Glob — plus browser automation (Playwright
  MCP), Gmail/Calendar (custom REST integration), WhatsApp/Slack (both via
  the browser, see above -- including clicking Slack poll options), and
  Buffer for LinkedIn/Instagram/X/Facebook/Threads/TikTok/YouTube/Pinterest.
- Persistent memory: preferences and facts survive restarts, filed and
  recalled by the agent itself via the memory tools.
- A live Activity feed (separate from chat) showing every tool call as it
  happens, from any tab, including automation runs.
- Automations: cron-scheduled background prompts, managed from the UI
  (create/enable/disable/run-now/delete), backed by APScheduler, and
  queryable by the agent itself via `list_automations` so it can actually
  answer "what's automated" correctly.
- Voice-triggered automations (see Voice-triggered automations above):
  an optional trigger phrase per automation that runs it instantly by
  voice or text, plus a `run_automation_now` tool for looser
  natural-language requests.
- Response style tuned for brevity by default, always spoken as short
  natural sentences now regardless of whether you typed or spoke.
- Voice: every reply is always spoken (typed or mic input, no silent
  mode), streamed sentence-by-sentence as it's generated so playback
  starts almost immediately instead of after the whole reply is done,
  free neural TTS (`edge-tts`) as the primary voice, continuous
  wake-word listening (say the phrase once, keep talking until you turn
  it off), a volume boost on playback, and both ElevenLabs and a local
  fallback available underneath if the primary voice ever has a bad day.
- One shared conversation across every device/tab (desktop, phone, extra
  browser tabs) -- not a separate memory-less session per connection.
- Push notifications end-to-end: automation results (success or failure),
  real TTS/voice errors, and anything Neural itself decides to flag via
  `send_notification` -- reaches your phone/desktop even with the app
  closed, once you flip the ALERTS toggle. See Notifications below.
- Automation output (scheduled or manually run) is written into the chat
  log and spoken aloud on every open device, exactly like a normal reply
  -- not just a push notification or an Activity-feed entry. See
  Notifications below.
- On-demand job search via Adzuna (`search_jobs`): real listings, no
  LinkedIn automation risk, no RapidAPI middleman. Needs your own free
  Adzuna app_id/app_key -- see Job search above (and its country-
  coverage caveat).
- Image editing via ImageMagick (`magick` through Bash, no dedicated
  tool) -- resize, crop, rotate, caption, or convert any image, including
  ones ChatGPT just generated, before posting via Buffer.
- Video editing via mcp-video (off by default -- see Video editing
  above): trim, caption, color-grade, and compose clips beyond a plain
  screen recording.
- Stock footage via Pexels' free API (see Stock footage above): real
  video clips, no browser/login needed, as raw material for the editor
  above -- used instead of AI video generation after three separate
  "free" AI video options each turned out to require payment eventually.
- A brand kit built on first use per brand/project (see Content
  creation above): consistent voice, colors, audience, and hashtags
  without re-explaining them every time.
- A responsive, mobile-usable UI: slide-over drawer for Activity/
  Automations, bottom-sheet automation modal, safe-area-aware layout,
  larger touch targets.

## Not yet built (tracked in the plan doc's Open Questions)

- Wake-word listening + mic input work in Chrome/Edge via the browser's
  own speech recognition; Brave's version of that API is broken (a known
  Brave bug, not something fixable on our end) -- use Chrome/Edge for voice
- ChatGPT browser login for image generation -- one-time sign-in at
  chatgpt.com through Neural's own browser tools (same mechanism as
  WhatsApp/Slack), not yet done, so the image-generation flow described
  in Social posting above hasn't been exercised for real yet
- Video editing (mcp-video) confirmed wired up and enabled, but never
  driven with a real end-to-end prompt (trim/caption/color-grade an
  actual clip) -- only the underlying command was checked, not the tool
  in use

## Safety note

`permission_mode` is set to `bypassPermissions` in `backend/config.py` --
every tool call (Bash, file edits, browser automation, web search,
Gmail/Calendar) runs immediately with no confirmation step. This is
deliberate: there's no terminal or UI here for an interactive "allow
this?" prompt to go to, so a stricter mode doesn't add safety, it just
leaves tool calls stuck forever with nothing able to approve them. The
one exception is anything that publishes or sends -- `gmail_send`,
Slack/WhatsApp messages, and Buffer's `create_post` are all instructed
(in `agent.py`'s system prompt, not by the SDK's permission system) to
always show you the exact recipient/channel and content in chat and
wait for you to say go-ahead before actually sending or posting.

Bash and browser automation run with real, unconfirmed access to your
machine and accounts. Read `agent.py` / `config.py` before pointing this
at anything you care about, and tighten `ALLOWED_TOOLS` if you want a
smaller surface -- but note that setting `PERMISSION_MODE` back to
`acceptEdits` or `default` will make any non-file-edit tool call (web
search, Bash, browser actions) hang waiting for an approval this setup
has no way to give.
