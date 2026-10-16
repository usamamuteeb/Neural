"use strict";
/*
 * Instant app launching: "open spotify" starts it locally in ~100 ms with
 * no model turn and no tokens. Indexes Start Menu shortcuts (Windows),
 * .desktop files (Linux) or /Applications (macOS), plus a few built-ins
 * and well-known websites, plus your own custom commands (settings.json).
 * Anything that doesn't match confidently goes to Neural as a normal
 * message instead -- so "open the report I wrote yesterday" still works.
 */
const fs = require("fs");
const path = require("path");
const os = require("os");

const BUILTINS = {
  win32: [
    { name: "Calculator", target: "calc.exe", kind: "run" },
    { name: "Notepad", target: "notepad.exe", kind: "run" },
    { name: "Paint", target: "mspaint.exe", kind: "run" },
    { name: "Task Manager", target: "taskmgr.exe", kind: "run" },
    { name: "File Explorer", target: "explorer.exe", kind: "run", aliases: ["explorer", "files", "my files"] },
    { name: "Command Prompt", target: "cmd.exe", kind: "run", aliases: ["cmd"] },
    { name: "PowerShell", target: "powershell.exe", kind: "run" },
    { name: "Windows Terminal", target: "wt.exe", kind: "run", aliases: ["terminal"] },
    { name: "Settings", target: "ms-settings:", kind: "uri", aliases: ["windows settings"] },
    { name: "Snipping Tool", target: "ms-screenclip:", kind: "uri", aliases: ["snip", "screen snip"] },
    { name: "Control Panel", target: "control.exe", kind: "run" },
  ],
  darwin: [],
  linux: [],
};

const WEBSITES = {
  youtube: "https://www.youtube.com", gmail: "https://mail.google.com", google: "https://www.google.com",
  github: "https://github.com", linkedin: "https://www.linkedin.com", twitter: "https://x.com", x: "https://x.com",
  instagram: "https://www.instagram.com", facebook: "https://www.facebook.com", reddit: "https://www.reddit.com",
  "whatsapp web": "https://web.whatsapp.com", netflix: "https://www.netflix.com", "google drive": "https://drive.google.com",
  "google calendar": "https://calendar.google.com", "claude": "https://claude.ai", chatgpt: "https://chatgpt.com",
  vercel: "https://vercel.com/dashboard", tiktok: "https://www.tiktok.com",
};

function normalize(s) {
  return String(s || "")
    .toLowerCase()
    .replace(/\.(lnk|url|appref-ms|desktop|app|exe)$/i, "")
    .replace(/[^a-z0-9+#]+/g, " ")
    .trim();
}

function walk(dir, exts, out, depth = 0, limit = 4000, maxDepth = 4) {
  if (depth > maxDepth || out.length >= limit) return;
  let entries;
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch (e) {
    return;
  }
  for (const e of entries) {
    const full = path.join(dir, e.name);
    if (e.isDirectory()) {
      if (exts.includes(".app") && e.name.endsWith(".app")) out.push(full);
      else walk(full, exts, out, depth + 1, limit, maxDepth);
    } else if (exts.includes(path.extname(e.name).toLowerCase())) {
      out.push(full);
    }
  }
}

// Shortcuts that are noise when you say "open X".
const SKIP = /\b(uninstall|uninstaller|readme|help|documentation|license|release notes|website|support|manual|changelog)\b/i;

function buildIndex(platform = process.platform, env = process.env) {
  const files = [];
  if (platform === "win32") {
    const menus = [
      path.join(env.ProgramData || "C:\\ProgramData", "Microsoft", "Windows", "Start Menu", "Programs"),
      path.join(env.APPDATA || path.join(os.homedir(), "AppData", "Roaming"), "Microsoft", "Windows", "Start Menu", "Programs"),
    ];
    for (const r of menus) walk(r, [".lnk", ".url", ".appref-ms"], files);
    // Desktop shortcuts: top level only -- never crawl whatever folders live there.
    for (const r of [path.join(os.homedir(), "Desktop"), path.join(env.PUBLIC || "C:\\Users\\Public", "Desktop")]) {
      walk(r, [".lnk", ".url", ".appref-ms"], files, 0, 4000, 0);
    }
  } else if (platform === "darwin") {
    for (const r of ["/Applications", "/System/Applications", path.join(os.homedir(), "Applications")]) walk(r, [".app"], files, 3);
  } else {
    for (const r of ["/usr/share/applications", path.join(os.homedir(), ".local/share/applications")]) walk(r, [".desktop"], files);
  }
  const seen = new Set();
  const index = [];
  for (const f of files) {
    const name = path.basename(f).replace(/\.(lnk|url|appref-ms|desktop|app)$/i, "");
    const key = normalize(name);
    if (!key || seen.has(key) || SKIP.test(name)) continue;
    seen.add(key);
    index.push({ name, key, target: f, kind: "open" });
  }
  for (const b of BUILTINS[platform] || []) {
    const key = normalize(b.name);
    if (seen.has(key)) continue;
    seen.add(key);
    index.push(Object.assign({ key }, b));
  }
  return index;
}

// 1.0 exact, ~0.85-0.9 prefix, 0.8-0.86 every word present ("chrome" -> Google Chrome), else lower.
function score(query, entry) {
  const q = normalize(query);
  if (!q) return 0;
  const names = [entry.key || normalize(entry.name)].concat((entry.aliases || []).map(normalize));
  let best = 0;
  for (const n of names) {
    if (!n) continue;
    if (n === q) return 1;
    if (n.startsWith(q + " ") || n.startsWith(q)) best = Math.max(best, q.length >= 3 ? 0.9 - Math.min(0.15, (n.length - q.length) / 60) : 0.5);
    const qw = q.split(" ");
    const nw = n.split(" ");
    if (qw.every((w) => nw.includes(w))) best = Math.max(best, Math.max(0.8, 0.86 - (nw.length - qw.length) * 0.02));
    else if (qw.length > 1 && qw.every((w) => nw.some((x) => x.startsWith(w)))) best = Math.max(best, 0.7);
  }
  return best;
}

const ALIASES = {
  "vs code": "visual studio code", vscode: "visual studio code", "vs": "visual studio",
  edge: "microsoft edge", ppt: "powerpoint", "power point": "powerpoint", teams: "microsoft teams",
  "file manager": "file explorer", "the browser": "google chrome", browser: "google chrome",
};

function findApp(query, index, commands = []) {
  query = ALIASES[normalize(query)] || query;
  const candidates = [];
  for (const c of commands || []) {
    if (!c || !c.name) continue;
    candidates.push({ entry: { name: c.name, key: normalize(c.name), command: c }, s: score(query, { key: normalize(c.name) }) + 0.01 });
  }
  for (const e of index || []) candidates.push({ entry: e, s: score(query, e) });
  candidates.sort((a, b) => b.s - a.s || a.entry.name.length - b.entry.name.length);
  const top = candidates[0];
  if (top && top.s >= 0.8) return top.entry;
  const site = WEBSITES[normalize(query)];
  if (site) return { name: query.trim(), target: site, kind: "url" };
  return null;
}

function expandTarget(target, vars = {}) {
  let t = String(target || "");
  t = t.replace(/\{(\w+)\}/g, (m, k) => (vars[k] !== undefined ? vars[k] : m));
  if (t.startsWith("~")) t = path.join(os.homedir(), t.slice(1));
  return t;
}

module.exports = { buildIndex, findApp, score, normalize, expandTarget, WEBSITES };
