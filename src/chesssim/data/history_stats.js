"use strict";
/* Local-only statistics UI: separate from biographies and historical games. */
(() => {
  const byId = id => document.getElementById(id);
  const validSlug = slug => typeof slug === "string" &&
    /^[a-z0-9-]{2,48}$/.test(slug);
  const measures = [
    ["run_count", "Execuções com participação"],
    ["tournaments_participated", "Torneios com participação"],
    ["duels_count", "Confrontos / tentativas"],
    ["completed_games", "Partidas concluídas"],
    ["wins", "Vitórias"],
    ["draws", "Empates"],
    ["losses", "Derrotas"],
    ["incomplete_games", "Partidas incompletas"]
  ];
  let generation = 0;
  let currentSlug = null;
  let visible = false;
  let loaded = false;
  let pending = false;
  let cached = null;
  const make = (tag, text, className) => {
    const el = document.createElement(tag);
    if (text !== null && text !== undefined) el.textContent = String(text);
    if (className) el.className = className;
    return el;
  };
  function validTotals(d) {
    if (!d || d.schema !== "chesssim-history-stats-v1" ||
        d.slug !== currentSlug || !d.coverage ||
        typeof d.coverage !== "object" ||
        typeof d.coverage.complete !== "boolean" ||
        !Array.isArray(d.head_to_head) || !d.source_scope ||
        d.source_scope.kind !== "configured_arena_workspace") return false;
    for (const [key] of measures) {
      if (!Number.isSafeInteger(d[key]) || d[key] < 0) return false;
    }
    if (d.wins+d.draws+d.losses !== d.completed_games ||
        d.tournaments_participated > d.run_count ||
        d.completed_games+d.incomplete_games < d.duels_count &&
        d.duels_count > 0) {
      // A duel must have at least one verifiable game in the canonical ledger.
      return false;
    }
    const names = new Set();
    const sum = {duels_count:0,completed_games:0,wins:0,draws:0,
                 losses:0,incomplete_games:0};
    for (const opponent of d.head_to_head) {
      if (!opponent || !validSlug(opponent.opponent_slug) ||
          opponent.opponent_slug === d.slug ||
          names.has(opponent.opponent_slug)) return false;
      names.add(opponent.opponent_slug);
      for (const key of Object.keys(sum)) {
        if (!Number.isSafeInteger(opponent[key]) || opponent[key]<0) return false;
        sum[key] += opponent[key];
      }
      if (opponent.wins+opponent.draws+opponent.losses !== opponent.completed_games) return false;
    }
    return Object.keys(sum).every(key=>sum[key]===d[key]);
  }
  function clear() {
    byId("history-stats-metrics").replaceChildren();
    byId("history-stats-rivals").replaceChildren();
    byId("history-stats-rivals-heading").hidden = true;
    byId("history-stats-coverage").textContent = "";
    byId("history-stats-status").textContent = "";
  }
  function show(data) {
    if (!validTotals(data)) throw new Error("Totais locais contraditórios");
    clear();
    const metrics = byId("history-stats-metrics");
    for (const [key, title] of measures) {
      const card = make("div", null, "history-stats-metric");
      card.append(make("span", title, "history-stats-label"));
      card.append(make("strong", String(data[key]), "history-stats-number"));
      metrics.append(card);
    }
    const rivals = byId("history-stats-rivals");
    const rows = data.head_to_head;
    if (rows.length) {
      byId("history-stats-rivals-heading").hidden = false;
      for (const opponent of rows) {
        const row = make("div", null, "history-stats-opponent");
        const label = window.ChessSimHistoryPlayerName ?
          window.ChessSimHistoryPlayerName(opponent.opponent_slug) :
          opponent.opponent_slug;
        row.append(make("strong", label));
        row.append(make("span",
          opponent.completed_games+" jogo(s): "+opponent.wins+" V · "+
          opponent.draws+" E · "+opponent.losses+" D · "+
          opponent.incomplete_games+" incompleto(s)"));
        rivals.append(row);
      }
    }
    const coverage = data.coverage;
    const text = "Fonte: diretório local configurado da Arena. "+
      coverage.runs_scanned+" execuções examinadas; "+
      coverage.attempts_scanned+" tentativas verificadas; "+
      coverage.games_accepted+" jogos do jogador aceitos; "+
      coverage.games_excluded+" registros excluídos. "+
      (coverage.complete ? "Cobertura sem erros detectados." :
       "Cobertura parcial: "+coverage.sources_rejected+
       " fontes excluídas ou inconsistentes.");
    const reasonLabels = {
      INVALID_ARENA_SESSION:"sessão da Arena inválida",
      INVALID_ARENA_PATH:"caminho de duelo incompatível",
      INVALID_ARENA_ATTEMPT:"PGN ou resumo de duelo inválido",
      INVALID_TOURNAMENT:"manifesto de torneio inválido",
      INVALID_TOURNAMENT_LEDGER:"registro de torneio inválido",
      INVALID_TOURNAMENT_ATTEMPT:"tentativa de torneio inválida",
      UNREGISTERED_TOURNAMENT_ATTEMPT:"tentativa não registrada",
      EXCLUDED_TOURNAMENT_ATTEMPT:"tentativa excluída/interrompida",
      DUPLICATE_ATTEMPT:"tentativa repetida"
    };
    const reasons = coverage.reasons && typeof coverage.reasons === "object" ?
      Object.entries(coverage.reasons).filter(([key,count]) =>
        Object.prototype.hasOwnProperty.call(reasonLabels,key) &&
        Number.isSafeInteger(count) && count > 0) : [];
    const details = reasons.map(([key,count]) => reasonLabels[key]+" ("+count+")").join("; ");
    byId("history-stats-coverage").textContent =
      text + (details ? " Motivos: "+details+"." : "");
    if (data.completed_games === 0 && data.incomplete_games === 0) {
      byId("history-stats-status").textContent =
        "Sem partidas verificadas neste acervo local. Participações sem jogos são mostradas separadamente.";
    } else {
      byId("history-stats-status").textContent =
        "Desempenho da simulação local, não da carreira histórica.";
    }
    if (!coverage.complete) {
      byId("history-stats-coverage").className =
        "history-stats-coverage history-stats-warning";
    } else {
      byId("history-stats-coverage").className = "history-stats-coverage";
    }
  }
  async function load(slug) {
    if (!visible || currentSlug !== slug) return;
    const request = ++generation;
    loaded = false;
    pending = true;
    byId("history-stats-status").textContent = "Consultando registros locais…";
    try {
      const response = await fetch("/api/history/players/" + slug + "/stats",
                                   {cache:"no-store"});
      if (!response.ok) throw new Error("Endpoint indisponível");
      const data = await response.json();
      if (!visible || request !== generation || slug !== currentSlug) return;
      show(data);
      cached = data;
      loaded = true;
      pending = false;
    } catch (_) {
      if (!visible || request !== generation || slug !== currentSlug) return;
      pending = false;
      cached = null;
      clear();
      byId("history-stats-status").textContent =
        "Estatísticas locais indisponíveis. Nenhum resultado foi presumido.";
    }
  }
  function leave() {
    ++generation;
    visible = false;
    pending = false;
    loaded = false;
    currentSlug = null;
    cached = null;
    clear();
    byId("history-stats-section").hidden = true;
  }
  function enter(slug) {
    leave();
    if (!validSlug(slug)) return;
    currentSlug = slug;
    visible = true;
    byId("history-stats-section").hidden = false;
    load(slug);
  }
  function suspend() {
    if (!visible) return;
    ++generation;
    visible = false;
    if (pending) loaded = false;
    pending = false;
  }
  function resume() {
    if (!currentSlug || visible) return;
    visible = true;
    if (!loaded) load(currentSlug);
    else if (cached) show(cached);
  }
  window.ChessSimHistoryStats = Object.freeze({enter,leave,suspend,resume});
})();
