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
const opsToggle = document.getElementById("ops-toggle");
const opsPanel = document.getElementById("ops-panel");
const opsBackdrop = document.getElementById("ops-backdrop");
const opsClose = document.getElementById("ops-close");
const ttsPlayer = document.getElementById("tts-player");
const uptimeEl = document.getElementById("uptime");

const orb = document.getElementById("orb");
const coreState = document.getElementById("core-state");

// Neural's animated avatar (frontend/avatar.js). Optional by design: if
// that script failed to load or throws for any reason, everything below
// keeps working exactly as before with just the orb.
let avatar = null;
// Background jobs currently running (id -> title), from the live event
// feed, and the last UI state -- so the avatar can say "Background: ..."
// whenever she's otherwise idle. Declared up here because setState()
// below runs once at load.
const backgroundJobs = new Map();
let uiState = "offline";
try {
  const avatarMount = document.getElementById("avatar-mount");
  if (avatarMount && window.NeuralAvatar) avatar = new window.NeuralAvatar(avatarMount);
  if (avatar) {
    document.getElementById("avatar-stage").classList.add("has-avatar");
    window.neuralAvatar = avatar; // handy from the browser console
  }
} catch (e) {
  console.error("Avatar failed to start:", e);
  avatar = null;
}

const activityFeed = document.getElementById("activity-feed");
const observabilityUpdated = document.getElementById("observability-updated");
const opsSummary = document.getElementById("ops-summary");
const opsHealth = document.getElementById("ops-health");
const opsUtility = document.getElementById("ops-utility");
const opsActiveWrap = document.getElementById("ops-active-wrap");
const opsActive = document.getElementById("ops-active");
const opsFailuresWrap = document.getElementById("ops-failures-wrap");
const opsFailures = document.getElementById("ops-failures");

const automationsList = document.getElementById("automations-list");
const modalBackdrop = document.getElementById("automation-modal-backdrop");
const openModalBtn = document.getElementById("automation-open-modal");
const closeModalBtn = document.getElementById("automation-modal-close");
const cancelBtn = document.getElementById("automation-cancel");
const automationForm = document.getElementById("automation-form");

const modeTabs = document.getElementById("mode-tabs");
const blockAutomations = document.getElementById("block-automations");
const blockKnowledgeBase = document.getElementById("block-knowledge-base");

const kbList = document.getElementById("kb-list");
const kbModalBackdrop = document.getElementById("kb-modal-backdrop");
const kbOpenModalBtn = document.getElementById("kb-open-modal");
const kbModalClose = document.getElementById("kb-modal-close");
const kbCancelBtn = document.getElementById("kb-cancel");
const kbForm = document.getElementById("kb-form");
const kbPathInput = document.getElementById("kb-path");
const kbSubmitBtn = document.getElementById("kb-submit");

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = String(s);
  return div.innerHTML;
}

// Hoisted up here (rather than down in the Mode: Chat / Tutor section
// where it conceptually lives) because setState()/STATE_HUE below read
// it on every call, including the very first setState() a few lines
// down -- it has to exist before that runs.
let currentMode = "chat";

/* ---------------- State / orb ---------------- */

// Two full hue palettes, not just one -- Tutor mode gets its own
// visual identity (a red/crimson family) instead of the same cyan/
// amber/violet/green Chat uses, so the whole UI (orb, glow, buttons,
// the active mode tab -- everything derives its color from
// --state-hue, see style.css) shifts palette the instant you switch
// modes, animated by --state-hue's existing registered-property
// transition rather than snapping instantly.
const STATE_HUE = {
  chat: { idle: 190, listening: 45, thinking: 265, speaking: 150, offline: 200 },
  tutor: { idle: 355, listening: 18, thinking: 330, speaking: 5, offline: 355 },
};

function setState(state, label) {
  const hues = STATE_HUE[currentMode] || STATE_HUE.chat;
  const hue = hues[state];
  document.documentElement.style.setProperty("--state-hue", hue ?? hues.idle);
  orb.classList.remove("thinking", "speaking", "listening");
  if (state && hue !== undefined && state !== "idle" && state !== "offline") {
    orb.classList.add(state);
  }
  coreState.textContent = label;
  uiState = state;
  if (avatar) {
    avatar.setState(state || "idle");
    if (state !== "thinking") avatar.setActivity(null);
    refreshIdleChip();
  }
}

function refreshIdleChip() {
  if (!avatar || uiState !== "idle" || !backgroundJobs.size) return;
  const first = backgroundJobs.values().next().value;
  const more = backgroundJobs.size > 1 ? " (+" + (backgroundJobs.size - 1) + ")" : "";
  avatar.setActivity("Background: " + first + more, "gear");
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

/* ---------------- Operations dashboard ---------------- */

function shortTime(ts) {
  if (!ts) return "";
  try { return new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); }
  catch (e) { return ""; }
}

function renderOpsList(el, rows, emptyText, render) {
  if (!rows || !rows.length) {
    el.innerHTML = '<div class="empty-note">' + escapeHtml(emptyText) + "</div>";
    return;
  }
  el.innerHTML = rows.map(render).join("");
}

