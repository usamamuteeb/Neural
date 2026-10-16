"use strict";
/*
 * Neural Desktop -- main process.
 *
 * A small always-on-top companion window for Neural. It's a thin client:
 * all the intelligence stays in the local Neural backend (Claude Agent SDK
 * on your Claude subscription, no API keys), reached over the same /ws
 * socket the web app uses, so it's one shared conversation across the
 * desktop, the browser and your phone.
 *
 * This process owns everything the page can't do itself: the transparent
 * click-through window and its geometry, the tray, global hotkeys and
 * hold-to-talk, which window you're working in (lib/context.js), window
 * screenshots, Windows OCR, clipboard, file search, launching apps,
 * reminders, and the HTTP calls to the backend (speech-to-text, TTS).
 */
const {
  app, BrowserWindow, ipcMain, screen, Tray, Menu, nativeImage, globalShortcut,
  clipboard, shell, desktopCapturer, Notification, session,
} = require("electron");
const path = require("path");
const fs = require("fs");
const os = require("os");
const { spawn } = require("child_process");

const { Settings, DEFAULTS } = require("./lib/settings");
const layout = require("./lib/layout");
const { ContextTracker, friendlyApp, ocrImage, searchIndex } = require("./lib/context");
const launcher = require("./lib/launcher");
const fileSearch = require("./lib/filesearch");
const suggest = require("./lib/suggest");
const intents = require("./lib/intents");
const { Health } = require("./lib/health");

const health = new Health({ logDir: path.join(__dirname, "logs"), stateDir: app.getPath("userData") });
const log = (...a) => health.log(...a);
{
  const origError = console.error.bind(console);
  console.error = (...a) => {
    origError(...a);
    log("ERROR", ...a);
  };
}
process.on("uncaughtException", (e) => log("uncaughtException:", e));
process.on("unhandledRejection", (e) => log("unhandledRejection:", e));

if (!app.requestSingleInstanceLock()) {
  // Another copy is already running -- it gets a "second-instance" event and
  // shows (or repairs) itself. Start Neural Desktop.bat also clears out a
  // stuck old copy before launching, so this never leaves you with nothing.
  log("another instance is running -- handing over to it");
  app.quit();
  process.exit(0);
}

if (health.safeMode) {
  // Transparent always-on-top windows are where GPU drivers misbehave most.
  app.disableHardwareAcceleration();
}
log(
  `start: electron ${process.versions.electron}, chrome ${process.versions.chrome}, ${process.platform} ${os.release()}`,
  health.previousCrashed ? `-- previous run did NOT exit cleanly (${health.crashes} in a row${health.lastReason ? ", last: " + health.lastReason : ""})` : "",
  health.safeMode ? "-- SAFE MODE (no GPU, no global key hook, no system-audio capture)" : ""
);

app.setAppUserModelId("com.neural.desktop");
// The window is never focused most of the time -- keep animations, audio
// and timers running at full speed anyway.
app.commandLine.appendSwitch("disable-renderer-backgrounding");
app.commandLine.appendSwitch("disable-background-timer-throttling");

let settings;
let win = null;
let tray = null;
let tracker = null;
let av = null;              // avatar rect on screen (DIP)
let side = "left";          // bubble side
let appIndex = [];
let hotkeyFailures = [];
let pttStatus = "off";
let backendStatus = "connecting";
let lastBackendStart = 0;
let quitting = false;

const timers = { drag: null, dragSafety: null, resize: null, cursor: null, reminders: null, index: null };

// ---------------------------------------------------------------- helpers

function send(channel, payload) {
  if (win && !win.isDestroyed()) win.webContents.send(channel, payload);
}

function neuralRoot() {
  return settings.get("neuralRoot") || path.resolve(__dirname, "..");
}

function baseUrl() {
  return String(settings.get("backendUrl") || DEFAULTS.backendUrl).replace(/\/+$/, "");
}

function isLocalBackend() {
  try {
    return ["127.0.0.1", "localhost", "[::1]"].includes(new URL(baseUrl()).hostname);
  } catch (e) {
    return false;
  }
}

function publicSettings() {
  const s = settings.get();
  return {
    speakReplies: s.speakReplies, followCursor: s.followCursor, alwaysListening: s.alwaysListening,
    pauseOnSystemAudio: s.pauseOnSystemAudio, wakeWords: s.wakeWords, followUpSeconds: s.followUpSeconds,
    contextInMessages: s.contextInMessages, autoAttach: s.autoAttach, suggestions: s.suggestions,
    hotkeys: s.hotkeys, pushToTalkKey: s.pushToTalkKey, pttStatus, hotkeyFailures, platform: process.platform,
    safeMode: health.safeMode, recoveredFromCrash: health.previousCrashed,
  };
}

const RENDERER_SETTABLE = new Set([
  "speakReplies", "followCursor", "alwaysListening", "pauseOnSystemAudio", "contextInMessages", "autoAttach", "suggestions",
]);

