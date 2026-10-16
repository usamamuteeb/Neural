// Unit tests for the main-process helpers (plain Node, no Electron needed):
//   npm test
"use strict";
const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");

const intents = require("../lib/intents");
const launcher = require("../lib/launcher");
const layout = require("../lib/layout");
const { Settings } = require("../lib/settings");
const fileSearch = require("../lib/filesearch");
const suggest = require("../lib/suggest");
const { ContextTracker } = require("../lib/context");

let passed = 0;
function test(name, fn) {
  try {
    fn();
    passed++;
    console.log("PASS", name);
  } catch (e) {
    console.log("FAIL", name, "\n   ", e.message);
    process.exitCode = 1;
  }
}

const NOW = new Date(2026, 9, 7, 15, 0, 0).getTime(); // Wed 7 Oct 2026, 3:00 PM
const at = (r) => new Date(r.at);

test("time / date", () => {
  assert.deepStrictEqual(intents.parseIntent("what time is it?"), { type: "time" });
  assert.deepStrictEqual(intents.parseIntent("Neural, what's the date today"), { type: "date" });
  assert.strictEqual(intents.parseIntent("what do you think about the time we live in"), null);
});

test("reminders: relative", () => {
  let r = intents.parseIntent("remind me to call mom in 10 minutes", NOW);
  assert.strictEqual(r.text, "call mom");
  assert.strictEqual(r.at - NOW, 10 * 60000);
  r = intents.parseIntent("remind me in 2 hours to check the oven", NOW);
  assert.strictEqual(r.text, "check the oven");
  assert.strictEqual(r.at - NOW, 2 * 3600000);
  r = intents.parseIntent("Remind me to stretch in half an hour", NOW);
  assert.strictEqual(r.at - NOW, 30 * 60000);
});

test("reminders: clock times", () => {
  let r = intents.parseIntent("remind me to submit the report at 5", NOW);
  assert.strictEqual(at(r).getHours(), 17); // 5 with no am/pm at 3 PM -> 5 PM today
  r = intents.parseIntent("remind me tomorrow at 9am to email Ali", NOW);
  assert.strictEqual(at(r).getDate(), 8);
  assert.strictEqual(at(r).getHours(), 9);
  assert.strictEqual(r.text, "email Ali");
  r = intents.parseIntent("remind me to sleep at 10", NOW);
  assert.strictEqual(at(r).getHours(), 22);
  r = intents.parseIntent("remind me to pray at 2pm", NOW); // already passed today -> tomorrow
  assert.strictEqual(at(r).getDate(), 8);
});

test("timers", () => {
  let r = intents.parseIntent("set a timer for 5 minutes", NOW);
  assert.strictEqual(r.timer, true);
  assert.strictEqual(r.at - NOW, 300000);
  assert.strictEqual(r.text, "Your 5-minute timer is done");
  r = intents.parseIntent("start a 1 hr timer", NOW);
  assert.strictEqual(r.text, "Your 1-hour timer is done");
});

test("launch / files / stop", () => {
  assert.deepStrictEqual(intents.parseIntent("Can you open chrome please?"), { type: "launch", name: "chrome" });
  assert.strictEqual(intents.parseIntent("open the file I saved yesterday"), null);
  assert.strictEqual(intents.parseIntent("start recording my screen"), null);
  assert.deepStrictEqual(intents.parseIntent("find my resume file"), { type: "files", query: "resume" });
  assert.deepStrictEqual(intents.parseIntent("where is invoice.pdf"), { type: "files", query: "invoice.pdf" });
  assert.deepStrictEqual(intents.parseIntent("Neural, be quiet."), { type: "stop" });
  assert.strictEqual(intents.parseIntent("remind me about the meeting"), null); // no time -> Neural handles it
});

