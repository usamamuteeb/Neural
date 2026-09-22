const chatLog = document.getElementById("chat-log");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const micBtn = document.getElementById("mic-btn");
const statusEl = document.getElementById("status");
const statusDot = document.getElementById("status-dot");
const voiceToggle = document.getElementById("voice-toggle");
const voiceSupportNote = document.getElementById("voice-support-note");
const notifToggle = document.getElementById("notif-toggle");
const panelToggle = document.getElementById("panel-toggle");
const sideCol = document.getElementById("side-col");
const sideBackdrop = document.getElementById("side-backdrop");
const ttsPlayer = document.getElementById("tts-player");
const uptimeEl = document.getElementById("uptime");

const orb = document.getElementById("orb");
const coreState = document.getElementById("core-state");

const activityFeed = document.getElementById("activity-feed");

const automationsList = document.getElementById("automations-list");
const modalBackdrop = document.getElementById("automation-modal-backdrop");
const openModalBtn = document.getElementById("automation-open-modal");
const closeModalBtn = document.getElementById("automation-modal-close");
const cancelBtn = document.getElementById("automation-cancel");
const automationForm = document.getElementById("automation-form");

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = String(s);
  return div.innerHTML;
}

/* ---------------- State / orb ---------------- */

const STATE_HUE = { idle: 190, listening: 45, thinking: 265, speaking: 150, offline: 200 };

function setState(state, label) {
  document.documentElement.style.setProperty("--state-hue", STATE_HUE[state] ?? STATE_HUE.idle);
  orb.classList.remove("thinking", "speaking", "listening");
  if (state && STATE_HUE[state] !== undefined && state !== "idle" && state !== "offline") {
    orb.classList.add(state);
  }
  coreState.textContent = label;
}

setState("offline", "STANDBY");

/* ---------------- Uptime ---------------- */

const bootTime = Date.now();
function pad(n) { return String(Math.floor(n)).padStart(2, "0"); }
function tickClock() {
  const elapsed = (Date.now() - bootTime) / 1000;
  uptimeEl.textContent = pad(elapsed / 3600) + ":" + pad((elapsed % 3600) / 60) + ":" + pad(elapsed % 60);
}
setInterval(tickClock, 1000);
tickClock();

/* ---------------- Chat ---------------- */

function addMessage(role, text) {
  const div = document.createElement("div");
  div.className = "msg " + role;
  div.textContent = text;
  chatLog.appendChild(div);
  chatLog.scrollTop = chatLog.scrollHeight;
  return div;
}

// Boosts TTS output beyond the browser's normal 0-1 volume ceiling --
// both ElevenLabs clips and especially the local pyttsx3 fallback have
// come across as noticeably quiet on some machines/speakers even at full
// <audio> volume. A single AudioContext + GainNode is created once (the
// Web Audio API only allows createMediaElementSource to be called once
// per <audio> element, ever) and reused for every subsequent TTS clip
// played through the same element.
let audioCtx = null;
let gainNode = null;
const TTS_VOLUME_BOOST = 2.5;

function ensureAudioBoost() {
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return;
    if (!audioCtx) audioCtx = new Ctx();
    if (audioCtx.state === "suspended") audioCtx.resume().catch(() => {});
    if (!gainNode) {
      const source = audioCtx.createMediaElementSource(ttsPlayer);
      gainNode = audioCtx.createGain();
      gainNode.gain.value = TTS_VOLUME_BOOST;
      source.connect(gainNode);
      gainNode.connect(audioCtx.destination);
    }
  } catch (e) {
    // Best-effort -- if this fails for any reason the <audio> element
    // still plays normally, just without the extra boost.
  }
}

// Every reply is now spoken sentence-by-sentence as it's generated (see
// main.py) instead of as one clip after the whole reply is done, so
// playback has to reassemble an in-order sequence of chunks that can
// arrive out of order (synthesis time varies per sentence) and that
// keep coming in after the text itself has finished streaming.
let chunkPlaying = false;     // the <audio> element is mid-playback of one chunk right now
let expectingAudio = false;   // this turn will produce at least a "no chunks" audio_done
let currentTurn = {
  queue: [],          // {seq, mime, data} not yet played, out of order is fine
  nextSeqToPlay: 1,
  totalChunks: null,  // set once the backend's audio_done arrives
  playedChunks: 0,
};

