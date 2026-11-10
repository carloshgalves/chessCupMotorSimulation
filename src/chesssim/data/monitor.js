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
let refreshQueued = false;
let eventSource = null;
let eventFeedConnected = false;
let moveSignature = "";
let moveCells = [];
let followMoves = true;
let automaticMoveScroll = false;
// Presentation-only pacing: never change the engine clock or the PGN.
const LIVE_MOVE_INTERVAL_MS = 650;
let liveGameId = "";
let lastPresentedAt = 0;

// Optional observer-only game. The real board never submits guesses to UCI.
const guess = {enabled:false,selected:null,pending:null,promotion:null,
               attempts:0,correct:0,gameId:null,message:""};
let guessDrag = null;
let ignoreGuessClickUntil = 0;
const SQUARE=/^[a-h][1-8]$/;
function guessSquare(node) {
  const index=[...$("board").children].indexOf(node);
  if(index<0||index>=64)return null;
  return String.fromCharCode(97+index%8)+String(8-Math.floor(index/8));
}
function guessGame() {
  const g=currentGame();
  return guess.enabled && !pinned && !!g && g.live &&
    cursor===g.states.length-1 &&
    Array.isArray(g.states[cursor]?.legal_moves) ? g : null;
}
function resetGuess(message="") {
  guess.selected=null;guess.pending=null;guess.promotion=null;
  guess.message=message;
  $("guess-promotion").hidden=true;
}
function setGuessGame(g) {
  if(!g||guess.gameId===g.id)return;
  guess.gameId=g.id;guess.attempts=0;guess.correct=0;
  resetGuess();
}
function stageGuess(uci) {
  const g=guessGame(),moves=g?.states[cursor]?.legal_moves||[];
  if(!g || !moves.includes(uci) )return false;
  guess.pending={gameId:g.id,ply:cursor,uci,from:uci.slice(0,2),to:uci.slice(2,4)};
  guess.selected=null;guess.promotion=null;
  guess.message="Palpite registrado: "+uci+". Aguardando o lance real.";
  $("guess-promotion").hidden=true;
  renderGuess(g);
  return true;
}
function chooseGuess(from,to) {
  const g=guessGame(),moves=g?.states[cursor]?.legal_moves||[];
  const candidates=moves.filter(m=>m.slice(0,4)===from+to);
  if(candidates.length===1)return stageGuess(candidates[0]);
  if(candidates.length>1) {
    guess.promotion={from,to,candidates};
    $("guess-promotion").hidden=false;
    $("guess-promotion").querySelector?.("button")?.focus?.();
    return true;
  }
  return false;
}
function guessSelect(square) {
  const g=guessGame();
  if(!g||!SQUARE.test(square)||guess.promotion)return;
  const moves=g.states[cursor].legal_moves;
  if(guess.selected && chooseGuess(guess.selected,square))return;
  guess.selected=moves.some(m=>m.startsWith(square))?square:null;
  renderGuess(g);
}
function reconcileGuess() {
  const p=guess.pending;
  if(!p)return;
  const game=availableGames().find(g=>g.id===p.gameId);
  if(!game||currentGame()?.id!==p.gameId) {
    resetGuess("Partida alterada. Palpite descartado.");return;
  }
  if(game.states.length<=p.ply+1) {
    if(!game.live)resetGuess("Partida encerrada antes do próximo lance.");
    return;
  }
  // Reveal only when the paced viewer actually presents the next move.
  // A fast backend update must not spoil the guess before its board animation.
  if(cursor<p.ply+1)return;
  const observed=game.states[p.ply+1]?.uci;
  if(typeof observed!=="string") {
    resetGuess("Lance real sem UCI verificável. Não foi pontuado.");
    return;
  }
  guess.attempts++;
  const correct=observed===p.uci;
  if(correct)guess.correct++;
  resetGuess(correct?
    "Acertou! O jogador realizou "+observed+".":
    "Não foi dessa vez. Palpite: "+p.uci+" · realizado: "+observed+".");
}
function drawGuessPreview() {
  const layer=$("guess-piece-preview");
  layer.replaceChildren();
  const p=guess.pending,g=currentGame();
  // The server may already know the next move, while the viewer is still
  // presenting the previous position. Retain the ghost until that animation.
  if(!p||!guess.enabled||pinned||!g||
     g.id!==p.gameId||cursor!==p.ply)return;
  const source=[...$("board").children].find(s=>guessSquare(s)===p.from);
  const artwork=[...source?.children||[]].find(
    child=>child.classList?.contains("piece-glyph"));
  if(!source||!artwork)return; // Still keep a textual prediction when art is unavailable.
  const ghost=document.createElement("div");
  ghost.className="square "+(source.classList.contains("whitepiece")?
    "whitepiece":"blackpiece");
  ghost.style.gridColumnStart=String(p.to.charCodeAt(0)-96);
  ghost.style.gridRowStart=String(9-Number(p.to[1]));
  ghost.appendChild(artwork.cloneNode(true));
  const kind=$("board").getAttribute?.("data-piece-kind")||
    $("board").attributes?.["data-piece-kind"]||"glyph";
  layer.setAttribute("data-piece-kind",kind);
  layer.appendChild(ghost);
}
function renderGuess(game=currentGame()) {
  setGuessGame(game);
  const live=guessGame();
  const waiting=guess.enabled && !pinned && !!guess.pending &&
    game?.id===guess.pending.gameId && cursor===guess.pending.ply;
  $("guess-toggle").textContent=guess.enabled?"Desativar palpites":"Ativar palpites";
  $("guess-toggle").setAttribute("aria-pressed",String(guess.enabled));
  $("guess-cancel").disabled=!guess.pending&&!guess.selected;
  $("guess-score").textContent=guess.correct+" acerto(s) em "+
    guess.attempts+" tentativa(s)";
  $("guess-status").textContent=!guess.enabled?
    "Ative para tentar prever o próximo lance ao vivo.":
    guess.message||(!live?
      "Volte à última posição de uma partida em andamento para palpitar.":
      "Escolha uma peça e uma casa de destino no tabuleiro.");
  const legal=live?.states[cursor]?.legal_moves||[];
  const playable=guess.selected?
    legal.filter(m=>m.startsWith(guess.selected)).map(m=>m.slice(2,4)):
    legal.map(m=>m.slice(0,2));
  $("board").setAttribute("role",live?"group":"img");
  for(const square of $("board").children) {
    if(!square.className?.split(/\s+/).includes("square"))continue;
    const name=guessSquare(square);
    square.classList.toggle("guess-selected",!!live&&name===guess.selected);
    square.classList.toggle("guess-from",waiting&&name===guess.pending?.from);
    square.classList.toggle("guess-to",waiting&&name===guess.pending?.to);
    square.tabIndex=live&&playable.includes(name)?0:-1;
    square.setAttribute("role",live?"button":"presentation");
  }
  drawGuessPreview();
}
function guessSquareAt(x,y) {
  const board=$("board"),r=board.getBoundingClientRect?.();
  if(!r?.width||!r?.height||x<r.left||x>=r.right||
     y<r.top||y>=r.bottom)return null;
  const col=Math.floor((x-r.left)*8/r.width);
  const row=Math.floor((y-r.top)*8/r.height);
  return board.children[row*8+col]||null;
}


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
  resetGuess("Seleção alterada. Palpite descartado.");
  guess.gameId=null;
  poll();
  openEventFeed();
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
  resetGuess("Previsão cancelada: visualização pausada.");
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
    const knownPly = moveCells.length;
    const knownPrefix = game.id + "|" + knownPly + "|" +
      game.states.slice(1, knownPly + 1).map(x => x.san).join("|");
    const appendOnly = moveSignature === knownPrefix && visiblePly >= knownPly;
    if (!appendOnly || (knownPly === 0 && visiblePly > 0)) {
      // A different game, rewind or corrected PGN needs a fresh notation.
      // A new move in the same game never recreates previous move buttons.
      list.replaceChildren();
      if (!appendOnly) moveCells = [];
    }
    const makeCell = ply => {
      const number = Math.ceil(ply / 2);
      const black = ply % 2 === 0;
      const cell = document.createElement("button");
      cell.type = "button";
      cell.className = "move-cell";
      cell.textContent = number + (black ? "... " : ". ") + game.states[ply].san;
      cell.setAttribute("aria-label",
        "Lance " + number + (black ? " das pretas: " : " das brancas: ") +
        game.states[ply].san);
      cell.addEventListener("click", () => stepTo(ply));
      return cell;
    };
    for (let ply = moveCells.length + 1; ply <= visiblePly; ply++) {
      const cell = makeCell(ply);
      if (ply % 2) {
        const row = document.createElement("li");
        row.className = "move-row";
        const empty = document.createElement("span");
        empty.className = "move-empty";
        empty.textContent = "—";
        row.append(cell, empty);
        list.appendChild(row);
      } else {
        const row = list.children[list.children.length - 1];
        // Preserve the white button; replace only black's placeholder.
        row.replaceChildren(row.children[0], cell);
      }
      moveCells.push({ply,cell});
    }
    if (visiblePly === 0 && !list.children.length) {
      const placeholder = document.createElement("li");
      placeholder.className = "move-empty";
      placeholder.textContent = "Aguardando lances";
      list.appendChild(placeholder);
    }
    moveSignature = signature;
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
  reconcileGuess();
  const state = game ? game.states[Math.min(cursor, game.states.length - 1)] :
                {fen:initialFEN,last:null,turn:"white",clocks:[null,null]};
  paintBoard(state);
  paintClocks();
  renderGuess(game);
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
    if (revision !== requestVersion || refreshQueued) {
      refreshQueued = false;
      void poll();
    }
  }
}
// SSE provides early change notifications; /api/state is still authoritative
// and periodic polling remains a fallback if the stream is missing or stalled.
function eventQuery() {
  return selection.match ?
    "?match=" + encodeURIComponent(selection.match) +
    (selection.attempt !== null ? "&attempt=" + String(selection.attempt) : "") : "";
}
function eventRefresh() {
  if (fetching)refreshQueued = true;
  else void poll();
}
function openEventFeed() {
  if(eventSource)eventSource.close();
  eventSource=null;
  eventFeedConnected=false;
  if(typeof EventSource !== "function")return;
  let source;
  try {
    source = new EventSource("/api/events" + eventQuery());
  } catch (_) {
    // CSP or browser policy may forbid constructing a stream. The polling
    // fallback must still be installed and remain fully functional.
    return;
  }
  eventSource=source;
  source.addEventListener("open",()=>{
    if(eventSource===source)eventFeedConnected=true;
  });
  source.addEventListener("state",event=>{
    if(eventSource!==source)return; // Ignore stale session callbacks.
    try {
      const data=JSON.parse(event.data);
      if(data.schema!=="chesssim-game-events-v1" ||
         !/^[a-f0-9]{64}$/.test(data.revision))return;
    } catch (_) {return;}
    eventFeedConnected=true;
    eventRefresh();
  });
  source.addEventListener("unavailable",()=>{
    if(eventSource===source)eventFeedConnected=false;
  });
  source.addEventListener("error",()=>{
    if(eventSource===source)eventFeedConnected=false;
  });
}
window.addEventListener("pagehide",()=>{
  if(eventSource)eventSource.close();
  eventSource=null;eventFeedConnected=false;
});

