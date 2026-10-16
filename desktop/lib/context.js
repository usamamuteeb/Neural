"use strict";
/*
 * "What is the user looking at?" -- the active window (title, app, bounds),
 * plus Windows OCR and Windows Search through the same helper process.
 *
 * Windows: a persistent PowerShell helper (winhelper.ps1) -- no native Node
 * modules, nothing to compile. macOS: osascript. Linux: xdotool if present.
 * Everything here degrades to "unknown" rather than throwing: context is a
 * nice-to-have, never a reason for the app to break.
 */
const { spawn, execFile } = require("child_process");
const path = require("path");
const os = require("os");

// Windows shell surfaces that aren't "an app the user is working in".
const IGNORED_CLASSES = new Set([
  "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "NotifyIconOverflowWindow",
  "TopLevelWindowForOverflowXamlIsland", "Windows.UI.Core.CoreWindow",
  "XamlExplorerHostIslandWindow", "ForegroundStaging", "MultitaskingViewFrame",
  "TaskListThumbnailWnd", "#32768",
]);

class WinHelper {
  constructor({ script = path.join(__dirname, "winhelper.ps1"), log = console } = {}) {
    this.script = script;
    this.log = log;
    this.proc = null;
    this.nextId = 1;
    this.pending = new Map();
    this.buf = "";
    this.restarts = [];
    this.stopped = false;
    this.ready = false;
  }

  start() {
    if (this.proc || this.stopped) return;
    const now = Date.now();
    this.restarts = this.restarts.filter((t) => now - t < 60000);
    if (this.restarts.length >= 5) {
      this.log.error("[winhelper] crashed 5 times in a minute -- giving up until restart");
      this.stopped = true;
      return;
    }
    this.restarts.push(now);
    try {
      this.proc = spawn(
        "powershell.exe",
        ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", this.script],
        { windowsHide: true, stdio: ["pipe", "pipe", "pipe"] }
      );
    } catch (e) {
      this.log.error("[winhelper] spawn failed:", e.message);
      this.proc = null;
      return;
    }
    this.ready = false;
    this.proc.stdout.setEncoding("utf8");
    this.proc.stdout.on("data", (chunk) => this._onData(chunk));
    this.proc.stderr.on("data", (d) => this.log.error("[winhelper]", String(d).trim().slice(0, 500)));
    this.proc.on("error", (e) => this.log.error("[winhelper] error:", e.message));
    this.proc.on("exit", (code) => {
      this.proc = null;
      this.ready = false;
      for (const [, p] of this.pending) {
        clearTimeout(p.timer);
        p.reject(new Error("helper exited"));
      }
      this.pending.clear();
      if (!this.stopped) setTimeout(() => this.start(), 1500);
      if (code) this.log.error("[winhelper] exited with code", code);
    });
  }

  _onData(chunk) {
    this.buf += chunk;
    let nl;
    while ((nl = this.buf.indexOf("\n")) !== -1) {
      const line = this.buf.slice(0, nl).trim();
      this.buf = this.buf.slice(nl + 1);
      if (!line) continue;
      let msg;
      try {
        msg = JSON.parse(line.replace(/^\uFEFF/, ""));
      } catch (e) {
        continue; // stray non-JSON output (a warning) -- ignore
      }
      if (msg.ready) {
        this.ready = true;
        continue;
      }
      const p = this.pending.get(msg.id);
      if (!p) continue;
      this.pending.delete(msg.id);
      clearTimeout(p.timer);
      if (msg.ok) p.resolve(msg.data);
      else p.reject(new Error(msg.error || "helper error"));
    }
  }

  request(cmd, args = {}, timeoutMs = 4000) {
    if (!this.proc) this.start();
    if (!this.proc) return Promise.reject(new Error("helper unavailable"));
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`helper timed out on ${cmd}`));
      }, timeoutMs);
      this.pending.set(id, { resolve, reject, timer });
      try {
        this.proc.stdin.write(JSON.stringify(Object.assign({ id, cmd }, args)) + "\n");
      } catch (e) {
        clearTimeout(timer);
        this.pending.delete(id);
        reject(e);
      }
    });
  }

  stop() {
    this.stopped = true;
    if (this.proc) {
      try { this.proc.stdin.write('{"id":0,"cmd":"exit"}\n'); } catch (e) {}
      const p = this.proc;
      setTimeout(() => { try { p.kill(); } catch (e) {} }, 500);
    }
  }
}

