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
  - `tutor.py` / `tutor_memory.py` / `docs_cache.py` / `knowledge_base.py`
    — Technical Tutor mode (see Technical Tutor mode below): a
    completely separate mode with its own system prompt (`tutor.py`),
    its own memory store under `Neural/tutor_memory/`
    (`tutor_memory.py`), a cached-and-summarized docs fetcher backed by
    `Neural/tutor_docs_cache.json` (`docs_cache.py`), and a local,
    no-API-key book/PDF knowledge base backed by
    `Neural/tutor_knowledge/kb.sqlite3` (`knowledge_base.py`, needs
    `pip install fastembed pypdf numpy`). `main.py` keeps this mode's
    shared agent client, lock, and chat history entirely separate from
    normal chat's (see its module-level comments).
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
5. From the `Neural/` root, start everything with one command:
   `powershell -File start_neural.ps1` -- this starts both the main
   backend (port 8787) and the public media server (port 8788, see
   Mobile access below), and safely stops/replaces any copies already
   running first. (You can still run `uvicorn backend.main:app --port
   8787` by hand if you only want the main backend and nothing else, but
   the script is the easy path, especially after code changes -- see
   "Restarting after changes / auto-start on reboot" right below.)
   Don't use `--reload` on Windows -- its process supervision leaves the
   worker on the wrong asyncio event loop, which breaks the Agent SDK's
   ability to spawn the `claude` CLI subprocess at all, failing every
   connection with `CLIConnectionError`.
6. Open `http://localhost:8787` in a browser.

### Restarting after changes / auto-start on reboot

Every code change (yours or Neural's own self-edits) needs a restart to
take effect, and previously that meant manually killing and re-launching
each process by hand. Two scripts fix that:

- **`start_neural.ps1`** -- run this any time to (re)start everything:
  stops any already-running main backend and public media server
  processes (matched by command line, so it's safe to run repeatedly --
  no "port already in use" `WinError 10048`), starts fresh copies of
  both, and re-asserts `tailscale serve 8787` / `tailscale funnel 8788`
  if Tailscale is installed (harmless no-op if that's already set up --
  see Mobile access below). Logs go to `neural_backend.log`/`.err` and
  `neural_media.log`/`.err` in the project root (all gitignored).
  ```
  powershell -File start_neural.ps1
  ```
- **`setup_autostart.ps1`** -- a **one-time** script you run yourself,
  once, in a real PowerShell window, that registers a Windows Task
  Scheduler task ("NeuralAutoStart") triggered **at login**, which runs
  `start_neural.ps1` automatically. After this, a laptop restart brings
  everything back on its own the next time you log in -- no manual
  restart needed. It's a logon trigger rather than a Windows Service on
  purpose: WhatsApp/Slack automation needs a real interactive desktop
  session, which a Service (running in an isolated SYSTEM session) does
  not have.
  ```
  powershell -File setup_autostart.ps1
  ```
  Test it immediately without rebooting: `Start-ScheduledTask -TaskName
  'NeuralAutoStart'`. Undo it later with `Unregister-ScheduledTask
  -TaskName 'NeuralAutoStart'`.

  This one-time setup step has to be run by you (or by asking Neural
  itself, since it has real command execution on this machine) directly
  in a PowerShell window on this PC -- it can't be done from a chat
  session that only has file access to this repo.

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
posting below).

**Do not run `tailscale funnel 8787` (or funnel/tunnel the main backend
at all) to get that.** Funnel makes that URL reachable by anyone on the
public internet, and `/ws` accepts any connection with zero
authentication -- no login, no API key, nothing -- then runs full agent
turns under `bypassPermissions`: arbitrary Bash, browser automation,
Gmail/WhatsApp/Slack, Buffer posting, everything Neural can do, on this
actual machine. Worse, Funnel provisions a real publicly-trusted TLS
certificate the moment it's turned on, and that certificate is logged in
public Certificate Transparency logs -- so the hostname isn't
meaningfully secret just because it looks like a random subdomain;
scanning CT logs for new certificates is a routine thing, not an
exotic attack. Funneling port 8787 hands unauthenticated remote control
of your machine and every connected account to whoever finds it.

**Use `backend/public_media_server.py` instead** -- a second, minimal
server that exposes only `/media/{token}` and nothing else (no `/ws`, no
`/api/*`, no chat, no automations). Run it alongside the main server, on
its own port:
```
uvicorn backend.public_media_server:app --port 8788
```
then `tailscale funnel 8788` (not 8787) and put that URL in
`PUBLIC_BASE_URL`. The only thing exposed is random-token file lookups
(`secrets.token_urlsafe(16)` -- 128 bits, not brute-forceable) that
expire after 24h and were only ever meant to go public anyway -- that's
the whole point of `local_file_to_public_url`. There's no meaningful new
exposure from someone finding that URL, unlike the main backend.
Cloudflare Tunnel or ngrok work the same way if you'd rather not use
Tailscale -- point either one at 8788, never at 8787.

