"use strict";
/* Evidence-only local Elo: no engine control, no FIDE claims, no HTML injection. */
(() => {
  const $ = id => document.getElementById(id);
  const state = {
    slug:null, playerSeq:0, playerActive:false, playerPending:false,
    boardSeq:0, boardPending:false,
  };
  const STATUSES = new Set(["sem_amostra","provisional","observed_experimental"]);
  const MODALITIES = new Set(["classical","rapid","blitz","bullet","unclassified"]);
  const modeLabel = mode => ({
    classical:"Clássico",rapid:"Rápido",blitz:"Blitz",bullet:"Bullet",
    unclassified:"Sem relógio"
  })[mode] || mode;
  const make = (tag, value) => {
    const node = document.createElement(tag);
    if(value !== undefined && value !== null) node.textContent = String(value);
    return node;
  };
  const count = n => Number.isSafeInteger(n) && n >= 0;
  const decimal = s => typeof s === "string" && s.length <= 80 &&
    /^-?[0-9]+(?:\.[0-9]+)?$/.test(s) && Number.isFinite(Number(s));
  function safeRow(row) {
    // Explicitly reject a made-up precision or strength score.
    if (!row || row.move_accuracy !== null ||
        row.strength_evaluation !== null) return false;
    return row && typeof row.slug === "string" &&
      /^[a-z0-9-]{2,48}$/.test(row.slug) &&
      decimal(row.rating) && decimal(row.display_rating) &&
      row.initial_rating === "2500" &&
      count(row.games) && count(row.wins) && count(row.draws) &&
      count(row.losses) && count(row.points_3_1_0) &&
      row.games === row.wins + row.draws + row.losses &&
      row.points_3_1_0 === row.wins * 3 + row.draws &&
      STATUSES.has(row.status) &&
      (row.rank === null || (count(row.rank) && row.rank > 0)) &&
      (row.games ? row.rank !== null : row.rank === null) &&
      (row.games || row.status === "sem_amostra") &&
      (row.status !== "sem_amostra" || row.display_rating === "2500.0") &&
      (row.sample_score === null ||
        (typeof row.sample_score === "number" &&
         Number.isFinite(row.sample_score) &&
         row.sample_score >= 0 && row.sample_score <= 1)) &&
      (row.score_range_exploratory === null ||
        (Array.isArray(row.score_range_exploratory) &&
         row.score_range_exploratory.length === 2 &&
         row.score_range_exploratory.every(x=>typeof x === "number" &&
                                              Number.isFinite(x) && x >= 0 && x <= 1) &&
         row.score_range_exploratory[0] <= row.score_range_exploratory[1]));
  }
  function valid(data, slug, mode) {
    if (!MODALITIES.has(mode) || !data ||
        data.schema !== "chesssim-competition-elo-v2" ||
        data.baseline !== "2500" ||
        data.universe !== "local-uci-observed-v2" ||
        data.modality !== mode ||
        !data.available_modalities ||
        [...MODALITIES].some(m => !count(data.available_modalities[m])) ||
        !Array.isArray(data.ratings) ||
        data.ratings.length !== (slug ? 1 : 64) ||
        !data.ratings.every(safeRow) ||
        !data.counts || !count(data.counts.scored_games) ||
        !data.coverage || typeof data.coverage.partial !== "boolean" ||
        !decimal(data.k) ||
        typeof data.scale_note !== "string" || data.scale_note.length > 400 ||
        !Array.isArray(data.modalities) ||
        data.mixed_modalities_warning !== false ||
        (slug && (data.slug !== slug || data.ratings[0].slug !== slug))) return false;
    return new Set(data.ratings.map(x=>x.slug)).size === data.ratings.length;
  }
  const playerName = slug => window.ChessSimHistoryPlayerName ?
    window.ChessSimHistoryPlayerName(slug) : slug;
  const statusText = row => row.status === "sem_amostra" ?
    "Inicial, sem partidas verificadas" : row.status === "provisional" ?
    "Provisório: amostra inferior a 30 partidas" :
    "Observado, ainda experimental";
  function playerClear() {
    $("history-rating-status").textContent = "";
    $("history-rating-value").textContent = "";
    $("history-rating-proof").textContent = "";
  }
  function renderPlayer(report) {
    const row = report.ratings[0];
    $("history-rating-value").textContent =
      "Elo ChessSimulation (" + modeLabel(report.modality) + "): " +
      row.display_rating + " · " + statusText(row) +
      " · " + row.games + " partidas; " + row.points_3_1_0 +
      " pontos (3/1/0).";
    $("history-rating-proof").textContent =
      "Ranking: " + (row.rank === null ? "sem classificação" : row.rank) +
      ". Precisão de lances: não medida. Força UCI: não avaliada. " +
      "Rating inicial 2500 é só uma escala competitiva; não indica " +
      "rating FIDE nem fidelidade histórica." +
      (report.coverage.partial ?
       " Cobertura parcial: registros inválidos não entram no Elo." : "");
    $("history-rating-status").textContent =
      "Resultados reais do simulador, não resultados históricos.";
  }
  async function loadPlayer(slug) {
    const id = ++state.playerSeq;
    const mode = $("history-rating-mode").value;
    if (!MODALITIES.has(mode)) return;
    state.playerPending = true;
    $("history-rating-status").textContent = "Calculando Elo experimental local…";
    try {
      const res = await fetch("/api/history/players/" + slug +
                              "/rating?modality=" + mode, {cache:"no-store"});
      if(!res.ok) throw new Error("HTTP");
      const data = await res.json();
      if(id !== state.playerSeq || !state.playerActive || slug !== state.slug) return;
      if(!valid(data,slug,mode)) throw new Error("Contrato");
      state.playerPending = false;
      renderPlayer(data);
    } catch (_) {
      if(id === state.playerSeq && state.playerActive && slug === state.slug) {
        state.playerPending = false;
        playerClear();
        $("history-rating-status").textContent =
          "Elo local indisponível. Nenhuma força foi presumida.";
      }
    }
  }
  function enter(slug) {
    leave();
    if(typeof slug !== "string" || !/^[a-z0-9-]{2,48}$/.test(slug)) return;
    state.slug = slug;
    state.playerActive = true;
    $("history-rating-profile").hidden = false;
    loadPlayer(slug);
  }
  function leave() {
    ++state.playerSeq;
    state.slug = null;
    state.playerActive = false;
    state.playerPending = false;
    playerClear();
    $("history-rating-profile").hidden = true;
  }
  function suspend() {
    ++state.playerSeq;
    state.playerActive = false;
  }
  function resume() {
    if(!state.slug || state.playerActive) return;
    state.playerActive = true;
    loadPlayer(state.slug);
  }
  function clearBoard() {
    $("competition-rating-status").textContent = "";
    $("competition-rating-meta").textContent = "";
    $("competition-rating-rows").replaceChildren();
  }
  async function loadBoard() {
    const id = ++state.boardSeq;
    const mode = $("competition-rating-mode").value;
    if (!MODALITIES.has(mode)) return;
    state.boardPending = true;
    clearBoard();
    $("competition-rating-status").textContent = "Calculando ranking experimental…";
    try {
      const response = await fetch("/api/competition/ratings?modality=" + mode,
                                   {cache:"no-store"});
      if(!response.ok) throw new Error("HTTP");
      const report = await response.json();
      if(id !== state.boardSeq || !$("competition-rating-leaderboard").open) return;
      if(!valid(report,null,mode)) throw new Error("Contrato");
      const target = $("competition-rating-rows");
      target.replaceChildren();
      for(const row of report.ratings) {
        const tr = make("tr");
        const cells = [
          row.rank === null ? "—" : row.rank,
          playerName(row.slug),
          row.display_rating,
          statusText(row),
          row.games,
          row.wins + "/" + row.draws + "/" + row.losses,
          row.points_3_1_0,
          "Não medida",
          row.score_range_exploratory === null ? "—" :
            row.score_range_exploratory.join("–")
        ];
        for(const value of cells) tr.append(make("td",value));
        target.append(tr);
      }
      $("competition-rating-meta").textContent =
        modeLabel(mode) + ": " + report.counts.scored_games +
        " partidas válidas nesse ritmo. Elo inicial 2500, K=" + report.k + ". " +
        (report.mixed_modalities_warning ?
         "Atenção: modalidades misturadas; ranking não é classificação FIDE. " : "") +
        "Precisão de lances não medida; força UCI não avaliada. " +
        "A faixa de score é apenas descritiva, não intervalo de Elo." +
        (report.coverage.partial ? " Cobertura parcial: registros excluídos." : "");
      $("competition-rating-status").textContent =
        "Ranking experimental local: " + modeLabel(mode) + ".";
      state.boardPending = false;
    } catch (_) {
      if(id === state.boardSeq && $("competition-rating-leaderboard").open) {
        clearBoard();
        $("competition-rating-status").textContent =
          "Ranking indisponível. Nenhum Elo fictício foi gerado.";
        state.boardPending = false;
      }
    }
  }
  $("history-rating-mode").addEventListener("change", () => {
    if (state.playerActive && state.slug) loadPlayer(state.slug);
  });
  $("competition-rating-mode").addEventListener("change", () => {
    if ($("competition-rating-leaderboard").open) loadBoard();
  });
  $("competition-rating-leaderboard").addEventListener("toggle", () => {
    if($("competition-rating-leaderboard").open) loadBoard();
    else {
      ++state.boardSeq;
      state.boardPending = false;
      clearBoard();
    }
  });
  window.ChessSimRatings = Object.freeze({enter,leave,suspend,resume});
})();