function resetTurnAudio() {
  // A new message was sent -- if the previous reply is still mid-sentence,
  // cut it off rather than let two replies' audio overlap (the same
  // "barge in" behavior you'd expect from any voice assistant).
  try { ttsPlayer.pause(); } catch (e) {}
  chunkPlaying = false;
  currentTurn = { queue: [], nextSeqToPlay: 1, totalChunks: null, playedChunks: 0 };
}

function playAudioChunk(base64Data, mime, onDone) {
  const bytes = atob(base64Data);
  const buffer = new Uint8Array(bytes.length);
  for (let i = 0; i < bytes.length; i++) buffer[i] = bytes.charCodeAt(i);
  const blob = new Blob([buffer], { type: mime || "audio/mpeg" });
  const url = URL.createObjectURL(blob);
  ttsPlayer.src = url;
  ttsPlayer.volume = 1.0;
  ensureAudioBoost();
  ttsPlayer.play().catch(onDone);
  ttsPlayer.onended = () => {
    URL.revokeObjectURL(url);
    onDone();
  };
}

function maybeFinishSpeaking() {
  if (currentTurn.totalChunks !== null && currentTurn.playedChunks >= currentTurn.totalChunks) {
    isSpeaking = false;
    expectingAudio = false;
    setState("idle", "ONLINE");
    resumeWakeListeningIfOn();
    return true;
  }
  return false;
}

function tryPlayNextChunk() {
  if (chunkPlaying) return;
  const idx = currentTurn.queue.findIndex((c) => c.seq === currentTurn.nextSeqToPlay);
  if (idx === -1) return; // still waiting for this seq to arrive
  const chunk = currentTurn.queue.splice(idx, 1)[0];
  currentTurn.nextSeqToPlay++;

  if (chunk.data == null) {
    // Every TTS tier failed for this one sentence -- skip it silently
    // rather than stall the rest of the reply waiting for audio that's
    // never coming.
    currentTurn.playedChunks++;
    if (!maybeFinishSpeaking()) tryPlayNextChunk();
    return;
  }

  if (!isSpeaking) {
    isSpeaking = true;
    pauseWakeListening();
    setState("speaking", "SPEAKING");
  }
  chunkPlaying = true;
  playAudioChunk(chunk.data, chunk.mime, () => {
    chunkPlaying = false;
    currentTurn.playedChunks++;
    if (!maybeFinishSpeaking()) tryPlayNextChunk();
  });
}

function onAudioChunk(seq, mime, data) {
  currentTurn.queue.push({ seq, mime, data });
  tryPlayNextChunk();
}

function onAudioDone(count) {
  currentTurn.totalChunks = count;
  maybeFinishSpeaking();
}

let socket;
let assistantDiv = null;
let historyLoaded = false; // only replay history once per page load, not on every reconnect

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(proto + "://" + location.host + "/ws");

  socket.onopen = () => {
    statusEl.textContent = "ONLINE";
    setState("idle", "ONLINE");
  };
  socket.onclose = () => {
    statusEl.textContent = "RECONNECTING";
    setState("offline", "STANDBY");
    setTimeout(connect, 1500);
  };
  socket.onerror = () => socket.close();

  socket.onmessage = (event) => {
    const data = JSON.parse(event.data);

    if (data.type === "history") {
      // Recent chat from the shared conversation (any device) -- lets a
      // freshly opened tab/phone pick up right where another device left
      // off instead of starting blank. Only applied once per page load;
      // a later reconnect of the same tab skips it to avoid duplicating
      // messages already on screen.
      if (!historyLoaded) {
        historyLoaded = true;
        for (const m of data.messages || []) {
          if (m.role === "user" || m.role === "assistant") addMessage(m.role, m.text);
        }
      }
    } else if (data.type === "assistant") {
      for (const block of data.blocks) {
        if (block.kind === "text") {
          if (!assistantDiv) assistantDiv = addMessage("assistant", "");
          assistantDiv.textContent += block.text;
          chatLog.scrollTop = chatLog.scrollHeight;
        }
      }
    } else if (data.type === "result") {
      assistantDiv = null;
      // Text is done, but audio for this reply may still be arriving/
      // playing (it streams sentence-by-sentence, on its own timeline) --
      // isSpeaking/expectingAudio cover "still has something to say"
      // regardless of whether that's text generation or trailing speech.
      if (!isSpeaking && !expectingAudio) setState("idle", "ONLINE");
    } else if (data.type === "audio") {
      onAudioChunk(data.seq, data.mime, data.data);
    } else if (data.type === "audio_done") {
      onAudioDone(data.count);
    }
  };
}

