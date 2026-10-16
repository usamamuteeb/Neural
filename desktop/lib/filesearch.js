"use strict";
/*
 * Desktop file search. On Windows the Windows Search index answers in
 * milliseconds (through the helper process); when it's unavailable -- or on
 * macOS/Linux -- this walks your usual folders with a time budget instead.
 */
const fs = require("fs");
const path = require("path");
const os = require("os");

const SKIP_DIRS = new Set(["node_modules", ".git", "__pycache__", ".venv", "venv", "AppData", "$RECYCLE.BIN", ".cache", "Library"]);

function defaultRoots(home = os.homedir()) {
  return ["Desktop", "Documents", "Downloads", "Pictures", "Videos", "Music", "OneDrive"].map((d) => path.join(home, d));
}

function matcher(query) {
  const words = String(query || "").toLowerCase().split(/\s+/).filter(Boolean);
  return (name) => {
    const n = name.toLowerCase();
    return words.length > 0 && words.every((w) => n.includes(w));
  };
}

function walkSearch(query, { roots = defaultRoots(), limit = 25, budgetMs = 2500, maxDepth = 6 } = {}) {
  const matches = matcher(query);
  const deadline = Date.now() + budgetMs;
  const results = [];
  const queue = roots.map((r) => ({ dir: r, depth: 0 }));
  while (queue.length && results.length < limit * 4 && Date.now() < deadline) {
    const { dir, depth } = queue.shift();
    let entries;
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch (e) {
      continue;
    }
    for (const e of entries) {
      if (e.name.startsWith(".")) continue;
      const full = path.join(dir, e.name);
      if (e.isDirectory()) {
        if (!SKIP_DIRS.has(e.name) && depth < maxDepth) queue.push({ dir: full, depth: depth + 1 });
        if (matches(e.name)) results.push({ path: full, dir: true });
      } else if (matches(e.name)) {
        results.push({ path: full });
      }
    }
  }
  for (const r of results) {
    try {
      r.modified = fs.statSync(r.path).mtime.toISOString();
    } catch (e) {
      r.modified = "";
    }
  }
  results.sort((a, b) => (b.modified || "").localeCompare(a.modified || ""));
  return results.slice(0, limit);
}

function decorate(results) {
  return results.map((r) => ({
    path: r.path,
    name: path.basename(r.path),
    folder: path.dirname(r.path),
    modified: r.modified || "",
  }));
}

module.exports = { walkSearch, defaultRoots, matcher, decorate };
