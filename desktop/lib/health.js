"use strict";
/*
 * Crash resilience for Neural Desktop.
 *
 *  - A log file (desktop/logs/desktop.log, rotated at 1 MB) with startup
 *    info, main-process errors, renderer console errors, and every crash
 *    or hang -- so "it crashed" comes with a reason next time.
 *  - A run-state file in userData that notices when the previous run
 *    didn't exit cleanly. Two unclean exits in a row -> start in SAFE MODE
 *    (no GPU acceleration, no global keyboard hook, no system-audio
 *    capture): the usual suspects for a transparent always-on-top window
 *    dying on a particular PC. A few stable minutes reset the counter.
 */
const fs = require("fs");
const path = require("path");

const MAX_LOG_BYTES = 1024 * 1024;

class Health {
  constructor({ logDir, stateDir, argv = process.argv }) {
    this.logFile = path.join(logDir, "desktop.log");
    this.stateFile = path.join(stateDir, "run-state.json");
    this.logDir = logDir;
    this.stateDir = stateDir;
    let prev = {};
    try {
      prev = JSON.parse(fs.readFileSync(this.stateFile, "utf8")) || {};
    } catch (e) {
      prev = {};
    }
    this.previousCrashed = prev.cleanExit === false;
    this.crashes = this.previousCrashed ? (prev.crashes || 0) + 1 : 0;
    this.forcedSafe = argv.includes("--safe-mode");
    this.safeMode = this.forcedSafe || this.crashes >= 2;
    this.lastReason = prev.lastReason || "";
    this._writeState({ cleanExit: false, crashes: this.crashes, startedAt: new Date().toISOString(), lastReason: "" });
  }

  _writeState(obj) {
    try {
      fs.mkdirSync(this.stateDir, { recursive: true });
      const cur = (() => {
        try { return JSON.parse(fs.readFileSync(this.stateFile, "utf8")) || {}; } catch (e) { return {}; }
      })();
      fs.writeFileSync(this.stateFile, JSON.stringify(Object.assign(cur, obj), null, 2));
    } catch (e) {
      // a full disk must never stop the app from starting
    }
  }

  log(...parts) {
    const line = `${new Date().toISOString()} ${parts.map((p) => (p instanceof Error ? p.stack || p.message : typeof p === "string" ? p : safeJson(p))).join(" ")}\n`;
    try {
      fs.mkdirSync(this.logDir, { recursive: true });
      try {
        if (fs.statSync(this.logFile).size > MAX_LOG_BYTES) fs.renameSync(this.logFile, this.logFile + ".old");
      } catch (e) {}
      fs.appendFileSync(this.logFile, line);
    } catch (e) {}
  }

  // Remember why we're going down (shown in the log on the next start).
  noteReason(reason) {
    this._writeState({ lastReason: String(reason).slice(0, 300) });
  }

  markStable() {
    this.crashes = 0;
    this._writeState({ crashes: 0 });
  }

  markCleanExit() {
    this._writeState({ cleanExit: true, crashes: 0 });
  }

  // Keep the crash count so the relaunch comes up in safe mode.
  markCrashExit(reason) {
    this._writeState({ cleanExit: false, crashes: Math.max(this.crashes, 1), lastReason: String(reason).slice(0, 300) });
  }
}

function safeJson(v) {
  try {
    return JSON.stringify(v);
  } catch (e) {
    return String(v);
  }
}

module.exports = { Health };