Once both are set up, you don't need to re-run either `tailscale serve`
or `tailscale funnel` by hand again -- `start_neural.ps1` (see Setup
above) re-asserts both every time it runs, including automatically after
a reboot if you've set up `setup_autostart.ps1`.

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

Cutoff happens the instant speech is *detected*, not once you've finished
asking -- wake-word listening watches interim (not-yet-final) speech
recognition results, and the moment one shows real content while Neural
is speaking, it interrupts right then rather than waiting for you to stop
talking and the browser to finalize a transcript first (that wait used to
mean Neural kept right on talking for the full length of your interrupting
question). It then keeps listening to that same utterance as normal and
sends it once it finalizes -- there's no gap where the mic "isn't
listening yet." The push-to-talk mic button gets the same treatment: the
click itself interrupts immediately, before you've said anything. This
applies identically in Chat and Tutor mode (same shared pipeline); it's
most noticeable in Tutor mode simply because its spoken answers tend to
run longer.

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

## Technical Tutor mode

A completely separate mode from normal chat -- its own system prompt,
its own memory store, its own conversation on the backend -- for
actually studying technical material (a language, a framework, a CS
topic) rather than everyday assistant tasks. Switch to it with the
**Tutor** tab above the chat box (next to **Chat**); switching back and
forth never mixes the two conversations, and nothing said in one mode
is visible to the other. It works the same way on mobile as on desktop
-- same tabs, same side panel (which swaps from Automations to a
Knowledge Base panel while in Tutor mode), same voice input/output.

Switching modes is visually distinct on purpose, not just a label/tab
change: Tutor mode re-tints the whole UI (orb, glow, borders, the active
tab) to its own red/crimson palette instead of Chat's cyan-leaning one,
and the page background shifts to a dark red to match -- both animate
smoothly rather than snapping instantly (`--state-hue`/`--bg` transitions
in `style.css`). The orb itself also plays a brief animated flourish (a
scale bounce plus an outward ring pulse) at the exact moment you switch,
so the transition reads as a deliberate designed moment rather than a
plain color fade -- see `playModeSwitchAnimation()` in `app.js`.

Three layers, in the order they're worth setting up:

1. **Docs cache** (`backend/docs_cache.py`) -- the tutor's `fetch_doc_page`
   tool fetches an official documentation page, summarizes it, and
   caches that summary by URL. A later question about the same page
   reuses the cache almost instantly instead of re-fetching; it
   automatically re-fetches once the cache entry is older than
   `TUTOR_DOCS_CACHE_DAYS` (14 by default -- change it in `.env`). No
   setup, no API key -- it's just an HTTP fetch plus the existing cheap
   Haiku utility tier for summarizing (see `backend/utility.py`).

2. **Session memory** (`backend/tutor_memory.py` + `backend/tutor.py`) --
   Tutor mode keeps its own memory files under `tutor_memory/`,
   completely separate from normal chat's `memory/`. Two files matter
   specifically: `progress.md` is a running record of what you've
   actually mastered vs. what's still shaky, and `sessions.md` is a
   dated log, one entry per session, of topics covered/weak spots/open
   questions. A "session" is one continuous stretch in Tutor mode: when
   every device leaves Tutor mode (switching back to Chat, or closing
   the app), the tutor is asked to write a `sessions.md` entry and
   update `progress.md` before its conversation closes; the next time
   you open Tutor mode, a fresh conversation starts already primed with
   the current `progress.md` and the last session's summary, so you
   don't have to re-explain your level every time. No setup needed.

3. **Knowledge base** (`backend/knowledge_base.py`) -- point it at a book
   or PDF and the tutor grounds its answers in your own reference
   material instead of general training alone. In Tutor mode's side
   panel, click **+ Add** under Knowledge Base and give it a file path
   (a single `.pdf`/`.txt`/`.md`) or a folder path (every supported file
   directly inside it) -- Neural reads it straight from this machine,
   chunks it, and embeds it **locally** (nothing is uploaded anywhere).
   Needs one extra install: `pip install fastembed pypdf numpy`. The
   first ingestion after that downloads a small local embedding model
   (BAAI/bge-small-en-v1.5, ONNX, no torch, ~130MB, one-time). Without
   that install, the other two layers still work fine -- the tutor's
   `search_knowledge_base`/`list_knowledge_base_sources` tools just say
   plainly that the knowledge base isn't set up yet instead of failing.

