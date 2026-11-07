"use strict";
/* No chess engine and no state-changing endpoint: this is a read-only viewer. */
const $ = (id) => document.getElementById(id);
// Filled silhouettes for both colors; the contrast is supplied by CSS.
const initialFEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
let feed = null;
let pinned = null;
let cursor = 0;
let activeKey = "";
let selectionSignature = null;
let attemptSignature = null;
let bracketSignature = null;
let fetching = false;
let requestVersion = 0;
let moveSignature = "";
let moveCells = [];
let followMoves = true;
let automaticMoveScroll = false;
// Presentation-only pacing: never change the engine clock or the PGN.
const LIVE_MOVE_INTERVAL_MS = 650;
let liveGameId = "";
let lastPresentedAt = 0;

function readSelection() {
  const params = new URLSearchParams(window.location.hash.slice(1));
  const match = params.get("match");
  if (!match || !/^r[0-9]{2}-m[0-9]{2}$/.test(match)) {
    return {match: null, attempt: null};
  }
  const raw = params.get("attempt");
  const attempt = raw && /^[1-9][0-9]{0,4}$/.test(raw) ? Number(raw) : null;
  return {match, attempt};
}
let selection = readSelection();

function setSelection(match, attempt=null) {
  const fragment = match ? "#match=" + encodeURIComponent(match) +
    (attempt !== null ? "&attempt=" + encodeURIComponent(String(attempt)) : "") : "";
  if (window.location.hash === fragment) {
    applySelection();
  } else {
    window.location.hash = fragment;
  }
}
function applySelection() {
  selection = readSelection();
  requestVersion++;
  pinned = null;
  activeKey = "";
  cursor = 0;
  selectionSignature = null;
  attemptSignature = null;
  bracketSignature = null;
  liveGameId = "";
  lastPresentedAt = 0;
  followMoves = true;
  poll();
}
window.addEventListener("hashchange", applySelection);

