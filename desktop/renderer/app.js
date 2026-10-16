// Neural Desktop -- the page inside the floating window.
//
// Talks to the Neural backend over the same /ws socket as the web app
// (one shared conversation), plays Neural's voice with lip-sync, captures
// your voice for local Whisper, handles quick local answers, and keeps
// the window click-through everywhere except on her, the text box and
// the reply bubble.
(function () {
  "use strict";

  const N = window.neural;
  const { renderMarkdown, escapeHtml } = window.NeuralMarkdown;
  const $ = (id) => document.getElementById(id);

  const el = {
    bubble: $("bubble"), bubbleUser: $("bubbleUser"), bubbleBody: $("bubbleBody"), bubbleTitle: $("bubbleTitle"),
    fileList: $("fileList"), pills: $("pills"), status: $("status"), connDot: $("connDot"), pending: $("pending"),
    input: $("input"), inputRow: $("inputRow"), mic: $("micBtn"), handle: $("handle"), grip: $("resizeGrip"),
    avatarBox: $("avatarBox"), tts: $("tts"),
  };

  const avatar = new window.NeuralAvatar(el.avatarBox);

  const HUES = { idle: 190, listening: 38, thinking: 275, speaking: 172, offline: 205 };
  const NEURAL_WORD = /^(neural|nural|neuro|neurol|newral|noral|neurals?|nerual|neura)$/;
  const SCREEN_RE = /\b(my screen|on (the )?screen|this (window|page|screen|error|code|video|tab|site|website|app|chart|graph|image|picture|photo|email|doc|document|slide|sheet|table|message|post)|what am i (looking at|seeing)|see (this|my screen)|look at (this|my screen))\b/i;
  const CLIP_RE = /\b(clipboard|what i (just )?copied|i('ve| have)? (just )?copied|the copied (text|thing|code))\b/i;

  const S = {
    settings: { speakReplies: true, followCursor: true, alwaysListening: false, pauseOnSystemAudio: true, wakeWords: ["neural"], followUpSeconds: 10, contextInMessages: true, autoAttach: true, suggestions: true },
    layout: null,
    context: null,
    socket: null,
    connected: false,
    everConnected: false,
    backoff: 800,
    reconnectTimer: null,
    turnCounter: 0,
    activeTurnId: null,
    isSpeaking: false,
    expectingAudio: false,
    chunkPlaying: false,
    turn: { queue: [], next: 1, total: null, played: 0 },
    replyRaw: "",
    replyForEcho: "",
    lastSpeechEndedAt: 0,
    uiState: "offline",
    queue: loadQueue(),
    history: [],
    pending: { attachments: [], clipboardText: "", label: "" },
    bubbleTimer: null,
    bubbleHover: false,
    bubblePinned: false,
    bubbleMode: "reply",       // reply | history | local
    hovering: false,
    voiceAvailable: null,      // null unknown, false = backend lacks Whisper
    voiceHint: "",
    sysAudioActive: false,
    activityTimer: null,
    automationQueue: [],
    automationPlaying: false,
    ducked: false,
    micDownAt: 0,
  };

  // ------------------------------------------------------------ small utils

  function store(key, value) {
    try {
      if (value === undefined) return JSON.parse(localStorage.getItem(key) || "null");
      localStorage.setItem(key, JSON.stringify(value));
    } catch (e) {
      return null;
    }
    return value;
  }

  function loadQueue() {
    const q = store("neural.queue");
    return Array.isArray(q) ? q : [];
  }

  function saveQueue() {
    store("neural.queue", S.queue);
  }

  function setUi(state, label) {
    S.uiState = state;
    avatar.setState(state);
    document.documentElement.style.setProperty("--state-hue", HUES[state] || 190);
    el.mic.classList.toggle("rec", state === "listening");
    if (label !== undefined) setStatus(label);
    else refreshStatus();
  }

  function setStatus(text) {
    el.status.textContent = text || "";
  }

  function refreshStatus() {
    if (!S.connected) {
      setStatus(S.queue.length ? `Offline -- ${S.queue.length} waiting to send` : S.everConnected ? "Reconnecting..." : "Connecting to Neural...");
      return;
    }
    if (S.uiState === "thinking") return setStatus("Thinking...");
    if (S.uiState === "speaking") return setStatus("");
    if (S.uiState === "listening") return setStatus("Listening...");
    if (S.settings.alwaysListening) {
      if (S.sysAudioActive) return setStatus("Paused while your PC plays audio");
      return setStatus('Say "Neural, ..." to talk');
    }
    setStatus("");
  }

  // ------------------------------------------------------------ click-through hit testing

  let ignoring = true;
  function setIgnore(v) {
    if (v === ignoring) return;
    ignoring = v;
    N.setIgnoreMouse(v);
  }

  function isHit(target) {
    if (!target || target === document.documentElement || target === document.body) return false;
    if (target.closest && target.closest(".hit")) return true;
    const svg = el.avatarBox.querySelector("svg");
    return !!(svg && target !== svg && svg.contains(target) && !target.closest(".nv-fx"));
  }

  document.addEventListener("mousemove", (e) => {
    const t = document.elementFromPoint(e.clientX, e.clientY);
    const hit = isHit(t);
    setIgnore(!hit && !document.body.classList.contains("dragging"));
    const overAvatarCol = !!(t && t.closest && t.closest("#avatarCol")) && (hit || t.closest("#avatarBox"));
    setHover(hit || !!overAvatarCol);
  });
  document.documentElement.addEventListener("mouseleave", () => {
    setHover(false);
    setIgnore(true);
  });

  let hoverTimer = null;
  function setHover(on) {
    if (on) {
      clearTimeout(hoverTimer);
      if (!S.hovering) {
        S.hovering = true;
        document.body.classList.add("hover");
        renderPills();
      }
    } else if (S.hovering && !hoverTimer) {
      hoverTimer = setTimeout(() => {
        hoverTimer = null;
        if (document.activeElement === el.input) return;
        S.hovering = false;
        document.body.classList.remove("hover");
        renderPills();
      }, 900);
    }
  }

  // ------------------------------------------------------------ drag, resize, buttons, menu

  function dragWith(handleEl, startFn, endFn) {
    handleEl.addEventListener("pointerdown", (e) => {
      if (e.button !== 0 || e.target.closest("button")) return;
      e.preventDefault();
      handleEl.setPointerCapture(e.pointerId);
      document.body.classList.add("dragging");
      setIgnore(false);
      startFn();
      const up = () => {
        handleEl.removeEventListener("pointerup", up);
        handleEl.removeEventListener("pointercancel", up);
        document.body.classList.remove("dragging");
        endFn();
      };
      handleEl.addEventListener("pointerup", up);
      handleEl.addEventListener("pointercancel", up);
    });
  }
  dragWith(el.handle, N.dragStart, N.dragEnd);
  dragWith(el.grip, N.resizeStart, N.resizeEnd);

  $("btnHide").addEventListener("click", () => N.hide());
  $("btnQuit").addEventListener("click", () => N.quit());
  $("btnMenu").addEventListener("click", () => N.popupMenu());
  el.avatarBox.addEventListener("contextmenu", (e) => {
    e.preventDefault();
    N.popupMenu();
  });
  el.handle.addEventListener("contextmenu", (e) => {
    e.preventDefault();
    N.popupMenu();
  });

  // ------------------------------------------------------------ bubble

  const bubble = {
    show() {
      clearTimeout(S.bubbleTimer);
      el.bubble.classList.remove("hidden");
      renderPills();
    },
    hide() {
      clearTimeout(S.bubbleTimer);
      el.bubble.classList.add("hidden");
      S.bubblePinned = false;
      renderPills();
    },
    visible() {
      return !el.bubble.classList.contains("hidden");
    },
    user(text, notes) {
      S.bubbleMode = "reply";
      el.bubbleTitle.textContent = "Neural";
      const parts = [];
      if (text) parts.push(escapeHtml(text.length > 220 ? text.slice(0, 220) + "..." : text));
      for (const n of notes || []) parts.push(`<span class="note">[${escapeHtml(n)}]</span>`);
      el.bubbleUser.innerHTML = parts.join(" ");
      el.bubbleUser.hidden = !parts.length;
    },
    startReply() {
      S.replyRaw = "";
      el.bubbleBody.innerHTML = "";
      el.fileList.hidden = true;
      el.fileList.innerHTML = "";
      this.show();
    },
    append(text) {
      S.replyRaw += text;
      const nearBottom = el.bubbleBody.scrollHeight - el.bubbleBody.scrollTop - el.bubbleBody.clientHeight < 40;
      el.bubbleBody.innerHTML = renderMarkdown(S.replyRaw);
      if (nearBottom) el.bubbleBody.scrollTop = el.bubbleBody.scrollHeight;
    },
    set(text, opts) {
      opts = opts || {};
      S.replyRaw = text || "";
      el.bubbleBody.innerHTML = opts.html || renderMarkdown(S.replyRaw);
      renderFiles(opts.files);
      this.show();
      if (!opts.keep) this.done(opts.holdMs);
    },
    // Reply finished (text and speech): fade out after a reading-time pause.
    done(holdMs) {
      clearTimeout(S.bubbleTimer);
      if (S.bubblePinned || S.bubbleMode === "history") return;
      const ms = holdMs || Math.min(45000, 7000 + S.replyRaw.length * 45);
      S.bubbleTimer = setTimeout(() => {
        if (S.bubbleHover || S.isSpeaking || S.expectingAudio) return bubble.done(4000);
        bubble.hide();
      }, ms);
    },
  };

  el.bubble.addEventListener("mouseenter", () => {
    S.bubbleHover = true;
  });
  el.bubble.addEventListener("mouseleave", () => {
    S.bubbleHover = false;
  });
  el.bubble.addEventListener("pointerdown", () => {
    S.bubblePinned = true; // touching it means you're reading it
    clearTimeout(S.bubbleTimer);
  });
  el.bubbleBody.addEventListener("click", (e) => {
    const a = e.target.closest("a");
    if (a) {
      e.preventDefault();
      N.openExternal(a.getAttribute("href"));
    }
  });
  $("bubbleClose").addEventListener("click", () => bubble.hide());
  $("bubbleCopy").addEventListener("click", () => {
    N.writeClipboard(S.bubbleMode === "history" ? el.bubbleBody.innerText : S.replyRaw);
    flashStatus("Copied");
  });
  $("bubbleHistory").addEventListener("click", () => (S.bubbleMode === "history" ? bubble.hide() : showHistory()));

  function flashStatus(text) {
    setStatus(text);
    setTimeout(refreshStatus, 1600);
  }

  function renderFiles(files) {
    el.fileList.innerHTML = "";
    if (!files || !files.length) {
      el.fileList.hidden = true;
      return;
    }
    for (const f of files) {
      const row = document.createElement("div");
      row.className = "file";
      row.title = f.path;
      row.innerHTML =
        '<div class="meta"><div class="name"></div><div class="folder"></div></div>' +
        '<button type="button" title="Show in folder"><svg viewBox="0 0 24 24"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg></button>';
      row.querySelector(".name").textContent = f.name;
      row.querySelector(".folder").textContent = f.folder;
      row.addEventListener("click", (e) => {
        if (e.target.closest("button")) N.revealFile(f.path);
        else N.openFile(f.path);
      });
      el.fileList.appendChild(row);
    }
    el.fileList.hidden = false;
  }

  function showHistory() {
    S.bubbleMode = "history";
    el.bubbleTitle.textContent = "History";
    el.bubbleUser.hidden = true;
    el.fileList.hidden = true;
    const items = S.history.slice(-40);
    el.bubbleBody.innerHTML = items.length
      ? items
          .map(
            (m) =>
              `<div class="hist-msg ${m.role}"><div class="who">${m.role === "user" ? "You" : "Neural"}</div><div class="body">${
                m.role === "user" ? escapeHtml(m.text) : renderMarkdown(m.text)
              }</div></div>`
          )
          .join("")
      : '<p class="hint">No messages yet.</p>';
    bubble.show();
    el.bubbleBody.scrollTop = el.bubbleBody.scrollHeight;
  }

  function addHistory(role, text) {
    if (!text) return;
    S.history.push({ role, text });
    if (S.history.length > 200) S.history.splice(0, S.history.length - 200);
  }

  // ------------------------------------------------------------ suggestion pills

  function renderPills() {
    const ctx = S.context;
    const show = S.settings.suggestions && ctx && ctx.suggestions && ctx.suggestions.length &&
      (S.hovering || document.activeElement === el.input) && !bubble.visible();
    if (!show) {
      el.pills.hidden = true;
      return;
    }
    el.pills.innerHTML = "";
    for (const item of ctx.suggestions.slice(0, 3)) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "pill hit";
      b.textContent = item.label;
      b.title = `About ${ctx.app}`;
      b.addEventListener("click", () => runSuggestion(item));
      el.pills.appendChild(b);
    }
    el.pills.hidden = false;
  }

  async function runSuggestion(item) {
    if (item.action === "prefill") return prefill(item.text);
    const opts = { display: item.label };
    if (item.attach === "window") {
      const shot = await N.captureWindow();
      if (shot.ok) opts.attachments = [shotAttachment(shot)];
    }
    sendToNeural(item.prompt, opts);
  }

  function shotAttachment(shot) {
    return { mime: shot.mime, data: shot.data, kind: "screen", preview: shot.preview, label: `screenshot of ${shot.app}` };
  }

  // ------------------------------------------------------------ pending attachments (screenshot / clipboard chips)

  function renderPending() {
    const p = S.pending;
    el.pending.innerHTML = "";
    const items = p.attachments.map((a, i) => ({ label: a.label, img: a.preview, remove: () => p.attachments.splice(i, 1) }));
    if (p.clipboardText) items.push({ label: "clipboard text", remove: () => (p.clipboardText = "") });
    for (const it of items) {
      const chip = document.createElement("div");
      chip.className = "chip";
      chip.innerHTML = (it.img ? `<img alt="" src="${it.img}">` : "") + "<span></span>" +
        '<button type="button" title="Remove"><svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg></button>';
      chip.querySelector("span").textContent = it.label;
      chip.querySelector("button").addEventListener("click", () => {
        it.remove();
        renderPending();
      });
      el.pending.appendChild(chip);
    }
    el.pending.hidden = !items.length;
    el.input.placeholder = items.length ? "Ask about it (or just press Enter)..." : "Talk to Neural...";
  }

  function takePending() {
    const p = S.pending;
    const out = { attachments: p.attachments.slice(), clipboardText: p.clipboardText };
    S.pending = { attachments: [], clipboardText: "", label: "" };
    renderPending();
    return out;
  }

  function prefill(text) {
    N.focus();
    el.input.value = text || "";
    el.input.focus();
    el.input.setSelectionRange(el.input.value.length, el.input.value.length);
  }

  async function askAboutWindow() {
    const shot = await N.captureWindow();
    if (!shot.ok) {
      bubble.user("");
      bubble.set("", { html: `<p class="err">Couldn't capture the window: ${escapeHtml(shot.error || "unknown error")}</p>` });
      return;
    }
    S.pending.attachments = [shotAttachment(shot)];
    renderPending();
    prefill("");
    flashStatus(`Got ${shot.app} -- ask away`);
  }

  async function attachClipboard() {
    const clip = await N.readClipboard();
    if (clip.kind === "empty") {
      bubble.user("");
      bubble.set("Your clipboard is empty.");
      return;
    }
    if (clip.kind === "text") S.pending.clipboardText = clip.text;
    else S.pending.attachments.push({ mime: clip.mime, data: clip.data, kind: "file", preview: clip.preview, label: "clipboard image" });
    renderPending();
    prefill("");
  }

  async function copyWindowText() {
    setStatus("Reading the window...");
    const r = await N.ocrWindow();
    refreshStatus();
    bubble.user("Copy text from the active window");
    if (!r.ok) {
      bubble.set("", { html: `<p class="err">${escapeHtml(r.error || "Couldn't read that window.")}</p>` });
      return;
    }
    if (!r.text) {
      bubble.set(`I couldn't find any text in ${r.app || "that window"}.`);
      return;
    }
    const lines = r.text.split("\n").length;
    bubble.set("", {
      html: `<p>Copied ${lines} line${lines === 1 ? "" : "s"} from ${escapeHtml(r.app || "the window")} to your clipboard.</p><div class="copybox">${escapeHtml(r.text.slice(0, 2000))}</div>`,
    });
    S.replyRaw = r.text;
    speakLocal("Copied it to your clipboard.");
  }

  // ------------------------------------------------------------ audio playback (queue + lip-sync)

  let audioCtx = null;
  let duckGain = null;
  function ensureAudioGraph() {
    try {
      if (!audioCtx) audioCtx = new AudioContext();
      if (audioCtx.state === "suspended") audioCtx.resume().catch(() => {});
      if (!duckGain) {
        const source = audioCtx.createMediaElementSource(el.tts);
        duckGain = audioCtx.createGain();
        source.connect(duckGain);
        duckGain.connect(audioCtx.destination);
        const analyser = audioCtx.createAnalyser();
        analyser.fftSize = 1024;
        analyser.smoothingTimeConstant = 0.2;
        const mute = audioCtx.createGain();
        mute.gain.value = 0;
        source.connect(analyser);
        analyser.connect(mute);
        mute.connect(audioCtx.destination);
        avatar.setAnalyser(analyser);
      }
    } catch (e) {
      // playback still works without the graph, just without lip-sync levels
    }
  }

  function duck(on) {
    S.ducked = on;
    if (duckGain && audioCtx) duckGain.gain.setTargetAtTime(on ? 0.22 : 1, audioCtx.currentTime, 0.05);
  }

  function resetTurnAudio() {
    try { el.tts.pause(); } catch (e) {}
    S.chunkPlaying = false;
    S.turn = { queue: [], next: 1, total: null, played: 0 };
    duck(false);
  }

  function playClip(b64, mime, done) {
    const bytes = atob(b64);
    const buf = new Uint8Array(bytes.length);
    for (let i = 0; i < bytes.length; i++) buf[i] = bytes.charCodeAt(i);
    const url = URL.createObjectURL(new Blob([buf], { type: mime || "audio/mpeg" }));
    ensureAudioGraph();
    el.tts.src = url;
    let finished = false;
    const finish = () => {
      if (finished) return;
      finished = true;
      URL.revokeObjectURL(url);
      done();
    };
    el.tts.onended = finish;
    el.tts.onerror = finish;
    el.tts.play().catch(finish);
  }

  function finishSpeakingIfDone() {
    if (S.turn.total !== null && S.turn.played >= S.turn.total) {
      if (S.isSpeaking) S.lastSpeechEndedAt = Date.now();
      S.isSpeaking = false;
      S.expectingAudio = false;
      duck(false);
      if (S.uiState !== "listening") setUi(S.connected ? "idle" : "offline");
      bubble.done();
      return true;
    }
    return false;
  }

  function playNext() {
    if (S.chunkPlaying) return;
    const i = S.turn.queue.findIndex((c) => c.seq === S.turn.next);
    if (i === -1) return;
    const chunk = S.turn.queue.splice(i, 1)[0];
    S.turn.next++;
    if (chunk.data == null || !S.settings.speakReplies) {
      S.turn.played++;
      if (!finishSpeakingIfDone()) playNext();
      return;
    }
    if (!S.isSpeaking) {
      S.isSpeaking = true;
      if (S.uiState !== "listening") setUi("speaking");
    }
    S.chunkPlaying = true;
    const turnAtStart = S.activeTurnId;
    playClip(chunk.data, chunk.mime, () => {
      if (turnAtStart !== S.activeTurnId) return; // barged in meanwhile
      S.chunkPlaying = false;
      S.turn.played++;
      if (!finishSpeakingIfDone()) playNext();
    });
  }

  function onAudioChunk(seq, mime, data) {
    S.turn.queue.push({ seq, mime, data });
    playNext();
  }

  function onAudioDone(count) {
    S.turn.total = count;
    finishSpeakingIfDone();
  }

  // Automation results (scheduled tasks) ride their own little queue.
  function playAutomation() {
    if (S.automationPlaying || S.isSpeaking || !S.automationQueue.length) return;
    S.automationQueue.sort((a, b) => a.seq - b.seq);
    const c = S.automationQueue.shift();
    if (!c.data || !S.settings.speakReplies) return playAutomation();
    S.automationPlaying = true;
    setUi("speaking");
    playClip(c.data, c.mime, () => {
      S.automationPlaying = false;
      if (!S.automationQueue.length) setUi(S.connected ? "idle" : "offline");
      playAutomation();
    });
  }

  // Speak a local answer (time, "opening Spotify", reminders) in Neural's
  // voice via the backend's TTS -- no model turn.
  async function speakLocal(text) {
    if (!S.settings.speakReplies || !text) return;
    S.turnCounter++;
    const myTurn = (S.activeTurnId = S.turnCounter);
    resetTurnAudio();
    S.expectingAudio = true;
    const r = await N.speak(text).catch(() => ({ ok: false }));
    if (myTurn !== S.activeTurnId) return;
    if (!r.ok) {
      S.expectingAudio = false;
      return;
    }
    onAudioChunk(1, r.mime, r.data);
    onAudioDone(1);
  }

  function bargeIn() {
    if (!S.isSpeaking && !S.expectingAudio && !S.automationPlaying) return false;
    if (S.connected) {
      try { S.socket.send(JSON.stringify({ type: "interrupt" })); } catch (e) {}
    }
    S.turnCounter++;
    S.activeTurnId = S.turnCounter;
    resetTurnAudio();
    S.automationQueue = [];
    S.automationPlaying = false;
    S.isSpeaking = false;
    S.expectingAudio = false;
    if (S.uiState !== "listening") setUi(S.connected ? "idle" : "offline");
    return true;
  }

  // ------------------------------------------------------------ connection

  async function connect() {
    clearTimeout(S.reconnectTimer);
    let info;
    try {
      info = await N.backendInfo();
    } catch (e) {
      return scheduleReconnect();
    }
    let ws;
    try {
      ws = new WebSocket(info.wsUrl);
    } catch (e) {
      return scheduleReconnect();
    }
    S.socket = ws;
    el.connDot.className = "dot connecting";
    ws.onopen = () => {
      if (S.socket !== ws) return;
      S.connected = true;
      S.backoff = 800;
      el.connDot.className = "dot online";
      N.reportStatus("online");
      if (S.uiState === "offline") setUi("idle");
      refreshStatus();
      if (!S.everConnected) {
        S.everConnected = true;
        firstRunHello();
        setTimeout(warmVoice, 4000);
      }
      flushQueue();
    };
    ws.onclose = () => {
      if (S.socket !== ws) return;
      const wasConnected = S.connected;
      S.connected = false;
      S.socket = null;
      el.connDot.className = "dot offline";
      N.reportStatus("offline");
      if (S.expectingAudio || S.uiState === "thinking") {
        resetTurnAudio();
        S.expectingAudio = false;
        S.isSpeaking = false;
        if (wasConnected && bubble.visible()) bubble.append("\n\n*(lost the connection to Neural)*");
      }
      setUi("offline");
      scheduleReconnect();
    };
    ws.onerror = () => {};
    ws.onmessage = (ev) => {
      let data;
      try {
        data = JSON.parse(ev.data);
      } catch (e) {
        return;
      }
      onSocketMessage(data);
    };
  }

  function scheduleReconnect() {
    clearTimeout(S.reconnectTimer);
    S.reconnectTimer = setTimeout(connect, S.backoff);
    S.backoff = Math.min(Math.round(S.backoff * 1.6), 15000);
    refreshStatus();
  }

  function onSocketMessage(data) {
    if (data.type === "history") {
      if (data.mode && data.mode !== "chat") return;
      S.history = (data.messages || []).filter((m) => m.role === "user" || m.role === "assistant").map((m) => ({ role: m.role, text: m.text }));
      return;
    }
    if (data.type === "automation_message") {
      if (data.mood) avatar.setMood(data.mood);
      bubble.user("");
      el.bubbleTitle.textContent = "Neural -- automation";
      bubble.set(data.text || "", { holdMs: 30000 });
      addHistory("assistant", data.text);
      return;
    }
    if (data.type === "automation_audio") {
      S.automationQueue.push({ seq: data.seq, mime: data.mime, data: data.data });
      playAutomation();
      return;
    }
    if (data.type === "automation_audio_done") return;

    if (data.turn_id !== undefined && data.turn_id !== S.activeTurnId) return; // a superseded turn

    if (data.type === "assistant") {
      if (data.mood) avatar.setMood(data.mood);
      for (const block of data.blocks || []) {
        if (block.kind === "text" && block.text) {
          if (S.bubbleMode !== "reply") {
            S.bubbleMode = "reply";
            el.bubbleTitle.textContent = "Neural";
          }
          bubble.append(block.text);
          S.replyForEcho += block.text;
        } else if (block.kind === "tool_use") {
          showActivity(block.tool || "", block.input || {});
        }
      }
    } else if (data.type === "result") {
      avatar.setActivity(null);
      if (S.replyRaw) addHistory("assistant", S.replyRaw);
      if (!S.isSpeaking && !S.expectingAudio) {
        setUi("idle");
        bubble.done();
      }
    } else if (data.type === "audio") {
      onAudioChunk(data.seq, data.mime, data.data);
    } else if (data.type === "audio_done") {
      onAudioDone(data.count);
    }
  }

  function activityFor(tool, input) {
    if (tool === "Bash") return ["Running a command", "code"];
    if (tool === "Read") {
      const p = String(input.file_path || "");
      if (/media[\\/]+analysis[\\/]/i.test(p)) return ["Watching the video", "eye"];
      return /media[\\/]+uploads[\\/]/i.test(p) ? ["Looking at your screen", "eye"] : ["Reading a file", "file"];
    }
    if (tool.includes("media_analysis")) return ["Watching the video", "video"];
    if (tool === "Write" || tool === "Edit" || tool === "MultiEdit") return ["Editing a file", "file"];
    if (tool === "Glob" || tool === "Grep") return ["Searching files", "search"];
    if (tool === "WebSearch") return ["Searching the web", "search"];
    if (tool === "WebFetch" || tool.includes("browser")) return ["Browsing the web", "web"];
    if (tool.includes("gmail")) return ["Checking email", "mail"];
    if (tool.includes("calendar")) return ["Checking your calendar", "mail"];
    if (tool.includes("memory") || tool.includes("crm")) return ["Remembering", "memory"];
    if (tool.includes("video") || tool.includes("__cap__")) return ["Working on video", "video"];
    if (tool.includes("background")) return ["Starting a background job", "gear"];
    if (tool.includes("automation")) return ["Checking automations", "gear"];
    if (tool.includes("playbook")) return ["Checking my notes", "file"];
    return ["Working on it", "gear"];
  }

  function showActivity(tool, input) {
    const [label, kind] = activityFor(tool, input);
    avatar.setActivity(label, kind);
    clearTimeout(S.activityTimer);
    S.activityTimer = setTimeout(() => avatar.setActivity(null), 12000);
  }

  // ------------------------------------------------------------ sending

  async function buildContext(extraClipboard) {
    let context = "";
    if (S.settings.contextInMessages) {
      const ctx = await N.getContext().catch(() => null);
      if (ctx && ctx.description) context = `Active window: ${ctx.description}`;
    }
    if (extraClipboard) context += (context ? "\n" : "") + "Clipboard contents:\n" + extraClipboard;
    return context;
  }

  async function sendToNeural(text, opts) {
    opts = opts || {};
    const attachments = opts.attachments || [];
    if (!text && !attachments.length && !opts.clipboardText) return;
    if (!text) text = opts.clipboardText ? "Here's what I copied -- what do you make of it?" : "What do you make of this?";
    const context = await buildContext(opts.clipboardText);
    const payload = { type: "message", text };
    if (context) payload.context = context;
    if (!S.settings.speakReplies) payload.speak = false;
    if (attachments.length) payload.attachments = attachments.map((a) => ({ mime: a.mime, kind: a.kind || "screen", data: a.data }));

    const notes = attachments.map((a) => a.label).concat(opts.clipboardText ? ["clipboard text"] : []);
    bubble.user(opts.display || text, notes);
    addHistory("user", opts.display || text);

    if (!S.connected) {
      S.queue.push({ text, context, attachments: payload.attachments || [], at: Date.now() });
      if (S.queue.length > 10) S.queue.splice(0, S.queue.length - 10);
      saveQueue();
      bubble.set("Neural isn't reachable right now -- I'll send this the moment it's back.", { holdMs: 9000 });
      avatar.setMood("worried");
      refreshStatus();
      return;
    }
    bargeIn();
    S.turnCounter++;
    S.activeTurnId = S.turnCounter;
    payload.turn_id = S.activeTurnId;
    S.replyForEcho = "";
    S.isSpeaking = false;
    S.expectingAudio = true; // the backend always finishes a turn with audio_done
    resetTurnAudio();
    bubble.startReply();
    setUi("thinking");
    try {
      S.socket.send(JSON.stringify(payload));
    } catch (e) {
      bubble.set("Couldn't send that -- reconnecting.", {});
    }
  }

  // Everything you type or say comes through here.
  async function handleUserText(text, source) {
    text = (text || "").trim();
    const pending = takePending();
    if (!text && !pending.attachments.length && !pending.clipboardText) return;
    bargeIn();

    if (text && !pending.attachments.length && !pending.clipboardText) {
      const r = await N.handleIntent(text).catch(() => ({ handled: false }));
      if (r && r.handled) {
        if (r.type === "stop") {
          bubble.hide();
          return;
        }
        if (r.prompt) return sendToNeural(r.prompt, { display: text });
        bubble.user(text);
        addHistory("user", text);
        if (r.mood) avatar.setMood(r.mood);
        bubble.set(r.reply || "", { files: r.files, holdMs: r.files ? 30000 : undefined });
        addHistory("assistant", r.reply);
        speakLocal(r.reply);
        return;
      }
    }

    const opts = { attachments: pending.attachments, clipboardText: pending.clipboardText };
    if (S.settings.autoAttach && text) {
      if (!opts.attachments.length && SCREEN_RE.test(text)) {
        const shot = await N.captureWindow().catch(() => ({ ok: false }));
        if (shot.ok) opts.attachments = [shotAttachment(shot)];
      }
      if (!opts.clipboardText && CLIP_RE.test(text)) {
        const clip = await N.readClipboard().catch(() => ({ kind: "empty" }));
        if (clip.kind === "text") opts.clipboardText = clip.text;
        else if (clip.kind === "image") opts.attachments = opts.attachments.concat([{ mime: clip.mime, data: clip.data, kind: "file", preview: clip.preview, label: "clipboard image" }]);
      }
    }
    sendToNeural(text, opts);
  }

  function flushQueue() {
    if (!S.queue.length || !S.connected) return;
    const items = S.queue.splice(0);
    saveQueue();
    const text = items.length === 1
      ? items[0].text
      : "While you were unreachable I sent these, in this order -- please handle them now:\n" + items.map((it, i) => `${i + 1}. ${it.text}`).join("\n");
    const payload = { type: "message", text };
    const ctx = items[items.length - 1].context;
    if (ctx) payload.context = ctx;
    const atts = [].concat(...items.map((it) => it.attachments || [])).slice(0, 4);
    if (atts.length) payload.attachments = atts;
    if (!S.settings.speakReplies) payload.speak = false;
    bubble.user(items.length === 1 ? items[0].text : `Sending the ${items.length} messages you left while Neural was offline`);
    S.turnCounter++;
    S.activeTurnId = payload.turn_id = S.turnCounter;
    S.expectingAudio = true;
    resetTurnAudio();
    bubble.startReply();
    setUi("thinking");
    S.socket.send(JSON.stringify(payload));
  }

  el.inputRow.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = el.input.value;
    el.input.value = "";
    handleUserText(text, "typed");
  });
  el.input.addEventListener("focus", () => {
    setIgnore(false);
    renderPills();
  });
  el.input.addEventListener("blur", () => setTimeout(renderPills, 150));
  el.input.addEventListener("paste", (e) => {
    const item = [...(e.clipboardData && e.clipboardData.items ? e.clipboardData.items : [])].find((i) => i.type.startsWith("image/"));
    if (!item) return;
    e.preventDefault();
    const file = item.getAsFile();
    const reader = new FileReader();
    reader.onload = () => {
      const dataUrl = String(reader.result);
      S.pending.attachments.push({ mime: file.type, data: dataUrl.slice(dataUrl.indexOf(",") + 1), kind: "file", preview: dataUrl, label: "pasted image" });
      renderPending();
    };
    reader.readAsDataURL(file);
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      if (voice.capturing) voice.cancel();
      else if (!bargeIn()) bubble.hide();
      el.input.blur();
      return;
    }
    if (e.code === "Space" && document.activeElement !== el.input && !e.repeat && !e.ctrlKey && !e.altKey) {
      e.preventDefault();
      startVoice("ptt");
    }
  });
  document.addEventListener("keyup", (e) => {
    if (e.code === "Space" && voice.capturing && voice.mode === "ptt") {
      e.preventDefault();
      voice.stop();
    }
  });

  // ------------------------------------------------------------ voice

  const voice = new window.NeuralVoice.Voice({
    onLevel: (rms) => {
      if (voice.capturing) document.documentElement.style.setProperty("--lvl", Math.min(1, rms * 12).toFixed(3));
    },
    onStart: () => {
      setUi("listening");
      avatar.setActivity(null);
    },
    onSpeechStart: () => {
      if (S.isSpeaking) duck(true); // talk over her: she gets quieter right away
      if (voice.mode === "listen" && S.uiState !== "speaking") setUi("listening");
    },
    onDiscard: (mode, why) => {
      document.documentElement.style.setProperty("--lvl", 0);
      if (S.ducked) duck(false);
      setUi(S.isSpeaking ? "speaking" : S.connected ? "idle" : "offline");
      if (mode !== "listen" && why === "silent") flashStatus("Didn't hear anything");
    },
    onUtterance: (wav, info) => transcribeAndHandle(wav, info),
    onError: (msg) => {
      setUi(S.connected ? "idle" : "offline");
      bubble.user("");
      bubble.set("", { html: `<p class="err">${escapeHtml(msg)}</p>`, holdMs: 12000 });
      if (S.settings.alwaysListening) N.setSetting("alwaysListening", false);
    },
    isNeuralSpeaking: () => S.isSpeaking || S.automationPlaying,
    isBlocked: () => S.sysAudioActive && S.settings.pauseOnSystemAudio && !S.isSpeaking,
  });

  const sysAudio = new window.NeuralVoice.SystemAudioMonitor((active) => {
    S.sysAudioActive = active;
    refreshStatus();
  });
  sysAudio.ignore = () => S.isSpeaking || S.automationPlaying || Date.now() - S.lastSpeechEndedAt < 600;

  async function startVoice(mode) {
    if (voice.capturing) return;
    if (!S.connected) {
      bubble.user("");
      bubble.set("Voice needs Neural running -- type instead, or start it from the tray icon.", { holdMs: 8000 });
      return;
    }
    if (S.voiceAvailable === false) {
      showVoiceInstallHint();
      return;
    }
    bargeIn();
    await voice.start(mode);
  }

  function showVoiceInstallHint() {
    bubble.user("");
    bubble.set("", {
      html:
        "<p>Voice input needs local speech recognition in Neural's backend (free, offline). Run this once, then restart Neural:</p>" +
        `<pre><code>${escapeHtml(S.voiceHint || "backend\\.venv\\Scripts\\python.exe -m pip install -U faster-whisper")}</code></pre>`,
      holdMs: 30000,
    });
  }

  function looksLikeOwnEcho(text) {
    if (!S.replyForEcho) return false;
    if (!S.isSpeaking && Date.now() - S.lastSpeechEndedAt > 1500) return false;
    const words = (text.toLowerCase().match(/[a-z0-9']+/g) || []).filter((w) => w.length > 2);
    if (words.length < 2) return false;
    const said = new Set(S.replyForEcho.toLowerCase().match(/[a-z0-9']+/g) || []);
    return words.filter((w) => said.has(w)).length / words.length >= 0.7;
  }

  // "Neural, what's the weather" -> "what's the weather"; null if no wake word up front.
  function stripWakeWord(text) {
    const words = text.trim().split(/\s+/);
    const norm = words.map((w) => w.toLowerCase().replace(/[^a-z']/g, ""));
    const custom = (S.settings.wakeWords || []).map((w) => w.toLowerCase().trim()).filter(Boolean);
    for (let i = 0; i < Math.min(4, norm.length); i++) {
      if (NEURAL_WORD.test(norm[i])) return words.slice(i + 1).join(" ").replace(/^[,.!?\s]+/, "");
    }
    const joined = norm.join(" ");
    for (const ww of custom.sort((a, b) => b.length - a.length)) {
      const idx = joined.indexOf(ww);
      if (idx !== -1 && joined.slice(0, idx).split(" ").filter(Boolean).length <= 3) {
        const consumed = joined.slice(0, idx + ww.length).split(" ").filter(Boolean).length;
        return words.slice(consumed).join(" ").replace(/^[,.!?\s]+/, "");
      }
    }
    return null;
  }

  let lastAcceptedAt = 0;
  async function transcribeAndHandle(wav, info) {
    document.documentElement.style.setProperty("--lvl", 0);
    const listenMode = info.mode === "listen";
    if (!listenMode || S.uiState !== "speaking") setUi("thinking", "Transcribing...");
    el.mic.classList.add("busy");
    const r = await N.transcribe(wav, "audio/wav").catch(() => ({ ok: false, error: "transcription failed" }));
    el.mic.classList.remove("busy");
    const backToRest = () => {
      if (S.ducked) duck(false);
      setUi(S.isSpeaking ? "speaking" : S.connected ? "idle" : "offline");
    };
    if (!r.ok) {
      backToRest();
      if (r.status === 503) {
        S.voiceAvailable = false;
        S.voiceHint = (r.error || "").match(/"[^"]+" -m pip install -U \S+/)?.[0] || "";
        showVoiceInstallHint();
      } else if (!listenMode) {
        flashStatus(r.error || "Couldn't transcribe that");
      }
      return;
    }
    S.voiceAvailable = true;
    const text = (r.text || "").trim();
    if (!text || r.noise) {
      backToRest();
      if (!listenMode) flashStatus("Didn't catch that");
      return;
    }
    if (looksLikeOwnEcho(text)) {
      backToRest();
      return;
    }
    let command = text;
    if (listenMode) {
      const stripped = stripWakeWord(text);
      const followUp = Date.now() - Math.max(S.lastSpeechEndedAt, lastAcceptedAt) < (S.settings.followUpSeconds ?? 10) * 1000;
      if (stripped !== null) {
        command = stripped;
        if (!command) {
          // Just her name: she's listening -- the next sentence needs no wake word.
          backToRest();
          lastAcceptedAt = Date.now();
          avatar.reactTo("curious", 1.6);
          flashStatus("Yes?");
          return;
        }
      } else if (!followUp && !S.isSpeaking) {
        backToRest();
        return; // not for her
      }
    }
    lastAcceptedAt = Date.now();
    handleUserText(command, "voice");
  }

  async function warmVoice() {
    const r = await N.warmVoice().catch(() => null);
    if (r && r.available === false) S.voiceAvailable = false;
    else if (r && r.ready) S.voiceAvailable = true;
  }

  async function applyListening() {
    if (S.settings.alwaysListening) {
      const ok = await voice.setAlwaysOn(true);
      if (ok && S.settings.pauseOnSystemAudio && !S.settings.safeMode && navigator.mediaDevices) {
        sysAudio.start().catch(() => false);
      } else {
        sysAudio.stop();
      }
    } else {
      voice.setAlwaysOn(false);
      sysAudio.stop();
    }
    refreshStatus();
  }

  // Mic button: hold = push-to-talk; quick tap = hands-free until you pause.
  el.mic.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    if (voice.capturing) {
      voice.stop();
      S.micDownAt = 0;
      return;
    }
    S.micDownAt = Date.now();
    el.mic.setPointerCapture(e.pointerId);
    startVoice("ptt");
  });
  el.mic.addEventListener("pointerup", () => {
    if (!S.micDownAt) return;
    const held = Date.now() - S.micDownAt;
    S.micDownAt = 0;
    if (!voice.capturing) return;
    if (held < 350) voice.switchTo("toggle");
    else voice.stop();
  });

  // ------------------------------------------------------------ main-process events

  N.on("cursor", (p) => {
    if (S.settings.followCursor) avatar.lookAt(p.x, p.y);
  });

  N.on("layout", (l) => {
    S.layout = l;
    document.body.classList.toggle("side-left", l.side === "left");
    document.body.classList.toggle("side-right", l.side === "right");
    document.documentElement.style.setProperty("--bubble-w", l.bubbleW + "px");
    document.documentElement.style.setProperty("--gap", l.gap + "px");
  });

  N.on("settings", (s) => {
    const prevListening = S.settings.alwaysListening;
    const prevPause = S.settings.pauseOnSystemAudio;
    S.settings = Object.assign({}, S.settings, s);
    if (!S.settings.followCursor) avatar.lookAt(null, null);
    if (prevListening !== S.settings.alwaysListening || prevPause !== S.settings.pauseOnSystemAudio) applyListening();
    if (!S.settings.speakReplies && (S.isSpeaking || S.automationPlaying)) {
      try { el.tts.pause(); } catch (e) {}
    }
    renderPills();
    refreshStatus();
  });

  N.on("context", (ctx) => {
    S.context = ctx;
    renderPills();
  });

  N.on("hotkey", (name) => {
    if (name === "voice") {
      if (voice.capturing) voice.stop();
      else startVoice("toggle");
    } else if (name === "type") {
      prefill(el.input.value);
    } else if (name === "ask-window") {
      askAboutWindow();
    }
  });

  N.on("ptt", (p) => {
    if (p.down) startVoice("ptt");
    else if (voice.capturing && voice.mode === "ptt") {
      if (p.cancel) voice.cancel();
      else voice.stop();
    }
  });

  N.on("reminder", (r) => {
    const text = r.missed ? `You missed a reminder while I was closed: ${r.text}` : r.timer ? `${r.text}!` : `Reminder: ${r.text}`;
    avatar.setMood("excited");
    avatar.reactTo("surprised", 1.5);
    bubble.user("");
    el.bubbleTitle.textContent = r.timer ? "Timer" : "Reminder";
    S.bubblePinned = true;
    bubble.set(text, { keep: true });
    speakLocal(text);
  });

  N.on("menu:action", (a) => {
    switch (a.action) {
      case "suggestion": return runSuggestion(a.item);
      case "ask-window": return askAboutWindow();
      case "ocr-window": return copyWindowText();
      case "ask-clipboard": return attachClipboard();
      case "prefill": return prefill(a.text);
      case "prompt": return handleUserText(a.text, "menu");
      case "local": return handleUserText(a.text, "menu");
      case "history": return showHistory();
      default: return undefined;
    }
  });

  N.on("backend-status", (b) => {
    if (b && b.starting && !S.connected) {
      setStatus("Starting Neural...");
    }
  });

  function firstRunHello() {
    if (store("neural.welcomed")) return;
    store("neural.welcomed", true);
    bubble.user("");
    el.bubbleTitle.textContent = "Hi!";
    bubble.set(
      "I'm here whenever you need me. **Hold Space** while I'm focused (or **hold Right Ctrl** anywhere) to talk, or type below. " +
        "**Right-click me** for quick actions -- screenshots, your clipboard, files, reminders.",
      { holdMs: 20000 }
    );
    avatar.setMood("happy");
  }

  let safeNoticeShown = false;
  function maybeShowSafeModeNotice() {
    if (safeNoticeShown || !S.settings.safeMode) return;
    safeNoticeShown = true;
    bubble.user("");
    el.bubbleTitle.textContent = "Safe mode";
    bubble.set(
      "I crashed twice in a row, so I started in **safe mode**: graphics acceleration and hold-to-talk anywhere are off for now. " +
        "Everything else works. Right-click me > *Restart normally* to try the full version again. (Details: `desktop\\logs\\desktop.log`)",
      { holdMs: 25000 }
    );
    avatar.setMood("embarrassed");
  }

  // ------------------------------------------------------------ boot

  setUi("offline", "Connecting to Neural...");
  N.getSettings().then((s) => {
    S.settings = Object.assign({}, S.settings, s);
    applyListening();
    setTimeout(maybeShowSafeModeNotice, 1500);
  });
  N.getContext().then((ctx) => {
    S.context = ctx;
  }).catch(() => {});
  connect();
})();