function wait(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

// ---------------------------------------------------------------- window

function initialAvatar() {
  let a = settings.get("avatar");
  const primary = screen.getPrimaryDisplay();
  if (!a || ![a.x, a.y, a.w, a.h].every(Number.isFinite)) a = layout.defaultAvatar(primary.workArea);
  const display = screen.getDisplayMatching({ x: a.x, y: a.y, width: a.w, height: a.h });
  return layout.clampAvatar(a, display.workArea);
}

function sendLayout() {
  send("layout", { side, w: av.w, h: av.h, bubbleW: layout.BUBBLE_W, gap: layout.GAP });
}

// Clamp on screen, pick the bubble side, resize/move the window, save.
function applyLayout(next, { save = true } = {}) {
  const display = screen.getDisplayMatching({ x: next.x, y: next.y, width: next.w, height: next.h });
  av = layout.clampAvatar(next, display.workArea);
  side = layout.chooseSide(av, display.workArea);
  if (win && !win.isDestroyed()) win.setBounds(layout.windowBounds(av, side));
  sendLayout();
  if (save) {
    settings.set("avatar", av);
    settings.set("bubbleSide", side);
  }
}

function createWindow() {
  av = initialAvatar();
  side = settings.get("bubbleSide") === "right" ? "right" : "left";
  const display = screen.getDisplayMatching({ x: av.x, y: av.y, width: av.w, height: av.h });
  side = layout.chooseSide(av, display.workArea);

  win = new BrowserWindow(Object.assign(layout.windowBounds(av, side), {
    frame: false,
    transparent: true,
    backgroundColor: "#00000000",
    resizable: false,
    maximizable: false,
    minimizable: false,
    fullscreenable: false,
    alwaysOnTop: true,
    skipTaskbar: true,
    hasShadow: false,
    show: false,
    title: "Neural",
    icon: iconPath(),
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      backgroundThrottling: false,
      spellcheck: false,
    },
  }));
  win.setAlwaysOnTop(true, "floating");
  win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: false });
  // Click-through everywhere until the page says the cursor is over the
  // avatar / text box / bubble (see renderer: updateHitTest).
  win.setIgnoreMouseEvents(true, { forward: true });

  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//i.test(url)) shell.openExternal(url);
    return { action: "deny" };
  });
  win.webContents.on("will-navigate", (e, url) => {
    if (!url.startsWith("file://")) {
      e.preventDefault();
      if (/^https?:\/\//i.test(url)) shell.openExternal(url);
    }
  });

  win.loadFile(path.join(__dirname, "renderer", "index.html"));
  win.once("ready-to-show", () => {
    win.showInactive(); // never steal focus at login
    sendLayout();
  });
  watchRenderer(win);
  win.webContents.on("did-finish-load", () => {
    sendLayout();
    send("settings", publicSettings());
    if (tracker && tracker.last) send("context", contextPayload(tracker.last));
  });
  win.on("show", () => tracker && tracker.start());
  win.on("hide", () => tracker && tracker.pause());
  win.on("close", (e) => {
    if (!quitting) {
      e.preventDefault();
      win.hide();
    }
  });
}

// ---------------------------------------------------------------- self-healing

const rendererCrashes = [];
let unresponsiveTimer = null;

function relaunchSafe(reason) {
  log("relaunching in safe mode:", reason);
  health.markCrashExit(reason);
  quitting = true;
  app.relaunch({ args: process.argv.slice(1).filter((a) => a !== "--safe-mode").concat(["--safe-mode"]) });
  app.exit(0);
}

function watchRenderer(w) {
  const wc = w.webContents;
  wc.on("render-process-gone", (e, details) => {
    log("renderer gone:", details.reason, "exit code", details.exitCode);
    if (details.reason === "clean-exit" || quitting) return;
    const now = Date.now();
    while (rendererCrashes.length && now - rendererCrashes[0] > 120000) rendererCrashes.shift();
    rendererCrashes.push(now);
    if (rendererCrashes.length >= 3 && !health.safeMode) return relaunchSafe(`renderer ${details.reason} x${rendererCrashes.length}`);
    // Bring her back instead of leaving an invisible dead window on screen.
    setTimeout(() => {
      if (!w.isDestroyed()) wc.reload();
    }, 800);
  });
  wc.on("unresponsive", () => {
    log("renderer unresponsive");
    clearTimeout(unresponsiveTimer);
    unresponsiveTimer = setTimeout(() => {
      log("renderer still unresponsive after 10s -- restarting it");
      try { wc.forcefullyCrashRenderer(); } catch (err) {}
      setTimeout(() => !w.isDestroyed() && wc.reload(), 500);
    }, 10000);
  });
  wc.on("responsive", () => {
    clearTimeout(unresponsiveTimer);
    log("renderer responsive again");
  });
  wc.on("console-message", (e, level, message, line, sourceId) => {
    // Electron 35+ puts these on the event object (level as a string).
    const lvl = e && e.level !== undefined ? e.level : level;
    const msg = e && e.message !== undefined ? e.message : message;
    const src = e && e.sourceId !== undefined ? e.sourceId : sourceId;
    const ln = e && e.lineNumber !== undefined ? e.lineNumber : line;
    if (lvl === "error" || lvl === "warning" || lvl >= 2) log(`renderer ${lvl}:`, msg, `(${path.basename(String(src || ""))}:${ln})`);
  });
  wc.on("preload-error", (e, preloadPath, error) => log("preload error:", error));
  wc.on("did-fail-load", (e, code, desc, url) => log("page failed to load:", code, desc, url));
}

