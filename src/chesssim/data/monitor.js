"use strict";
/* No chess engine and no state-changing endpoint: this is a read-only viewer. */
const $ = (id) => document.getElementById(id);
const glyph = {
  K:"♔", Q:"♕", R:"♖", B:"♗", N:"♘", P:"♙",
  k:"♚", q:"♛", r:"♜", b:"♝", n:"♞", p:"♟"
};
const initialFEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
let feed = null;
let pinned = null;
let cursor = 0;
let activeKey = "";
let selectionSignature = "";
let fetching = false;

function availableGames() {
  return feed && feed.duel && Array.isArray(feed.duel.games) ? feed.duel.games : [];
}
function currentGame() {
  if (pinned) return pinned;
  const games = availableGames();
  return games[games.length - 1] || null;
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
function goLive() {
  pinned = null;
  activeKey = "";
  const game = currentGame();
  cursor = game ? game.states.length - 1 : 0;
  render();
}
function stepTo(ply) {
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
function chessSquares(fen) {
  const ranks = fen.split(" ")[0].split("/");
  if (ranks.length !== 8) return [];
  const rows = [];
  for (const rank of ranks) {
    const row = [];
    for (const piece of rank) {
      if (/^[1-8]$/.test(piece)) {
        for (let j = 0; j < Number(piece); j++) row.push("");
      } else row.push(piece);
    }
    if (row.length !== 8) return [];
    rows.push(row);
  }
  return rows;
}
function paintBoard(state) {
  const rows = chessSquares(state.fen);
  if (rows.length !== 8) return;
  const container = $("board");
  const fragment = document.createDocumentFragment();
  for (let rank = 0; rank < 8; rank++) {
    for (let file = 0; file < 8; file++) {
      const squareName = String.fromCharCode(97 + file) + String(8 - rank);
      const piece = rows[rank][file];
      const square = document.createElement("div");
      square.className = "square " + ((rank + file) % 2 ? "dark" : "light");
      if (state.last && state.last.includes(squareName)) square.classList.add("last");
      if (piece) {
        square.classList.add(piece === piece.toUpperCase() ? "whitepiece" : "blackpiece");
      }
      if (rank === 7) {
        square.classList.add("coordinate");
        square.dataset.coordinate = squareName[0];
      }
      const label = document.createElement("span");
      label.textContent = glyph[piece] || "";
      square.appendChild(label);
      square.setAttribute("aria-label", squareName + " " + (piece || "vazio"));
      fragment.appendChild(square);
    }
  }
  container.replaceChildren(fragment);
  container.setAttribute("aria-label", "Tabuleiro após " + cursor + " meios-lances");
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
                         (state.turn === "white" ? 0 : 1) === index);
  }
}
function displayMoves(game) {
  const list = $("moves");
  list.replaceChildren();
  const start = Math.max(1, cursor - 19);
  for (let n = start; n <= cursor; n++) {
    const item = document.createElement("li");
    item.textContent = Math.ceil(n / 2) + (n % 2 ? ". " : "... ") +
                       game.states[n].san;
    if (n === cursor) item.classList.add("selected");
    list.appendChild(item);
  }
  if (cursor === 0) {
    const placeholder = document.createElement("li");
    placeholder.textContent = "Aguardando lances";
    list.appendChild(placeholder);
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
function render() {
  updateSelector();
  const game = currentGame();
  const state = game ? game.states[Math.min(cursor, game.states.length - 1)] :
                {fen:initialFEN,last:null,turn:"white",clocks:[null,null]};
  paintBoard(state);
  paintClocks();
  const tournament = feed && feed.tournament;
  const duel = feed && feed.duel;
  const names = game ? game.white + " × " + game.black : "Aguardando partida";
  $("match-title").textContent = names;
  $("white-name").textContent = game ? game.white : "—";
  $("black-name").textContent = game ? game.black : "—";
  $("headline").textContent = game && cursor ? 
    Math.ceil(cursor / 2) + (cursor % 2 ? ". " : "... ") + state.san :
    "Posição inicial";
  $("match-status").textContent = !duel ? "Nenhum confronto iniciado." :
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
  $("view-status").textContent = pinned ?
    "Visualização pausada. O torneio continua jogando." :
    "A visualização acompanha o lance mais recente.";
  $("updated").textContent = game ?
    "Última atualização: " + new Date(game.updated_at * 1000).toLocaleTimeString("pt-BR") :
    "Sem atualizações de partida";
  if (game) displayMoves(game);
  else $("moves").replaceChildren();
}
async function poll() {
  if (fetching) return;
  fetching = true;
  try {
    const response = await fetch("/api/state", {cache:"no-store"});
    if (!response.ok) throw new Error("HTTP " + response.status);
    const next = await response.json();
    if (next.schema !== "chesssim-live-board-v1") throw new Error("Formato inesperado");
    feed = next;
    if (!pinned) {
      const game = currentGame();
      cursor = game ? game.states.length - 1 : 0;
    }
    $("connection").textContent = "● Conectado ao simulador";
    $("connection").className = "connection live";
    render();
  } catch (error) {
    $("connection").textContent = "● Sem atualização do simulador";
    $("connection").className = "connection error";
  } finally {
    fetching = false;
  }
}
$("games").addEventListener("change", () => {
  const chosen = availableGames().find(g => g.id === $("games").value);
  if (chosen) {
    pinned = chosen;
    activeKey = chosen.id;
    cursor = chosen.states.length - 1;
    render();
  }
});
$("pause").addEventListener("click", () => pinned ? goLive() : (freezeView(),render()));
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
setInterval(paintClocks, 250);
