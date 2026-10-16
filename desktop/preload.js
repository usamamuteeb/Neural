"use strict";
// The page's only door to the main process: a small, explicit API. No Node
// in the page itself (contextIsolation + sandbox).
const { contextBridge, ipcRenderer } = require("electron");

const EVENTS = new Set(["cursor", "layout", "settings", "context", "hotkey", "ptt", "reminder", "menu:action", "backend-status"]);

contextBridge.exposeInMainWorld("neural", {
  on(channel, fn) {
    if (!EVENTS.has(channel)) throw new Error("unknown channel " + channel);
    const handler = (e, payload) => fn(payload);
    ipcRenderer.on(channel, handler);
    return () => ipcRenderer.removeListener(channel, handler);
  },
  // window
  setIgnoreMouse: (ignore) => ipcRenderer.send("mouse:ignore", !!ignore),
  dragStart: () => ipcRenderer.send("drag:start"),
  dragEnd: () => ipcRenderer.send("drag:end"),
  resizeStart: () => ipcRenderer.send("resize:start"),
  resizeEnd: () => ipcRenderer.send("resize:end"),
  hide: () => ipcRenderer.send("win:hide"),
  quit: () => ipcRenderer.send("app:quit"),
  focus: () => ipcRenderer.send("win:focus"),
  popupMenu: () => ipcRenderer.send("menu:popup"),
  // settings
  getSettings: () => ipcRenderer.invoke("settings:get"),
  setSetting: (key, value) => ipcRenderer.invoke("settings:set", key, value),
  // context + capture
  getContext: () => ipcRenderer.invoke("context:get"),
  captureWindow: () => ipcRenderer.invoke("capture:window"),
  captureScreen: () => ipcRenderer.invoke("capture:screen"),
  ocrWindow: () => ipcRenderer.invoke("ocr:window"),
  readClipboard: () => ipcRenderer.invoke("clipboard:read"),
  writeClipboard: (text) => ipcRenderer.invoke("clipboard:write", text),
  // local actions
  handleIntent: (text) => ipcRenderer.invoke("intent:handle", text),
  searchFiles: (q) => ipcRenderer.invoke("files:search", q),
  openFile: (p) => ipcRenderer.invoke("files:open", p),
  revealFile: (p) => ipcRenderer.invoke("files:reveal", p),
  openExternal: (url) => ipcRenderer.invoke("open:external", url),
  openWeb: () => ipcRenderer.invoke("open:web"),
  listReminders: () => ipcRenderer.invoke("reminders:list"),
  cancelReminder: (id) => ipcRenderer.invoke("reminders:cancel", id),
  // backend
  backendInfo: () => ipcRenderer.invoke("backend:info"),
  backendConfig: () => ipcRenderer.invoke("backend:config"),
  transcribe: (audio, mime, language) => ipcRenderer.invoke("backend:transcribe", { audio, mime, language }),
  speak: (text) => ipcRenderer.invoke("backend:speak", text),
  warmVoice: () => ipcRenderer.invoke("backend:warm"),
  reportStatus: (status) => ipcRenderer.send("backend:status", status),
});