app.on("child-process-gone", (e, d) => {
  if (d.reason === "clean-exit" || quitting) return;
  log("child process gone:", d.type, d.reason, "exit code", d.exitCode, d.name || "");
  if (d.type === "GPU" && !health.safeMode) {
    gpuCrashes++;
    if (gpuCrashes >= 2) relaunchSafe(`GPU process ${d.reason}`);
  }
});
let gpuCrashes = 0;

function showWindow(focus) {
  if (!win) return;
  if (!win.isVisible()) win.showInactive();
  win.moveTop();
  if (focus) {
    win.setIgnoreMouseEvents(false);
    win.focus();
  }
}

function toggleWindow() {
  if (!win) return;
  if (win.isVisible()) win.hide();
  else showWindow(false);
}

function iconPath() {
  const own = path.join(__dirname, "assets", "icon.png");
  if (fs.existsSync(own)) return own;
  return path.join(neuralRoot(), "frontend", "icon-192.png");
}

// ---------------------------------------------------------------- drag / resize

function stopDrag() {
  clearInterval(timers.drag);
  clearTimeout(timers.dragSafety);
  timers.drag = null;
  if (av) applyLayout(av);
}

ipcMain.on("drag:start", () => {
  if (!win || timers.drag) return;
  const start = screen.getCursorScreenPoint();
  const off = { x: start.x - av.x, y: start.y - av.y };
  timers.drag = setInterval(() => {
    const p = screen.getCursorScreenPoint();
    const x = p.x - off.x;
    const y = p.y - off.y;
    if (x === av.x && y === av.y) return;
    av = Object.assign({}, av, { x, y });
    win.setBounds(layout.windowBounds(av, side)); // side re-evaluated on drop
  }, 16);
  timers.dragSafety = setTimeout(stopDrag, 60000);
});
ipcMain.on("drag:end", stopDrag);

// Resize from the outer bottom corner, keeping the avatar's 2:3 shape.
ipcMain.on("resize:start", () => {
  if (!win || timers.resize) return;
  const start = screen.getCursorScreenPoint();
  const from = Object.assign({}, av);
  const outerIsRight = side === "left"; // bubble on the left -> avatar's outer edge is its right edge
  timers.resize = setInterval(() => {
    const p = screen.getCursorScreenPoint();
    const dx = (p.x - start.x) * (outerIsRight ? 1 : -1);
    const dy = p.y - start.y;
    const grow = Math.abs(dx) * 1.5 > Math.abs(dy) ? dx * 1.5 : dy; // follow the larger movement, in height units
    const size = layout.clampSize((from.h + grow) / 1.5, from.h + grow);
    size.h = Math.round(size.w * 1.5);
    const x = outerIsRight ? from.x : from.x + from.w - size.w;
    av = { x, y: from.y, w: size.w, h: size.h };
    win.setBounds(layout.windowBounds(av, side));
    sendLayout();
  }, 16);
  setTimeout(() => stopResize(), 60000);
});

function stopResize() {
  if (!timers.resize) return;
  clearInterval(timers.resize);
  timers.resize = null;
  applyLayout(av);
}
ipcMain.on("resize:end", stopResize);

ipcMain.on("mouse:ignore", (e, ignore) => {
  if (!win) return;
  if (ignore && (timers.drag || timers.resize)) return;
  win.setIgnoreMouseEvents(!!ignore, { forward: true });
});

ipcMain.on("win:hide", () => win && win.hide());
ipcMain.on("app:quit", () => quit());
ipcMain.on("win:focus", () => showWindow(true));

// ---------------------------------------------------------------- cursor feed (her eyes follow you)

function startCursorFeed() {
  let last = { x: -1, y: -1 };
  timers.cursor = setInterval(() => {
    if (!win || !win.isVisible() || !settings.get("followCursor")) return;
    const p = screen.getCursorScreenPoint();
    if (p.x === last.x && p.y === last.y) return;
    last = p;
    const b = win.getBounds();
    send("cursor", { x: p.x - b.x, y: p.y - b.y });
  }, 50);
}

// ---------------------------------------------------------------- context

function contextPayload(w) {
  if (!w) return null;
  return {
    title: w.title || "",
    app: friendlyApp(w),
    exe: w.exe || "",
    description: ContextTracker.describe(w),
    suggestions: settings.get("suggestions") ? suggest.forWindow(w) : [],
  };
}

ipcMain.handle("context:get", async () => {
  if (tracker && (!tracker.last || Date.now() - (tracker.last.seenAt || 0) > 3000)) {
    try { await tracker.poll(); } catch (e) {}
  }
  return contextPayload(tracker && tracker.last);
});

// ---------------------------------------------------------------- screenshots / OCR / clipboard

function looksBlank(img) {
  try {
    const small = img.resize({ width: 24 });
    const bmp = small.toBitmap();
    let max = 0;
    for (let i = 0; i < bmp.length; i += 4) max = Math.max(max, bmp[i], bmp[i + 1], bmp[i + 2]);
    return max < 10;
  } catch (e) {
    return false;
  }
}

