"use strict";
/*
 * One-tap suggestions based on the app you're in. Each one is a prompt plus
 * what to attach (a screenshot of that window, or the clipboard). Shown as
 * pills when you hover the avatar, and at the top of the right-click menu.
 */

const RULES = [
  {
    test: (w) => /youtube|tiktok|instagram|vimeo|twitch|facebook|x\.com| \/ x$|twitter|linkedin|reddit/i.test(w.title || "") && isBrowser(w),
    items: [
      { label: "What's this about?", prompt: "What is this post or video about? Use the title and what's on screen; if you can identify the link, analyze it properly.", attach: "window" },
      { label: "How do I do this?", prompt: "If this shows a feature, tool or technique, explain what it is, whether it's really available, and step by step how I can do it.", attach: "window" },
    ],
  },
  {
    test: isBrowser,
    items: [
      { label: "Summarize this page", prompt: "Summarize what's on this page in a few sentences.", attach: "window" },
      { label: "Explain this", prompt: "Explain what I'm looking at here.", attach: "window" },
    ],
  },
  {
    test: (w) => /^(code|cursor|windsurf|devenv|idea64|pycharm64|webstorm64|sublime_text|notepad\+\+|zed)\.exe$/i.test(w.exe || "") || /visual studio code|cursor/i.test(w.app || ""),
    items: [
      { label: "Explain this code", prompt: "Explain the code that's on my screen.", attach: "window" },
      { label: "Find the bug", prompt: "Look at the code and any errors on my screen. What's wrong, and how do I fix it?", attach: "window" },
    ],
  },
  {
    test: (w) => /^(windowsterminal|cmd|powershell|pwsh|wezterm-gui|alacritty|mintty|conhost)\.exe$/i.test(w.exe || ""),
    items: [
      { label: "What does this error mean?", prompt: "What does the output or error in my terminal mean, and what should I run next?", attach: "window" },
    ],
  },
  {
    test: (w) => /^winword\.exe$/i.test(w.exe || ""),
    items: [{ label: "Proofread this", prompt: "Proofread the visible text and suggest improvements.", attach: "window" }],
  },
  {
    test: (w) => /^excel\.exe$/i.test(w.exe || ""),
    items: [{ label: "Explain this sheet", prompt: "Explain this spreadsheet and point out anything that looks wrong.", attach: "window" }],
  },
  {
    test: (w) => /^powerpnt\.exe$/i.test(w.exe || ""),
    items: [{ label: "Feedback on this slide", prompt: "Give me quick feedback on this slide's content and design.", attach: "window" }],
  },
  {
    test: (w) => /^(outlook|olk|thunderbird)\.exe$/i.test(w.exe || "") || /gmail|outlook/i.test(w.title || ""),
    items: [{ label: "Help me reply", prompt: "Draft a reply to the email on my screen. Show it to me first; don't send anything.", attach: "window" }],
  },
  {
    test: (w) => /^explorer\.exe$/i.test(w.exe || ""),
    items: [{ label: "Find a file", action: "prefill", text: "Find file called " }],
  },
];

function isBrowser(w) {
  return /^(chrome|msedge|firefox|brave|opera|vivaldi|arc|zen)\.exe$/i.test(w.exe || "") || /chrome|edge|firefox|brave|safari/i.test(w.app || "");
}

function forWindow(w, max = 3) {
  if (!w) return [];
  for (const r of RULES) {
    let hit = false;
    try { hit = r.test(w); } catch (e) {}
    if (hit) return r.items.slice(0, max);
  }
  return [{ label: "What's on my screen?", prompt: "What am I looking at? Anything useful to know or do here?", attach: "window" }];
}

module.exports = { forWindow, isBrowser };
