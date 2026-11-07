"use strict";
/* Presentation preferences only: no chess rules, network, tokens or engine state. */
(() => {
  const KEY = "chesssim.board-presentation.v1";
  const DEFAULTS = Object.freeze({theme:"classic", pieces:"cburnett", motion:"off"});
  const CHOICES = Object.freeze({
    theme: Object.freeze(["classic","wood","slate","light","dark","contrast",
                          "green","blue","walnut","marble","lavender","terracotta"]),
    pieces: Object.freeze(["cburnett","merida","chessnut","fantasy",
                           "spatial","celtic","rhosgfx"]),
    motion: Object.freeze(["off","fast","normal"])
  });
  const SECTIONS = Object.freeze(["theme","pieces","motion"]);
  const BOARD_IDS = Object.freeze(["board","history-replay-board"]);
  const CONTROLS = Object.freeze({
    theme:"board-pref-theme",
    pieces:"board-pref-pieces",
    motion:"board-pref-motion"
  });
  let current = {...DEFAULTS};
  let expanded = null;
  function toggleSection(name) {
    if (name !== null && !SECTIONS.includes(name)) return false;
    expanded = name === expanded ? null : name;
    for (const section of SECTIONS) {
      const tab = board("board-pref-toggle-" + section);
      const panel = board("board-pref-panel-" + section);
      if (tab) tab.setAttribute("aria-expanded",String(expanded === section));
      if (panel) panel.hidden = expanded !== section;
    }
    return true;
  }

  function sanitize(value) {
    const next = {...DEFAULTS};
    if (value && typeof value === "object" && !Array.isArray(value)) {
      for (const name of Object.keys(CHOICES)) {
        // Transparent migration of old settings: avoid broken Unicode piece glyphs.
        const candidate = name === "pieces" && value[name] === "solid" ? "cburnett" :
                          name === "pieces" && value[name] === "outline" ? "spatial" :
                          value[name];
        if (CHOICES[name].includes(candidate)) next[name] = candidate;
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
      window.ChessSimBoard?.refreshArtwork(element);
    }
  }
  function controls() {
    for (const [name,id] of Object.entries(CONTROLS)) {
      const element = board(id);
      if (element) element.value = current[name];
    }
    for (const [id,attribute,selection] of [
      ["board-theme-gallery","data-theme",current.theme],
      ["board-piece-gallery","data-piece-set",current.pieces]
    ]) {
      const container = board(id);
      if (!container) continue;
      for (const option of container.children) {
        option.setAttribute("aria-pressed",String(
          option.getAttribute(attribute) === selection));
      }
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
  for (const [id,name,attribute] of [
    ["board-theme-gallery","theme","data-theme"],
    ["board-piece-gallery","pieces","data-piece-set"]
  ]) {
    const gallery = board(id);
    if (!gallery) continue;
    for (const option of gallery.children) {
      option.addEventListener("click", () => set(name,option.getAttribute(attribute)));
    }
  }
  for (const name of SECTIONS) {
    const toggle = board("board-pref-toggle-" + name);
    if (toggle) toggle.addEventListener("click", () => toggleSection(name));
  }
  // Initially expose just the three choices. Never auto-expand a large gallery.
  toggleSection(null);
  const popup = board("board-prefs-popup");
  if (popup) popup.addEventListener("keydown", event => {
    if (event.key === "Escape") {
      const details = board("board-prefs");
      if (details) details.open = false;
      board("board-prefs-summary")?.focus();
      event.preventDefault();
    }
  });
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