function encodeImage(img, maxEdge = 1568) {
  let { width, height } = img.getSize();
  if (Math.max(width, height) > maxEdge) {
    const k = maxEdge / Math.max(width, height);
    img = img.resize({ width: Math.round(width * k), height: Math.round(height * k), quality: "good" });
  }
  const jpeg = img.toJPEG(82);
  const preview = img.resize({ width: Math.min(260, img.getSize().width) }).toDataURL();
  return { mime: "image/jpeg", data: jpeg.toString("base64"), preview };
}

async function captureDisplay(w) {
  let display = screen.getDisplayNearestPoint(screen.getCursorScreenPoint());
  let dip = null;
  if (w && w.width > 0 && w.height > 0 && process.platform === "win32") {
    try {
      dip = screen.screenToDipRect(null, { x: w.x, y: w.y, width: w.width, height: w.height });
      display = screen.getDisplayMatching(dip);
    } catch (e) {
      dip = null;
    }
  }
  const sf = display.scaleFactor || 1;
  const size = { width: Math.round(display.bounds.width * sf), height: Math.round(display.bounds.height * sf) };
  // Our own window would show up in a full-screen grab -- hide it for a moment.
  const wasVisible = win && win.isVisible();
  if (wasVisible) win.setOpacity(0);
  await wait(70);
  let sources;
  try {
    sources = await desktopCapturer.getSources({ types: ["screen"], thumbnailSize: size });
  } finally {
    if (wasVisible) win.setOpacity(1);
  }
  const src = sources.find((s) => String(s.display_id) === String(display.id)) || sources[0];
  if (!src) throw new Error("no screen to capture");
  let img = src.thumbnail;
  if (dip) {
    const k = img.getSize().width / display.bounds.width;
    const x = Math.max(0, Math.round((dip.x - display.bounds.x) * k));
    const y = Math.max(0, Math.round((dip.y - display.bounds.y) * k));
    const width = Math.min(img.getSize().width - x, Math.round(dip.width * k));
    const height = Math.min(img.getSize().height - y, Math.round(dip.height * k));
    if (width > 40 && height > 40) img = img.crop({ x, y, width, height });
  }
  return img;
}

async function captureActiveWindowImage() {
  const w = tracker && tracker.last;
  if (w && w.hwnd && !w.minimized) {
    try {
      const sources = await desktopCapturer.getSources({
        types: ["window"], thumbnailSize: { width: 2400, height: 2400 }, fetchWindowIcons: false,
      });
      const src = sources.find((s) => s.id.split(":")[1] === String(w.hwnd)) || sources.find((s) => s.name === w.title);
      if (src && !src.thumbnail.isEmpty() && !looksBlank(src.thumbnail)) return { img: src.thumbnail, window: w };
    } catch (e) {
      // fall through to a cropped screen grab
    }
  }
  return { img: await captureDisplay(w), window: w };
}

ipcMain.handle("capture:window", async () => {
  try {
    const { img, window: w } = await captureActiveWindowImage();
    return Object.assign({ ok: true, title: (w && w.title) || "", app: friendlyApp(w) || "your screen" }, encodeImage(img));
  } catch (e) {
    return { ok: false, error: e.message };
  }
});

ipcMain.handle("capture:screen", async () => {
  try {
    const img = await captureDisplay(null);
    return Object.assign({ ok: true, title: "", app: "your screen" }, encodeImage(img));
  } catch (e) {
    return { ok: false, error: e.message };
  }
});

ipcMain.handle("ocr:window", async () => {
  if (!tracker || !tracker.helper) return { ok: false, error: "Copying text from a window uses Windows' built-in OCR, so it only works on Windows." };
  const file = path.join(os.tmpdir(), `neural-ocr-${Date.now()}.png`);
  try {
    const { img, window: w } = await captureActiveWindowImage();
    fs.writeFileSync(file, img.toPNG());
    const text = (await ocrImage(tracker.helper, file)).trim();
    if (text) clipboard.writeText(text);
    return { ok: true, text, app: friendlyApp(w) };
  } catch (e) {
    return { ok: false, error: e.message };
  } finally {
    fs.unlink(file, () => {});
  }
});

ipcMain.handle("clipboard:read", () => {
  const text = clipboard.readText();
  if (text && text.trim()) {
    const max = 6000;
    return { kind: "text", text: text.length > max ? text.slice(0, max) : text, truncated: text.length > max };
  }
  const img = clipboard.readImage();
  if (img && !img.isEmpty()) return Object.assign({ kind: "image" }, encodeImage(img));
  return { kind: "empty" };
});

ipcMain.handle("clipboard:write", (e, text) => {
  clipboard.writeText(String(text || ""));
  return true;
});

// ---------------------------------------------------------------- apps, files, commands

function refreshAppIndex() {
  try {
    appIndex = launcher.buildIndex();
  } catch (e) {
    console.error("[launcher] index failed:", e.message);
  }
}

const SAFE_SCHEMES = /^(https?:|ms-[a-z-]+:|mailto:|spotify:|steam:|discord:|zoommtg:|msteams:)/i;