function renderObservability(data) {
  const usage = data.usage || {};
  const tokens = usage.tokens || {};
  const cost = Number(usage.recorded_cost_usd || 0).toFixed(4);
  opsSummary.innerHTML =
    '<span><b>' + escapeHtml(usage.audited_actions || 0) + '</b> actions</span>' +
    '<span><b>' + escapeHtml(usage.tool_calls || 0) + '</b> tools</span>' +
    '<span><b>' + escapeHtml(Math.round((tokens.input || 0) / 1000)) + 'k</b> input</span>' +
    '<span><b>' + escapeHtml(Math.round((tokens.output || 0) / 1000)) + 'k</b> output</span>' +
    '<span><b>$' + escapeHtml(cost) + '</b> recorded</span>';

  const health = data.health || {};
  opsHealth.innerHTML = Object.entries(health).map(([name, item]) => {
    const status = item.status || "unknown";
    const cls = status === "configured" || status === "ready" || status === "enabled" ? "ok" : "muted";
    return '<span class="ops-health ' + cls + '" title="' + escapeHtml(status) + '">' +
      escapeHtml(name) + "</span>";
  }).join("");

  const utility = data.utility || {};
  const routedTo = utility.selected || "claude";
  const byProvider = utility.by_provider || {};
  const qwenCalls = byProvider.qwen || 0;
  const claudeCalls = byProvider.claude || 0;
  opsUtility.innerHTML =
    '<div class="ops-row"><span>ACTIVE</span><span>' + escapeHtml(routedTo) + '</span><span>' +
    escapeHtml(utility.calls || 0) + ' calls</span></div>' +
    '<div class="ops-row"><span>QWEN</span><span>stateless only</span><span>' + escapeHtml(qwenCalls) + '</span></div>' +
    '<div class="ops-row"><span>CLAUDE</span><span>fallback</span><span>' + escapeHtml(claudeCalls) + '</span></div>';

  const active = data.active_jobs || [];
  opsActiveWrap.hidden = !active.length;
  renderOpsList(opsActive, active, "No active jobs.", (name) =>
    '<div class="ops-row active"><span>RUNNING</span><span>' + escapeHtml(name) + "</span></div>");

  const failures = data.failures || [];
  opsFailuresWrap.hidden = !failures.length;
  renderOpsList(opsFailures, failures, "No failures recorded.", (item) =>
    '<div class="ops-row failure"><span>' + escapeHtml(item.tool || item.type || "error") + '</span><span>' +
      escapeHtml(item.summary || "Failed") + "</span></div>");

  observabilityUpdated.textContent = "LIVE " + shortTime(data.generated_at);
}

async function loadObservability() {
  try {
    const response = await fetch("/api/observability");
    if (!response.ok) throw new Error("dashboard request failed");
    renderObservability(await response.json());
  } catch (e) {
    observabilityUpdated.textContent = "OFFLINE";
  }
}

loadObservability();
setInterval(loadObservability, 15000);

/* ---------------- Chat ---------------- */

