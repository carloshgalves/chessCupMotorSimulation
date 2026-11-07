"use strict";
/* Presentation preferences only: no chess rules, network, tokens or engine state. */
(() => {
  const KEY = "chesssim.board-presentation.v1";
  const DEFAULTS = Object.freeze({theme:"classic", pieces:"solid", motion:"off"});
  const CHOICES = Object.freeze({
    theme: Object.freeze(["classic","wood","slate","contrast"]),
    pieces: Object.freeze(["solid","outline"]),
    motion: Object.freeze(["off","fast","normal"])
  });
  const BOARD_IDS = Object.freeze(["board","history-replay-board"]);
  const CONTROLS = Object.freeze({
    theme:"board-pref-theme",
    pieces:"board-pref-pieces",
    motion:"board-pref-motion"
  });
  let current = {...DEFAULTS};

  function sanitize(value) {
    const next = {...DEFAULTS};
    if (value && typeof value === "object" && !Array.isArray(value)) {
      for (const name of Object.keys(CHOICES)) {
        if (CHOICES[name].includes(value[name])) next[name] = value[name];
      }
    }
    return next;
  }
  function parse(raw) {
    if (typeof raw !== "string" || raw.length > 1024) return {...DEFAULTS};
    try { return sanitize(JSON.parse(raw)); }
    catch (_) { return {...DEFAULTS}; }
  }
  function read() {
    try { return parse(window.localStorage.getItem(KEY)); }
    catch (_) { return {...DEFAULTS}; } // file://, privacy or blocked storage
  }
  function write() {
    try {
      window.localStorage.setItem(KEY, JSON.stringify(current));
      return true;
    } catch (_) {
      return false;
    }
  }
  function board(id) { return document.getElementById(id); }
  function apply() {
    for (const id of BOARD_IDS) {
      const element = board(id);
      if (!element) continue;
      element.setAttribute("data-board-theme", current.theme);
      element.setAttribute("data-piece-style", current.pieces);
      element.setAttribute("data-board-motion", current.motion);
    }
  }
  function controls() {
    for (const [name,id] of Object.entries(CONTROLS)) {
      const element = board(id);
      if (element) element.value = current[name];
    }
  }
  function status(message) {
    const element = board("board-pref-status");
    if (element) element.textContent = message;
  }
  function set(name, value) {
    if (!Object.prototype.hasOwnProperty.call(CHOICES, name) ||
        !CHOICES[name].includes(value)) {
      controls();
      return false;
    }
    current = {...current, [name]:value};
    apply();
    controls();
    status(write() ? "Preferências visuais salvas neste navegador." :
      "Preferência aplicada somente nesta página; armazenamento indisponível.");
    return true;
  }
  function reset() {
    current = {...DEFAULTS};
    apply();
    controls();
    status(write() ? "Preferências visuais restauradas e salvas." :
      "Padrão aplicado nesta página; armazenamento indisponível.");
  }
  function onStorage(event) {
    if (event.key !== KEY && event.key !== null) return;
    current = event.key === null ? read() : parse(event.newValue);
    apply();
    controls();
    status("Preferências sincronizadas com outra janela local.");
  }
  current = read();
  apply();
  controls();
  for (const [name,id] of Object.entries(CONTROLS)) {
    const input = board(id);
    if (input) input.addEventListener("change", () => set(name,input.value));
  }
  const restore = board("board-pref-reset");
  if (restore) restore.addEventListener("click", reset);
  // The Arena itself owns the visible settings panel. Its same-origin
  // embedded watch must still receive storage updates but avoid two editors.
  const panel = board("board-prefs");
  if (panel && window.parent && window.parent !== window) panel.hidden = true;
  window.addEventListener("storage", onStorage);
  window.ChessSimBoardPreferences = Object.freeze({
    get: () => ({...current}),
    set, reset, apply,
    storageKey: KEY
  });
})();