function launchEntry(entry) {
  const target = launcher.expandTarget(entry.target, { backendUrl: baseUrl() });
  if (entry.kind === "url" || entry.kind === "uri") {
    if (!SAFE_SCHEMES.test(target)) return Promise.resolve("that link type isn't allowed");
    return shell.openExternal(target).then(() => "", (e) => e.message);
  }
  if (entry.kind === "run") {
    try {
      const child = spawn(target, [], { detached: true, stdio: "ignore" });
      child.on("error", (e) => console.error("[launch]", target, e.message));
      child.unref();
      return Promise.resolve("");
    } catch (e) {
      return Promise.resolve(e.message);
    }
  }
  return shell.openPath(target); // "" on success, an error message otherwise
}

function runCommand(c) {
  if (!c) return { ok: false };
  const type = (c.type || "open").toLowerCase();
  if (type === "prompt") {
    send("menu:action", { action: "prompt", text: c.target });
    return { ok: true, prompt: c.target };
  }
  if (type === "run") {
    const cmd = launcher.expandTarget(c.target, { backendUrl: baseUrl() });
    spawn(cmd, { shell: true, detached: true, stdio: "ignore", windowsHide: true }).unref();
    return { ok: true };
  }
  launchEntry({ target: c.target, kind: type === "url" ? "url" : "open" });
  return { ok: true };
}

async function searchFiles(query) {
  let results = [];
  if (tracker && tracker.helper) {
    try {
      results = await searchIndex(tracker.helper, query, 25);
    } catch (e) {
      console.error("[files] index search failed, walking folders instead:", e.message);
    }
  }
  if (!results.length) results = fileSearch.walkSearch(query);
  return fileSearch.decorate(results);
}

ipcMain.handle("files:search", async (e, query) => searchFiles(String(query || "")));
ipcMain.handle("files:open", (e, p) => shell.openPath(String(p)));
ipcMain.handle("files:reveal", (e, p) => {
  shell.showItemInFolder(String(p));
  return true;
});
ipcMain.handle("open:external", (e, url) => {
  if (/^https?:\/\//i.test(String(url))) shell.openExternal(String(url));
  return true;
});
ipcMain.handle("open:web", () => shell.openExternal(baseUrl()));

// "what time is it", "open spotify", "remind me in 10 minutes to ..." --
// answered right here, no model turn. null/handled:false -> send to Neural.
ipcMain.handle("intent:handle", async (e, text) => {
  const it = intents.parseIntent(String(text || ""));
  if (!it) return { handled: false };
  const now = new Date();
  switch (it.type) {
    case "stop":
      return { handled: true, type: "stop" };
    case "time":
      return { handled: true, reply: `It's ${now.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}.`, mood: "happy" };
    case "date":
      return { handled: true, reply: `It's ${now.toLocaleDateString([], { weekday: "long", month: "long", day: "numeric", year: "numeric" })}.`, mood: "happy" };
    case "reminder": {
      const r = addReminder(it.text, it.at, it.timer);
      const when = intents.formatWhen(r.at);
      return {
        handled: true, mood: "happy",
        reply: it.timer ? `Timer set -- I'll tell you ${when}.` : `Okay, I'll remind you to ${r.text} ${when}.`,
      };
    }
    case "files": {
      const files = await searchFiles(it.query);
      if (!files.length) return { handled: false }; // let Neural dig deeper
      return {
        handled: true, files, mood: "proud",
        reply: `I found ${files.length === 25 ? "25+" : files.length} file${files.length === 1 ? "" : "s"} matching "${it.query}".`,
      };
    }
    case "launch": {
      const entry = launcher.findApp(it.name, appIndex, settings.get("commands"));
      if (!entry) return { handled: false };
      if (entry.command) {
        // A prompt-type command is sent by the page itself (as your message).
        if ((entry.command.type || "").toLowerCase() === "prompt") return { handled: true, prompt: entry.command.target };
        runCommand(entry.command);
        return { handled: true, reply: `Running ${entry.name}.`, mood: "happy" };
      }
      const err = await launchEntry(entry);
      if (err) return { handled: true, reply: `I couldn't open ${entry.name}: ${err}`, mood: "worried" };
      return { handled: true, reply: `Opening ${entry.name}.`, mood: "happy" };
    }
    default:
      return { handled: false };
  }
});

// ---------------------------------------------------------------- reminders

function addReminder(text, at, timer) {
  const r = { id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6), text, at, timer: !!timer };
  const list = settings.get("reminders") || [];
  list.push(r);
  list.sort((a, b) => a.at - b.at);
  settings.set("reminders", list);
  return r;
}

function checkReminders(startup = false) {
  const list = settings.get("reminders") || [];
  const now = Date.now();
  const due = list.filter((r) => r.at <= now);
  if (!due.length) return;
  settings.set("reminders", list.filter((r) => r.at > now));
  for (const r of due) {
    const missed = startup && now - r.at > 120000;
    const payload = Object.assign({}, r, { missed });
    try {
      if (Notification.isSupported()) {
        new Notification({ title: missed ? "Missed reminder" : "Neural", body: r.text, icon: iconPath(), silent: false }).show();
      }
    } catch (e) {}
    showWindow(false);
    send("reminder", payload);
  }
}

ipcMain.handle("reminders:list", () => settings.get("reminders") || []);
ipcMain.handle("reminders:cancel", (e, id) => {
  settings.set("reminders", (settings.get("reminders") || []).filter((r) => r.id !== id));
  return true;
});

// ---------------------------------------------------------------- backend (HTTP lives here: no CORS, no mixed origins)