connect();

function sendMessage(text) {
  if (!text || !socket || socket.readyState !== WebSocket.OPEN) return;
  addMessage("user", text);
  assistantDiv = null;
  expectingAudio = true; // every reply now always talks -- see main.py
  resetTurnAudio();
  setState("thinking", "PROCESSING");
  socket.send(JSON.stringify({ type: "message", text }));
}

chatForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = chatInput.value.trim();
  if (!text) return;
  sendMessage(text);
  chatInput.value = "";
});

/* ---------------- Voice: mic + wake word ---------------- */

const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
let wakePhrase = "wake up";
let wakeWordOn = false;
let continuousRecognition = null;
let isSpeaking = false;

fetch("/api/config")
  .then((r) => r.json())
  .then((cfg) => { if (cfg.wake_phrase) wakePhrase = cfg.wake_phrase.toLowerCase(); })
  .catch(() => {});

if (!SR) {
  micBtn.disabled = true;
  voiceToggle.disabled = true;
  voiceSupportNote.hidden = false;
} else if (navigator.brave && typeof navigator.brave.isBrave === "function") {
  // Brave ships the SpeechRecognition constructor (so the check above
  // passes) but its implementation has been silently non-functional for
  // years -- recognition starts and ends immediately with no result and
  // no error. Rather than let that look like Neural is broken, tell you
  // up front. See: github.com/brave/brave-browser/issues/18569
  navigator.brave.isBrave().then((isBrave) => {
    if (isBrave) {
      voiceSupportNote.hidden = false;
      voiceSupportNote.textContent = "Brave blocks voice recognition (a known Brave bug) — use Chrome or Edge for mic and wake word.";
    }
  });
}

function sendVoiceCommand(text) {
  sendMessage(text);
}

// Wake word mode, once turned on with the toggle button, listens
// continuously and treats every finished utterance as a command straight
// away -- no need to repeat the wake phrase before each one. Turning the
// toggle on is itself the deliberate "start listening" action, so the
// phrase is now optional: say it out of habit and it's just stripped off
// the front, or skip it entirely and talk directly. This trades away the
// old per-utterance gating, so anything the mic picks up while wake word
// mode is on gets sent to Neural -- turn the toggle off (or use the
// push-to-talk mic button instead) in situations where that's not wanted.
function handleWakeTranscript(transcript) {
  const trimmed = transcript.trim();
  if (!trimmed) return;
  const lower = trimmed.toLowerCase();
  const idx = lower.indexOf(wakePhrase);
  const command = idx !== -1 ? trimmed.slice(idx + wakePhrase.length).trim() : trimmed;
  if (!command) return; // just the wake phrase alone, nothing to act on yet
  sendVoiceCommand(command);
}

function startContinuousListening() {
  if (!SR || isSpeaking || continuousRecognition) return;
  const rec = new SR();
  rec.continuous = true;
  rec.interimResults = false;
  rec.lang = "en-US";
  rec.onresult = (event) => {
    for (let i = event.resultIndex; i < event.results.length; i++) {
      if (!event.results[i].isFinal) continue;
      handleWakeTranscript(event.results[i][0].transcript.trim());
    }
  };
  rec.onerror = (e) => {
    if (e.error === "not-allowed" || e.error === "service-not-allowed") {
      addMessage("system", "Microphone access blocked — allow it in your browser to use wake-word listening.");
      wakeWordOn = false;
      updateVoiceToggleUI();
      continuousRecognition = null;
    }
  };
  rec.onend = () => {
    continuousRecognition = null;
    if (wakeWordOn && !isSpeaking) startContinuousListening();
  };
  continuousRecognition = rec;
  try {
    rec.start();
  } catch (e) {
    continuousRecognition = null;
  }
}

function stopContinuousListening() {
  if (continuousRecognition) {
    const rec = continuousRecognition;
    rec.onend = null;
    continuousRecognition = null;
    try { rec.stop(); } catch (e) {}
  }
  if (!isSpeaking) setState("idle", "ONLINE");
}

function pauseWakeListening() {
  if (continuousRecognition) {
    const rec = continuousRecognition;
    rec.onend = null;
    continuousRecognition = null;
    try { rec.stop(); } catch (e) {}
  }
}

