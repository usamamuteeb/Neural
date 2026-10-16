"use strict";
/*
 * Local fast paths: things the desktop app can answer or do by itself,
 * instantly and with zero model tokens -- the time, timers and reminders,
 * opening apps, finding files, "stop". Deliberately strict patterns: when
 * in doubt, return null and let Neural handle the message normally.
 */

const NUMBER_WORDS = {
  a: 1, an: 1, one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9, ten: 10,
  eleven: 11, twelve: 12, fifteen: 15, twenty: 20, thirty: 30, forty: 40, "forty five": 45, fortyfive: 45, sixty: 60, ninety: 90,
  couple: 2, "a couple": 2, "a couple of": 2, few: 3, "a few": 3,
};

const UNIT_MS = { s: 1000, sec: 1000, second: 1000, m: 60000, min: 60000, minute: 60000, h: 3600000, hr: 3600000, hour: 3600000 };

const NUM = "(\\d+(?:\\.\\d+)?|a couple of|a couple|a few|forty five|[a-z]+)";
const UNIT = "(seconds?|secs?|minutes?|mins?|hours?|hrs?|h|m|s)";
const LEAD = "^(?:hey\\s+)?(?:neural[,\\s]+)?(?:please\\s+)?(?:can you\\s+|could you\\s+|would you\\s+)?";
const TRAIL = "[\\s.!?]*$";

function toNumber(s) {
  s = String(s || "").toLowerCase().trim();
  if (/^\d+(\.\d+)?$/.test(s)) return parseFloat(s);
  return NUMBER_WORDS[s] !== undefined ? NUMBER_WORDS[s] : NaN;
}

function unitMs(u) {
  u = String(u || "").toLowerCase().replace(/s$/, "");
  return UNIT_MS[u] || UNIT_MS[u.replace(/s$/, "")] || NaN;
}

function durationMs(num, unit) {
  const n = toNumber(num);
  const u = unitMs(unit);
  if (!isFinite(n) || !isFinite(u) || n <= 0) return NaN;
  return Math.round(n * u);
}

function cleanTask(t) {
  return String(t || "")
    .trim()
    .replace(/^(to|about|that)\s+/i, "")
    .replace(/[\s.!?]+$/, "")
    .trim();
}

// "5", "5:30", "5pm", "17:00" (+ today/tonight/tomorrow) -> epoch ms, next occurrence.
function timeOfDay(hourStr, minStr, ampm, dayWord, now) {
  let h = parseInt(hourStr, 10);
  const m = minStr ? parseInt(minStr, 10) : 0;
  if (!(h >= 0 && h <= 23 && m >= 0 && m <= 59)) return NaN;
  ampm = (ampm || "").toLowerCase().replace(/\./g, "");
  dayWord = (dayWord || "").toLowerCase();
  if (ampm === "pm" && h < 12) h += 12;
  if (ampm === "am" && h === 12) h = 0;
  const base = new Date(now);
  const at = (hh, addDays) => {
    const d = new Date(base.getFullYear(), base.getMonth(), base.getDate() + addDays, hh, m, 0, 0);
    return d.getTime();
  };
  if (dayWord === "tomorrow") {
    if (!ampm && h < 12 && h >= 1 && h <= 6) h += 12; // "tomorrow at 3" -> 3pm
    return at(h, 1);
  }
  if (dayWord === "tonight" && !ampm && h < 12) h += 12;
  if (ampm || h > 12 || h === 0) {
    const t = at(h, 0);
    return t > now ? t : dayWord ? NaN : at(h, 1);
  }
  // No am/pm and ambiguous (1-12): the soonest of h or h+12 still ahead.
  const options = [at(h, 0), at(h === 12 ? 12 : h + 12, 0), at(h, 1)].filter((t) => t > now);
  return Math.min.apply(null, options);
}

function timerText(num, unit) {
  const u = String(unit).toLowerCase().replace(/s$/, "");
  const full = { sec: "second", s: "second", min: "minute", m: "minute", hr: "hour", h: "hour" }[u] || u;
  return `Your ${num}-${full} timer is done`;
}