async function backendFetch(p, opts = {}, timeoutMs = 15000) {
  return fetch(baseUrl() + p, Object.assign({ signal: AbortSignal.timeout(timeoutMs) }, opts));
}

ipcMain.handle("backend:info", () => ({ url: baseUrl(), wsUrl: baseUrl().replace(/^http/i, "ws") + "/ws" }));

ipcMain.handle("backend:config", async () => {
  try {
    const res = await backendFetch("/api/config", {}, 4000);
    return res.ok ? await res.json() : {};
  } catch (e) {
    return {};
  }
});

ipcMain.handle("backend:transcribe", async (e, { audio, mime, language }) => {
  try {
    const q = language != null ? `?language=${encodeURIComponent(language)}` : "";
    const res = await backendFetch("/api/transcribe" + q, {
      method: "POST", headers: { "content-type": mime || "audio/wav" }, body: Buffer.from(audio),
    }, 90000);
    const body = await res.json().catch(() => ({}));
    if (!res.ok) return { ok: false, status: res.status, error: body.detail || `HTTP ${res.status}` };
    return Object.assign({ ok: true }, body);
  } catch (err) {
    return { ok: false, status: 0, error: err.name === "TimeoutError" ? "transcription timed out" : "Neural isn't reachable" };
  }
});

ipcMain.handle("backend:speak", async (e, text) => {
  try {
    const res = await backendFetch("/api/speak", {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ text: String(text || "") }),
    }, 30000);
    if (!res.ok) return { ok: false };
    const buf = Buffer.from(await res.arrayBuffer());
    return { ok: true, mime: res.headers.get("content-type") || "audio/mpeg", data: buf.toString("base64") };
  } catch (err) {
    return { ok: false };
  }
});

ipcMain.handle("backend:warm", async () => {
  try {
    const res = await backendFetch("/api/transcribe/warm", { method: "POST" }, 180000);
    return res.ok ? await res.json() : { ready: false, available: false, status: res.status };
  } catch (e) {
    return { ready: false, available: false };
  }
});

ipcMain.on("backend:status", (e, status) => {
  backendStatus = String(status);
  updateTray();
});

async function probeBackend() {
  try {
    const res = await backendFetch("/api/config", {}, 2500);
    return res.ok;
  } catch (e) {
    return false;
  }
}

// Starts Neural with its own start_neural.ps1 if nothing answers -- only
// for a local backend, only on Windows, at most once every 2 minutes.
async function ensureBackend(force = false) {
  if (await probeBackend()) return "running";
  if ((!force && !settings.get("autoStartBackend")) || process.platform !== "win32" || !isLocalBackend()) return "offline";
  const script = path.join(neuralRoot(), "start_neural.ps1");
  if (!fs.existsSync(script)) return "missing";
  if (!force && Date.now() - lastBackendStart < 120000) return "starting";
  lastBackendStart = Date.now();
  try {
    spawn("powershell.exe", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", script], {
      cwd: neuralRoot(), detached: true, stdio: "ignore", windowsHide: true,
    }).unref();
    send("backend-status", { starting: true });
    return "starting";
  } catch (e) {
    return "offline";
  }
}

// ---------------------------------------------------------------- hotkeys + hold-to-talk

function registerHotkeys() {
  globalShortcut.unregisterAll();
  const hk = settings.get("hotkeys") || {};
  const failed = [];
  const reg = (acc, fn) => {
    if (!acc) return;
    try {
      if (!globalShortcut.register(acc, fn)) failed.push(acc);
    } catch (e) {
      failed.push(acc);
    }
  };
  reg(hk.voice, () => {
    showWindow(false);
    send("hotkey", "voice");
  });
  reg(hk.type, () => {
    showWindow(true);
    send("hotkey", "type");
  });
  reg(hk.askScreen, () => {
    showWindow(false);
    send("hotkey", "ask-window");
  });
  hotkeyFailures = failed;
  if (failed.length) console.error("[hotkeys] already taken by another app:", failed.join(", "));
}

let uio = null;
function stopPtt() {
  if (uio) {
    try {
      uio.removeAllListeners("keydown");
      uio.removeAllListeners("keyup");
      uio.stop();
    } catch (e) {}
  }
}

function setupPtt() {
  stopPtt();
  if (health.safeMode) {
    pttStatus = "off in safe mode";
    return;
  }
  const keyName = settings.get("pushToTalkKey");
  if (!keyName) {
    pttStatus = "off";
    return;
  }
  let mod;
  try {
    mod = require("uiohook-napi");
  } catch (e) {
    pttStatus = "unavailable";
    return;
  }
  const { uIOhook, UiohookKey } = mod;
  const names = { RightControl: "CtrlRight", RightAlt: "AltRight", RightShift: "ShiftRight", RightWin: "MetaRight" };
  const code = UiohookKey[names[keyName] || keyName];
  if (code === undefined) {
    pttStatus = "unknown key";
    return;
  }
  let down = false;
  uIOhook.on("keydown", (e) => {
    if (e.keycode === code) {
      if (!down) {
        down = true;
        send("ptt", { down: true });
      }
    } else if (down) {
      // Right Ctrl + C etc. -- a shortcut, not talking.
      down = false;
      send("ptt", { down: false, cancel: true });
    }
  });
  uIOhook.on("keyup", (e) => {
    if (e.keycode === code && down) {
      down = false;
      send("ptt", { down: false });
    }
  });
  try {
    uIOhook.start();
    uio = uIOhook;
    pttStatus = "on";
    log("hold-to-talk key hook on:", keyName);
  } catch (e) {
    pttStatus = "unavailable";
    log("hold-to-talk key hook failed:", e);
  }
}

