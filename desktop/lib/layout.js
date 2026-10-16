"use strict";
/*
 * Window geometry. The app is ONE transparent window holding the avatar
 * column plus the space beside it where the reply bubble appears; the
 * empty transparent part is click-through, so it costs nothing to keep it
 * there. We persist the AVATAR's rectangle, and derive the window from it
 * plus which side the bubble goes on (whichever side has room on screen).
 */
const BUBBLE_W = 300;
const GAP = 6;
const MIN = { w: 150, h: 230 };
const MAX = { w: 520, h: 780 };
const DEFAULT = { w: 200, h: 300 };

function clamp(v, lo, hi) {
  return Math.max(lo, Math.min(hi, v));
}

function clampSize(w, h) {
  return { w: Math.round(clamp(w, MIN.w, MAX.w)), h: Math.round(clamp(h, MIN.h, MAX.h)) };
}

function defaultAvatar(workArea) {
  return {
    x: workArea.x + workArea.width - DEFAULT.w - 24,
    y: workArea.y + workArea.height - DEFAULT.h - 12,
    w: DEFAULT.w,
    h: DEFAULT.h,
  };
}

// Keep the whole avatar on screen (the bubble side is chosen to fit).
function clampAvatar(av, workArea) {
  const size = clampSize(av.w, av.h);
  return {
    x: Math.round(clamp(av.x, workArea.x, workArea.x + workArea.width - size.w)),
    y: Math.round(clamp(av.y, workArea.y, workArea.y + workArea.height - size.h)),
    w: size.w,
    h: size.h,
  };
}

function chooseSide(av, workArea) {
  const roomLeft = av.x - workArea.x;
  const roomRight = workArea.x + workArea.width - (av.x + av.w);
  const need = BUBBLE_W + GAP;
  if (roomLeft >= need && roomRight >= need) {
    // Both fit: put it toward the middle of the screen.
    return av.x + av.w / 2 > workArea.x + workArea.width / 2 ? "left" : "right";
  }
  if (roomLeft >= need) return "left";
  if (roomRight >= need) return "right";
  return roomLeft >= roomRight ? "left" : "right";
}

function windowBounds(av, side) {
  return {
    x: side === "left" ? av.x - BUBBLE_W - GAP : av.x,
    y: av.y,
    width: av.w + BUBBLE_W + GAP,
    height: av.h,
  };
}

function avatarFromWindow(bounds, side, w, h) {
  return {
    x: side === "left" ? bounds.x + BUBBLE_W + GAP : bounds.x,
    y: bounds.y,
    w: w,
    h: h,
  };
}

module.exports = { BUBBLE_W, GAP, MIN, MAX, DEFAULT, clampSize, clampAvatar, chooseSide, windowBounds, avatarFromWindow, defaultAvatar };