test("app matching", () => {
  const idx = ["Google Chrome", "Spotify", "Visual Studio Code", "Word", "WordPad"].map((name) => ({ name, key: launcher.normalize(name) }));
  assert.strictEqual(launcher.findApp("chrome", idx).name, "Google Chrome");
  assert.strictEqual(launcher.findApp("spot", idx).name, "Spotify");
  assert.strictEqual(launcher.findApp("vs code", idx).name, "Visual Studio Code");
  assert.strictEqual(launcher.findApp("word", idx).name, "Word");
  assert.strictEqual(launcher.findApp("youtube", idx).kind, "url");
  assert.strictEqual(launcher.findApp("the report", idx), null);
  const cmd = launcher.findApp("daily briefing", idx, [{ name: "Daily briefing", type: "prompt", target: "x" }]);
  assert.strictEqual(cmd.command.type, "prompt");
});

test("layout", () => {
  const wa = { x: 0, y: 0, width: 1920, height: 1040 };
  const av = layout.defaultAvatar(wa);
  assert.strictEqual(layout.chooseSide(av, wa), "left");
  assert.strictEqual(layout.chooseSide({ x: 10, y: 0, w: 200, h: 300 }, wa), "right");
  const b = layout.windowBounds(av, "left");
  assert.strictEqual(b.width, 200 + layout.BUBBLE_W + layout.GAP);
  assert.deepStrictEqual(layout.avatarFromWindow(b, "left", 200, 300), av);
  const c = layout.clampAvatar({ x: -500, y: 5000, w: 9999, h: 10 }, wa);
  assert.ok(c.x >= 0 && c.y + c.h <= 1040 && c.w <= layout.MAX.w && c.h >= layout.MIN.h);
});

test("settings persist + survive corruption", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "nd-settings-"));
  const s = new Settings(dir, { debounceMs: 0 });
  s.set("speakReplies", false);
  s.saveNow();
  const s2 = new Settings(dir);
  assert.strictEqual(s2.get("speakReplies"), false);
  assert.strictEqual(s2.get("hotkeys").voice, "Control+Alt+Space");
  fs.writeFileSync(path.join(dir, "settings.json"), "{not json");
  const s3 = new Settings(dir);
  assert.strictEqual(s3.get("speakReplies"), true);
});

test("file search fallback walk", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "nd-files-"));
  fs.mkdirSync(path.join(dir, "Docs", "2026"), { recursive: true });
  fs.writeFileSync(path.join(dir, "Docs", "2026", "Budget Plan.xlsx"), "x");
  fs.writeFileSync(path.join(dir, "notes.txt"), "x");
  fs.mkdirSync(path.join(dir, "node_modules", "budget"), { recursive: true });
  const res = fileSearch.decorate(fileSearch.walkSearch("budget plan", { roots: [dir] }));
  assert.strictEqual(res.length, 1);
  assert.strictEqual(res[0].name, "Budget Plan.xlsx");
});

test("suggestions by app", () => {
  assert.match(suggest.forWindow({ exe: "Code.exe", title: "main.py" })[0].label, /code/i);
  assert.match(suggest.forWindow({ exe: "chrome.exe", title: "Cool trick - YouTube - Google Chrome" })[0].label, /about/i);
  assert.match(suggest.forWindow({ exe: "chrome.exe", title: "Docs" })[0].label, /Summarize/);
  assert.match(suggest.forWindow({ exe: "WindowsTerminal.exe", title: "PowerShell" })[0].label, /error/);
  assert.strictEqual(suggest.forWindow({ exe: "random.exe", title: "x" }).length, 1);
});

test("context description", () => {
  assert.strictEqual(ContextTracker.describe({ title: "main.py - Visual Studio Code", app: "Visual Studio Code", exe: "Code.exe" }), '"main.py - Visual Studio Code" in Visual Studio Code');
  assert.strictEqual(ContextTracker.describe({ title: "", app: "", exe: "explorer.exe" }), "File Explorer");
});

console.log(`\n${passed} passed`);
