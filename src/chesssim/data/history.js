"use strict";
/* Read-only local History. Source and licence text always use DOM text nodes. */
(() => {
  const byId = id => document.getElementById(id);
  const safeSlug = slug => /^[a-z0-9-]{2,48}$/.test(slug);
  const state = {players: [], index: 0, loaded: false, view: "roster", seq: 0};
  const make = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== null && text !== undefined) node.textContent = String(text);
    if (className) node.className = className;
    return node;
  };
  const link = (label, url) => {
    const node = make("a", label);
    // Sources are validated by the Python catalogue; defend again in the UI.
    if (typeof url !== "string" || !url.startsWith("https://")) {
      return make("span", label);
    }
    node.href = url;
    node.target = "_blank";
    node.rel = "noopener noreferrer";
    return node;
  };
  function portrait(slug, info, small = false) {
    const figure = make("figure", null, small ? "history-figure compact" : "history-figure");
    if (!safeSlug(slug) || !info) {
      figure.append(make("div", "Retrato não disponível", "history-placeholder"));
      return figure;
    }
    const picture = make("img");
    picture.src = "/api/history/portraits/" + slug + ".jpg";
    picture.alt = info.alt;
    picture.loading = "lazy";
    picture.decoding = "async";
    picture.addEventListener("error", () => {
      picture.replaceWith(make("div", "Retrato indisponível", "history-placeholder"));
    });
    figure.append(picture);
    const caption = make("figcaption", null, "history-credit");
    caption.append(make("span", "Foto: " + info.author + " · " + info.event +
      " (" + info.event_date + ")"));
    caption.append(link("Origem", info.source_url));
    caption.append(link("Licença " + info.license, info.license_url));
    caption.append(make("span", info.derivative_note));
    figure.append(caption);
    return figure;
  }
  function playerAt(index) {
    return state.players[(index + state.players.length) % state.players.length];
  }
  function showSelection() {
    if (!state.players.length) return;
    const selected = playerAt(state.index);
    const center = byId("history-selected");
    center.replaceChildren();
    center.append(portrait(selected.slug, selected.portrait));
    center.append(make("h3", selected.name));
    if (selected.era_label) center.append(make("p", selected.era_label, "history-era"));
    center.append(make("p", selected.short_bio || "Biografia ainda indisponível.", "history-summary"));
    center.append(make("p", selected.review_status === "reviewed"
      ? "Marcos com fontes documentadas" : "Em curadoria — informações ainda não homologadas",
      "history-curation"));
    for (const [id, offset] of [["history-left", -1], ["history-right", 1]]) {
      const slot = byId(id), neighbor = playerAt(state.index + offset);
      slot.replaceChildren();
      // Keep attribution links OUTSIDE the button: nested interactive
      // controls are invalid and inaccessible.
      slot.append(portrait(neighbor.slug, neighbor.portrait, true));
      const button = make("button", "Selecionar " + neighbor.name,
                          "history-side-button");
      button.type = "button";
      button.addEventListener("click", () => move(offset));
      slot.append(button);
    }
    byId("history-position").textContent = (state.index + 1) + " de " +
      state.players.length + " · ordem do catálogo, não ranking";
    byId("history-open").disabled = !safeSlug(selected.slug);
  }
  function move(offset) {
    if (!state.players.length || state.view !== "roster") return;
    state.index = (state.index + offset + state.players.length) % state.players.length;
    showSelection();
  }
  function sourceRef(sourceId, sources) {
    const info = sources.find(s => s.id === sourceId);
    return info ? link(info.publisher + ": " + info.title, info.url) :
      make("span", "Fonte em curadoria");
  }
  function renderProfile(profile) {
    const area = byId("history-profile-body");
    area.replaceChildren();
    byId("history-profile-name").textContent = profile.name;
    const header = make("div", null, "history-detail-header");
    header.append(portrait(profile.slug, profile.portrait));
    const description = make("div");
    description.append(make("p", profile.era_label || "Período não documentado", "history-era"));
    description.append(make("p", profile.short_bio || "Biografia em curadoria."));
    description.append(make("p", profile.review_status === "reviewed" ?
      "Marcos históricos documentados." :
      "CONTEÚDO EM CURADORIA: as alegações ainda exigem revisão editorial.",
      "history-curation"));
    description.append(make("p", "Este texto em primeira pessoa é uma recriação editorial, não uma declaração do enxadrista.",
      "history-disclaimer"));
    header.append(description);
    area.append(header);
    area.append(make("h3", "Maiores feitos"));
    const achievements = make("ol", null, "history-achievements");
    for (const feat of profile.highlights || []) {
      const item = make("li");
      item.append(make("strong", feat.year + " — "));
      item.append(make("span", feat.text));
      const refs = make("div", null, "history-sources");
      for (const id of feat.source_ids || []) refs.append(sourceRef(id, profile.sources || []));
      item.append(refs);
      achievements.append(item);
    }
    if (!achievements.children.length) achievements.append(make("li", "Conquistas em curadoria."));
    area.append(achievements);
    area.append(make("h3", "Linha do tempo"));
    const chronology = make("ol", null, "history-timeline");
    for (const point of profile.timeline || []) {
      const item = make("li", point.year + " · " + point.title + " — " + point.summary);
      chronology.append(item);
    }
    if (!chronology.children.length) chronology.append(make("li", "Cronologia em curadoria."));
    area.append(chronology);
    area.append(make("h3", "Partidas para explorar"));
    area.append(make("p", "Partidas históricas e replay serão incorporados na issue #51."));
    area.append(make("h3", "No simulador"));
    area.append(make("p", "Estatísticas locais serão integradas na issue #52. Não representam a carreira real."));
  }
  async function openProfile() {
    if (!state.players.length) return;
    const selected = playerAt(state.index);
    if (!safeSlug(selected.slug)) return;
    const id = ++state.seq;
    byId("history-status").textContent = "Carregando perfil…";
    try {
      const response = await fetch("/api/history/players/" + selected.slug, {cache:"no-store"});
      if (!response.ok) throw new Error("Falha de consulta histórica");
      const profile = await response.json();
      if (id !== state.seq || profile.slug !== selected.slug) return;
      renderProfile(profile);
      state.view = "profile";
      byId("history-roster").hidden = true;
      byId("history-profile").hidden = false;
      byId("history-status").textContent = "";
      byId("history-back").focus();
    } catch (_) {
      if (id === state.seq) byId("history-status").textContent =
        "Não foi possível carregar esse perfil. Tente novamente.";
    }
  }
  function backToRoster() {
    ++state.seq;
    state.view = "roster";
    byId("history-profile").hidden = true;
    byId("history-roster").hidden = false;
    showSelection();
    byId("history-open").focus();
  }
  async function load() {
    if (state.loaded) return;
    byId("history-status").textContent = "Carregando elenco histórico…";
    try {
      const response = await fetch("/api/history/players", {cache:"no-store"});
      if (!response.ok) throw new Error("Catálogo indisponível");
      const data = await response.json();
      if (!Array.isArray(data.players) || !data.players.length) throw new Error("Sem jogadores");
      state.players = data.players.filter(p => safeSlug(p.slug));
      if (!state.players.length) throw new Error("Elenco inválido");
      state.loaded = true;
      byId("history-status").textContent = "";
      byId("history-roster").hidden = false;
      showSelection();
    } catch (_) {
      byId("history-status").textContent = "Não foi possível carregar o catálogo. Retorne à Arena e tente novamente.";
    }
  }
  function toggle(toHistory) {
    byId("arena-view").hidden = toHistory;
    byId("history-view").hidden = !toHistory;
    byId("tab-history").setAttribute("aria-pressed", String(toHistory));
    byId("tab-arena").setAttribute("aria-pressed", String(!toHistory));
    if (toHistory) {
      load();
      byId("history-heading").setAttribute("tabindex", "-1");
      byId("history-heading").focus();
    } else {
      ++state.seq;
      byId("tab-arena").focus();
    }
  }
  byId("tab-history").addEventListener("click", () => toggle(true));
  byId("tab-arena").addEventListener("click", () => toggle(false));
  byId("history-prev").addEventListener("click", () => move(-1));
  byId("history-next").addEventListener("click", () => move(1));
  byId("history-open").addEventListener("click", openProfile);
  byId("history-back").addEventListener("click", backToRoster);
  byId("history-view").addEventListener("keydown", event => {
    if (state.view !== "roster" || ["INPUT","SELECT","TEXTAREA"].includes(event.target.tagName)) return;
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      event.preventDefault();
      move(event.key === "ArrowLeft" ? -1 : 1);
    }
  });
})();