function execText(cmd, args, timeout = 1500) {
  return new Promise((resolve) => {
    execFile(cmd, args, { timeout, windowsHide: true }, (err, stdout) => resolve(err ? "" : String(stdout).trim()));
  });
}

async function activeWindowMac() {
  const script =
    'tell application "System Events" to set p to first application process whose frontmost is true\n' +
    'set n to name of p\nset t to ""\ntry\nset t to name of front window of p\nend try\nreturn n & "\\n" & t & "\\n" & (unix id of p)';
  const out = await execText("osascript", ["-e", script]);
  if (!out) return null;
  const [app, title, pid] = out.split("\n");
  return { app, exe: app, title: title || "", pid: Number(pid) || 0 };
}

async function activeWindowLinux() {
  const id = await execText("xdotool", ["getactivewindow"]);
  if (!id) return null;
  const [title, pid] = await Promise.all([
    execText("xdotool", ["getwindowname", id]),
    execText("xdotool", ["getwindowpid", id]),
  ]);
  let exe = "";
  if (pid) exe = await execText("ps", ["-p", pid, "-o", "comm="]);
  return { title, pid: Number(pid) || 0, exe, app: exe, hwnd: 0 };
}

class ContextTracker {
  constructor({ ownPids = [process.pid], helper = null, platform = process.platform, intervalMs = 1500 } = {}) {
    this.ownPids = new Set(ownPids);
    this.platform = platform;
    this.helper = helper || (platform === "win32" ? new WinHelper() : null);
    this.intervalMs = intervalMs;
    this.current = null;   // whatever is in front right now (may be us)
    this.last = null;      // the last window that WASN'T us -- "the active window" for every feature
    this.timer = null;
    this.listeners = [];
  }

  start() {
    if (this.helper) this.helper.start();
    if (this.timer) return;
    this.timer = setInterval(() => this.poll().catch(() => {}), this.intervalMs);
    this.poll().catch(() => {});
  }

  pause() {
    clearInterval(this.timer);
    this.timer = null;
  }

  stop() {
    this.pause();
    if (this.helper) this.helper.stop();
  }

  onChange(fn) {
    this.listeners.push(fn);
  }

  async query() {
    if (this.platform === "win32") return this.helper ? this.helper.request("active", {}, 2500) : null;
    if (this.platform === "darwin") return activeWindowMac();
    return activeWindowLinux();
  }

  async poll() {
    let w;
    try {
      w = await this.query();
    } catch (e) {
      return this.last;
    }
    if (!w || !w.title && !w.exe) return this.last;
    this.current = w;
    if (this.ownPids.has(w.pid) || IGNORED_CLASSES.has(w.cls || "")) return this.last;
    const changed = !this.last || this.last.hwnd !== w.hwnd || this.last.title !== w.title;
    this.last = Object.assign({}, w, { seenAt: Date.now() });
    if (changed) {
      for (const fn of this.listeners) {
        try { fn(this.last); } catch (e) {}
      }
    }
    return this.last;
  }

  // Short human description for prompts and the UI.
  static describe(w) {
    if (!w) return "";
    const app = friendlyApp(w);
    const title = (w.title || "").trim();
    if (!title || title === app) return app;
    return `"${title.slice(0, 160)}" in ${app}`;
  }
}

function friendlyApp(w) {
  if (!w) return "";
  const exe = (w.exe || "").replace(/\.exe$/i, "");
  let name = (w.app || exe || "").trim();
  if (/^explorer$/i.test(exe)) name = "File Explorer";
  if (/^applicationframehost$/i.test(exe)) name = (w.title || "a Windows app").split(" - ").pop();
  return name || "an app";
}

async function ocrImage(helper, file) {
  if (!helper) throw new Error("OCR is only available on Windows");
  const data = await helper.request("ocr", { path: file }, 20000);
  return (data && data.text) || "";
}

async function searchIndex(helper, query, limit = 25) {
  if (!helper) throw new Error("Windows Search is only available on Windows");
  const data = await helper.request("search", { query, limit }, 8000);
  return (data && data.results) || [];
}

module.exports = { WinHelper, ContextTracker, friendlyApp, ocrImage, searchIndex, IGNORED_CLASSES, homedir: os.homedir };