function parseReminder(text, now = Date.now()) {
  const t = text.trim();
  let m;
  // Timers.
  m = t.match(new RegExp(LEAD + "(?:set|start)\\s+(?:a\\s+)?timer\\s+(?:for\\s+)?" + NUM + "\\s+" + UNIT + TRAIL, "i"));
  if (m) {
    const ms = durationMs(m[1], m[2]);
    if (isFinite(ms)) return { type: "reminder", text: timerText(m[1], m[2]), at: now + ms, timer: true };
  }
  m = t.match(new RegExp(LEAD + "(?:set|start)\\s+(?:a\\s+)?" + NUM + "[\\s-]+" + UNIT + "\\s+timer" + TRAIL, "i"));
  if (m) {
    const ms = durationMs(m[1], m[2]);
    if (isFinite(ms)) return { type: "reminder", text: timerText(m[1], m[2]), at: now + ms, timer: true };
  }
  if (!/remind me/i.test(t)) return null;
  const halfHour = /\bin\s+(?:half an hour|30 min(?:ute)?s?)\b/i;
  // remind me [to] X in N units
  m = t.match(new RegExp(LEAD + "remind me\\s+(.+?)\\s+in\\s+" + NUM + "\\s+" + UNIT + TRAIL, "i"));
  if (m) {
    const ms = durationMs(m[2], m[3]);
    if (isFinite(ms) && cleanTask(m[1])) return { type: "reminder", text: cleanTask(m[1]), at: now + ms };
  }
  // remind me in N units [to] X
  m = t.match(new RegExp(LEAD + "remind me\\s+in\\s+" + NUM + "\\s+" + UNIT + "\\s+(.+?)" + TRAIL, "i"));
  if (m) {
    const ms = durationMs(m[1], m[2]);
    if (isFinite(ms) && cleanTask(m[3])) return { type: "reminder", text: cleanTask(m[3]), at: now + ms };
  }
  if (halfHour.test(t)) {
    const task = cleanTask(t.replace(new RegExp(LEAD + "remind me", "i"), "").replace(halfHour, ""));
    if (task) return { type: "reminder", text: task, at: now + 30 * 60000 };
  }
  const TIME = "(\\d{1,2})(?::(\\d{2}))?\\s*(a\\.?m\\.?|p\\.?m\\.?)?";
  const DAY = "(today|tonight|tomorrow)";
  // remind me [to] X at TIME [day]
  m = t.match(new RegExp(LEAD + "remind me\\s+(.+?)\\s+(?:at|by)\\s+" + TIME + "(?:\\s+" + DAY + ")?" + TRAIL, "i"));
  if (m) {
    let task = m[1];
    let day = m[5];
    const dm = task.match(/\s+(today|tonight|tomorrow)$/i);
    if (dm) {
      day = day || dm[1];
      task = task.slice(0, dm.index);
    }
    const at = timeOfDay(m[2], m[3], m[4], day, now);
    if (isFinite(at) && cleanTask(task)) return { type: "reminder", text: cleanTask(task), at };
  }
  // remind me [day] at TIME [to] X
  m = t.match(new RegExp(LEAD + "remind me\\s+(?:" + DAY + "\\s+)?(?:at|by)\\s+" + TIME + "(?:\\s+" + DAY + ")?\\s+(.+?)" + TRAIL, "i"));
  if (m) {
    const at = timeOfDay(m[2], m[3], m[4], m[1] || m[5], now);
    if (isFinite(at) && cleanTask(m[6])) return { type: "reminder", text: cleanTask(m[6]), at };
  }
  return null;
}

function parseTime(text) {
  const re = new RegExp(
    LEAD + "(?:tell me\\s+)?(what(?:'s| is) the time|what time is it|the time|time please|what(?:'s| is) (?:the |today's )?date(?: today)?|what day is (?:it|today)|what(?:'s| is) today)" + TRAIL,
    "i"
  );
  const m = text.trim().match(re);
  if (!m) return null;
  return { type: /date|day|today/i.test(m[1]) ? "date" : "time" };
}

function parseLaunch(text) {
  const m = text
    .trim()
    .match(new RegExp(LEAD + "(?:open|launch|start|run|fire up|bring up)\\s+(?:up\\s+)?(?:the\\s+|my\\s+)?(.+?)(?:\\s+(?:app|application|program))?(?:\\s+for me)?(?:\\s+please)?" + TRAIL, "i"));
  if (!m) return null;
  const name = m[1].trim();
  if (!name || name.length > 40 || name.split(/\s+/).length > 5) return null;
  // Things that are clearly NOT an app name.
  if (/\b(file|folder|link|url|page|tab|email|document|recording|timer|video|chat)\b/i.test(name) && !/folder$/i.test(name)) return null;
  return { type: "launch", name };
}

function parseFileSearch(text) {
  const t = text.trim();
  let m = t.match(
    new RegExp(LEAD + "(?:find|search for|look for|locate|where(?:'s| is| are))\\s+(?:my\\s+|the\\s+|a\\s+)?(?:files?|documents?|docs?|pdfs?)\\s+(?:called|named|about|with|containing)?\\s*(.+?)" + TRAIL, "i")
  );
  if (m && m[1].trim()) return { type: "files", query: m[1].trim().replace(/^["']|["']$/g, "") };
  m = t.match(
    new RegExp(LEAD + "(?:find|locate|where(?:'s| is))\\s+(?:my\\s+|the\\s+)?([\\w .()-]+?\\.(?:pdf|docx?|xlsx?|pptx?|txt|md|csv|png|jpe?g|gif|mp4|mov|mp3|wav|zip|py|js|ts|html|json))" + TRAIL, "i")
  );
  if (m) return { type: "files", query: m[1].trim() };
  m = t.match(new RegExp(LEAD + "(?:find|locate|where(?:'s| is| are))\\s+(?:my|the)\\s+(.+?)\\s+(?:files?|documents?|docs?|pdfs?|spreadsheets?|presentations?)" + TRAIL, "i"));
  if (m && m[1].trim().split(/\s+/).length <= 5) return { type: "files", query: m[1].trim() };
  return null;
}

function parseStop(text) {
  return /^(?:neural[,\s]+)?(?:stop|stop talking|be quiet|quiet|shut up|enough|that's enough|cancel|never ?mind|shh+)[\s.!]*$/i.test(text.trim())
    ? { type: "stop" }
    : null;
}

function parseIntent(text, now = Date.now()) {
  if (!text || text.length > 200) return null;
  return parseStop(text) || parseTime(text) || parseReminder(text, now) || parseFileSearch(text) || parseLaunch(text);
}

function formatWhen(at, now = Date.now()) {
  const d = new Date(at);
  const diff = at - now;
  const time = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  if (diff < 60000) return `in ${Math.max(1, Math.round(diff / 1000))} seconds`;
  if (diff < 3600000) return `in ${Math.round(diff / 60000)} minute${Math.round(diff / 60000) === 1 ? "" : "s"} (${time})`;
  const sameDay = new Date(now).toDateString() === d.toDateString();
  const tomorrow = new Date(now + 86400000).toDateString() === d.toDateString();
  if (sameDay) return `at ${time}`;
  if (tomorrow) return `tomorrow at ${time}`;
  return d.toLocaleString([], { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

module.exports = { parseIntent, parseReminder, parseTime, parseLaunch, parseFileSearch, parseStop, formatWhen, timeOfDay };
