/*
 * Neural's avatar -- an original anime-style character drawn entirely in
 * code (inline SVG + a small animation loop). No downloaded models, no
 * Live2D/VRM runtime, no third-party art, nothing to license or pay for:
 * every shape below is hand-written path data, and every motion is
 * computed here each frame.
 *
 * What she does:
 *   - Breathes, blinks (with the occasional double-blink), sways her hair
 *     with a lagging spring so it follows head movement, and glances
 *     around on her own when idle.
 *   - Follows your cursor with her eyes (and a little with her head).
 *   - Reacts to Neural's state: listening (leans in, eyes wide, sound
 *     waves at her headset), thinking (looks up, thought bubble),
 *     speaking (lip-sync driven by the actual TTS audio level via a Web
 *     Audio AnalyserNode), offline (dozes off, "z z z").
 *   - Shows a mood for each reply -- happy, excited, playful, proud,
 *     surprised, curious, worried, sad, embarrassed, affectionate,
 *     annoyed -- picked by Claude itself via a [mood:...] tag the backend
 *     strips out of the spoken text (see backend/mood.py).
 *   - Headpat her (click/tap her head) and she reacts.
 *   - Puts on glasses in Tutor mode.
 *   - Eye color follows --state-hue, so her eyes shift color with the
 *     rest of the UI (cyan idle, amber listening, violet thinking, ...).
 *
 * Usage:
 *   const av = new NeuralAvatar(containerEl);
 *   av.setState("thinking");           // idle|listening|thinking|speaking|offline
 *   av.setMood("happy");               // see MOODS below
 *   av.setMode("tutor");               // chat|tutor (glasses)
 *   av.setAnalyser(analyserNode);      // lip-sync from real audio
 *   av.setTalking(true);               // procedural lip-flap when no analyser feeds the audio
 *   av.lookAt(x, y);                   // gaze target (client coords) from outside the page
 *   av.setActivity("Browsing the web", "web");  // tool chip; null clears
 *   av.flourish();                     // ring burst (mode switch)
 */