function availableGames() {
  return feed && feed.duel && Array.isArray(feed.duel.games) ? feed.duel.games : [];
}
function currentGame() {
  if (pinned) return pinned;
  const games = availableGames();
  // A second game can start before the first one's visual queue has drained.
  return games.find(game => game.id === liveGameId) ||
    games[games.length - 1] || null;
}
function acceptLiveSnapshot() {
  if (pinned) return;
  const games = availableGames();
  if (!games.length) {
    liveGameId = "";
    cursor = 0;
    return;
  }
  const index = games.findIndex(game => game.id === liveGameId);
  if (index < 0) {
    const game = games[games.length - 1];
    const isInitial = !liveGameId;
    liveGameId = game.id;
    // On entry show the current position. When the selected game changes,
    // start at its initial position so its first moves aren't skipped.
    cursor = isInitial ? game.states.length - 1 : 0;
    lastPresentedAt = Date.now();
  } else {
    cursor = Math.min(cursor, games[index].states.length - 1);
  }
}
function advanceLivePlayback(now = Date.now()) {
  if (pinned || !liveGameId || now - lastPresentedAt < LIVE_MOVE_INTERVAL_MS) return;
  const games = availableGames();
  const index = games.findIndex(game => game.id === liveGameId);
  if (index < 0) return;
  const game = games[index];
  if (cursor < game.states.length - 1) {
    // Advance exactly one half-move per visual tick, never jump to the tail.
    cursor += 1;
  } else if (index + 1 < games.length) {
    liveGameId = games[index + 1].id;
    cursor = 0;
  } else {
    return;
  }
  lastPresentedAt = now;
  render();
}
function freezeView() {
  if (!pinned) {
    const game = currentGame();
    if (game) {
      pinned = game;
      activeKey = game.id;
      cursor = Math.min(cursor, game.states.length - 1);
    }
  }
}
function resumeView() {
  pinned = null;
  activeKey = "";
  followMoves = true;
  const games = availableGames();
  const game = games[games.length - 1] || null;
  liveGameId = game ? game.id : "";
  cursor = game ? game.states.length - 1 : 0;
  lastPresentedAt = Date.now();
  render();
}
function goLive() {
  if (selection.match) {
    setSelection(null);
    return;
  }
  resumeView();
}
function stepTo(ply) {
  followMoves = false;
  freezeView();
  const game = currentGame();
  if (!game) return;
  cursor = Math.max(0, Math.min(game.states.length - 1, ply));
  render();
}
function clockText(seconds) {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return "—:—";
  const rounded = Math.max(0, Math.ceil(seconds));
  const min = Math.floor(rounded / 60);
  return String(min).padStart(2, "0") + ":" + String(rounded % 60).padStart(2, "0");
}
function paintBoard(state) {
  // Geometry, silhouettes and FEN parsing are shared with the History tab.
  return window.ChessSimBoard.paint($("board"), state, cursor);
}
function paintClocks() {
  const game = currentGame();
  const state = game && game.states[cursor];
  const clocks = state ? state.clocks.slice() : [null, null];
  if (game && !pinned && game.live && cursor === game.states.length - 1) {
    const moving = state.turn === "white" ? 0 : 1;
    if (clocks[moving] !== null && clocks[moving] !== undefined) {
      const since = Math.max(0, Date.now() / 1000 - game.updated_at);
      clocks[moving] = Math.max(0, clocks[moving] - since);
    }
  }
  $("white-clock").textContent = clockText(clocks[0]);
  $("black-clock").textContent = clockText(clocks[1]);
  for (const [index, id] of ["white-clock", "black-clock"].entries()) {
    const box = $(id);
    box.classList.toggle("low", clocks[index] !== null &&
                         clocks[index] !== undefined && clocks[index] < 30);
    box.classList.toggle("active", !!game && !pinned && game.live &&
                         cursor === game.states.length - 1 &&
                         (state.turn === "white" ? 0 : 1) === index);
  }
}
function displayMoves(game) {
  const list = $("moves");
  // The notation must advance with the paced board, not reveal future plies.
  const visiblePly = pinned ? game.states.length - 1 : cursor;
  const signature = game.id + "|" + visiblePly + "|" +
    game.states.slice(1, visiblePly + 1).map(x => x.san).join("|");
  if (signature !== moveSignature) {
    const priorScroll = list.scrollTop;
    const rows = [];
    const controls = [];
    for (let ply = 1; ply <= visiblePly; ply += 2) {
      const number = Math.ceil(ply / 2);
      const row = document.createElement("li");
      row.className = "move-row";
      for (const side of [0, 1]) {
        const cellPly = ply + side;
        if (cellPly <= visiblePly) {
          const cell = document.createElement("button");
          cell.type = "button";
          cell.className = "move-cell";
          cell.textContent = number + (side ? "... " : ". ") +
                             game.states[cellPly].san;
          cell.setAttribute("aria-label",
            "Lance " + number + (side ? " das pretas: " : " das brancas: ") +
            game.states[cellPly].san);
          cell.addEventListener("click", () => stepTo(cellPly));
          row.appendChild(cell);
          controls.push({ply:cellPly,cell});
        } else {
          const empty = document.createElement("span");
          empty.className = "move-empty";
          empty.textContent = "—";
          row.appendChild(empty);
        }
      }
      rows.push(row);
    }
    if (!rows.length) {
      const placeholder = document.createElement("li");
      placeholder.className = "move-empty";
      placeholder.textContent = "Aguardando lances";
      rows.push(placeholder);
    }
    list.replaceChildren(...rows);
    moveSignature = signature;
    moveCells = controls;
    if (!followMoves || pinned) list.scrollTop = priorScroll;
  }
  for (const item of moveCells) {
    const selected = item.ply === cursor;
    item.cell.classList.toggle("selected", selected);
    item.cell.setAttribute("aria-current", selected ? "step" : "false");
  }
  // Follow new turns only in live mode and only while the user is at the end.
  if (followMoves && !pinned && Number.isFinite(list.scrollHeight)) {
    automaticMoveScroll = true;
    list.scrollTop = list.scrollHeight;
    automaticMoveScroll = false;
  }
}
function updateSelector() {
  const games = availableGames();
  const signature = games.map(g => g.id + ":" + g.white + ":" + g.black).join("|");
  if (signature === selectionSignature) return;
  selectionSignature = signature;
  const selector = $("games");
  selector.replaceChildren();
  if (!games.length) {
    const option = document.createElement("option");
    option.textContent = "Aguardando confronto";
    option.value = "";
    selector.appendChild(option);
    selector.disabled = true;
    return;
  }
  selector.disabled = false;
  for (const g of games) {
    const option = document.createElement("option");
    option.value = g.id;
    option.textContent = "Jogo " + g.number + ": " + g.white + " × " + g.black;
    selector.appendChild(option);
  }
}
function updateAttemptSelector() {
  const select = $("attempts");
  const attempts = feed && Array.isArray(feed.attempts) ? feed.attempts : [];
  const match = feed && feed.selection && feed.selection.match_id;
  // O timestamp muda a cada lance; não recriar o select enquanto o usuário o opera.
  const signature = JSON.stringify({
    match, attempts: attempts.map(a => [a.index, a.status])
  });
  if (signature !== attemptSignature) {
    attemptSignature = signature;
    select.replaceChildren();
    const latest = document.createElement("option");
    latest.value = "";
    latest.textContent = attempts.length ?
      "Última tentativa (automática)" : "Nenhuma tentativa registrada";
    select.appendChild(latest);
    for (const attempt of attempts) {
      const option = document.createElement("option");
      option.value = String(attempt.index);
      option.textContent = "Tentativa " + attempt.index + " · " + attempt.status;
      select.appendChild(option);
    }
  }
  select.disabled = !match || attempts.length === 0;
  select.value = selection.attempt === null ? "" : String(selection.attempt);
}
function renderBracket() {
  const panel = $("bracket-panel");
  const tournament = feed && feed.tournament;
  panel.hidden = !tournament;
  if (!tournament) return;
  const bracket = Array.isArray(tournament.bracket) ? tournament.bracket : [];
  const names = tournament.names || {};
  const displayName = (slug) => names[slug] || slug;
  const current = selection.match || (feed.selection && feed.selection.match_id);
  $("bracket-status").textContent = tournament.champion ?
    "Campeão: " + displayName(tournament.champion) + " · Torneio encerrado" :
    tournament.matches_completed + " confrontos resolvidos · " +
    (tournament.status || "Aguardando") + " · Pontos acumulados por confronto";
  const signature = JSON.stringify({bracket, current, champion:tournament.champion});
  if (signature === bracketSignature) return;
  bracketSignature = signature;
  const holder = $("bracket");
  const oldScroll = holder.scrollTop;
  const fragment = document.createDocumentFragment();
  for (const rd of bracket) {
    const group = document.createElement("section");
    group.className = "round-group";
    const title = document.createElement("h3");
    title.className = "round-title";
    title.textContent = "Rodada " + rd.round;
    const meta = document.createElement("span");
    meta.className = "round-meta";
    meta.textContent = " · " + rd.matches.length + " confronto(s)";
    title.appendChild(meta);
    group.appendChild(title);
    for (const match of rd.matches) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "bracket-match";
      button.classList.toggle("selected", match.id === current);
      button.setAttribute("aria-pressed", match.id === current ? "true" : "false");
      button.setAttribute("aria-label", "Ver " + match.id + ": " +
        match.players.map(displayName).join(" contra "));
      const label = document.createElement("div");
      label.className = "match-label";
      const id = document.createElement("span");
      id.textContent = match.id;
      const status = document.createElement("span");
      status.textContent = match.status;
      label.append(id, status);
      button.appendChild(label);
      for (const slug of match.players) {
        const row = document.createElement("div");
        row.className = "match-line";
        if (match.winner === slug) row.classList.add("winner");
        if (match.winner && match.winner !== slug) row.classList.add("loser");
        const name = document.createElement("span");
        name.className = "match-name";
        name.textContent = displayName(slug);
        const score = document.createElement("span");
        score.className = "match-score";
        const value = match.score && match.score[slug];
        score.textContent = (typeof value === "number" ?
          value.toLocaleString("pt-BR", {maximumFractionDigits:1}) : "—") + " pt";
        row.append(name, score);
        button.appendChild(row);
      }
      button.addEventListener("click", () => setSelection(match.id));
      group.appendChild(button);
    }
    fragment.appendChild(group);
  }
  holder.replaceChildren(fragment);
  holder.scrollTop = oldScroll;
}
function render() {
  updateSelector();
  updateAttemptSelector();
  renderBracket();
  const game = currentGame();
  const state = game ? game.states[Math.min(cursor, game.states.length - 1)] :
                {fen:initialFEN,last:null,turn:"white",clocks:[null,null]};
  paintBoard(state);
  paintClocks();
  const tournament = feed && feed.tournament;
  const duel = feed && feed.duel;
  const chosenMatch = tournament && feed.selection &&
    tournament.bracket.flatMap(rd => rd.matches)
      .find(m => m.id === feed.selection.match_id);
  const names = game ? game.white + " × " + game.black : chosenMatch ?
    chosenMatch.players.map(slug => tournament.names[slug] || slug).join(" × ") :
    "Aguardando partida";
  $("match-title").textContent = names;
  $("white-name").textContent = game ? game.white : "—";
  $("black-name").textContent = game ? game.black : "—";
  $("headline").textContent = game && cursor ? 
    Math.ceil(cursor / 2) + (cursor % 2 ? ". " : "... ") + state.san :
    "Posição inicial";
  $("match-status").textContent = !duel ?
    (chosenMatch ? "Confronto ainda sem partidas registradas." : "Nenhum confronto iniciado.") :
    "Confronto: " + duel.status + (game ? " · Partida: " + game.status +
       " · Resultado: " + game.result : "");
  $("tournament-meta").textContent = tournament ?
    "Copa: " + tournament.status + " · Confrontos: " +
    tournament.matches_completed + "/" + tournament.matches_total_generated +
    (duel && duel.match_id ? " · " + duel.match_id : "") +
    (tournament.stop_requested ? " · Parada solicitada" : "") :
    (duel ? "Duelo independente · " + duel.folder : "—");
  $("ply-count").textContent = cursor + " / " + (game ? game.states.length - 1 : 0);
  $("seek").max = String(game ? game.states.length - 1 : 0);
  $("seek").value = String(cursor);
  $("games").value = pinned ? activeKey : game ? game.id : "";
  $("pause").textContent = pinned ? "▶ Continuar ao vivo" : "⏸ Pausar visualização";
  $("pause").setAttribute("aria-pressed", pinned ? "true" : "false");
  const games = availableGames();
  const currentIndex = game ? games.findIndex(x => x.id === game.id) : -1;
  const remaining = !pinned && currentIndex >= 0 ?
    Math.max(0, game.states.length - 1 - cursor) +
      games.slice(currentIndex + 1).reduce(
        (sum, next) => sum + Math.max(0, next.states.length - 1), 0) : 0;
  $("view-status").textContent = pinned ?
    "Visualização pausada. O torneio continua jogando." :
    remaining ?
    "Reprodução visual em sequência: " + remaining +
    " meio(s)-lance aguardando. Relógios refletem o lance exibido; " +
    "o motor continua em tempo real." :
    selection.match ? "Revisão histórica selecionada; o torneio pode seguir em outra rodada." :
    "Ao vivo. Cada meio-lance é apresentado separadamente.";
  $("updated").textContent = game ?
    "Última atualização: " + new Date(game.updated_at * 1000).toLocaleTimeString("pt-BR") :
    "Sem atualizações de partida";
  if (game) displayMoves(game);
  else {
    $("moves").replaceChildren();
    moveSignature = "";
    moveCells = [];
  }
}
async function poll() {
  if (fetching) return;
  fetching = true;
  const revision = requestVersion;
  try {
    const query = selection.match ?
      "?match=" + encodeURIComponent(selection.match) +
      (selection.attempt !== null ? "&attempt=" + String(selection.attempt) : "") : "";
    const response = await fetch("/api/state" + query, {cache:"no-store"});
    if (!response.ok) throw new Error("HTTP " + response.status);
    const next = await response.json();
    if (revision !== requestVersion) return;
    if (next.schema !== "chesssim-live-board-v1") throw new Error("Formato inesperado");
    feed = next;
    acceptLiveSnapshot();
    $("connection").textContent = "● Conectado ao simulador";
    $("connection").className = "connection live";
    render();
  } catch (error) {
    $("connection").textContent = "● Sem atualização do simulador";
    $("connection").className = "connection error";
  } finally {
    fetching = false;
    if (revision !== requestVersion) poll();
  }
}
$("moves").addEventListener("scroll", () => {
  if (automaticMoveScroll) return;
  const list = $("moves");
  if (Number.isFinite(list.scrollHeight) && Number.isFinite(list.clientHeight)) {
    followMoves = list.scrollHeight - list.scrollTop - list.clientHeight <= 24;
  }
});
$("games").addEventListener("change", () => {
  const chosen = availableGames().find(g => g.id === $("games").value);
  if (chosen) {
    pinned = chosen;
    activeKey = chosen.id;
    cursor = chosen.states.length - 1;
    render();
  }
});
$("attempts").addEventListener("change", () => {
  const match = selection.match || (feed && feed.selection && feed.selection.match_id);
  if (!match) return;
  const raw = $("attempts").value;
  setSelection(match, raw ? Number(raw) : null);
});
$("pause").addEventListener("click", () => pinned ? resumeView() : (freezeView(),render()));
$("live").addEventListener("click", goLive);
$("start").addEventListener("click", () => stepTo(0));
$("end").addEventListener("click", () => {
  const game = currentGame();
  if (game) stepTo(game.states.length - 1);
});
$("prev").addEventListener("click", () => stepTo(cursor - 1));
$("next").addEventListener("click", () => stepTo(cursor + 1));
$("seek").addEventListener("input", () => stepTo(Number($("seek").value)));
document.addEventListener("keydown", event => {
  if (event.target instanceof HTMLElement &&
      ["INPUT", "SELECT", "BUTTON"].includes(event.target.tagName)) return;
  if (event.key === "ArrowLeft") stepTo(cursor - 1);
  else if (event.key === "ArrowRight") stepTo(cursor + 1);
});
render();
poll();
setInterval(poll, 1200);
// Independent rendering cadence: fast UCI bursts are queued for the viewer.
setInterval(advanceLivePlayback, 100);
setInterval(paintClocks, 250);