// ---------------------------------------------------------------- menus + tray

function settingsCheckbox(label, key, extra = {}) {
  return Object.assign({ label, type: "checkbox", checked: !!settings.get(key), click: (item) => settings.set(key, item.checked) }, extra);
}

function remindersMenu() {
  const list = settings.get("reminders") || [];
  if (!list.length) return [{ label: "No reminders", enabled: false }];
  return list.slice(0, 15).map((r) => ({
    label: `${r.text.slice(0, 50)} -- ${intents.formatWhen(r.at)}`,
    submenu: [{
      label: "Cancel this reminder",
      click: () => settings.set("reminders", (settings.get("reminders") || []).filter((x) => x.id !== r.id)),
    }],
  }));
}

function buildContextMenu() {
  const act = (action, extra) => () => send("menu:action", Object.assign({ action }, extra || {}));
  const w = tracker && tracker.last;
  const sugg = settings.get("suggestions") && w ? suggest.forWindow(w) : [];
  const commands = (settings.get("commands") || []).filter((c) => c && c.name);
  const hk = settings.get("hotkeys") || {};
  const pttKey = settings.get("pushToTalkKey");
  const template = [];
  if (sugg.length) {
    template.push({ label: `In ${friendlyApp(w)}`, enabled: false });
    for (const s of sugg) template.push({ label: s.label, click: act("suggestion", { item: s }) });
    template.push({ type: "separator" });
  }
  template.push(
    { label: "Ask about the active window", accelerator: hk.askScreen || undefined, click: act("ask-window") },
    { label: "Copy text from the active window", click: act("ocr-window"), enabled: process.platform === "win32" },
    { label: "Ask about my clipboard", click: act("ask-clipboard") },
    { type: "separator" },
    { label: "Find a file...", click: act("prefill", { text: "Find file called " }) },
    { label: "Search the web...", click: act("prefill", { text: "Search the web for " }) },
    { label: "Open an app...", click: act("prefill", { text: "Open " }) },
    { label: "Weather", click: act("prompt", { text: "What's the weather like right now and for the rest of today where I am? Keep it short." }) },
    { label: "What time is it?", click: act("local", { text: "what time is it" }) },
    { label: "Set a reminder...", click: act("prefill", { text: "Remind me to " }) },
    { label: "Reminders", submenu: remindersMenu() },
    {
      label: "My commands",
      submenu: commands.map((c) => ({ label: c.name, click: () => runCommand(c) })).concat(
        commands.length ? [{ type: "separator" }] : [],
        [{ label: "Edit commands (settings file)...", click: openSettingsFile }]
      ),
    },
    { type: "separator" },
    {
      label: "Voice",
      submenu: [
        settingsCheckbox("Always listening (say \"Neural ...\")", "alwaysListening"),
        settingsCheckbox("Pause listening while my PC plays audio", "pauseOnSystemAudio", { enabled: process.platform === "win32" }),
        settingsCheckbox("Speak replies", "speakReplies"),
        { type: "separator" },
        { label: `Talk: hold Space here, or press ${hk.voice || "(no hotkey)"}`, enabled: false },
        {
          label: pttStatus === "on" ? `Hold ${pttKey} anywhere to talk` : pttStatus === "unavailable"
            ? "Hold-to-talk anywhere: run npm install (uiohook-napi)" : "Hold-to-talk anywhere: off",
          enabled: false,
        },
      ],
    },
    {
      label: "Behaviour",
      submenu: [
        settingsCheckbox("Eyes follow my cursor", "followCursor"),
        settingsCheckbox("Tell Neural which window I'm in", "contextInMessages"),
        settingsCheckbox("Attach my screen / clipboard when I mention them", "autoAttach"),
        settingsCheckbox("Show suggestions for the app I'm in", "suggestions"),
        settingsCheckbox("Start Neural backend if it isn't running", "autoStartBackend"),
        settingsCheckbox(process.platform === "darwin" ? "Open at login" : "Start with Windows", "launchAtLogin"),
      ],
    },
    { label: "History", click: act("history") },
    { label: "Open full Neural", click: () => shell.openExternal(baseUrl()) },
    { label: "Settings file...", click: openSettingsFile },
    { label: health.safeMode ? "Restart normally (leave safe mode)" : "Restart Neural Desktop", click: restartApp },
    { type: "separator" },
    { label: "Hide (tray icon brings her back)", click: () => win && win.hide() },
    { label: "Quit Neural Desktop", click: quit }
  );
  return Menu.buildFromTemplate(template);
}

ipcMain.on("menu:popup", () => {
  if (!win) return;
  buildContextMenu().popup({ window: win });
});

function openSettingsFile() {
  settings.saveNow();
  shell.openPath(settings.file);
}

function trayImage() {
  let img = nativeImage.createFromPath(iconPath());
  if (img.isEmpty()) return nativeImage.createEmpty();
  return img.resize({ width: 32, height: 32, quality: "best" });
}