// Small, escape-first markdown renderer for assistant replies (Tutor mode
// sends code blocks; chat replies occasionally carry **bold** or a link).
// Everything is HTML-escaped BEFORE any markup is added, and links only
// ever become <a> tags for plain http(s) URLs with no quotes/brackets in
// them, so model output can never inject markup of its own.
function renderMarkdown(src) {
  const blocks = [];
  const inline = [];
  let out = escapeHtml(src);
  out = out.replace(/```[\w+-]*\n?([\s\S]*?)(?:```|$)/g, (m, code) => {
    blocks.push("<pre><code>" + code.replace(/\n$/, "") + "</code></pre>");
    return "\u0000" + (blocks.length - 1) + "\u0000";
  });
  out = out.replace(/`([^`\n]+)`/g, (m, c) => {
    inline.push("<code>" + c + "</code>");
    return "\u0001" + (inline.length - 1) + "\u0001";
  });
  out = out
    .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*\w])\*([^*\n]+)\*(?!\w)/g, "$1<em>$2</em>")
    .replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)"'<>]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
    .replace(/(^|[\s(])(https?:\/\/[^\s)"'<>]+)/g, '$1<a href="$2" target="_blank" rel="noopener noreferrer">$2</a>');
  let html = "";
  let list = null;
  let para = [];
  const flushPara = () => { if (para.length) { html += "<p>" + para.join("<br>") + "</p>"; para = []; } };
  const flushList = () => { if (list) { html += "</" + list + ">"; list = null; } };
  for (const line of out.split("\n")) {
    const ul = line.match(/^\s*[-*\u2022]\s+(.*)$/);
    const ol = line.match(/^\s*\d+[.)]\s+(.*)$/);
    if (ul || ol) {
      flushPara();
      const tag = ul ? "ul" : "ol";
      if (list !== tag) { flushList(); html += "<" + tag + ">"; list = tag; }
      html += "<li>" + (ul ? ul[1] : ol[1]) + "</li>";
      continue;
    }
    flushList();
    if (!line.trim()) { flushPara(); continue; }
    if (/^\u0000\d+\u0000$/.test(line.trim())) { flushPara(); html += line.trim(); continue; }
    const heading = line.match(/^#{1,4}\s+(.*)$/);
    if (heading) { flushPara(); html += "<p><strong>" + heading[1] + "</strong></p>"; continue; }
    para.push(line);
  }
  flushPara();
  flushList();
  return html
    .replace(/\u0000(\d+)\u0000/g, (m, i) => blocks[+i])
    .replace(/\u0001(\d+)\u0001/g, (m, i) => inline[+i]);
}

function setMessageText(div, text) {
  div._raw = text;
  const body = div.querySelector(".msg-body") || div;
  if (div.classList.contains("assistant")) body.innerHTML = renderMarkdown(text);
  else body.textContent = text;
}

function addMessage(role, text, images) {
  const div = document.createElement("div");
  div.className = "msg " + role;
  if (images && images.length) {
    const strip = document.createElement("div");
    strip.className = "msg-images";
    for (const src of images) {
      const img = document.createElement("img");
      img.src = src;
      img.alt = "Image shown to Neural";
      strip.appendChild(img);
    }
    div.appendChild(strip);
  }
  const body = document.createElement("div");
  body.className = "msg-body";
  div.appendChild(body);
  setMessageText(div, text || "");
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
      // Lip-sync tap for the avatar: measures the raw clip (before the
      // boost above) so her mouth follows the actual speech. Routed into
      // a muted gain node so every browser keeps pulling audio through it.
      try {
        const analyser = audioCtx.createAnalyser();
        analyser.fftSize = 1024;
        analyser.smoothingTimeConstant = 0.2;
        const mute = audioCtx.createGain();
        mute.gain.value = 0;
        source.connect(analyser);
        analyser.connect(mute);
        mute.connect(audioCtx.destination);
        if (avatar) avatar.setAnalyser(analyser);
      } catch (e) {}
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
// Every message sent gets an incrementing id; the backend echoes it back
// on every event belonging to that turn (assistant text, audio chunks,
// audio_done, result). If a barge-in starts a NEWER turn before an OLDER
// one has finished winding down server-side, stray leftover events from
// the old turn arrive tagged with an id that no longer matches
// activeTurnId and get dropped -- otherwise they'd bleed into the new
// turn's chat bubble or audio queue (wrong text appended, chunks played
// out of order/twice).
let turnCounter = 0;
let activeTurnId = null;
// What Neural is saying/said in the current reply, and when she last
// stopped talking -- used to tell YOUR voice apart from her own voice
// leaking from the speakers into the mic (see looksLikeOwnEcho below).
let replyTextForEcho = "";
let lastSpeechEndedAt = 0;
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
    lastSpeechEndedAt = Date.now();
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
    // Deliberately NOT pausing wake-word listening here (unlike before) --
    // the mic needs to stay live while Neural talks so the user can barge
    // in and interrupt it, the same way you'd talk over a real assistant.
    // Risk: SpeechRecognition can pick up Neural's own voice from the
    // speakers as if it were you talking (an echo/feedback false-positive),
    // which would self-interrupt for no reason. Modern Chrome/Edge apply
    // real echo cancellation to mic capture and this held up in testing,
    // but if it misfires in your setup, turn WAKE WORD off during
    // playback or use the push-to-talk mic button instead.
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

/* ---------------- Automation output (background chat delivery) ---------------- */
// An automation can finish at any moment -- on its own cron schedule, or
// via the "run now" button -- including while a live chat turn is
// already streaming text/audio on this exact connection. This runs on a
// fully separate pipeline (own <audio> element, own queue/state, its own
// plain chat bubble) instead of reusing the live-turn machinery below,
// so it can never corrupt an in-progress reply's bubble or interleave
// its audio chunks with the reply's own.
const automationAudioEl = new Audio();
automationAudioEl.addEventListener("playing", () => { if (avatar) avatar.setTalking(true); });
for (const ev of ["pause", "ended", "error"]) {
  automationAudioEl.addEventListener(ev, () => { if (avatar) avatar.setTalking(false); });
}
let automationQueue = [];     // {seq, mime, data} not yet played
let automationNextSeq = 1;

// iOS Safari (unlike desktop Chrome/Edge) refuses to play() an <audio>
// element unless that exact call happens synchronously inside a real
// user gesture (tap/click) -- and every real play() here happens later,
// async, after a WebSocket round-trip, which is what silently killed
// spoken replies on mobile while text still arrived fine (a rejected
// play() throws NotAllowedError, which playAudioChunk's .catch(onDone)
// swallows as if the clip had just finished, with nothing visibly
// wrong). Fix: "warm up" both audio elements once, on the very first
// tap anywhere on the page, with a real (if silent) play() call made
// directly inside that gesture -- WebKit then treats each element as
// unlocked for the rest of the page's lifetime, so later async play()
// calls succeed normally. Each <audio> element needs its OWN unlock,
// hence doing both here rather than just ttsPlayer.
const _SILENT_AUDIO_SRC =
  "data:audio/wav;base64,UklGRigAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQQAAAAAAAAA";
let _audioUnlocked = false;

function unlockAudioPlayback() {
  if (_audioUnlocked) return;
  _audioUnlocked = true;
  // Also create/resume the AudioContext used for volume-boosting TTS
  // playback (see ensureAudioBoost above) right here, inside this same
  // real gesture. iOS starts every AudioContext "suspended" and, like
  // <audio>.play(), typically only honors .resume() when it's called
  // (or at least *triggered*) from within a genuine user gesture --
  // otherwise it can stay suspended forever even though .resume() was
  // technically called later from async code, which would mean no
  // sound ever comes out of the boosted path even after the <audio>
  // unlock above succeeds.
  ensureAudioBoost();
  for (const el of [ttsPlayer, automationAudioEl]) {
    try {
      const prevSrc = el.src;
      el.src = _SILENT_AUDIO_SRC;
      const p = el.play();
      if (p && typeof p.then === "function") {
        p.then(() => {
          try {
            el.pause();
            el.src = prevSrc || "";
          } catch (e) {}
        }).catch(() => {});
      }
    } catch (e) {
      // Best-effort -- if this fails, later playback just fails the
      // same way it always did on this browser, no worse off.
    }
  }
}

document.addEventListener("pointerdown", unlockAudioPlayback, { once: true });

function tryPlayNextAutomationChunk() {
  if (automationAudioEl.dataset.playing === "1") return;
  const idx = automationQueue.findIndex((c) => c.seq === automationNextSeq);
  if (idx === -1) return; // still waiting for this seq to arrive
  const chunk = automationQueue.splice(idx, 1)[0];
  automationNextSeq++;

  if (chunk.data == null) {
    tryPlayNextAutomationChunk();
    return;
  }

  const bytes = atob(chunk.data);
  const buffer = new Uint8Array(bytes.length);
  for (let i = 0; i < bytes.length; i++) buffer[i] = bytes.charCodeAt(i);
  const blob = new Blob([buffer], { type: chunk.mime || "audio/mpeg" });
  const url = URL.createObjectURL(blob);
  automationAudioEl.src = url;
  automationAudioEl.volume = 1.0;
  automationAudioEl.dataset.playing = "1";
  const finish = () => {
    URL.revokeObjectURL(url);
    automationAudioEl.dataset.playing = "0";
    tryPlayNextAutomationChunk();
  };
  automationAudioEl.play().catch(finish);
  automationAudioEl.onended = finish;
}

/* ---------------- Mode: Chat / Tutor ---------------- */
// Tutor mode is a completely separate mode from normal chat -- its own
// system prompt, its own memory, its own conversation on the backend
// (see backend/main.py's mode-aware _shared_clients) -- not just a
// different topic within the same chat. Switching tabs here tells the
// backend via {"type": "set_mode"}, which swaps which shared agent
// client this connection's messages go to and sends back that mode's
// own chat history (see socket.onmessage's "history"/"reset" handling
// below) so the visible chat log actually swaps over too, instead of
// mixing both modes' messages in one scrollback. (currentMode itself is
// declared up in the State / orb section -- setState()'s STATE_HUE
// lookup needs it before this section even runs.)

function updateModeUI() {
  for (const tab of modeTabs.querySelectorAll(".mode-tab")) {
    const active = tab.dataset.mode === currentMode;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", active ? "true" : "false");
  }
  document.body.classList.toggle("mode-tutor", currentMode === "tutor");
  if (avatar) avatar.setMode(currentMode);
  chatInput.placeholder = currentMode === "tutor" ? "Ask your tutor…" : "Talk to Neural…";
  if (blockAutomations) blockAutomations.hidden = currentMode === "tutor";
  if (blockKnowledgeBase) blockKnowledgeBase.hidden = currentMode !== "tutor";
  if (currentMode === "tutor") loadKnowledgeBase();
}

function switchMode(mode) {
  if (mode === currentMode) return;
  if (!socket || socket.readyState !== WebSocket.OPEN) return;
  // Same "barge-in" treatment as sending a new message -- don't let the
  // OLD mode's reply keep talking/streaming into the just-switched-to
  // chat log.
  bargeIn();
  currentMode = mode;
  updateModeUI();
  // Re-run setState under the NEW mode's palette (bargeIn() above just
  // set idle/ONLINE using the OLD mode's hues) and play the orb's
  // animated mode-switch flourish -- see style.css's .mode-switching.
  setState("idle", "ONLINE");
  playModeSwitchAnimation();
  socket.send(JSON.stringify({ type: "set_mode", mode }));
}

function playModeSwitchAnimation() {
  // Restart the animation even if a switch happens again before the
  // previous one finished -- remove the class, force a reflow, re-add.
  orb.classList.remove("mode-switching");
  void orb.offsetWidth;
  orb.classList.add("mode-switching");
  setTimeout(() => orb.classList.remove("mode-switching"), 900);
  if (avatar) avatar.flourish();
}

if (modeTabs) {
  modeTabs.addEventListener("click", (e) => {
    const btn = e.target.closest(".mode-tab");
    if (btn) switchMode(btn.dataset.mode);
  });
}
updateModeUI();

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
      // A mode switch sends its own history with reset:true -- swap the
      // visible chat log over entirely rather than appending, since this
      // is a genuinely different conversation (see switchMode above).
      if (data.reset) {
        chatLog.innerHTML = "";
        for (const m of data.messages || []) {
          if (m.role === "user" || m.role === "assistant") addMessage(m.role, m.text);
        }
        return;
      }
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
      return;
    }

    // Automation output (see backend/main.py's deliver_assistant_message)
    // rides its own event types and its own playback pipeline above --
    // deliberately outside the turn_id system below, since it isn't part
    // of any interactive turn and must never be dropped or confused with
    // one. Shown regardless of which mode (Chat/Tutor) is currently
    // open -- an automation firing (including one just started by a
    // trigger phrase typed/said while in Tutor mode, see main.py's
    // voice-trigger interception) should never be silently swallowed
    // just because the user happened to be looking at the other mode.
    // It's still NOT saved into Tutor mode's own persisted history
    // (automations are tagged mode="chat" server-side), so reopening
    // Tutor mode later won't show a stray automation message that
    // doesn't belong to that conversation -- this is just the live,
    // in-the-moment notice.
    if (data.type === "automation_message") {
      if (data.mood && avatar) avatar.setMood(data.mood);
      addMessage("assistant", data.text);
      return;
    }
    if (data.type === "automation_audio") {
      automationQueue.push({ seq: data.seq, mime: data.mime, data: data.data });
      tryPlayNextAutomationChunk();
      return;
    }
    if (data.type === "automation_audio_done") {
      return;
    }

    // Every other message type belongs to one turn -- drop anything left
    // over from a turn a barge-in already superseded (see sendMessage's
    // turn_id comment above).
    if (data.turn_id !== undefined && data.turn_id !== activeTurnId) return;

    if (data.type === "assistant") {
      if (data.mood && avatar) avatar.setMood(data.mood);
      for (const block of data.blocks) {
        if (block.kind === "text" && block.text) {
          if (!assistantDiv) assistantDiv = addMessage("assistant", "");
          setMessageText(assistantDiv, (assistantDiv._raw || "") + block.text);
          replyTextForEcho += block.text;
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

// Barge-in: a previous reply is still being generated or spoken, so stop
// it -- tell the backend to actually stop that turn (not just mute its
// audio locally), cut local playback immediately, and bump the turn
// counter so any leftover events the old (now-abandoned) turn is still
// sending arrive tagged with a stale turn_id and get dropped by
// socket.onmessage rather than resuming playback or bleeding into
// whatever comes next. Used both when a new message is actually ready to
// send (sendMessage below) and, for voice, the instant speech is
// DETECTED rather than once the whole interrupting question has finished
// (see startContinuousListening's interim-result handling) -- waiting
// for a full transcript first would mean Neural keeps talking for the
// entire length of your question before it even notices you spoke.
// Returns whether there was actually anything to interrupt.
function bargeIn() {
  if (!isSpeaking && !expectingAudio) return false;
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ type: "interrupt" }));
  }
  turnCounter += 1;
  activeTurnId = turnCounter;
  resetTurnAudio();
  isSpeaking = false;
  expectingAudio = false;
  assistantDiv = null;
  setState("idle", "ONLINE");
  return true;
}

function sendMessage(text, attachments) {
  attachments = attachments || [];
  if ((!text && !attachments.length) || !socket || socket.readyState !== WebSocket.OPEN) return false;

  bargeIn();
  turnCounter += 1;
  activeTurnId = turnCounter;

  addMessage("user", text, attachments.map((a) => a.dataUrl));
  assistantDiv = null;
  replyTextForEcho = "";
  isSpeaking = false;
  expectingAudio = true; // every reply now always talks -- see main.py
  resetTurnAudio();
  setState("thinking", "PROCESSING");
  const payload = { type: "message", text, turn_id: activeTurnId };
  if (attachments.length) {
    payload.attachments = attachments.map((a) => ({
      mime: a.mime,
      kind: a.kind,
      data: a.dataUrl.slice(a.dataUrl.indexOf(",") + 1),
    }));
  }
  socket.send(JSON.stringify(payload));
  return true;
}

chatForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = chatInput.value.trim();
  const atts = pendingAttachments.slice();
  if (!text && !atts.length) return;
  if (sendMessage(text, atts)) {
    chatInput.value = "";
    clearPendingAttachments();
  }
});

/* ---------------- Vision: show Neural an image ---------------- */
// Three ways in: pick/paste/drop an image, "Look at my screen" (a single
// frame from the browser's screen-share picker -- nothing keeps
// recording), or "Take a photo" (camera). Each image is downscaled here
// to at most MAX_IMAGE_EDGE px and re-encoded as JPEG before it's sent,
// so a 4K screenshot is a few hundred KB over the websocket, not 10MB.
// The backend saves it under media/uploads/ and asks Claude to look at it
// with its own Read tool -- which is how Claude Code already views images
// -- so this uses nothing beyond the existing Claude subscription.
const attachBtn = document.getElementById("attach-btn");
const attachMenu = document.getElementById("attach-menu");
const attachStrip = document.getElementById("attach-strip");
const attachFileInput = document.getElementById("attach-file");
const attachCameraInput = document.getElementById("attach-camera-input");
const attachScreenItem = document.getElementById("attach-screen-item");
const chatCol = document.getElementById("chat-col");
const cameraBackdrop = document.getElementById("camera-backdrop");
const cameraVideo = document.getElementById("camera-video");
const MAX_ATTACHMENTS = 4;
const MAX_IMAGE_EDGE = 1600;
let pendingAttachments = []; // {dataUrl, mime, kind}
let cameraStream = null;
let cameraFacing = "user";

const touchOnlyDevice = window.matchMedia
  && window.matchMedia("(pointer: coarse)").matches
  && !window.matchMedia("(any-pointer: fine)").matches;
if (attachScreenItem && (touchOnlyDevice || !(navigator.mediaDevices && navigator.mediaDevices.getDisplayMedia))) {
  // Phones/tablets don't offer screen capture to web pages at all.
  attachScreenItem.hidden = true;
}

function renderAttachStrip() {
  if (!attachStrip) return;
  attachStrip.innerHTML = "";
  attachStrip.hidden = pendingAttachments.length === 0;
  if (attachBtn) attachBtn.classList.toggle("has-items", pendingAttachments.length > 0);
  pendingAttachments.forEach((a, i) => {
    const thumb = document.createElement("div");
    thumb.className = "attach-thumb";
    const img = document.createElement("img");
    img.src = a.dataUrl;
    img.alt = "Attached image";
    const kind = document.createElement("span");
    kind.className = "attach-kind";
    kind.textContent = a.kind === "screen" ? "screen" : a.kind === "camera" ? "photo" : "";
    const rm = document.createElement("button");
    rm.type = "button";
    rm.textContent = "✕";
    rm.title = "Remove";
    rm.addEventListener("click", () => {
      pendingAttachments.splice(i, 1);
      renderAttachStrip();
    });
    thumb.append(img, kind, rm);
    attachStrip.appendChild(thumb);
  });
}

function clearPendingAttachments() {
  pendingAttachments = [];
  renderAttachStrip();
}

function addAttachment(dataUrl, kind) {
  if (pendingAttachments.length >= MAX_ATTACHMENTS) {
    addMessage("system", "That's the most images I can take in one message (" + MAX_ATTACHMENTS + ").");
    return;
  }
  pendingAttachments.push({ dataUrl, mime: "image/jpeg", kind });
  renderAttachStrip();
  if (avatar) avatar.reactTo("curious", 1.4);
  chatInput.focus();
}

// Draws any image source onto a canvas no larger than MAX_IMAGE_EDGE on
// its longest side and returns it as a JPEG data URL.
function canvasJpeg(source, w, h) {
  const scale = Math.min(1, MAX_IMAGE_EDGE / Math.max(w, h));
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(w * scale));
  canvas.height = Math.max(1, Math.round(h * scale));
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#000";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(source, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL("image/jpeg", 0.86);
}

function imageFileToJpeg(file) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      try {
        resolve(canvasJpeg(img, img.naturalWidth, img.naturalHeight));
      } catch (e) {
        reject(e);
      } finally {
        URL.revokeObjectURL(url);
      }
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("couldn't read that image"));
    };
    img.src = url;
  });
}

async function addImageFiles(files, kind) {
  for (const file of files) {
    if (!file || !file.type || !file.type.startsWith("image/")) continue;
    try {
      addAttachment(await imageFileToJpeg(file), kind || "file");
    } catch (e) {
      addMessage("system", "Couldn't attach " + (file.name || "that image") + ": " + e.message);
    }
  }
}

async function captureScreen() {
  let stream;
  try {
    stream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false });
  } catch (e) {
    if (e && e.name !== "NotAllowedError" && e.name !== "AbortError") {
      addMessage("system", "Screen capture isn't available here: " + e.message);
    }
    return;
  }
  try {
    const video = document.createElement("video");
    video.muted = true;
    video.playsInline = true;
    video.srcObject = stream;
    await video.play();
    // Give the first real frame a moment to arrive (some browsers hand
    // back a black frame if you draw immediately).
    await new Promise((r) => setTimeout(r, 400));
    addAttachment(canvasJpeg(video, video.videoWidth, video.videoHeight), "screen");
  } catch (e) {
    addMessage("system", "Couldn't grab the screen: " + e.message);
  } finally {
    stream.getTracks().forEach((t) => t.stop());
  }
}

async function openCamera() {
  // Phones get the native camera UI via the capture input; desktops get a
  // live preview with a Capture button.
  const coarse = window.matchMedia && window.matchMedia("(pointer: coarse)").matches;
  if (coarse || !(navigator.mediaDevices && navigator.mediaDevices.getUserMedia)) {
    attachCameraInput.click();
    return;
  }
  try {
    cameraStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: cameraFacing }, audio: false });
  } catch (e) {
    addMessage("system", "Camera unavailable: " + e.message);
    return;
  }
  cameraVideo.srcObject = cameraStream;
  cameraBackdrop.hidden = false;
}

function closeCamera() {
  if (cameraStream) cameraStream.getTracks().forEach((t) => t.stop());
  cameraStream = null;
  cameraVideo.srcObject = null;
  cameraBackdrop.hidden = true;
}

function setAttachMenu(open) {
  if (!attachMenu) return;
  attachMenu.hidden = !open;
  attachBtn.setAttribute("aria-expanded", open ? "true" : "false");
}

if (attachBtn && attachMenu) {
  attachBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    setAttachMenu(attachMenu.hidden);
  });
  attachMenu.addEventListener("click", (e) => {
    const item = e.target.closest("[data-attach]");
    if (!item) return;
    setAttachMenu(false);
    const what = item.dataset.attach;
    if (what === "file") attachFileInput.click();
    else if (what === "screen") captureScreen();
    else if (what === "camera") openCamera();
  });
  document.addEventListener("click", (e) => {
    if (!attachMenu.hidden && !e.target.closest(".attach-wrap")) setAttachMenu(false);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") setAttachMenu(false);
  });
}

if (attachFileInput) {
  attachFileInput.addEventListener("change", () => {
    addImageFiles([...attachFileInput.files], "file");
    attachFileInput.value = "";
  });
}
if (attachCameraInput) {
  attachCameraInput.addEventListener("change", () => {
    addImageFiles([...attachCameraInput.files], "camera");
    attachCameraInput.value = "";
  });
}

if (cameraBackdrop) {
  document.getElementById("camera-close").addEventListener("click", closeCamera);
  document.getElementById("camera-cancel").addEventListener("click", closeCamera);
  document.getElementById("camera-capture").addEventListener("click", () => {
    if (cameraVideo.videoWidth) addAttachment(canvasJpeg(cameraVideo, cameraVideo.videoWidth, cameraVideo.videoHeight), "camera");
    closeCamera();
  });
  document.getElementById("camera-flip").addEventListener("click", async () => {
    cameraFacing = cameraFacing === "user" ? "environment" : "user";
    closeCamera();
    await openCamera();
  });
}

// Paste an image anywhere (screenshot tool -> Ctrl+V).
document.addEventListener("paste", (e) => {
  const files = [...((e.clipboardData && e.clipboardData.files) || [])].filter((f) => f.type.startsWith("image/"));
  if (!files.length) return;
  e.preventDefault();
  addImageFiles(files, "file");
});

// Drag & drop onto the chat column.
if (chatCol) {
  let dragDepth = 0;
  const hasFiles = (e) => e.dataTransfer && [...e.dataTransfer.types].includes("Files");
  chatCol.addEventListener("dragenter", (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    dragDepth++;
    chatCol.classList.add("dragging");
  });
  chatCol.addEventListener("dragover", (e) => { if (hasFiles(e)) e.preventDefault(); });
  chatCol.addEventListener("dragleave", () => {
    dragDepth = Math.max(0, dragDepth - 1);
    if (!dragDepth) chatCol.classList.remove("dragging");
  });
  chatCol.addEventListener("drop", (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    dragDepth = 0;
    chatCol.classList.remove("dragging");
    addImageFiles([...e.dataTransfer.files], "file");
  });
}

/* ---------------- Avatar: what she's doing right now ---------------- */
// Turns the live tool-activity feed into the little status chip above the
// avatar ("Browsing the web", "Looking at your image", ...).
let avatarActivityTimer = null;

function avatarActivityFor(event) {
  const tool = event.tool || "";
  const input = event.input || {};
  if (tool === "Bash") return ["Running a command", "code"];
  if (tool.includes("media_analysis")) return ["Watching the video", "video"];
  if (tool === "Read") {
    const readPath = String(input.file_path || "");
    if (/media[\\/]+analysis[\\/]/i.test(readPath)) return ["Watching the video", "eye"];
    return /media[\\/]+uploads[\\/]/i.test(readPath)
      ? ["Looking at your image", "eye"]
      : ["Reading a file", "file"];
  }
  if (tool === "Write" || tool === "Edit" || tool === "MultiEdit" || tool === "NotebookEdit") return ["Editing a file", "file"];
  if (tool === "Glob" || tool === "Grep") return ["Searching files", "search"];
  if (tool === "WebSearch") return ["Searching the web", "search"];
  if (tool === "WebFetch" || tool.includes("browser")) return ["Browsing the web", "web"];
  if (tool.includes("gmail")) return ["Checking email", "mail"];
  if (tool.includes("calendar")) return ["Checking your calendar", "mail"];
  if (tool.includes("memory") || tool.includes("crm")) return ["Remembering", "memory"];
  if (tool.includes("video") || tool.includes("__cap__")) return ["Working on video", "video"];
  if (tool.includes("stock")) return ["Finding footage", "video"];
  if (tool.includes("jobs")) return ["Searching jobs", "search"];
  if (tool.includes("buffer")) return ["Preparing a post", "web"];
  if (tool.includes("tutor_kb") || tool.includes("tutor_docs")) return ["Checking references", "file"];
  if (tool.includes("automation")) return ["Checking automations", "gear"];
  return ["Working on it", "gear"];
}

function updateAvatarActivity(event) {
  if (!avatar || event.type !== "tool_use") return;
  if (event.source && event.source !== "chat") return;
  const [label, kind] = avatarActivityFor(event);
  avatar.setActivity(label, kind);
  clearTimeout(avatarActivityTimer);
  avatarActivityTimer = setTimeout(() => { avatar.setActivity(null); refreshIdleChip(); }, 12000);
}

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
  // A spoken question goes out with whatever image is waiting in the
  // composer -- attach a screenshot, then just ask "what's this?".
  if (sendMessage(text, pendingAttachments.slice())) clearPendingAttachments();
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
// True when a transcript is most likely Neural's own voice picked up by the
// mic (speakers -> mic echo), not the user. Without this, a loud speaker
// could make her interrupt herself, or even "hear" her own sentence as a
// new command and answer it.
function looksLikeOwnEcho(transcript) {
  if (!replyTextForEcho) return false;
  if (!isSpeaking && Date.now() - lastSpeechEndedAt > 1500) return false;
  const words = (transcript.toLowerCase().match(/[a-z0-9']+/g) || []).filter((w) => w.length > 2);
  if (words.length < 2) return false;
  const said = new Set(replyTextForEcho.toLowerCase().match(/[a-z0-9']+/g) || []);
  const overlap = words.filter((w) => said.has(w)).length / words.length;
  return overlap >= 0.7;
}

function handleWakeTranscript(transcript) {
  const trimmed = transcript.trim();
  if (!trimmed) return;
  if (looksLikeOwnEcho(trimmed)) return;
  const lower = trimmed.toLowerCase();
  const idx = lower.indexOf(wakePhrase);
  const command = idx !== -1 ? trimmed.slice(idx + wakePhrase.length).trim() : trimmed;
  if (!command) return; // just the wake phrase alone, nothing to act on yet
  sendVoiceCommand(command);
}

function startContinuousListening() {
  if (!SR || continuousRecognition) return;
  const rec = new SR();
  rec.continuous = true;
  // Interim (not-yet-final) results, on purpose: with interimResults
  // false, the browser only tells us anything once the WHOLE
  // interrupting question has finished and gone quiet, which means
  // Neural keeps right on talking for the full length of your question
  // before it even notices you spoke -- the "keeps speaking until it's
  // done" complaint. Watching interim results instead means we can cut
  // Neural off the moment speech is actually detected, and then just
  // keep accumulating that same utterance until it finalizes.
  rec.interimResults = true;
  rec.lang = "en-US";
  let bargedInThisUtterance = false;
  rec.onresult = (event) => {
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const result = event.results[i];
      const transcript = result[0].transcript.trim();
      // Cut her off the instant you start talking -- but only while she's
      // actually SPEAKING, and only if it isn't her own voice echoing back.
      // While she's merely thinking/working, a stray noise shouldn't cancel
      // the work; your finished sentence still interrupts it (sendMessage
      // supersedes the old turn) once the transcript is final.
      if (
        !bargedInThisUtterance && isSpeaking &&
        (transcript.split(/\s+/).length >= 2 || transcript.length >= 6) &&
        !looksLikeOwnEcho(transcript)
      ) {
        bargedInThisUtterance = true;
        bargeIn();
        setState("listening", "LISTENING…");
      }
      if (!result.isFinal) continue;
      bargedInThisUtterance = false;
      handleWakeTranscript(transcript);
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
    if (wakeWordOn) startContinuousListening();
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
  // Pressing the mic button is an unambiguous "let me talk" signal: stop
  // her VOICE immediately. Work she's in the middle of is only superseded
  // once you've actually said something (sendMessage handles that), so a
  // mis-tap doesn't throw away a half-done task.
  if (isSpeaking) bargeIn();
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
  if (event.type === "background_job") return { cls: "tag-automation", tag: "JOB" };
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
  else if (event.type === "automation_run" || event.type === "background_job") detail = event.name + " — " + event.status;
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
    updateAvatarActivity(data);
    if (data.type === "background_job") {
      if (data.status === "started") backgroundJobs.set(data.id, data.name);
      else backgroundJobs.delete(data.id);
      if (avatar && uiState === "idle") {
        avatar.setActivity(null);
        refreshIdleChip();
      }
    }
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
  const voiceTrigger = document.getElementById("auto-trigger").value.trim();
  const cron = currentCron();
  if (!name || !prompt || !cron) return;
  await fetch("/api/automations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, cron, prompt, enabled: true, voice_trigger: voiceTrigger || null }),
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
      (a.voice_trigger ? '<div class="automation-trigger">trigger: “' + escapeHtml(a.voice_trigger) + '”</div>' : "") +
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

/* ---------------- Knowledge Base (Tutor mode) ---------------- */
// Layer 1 of Tutor mode's three-layer design (see backend/tutor.py) --
// point it at a book/PDF already on this machine and it's chunked and
// embedded locally (backend/knowledge_base.py) for the tutor to search
// before answering. Mirrors the Automations panel's own modal pattern
// above. Only loaded/shown while Tutor mode is active (see
// updateModeUI); this can otherwise sit idle since it's a manual, no-
// polling panel (unlike automations, nothing here changes on its own).

function renderKnowledgeBase(items) {
  if (!items.length) {
    kbList.innerHTML = '<div class="empty-note">No books or PDFs added yet.</div>';
    return;
  }
  kbList.innerHTML = "";
  for (const s of items) {
    const row = document.createElement("div");
    row.className = "automation-row";
    const added = s.added_at ? new Date(s.added_at).toLocaleDateString([], { month: "short", day: "numeric" }) : "—";
    row.innerHTML =
      '<div class="automation-row-top">' +
      "<div>" +
      '<div class="automation-name">' + escapeHtml(s.title) + "</div>" +
      '<div class="automation-schedule">' + s.chunk_count + " chunks · added " + escapeHtml(added) + "</div>" +
      "</div>" +
      '<div class="automation-actions">' +
      '<button class="icon-btn" data-action="delete" title="Remove">✕</button>' +
      "</div></div>";

    row.querySelector('[data-action="delete"]').addEventListener("click", () =>
      fetch("/api/tutor/knowledge-base/" + s.id, { method: "DELETE" }).then(loadKnowledgeBase)
    );

    kbList.appendChild(row);
  }
}

async function loadKnowledgeBase() {
  try {
    const res = await fetch("/api/tutor/knowledge-base");
    renderKnowledgeBase(await res.json());
  } catch (e) {
    // server not ready; the panel just stays as it was
  }
}

function openKbModal() {
  kbModalBackdrop.hidden = false;
}
function closeKbModal() {
  kbModalBackdrop.hidden = true;
  kbForm.reset();
  kbSubmitBtn.disabled = false;
  kbSubmitBtn.textContent = "Ingest";
}
if (kbOpenModalBtn) {
  kbOpenModalBtn.addEventListener("click", openKbModal);
  kbModalClose.addEventListener("click", closeKbModal);
  kbCancelBtn.addEventListener("click", closeKbModal);
  kbModalBackdrop.addEventListener("click", (e) => { if (e.target === kbModalBackdrop) closeKbModal(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !kbModalBackdrop.hidden) closeKbModal(); });

  kbForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const path = kbPathInput.value.trim();
    if (!path) return;
    kbSubmitBtn.disabled = true;
    kbSubmitBtn.textContent = "Ingesting…";
    try {
      const res = await fetch("/api/tutor/knowledge-base/ingest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        addMessage("system", "Couldn't add that to the knowledge base: " + (err.detail || res.statusText));
        kbSubmitBtn.disabled = false;
        kbSubmitBtn.textContent = "Ingest";
        return;
      }
      closeKbModal();
      loadKnowledgeBase();
    } catch (e) {
      addMessage("system", "Couldn't reach the server to ingest that path.");
      kbSubmitBtn.disabled = false;
      kbSubmitBtn.textContent = "Ingest";
    }
  });
}

/* ---------------- Mobile side drawer ---------------- */

function openPanel() {
  setOpsOpen(false);
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

/* ---------------- Operations slide-out ---------------- */

function setOpsOpen(open) {
  if (!opsPanel) return;
  opsPanel.classList.toggle("open", open);
  opsBackdrop.classList.toggle("show", open);
  opsPanel.setAttribute("aria-hidden", String(!open));
  opsToggle.setAttribute("aria-expanded", String(open));
}

if (opsToggle) {
  opsToggle.addEventListener("click", () => {
    const isOpen = opsPanel.classList.contains("open");
    if (!isOpen) closePanel();
    setOpsOpen(!isOpen);
  });
  opsClose.addEventListener("click", () => setOpsOpen(false));
  opsBackdrop.addEventListener("click", () => setOpsOpen(false));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && opsPanel.classList.contains("open")) setOpsOpen(false);
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
