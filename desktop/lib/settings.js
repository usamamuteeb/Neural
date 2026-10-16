"use strict";
/*
 * Persistent settings for Neural Desktop: one JSON file in Electron's
 * userData folder (%APPDATA%\neural-desktop\settings.json on Windows).
 * Unknown keys are kept, missing keys fall back to DEFAULTS, and writes
 * are atomic (temp file + rename) and debounced, so dragging the window
 * around doesn't hammer the disk and a crash mid-write can't corrupt it.
 */
const fs = require("fs");
const path = require("path");

const DEFAULTS = {
  backendUrl: "http://127.0.0.1:8787",
  // Folder of the Neural project (start_neural.ps1 lives there). Empty =
  // the folder this desktop/ app sits in.
  neuralRoot: "",
  autoStartBackend: true,

  // Avatar rectangle on screen (DIP). null = bottom-right of the primary display.
  avatar: null,           // { x, y, w, h }
  bubbleSide: "left",     // which side of the avatar the reply bubble sits on

  speakReplies: true,
  followCursor: true,
  alwaysListening: false,
  pauseOnSystemAudio: true,
  wakeWords: ["neural", "hey neural", "okay neural"],
  followUpSeconds: 10,
  contextInMessages: true,  // tell Neural which window you're looking at
  autoAttach: true,         // "my screen"/"this window" -> screenshot, "clipboard" -> clipboard text
  suggestions: true,
  launchAtLogin: false,

  hotkeys: {
    voice: "Control+Alt+Space",     // press to talk (stops by itself when you pause)
    type: "Control+Alt+N",          // show the window and focus the text box
    askScreen: "Control+Alt+A",     // ask about the active window (screenshot)
  },
  // Hold-to-talk anywhere (needs the optional uiohook-napi module).
  // RightControl | RightAlt | RightShift | CapsLock | F8..F12 | Pause | "" (off)
  pushToTalkKey: "RightControl",

  commands: [
    { name: "Neural web app", type: "url", target: "{backendUrl}" },
    { name: "Daily briefing", type: "prompt", target: "Give me a quick briefing: today's calendar, unread important email, and anything I asked you to remind me about." },
    { name: "Downloads folder", type: "open", target: "~/Downloads" },
  ],
  reminders: [],          // [{ id, text, at }]
};

function clone(v) {
  return v === undefined ? undefined : JSON.parse(JSON.stringify(v));
}

class Settings {
  constructor(dir, { debounceMs = 400 } = {}) {
    this.file = path.join(dir, "settings.json");
    this.debounceMs = debounceMs;
    this.timer = null;
    this.listeners = [];
    let stored = {};
    try {
      stored = JSON.parse(fs.readFileSync(this.file, "utf8"));
      if (!stored || typeof stored !== "object" || Array.isArray(stored)) stored = {};
    } catch (e) {
      stored = {};
    }
    this.data = Object.assign(clone(DEFAULTS), stored);
    this.data.hotkeys = Object.assign(clone(DEFAULTS.hotkeys), stored.hotkeys || {});
    if (!Array.isArray(this.data.reminders)) this.data.reminders = [];
    if (!Array.isArray(this.data.commands)) this.data.commands = clone(DEFAULTS.commands);
    if (!Array.isArray(this.data.wakeWords)) this.data.wakeWords = clone(DEFAULTS.wakeWords);
  }

  get(key) {
    return key === undefined ? clone(this.data) : clone(this.data[key]);
  }

  set(key, value) {
    this.data[key] = clone(value);
    this._scheduleSave();
    for (const fn of this.listeners) {
      try { fn(key, clone(value)); } catch (e) { /* listener bugs must not break settings */ }
    }
  }

  onChange(fn) {
    this.listeners.push(fn);
  }

  _scheduleSave() {
    clearTimeout(this.timer);
    this.timer = setTimeout(() => this.saveNow(), this.debounceMs);
  }

  saveNow() {
    clearTimeout(this.timer);
    this.timer = null;
    try {
      fs.mkdirSync(path.dirname(this.file), { recursive: true });
      const tmp = this.file + ".tmp";
      fs.writeFileSync(tmp, JSON.stringify(this.data, null, 2), "utf8");
      fs.renameSync(tmp, this.file);
    } catch (e) {
      console.error("[settings] save failed:", e.message);
    }
  }
}

module.exports = { Settings, DEFAULTS };
