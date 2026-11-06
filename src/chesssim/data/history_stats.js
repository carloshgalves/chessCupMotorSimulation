"use strict";
/* Read-only statistics for verified local ChessSimulation games.
 * Never conflate this panel with historical career or style authenticity.
 */
(() => {
  const $ = id => document.getElementById(id);
  const fields = ["tournaments", "duels", "games", "completed",
                  "wins", "draws", "losses", "incomplete"];
  const label = {
    tournaments:"Torneios", duels:"Confrontos", games:"Partidas registradas",
    completed:"Concluídas", wins:"Vitórias", draws:"Empates",
    losses:"Derrotas", incomplete:"Interrompidas / incompletas"
  };
  const state = {slug:null,seq:0,active:false,loaded:false,pending:false,report:null};
  function make(tag, value, className) {
    const node = document.createElement(tag);
    if (value !== null && value !== undefined) node.textContent = String(value);
    if (className) node.className = className;
    return node;
  }
  function isCount(value) {
    return Number.isSafeInteger(value) && value >= 0;
  }
  function consistentTotals(value) {
    return value && fields.every(name=>isCount(value[name])) &&
      value.games === value.completed + value.incomplete &&
      value.completed === value.wins + value.draws + value.losses &&
      value.duels <= value.games;
  }
  function valid(data, slug) {
    if (!data || data.schema !== "chesssim-player-stats-v1" ||
        data.slug !== slug || !consistentTotals(data.totals) ||
        !data.scope || !data.coverage ||
        !Array.isArray(data.opponents) || data.opponents.length > 63 ||
        !["arena_local", "tournament_local"].every(s=>
          Array.isArray(data.scope.sources) && data.scope.sources.includes(s))) return false;
    const coverage = data.coverage;
    for (const key of ["sessions_seen","tournaments_seen","candidate_games",
                       "verified_games","arena_verified_games","tournament_verified_games",
                       "rejected_games","rejected_records",
                       "excluded_attempts","failed_attempts","duplicate_records"]) {
      if (!isCount(coverage[key])) return false;
    }
    if (typeof coverage.partial !== "boolean" ||
        !data.opponents.every(item=>item &&
          typeof item.slug === "string" && /^[a-z0-9-]{2,48}$/.test(item.slug) &&
          item.slug !== slug && ["games","completed","wins","draws","losses","incomplete"].every(
            key=>isCount(item[key])) &&
          item.games === item.completed + item.incomplete &&
          item.completed === item.wins + item.draws + item.losses)) {
      return false;
    }
    if(coverage.arena_verified_games + coverage.tournament_verified_games !==
           coverage.verified_games ||
       coverage.verified_games !== data.totals.games ||
       coverage.candidate_games < coverage.verified_games + coverage.rejected_games ||
       coverage.partial !== Boolean(coverage.rejected_records || coverage.rejected_games)) {
      return false;
    }
    const seen = new Set();
    const sum = {games:0,completed:0,wins:0,draws:0,losses:0,incomplete:0};
    for(const opponent of data.opponents) {
      if(seen.has(opponent.slug)) return false;
      seen.add(opponent.slug);
      for(const key of Object.keys(sum)) sum[key] += opponent[key];
    }
    return Object.keys(sum).every(key=>sum[key]===data.totals[key]);
  }
  function counterList(report) {
    const group = $("history-stats-totals");
    group.replaceChildren();
    for (const field of fields) {
      const card = make("div", null, "history-stat-card");
      card.append(make("dt", label[field]));
      card.append(make("dd", report.totals[field], "history-stat-number"));
      group.append(card);
    }
  }
  function opponents(report) {
    const box = $("history-stats-opponents");
    box.replaceChildren();
    if (report.opponents.length === 0) {
      box.append(make("p", "Sem confrontos registrados contra outros jogadores."));
      return;
    }
    const list = make("ul", null, "history-opponent-list");
    for (const entry of report.opponents) {
      const display = window.ChessSimHistoryPlayerName ?
        window.ChessSimHistoryPlayerName(entry.slug) : entry.slug;
      const li = make("li");
      li.append(make("strong", display));
      li.append(make("span", entry.games + " jogos · " + entry.wins +
        " V · " + entry.draws + " E · " + entry.losses + " D · " +
        entry.incomplete + " incompletos"));
      list.append(li);
    }
    box.append(list);
  }
  function render(report) {
    counterList(report);
    opponents(report);
    const scope = report.scope;
    const directory = typeof scope.directory === "string" &&
      scope.directory.length <= 200 ? scope.directory : "diretório local";
    const first = scope.from_utc || "sem data registrada";
    const last = scope.to_utc || "sem data registrada";
    $("history-stats-scope").textContent =
      "Fonte: sessões da Arena e torneios locais em “" + directory +
      "”. Período documentado dos manifestos: " + first + " a " + last +
      ". Datas de criação, não duração das partidas.";
    const coverage = report.coverage;
    $("history-stats-coverage").textContent =
      "Cobertura: " + coverage.verified_games + " partidas verificadas de " +
      coverage.candidate_games + " candidatas; " +
      coverage.rejected_games + " partidas e " +
      coverage.rejected_records + " registros rejeitados. " +
      "Origem verificada: " + coverage.arena_verified_games + " jogos da Arena e " +
      coverage.tournament_verified_games + " jogos de torneios. " +
      coverage.excluded_attempts + " tentativas excluídas; " +
      coverage.failed_attempts + " tentativas falhas.";
    $("history-stats-warning").textContent = coverage.partial ?
      "Atenção: cobertura parcial. Registros inconsistentes foram descartados, " +
      "não convertidos em resultados presumidos." : "";
    $("history-stats-empty").textContent = report.totals.games === 0 ?
      "Sem partidas locais verificadas para este jogador. " +
      (report.totals.tournaments ?
       "Há participação em torneio, mas nenhuma partida contabilizável." :
       "Nenhuma estatística foi estimada.") : "";
    $("history-stats-status").textContent =
      "Estatísticas de simulações locais verificadas; não representam a carreira real.";
  }
  function clear() {
    for(const id of ["history-stats-status","history-stats-scope",
                     "history-stats-coverage","history-stats-warning",
                     "history-stats-empty"]) $(id).textContent = "";
    $("history-stats-totals").replaceChildren();
    $("history-stats-opponents").replaceChildren();
  }
  function leave() {
    state.seq++;
    state.slug = null;
    state.active = false;
    state.loaded = false;
    state.pending = false;
    state.report = null;
    clear();
    $("history-stats-section").hidden = true;
  }
  function suspend() {
    state.seq++;
    state.active = false;
    if(state.pending) state.loaded = false;
    state.pending = false;
  }
  function resume() {
    if(!state.slug || state.active) return;
    state.active = true;
    if(state.loaded && state.report) render(state.report);
    else load(state.slug);
  }
  function enter(slug) {
    leave();
    if(typeof slug !== "string" || !/^[a-z0-9-]{2,48}$/.test(slug)) return;
    state.slug = slug;
    state.active = true;
    $("history-stats-section").hidden = false;
    load(slug);
  }
  async function load(slug) {
    const request = ++state.seq;
    state.pending = true;
    $("history-stats-status").textContent = "Consultando estatísticas locais…";
    try {
      const response = await fetch("/api/history/players/" + slug + "/stats",
                                   {cache:"no-store"});
      if(!response.ok) throw new Error("Falha de API");
      const report = await response.json();
      if(!state.active || state.seq !== request || state.slug !== slug) return;
      if(!valid(report,slug)) throw new Error("Contrato inconsistente");
      state.pending = false;
      state.loaded = true;
      state.report = report;
      render(report);
    } catch(_) {
      if(state.active && state.seq === request && state.slug === slug) {
        state.pending = false;
        state.loaded = false;
        state.report = null;
        clear();
        $("history-stats-status").textContent =
          "Estatísticas indisponíveis. Nenhum resultado foi presumido.";
      }
    }
  }
  window.ChessSimHistoryStats = Object.freeze({enter,leave,suspend,resume});
})();
