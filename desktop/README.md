# Neural Desktop

Neural Desktop puts Neural on your desktop as a floating companion. It stays on top of every app. You can talk to it, type to it, or right-click it. It knows which window you're working in.

It doesn't have a brain of its own. It's a small client for the Neural backend you already run. That means:

- It uses your **Claude subscription**. There are no API keys and no paid services.
- It's **one shared conversation** with the web app and your phone.
- Voice is free and local. Replies use Neural's own voice (edge-tts). Your speech is transcribed on your PC by Whisper.

## Start it

1. Make sure Neural's backend is running (`start_neural.ps1`). If it isn't, Neural Desktop tries to start it for you.
2. Double-click **`Start Neural Desktop.bat`** in this folder. The first run installs Electron, which takes a minute.
3. For voice input, run this once and then restart Neural:

   ```
   backend\.venv\Scripts\python.exe -m pip install -U faster-whisper
   ```

   If that fails on your Python version, use `openai-whisper` instead.

Neural Desktop needs Node.js (LTS). To have it start with Windows, use **right-click → Behaviour → Start with Windows**.

## Using it

| Do this | To |
|---|---|
| Type in the box under her and press Enter | Send a message |
| Hold **Space** while her window is focused, or hold the **mic button** | Talk; release to send |
| Tap the mic button, or press **Ctrl+Alt+Space** anywhere | Talk hands-free; it stops when you pause |
| Hold **Right Ctrl** anywhere | Talk from any app (uses the optional `uiohook-napi` module) |
| Press **Ctrl+Alt+A** anywhere | Screenshot the active window, then ask about it |
| Press **Ctrl+Alt+N** anywhere | Jump to the text box |
| Press **Esc** | Stop her talking, or close the bubble |
| Start talking while she's speaking | Interrupt her. She turns her volume down the moment you start |
| Drag the bar above her head (shown on hover) | Move her. The bubble flips to whichever side has room |
| Drag the corner grip | Resize her |
| Click her head | Headpat |
| **Right-click** her | Quick actions and settings |

**Always listening** is off by default. Turn it on with right-click → Voice. Then start a sentence with "Neural…" and she answers. For about 10 seconds after she replies, you can follow up without saying her name. On Windows she also pauses listening while your PC is playing audio, such as a video or a call, so she doesn't react to it.

### Context awareness

- **Active window.** Every message tells Neural which app and window you're in, so a question like "what does this error mean?" makes sense to her. You can turn this off under Behaviour.
- **Automatic attachments.** If you mention "my screen", "this page" or "this code", a screenshot of that window is attached. If you mention "what I copied" or "clipboard", your clipboard is attached.
- **Suggestions.** When you hover over her, one-tap suggestions appear for the app you're in. For example: *Explain this code* in VS Code, *Summarize this page* in a browser, *What's this video about?* on YouTube, *What does this error mean?* in a terminal.
- **Copy text from the active window.** This uses Windows' built-in OCR. It works offline and uses no tokens.

### Instant local answers (no model turn, no tokens)

- "What time is it?" and "what's the date?"
- "Open Spotify", "open VS Code", "open YouTube". This also works for anything in your Start Menu and your custom commands.
- "Remind me to stretch in 20 minutes", "remind me tomorrow at 9am to email Ali", "set a timer for 5 minutes". Reminders survive restarts. Any you missed while the app was closed are shown when it starts again.
- "Find file called budget", "where is invoice.pdf". This searches the Windows Search index instantly. Click a result to open it, or click the folder icon to show it in its folder.
- "Stop" or "be quiet"

Anything that isn't one of these goes to Neural as a normal message.

### Offline

If the backend is unreachable, she dozes off. Messages you send are queued, saved to disk, and sent together when Neural comes back. Local answers, reminders, app launching and file search keep working while she's offline.

## If she crashes or won't open

- Double-click `Start Neural Desktop.bat` again. It closes any stuck copy first, then starts fresh.
- If her page crashes or freezes, she reloads herself.
- If she crashes twice in a row, she starts in **safe mode**: graphics acceleration, hold-to-talk anywhere, and the "pause while the PC plays audio" check are off. Everything else still works. Right-click → **Restart normally** to try the full version again.
- Crashes and errors are written to `desktop\logs\desktop.log`, which you can also open from the tray menu. Send that file if something keeps going wrong.

## Settings

Most settings are in the right-click menu. Everything else is in the settings file: right-click → **Settings file…**, which is `%APPDATA%\neural-desktop\settings.json`. That file has:

- `backendUrl`: the Neural backend address. Default `http://127.0.0.1:8787`.
- `hotkeys`: `voice`, `type`, `askScreen`, using Electron accelerator names. If a hotkey is already taken by another app, it's skipped. The error log says which one.
- `pushToTalkKey`: one of `RightControl`, `RightAlt`, `RightShift`, `CapsLock`, `F8`–`F12`, or `""` to turn it off.
- `wakeWords`: extra wake words for always-listening mode.
- `followUpSeconds`: how long after a reply she accepts a follow-up without her name. Set it to `0` to always require the wake word.
- `commands`: your own entries for the right-click menu and for "open …". Each has `{ "name", "type", "target" }`, where `type` is one of:
  - `url`: a web link
  - `open`: a file, folder or app path
  - `run`: a shell command
  - `prompt`: text sent to Neural

Restart Neural Desktop after editing hotkeys.

## How it's built

- `main.js`: the window, tray, hotkeys, screenshots, OCR, clipboard, launching apps, reminders, and HTTP calls to the backend.
  - The window is transparent and stays on top.
  - It's click-through everywhere except on her, the text box and the bubble.
- `preload.js`: the only bridge between the page and the main process. The page runs sandboxed, with no Node.js access.
- `renderer/`: the page itself.
  - She's the same `frontend/avatar.js` the web app uses, with lip-sync, 12 moods, blinking and eyes that follow your cursor.
  - `voice.js` records your voice and detects when you're speaking, then sends 16 kHz WAV clips to `/api/transcribe`.
- `lib/context.js` with `lib/winhelper.ps1`: a long-running Windows PowerShell helper that reports the foreground window, runs Windows OCR, and searches the Windows Search index.
  - It needs no native modules and nothing has to be compiled.
  - On macOS and Linux, the active window comes from `osascript` and `xdotool` instead.
- `lib/intents.js`, `lib/launcher.js`, `lib/filesearch.js`, `lib/suggest.js`: the instant local answers and the per-app suggestions.
- Backend additions in Neural's own backend:
  - `POST /api/transcribe`: local Whisper, in `backend/stt.py`.
  - `POST /api/speak`: speaks text in Neural's voice without a model turn.
  - Per-message `context`: added to the prompt but never saved to history.
  - `speak: false`: skips text-to-speech for a reply.

Run `npm test` to unit-test the helpers.

### Differences from the original spec, and why

| Spec | What's here | Why |
|---|---|---|
| Claude API | The Neural backend over `/ws` | Uses your subscription with no API key, and keeps one shared conversation and all of Neural's tools |
| Web Speech API | Local Whisper through the backend | Web Speech doesn't work in Electron, because it needs Google's private keys |
| ElevenLabs / OS TTS | Neural's existing voice chain (edge-tts) | Free, and the same voice everywhere |
| React + SQLite | Plain JS + JSON files | Less memory, nothing native to compile, and it reuses the web app's avatar |
| node-ffi for window detection | A persistent PowerShell helper | No native build step |
| Phoneme lip-sync | Lip-sync from the audio level | Already smooth with the avatar's mouth shapes, and needs no phoneme data from TTS |