function resumeWakeListeningIfOn() {
  if (wakeWordOn) startContinuousListening();
}

function updateVoiceToggleUI() {
  voiceToggle.textContent = wakeWordOn ? "WAKE WORD ON" : "WAKE WORD OFF";
  voiceToggle.classList.toggle("on", wakeWordOn);
}

voiceToggle.addEventListener("click", () => {
  if (!SR) return;
  wakeWordOn = !wakeWordOn;
  updateVoiceToggleUI();
  if (wakeWordOn) startContinuousListening();
  else stopContinuousListening();
});

micBtn.addEventListener("click", () => {
  if (!SR || micBtn.classList.contains("recording")) return;
  const rec = new SR();
  rec.continuous = false;
  rec.interimResults = false;
  rec.lang = "en-US";
  micBtn.classList.add("recording");
  setState("listening", "LISTENING…");
  rec.onresult = (e) => {
    const text = e.results[0][0].transcript.trim();
    if (text) sendVoiceCommand(text);
  };
  rec.onerror = () => {
    if (!isSpeaking) setState("idle", "ONLINE");
  };
  rec.onend = () => {
    micBtn.classList.remove("recording");
    if (!isSpeaking) setState("idle", "ONLINE");
  };
  try {
    rec.start();
  } catch (e) {
    micBtn.classList.remove("recording");
  }
});

/* ---------------- Live activity feed ---------------- */

function classify(event) {
  if (event.type === "tts_error") return { cls: "tag-error", tag: "TTS" };
  if (event.type === "automation_run") return { cls: "tag-automation", tag: "AUTO" };
  const tool = event.tool || "";
  if (tool === "Bash") return { cls: "tag-bash", tag: tool };
  if (tool === "Write" || tool === "Edit" || tool === "Read") return { cls: "tag-file", tag: tool };
  if (tool.includes("browser")) return { cls: "tag-browser", tag: tool };
  if (tool.includes("gmail") || tool.includes("calendar")) return { cls: "tag-mail", tag: tool };
  if (tool.includes("memory")) return { cls: "tag-memory", tag: tool };
  return { cls: "tag-default", tag: tool || event.type };
}

function summarizeInput(tool, input) {
  if (!input) return "";
  if (tool === "Bash" && input.command) return input.command;
  if ((tool === "Write" || tool === "Edit") && input.file_path) return input.file_path;
  if (input.path) return input.path;
  if (input.url) return input.url;
  if (input.query) return input.query;
  try { return JSON.stringify(input).slice(0, 90); } catch (e) { return ""; }
}

function renderActivity(event) {
  if (event.type === "message" || event.type === "tool_result") return;

  const empty = activityFeed.querySelector(".empty-note");
  if (empty) empty.remove();

  const { cls, tag } = classify(event);
  let detail = "";
  if (event.type === "tool_use") detail = summarizeInput(event.tool, event.input);
  else if (event.type === "automation_run") detail = event.name + " — " + event.status;
  else if (event.type === "tts_error") detail = event.message;

  const source = event.source && event.source !== "chat" ? event.source : "";

  const item = document.createElement("div");
  item.className = "activity-item";
  item.innerHTML =
    '<span class="tag ' + cls + '">' + escapeHtml(tag) + "</span>" +
    '<span class="detail">' + escapeHtml(detail) + "</span>" +
    (source ? '<span class="src">' + escapeHtml(source) + "</span>" : "");

  activityFeed.appendChild(item);
  activityFeed.scrollTop = activityFeed.scrollHeight;
  while (activityFeed.children.length > 150) activityFeed.removeChild(activityFeed.firstChild);
}

if (!activityFeed.children.length) {
  activityFeed.innerHTML = '<div class="empty-note">No activity yet.</div>';
}

let eventsSocket;
function connectEvents() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  eventsSocket = new WebSocket(proto + "://" + location.host + "/events");
  eventsSocket.onclose = () => setTimeout(connectEvents, 1500);
  eventsSocket.onerror = () => eventsSocket.close();
  eventsSocket.onmessage = (event) => {
    const data = JSON.parse(event.data);
    renderActivity(data);
    if (data.type === "automation_run") loadAutomations();
  };
}
connectEvents();

/* ---------------- Automations ---------------- */