function updateTray() {
  if (!tray) return;
  const offline = backendStatus === "offline";
  tray.setToolTip(offline ? "Neural (backend offline)" : "Neural");
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: win && win.isVisible() ? "Hide Neural" : "Show Neural", click: toggleWindow },
    settingsCheckbox("Always listening", "alwaysListening"),
    settingsCheckbox("Speak replies", "speakReplies"),
    { type: "separator" },
    { label: "Open full Neural", click: () => shell.openExternal(baseUrl()) },
    {
      label: offline ? "Start Neural backend" : "Neural backend is running",
      enabled: offline && process.platform === "win32",
      click: () => ensureBackend(true),
    },
    { label: "Settings file...", click: openSettingsFile },
    { label: "Open log file...", click: () => shell.openPath(health.logFile) },
    { label: health.safeMode ? "Restart normally (leave safe mode)" : "Restart Neural Desktop", click: restartApp },
    { type: "separator" },
    { label: "Quit Neural Desktop", click: quit },
  ]));
}

function createTray() {
  try {
    tray = new Tray(trayImage());
  } catch (e) {
    console.error("[tray]", e.message);
    return;
  }
  tray.on("click", toggleWindow);
  updateTray();
}

// ---------------------------------------------------------------- settings changes

function onSettingChanged(key, value) {
  if (key === "avatar" || key === "bubbleSide" || key === "reminders") return;
  if (key === "hotkeys") registerHotkeys();
  if (key === "pushToTalkKey") setupPtt();
  if (key === "launchAtLogin") setLoginItem(value);
  if (key === "suggestions" && tracker && tracker.last) send("context", contextPayload(tracker.last));
  send("settings", publicSettings());
  updateTray();
}

function setLoginItem(on) {
  try {
    if (app.isPackaged) app.setLoginItemSettings({ openAtLogin: !!on });
    else app.setLoginItemSettings({ openAtLogin: !!on, path: process.execPath, args: [path.resolve(__dirname)] });
  } catch (e) {
    console.error("[login item]", e.message);
  }
}

ipcMain.handle("settings:get", () => publicSettings());
ipcMain.handle("settings:set", (e, key, value) => {
  if (!RENDERER_SETTABLE.has(key)) return false;
  settings.set(key, value);
  return true;
});

// ---------------------------------------------------------------- lifecycle

function quit() {
  quitting = true;
  app.quit();
}

function restartApp() {
  log("restart requested");
  quitting = true;
  health.markCleanExit();
  app.relaunch({ args: process.argv.slice(1).filter((a) => a !== "--safe-mode") });
  app.exit(0);
}

app.on("second-instance", () => {
  log("second launch -- showing the existing window");
  if (win && !win.isDestroyed() && win.webContents.isCrashed()) win.webContents.reload();
  showWindow(true);
});

app.whenReady().then(async () => {
  settings = new Settings(app.getPath("userData"));
  settings.onChange(onSettingChanged);

  // Microphone (voice input), notifications, clipboard -- our own page only.
  const allowed = new Set(["media", "notifications", "clipboard-read", "clipboard-sanitized-write", "display-capture"]);
  session.defaultSession.setPermissionRequestHandler((wc, permission, cb) => cb(allowed.has(permission)));
  session.defaultSession.setPermissionCheckHandler((wc, permission) => allowed.has(permission));
  // System-audio loopback, used only to pause always-listening while your
  // PC is playing sound (Windows). The page requests a display stream,
  // keeps only its audio track, and never records it.
  try {
    session.defaultSession.setDisplayMediaRequestHandler((request, callback) => {
      desktopCapturer.getSources({ types: ["screen"] }).then(
        (sources) => callback(sources[0] ? { video: sources[0], audio: process.platform === "win32" ? "loopback" : undefined } : {}),
        () => callback({})
      );
    });
  } catch (e) {
    console.error("[loopback]", e.message);
  }

  tracker = new ContextTracker({ ownPids: [process.pid] });
  tracker.onChange((w) => send("context", contextPayload(w)));

  createWindow();
  createTray();
  registerHotkeys();
  setupPtt();
  startCursorFeed();
  tracker.start();

  setTimeout(refreshAppIndex, 1500);
  timers.index = setInterval(refreshAppIndex, 30 * 60000);
  checkReminders(true);
  timers.reminders = setInterval(() => checkReminders(false), 5000);

  screen.on("display-removed", () => av && applyLayout(av));
  screen.on("display-metrics-changed", () => av && applyLayout(av));

  ensureBackend(false).then((status) => {
    log("backend:", status, "at", baseUrl());
    send("backend-status", { status });
  });
  // Three quiet minutes = this run is healthy; forget earlier crashes.
  setTimeout(() => health.markStable(), 3 * 60000);
});

app.on("before-quit", () => {
  quitting = true;
  log("quit");
  health.markCleanExit();
  try { settings && settings.saveNow(); } catch (e) {}
  try { globalShortcut.unregisterAll(); } catch (e) {}
  stopPtt();
  if (tracker) tracker.stop();
  for (const t of Object.values(timers)) clearInterval(t);
});

app.on("window-all-closed", () => {
  if (quitting) app.quit();
});
