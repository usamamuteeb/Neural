// Voice capture for Neural Desktop.
//
// Electron has no working Web Speech API, so this records the mic itself
// (raw PCM through an AudioWorklet), cuts utterances with a small
// energy-based voice-activity detector, and hands each one over as a
// 16 kHz mono WAV for the backend's local Whisper to transcribe.
//
// Modes:
//   "ptt"    -- hold-to-talk: records until stop() (key/button released)
//   "toggle" -- press once: records until you pause (~1.1 s of silence)
//   "listen" -- always-listening: the VAD starts and ends utterances itself
//
// A 0.5 s rolling pre-roll means the first syllable isn't clipped when
// recording starts a moment after you begin speaking.
(function () {
  "use strict";

  const WORKLET_SRC = `
class NeuralPcmCapture extends AudioWorkletProcessor {
  constructor() { super(); this.buf = new Float32Array(2048); this.n = 0; }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch) {
      for (let i = 0; i < ch.length; i++) {
        this.buf[this.n++] = ch[i];
        if (this.n === this.buf.length) { this.port.postMessage(this.buf.slice(0)); this.n = 0; }
      }
    }
    return true;
  }
}
registerProcessor("neural-pcm-capture", NeuralPcmCapture);
`;

  const TARGET_RATE = 16000;

  function encodeWav(chunks, rate) {
    let total = 0;
    for (const c of chunks) total += c.length;
    const flat = new Float32Array(total);
    let o = 0;
    for (const c of chunks) {
      flat.set(c, o);
      o += c.length;
    }
    const ratio = rate / TARGET_RATE;
    const outLen = Math.max(0, Math.floor(total / ratio));
    const pcm = new Int16Array(outLen);
    for (let i = 0; i < outLen; i++) {
      // Box-filter decimation: averaging each window is a cheap low-pass.
      const a = Math.floor(i * ratio);
      const b = Math.min(total, Math.max(a + 1, Math.floor((i + 1) * ratio)));
      let s = 0;
      for (let j = a; j < b; j++) s += flat[j];
      const v = Math.max(-1, Math.min(1, s / (b - a)));
      pcm[i] = v < 0 ? v * 0x8000 : v * 0x7fff;
    }
    const buf = new ArrayBuffer(44 + pcm.length * 2);
    const dv = new DataView(buf);
    const str = (off, s) => { for (let i = 0; i < s.length; i++) dv.setUint8(off + i, s.charCodeAt(i)); };
    str(0, "RIFF");
    dv.setUint32(4, 36 + pcm.length * 2, true);
    str(8, "WAVE");
    str(12, "fmt ");
    dv.setUint32(16, 16, true);
    dv.setUint16(20, 1, true);           // PCM
    dv.setUint16(22, 1, true);           // mono
    dv.setUint32(24, TARGET_RATE, true);
    dv.setUint32(28, TARGET_RATE * 2, true);
    dv.setUint16(32, 2, true);
    dv.setUint16(34, 16, true);
    str(36, "data");
    dv.setUint32(40, pcm.length * 2, true);
    new Int16Array(buf, 44).set(pcm);
    return { wav: buf, seconds: outLen / TARGET_RATE };
  }

  class Voice {
    constructor(opts) {
      this.opts = Object.assign({
        onLevel: () => {}, onStart: () => {}, onSpeechStart: () => {}, onUtterance: () => {},
        onDiscard: () => {}, onError: () => {}, isNeuralSpeaking: () => false, isBlocked: () => false,
      }, opts || {});
      this.ctx = null;
      this.stream = null;
      this.node = null;
      this.rate = 48000;
      this.mode = null;         // null when not capturing
      this.chunks = [];
      this.samples = 0;
      this.preroll = [];
      this.prerollSamples = 0;
      this.alwaysOn = false;
      this.floor = 0.006;
      this.heardFrames = 0;
      this.silentFrames = 0;
      this.loudFrames = 0;
      this.speechFrames = 0;
      this.peak = 0;
      this.startedAt = 0;
      this.idleTimer = null;
      this.opening = null;
    }

    get capturing() {
      return this.mode !== null;
    }

    async ensure() {
      if (this.ctx && this.stream && this.stream.active) {
        if (this.ctx.state === "suspended") await this.ctx.resume();
        return;
      }
      if (this.opening) return this.opening;
      this.opening = (async () => {
        this.stream = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
        });
        this.ctx = new AudioContext({ latencyHint: "interactive" });
        const url = URL.createObjectURL(new Blob([WORKLET_SRC], { type: "application/javascript" }));
        try {
          await this.ctx.audioWorklet.addModule(url);
        } finally {
          URL.revokeObjectURL(url);
        }
        const src = this.ctx.createMediaStreamSource(this.stream);
        this.node = new AudioWorkletNode(this.ctx, "neural-pcm-capture");
        this.node.port.onmessage = (e) => this._onFrame(e.data);
        const mute = this.ctx.createGain();
        mute.gain.value = 0;
        src.connect(this.node);
        this.node.connect(mute);
        mute.connect(this.ctx.destination);
        this.rate = this.ctx.sampleRate;
      })();
      try {
        await this.opening;
      } finally {
        this.opening = null;
      }
    }

    // Release the mic (and Windows' "mic in use" indicator) when idle.
    _scheduleIdleClose() {
      clearTimeout(this.idleTimer);
      if (this.alwaysOn) return;
      this.idleTimer = setTimeout(() => {
        if (!this.alwaysOn && !this.capturing) this.close();
      }, 45000);
    }

    close() {
      clearTimeout(this.idleTimer);
      try { this.stream && this.stream.getTracks().forEach((t) => t.stop()); } catch (e) {}
      try { this.ctx && this.ctx.close(); } catch (e) {}
      this.stream = null;
      this.ctx = null;
      this.node = null;
      this.preroll = [];
      this.prerollSamples = 0;
    }

    async setAlwaysOn(on) {
      this.alwaysOn = !!on;
      if (this.alwaysOn) {
        try {
          await this.ensure();
        } catch (e) {
          this.alwaysOn = false;
          this.opts.onError(micError(e));
          return false;
        }
      } else {
        this._scheduleIdleClose();
      }
      return true;
    }

    async start(mode) {
      clearTimeout(this.idleTimer);
      try {
        await this.ensure();
      } catch (e) {
        this.opts.onError(micError(e));
        return false;
      }
      this._begin(mode, true);
      return true;
    }

    _begin(mode, withPreroll) {
      this.mode = mode;
      this.chunks = withPreroll ? this.preroll.slice() : [];
      this.samples = withPreroll ? this.prerollSamples : 0;
      this.preroll = [];
      this.prerollSamples = 0;
      this.heardFrames = 0;
      this.silentFrames = 0;
      this.speechFrames = 0;
      this.peak = 0;
      this.startedAt = performance.now();
      this.opts.onStart(mode);
    }

    // A ptt press turning into hands-free toggle mode (a quick tap).
    switchTo(mode) {
      if (this.capturing) this.mode = mode;
    }

    stop() {
      if (!this.capturing) return;
      const mode = this.mode;
      const chunks = this.chunks;
      this.mode = null;
      this.chunks = [];
      this._scheduleIdleClose();
      const speech = this.speechFrames * (2048 / this.rate);
      const { wav, seconds } = encodeWav(chunks, this.rate);
      const tooShort = seconds < 0.35 || (mode !== "ptt" && speech < 0.25);
      const silent = this.peak < 0.01;
      if (tooShort || silent) {
        this.opts.onDiscard(mode, tooShort ? "short" : "silent");
        return;
      }
      this.opts.onUtterance(wav, { mode, seconds, peak: this.peak });
    }

    cancel() {
      if (!this.capturing) return;
      const mode = this.mode;
      this.mode = null;
      this.chunks = [];
      this._scheduleIdleClose();
      this.opts.onDiscard(mode, "cancelled");
    }

    _threshold() {
      const t = Math.max(0.012, this.floor * 3.2);
      // Louder bar while Neural is talking, so leftover echo doesn't trigger.
      return this.opts.isNeuralSpeaking() ? t * 1.8 : t;
    }

    _onFrame(f) {
      let sum = 0;
      let peak = 0;
      for (let i = 0; i < f.length; i++) {
        const v = f[i];
        sum += v * v;
        if (v > peak) peak = v;
        else if (-v > peak) peak = -v;
      }
      const rms = Math.sqrt(sum / f.length);
      this.opts.onLevel(rms);
      const frameSec = f.length / this.rate;
      const thr = this._threshold();
      const loud = rms > thr;

      if (!this.capturing) {
        // Rolling pre-roll + noise floor tracking.
        this.preroll.push(f);
        this.prerollSamples += f.length;
        while (this.preroll.length > 1 && this.prerollSamples - this.preroll[0].length > this.rate * 0.5) {
          this.prerollSamples -= this.preroll.shift().length;
        }
        if (!loud) this.floor = Math.min(0.05, Math.max(0.002, this.floor * 0.97 + rms * 0.03));
        if (this.alwaysOn && !this.opts.isBlocked()) {
          this.loudFrames = loud ? this.loudFrames + 1 : 0;
          if (this.loudFrames * frameSec >= 0.12) {
            this.loudFrames = 0;
            this._begin("listen", true);
            this.speechFrames = 3;
            this.opts.onSpeechStart();
          }
        } else {
          this.loudFrames = 0;
        }
        return;
      }

      this.chunks.push(f);
      this.samples += f.length;
      if (peak > this.peak) this.peak = peak;
      if (loud) {
        this.speechFrames++;
        this.silentFrames = 0;
        if (++this.heardFrames === 3 && this.mode === "toggle") this.opts.onSpeechStart();
      } else {
        this.silentFrames++;
      }
      const elapsed = this.samples / this.rate;
      const silence = this.silentFrames * frameSec;
      const heard = this.speechFrames * frameSec >= 0.2;
      if (this.mode === "ptt") {
        if (elapsed > 60) this.stop();
      } else if (this.mode === "toggle") {
        if ((heard && silence >= 1.1) || elapsed > 30) this.stop();
        else if (!heard && elapsed > 7) this.cancel();
      } else if (this.mode === "listen") {
        if (silence >= 0.8 || elapsed > 20) this.stop();
      }
    }
  }

  function micError(e) {
    const name = e && e.name;
    if (name === "NotAllowedError" || name === "SecurityError") return "Microphone access was blocked. Allow it in Windows Settings > Privacy & security > Microphone (desktop apps).";
    if (name === "NotFoundError" || name === "OverconstrainedError") return "No microphone found.";
    if (name === "NotReadableError") return "The microphone is busy in another app.";
    return "Couldn't open the microphone: " + ((e && e.message) || e);
  }

  // Pauses always-listening while the PC is playing sound (a video, music,
  // a call), using a system-audio loopback stream (Windows). Never records.
  class SystemAudioMonitor {
    constructor(onChange) {
      this.onChange = onChange;
      this.stream = null;
      this.ctx = null;
      this.timer = null;
      this.active = false;
      this.loudPolls = 0;
      this.quietSince = 0;
      this.ignore = () => false; // true while Neural herself is talking
    }

    async start() {
      if (this.stream) return true;
      let stream = null;
      try {
        stream = await navigator.mediaDevices.getDisplayMedia({ audio: true, video: true });
      } catch (e) {
        try {
          stream = await navigator.mediaDevices.getUserMedia({
            audio: { mandatory: { chromeMediaSource: "desktop" } },
            video: { mandatory: { chromeMediaSource: "desktop", maxWidth: 2, maxHeight: 2 } },
          });
        } catch (e2) {
          return false;
        }
      }
      stream.getVideoTracks().forEach((t) => t.stop());
      if (!stream.getAudioTracks().length) {
        stream.getTracks().forEach((t) => t.stop());
        return false;
      }
      this.stream = stream;
      this.ctx = new AudioContext();
      const src = this.ctx.createMediaStreamSource(new MediaStream(stream.getAudioTracks()));
      const an = this.ctx.createAnalyser();
      an.fftSize = 1024;
      src.connect(an);
      const buf = new Float32Array(an.fftSize);
      this.timer = setInterval(() => {
        an.getFloatTimeDomainData(buf);
        let s = 0;
        for (let i = 0; i < buf.length; i++) s += buf[i] * buf[i];
        const rms = Math.sqrt(s / buf.length);
        const now = performance.now();
        if (this.ignore()) {
          this.loudPolls = 0;
          return;
        }
        if (rms > 0.008) {
          this.loudPolls++;
          this.quietSince = 0;
          if (!this.active && this.loudPolls >= 2) this._set(true);
        } else {
          this.loudPolls = 0;
          if (!this.quietSince) this.quietSince = now;
          if (this.active && now - this.quietSince > 1500) this._set(false);
        }
      }, 150);
      return true;
    }

    _set(on) {
      this.active = on;
      try { this.onChange(on); } catch (e) {}
    }

    stop() {
      clearInterval(this.timer);
      this.timer = null;
      try { this.stream && this.stream.getTracks().forEach((t) => t.stop()); } catch (e) {}
      try { this.ctx && this.ctx.close(); } catch (e) {}
      this.stream = null;
      this.ctx = null;
      if (this.active) this._set(false);
    }
  }

  window.NeuralVoice = { Voice, SystemAudioMonitor, encodeWav };
})();