function describeCron(cron) {
  const parts = (cron || "").trim().split(/\s+/);
  if (parts.length !== 5) return cron;
  const [min, hour, dom, mon, dow] = parts;
  const timeStr = (h, m) => {
    let hh = parseInt(h, 10);
    const ampm = hh >= 12 ? "PM" : "AM";
    let h12 = hh % 12; if (h12 === 0) h12 = 12;
    return h12 + ":" + String(m).padStart(2, "0") + " " + ampm;
  };
  if (/^\*\/\d+$/.test(min) && hour === "*" && dom === "*" && mon === "*" && dow === "*") {
    return "Every " + min.slice(2) + " minutes";
  }
  if (dom === "*" && mon === "*" && dow === "*" && /^\d+$/.test(min) && /^\d+$/.test(hour)) {
    return "Daily at " + timeStr(hour, min);
  }
  if (dom === "*" && mon === "*" && dow !== "*" && /^\d+$/.test(min) && /^\d+$/.test(hour)) {
    const names = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
    const days = dow.split(",").map((d) => names[parseInt(d, 10)] ?? d).join(", ");
    return "Weekly on " + days + " at " + timeStr(hour, min);
  }
  return cron;
}

/* modal + schedule builder */

const freqSelect = document.getElementById("auto-freq");
const freqBlocks = {
  daily: document.getElementById("freq-daily"),
  weekly: document.getElementById("freq-weekly"),
  minutes: document.getElementById("freq-minutes"),
  custom: document.getElementById("freq-custom"),
};
const cronPreview = document.getElementById("cron-preview");
const dayButtons = Array.from(document.querySelectorAll(".day"));
let selectedDays = new Set();

dayButtons.forEach((btn) => {
  btn.addEventListener("click", () => {
    const d = btn.dataset.day;
    if (selectedDays.has(d)) { selectedDays.delete(d); btn.classList.remove("active"); }
    else { selectedDays.add(d); btn.classList.add("active"); }
    updateCronPreview();
  });
});

function currentCron() {
  const freq = freqSelect.value;
  if (freq === "daily") {
    const [h, m] = (document.getElementById("auto-time").value || "08:00").split(":");
    return m + " " + h + " * * *";
  }
  if (freq === "weekly") {
    const [h, m] = (document.getElementById("auto-time-weekly").value || "08:00").split(":");
    const days = Array.from(selectedDays).sort().join(",") || "1";
    return m + " " + h + " * * " + days;
  }
  if (freq === "minutes") {
    const n = document.getElementById("auto-minutes").value || "15";
    return "*/" + n + " * * * *";
  }
  return document.getElementById("auto-cron-raw").value.trim();
}

function updateCronPreview() {
  const cron = currentCron();
  cronPreview.textContent = cron ? "Runs: " + describeCron(cron) : "Enter a cron expression";
}

function updateFreqVisibility() {
  const freq = freqSelect.value;
  Object.entries(freqBlocks).forEach(([key, el]) => { el.hidden = key !== freq; });
  updateCronPreview();
}
freqSelect.addEventListener("change", updateFreqVisibility);
["auto-time", "auto-time-weekly", "auto-minutes", "auto-cron-raw"].forEach((id) => {
  document.getElementById(id).addEventListener("input", updateCronPreview);
});
updateFreqVisibility();

function openModal() {
  modalBackdrop.hidden = false;
}
function closeModal() {
  modalBackdrop.hidden = true;
  automationForm.reset();
  selectedDays = new Set();
  dayButtons.forEach((b) => b.classList.remove("active"));
  freqSelect.value = "daily";
  updateFreqVisibility();
}
openModalBtn.addEventListener("click", openModal);
closeModalBtn.addEventListener("click", closeModal);
cancelBtn.addEventListener("click", closeModal);
modalBackdrop.addEventListener("click", (e) => { if (e.target === modalBackdrop) closeModal(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !modalBackdrop.hidden) closeModal(); });

automationForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = document.getElementById("auto-name").value.trim();
  const prompt = document.getElementById("auto-prompt").value.trim();
  const cron = currentCron();
  if (!name || !prompt || !cron) return;
  await fetch("/api/automations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, cron, prompt, enabled: true }),
  });
  closeModal();
  loadAutomations();
});

