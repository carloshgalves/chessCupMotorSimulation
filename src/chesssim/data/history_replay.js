"use strict";
/* Profile-only, GET-only local replay. All chess positions are verified by
 * python-chess server-side; this component only paints FEN and navigates.
 * Request generations prevent late responses from changing another game.
 */
(() => {
  const byId = id => document.getElementById(id);
  const board = window.ChessSimBoard;
  const validId = id => typeof id === "string" &&
    /^(?:h-[a-z0-9-]{2,64}|s-[a-f0-9]{24})$/.test(id);
  let generation = 0;
  let active = false;
  let slug = null;
  let games = [];
  let selected = null;
  let replay = null;
  let ply = 0;
  let listLoaded = false;
  let listPending = false;
  let replayPending = false;
  const cards = new Map();

  function make(tag, value, className) {
    const element = document.createElement(tag);
    if (value !== null && value !== undefined) element.textContent = String(value);
    if (className) element.className = className;
    return element;
  }
  function safeText(value, missing="—", limit=240) {
    return typeof value === "string" && value.length > 0 && value.length <= limit ?
      value : missing;
  }
  function safeLink(url, label) {
    const anchor = make("a", label);
    try {
      const parsed = new URL(url);
      if (parsed.protocol !== "https:" || !parsed.hostname ||
          parsed.username || parsed.password) return make("span", label);
      anchor.href = parsed.href;
      anchor.target = "_blank";
      anchor.rel = "noopener noreferrer";
      return anchor;
    } catch (_) {
      return make("span", label);
    }
  }
  function originText(game) {
    return game.origin === "historical" ?
      "Histórica documentada" : "Simulação local (experimental)";
  }
  function resultText(game) {
    if (game.status !== "COMPLETED") return "Incompleta — sem resultado final";
    return {"1-0":"1–0", "0-1":"0–1", "1/2-1/2":"½–½"}[game.result] || "Resultado não informado";
  }
  function clearBoard() {
    byId("history-replay-board").replaceChildren();
    byId("history-replay-board").setAttribute("aria-label", "Sem partida selecionada");
    byId("history-replay-moves").replaceChildren();
    byId("history-replay-meta").replaceChildren();
    byId("history-replay-headline").textContent = "Posição inicial";
    byId("history-replay-progress").textContent = "0 / 0";
    const seek = byId("history-replay-seek");
    seek.max = "0";
    seek.value = "0";
    seek.disabled = true;
    for (const command of ["start","prev","next","end"]) {
      byId("history-replay-" + command).disabled = true;
    }
    byId("history-game-replay").hidden = true;
  }
  function clearAll() {
    games = [];
    selected = null;
    replay = null;
    ply = 0;
    listLoaded = false;
    listPending = false;
    replayPending = false;
    cards.clear();
    byId("history-game-list").replaceChildren();
    byId("history-games-status").textContent = "";
    clearBoard();
  }
  function invalidate() {
    generation += 1;
    return generation;
  }
  function leave() {
    invalidate();
    active = false;
    slug = null;
    clearAll();
    byId("history-games-section").hidden = true;
  }
  function suspend() {
    if (!active) return;
    invalidate();
    active = false;
    if (listPending) listLoaded = false;
    if (replayPending) {
      replay = null;
      clearBoard();
    }
    listPending = false;
    replayPending = false;
  }
  function resume() {
    if (!slug || active) return;
    active = true;
    if (!listLoaded) {
      fetchGames(slug);
    } else if (selected && !replay) {
      chooseGame(selected);
    }
  }
  function enter(playerSlug) {
    leave();
    if (typeof playerSlug !== "string" ||
        !/^[a-z0-9-]{2,48}$/.test(playerSlug)) return;
    slug = playerSlug;
    active = true;
    byId("history-games-section").hidden = false;
    fetchGames(playerSlug);
  }
  function validCard(game) {
    return game && typeof game === "object" && validId(game.game_id) &&
      ["historical","simulation"].includes(game.origin) &&
      ["COMPLETED","INCOMPLETE"].includes(game.status) &&
      typeof game.white_slug === "string" && typeof game.black_slug === "string" &&
      typeof game.source === "object" && game.source !== null &&
      typeof game.pgn_sha256 === "string" &&
      /^[a-f0-9]{64}$/.test(game.pgn_sha256) &&
      (game.status === "INCOMPLETE" ? game.result === null :
       ["1-0","0-1","1/2-1/2"].includes(game.result));
  }
  function playerName(playerSlug) {
    // Labels come from the canonical 64-player roster, not from PGN filenames.
    return safeText(window.ChessSimHistoryPlayerName ?
      window.ChessSimHistoryPlayerName(playerSlug) : playerSlug);
  }
  function drawCards() {
    const container = byId("history-game-list");
    container.replaceChildren();
    cards.clear();
    for (const [index, game] of games.entries()) {
      const item = make("button", null, "history-game-card");
      item.type = "button";
      item.setAttribute("aria-pressed", "false");
      item.append(make("span", String(index + 1) + " · " + originText(game), "history-game-origin"));
      item.append(make("strong", playerName(game.white_slug) + " (B) × " +
                                playerName(game.black_slug) + " (P)"));
      item.append(make("span", safeText(game.event) + " · " +
                        safeText(game.date, "Data não informada") + " · " +
                        resultText(game), "history-game-details"));
      item.addEventListener("click", () => chooseGame(game.game_id));
      cards.set(game.game_id, item);
      container.append(item);
    }
  }
  function updateSelected() {
    for (const [id, button] of cards) {
      button.setAttribute("aria-pressed", String(id === selected));
    }
  }
  async function fetchGames(playerSlug) {
    const request = invalidate();
    listPending = true;
    listLoaded = false;
    byId("history-games-status").textContent = "Carregando partidas documentadas…";
    try {
      const response = await fetch("/api/history/players/" + playerSlug + "/games",
                                   {cache:"no-store"});
      if (!response.ok) throw new Error("Falha ao ler catálogo");
      const data = await response.json();
      if (!active || generation !== request || slug !== playerSlug) return;
      if (data.schema !== "chesssim-history-games-v1" || data.slug !== playerSlug ||
          !Array.isArray(data.games) || data.games.length > 5 ||
          !data.games.every(validCard) ||
          new Set(data.games.map(g => g.game_id)).size !== data.games.length) {
        throw new Error("Catálogo inconsistente");
      }
      games = data.games;
      listPending = false;
      listLoaded = true;
      drawCards();
      if (games.length === 0) {
        byId("history-games-status").textContent =
          "Nenhuma partida disponível neste acervo local. Nenhum resultado foi inventado.";
      } else {
        byId("history-games-status").textContent =
          String(games.length) + " partida(s) disponível(is). Selecione uma para rever os lances.";
        chooseGame(games[0].game_id);
      }
    } catch (_) {
      if (active && generation === request && slug === playerSlug) {
        listPending = false;
        listLoaded = false;
        games = [];
        byId("history-game-list").replaceChildren();
        clearBoard();
        byId("history-games-status").textContent =
          "Não foi possível carregar as partidas. Volte ao elenco e tente novamente.";
      }
    }
  }
  function validStates(data, gameId, item) {
    if (!data || data.schema !== "chesssim-history-replay-v1" ||
        !data.game || data.game.game_id !== gameId ||
        data.game.origin !== item.origin ||
        data.game.white_slug !== item.white_slug ||
        data.game.black_slug !== item.black_slug ||
        data.game.status !== item.status ||
        data.game.result !== item.result ||
        data.game.pgn_sha256 !== item.pgn_sha256 ||
        !Array.isArray(data.states) || data.states.length < 1 ||
        data.states.length > 2001) return false;
    return data.states.every((position, index) =>
      position && typeof position === "object" &&
      typeof position.san === "string" && position.san.length <= 64 &&
      (index === 0 || position.san.length > 0) &&
      board.parseFen(position.fen) !== null &&
      (position.last === null ||
       (Array.isArray(position.last) && position.last.length === 2 &&
        position.last.every(s => typeof s === "string" && /^[a-h][1-8]$/.test(s)))));
  }
  async function chooseGame(gameId) {
    if (!active || !validId(gameId)) return;
    const item = games.find(g => g.game_id === gameId);
    if (!item) return;
    const request = invalidate();
    selected = gameId;
    replay = null;
    ply = 0;
    replayPending = true;
    updateSelected();
    clearBoard(); // Never display a stale board under a newly selected card.
    byId("history-games-status").textContent = "Carregando reprodução da partida…";
    try {
      const response = await fetch("/api/history/games/" + gameId + "/replay",
                                   {cache:"no-store"});
      if (!response.ok) throw new Error("PGN indisponível");
      const data = await response.json();
      if (!active || generation !== request || selected !== gameId) return;
      if (!validStates(data, gameId, item)) throw new Error("Replay inconsistente");
      replay = data;
      replayPending = false;
      ply = 0;
      renderReplay();
      byId("history-games-status").textContent =
        "Reprodução local disponível. Os lances são históricos ou simulados conforme o selo.";
    } catch (_) {
      if (active && generation === request && selected === gameId) {
        replay = null;
        replayPending = false;
        clearBoard();
        byId("history-games-status").textContent =
          "Partida indisponível ou PGN inválido. Selecione outra partida.";
      }
    }
  }
  function renderMeta() {
    const item = replay.game;
    const box = byId("history-replay-meta");
    box.replaceChildren();
    box.append(make("p", originText(item) + " · " + resultText(item),
                    "history-replay-origin"));
    box.append(make("p", playerName(item.white_slug) + " (brancas) × " +
                         playerName(item.black_slug) + " (pretas)"));
    box.append(make("p", safeText(item.event) + " · " +
                         safeText(item.date,"Data não informada")));
    if (item.origin === "historical") {
      const source = item.source || {};
      const line = make("p");
      line.append(make("span", "Fonte: "));
      line.append(safeLink(source.url, safeText(source.label, "Acervo documental")));
      box.append(line);
    } else {
      box.append(make("p", "Origem: sessão local do simulador; não é partida histórica real."));
    }
    if (item.status === "INCOMPLETE") {
      box.append(make("p", "Partida interrompida: sem resultado final.", "history-replay-warning"));
    }
  }
  function renderMoves() {
    const list = byId("history-replay-moves");
    const states = replay.states;
    list.replaceChildren();
    // Virtualize long games to avoid 2,000 live controls on the local page.
    const begin = Math.max(1, ply - 16);
    const end = Math.min(states.length - 1, Math.max(ply + 16, 32));
    list.start = begin;
    for (let index=begin; index<=end; index++) {
      const li = make("li");
      const moveNumber = Math.ceil(index / 2);
      const label = String(moveNumber) + (index % 2 ? ". " : "… ") + states[index].san;
      const button = make("button", label, "history-move-button");
      button.type = "button";
      button.setAttribute("aria-label", "Ir ao lance " + index + ": " + states[index].san);
      if (index === ply) {
        button.setAttribute("aria-current", "step");
        button.className += " selected";
      }
      button.addEventListener("click", () => step(index));
      li.append(button);
      list.append(li);
    }
  }
  function renderReplay() {
    if (!replay || !active) return;
    const states = replay.states;
    if (!board.paint(byId("history-replay-board"), states[ply], ply)) {
      replay = null;
      clearBoard();
      byId("history-games-status").textContent = "Posição do PGN inválida.";
      return;
    }
    byId("history-game-replay").hidden = false;
    renderMeta();
    const text = ply === 0 ? "Posição inicial" :
      String(Math.ceil(ply / 2)) + (ply % 2 ? ". " : "… ") + states[ply].san;
    byId("history-replay-headline").textContent = text;
    const max = states.length - 1;
    const seek = byId("history-replay-seek");
    seek.max = String(max);
    seek.value = String(ply);
    seek.disabled = max === 0;
    seek.setAttribute("aria-valuetext", text);
    byId("history-replay-progress").textContent = ply + " / " + max;
    byId("history-replay-start").disabled = ply === 0;
    byId("history-replay-prev").disabled = ply === 0;
    byId("history-replay-next").disabled = ply === max;
    byId("history-replay-end").disabled = ply === max;
    renderMoves();
  }
  function step(target) {
    if (!active || !replay || !Number.isInteger(target)) return;
    ply = Math.max(0, Math.min(replay.states.length - 1, target));
    renderReplay();
  }
  byId("history-replay-start").addEventListener("click", () => step(0));
  byId("history-replay-prev").addEventListener("click", () => step(ply - 1));
  byId("history-replay-next").addEventListener("click", () => step(ply + 1));
  byId("history-replay-end").addEventListener("click", () =>
    replay && step(replay.states.length - 1));
  byId("history-replay-seek").addEventListener("input", event =>
    step(Number(event.target.value)));
  byId("history-replay-board").addEventListener("keydown", event => {
    if (!active || !replay || event.altKey || event.ctrlKey || event.metaKey) return;
    const destinations = {
      ArrowLeft:ply-1, ArrowRight:ply+1, Home:0, End:replay.states.length-1
    };
    if (Object.prototype.hasOwnProperty.call(destinations,event.key)) {
      event.preventDefault();
      step(destinations[event.key]);
    }
  });
  window.ChessSimHistoryGames = Object.freeze({enter, leave, suspend, resume});
})();