(function () {
  "use strict";

  const MOODS = [
    "neutral", "happy", "excited", "playful", "proud", "surprised",
    "curious", "worried", "sad", "embarrassed", "affectionate", "annoyed",
  ];

  const ICONS = {
    code: '<path d="M8 7l-5 5 5 5M16 7l5 5-5 5M14 4l-4 16"/>',
    web: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3.5 3 14.5 0 18M12 3c-3 3.5-3 14.5 0 18"/>',
    mail: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 6 9-6"/>',
    file: '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4M9 13h6M9 17h6"/>',
    memory: '<path d="M12 3l2.2 5.8L20 11l-5.8 2.2L12 19l-2.2-5.8L4 11l5.8-2.2z"/>',
    video: '<rect x="3" y="6" width="13" height="12" rx="2"/><path d="M16 10l5-3v10l-5-3z"/>',
    search: '<circle cx="11" cy="11" r="6.5"/><path d="M16 16l5 5"/>',
    eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    gear: '<circle cx="12" cy="12" r="3.2"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1"/>',
  };

  const STYLE = `
.nv-wrap { position: relative; width: 100%; height: 100%; user-select: none; -webkit-user-select: none; }
.nv-wrap svg { width: 100%; height: 100%; display: block; overflow: visible; cursor: pointer;
  transition: filter 0.8s ease; -webkit-tap-highlight-color: transparent; }
.nv-wrap.nv-offline svg { filter: saturate(0.55) brightness(0.82); }
.nv-wrap svg { -webkit-mask-image: linear-gradient(to bottom, #000 80%, transparent 99%); mask-image: linear-gradient(to bottom, #000 80%, transparent 99%); }
.nv-wrap .nv-ring, .nv-wrap .nv-orbit, .nv-wrap .nv-burst { transform-box: fill-box; transform-origin: center; }
.nv-wrap .nv-ring1 { animation: nv-spin 14s linear infinite; }
.nv-wrap .nv-ring2 { animation: nv-spin 24s linear infinite reverse; }
.nv-wrap .nv-orbit1 { animation: nv-spin 7s linear infinite; }
.nv-wrap .nv-orbit2 { animation: nv-spin 11s linear infinite reverse; }
.nv-wrap .nv-orbit3 { animation: nv-spin 9s linear infinite; }
.nv-wrap .nv-orbit4 { animation: nv-spin 15s linear infinite reverse; }
.nv-wrap svg.st-thinking .nv-ring1 { animation-duration: 3s; }
.nv-wrap svg.st-thinking .nv-ring2 { animation-duration: 5s; }
.nv-wrap svg.st-thinking .nv-orbit { animation-duration: 2.4s; }
.nv-wrap svg.st-listening .nv-ring1 { animation-duration: 7s; }
.nv-wrap svg.st-speaking .nv-ring1 { animation-duration: 6s; }
.nv-wrap svg.st-offline .nv-hud { opacity: 0.35; }
.nv-wrap svg.nv-flourish .nv-burst { animation: nv-burst 0.9s ease-out; }
.nv-wrap svg.nv-flourish .nv-ring { animation-duration: 0.7s; }
@keyframes nv-spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
@keyframes nv-burst { 0% { transform: scale(0.6); opacity: 0.95; } 100% { transform: scale(1.5); opacity: 0; } }
.nv-wrap .nv-fx { opacity: 0; transition: opacity 0.35s ease; pointer-events: none; }
.nv-wrap svg.fx-sparkle .nv-fx-sparkle,
.nv-wrap svg.fx-hearts .nv-fx-hearts,
.nv-wrap svg.fx-zzz .nv-fx-zzz,
.nv-wrap svg.fx-think .nv-fx-think,
.nv-wrap svg.fx-waves .nv-fx-waves,
.nv-wrap svg.fx-bang .nv-fx-bang,
.nv-wrap svg.fx-sweat .nv-fx-sweat,
.nv-wrap svg.fx-blushlines .nv-fx-blushlines,
.nv-wrap svg.fx-annoyed .nv-fx-annoyed { opacity: 1; }
.nv-wrap .nv-glasses { opacity: 0; transition: opacity 0.5s ease; }
.nv-wrap svg.mode-tutor .nv-glasses { opacity: 1; }

.nv-wrap .nv-fx-sparkle > g > path { transform-box: fill-box; transform-origin: center; animation: nv-twinkle 1.6s ease-in-out infinite; }
.nv-wrap .nv-fx-sparkle > g:nth-child(2) > path { animation-delay: -0.4s; }
.nv-wrap .nv-fx-sparkle > g:nth-child(3) > path { animation-delay: -0.9s; }
.nv-wrap .nv-fx-sparkle > g:nth-child(4) > path { animation-delay: -1.2s; }
@keyframes nv-twinkle { 0%,100% { transform: scale(0.35) rotate(0deg); opacity: 0.25; } 50% { transform: scale(1) rotate(45deg); opacity: 1; } }

.nv-wrap .nv-fx-hearts > g > path { transform-box: fill-box; transform-origin: center; animation: nv-float 2.2s ease-in infinite; }
.nv-wrap .nv-fx-hearts > g:nth-child(2) > path { animation-delay: -0.75s; }
.nv-wrap .nv-fx-hearts > g:nth-child(3) > path { animation-delay: -1.5s; }
@keyframes nv-float { 0% { transform: translateY(14px) scale(0.6); opacity: 0; } 25% { opacity: 1; } 100% { transform: translateY(-34px) scale(1.05); opacity: 0; } }

.nv-wrap .nv-fx-zzz text { animation: nv-zzz 3s ease-in-out infinite; }
.nv-wrap .nv-fx-zzz text:nth-child(2) { animation-delay: -1s; }
.nv-wrap .nv-fx-zzz text:nth-child(3) { animation-delay: -2s; }
@keyframes nv-zzz { 0% { opacity: 0; transform: translate(0, 10px); } 30% { opacity: 1; } 100% { opacity: 0; transform: translate(10px, -16px); } }

.nv-wrap .nv-fx-think .nv-dot { animation: nv-dot 1.2s ease-in-out infinite; }
.nv-wrap .nv-fx-think .nv-dot:nth-of-type(2) { animation-delay: 0.2s; }
.nv-wrap .nv-fx-think .nv-dot:nth-of-type(3) { animation-delay: 0.4s; }
@keyframes nv-dot { 0%,80%,100% { opacity: 0.25; } 40% { opacity: 1; } }

.nv-wrap .nv-fx-waves path { animation: nv-wave 1.4s ease-out infinite; }
.nv-wrap .nv-fx-waves path:nth-child(2) { animation-delay: 0.35s; }
.nv-wrap .nv-fx-waves path:nth-child(3) { animation-delay: 0.7s; }
@keyframes nv-wave { 0% { opacity: 0; } 30% { opacity: 1; } 100% { opacity: 0; } }

.nv-wrap .nv-fx-bang { transform-box: fill-box; transform-origin: bottom center; }
.nv-wrap svg.fx-bang .nv-fx-bang { animation: nv-pop 0.5s cubic-bezier(0.34, 1.56, 0.64, 1); }
@keyframes nv-pop { 0% { transform: scale(0.2); } 100% { transform: scale(1); } }

.nv-wrap .nv-chip {
  position: absolute; left: 50%; top: 4%; transform: translate(-50%, -6px);
  display: flex; align-items: center; gap: 7px; max-width: 92%;
  padding: 5px 11px 5px 8px; border-radius: 999px;
  font: 600 0.72rem/1.1 var(--font-ui, "Segoe UI", sans-serif); letter-spacing: 0.04em;
  color: hsl(var(--state-hue, 190) 85% 82%);
  background: hsla(var(--state-hue, 190), 60%, 12%, 0.72);
  border: 1px solid hsla(var(--state-hue, 190), 80%, 60%, 0.45);
  box-shadow: 0 0 18px hsla(var(--state-hue, 190), 80%, 50%, 0.25);
  backdrop-filter: blur(6px); -webkit-backdrop-filter: blur(6px);
  opacity: 0; pointer-events: none; transition: opacity 0.25s ease, transform 0.25s ease;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; z-index: 2;
}
.nv-wrap .nv-chip.show { opacity: 1; transform: translate(-50%, 0); }
.nv-wrap .nv-chip svg { width: 14px; height: 14px; flex-shrink: 0; fill: none; stroke: currentColor;
  stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; cursor: default; }
.nv-wrap .nv-chip span { overflow: hidden; text-overflow: ellipsis; }
.nv-wrap .nv-chip::after { content: ""; width: 5px; height: 5px; border-radius: 50%; flex-shrink: 0;
  background: currentColor; animation: nv-dot 1s ease-in-out infinite; }

@media (prefers-reduced-motion: reduce) {
  .nv-wrap .nv-ring, .nv-wrap .nv-orbit { animation-duration: 60s !important; }
  .nv-wrap .nv-fx-sparkle path, .nv-wrap .nv-fx-hearts path, .nv-wrap .nv-fx-zzz text { animation-duration: 6s; }
}
`;

  // Hue-driven paint helpers (inline SVG inherits CSS custom properties,
  // and --state-hue is a registered, transitioned property in style.css,
  // so these shift smoothly whenever Neural's state/mode changes).
  const H = (s, l, a) =>
    a === undefined ? `hsl(var(--state-hue,190) ${s}% ${l}%)` : `hsla(var(--state-hue,190), ${s}%, ${l}%, ${a})`;

  function buildSVG(u) {
    const id = (n) => `nv${u}-${n}`;
    const url = (n) => `url(#${id(n)})`;
    // Eye in LOCAL coordinates: outer corner at -x, inner (toward the nose)
    // at +x. The right eye reuses the same shapes inside a mirrored group.
    const scleraD = "M -25 -1 C -21 -17, 10 -21, 23 -9 C 26 4, 17 19, 1 20 C -15 20, -26 11, -25 -1 Z";
    const lashD =
      "M -33 -9 C -31 -7, -29 -5, -27 -2 C -20 -17, 9 -21, 24 -9 C 25 -7, 26 -6, 26 -4 " +
      "C 28 -11, 21 -24, 0 -25 C -16 -25, -25 -19, -30 -13 C -31 -11, -32 -10, -33 -9 Z";
    const eye = (side) => `
      <g class="nv-eye nv-eye-${side}">
        <g class="nv-eye-open">
          <path d="${scleraD}" fill="#fbfdff"/>
          <g clip-path="${url("eyeclip")}">
            <g class="nv-iris">
              <ellipse cx="0" cy="3" rx="15" ry="18.5" fill="${url("iris")}"/>
              <ellipse cx="0" cy="3" rx="15" ry="18.5" fill="none" stroke="${H(70, 16)}" stroke-width="1.6"/>
              <ellipse class="nv-pupil" cx="0" cy="4.5" rx="6.2" ry="8.6" fill="${H(75, 12)}"/>
              <ellipse cx="0" cy="10" rx="9" ry="4.5" fill="${H(95, 82, 0.55)}"/>
              <circle class="nv-hl1" cx="-5.5" cy="-4" r="5" fill="#fff"/>
              <circle cx="6.5" cy="9" r="2.3" fill="#fff" opacity="0.9"/>
              <circle class="nv-hl3" cx="-7" cy="10" r="1.4" fill="#fff" opacity="0.8"/>
            </g>
            <rect x="-30" y="-26" width="60" height="16" fill="${url("lidshade")}"/>
          </g>
          <path d="${lashD}" fill="#1c1630"/>
          <path d="M -18 15.5 Q -6 21 9 18.5" fill="none" stroke="#3a2a3e" stroke-width="1.3" stroke-linecap="round" opacity="0.55"/>
        </g>
        <path class="nv-eye-closed" d="M -26 3 Q -1 14 24 1" fill="none" stroke="#1c1630" stroke-width="3.2" stroke-linecap="round" opacity="0"/>
        <path class="nv-eye-happy" d="M -24 7 Q -1 -12 23 5" fill="none" stroke="#1c1630" stroke-width="3.4" stroke-linecap="round" opacity="0"/>
      </g>`;

    return `
<svg viewBox="0 0 400 520" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
  <defs>
    <radialGradient id="${id("iris")}" cx="50%" cy="62%" r="62%">
      <stop offset="0%" style="stop-color:${H(95, 80)}"/>
      <stop offset="42%" style="stop-color:${H(88, 58)}"/>
      <stop offset="80%" style="stop-color:${H(82, 34)}"/>
      <stop offset="100%" style="stop-color:${H(75, 20)}"/>
    </radialGradient>
    <linearGradient id="${id("lidshade")}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#2a1d3a" stop-opacity="0.75"/>
      <stop offset="100%" stop-color="#2a1d3a" stop-opacity="0"/>
    </linearGradient>
    <linearGradient id="${id("skin")}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#f1c3b3"/>
      <stop offset="40%" stop-color="#f6d0c1"/>
      <stop offset="50%" stop-color="#ffe5d8"/>
      <stop offset="100%" stop-color="#ffe9df"/>
    </linearGradient>
    <linearGradient id="${id("hair")}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#2d2756"/>
      <stop offset="55%" stop-color="#363374"/>
      <stop offset="100%" stop-color="#5463c9"/>
    </linearGradient>
    <linearGradient id="${id("hairfront")}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#2a2452"/>
      <stop offset="70%" stop-color="#3a3880"/>
      <stop offset="100%" stop-color="#4b56b4"/>
    </linearGradient>
    <linearGradient id="${id("jacket")}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#1b2230"/>
      <stop offset="100%" stop-color="#0e131b"/>
    </linearGradient>
    <clipPath id="${id("eyeclip")}"><path d="${scleraD}"/></clipPath>
    <clipPath id="${id("mouthclip")}"><path class="nv-mouth-clip" d="M -10 0 Q 0 4 10 0 Z"/></clipPath>
    <filter id="${id("glow")}" x="-60%" y="-60%" width="220%" height="220%">
      <feGaussianBlur stdDeviation="2.4" result="b"/>
      <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
    <radialGradient id="${id("halo")}" cx="50%" cy="50%" r="50%">
      <stop offset="0%" style="stop-color:${H(85, 60, 0.32)}"/>
      <stop offset="60%" style="stop-color:${H(85, 50, 0.08)}"/>
      <stop offset="100%" style="stop-color:${H(85, 50, 0)}"/>
    </radialGradient>
  </defs>

  <ellipse cx="200" cy="230" rx="190" ry="210" fill="${url("halo")}"/>

  <!-- holographic HUD halo (replaces the old standalone orb) -->
  <g class="nv-hud" transform="translate(200 200)">
    <circle class="nv-ring nv-ring1" r="150" fill="none" stroke="${H(75, 65, 0.42)}" stroke-width="1.4" stroke-dasharray="7 8"/>
    <circle class="nv-ring nv-ring2" r="168" fill="none" stroke="${H(75, 65, 0.32)}" stroke-width="1.6" stroke-dasharray="0.5 10" stroke-linecap="round"/>
    <g class="nv-orbit nv-orbit1"><circle r="160" fill="none"/><circle cx="160" cy="0" r="2.8" fill="${H(90, 74)}" filter="${url("glow")}"/></g>
    <g class="nv-orbit nv-orbit2"><circle r="176" fill="none"/><circle cx="-176" cy="0" r="2.1" fill="${H(90, 74)}" filter="${url("glow")}"/></g>
    <g class="nv-orbit nv-orbit3"><circle r="140" fill="none"/><circle cx="0" cy="140" r="2.4" fill="${H(90, 74)}" filter="${url("glow")}"/></g>
    <g class="nv-orbit nv-orbit4"><circle r="186" fill="none"/><circle cx="0" cy="-186" r="1.8" fill="${H(90, 74)}" opacity="0.7"/></g>
    <circle class="nv-burst" r="150" fill="none" stroke="${H(85, 65)}" stroke-width="3" opacity="0"/>
  </g>

  <g class="nv-root">
    <!-- back hair (drawn behind the body; follows the head with lag) -->
    <g class="nv-hairback">
      <path fill="${url("hair")}" d="M 200 58 C 144 58, 106 100, 102 162 C 96 242, 92 330, 78 442
        L 98 454 L 107 440 L 121 461 L 132 442 L 146 464 L 157 442 L 168 454
        C 173 400, 176 360, 178 330 L 222 330 C 224 360, 227 400, 232 454
        L 243 442 L 254 464 L 268 442 L 279 461 L 293 440 L 302 454 L 318 442
        C 308 330, 304 242, 298 162 C 294 100, 256 58, 200 58 Z"/>
      <path fill="#1a1636" d="M 162 236 C 170 230, 230 230, 238 236 L 244 400 L 156 400 Z"/>
    </g>

    <!-- body -->
    <g class="nv-body">
      <path fill="#ffe3d5" d="M 183 258 L 183 352 Q 200 362 217 352 L 217 258 Z"/>
      <path fill="#ecb9a6" opacity="0.9" d="M 183 276 Q 200 302 217 276 L 217 296 Q 200 318 183 296 Z"/>
      <path fill="${url("jacket")}" stroke="#0a0e15" stroke-width="2"
        d="M 50 520 C 54 440, 98 386, 160 362 C 171 358, 181 353, 185 346 L 215 346 C 219 353, 229 358, 240 362 C 302 386, 346 440, 350 520 Z"/>
      <path fill="#ffe3d5" d="M 176 343 L 200 410 L 224 343 Z"/>
      <path fill="none" stroke="#e6b3a1" stroke-width="1.4" stroke-linecap="round" d="M 186 372 Q 192 376 196 374 M 214 372 Q 208 376 204 374"/>
      <path fill="#e4eaf2" d="M 189 386 L 200 412 L 211 386 L 222 420 L 178 420 Z"/>
      <path fill="#1f2838" stroke="#0a0e15" stroke-width="1.5" d="M 160 362 L 185 346 L 201 420 L 176 450 Z"/>
      <path fill="#1f2838" stroke="#0a0e15" stroke-width="1.5" d="M 240 362 L 215 346 L 199 420 L 224 450 Z"/>
      <path fill="none" stroke="${H(90, 62)}" stroke-width="2" filter="${url("glow")}" stroke-linejoin="round" d="M 185 346 L 200 418 L 215 346"/>
      <path fill="none" stroke="${H(85, 60, 0.55)}" stroke-width="1.6" filter="${url("glow")}" d="M 92 430 C 112 402, 136 384, 160 372 M 308 430 C 288 402, 264 384, 240 372"/>
      <path fill="${H(95, 70)}" filter="${url("glow")}" d="M 200 424 L 205 431 L 200 438 L 195 431 Z"/>
      <path fill="none" stroke="${H(85, 60, 0.8)}" stroke-width="1.6" filter="${url("glow")}" d="M 262 444 l 9 -5 l 9 5 l 0 10 l -9 5 l -9 -5 Z"/>
      <rect x="182.5" y="318" width="35" height="7" rx="3" fill="#1b2130"/>
      <circle cx="200" cy="321.5" r="3" fill="${H(95, 70)}" filter="${url("glow")}"/>
    </g>

    <!-- head -->
    <g class="nv-head">
      <path fill="${url("skin")}" stroke="#c98d7c" stroke-width="1.6"
        d="M 124 172 C 122 230, 146 272, 174 293 C 185 301, 194 306, 200 306 C 206 306, 215 301, 226 293 C 254 272, 278 230, 276 172 C 274 114, 240 86, 200 86 C 160 86, 126 114, 124 172 Z"/>
      <path fill="#ffe3d5" stroke="#c98d7c" stroke-width="1.4" d="M 274 194 C 287 188, 292 212, 279 228 C 276 231, 273 229, 273 224 Z"/>

      <g class="nv-blush">
        <ellipse cx="151" cy="242" rx="17" ry="7.5" fill="#ff7d99"/>
        <ellipse cx="249" cy="242" rx="17" ry="7.5" fill="#ff7d99"/>
      </g>
      <g class="nv-fx nv-fx-blushlines" stroke="#e8577a" stroke-width="1.6" stroke-linecap="round">
        <path d="M 142 246 l 5 -8 M 150 246 l 5 -8 M 158 246 l 5 -8"/>
        <path d="M 240 246 l 5 -8 M 248 246 l 5 -8 M 256 246 l 5 -8"/>
      </g>

      <path fill="none" stroke="#d99a86" stroke-width="1.6" stroke-linecap="round" d="M 200 239 Q 203 245 198 248"/>

      <g class="nv-mouth" transform="translate(200 268)">
        <path class="nv-mouth-fill" d="M -10 0 Q 0 4 10 0 Z" fill="#7d2633" stroke="#4e1820" stroke-width="1.5" stroke-linejoin="round"/>
        <g clip-path="${url("mouthclip")}">
          <ellipse class="nv-tongue" cx="0" cy="10" rx="8" ry="5" fill="#e86f82"/>
          <rect class="nv-teeth" x="-20" y="-12" width="40" height="0" fill="#fff"/>
        </g>
        <path class="nv-mouth-line" d="M -10 0 Q 0 4 10 0" fill="none" stroke="#4e1820" stroke-width="2.2" stroke-linecap="round"/>
      </g>

      <g class="nv-eyeL" transform="translate(166 207)">${eye("l")}</g>
      <g class="nv-eyeR" transform="translate(234 207) scale(-1 1)">${eye("r")}</g>

      <g class="nv-glasses" fill="none">
        <rect x="134" y="183" width="63" height="47" rx="17" fill="rgba(200,230,255,0.07)" stroke="#2b2f3d" stroke-width="2.8"/>
        <rect x="203" y="183" width="63" height="47" rx="17" fill="rgba(200,230,255,0.07)" stroke="#2b2f3d" stroke-width="2.8"/>
        <path d="M 197 200 Q 200 196 203 200" stroke="#2b2f3d" stroke-width="2.6"/>
        <path d="M 266 198 L 278 194 M 134 198 L 122 194" stroke="#2b2f3d" stroke-width="2.4" stroke-linecap="round"/>
        <path d="M 144 194 l 10 -6 M 213 194 l 10 -6" stroke="#ffffff" stroke-width="2.2" stroke-linecap="round" opacity="0.6"/>
      </g>

      <!-- side locks (pivot near the temples, sway on a spring) -->
      <g class="nv-lockL">
        <path fill="${url("hairfront")}" stroke="#1d1940" stroke-width="1.2"
          d="M 128 138 C 114 190, 106 250, 108 330 C 109 360, 114 384, 122 404 C 124 384, 127 374, 131 364 C 133 378, 137 386, 142 392 C 139 342, 134 282, 133 222 C 133 190, 137 162, 142 138 Z"/>
      </g>
      <g class="nv-lockR">
        <path fill="${url("hairfront")}" stroke="#1d1940" stroke-width="1.2"
          d="M 272 138 C 286 190, 294 250, 292 330 C 291 360, 286 384, 278 404 C 276 384, 273 374, 269 364 C 267 378, 263 386, 258 392 C 261 342, 266 282, 267 222 C 267 190, 263 162, 258 138 Z"/>
      </g>

      <!-- headset -->
      <path fill="none" stroke="#202838" stroke-width="6" stroke-linecap="round" d="M 117 180 C 111 104, 156 63, 200 60 C 244 63, 289 104, 283 180"/>

      <!-- bangs -->
      <path fill="${url("hairfront")}" stroke="#1d1940" stroke-width="1.4" stroke-linejoin="round"
        d="M 118 190 C 111 118, 150 66, 200 64 C 250 66, 289 118, 282 190
           Q 276 170, 268 150 Q 268 176, 262 194
           Q 252 166, 239 140 Q 241 170, 231 193
           Q 222 160, 211 136 Q 211 166, 199 189
           Q 190 158, 186 138 Q 182 166, 169 193
           Q 161 162, 152 142 Q 146 170, 138 194
           Q 132 174, 132 150 Q 124 170, 118 190 Z"/>
      <path fill="none" stroke="rgba(178,206,255,0.55)" stroke-width="6" stroke-linecap="round" stroke-dasharray="20 9" d="M 140 112 Q 200 88 260 112"/>
      <path fill="none" stroke="rgba(120,140,230,0.35)" stroke-width="1.4" d="M 199 189 Q 197 150, 200 92 M 229 193 Q 228 150, 222 98 M 171 191 Q 172 150, 178 98"/>

      <g class="nv-brows" fill="none" stroke="#2a2350" stroke-width="2.7" stroke-linecap="round" opacity="0.85">
        <g class="nv-browL" transform="translate(166 207)"><path class="nv-brow" d="M -22 -34 Q -4 -42 18 -36"/></g>
        <g class="nv-browR" transform="translate(234 207) scale(-1 1)"><path class="nv-brow" d="M -22 -34 Q -4 -42 18 -36"/></g>
      </g>

      <g class="nv-ahoge"><path fill="${url("hairfront")}" stroke="#1d1940" stroke-width="1.2" d="M 197 72 C 194 48, 210 28, 234 34 C 219 38, 208 51, 206 72 Z"/></g>

      <g class="nv-clip" filter="${url("glow")}">
        <path d="M 231 116 L 246 110 L 243 125 Z" fill="none" stroke="${H(90, 70)}" stroke-width="1.6"/>
        <circle cx="231" cy="116" r="3.6" fill="${H(95, 75)}"/>
        <circle cx="246" cy="110" r="3.6" fill="${H(95, 75)}"/>
        <circle cx="243" cy="125" r="3.6" fill="${H(95, 75)}"/>
      </g>

      <!-- ear cups + mic boom -->
      <g class="nv-cups">
        <circle cx="114" cy="208" r="16" fill="#1c2333" stroke="#0b0f17" stroke-width="2"/>
        <circle cx="114" cy="208" r="10" fill="none" stroke="${H(90, 62)}" stroke-width="2.6" filter="${url("glow")}"/>
        <circle class="nv-cuplight" cx="114" cy="208" r="4" fill="${H(95, 72)}" filter="${url("glow")}"/>
        <circle cx="286" cy="208" r="16" fill="#1c2333" stroke="#0b0f17" stroke-width="2"/>
        <circle cx="286" cy="208" r="10" fill="none" stroke="${H(90, 62)}" stroke-width="2.6" filter="${url("glow")}"/>
        <circle class="nv-cuplight" cx="286" cy="208" r="4" fill="${H(95, 72)}" filter="${url("glow")}"/>
        <path fill="none" stroke="#202838" stroke-width="3.6" stroke-linecap="round" d="M 120 222 C 126 254, 146 274, 172 279"/>
        <rect x="170" y="275" width="11" height="8" rx="4" fill="#202838"/>
        <circle class="nv-miclight" cx="175.5" cy="279" r="1.8" fill="${H(95, 72)}"/>
      </g>

      <g class="nv-fx nv-fx-sweat">
        <path d="M 270 140 C 275 150, 279 156, 270 163 C 261 156, 265 150, 270 140 Z" fill="#a8dcff" stroke="#5aa6dc" stroke-width="1.4"/>
        <path d="M 267 152 q -1 4 1 7" stroke="#fff" stroke-width="1.4" fill="none" stroke-linecap="round"/>
      </g>
      <g class="nv-fx nv-fx-annoyed" stroke="#ff4d64" stroke-width="3.2" stroke-linecap="round" fill="none" transform="translate(262 124)">
        <path d="M -9 -3 Q -3 -3 -3 -9 M 3 -9 Q 3 -3 9 -3 M 9 3 Q 3 3 3 9 M -3 9 Q -3 3 -9 3"/>
      </g>
    </g>

    <!-- effects that float independently of the head -->
    <g class="nv-fx nv-fx-sparkle">
      <g transform="translate(96 132)"><path d="M 0 -11 L 2.6 -2.6 L 11 0 L 2.6 2.6 L 0 11 L -2.6 2.6 L -11 0 L -2.6 -2.6 Z" fill="#fff" filter="${url("glow")}"/></g>
      <g transform="translate(312 176)"><path d="M 0 -9 L 2.2 -2.2 L 9 0 L 2.2 2.2 L 0 9 L -2.2 2.2 L -9 0 L -2.2 -2.2 Z" fill="${H(95, 82)}" filter="${url("glow")}"/></g>
      <g transform="translate(292 78)"><path d="M 0 -12 L 2.8 -2.8 L 12 0 L 2.8 2.8 L 0 12 L -2.8 2.8 L -12 0 L -2.8 -2.8 Z" fill="#fff" filter="${url("glow")}"/></g>
      <g transform="translate(108 258)"><path d="M 0 -7 L 1.8 -1.8 L 7 0 L 1.8 1.8 L 0 7 L -1.8 1.8 L -7 0 L -1.8 -1.8 Z" fill="${H(95, 82)}" filter="${url("glow")}"/></g>
    </g>
    <g class="nv-fx nv-fx-hearts" fill="#ff6b92" stroke="#e0436e" stroke-width="1.2">
      <g transform="translate(298 150) scale(1.35)"><path d="M 0 4 C -7 -4, -15 3, 0 13 C 15 3, 7 -4, 0 4 Z"/></g>
      <g transform="translate(102 170) scale(1.35)"><path d="M 0 3 C -5 -3, -11 2, 0 10 C 11 2, 5 -3, 0 3 Z"/></g>
      <g transform="translate(286 96) scale(1.35)"><path d="M 0 3 C -5 -3, -11 2, 0 10 C 11 2, 5 -3, 0 3 Z"/></g>
    </g>
    <g class="nv-fx nv-fx-zzz" font-family="var(--font-display, sans-serif)" font-weight="700" fill="${H(80, 78)}">
      <text x="276" y="116" font-size="15">z</text>
      <text x="292" y="94" font-size="19">z</text>
      <text x="310" y="70" font-size="24">z</text>
    </g>
    <g class="nv-fx nv-fx-think">
      <circle cx="278" cy="140" r="4" fill="${H(70, 75, 0.35)}"/>
      <circle cx="290" cy="124" r="6.5" fill="${H(70, 75, 0.35)}"/>
      <ellipse cx="322" cy="96" rx="30" ry="20" fill="${H(60, 18, 0.65)}" stroke="${H(85, 65, 0.8)}" stroke-width="1.6"/>
      <circle class="nv-dot" cx="310" cy="96" r="3.4" fill="${H(90, 80)}"/>
      <circle class="nv-dot" cx="322" cy="96" r="3.4" fill="${H(90, 80)}"/>
      <circle class="nv-dot" cx="334" cy="96" r="3.4" fill="${H(90, 80)}"/>
    </g>
    <g class="nv-fx nv-fx-waves" fill="none" stroke="${H(90, 68)}" stroke-width="2.4" stroke-linecap="round" filter="${url("glow")}">
      <path d="M 90 196 Q 82 208 90 220"/>
      <path d="M 80 188 Q 68 208 80 228"/>
      <path d="M 70 180 Q 54 208 70 236"/>
    </g>
    <g class="nv-fx nv-fx-bang">
      <path d="M 300 60 L 312 60 L 309 96 L 303 96 Z" fill="${H(95, 70)}" stroke="#0b0f17" stroke-width="1.5"/>
      <circle cx="306" cy="106" r="5" fill="${H(95, 70)}" stroke="#0b0f17" stroke-width="1.5"/>
    </g>
  </g>
</svg>`;
  }

  // ---------------------------------------------------------------------

  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const rand = (a, b) => a + Math.random() * (b - a);
  const approach = (cur, target, speed, dt) => cur + (target - cur) * (1 - Math.exp(-speed * dt));

  let instanceCounter = 0;

  class NeuralAvatar {
    constructor(container, opts = {}) {
      this.opts = opts;
      this.u = ++instanceCounter;
      if (!document.getElementById("nv-style")) {
        const st = document.createElement("style");
        st.id = "nv-style";
        st.textContent = STYLE;
        document.head.appendChild(st);
      }
      this.wrap = document.createElement("div");
      this.wrap.className = "nv-wrap";
      this.wrap.innerHTML = buildSVG(this.u) +
        '<div class="nv-chip" role="status" aria-live="polite"><svg viewBox="0 0 24 24"></svg><span></span></div>';
      container.appendChild(this.wrap);
      this.svg = this.wrap.querySelector("svg");
      this.chip = this.wrap.querySelector(".nv-chip");

      const q = (s) => this.svg.querySelector(s);
      this.el = {
        root: q(".nv-root"), head: q(".nv-head"), hairback: q(".nv-hairback"), body: q(".nv-body"),
        lockL: q(".nv-lockL"), lockR: q(".nv-lockR"), ahoge: q(".nv-ahoge"),
        eyeL: q(".nv-eyeL"), eyeR: q(".nv-eyeR"), browL: q(".nv-browL"), browR: q(".nv-browR"),
        blush: q(".nv-blush"), mouthFill: q(".nv-mouth-fill"), mouthLine: q(".nv-mouth-line"),
        mouthClip: q(".nv-mouth-clip"), tongue: q(".nv-tongue"), teeth: q(".nv-teeth"),
        cupLights: this.svg.querySelectorAll(".nv-cuplight"), micLight: q(".nv-miclight"),
      };
      this.eyes = ["l", "r"].map((s) => {
        const g = q(".nv-eye-" + s);
        return {
          open: g.querySelector(".nv-eye-open"), closed: g.querySelector(".nv-eye-closed"),
          happy: g.querySelector(".nv-eye-happy"), iris: g.querySelector(".nv-iris"),
          pupil: g.querySelector(".nv-pupil"), mirror: s === "r",
        };
      });

      this.state = "idle";
      this.svg.classList.add("st-idle");
      this.mode = "chat";
      this.mood = "neutral";
      this.moodUntil = 0;
      this.react = null;
      this.reactUntil = 0;
      this.talking = false;
      this.analyser = null;
      this.audioBuf = null;
      this.level = 0;
      this.lastAudioSignal = 0;
      this.pointer = null;
      this.lastPointerMove = -1e9;
      this.glance = { x: 0, y: 0, until: 0 };
      this.blink = { next: performance.now() / 1000 + rand(1.5, 4), t0: -1, double: false };
      this.wink = { t0: -1 };
      this.fx = "";
      this.reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

      // Smoothed parameters.
      this.p = {
        headRot: 0, headX: 0, headY: 0, lookX: 0, lookY: 0, eyeOpen: 1, eyeScale: 1,
        browY: 0, browTilt: 0, browAsym: 0, smile: 0.25, mouthOpen: 0, mouthW: 1,
        blush: 0.18, lean: 0, irisScale: 1, lockL: 0, lockR: 0, hairRot: 0, ahoge: 0,
        squish: 0, sideMouth: 0,
      };
      this.v = { lockL: 0, lockR: 0, ahoge: 0, hairRot: 0 };

      this._onPointer = (e) => {
        this.pointer = { x: e.clientX, y: e.clientY };
        this.lastPointerMove = performance.now() / 1000;
      };
      window.addEventListener("pointermove", this._onPointer, { passive: true });
      this.svg.addEventListener("click", (e) => this._onClick(e));

      this._last = performance.now();
      this._raf = requestAnimationFrame((t) => this._frame(t));
    }

    // ---------------- public API ----------------

    setState(state) {
      if (state === this.state) return;
      const prev = this.state;
      this.state = state;
      this.wrap.classList.toggle("nv-offline", state === "offline");
      for (const st of ["idle", "listening", "thinking", "speaking", "offline"]) {
        this.svg.classList.toggle("st-" + st, st === state);
      }
      // A reply's mood lingers a little after she finishes talking instead
      // of snapping back to neutral the instant the audio ends.
      if (prev === "speaking" && state !== "speaking" && this.mood !== "neutral") {
        this.moodUntil = Math.max(this.moodUntil, performance.now() / 1000 + 6);
      }
      if (state === "listening" || state === "thinking") this._kickAhoge(1.2);
    }

    setMood(mood, holdSeconds) {
      if (!MOODS.includes(mood)) mood = "neutral";
      this.mood = mood;
      this.moodUntil = performance.now() / 1000 + (holdSeconds || 14);
      if (mood === "surprised" || mood === "excited") this._kickAhoge(2.4);
      if (mood === "playful") this.wink.t0 = performance.now() / 1000 + 0.4;
    }

    setMode(mode) {
      this.mode = mode;
      this.svg.classList.toggle("mode-tutor", mode === "tutor");
      this._kickAhoge(1.5);
    }

    // Ring burst + ahoge bounce -- used when switching Chat/Tutor.
    flourish() {
      this.svg.classList.remove("nv-flourish");
      void this.svg.getBoundingClientRect();
      this.svg.classList.add("nv-flourish");
      clearTimeout(this._flourishTimer);
      this._flourishTimer = setTimeout(() => this.svg.classList.remove("nv-flourish"), 950);
      this._kickAhoge(2.5);
    }

    setAnalyser(node) {
      this.analyser = node || null;
      this.audioBuf = node ? new Uint8Array(node.fftSize) : null;
    }

    setTalking(on) { this.talking = !!on; }

    // Gaze target in this window's client coordinates -- for hosts that
    // know where the cursor is even when it's outside the page (Neural
    // Desktop feeds the global cursor position here). Same effect as a
    // pointermove inside the page; pass null to let her glance around.
    lookAt(x, y) {
      if (x == null || y == null) {
        this.lastPointerMove = -1e9;
        return;
      }
      this.pointer = { x, y };
      this.lastPointerMove = performance.now() / 1000;
    }

    setActivity(label, kind) {
      if (!label) {
        this.chip.classList.remove("show");
        return;
      }
      this.chip.querySelector("svg").innerHTML = ICONS[kind] || ICONS.gear;
      this.chip.querySelector("span").textContent = label;
      this.chip.classList.add("show");
    }

    reactTo(kind, seconds) {
      this.react = kind;
      this.reactUntil = performance.now() / 1000 + (seconds || 2.2);
      this._kickAhoge(2);
    }

    destroy() {
      cancelAnimationFrame(this._raf);
      window.removeEventListener("pointermove", this._onPointer);
      this.wrap.remove();
    }

    // ---------------- internals ----------------

    _kickAhoge(strength) { this.v.ahoge += (Math.random() < 0.5 ? -1 : 1) * 40 * strength; }

    _onClick(e) {
      const pt = this.svg.createSVGPoint();
      pt.x = e.clientX;
      pt.y = e.clientY;
      const ctm = this.svg.getScreenCTM();
      const y = ctm ? pt.matrixTransform(ctm.inverse()).y : 0;
      if (this.state === "offline") { this.reactTo("curious", 1.5); return; }
      if (y < 240) this.reactTo("headpat", 2.6);
      else this.reactTo("curious", 1.6);
    }

    _audioLevel() {
      if (!this.analyser || !this.audioBuf) return 0;
      try {
        this.analyser.getByteTimeDomainData(this.audioBuf);
      } catch (e) {
        return 0;
      }
      let sum = 0;
      for (let i = 0; i < this.audioBuf.length; i++) {
        const d = (this.audioBuf[i] - 128) / 128;
        sum += d * d;
      }
      const rms = Math.sqrt(sum / this.audioBuf.length);
      // Auto-gain: normalize against a slowly decaying recent peak so the
      // mouth moves through its full range whether the voice is a loud
      // ElevenLabs clip or the much quieter local fallback engine.
      this.peak = Math.max(rms, (this.peak || 0.06) * 0.993, 0.04);
      return clamp((rms - 0.01) / (this.peak * 0.8), 0, 1);
    }

    _currentMood(now) {
      if (this.react && now < this.reactUntil) return this.react;
      if (this.mood !== "neutral" && now > this.moodUntil && this.state !== "speaking") this.mood = "neutral";
      return this.mood;
    }

    _targets(now) {
      const T = {
        headRot: 0, headX: 0, headY: 0, lookX: 0, lookY: 0, eyeOpen: 1, eyeScale: 1,
        browY: 0, browTilt: 0, browAsym: 0, smile: 0.25, mouthOpen: 0, mouthW: 1,
        blush: 0.18, lean: 0, irisScale: 1, squish: 0, sideMouth: 0,
      };
      const fx = new Set();
      let eyeMode = "open"; // open | closed | happy
      let mouthShape = "normal"; // normal | o | cat

      // --- where she's looking: cursor > idle glances ---
      const pointerFresh = this.pointer && now - this.lastPointerMove < 3.5;
      if (pointerFresh) {
        const r = this.svg.getBoundingClientRect();
        const cx = r.left + r.width * 0.5;
        const cy = r.top + r.height * 0.4;
        const sx = Math.max(window.innerWidth * 0.45, 240);
        const sy = Math.max(window.innerHeight * 0.45, 240);
        T.lookX = clamp(((this.pointer.x - cx) / sx) * 1.3, -1, 1);
        T.lookY = clamp(((this.pointer.y - cy) / sy) * 1.3, -1, 1);
      } else if (!(this.opts.debug && this.opts.debug.noGlance)) {
        if (now > this.glance.until) {
          const center = Math.random() < 0.55;
          this.glance = {
            x: center ? rand(-0.15, 0.15) : rand(-0.8, 0.8),
            y: center ? rand(-0.1, 0.1) : rand(-0.5, 0.45),
            until: now + rand(1.4, 4.2),
          };
        }
        T.lookX = this.glance.x;
        T.lookY = this.glance.y;
      }
      T.headRot = T.lookX * 3.2;
      T.headX = T.lookX * 3;
      T.headY = T.lookY * 2;

      // --- state ---
      const st = this.state;
      if (st === "offline") {
        eyeMode = "closed";
        T.headRot = 7;
        T.headY = 5;
        T.smile = 0.1;
        T.lookX = 0;
        T.lookY = 0;
        fx.add("zzz");
      } else if (st === "listening") {
        T.eyeScale = 1.08;
        T.browY = -3;
        T.lean = 1;
        T.lookX *= 0.3;
        T.lookY = T.lookY * 0.3 - 0.05;
        T.headRot = -4 + T.lookX * 2;
        T.smile = 0.35;
        T.mouthOpen = 0.04;
        fx.add("waves");
      } else if (st === "thinking") {
        T.lookX = 0.55;
        T.lookY = -0.8;
        T.headRot = 5;
        T.headY = -1;
        T.browAsym = 3;
        T.smile = 0;
        T.sideMouth = 1;
        fx.add("think");
      } else if (st === "speaking") {
        T.lookX *= 0.35;
        T.lookY *= 0.35;
        T.smile = 0.35;
      }

      // --- mood (on top of state) ---
      const mood = st === "offline" ? "neutral" : this._currentMood(now);
      switch (mood) {
        case "happy":
          T.smile = 0.95; T.eyeOpen = 0.82; T.blush = 0.38; T.browY -= 1;
          if (st !== "speaking") eyeMode = "happy";
          fx.add("sparkle");
          break;
        case "excited":
          T.smile = 1; T.eyeScale = 1.08; T.browY -= 3; T.blush = 0.35; T.headY -= 2;
          fx.add("sparkle");
          break;
        case "playful":
          T.smile = 0.7; mouthShape = "cat"; T.headRot += 4; T.browAsym = -3; T.blush = 0.25;
          break;
        case "proud":
          T.smile = 0.75; T.eyeOpen = 0.62; T.headRot -= 3; T.headY -= 2; T.browY -= 1;
          fx.add("sparkle");
          break;
        case "surprised":
          T.eyeScale = 1.14; T.irisScale = 0.82; T.browY = -6; mouthShape = "o";
          T.mouthOpen = Math.max(T.mouthOpen, 0.45); T.smile = 0;
          fx.add("bang");
          break;
        case "curious":
          T.headRot += 8; T.eyeScale = 1.05; T.browAsym = 4; T.smile = 0.2; mouthShape = "o";
          T.mouthOpen = Math.max(T.mouthOpen, 0.16);
          break;
        case "worried":
          T.browTilt = 14; T.smile = -0.3; T.browY += 1; T.eyeOpen = 0.92;
          fx.add("sweat");
          break;
        case "sad":
          T.browTilt = 16; T.smile = -0.65; T.eyeOpen = 0.72; T.lookY = Math.max(T.lookY, 0.45);
          T.headRot += 3; T.headY += 3;
          break;
        case "embarrassed":
          T.blush = 1; T.lookX = -0.65; T.lookY = 0.35; T.smile = 0.2; T.browTilt = 5;
          fx.add("blushlines"); fx.add("sweat");
          break;
        case "affectionate":
        case "headpat":
          eyeMode = "happy"; T.smile = 1; T.blush = 0.7; T.headRot += 6;
          fx.add("hearts"); fx.add("blushlines");
          if (mood === "headpat") T.squish = 1;
          break;
        case "annoyed":
          T.browTilt = -14; T.smile = -0.35; T.eyeOpen = 0.78; T.blush = 0.3;
          fx.add("annoyed");
          break;
        default:
          break;
      }

      // --- lip sync ---
      if (st === "speaking" || this.talking) {
        let lvl = this._audioLevel();
        if (lvl > 0.03) this.lastAudioSignal = now;
        // No analyser, or nothing measurable on it for a while even though
        // she's supposed to be talking: fall back to a believable flap.
        if ((!this.analyser || now - this.lastAudioSignal > 1.2) && (this.talking || st === "speaking")) {
          if (this.talking || !this.analyser) {
            lvl = Math.max(0, 0.42 + 0.38 * Math.sin(now * 15.3) * Math.sin(now * 3.7 + 1.1));
          }
        }
        this.level = lvl;
        if (mouthShape === "normal" || lvl > 0.1) {
          T.mouthOpen = Math.max(T.mouthOpen, lvl);
          T.mouthW = 1 + lvl * 0.12;
          if (mouthShape === "cat" && lvl > 0.1) mouthShape = "normal";
        }
        T.headY += lvl * 1.6;
        T.headRot += Math.sin(now * 2.6) * 1.1;
      } else {
        this.level = 0;
      }

      this.fxSet = fx;
      this.eyeMode = eyeMode;
      this.mouthShape = mouthShape;
      return T;
    }

    _blinkAmount(now) {
      if (this.opts.debug && this.opts.debug.noBlink) return 0;
      const b = this.blink;
      if (b.t0 < 0 && now >= b.next) {
        b.t0 = now;
        b.double = Math.random() < 0.18;
      }
      if (b.t0 >= 0) {
        const dur = 0.17;
        const e = now - b.t0;
        if (e >= dur) {
          if (b.double) {
            b.double = false;
            b.t0 = now + 0.08;
            return 0;
          }
          b.t0 = -1;
          b.next = now + rand(2.2, 5.6);
          return 0;
        }
        if (e < 0) return 0;
        return Math.sin((e / dur) * Math.PI);
      }
      return 0;
    }

    _winkAmount(now) {
      const w = this.wink;
      if (w.t0 < 0) return 0;
      const e = now - w.t0;
      if (e < 0) return 0;
      if (e > 0.55) { w.t0 = -1; return 0; }
      return Math.sin(Math.min(1, e / 0.55) * Math.PI);
    }

    _mouthPaths() {
      const p = this.p;
      const open = clamp(p.mouthOpen, 0, 1);
      const smile = clamp(p.smile, -1, 1);
      const w = 12.5 * p.mouthW * (1 + Math.max(0, smile) * 0.25);
      const side = p.sideMouth * 4;
      const cy = -smile * 4.2;
      const shape = this.mouthShape;

      if (shape === "cat" && open < 0.12) {
        const d = `M ${-w - 2} ${cy - 1} Q ${-w / 2} ${cy + 5} 0 ${cy} Q ${w / 2} ${cy + 5} ${w + 2} ${cy - 1}`;
        return { line: d, fill: null };
      }
      if (shape === "o") {
        const rx = 5.5 + open * 4;
        const ry = 4 + open * 9;
        const d = `M ${-rx} 2 A ${rx} ${ry} 0 1 0 ${rx} 2 A ${rx} ${ry} 0 1 0 ${-rx} 2 Z`;
        return { line: null, fill: d, tongueY: 2 + ry * 0.55, tongueRx: rx * 0.6, tongueRy: ry * 0.35 };
      }
      if (open < 0.06) {
        const d = `M ${-w + side} ${cy} Q ${side * 0.5} ${smile * 6 + 1} ${w + side} ${cy + p.sideMouth * -1.5}`;
        return { line: d, fill: null };
      }
      const upperY = cy - 1 + Math.min(0, smile) * 1.5 - open * 1.5;
      const lowerY = cy + 3 + open * 17 + Math.max(0, smile) * 4;
      const d =
        `M ${-w + side} ${cy} Q ${side * 0.5} ${upperY} ${w + side} ${cy}` +
        ` Q ${w * 0.6 + side} ${lowerY} ${side * 0.5} ${lowerY} Q ${-w * 0.6 + side} ${lowerY} ${-w + side} ${cy} Z`;
      return { line: null, fill: d, tongueY: lowerY - 3.5, tongueRx: w * 0.55, tongueRy: 2 + open * 4 };
    }

    _frame(ms) {
      this._raf = requestAnimationFrame((t) => this._frame(t));
      const dt = clamp((ms - this._last) / 1000, 0, 0.06);
      this._last = ms;
      const now = ms / 1000;
      const amp = this.reduced ? 0.35 : 1;

      const T = this._targets(now);
      const p = this.p;
      const speeds = {
        lookX: 9, lookY: 9, headRot: 3.2, headX: 3.2, headY: 3.4, mouthOpen: T.mouthOpen > p.mouthOpen ? 32 : 15,
      };
      for (const k of Object.keys(T)) p[k] = approach(p[k], T[k], speeds[k] || 6, dt);

      // Springs: side locks + back hair lag behind the head, ahoge wobbles.
      const spring = (key, target, k, d) => {
        const a = (target - p[key]) * k - this.v[key] * d;
        this.v[key] += a * dt;
        p[key] += this.v[key] * dt;
      };
      const sway = Math.sin(now * 1.25) * 1.1 * amp;
      spring("lockL", -p.headRot * 0.8 + sway, 60, 7);
      spring("lockR", -p.headRot * 0.8 - sway * 0.8, 55, 7);
      spring("hairRot", p.headRot * 0.85, 40, 9);
      spring("ahoge", Math.sin(now * 0.9) * 4 * amp - p.headRot * 1.5, 90, 6);

      // --- breathing / body ---
      const breathRate = this.state === "speaking" ? 2.1 : this.state === "offline" ? 5.4 : 4.2;
      const breath = Math.sin((now * 2 * Math.PI) / breathRate) * 1.7 * amp;
      const squish = p.squish;
      const lean = p.lean;
      this.el.root.setAttribute(
        "transform",
        `translate(200 520) scale(${1 + lean * 0.035} ${1 + lean * 0.035 - squish * 0.025}) translate(-200 -520)`
      );
      this.el.body.setAttribute("transform", `translate(0 ${(-breath * 0.55).toFixed(2)})`);
      const hx = p.headX * amp;
      const hy = p.headY * amp - breath * 0.9 + squish * 3;
      const hr = p.headRot * amp;
      this.el.head.setAttribute("transform", `translate(${hx.toFixed(2)} ${hy.toFixed(2)}) rotate(${hr.toFixed(2)} 200 300)`);
      this.el.hairback.setAttribute(
        "transform",
        `translate(${(hx * 0.8).toFixed(2)} ${(hy * 0.9).toFixed(2)}) rotate(${(p.hairRot * amp).toFixed(2)} 200 300)`
      );
      this.el.lockL.setAttribute("transform", `rotate(${p.lockL.toFixed(2)} 136 144)`);
      this.el.lockR.setAttribute("transform", `rotate(${p.lockR.toFixed(2)} 264 144)`);
      this.el.ahoge.setAttribute("transform", `rotate(${clamp(p.ahoge, -35, 35).toFixed(2)} 202 72)`);

      // --- eyes ---
      const blink = this._blinkAmount(now);
      const wink = this._winkAmount(now);
      const mode = this.eyeMode;
      const lx = p.lookX * 6.5;
      const ly = p.lookY * 4.5;
      this.eyes.forEach((eye, i) => {
        const isR = i === 1;
        let open = p.eyeOpen * (1 - blink);
        if (isR) open *= 1 - wink;
        const showOpen = mode === "open" && open > 0.12;
        eye.open.setAttribute("opacity", showOpen ? 1 : 0);
        eye.closed.setAttribute("opacity", mode === "closed" || (mode === "open" && !showOpen) ? 1 : 0);
        eye.happy.setAttribute("opacity", mode === "happy" ? 1 : 0);
        if (showOpen) {
          const sy = clamp(open, 0.12, 1) * p.eyeScale;
          eye.open.setAttribute("transform", `translate(0 -8) scale(${p.eyeScale.toFixed(3)} ${sy.toFixed(3)}) translate(0 8)`);
          const ix = isR ? -lx : lx;
          eye.iris.setAttribute("transform", `translate(${ix.toFixed(2)} ${ly.toFixed(2)}) scale(${p.irisScale.toFixed(3)})`);
          const pupil = 1 + (this.state === "listening" ? 0.15 : 0) - (p.irisScale < 0.9 ? 0.3 : 0);
          eye.pupil.setAttribute("transform", `scale(${pupil.toFixed(3)})`);
        }
      });

      // --- brows ---
      const browFor = (asymSign) =>
        `translate(0 ${(p.browY + p.browAsym * asymSign).toFixed(2)}) rotate(${(-p.browTilt).toFixed(2)} -2 -37)`;
      this.el.browL.firstElementChild.setAttribute("transform", browFor(-1));
      this.el.browR.firstElementChild.setAttribute("transform", browFor(1));

      // --- mouth ---
      const m = this._mouthPaths();
      if (m.fill) {
        this.el.mouthFill.setAttribute("d", m.fill);
        this.el.mouthClip.setAttribute("d", m.fill);
        this.el.mouthFill.setAttribute("opacity", 1);
        this.el.mouthLine.setAttribute("opacity", 0);
        this.el.tongue.setAttribute("cy", m.tongueY.toFixed(2));
        this.el.tongue.setAttribute("rx", m.tongueRx.toFixed(2));
        this.el.tongue.setAttribute("ry", m.tongueRy.toFixed(2));
        const teeth = this.mouthShape === "normal" ? clamp((p.mouthOpen - 0.25) * 8, 0, 3.2) : 0;
        this.el.teeth.setAttribute("y", (-p.smile * 4.2 - 6).toFixed(2));
        this.el.teeth.setAttribute("height", (5 + teeth).toFixed(2));
        this.el.teeth.setAttribute("opacity", teeth > 0.2 ? 1 : 0);
      } else {
        this.el.mouthLine.setAttribute("d", m.line);
        this.el.mouthLine.setAttribute("opacity", 1);
        this.el.mouthFill.setAttribute("opacity", 0);
      }

      this.el.blush.setAttribute("opacity", clamp(p.blush, 0, 1).toFixed(3));

      // Headset lights pulse with her voice (and softly on their own).
      const glow = 0.55 + 0.45 * Math.max(this.level, (Math.sin(now * 2) + 1) * 0.25);
      this.el.cupLights.forEach((c) => c.setAttribute("opacity", glow.toFixed(3)));
      this.el.micLight.setAttribute("opacity", (0.4 + this.level * 0.6).toFixed(3));

      // --- effect layers ---
      const fxKey = [...this.fxSet].sort().join(" ");
      if (fxKey !== this.fx) {
        for (const name of ["sparkle", "hearts", "zzz", "think", "waves", "bang", "sweat", "blushlines", "annoyed"]) {
          this.svg.classList.toggle("fx-" + name, this.fxSet.has(name));
        }
        this.fx = fxKey;
      }
    }
  }

  NeuralAvatar.MOODS = MOODS;
  window.NeuralAvatar = NeuralAvatar;
})();