Tutor mode's tool set is deliberately narrow -- the core file/terminal
tools (Bash/Read/Write/Edit/Grep/Glob, genuinely useful for running
example code) plus the three layers above, and nothing else: no Gmail,
Buffer, WhatsApp/Slack, video editor, job search, stock footage, or
automations tools. Saying a configured automation's voice/text trigger
phrase still works even while in Tutor mode (see Voice-triggered
automations above) -- that check happens before either mode's
conversation ever sees the message -- but the tutor itself can't create,
run, or ask about automations the way chat-mode Neural can.

Voice works exactly the same way it does in normal chat -- same mic
button, same wake-word listening, same TTS playback -- reusing the
existing pipeline entirely rather than a new one, so it doesn't carry
any new voice-specific risk. The one difference: chat mode's voice
replies are kept to short, code-free spoken sentences by design, while
Tutor mode's voice prefix allows full explanations and code in the text
reply, asking the model to keep just the *spoken* portion in plain
language and put any exact code in a short block after it.

## Personal CRM

Lightweight contact notes -- one markdown file per person under
`memory/people/<slug>.md`, in the same plain-file style as the rest of
memory (see Layout). No setup, no separate database.

- **Log an interaction:** just tell Neural about it in normal
  conversation ("talked to Sarah today about the proposal") -- it calls
  `crm_log_contact`, which updates that person's "Last contact" date and
  appends a short note in one step.