function renderAutomations(items) {
  if (!items.length) {
    automationsList.innerHTML = '<div class="empty-note">No automations yet.</div>';
    return;
  }
  automationsList.innerHTML = "";
  for (const a of items) {
    const row = document.createElement("div");
    row.className = "automation-row";
    const nextRun = a.next_run ? new Date(a.next_run).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "—";
    row.innerHTML =
      '<div class="automation-row-top">' +
      "<div>" +
      '<div class="automation-name">' + escapeHtml(a.name) + "</div>" +
      '<div class="automation-schedule">' + escapeHtml(describeCron(a.cron)) + " · next " + escapeHtml(nextRun) + "</div>" +
      "</div>" +
      '<div class="automation-actions">' +
      '<button class="icon-btn" data-action="run" title="Run now">▶</button>' +
      '<label class="switch"><input type="checkbox" data-action="toggle" ' + (a.enabled ? "checked" : "") + '><span class="switch-track"></span></label>' +
      '<button class="icon-btn" data-action="delete" title="Delete">✕</button>' +
      "</div></div>";

    row.querySelector('[data-action="run"]').addEventListener("click", () =>
      fetch("/api/automations/" + a.id + "/run", { method: "POST" })
    );
    row.querySelector('[data-action="toggle"]').addEventListener("change", (ev) =>
      fetch("/api/automations/" + a.id + "/toggle", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: ev.target.checked }),
      }).then(loadAutomations)
    );
    row.querySelector('[data-action="delete"]').addEventListener("click", () =>
      fetch("/api/automations/" + a.id, { method: "DELETE" }).then(loadAutomations)
    );

    automationsList.appendChild(row);
  }
}

async function loadAutomations() {
  try {
    const res = await fetch("/api/automations");
    renderAutomations(await res.json());
  } catch (e) {
    // server not ready; next poll retries
  }
}
loadAutomations();
setInterval(loadAutomations, 30000);

/* ---------------- Mobile side drawer ---------------- */

function openPanel() {
  sideCol.classList.add("open");
  sideBackdrop.classList.add("show");
}
function closePanel() {
  sideCol.classList.remove("open");
  sideBackdrop.classList.remove("show");
}
if (panelToggle) {
  panelToggle.addEventListener("click", () => {
    sideCol.classList.contains("open") ? closePanel() : openPanel();
  });
  sideBackdrop.addEventListener("click", closePanel);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && sideCol.classList.contains("open")) closePanel();
  });
}

/* ---------------- Push notifications ---------------- */

function urlBase64ToUint8Array(base64String) {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  const arr = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) arr[i] = raw.charCodeAt(i);
  return arr;
}

const pushSupported = "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;

async function updateNotifToggleUI() {
  if (!notifToggle) return;
  if (!pushSupported) {
    notifToggle.hidden = true;
    return;
  }
  try {
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.getSubscription();
    notifToggle.textContent = sub ? "ALERTS ON" : "ALERTS OFF";
    notifToggle.classList.toggle("on", !!sub);
  } catch (e) {
    // service worker not ready yet or blocked -- leave the default label
  }
}

async function enablePush() {
  const permission = await Notification.requestPermission();
  if (permission !== "granted") {
    addMessage("system", "Notifications blocked — allow them for this site in your browser/OS settings to get alerts.");
    return;
  }
  try {
    const reg = await navigator.serviceWorker.ready;
    const keyRes = await fetch("/api/push/vapid-public-key");
    const { key } = await keyRes.json();
    const sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(key),
    });
    await fetch("/api/push/subscribe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(sub.toJSON()),
    });
  } catch (e) {
    addMessage("system", "Couldn't turn on notifications: " + e.message);
  }
  updateNotifToggleUI();
}

async function disablePush() {
  try {
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.getSubscription();
    if (sub) {
      await fetch("/api/push/unsubscribe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ endpoint: sub.endpoint }),
      });
      await sub.unsubscribe();
    }
  } catch (e) {
    // best-effort
  }
  updateNotifToggleUI();
}

if (notifToggle && pushSupported) {
  notifToggle.addEventListener("click", async () => {
    const reg = await navigator.serviceWorker.ready.catch(() => null);
    const sub = reg ? await reg.pushManager.getSubscription() : null;
    if (sub) await disablePush();
    else await enablePush();
  });
}

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("service-worker.js").catch(() => {});
  navigator.serviceWorker.ready.then(updateNotifToggleUI).catch(() => {});
} else if (notifToggle) {
  notifToggle.hidden = true;
}