$("guess-toggle").addEventListener("click",()=>{
  guess.enabled=!guess.enabled;
  resetGuess();
  render();
});
$("guess-cancel").addEventListener("click",()=>{
  resetGuess("Palpite cancelado.");renderGuess();
});
for(const button of $("guess-promotion").querySelectorAll("button")) {
  button.addEventListener("click",()=>{
    const p=guess.promotion;
    if(!p)return;
    const uci=p.from+p.to+button.dataset.promote;
    if(p.candidates.includes(uci))stageGuess(uci);
    else resetGuess("Promoção indisponível.");
  });
}
$("board").addEventListener("click",event=>{
  if(event.detail>0 && Date.now()<ignoreGuessClickUntil){
    ignoreGuessClickUntil=0;return;
  }
  const square=event.target.closest?.(".square");
  if(square)guessSelect(guessSquare(square));
});
$("board").addEventListener("keydown",event=>{
  if(event.key==="Escape") {
    if(guess.enabled){resetGuess("Palpite cancelado.");renderGuess();}
    return;
  }
  if(event.key!=="Enter"&&event.key!==" ")return;
  const square=event.target.closest?.(".square");
  if(square&&guess.enabled){event.preventDefault();guessSelect(guessSquare(square));}
});
$("board").addEventListener("pointerdown",event=>{
  if(event.button!==0||!guessGame())return;
  const square=event.target.closest?.(".square");
  const from=square&&guessSquare(square),g=guessGame();
  if(!g?.states[cursor].legal_moves.some(m=>m.startsWith(from)))return;
  guessDrag={id:event.pointerId,from,x:event.clientX,y:event.clientY};
});
$("board").addEventListener("pointerup",event=>{
  const drag=guessDrag;
  guessDrag=null;
  if(!drag||drag.id!==event.pointerId||!guessGame())return;
  if(Math.hypot(event.clientX-drag.x,event.clientY-drag.y)<8)return;
  const target=guessSquareAt(event.clientX,event.clientY);
  const to=target&&guessSquare(target);
  ignoreGuessClickUntil=Date.now()+200;
  if(to)chooseGuess(drag.from,to);
});
for(const event of ["pointercancel","lostpointercapture"])
  $("board").addEventListener(event,()=>{guessDrag=null;});

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
openEventFeed();
// Fast fallback on SSE failure; a slower safety reconciliation while connected.
setInterval(()=>{if(!eventFeedConnected)poll();},1200);
setInterval(()=>{if(eventFeedConnected)poll();},10000);
// Independent rendering cadence: fast UCI bursts are queued for the viewer.
setInterval(advanceLivePlayback, 100);
setInterval(paintClocks, 250);