- **Standalone facts:** anything about a person that isn't tied to a
  specific conversation ("Sarah's the one who introduced me to the
  client", allergies, how you know them) goes through `crm_note` instead.
- **Follow-ups:** say "remind me to follow up with Sarah about the
  proposal" and Neural adds a checkbox item via `crm_add_followup`
  (optionally with a due date); ask "what do I owe people" or "who do I
  need to follow up with" and it scans every contact file and reports
  what's open (`crm_list_followups`); tell it something's handled and it
  checks the box (`crm_complete_followup`).
- **Daily reminder:** the CRM tools themselves don't run on a schedule --
  to get a proactive "here's what's outstanding" ping every morning,
  create a normal automation from the Automations panel with a prompt
  like `list my open CRM follow-ups` and a daily cron schedule. The
  automation infrastructure (see Automations below) does the rest.

Contact files live under `memory/`, so they're included in the daily
memory backup automatically (see Memory backups below) with no extra
wiring.

## Meal planning / grocery lists

Give Neural what's in your pantry and what you like, and it turns that
into a plan and a shopping list -- no new backend module, just two more
memory files plus normal chat.

- The first time you ask for a meal plan, Neural will ask what's
  currently in your pantry and any dietary preferences/dislikes, then
  save the answers to `memory/food/pantry.md` and
  `memory/food/preferences.md` so it doesn't ask again.
- Ask for a plan ("plan my meals for the week") and it builds one around
  what's already on hand first, respecting your saved preferences.
- It follows up with a grocery list of just what's missing, grouped by
  store section (produce, dairy, meat/protein, pantry, frozen, other) --
  not a flat ingredient dump.
- Tell it when you use something up or restock ("I'm out of eggs now",
  "picked up more rice") and it updates `pantry.md` so the next plan
  reflects reality.

## Post-performance feedback loop

Before suggesting new content for a brand you've posted for before,
Neural can check how past posts on Buffer actually did, using Buffer's
own analytics tools (`get_post` with `includeMetrics`, and
`get_aggregated_post_metrics` for a rolled-up view across a date range
and one or more channels) and fold what worked into its next
suggestion, instead of posting blind every time.

- Ask something like "how did last week's posts do" or "what should I
  post next for `<brand>`, based on what's worked" and it pulls the
  numbers and reasons about them in plain language (topic, format, time
  of day, channel), not just a raw metrics dump.
- What it learns gets saved to `memory/brand/<slug>-performance.md` so
  the pattern persists across conversations instead of being
  re-derived every time.
- **Caveat, stated plainly:** this was implemented against Buffer's
  official documented API (confirmed live via their own docs site, not
  assumed), but has not yet been exercised end-to-end against a real
  Buffer account's post history by me. Buffer's **free plan caps
  analytics history at 30 days** -- a request for a wider window will
  come back empty/truncated on free tier, which is a real platform
  limit, not a bug. Paid Buffer tiers ($5-10/mo) remove that cap if you
  need longer history.

## Newsletter/podcast script generation

Same content pipeline as social posts (brand kit, saved performance
notes), different output shape -- also no new backend module, just a
system-prompt addition reusing existing tools.

- **Newsletter:** ask for one and Neural drafts it in the brand's voice
  with a subject line, shows you the full draft, and only sends it via
  `gmail_send` after you explicitly say go-ahead (same confirmation
  rule as every other outbound message) -- or just hand you the draft
  to paste elsewhere if you'd rather send it yourself.
- **Podcast script:** structured as a cold open/hook, 2-4 main segments,
  and a close/call-to-action, written to be read aloud. Once you
  approve it, Neural synthesizes the actual episode audio with the same
  free `edge-tts` engine used for voice chat and video narration, and
  saves the result under `media/` -- you don't have to record it
  yourself.

## Memory backups

`memory/`, `tutor_memory/`, `tutor_knowledge/`, `automations.json`,
`tutor_docs_cache.json`, and `push_subscriptions.json` are all plain
files deliberately kept OUT of the main git repo (see `.gitignore`) so
personal facts/contacts/automations never end up somewhere that might
get pushed. That also means they had no version history at all -- an
accidental overwrite or a bad `memory_write` had nothing to fall back
on. Fixed with a completely separate, **local-only** git repository at
`.memory_backups/` (itself gitignored from the main repo) that mirrors
just those files.

- Runs automatically once a day (3:00 AM by default, on the same
  APScheduler instance the Automations feature already uses -- see
  `backend/backup.py`'s `schedule_backup()`), committing whatever
  changed since the last run. Unchanged files cost nothing extra --
  git dedups identical content, so daily commits of mostly-static files
  stay cheap indefinitely.
- On demand: ask Neural to "back up my memory now" (or just before you
  ask it to do something that feels risky to your memory files, like a
  bulk edit) and it calls `backup_now` directly instead of waiting for
  the schedule.
- To actually recover something: this is a normal git repo, so
  `git log`/`git diff`/`git checkout <commit> -- <path>` inside
  `.memory_backups/` works exactly like it would anywhere else -- open a
  terminal there if you ever need to roll something back.
- This is infrastructure, not a user-facing automation -- it
  deliberately doesn't appear in the Automations panel or
  `automations.json`, since it runs a plain Python/git operation, not
  an agent prompt.
- If `git` isn't on PATH at all, it falls back to a single
  always-overwritten plain-copy snapshot at `.memory_backup_snapshot/`
  instead of hard-failing -- no history, but still something. (Not a
  concern on a machine that already has git installed, which yours
  does.)

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
- Technical Tutor mode (see Technical Tutor mode above): a fully
  separate mode -- own system prompt, own memory, own conversation --
  with a docs cache, session-summary/progress memory, and a local
  (no-API-key) book/PDF knowledge base, switchable via the Chat/Tutor
  tabs above the chat box, on desktop and mobile alike.
- Personal CRM (see Personal CRM above): contact notes, last-contact
  tracking, and follow-up reminders backed by one markdown file per
  person.
- Meal planning / grocery lists (see above): weekly plans built around
  your actual pantry and preferences, plus a shopping list grouped by
  store section.
- Post-performance feedback loop (see above): Buffer analytics
  (`get_aggregated_post_metrics`) feeding into future content
  suggestions instead of posting blind.
- Newsletter/podcast script generation (see above): same brand-kit
  pipeline as social posts, output as an email draft or a read-aloud
  podcast script with synthesized episode audio.
- Memory backups (see above): a daily local-only git snapshot of every
  personal/runtime data file, plus an on-demand `backup_now` tool.

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
- Technical Tutor mode's knowledge base layer (`fastembed`/`pypdf`) has
  not been exercised with a real book/PDF end to end yet -- ingest one
  and ask a question that should hit `search_knowledge_base` to confirm
  chunking/embedding/retrieval actually behaves the way this was
  designed to before relying on it
- Post-performance feedback loop's Buffer analytics tools
  (`get_aggregated_post_metrics`, `get_post` with `includeMetrics`) are
  wired up against Buffer's documented API but have not been driven
  against a real Buffer account's post history end to end yet -- also
  remember free-tier Buffer accounts only get 30 days of analytics
  history
- Memory backups' git-based path has not been exercised against a real
  restore (`git checkout` pulling an old version back out of
  `.memory_backups/`) yet -- the commit side has only been sanity-
  checked by reading through `backend/backup.py`, not run live

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

None of this has a login screen: `/ws` (the chat socket) accepts any
connection with no authentication at all, and once connected it's this
exact `bypassPermissions` access. That's fine as long as it's only
reachable from this machine or your own tailnet (`tailscale serve`) --
**never put the main backend (port 8787) on a public tunnel/Funnel**.
See Mobile access above for why, and use `backend/public_media_server.py`
instead for the one thing that actually needs a public URL (Buffer
fetching media).
