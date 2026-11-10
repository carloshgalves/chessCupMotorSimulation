"use strict";
/* #57 Human vs UCI. Only the loopback server validates chess moves/clocks. */
(() => {
  const el = id => document.getElementById(id);
  const capability = new URLSearchParams(location.hash.slice(1)).get("token");
  const board = el("human-board");
  const annotationLayer = el("human-annotations");
  const annotationClear = el("human-annotations-clear");
  const annotationStatus = el("human-annotations-status");
  const annotationColors = Object.freeze({
    yellow:"#f2c94c",red:"#ee5353",blue:"#579de6",green:"#56c484"
  });
  const arrows = new Map(), markedSquares = new Map();
  let rightGesture = null, annotationRevision = null;
  const state = {game:null, selected:null, side:"white", pending:false,
                 ready:false, mode:"duel", snapshotAt:0, boardId:null,
                 flipped:false, movesId:null, renderedPly:0,
                 tab:"moves", updateSeq:0, displayPly:0,
                 displayedAt:0,displayGameId:null,promotion:null};
  const FROM = /^[a-h][1-8]$/;
  const MOVE_DISPLAY_INTERVAL_MS = 650;
  const DRAG_THRESHOLD_PX = 8;
  let dragGesture = null;
  // One client-side intent; server validates moves and clocks.
  let premove = null;
  let ignorePointerClickUntil = 0;
  const signed = {white:"Brancas",black:"Pretas"};
  const humanStatus = status => ({
    COMPLETED:"Partida concluída", INCOMPLETE:"Partida inconclusiva",
    FAILED:"Falha na partida", INTERRUPTED:"Partida interrompida",
    RUNNING:"Em andamento"
  })[status] || status;
  const humanTermination = value => ({
    HumanResignation:"Desistência do jogador",
    HumanDrawClaim:"Empate reivindicado",
    TimeForfeit:"Tempo esgotado", CHECKMATE:"Xeque-mate",
    STALEMATE:"Afogamento", INSUFFICIENT_MATERIAL:"Material insuficiente",
    THREEFOLD_REPETITION:"Repetição de posição",
    FIFTY_MOVES:"Regra dos 50 lances", OperatorStop:"Interrupção solicitada",
    ServerShutdown:"Servidor encerrado", ServerRestart:"Servidor reiniciado",
    EngineFailure:"Falha do motor", CheckpointMismatch:"Checkpoint divergente",
    PlyLimit:"Limite de lances", WallTimeLimit:"Limite de execução",
  })[value] || value || "Sem causa definida";
  const initial = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

  function mode(next) {
    if (!["duel","human"].includes(next)) return;
    if (next !== "human") {
      cancelDrag();
      clearAnnotations();
      clearPremove();
    }
    state.mode = next;
    el("duel-form").hidden = next !== "duel";
    el("human-form").hidden = next !== "human";
    el("duel-viewer").hidden = next !== "duel";
    el("human-viewer").hidden = next !== "human";
    el("arena-mode-duel").classList.toggle("selected",next === "duel");
    el("arena-mode-human").classList.toggle("selected",next === "human");
    el("arena-mode-duel").setAttribute("aria-pressed",String(next === "duel"));
    el("arena-mode-human").setAttribute("aria-pressed",String(next === "human"));
    if(next === "human") render();
  }
  function feedback(msg,error=false) {
    const p=el("human-feedback");
    p.textContent=msg;
    p.className="feedback "+(error?"error":"good");
  }
  function clockText(n) {
    if (n === null || !Number.isFinite(n)) return "—:—";
    n = Math.max(0,Math.ceil(n));
    return String(Math.floor(n/60)).padStart(2,"0")+":"+
           String(n%60).padStart(2,"0");
  }
  function currentClock(idx) {
    const g=state.game;
    if (!g || g.clocks === null) return null;
    let seconds=g.clocks[idx];
    if (g.status === "RUNNING" &&
        (g.turn === "white" ? 0 : 1) === idx) {
      seconds-=Math.max(0,(Date.now()-state.snapshotAt)/1000);
    }
    return Math.max(0,seconds);
  }
  function clocks() {
    const blackAtBottom=state.game?.config.color==="black";
    // A black-side human sees the board reversed: opponent above, self below.
    el("human-black-clock").textContent=clockText(currentClock(blackAtBottom?0:1));
    el("human-white-clock").textContent=clockText(currentClock(blackAtBottom?1:0));
  }
  function fillOpponents(players) {
    const select=el("human-opponent");
    const existing=select.value || "fischer";
    select.replaceChildren();
    let available=0;
    for(const p of players) {
      const option=document.createElement("option");
      option.value=p.slug;
      option.disabled=!p.installed;
      option.textContent=p.name+(p.installed?" · disponível":" · instalação necessária");
      if(p.installed) available++;
      select.appendChild(option);
    }
    const selectable=players.find(p=>p.slug===existing&&p.installed);
    select.value=selectable?existing:(players.find(p=>p.installed)?.slug||"");
    state.ready=available>0;
    el("human-availability").textContent=available+
      " perfis UCI instalados. Os demais não possuem motor jogável; "+
      "a fidelidade histórica continua experimental.";
  }
  function currentConfig() {
    const preset=el("human-clock").value;
    const config={
      opponent:el("human-opponent").value, color:state.side,
      depth:Number(el("human-depth").value),preset,
      opening_mode:el("human-opening").value,
    };
    if (preset==="custom") {
      config.initial_seconds=Number(el("human-minutes").value)*60;
      config.increment_seconds=Number(el("human-increment").value);
    }
    return config;
  }
  async function send(path,data) {
    if(!capability || capability.length<32)
      throw new Error("Abra a URL local completa com token emitida pelo terminal.");
    const response=await fetch(path,{
      method:"POST",headers:{"Content-Type":"application/json",
                             "X-Chesssim-Token":capability},
      cache:"no-store",body:JSON.stringify(data)
    });
    const json=await response.json();
    if(!response.ok)throw new Error(json.error||"Comando recusado pelo servidor.");
    return json;
  }
  async function refresh() {
    if(state.pending) return;
    const seq=++state.updateSeq;
    try {
      const [arenaResponse,humanResponse]=await Promise.all([
        fetch("/api/arena",{cache:"no-store"}),
        fetch("/api/arena/human",{cache:"no-store"})
      ]);
      if(!arenaResponse.ok||!humanResponse.ok)throw new Error("Conexão indisponível");
      const arena=await arenaResponse.json();
      const human=await humanResponse.json();
      if(seq!==state.updateSeq) return;
      if(!state.ready)fillOpponents(arena.players);
      state.game=human.game;
      state.snapshotAt=Date.now();
      syncPlayback();
      const active=state.game?.status==="RUNNING";
      el("human-launch").disabled=!state.ready||!capability||
        arena.busy||active;
      render();
      void flushPremove();
    } catch(e) {
      if(state.mode==="human")feedback("Sem atualização local: "+e.message,true);
    }
  }
  function setTab(next) {
    state.tab=next;
    for(const tab of ["moves","info","result"]) {
      const active=tab===next;
      el("human-tab-"+tab).setAttribute("aria-pressed",String(active));
      el("human-tab-"+tab).classList.toggle("selected",active);
      el("human-"+tab).hidden=!active;
    }
  }
  function drawMoves(g) {
    const list=el("human-moves");
    const visiblePly=g?Math.min(state.displayPly,g.moves.length):0;
    if(!g||g.id!==state.movesId||visiblePly<state.renderedPly) {
      list.replaceChildren();
      state.movesId=g?.id||null;
      state.renderedPly=0;
    }
    if(!g)return;
    const nearBottom=list.scrollHeight-list.scrollTop-list.clientHeight<44;
    for(let ply=state.renderedPly+1;ply<=visiblePly;ply++) {
      const move=g.moves[ply-1];
      const cell=document.createElement("span");
      cell.textContent=move.san;
      cell.title="Lance "+Math.ceil(ply/2)+": "+move.san;
      if(ply%2) {
        const row=document.createElement("li");
        row.className="human-move-row";
        const number=document.createElement("span");
        number.className="move-number";
        number.textContent=String(Math.ceil(ply/2))+".";
        row.append(number,cell,document.createElement("span"));
        list.appendChild(row);
      } else {
        const row=list.lastElementChild;
        row.replaceChild(cell,row.lastElementChild);
      }
    }
    state.renderedPly=visiblePly;
    if(nearBottom)list.scrollTop=list.scrollHeight;
  }
  function info(g) {
    const dl=el("human-info-details");
    dl.replaceChildren();
    if(!g)return;
    const details=[
      ["Adversário",g.config.opponent],["Você",signed[g.config.color]],
      ["Profundidade",String(g.config.depth)],
      ["Ritmo",g.config.preset],["Lances",String(g.revision)],
      ["Origem","Jogo local contra Rodent IV; não é partida histórica"]
    ];
    for(const [label,value] of details) {
      const dt=document.createElement("dt"),dd=document.createElement("dd");
      dt.textContent=label;dd.textContent=value;dl.append(dt,dd);
    }
  }
  function orientation(g) {
    const black=g.config.color==="black";
    // Squares are rendered in canonical A8..H1 order. Reverse the DOM order
    // only on orientation changes; never rotate the SVG pieces themselves.
    if(state.flipped!==black) {
      board.replaceChildren(...[...board.children].reverse());
      state.flipped=black;
    }
  }
  function squareName(node) {
    const index=[...board.children].indexOf(node);
    if(index<0)return null;
    const canonical=state.flipped?63-index:index;
    return String.fromCharCode(97+canonical%8)+String(8-Math.floor(canonical/8));
  }
  function selectedMoves(g) {
    if(!g||!g.human_turn||state.displayPly!==g.revision||
       !state.selected)return [];
    return g.legal_moves.filter(m=>m.startsWith(state.selected));
  }
  function decorate(g) {
    const allowed=selectedMoves(g);
    for(const square of board.children) {
      const name=squareName(square);
      const isActive=!!g&&g.status==="RUNNING"&&!state.pending&&
                     state.displayPly===g.revision;
      square.tabIndex=isActive?0:-1;
      square.setAttribute("role","button");
      const selectable=isActive&&(g.human_turn?
        g.legal_moves.some(m=>m.startsWith(name)):isHumanPiece(square,g));
      square.setAttribute("aria-disabled",String(!selectable&&
        !allowed.some(m=>m.slice(2,4)===name)));
      square.classList.toggle("human-selected",name===state.selected&&g?.human_turn);
      square.classList.toggle("human-selected-premove",name===state.selected&&!g?.human_turn);
      square.classList.toggle("human-premove-from",name===premove?.from);
      square.classList.toggle("human-premove-to",name===premove?.to);
      const legal=allowed.some(m=>m.slice(2,4)===name);
      square.classList.toggle("human-legal",legal&&!square.classList.contains("whitepiece")&&
        !square.classList.contains("blackpiece"));
      square.classList.toggle("human-capture",legal&&
        (square.classList.contains("whitepiece")||square.classList.contains("blackpiece")));
      // ChessSimBoard.paint refreshes square.className on each poll. Restore
      // drag affordances while the pointer is held, without repainting pieces.
      const dragging=!!dragGesture?.dragged;
      square.classList.toggle("human-drag-origin",
        dragging&&dragGesture.square===square);
      square.classList.toggle("human-drop-hover",
        dragging&&dragGesture.hover===square);
      square.classList.toggle("human-premove-hover",
        dragging&&dragGesture.premove&&dragGesture.hover===square);
    }
  }
  function displayBoard(g) {
    // Chess.com-style analysis marks belong to the currently displayed
    // position, not to a future move or a different human-game session.
    syncAnnotationRevision(g);
    if(!g) {
      if(state.boardId!==null) {
        state.boardId=null;
        state.flipped=false;
        board.replaceChildren();
      }
      window.ChessSimBoard.paint(board,{fen:initial,last:null},0);
      return;
    }
    if(g.id!==state.boardId) {
      board.replaceChildren();
      state.boardId=g.id;
      state.flipped=false;
      state.selected=null;
    }
    const shown=g.states?.[Math.min(state.displayPly,g.states.length-1)];
    if(!shown)return;
    window.ChessSimBoard.paint(board,{
      fen:shown.fen,last:shown.last
    },state.displayPly);
    orientation(g);
    decorate(g);
  }
  function render() {
    if(state.mode!=="human")return;
    const g=state.game;
    if (dragGesture && (!g || g.id !== dragGesture.gameId ||
        g.revision !== dragGesture.revision || g.status !== "RUNNING" ||
        dragGesture.premove === g.human_turn || state.pending || state.promotion ||
        state.displayPly !== g.revision)) cancelDrag();
    displayBoard(g);
    const blackAtBottom=g?.config.color==="black";
    el("human-black-name").textContent=!g?"Adversário (motor UCI)":
      blackAtBottom?g.white:g.black;
    el("human-white-name").textContent=!g?"Você":
      blackAtBottom?g.black:g.white;
    clocks();drawMoves(g);info(g);
    const ongoing=g?.status==="RUNNING";
    const pacing=!!g&&state.displayPly<g.revision;
    const yours=ongoing&&g.human_turn&&!pacing;
    el("human-turn-title").textContent=!g?"Pronto para iniciar":
      pacing?"Reproduzindo os lances":
      !ongoing?humanStatus(g.status):
      yours?"Sua vez":premove?"Motor pensando · pré-move pronto":"Motor pensando";
    el("human-turn-detail").textContent=!g?
      "Selecione um adversário para jogar.":
      pacing?"O jogo continua no tempo real; o tabuleiro apresenta cada lance em sequência.":
      !ongoing?"Resultado "+g.result+" · "+humanTermination(g.termination):
      yours?"Selecione uma peça e sua casa de destino.":
      premove?"Pré-move "+premove.uci+" pendente: será validado após o lance do motor.":
       "O servidor está calculando o próximo lance.";
    const selfMoved=ongoing&&!state.pending;
    el("human-draw").disabled=!selfMoved;
    el("human-claim").disabled=!yours||state.pending;
    el("human-resign").disabled=!selfMoved;
    el("human-interrupt").disabled=!selfMoved;
    el("human-resume").hidden=g?.status!=="INTERRUPTED";
    el("human-resume").disabled=state.pending;
    el("human-result-details").textContent=g?
      (humanStatus(g.status)+" · Placar: "+g.result+
       " · Encerramento: "+humanTermination(g.termination)):
      "Nenhuma partida iniciada.";
    if(g?.draw_offer)feedback(g.draw_offer);
  }
  function syncPlayback() {
    const g=state.game;
    if(!g){state.displayGameId=null;state.displayPly=0;return;}
    if(state.displayGameId!==g.id) {
      state.displayGameId=g.id;
      // On reload enter at the current position; subsequent bursts are paced.
      state.displayPly=g.revision;
      state.displayedAt=Date.now();
    } else {
      state.displayPly=Math.min(state.displayPly,g.revision);
    }
  }
  function advancePlayback() {
    const g=state.game;
    if(!g||state.displayPly>=g.revision)return;
    const now=Date.now();
    if(now-state.displayedAt<MOVE_DISPLAY_INTERVAL_MS)return;
    state.displayPly++;
    state.displayedAt=now;
    render();
  }
  async function command(path,data) {
    if(state.pending)return;
    state.pending=true;
    try {
      const result=await send(path,data);
      await refreshForced();
      return result;
    } catch(e) {
      feedback(e.message,true);
      return null;
    } finally {
      state.pending=false;
      render();
      await refresh();
    }
  }
  async function refreshForced() {
    const r=await fetch("/api/arena/human",{cache:"no-store"});
    if(!r.ok)throw new Error("Falha ao atualizar a partida");
    const data=await r.json();
    state.game=data.game;state.snapshotAt=Date.now();
    syncPlayback();
    render();
    void flushPremove();
  }
  function isHumanPiece(square,g) {
    return !!g && !!square && square.classList.contains(
      g.config.color==="white"?"whitepiece":"blackpiece");
  }
  function canStagePremove(g,from) {
    if(state.mode!=="human" || !g || g.status!=="RUNNING" ||
       g.human_turn || state.pending || state.promotion ||
       state.displayPly!==g.revision || !FROM.test(from))return false;
    const square=[...board.children].find(node=>squareName(node)===from);
    return isHumanPiece(square,g);
  }
  function pieceAt(g,from) {
    const rows=window.ChessSimBoard.parseFen(g.states?.[g.revision]?.fen);
    return rows?.[8-Number(from[1])]?.[from.charCodeAt(0)-97]||"";
  }
  function renderPremove() {
    el("human-premove-cancel").disabled=!premove;
    el("human-premove-status").textContent=premove?
      "Pré-move: "+premove.uci+" · aguardando o motor":
      "Nenhum pré-move pendente";
    if(state.game && board.children.length===64)decorate(state.game);
  }
  function clearPremove(message) {
    premove=null;
    state.selected=null;
    if(message)feedback(message);
    renderPremove();
  }
  function queuePremove(from,to,suffix="") {
    const g=state.game;
    if(!canStagePremove(g,from)||!FROM.test(to)||from===to)return false;
    const pawn=pieceAt(g,from);
    const promotion=(pawn==="P"&&to[1]==="8")||
                    (pawn==="p"&&to[1]==="1");
    if(promotion && !suffix) {
      state.promotion={from,to,premove:true};
      el("human-promotion").hidden=false;
      el("human-promotion").querySelector("button")?.focus();
      return true;
    }
    if((promotion && !/^[qrbn]$/.test(suffix)) ||
       (!promotion && suffix))return false;
    premove={id:g.id,revision:g.revision,from,to,uci:from+to+suffix};
    state.selected=null;
    renderPremove();
    return true;
  }
  function flushPremove() {
    const queued=premove,g=state.game;
    if(!queued)return;
    if(!g || g.id!==queued.id || g.status!=="RUNNING" ||
       state.mode!=="human" || g.revision<queued.revision) {
      clearPremove("Pré-move cancelado: a partida mudou ou terminou.");
      return;
    }
    if(g.revision===queued.revision)return;
    if(!g.human_turn)return;
    // Consume before POST; overlapping polls cannot submit twice.
    clearPremove();
    if(!g.legal_moves.includes(queued.uci)) {
      feedback("Pré-move descartado: lance ilegal na nova posição.",true);
      return;
    }
    if(state.pending) {
      feedback("Pré-move descartado: outra ação já está em andamento.",true);
      return;
    }
    void command("/api/arena/human/move",{
      id:g.id,revision:g.revision,uci:queued.uci
    });
  }
  function canMoveFrom(g, from) {
    return state.mode === "human" && g?.status === "RUNNING" &&
      g.human_turn && !state.pending && !state.promotion &&
      state.displayPly === g.revision &&
      g.legal_moves.some(move => move.slice(0,2) === from);
  }
  async function applyMove(uci) {
    const g=state.game;
    if (!g || !canMoveFrom(g, uci.slice(0,2)) ||
        !g.legal_moves.includes(uci)) return;
    state.selected=null;
    await command("/api/arena/human/move",{
      id:g.id,revision:g.revision,uci
    });
  }
  async function moveFromTo(from, to) {
    const g=state.game;
    if(canStagePremove(g,from))return queuePremove(from,to);
    if(!canMoveFrom(g,from) || !FROM.test(to))return false;
    const candidates=g.legal_moves.filter(
      m=>m.slice(0,2)===from&&m.slice(2,4)===to);
    if(candidates.length===1) {
      await applyMove(candidates[0]);return true;
    }
    if(candidates.length>1) {
      state.promotion={from,to,candidates};
      el("human-promotion").hidden=false;
      el("human-promotion").querySelector("button")?.focus();
      return true;
    }
    return false;
  }
  async function clickSquare(square) {
    const g=state.game;
    if(!g||g.status!=="RUNNING"||state.pending||
       state.displayPly!==g.revision||state.promotion)return;
    const name=squareName(square);
    if(!name||!FROM.test(name))return;
    if(state.selected && await moveFromTo(state.selected,name))return;
    const chosen=[...board.children].find(s=>squareName(s)===name);
    state.selected=g.human_turn?
      (g.legal_moves.some(m=>m.startsWith(name))?name:null):
      (isHumanPiece(chosen,g)?name:null);
    decorate(g);
  }
  const promo=el("human-promotion");
  for(const button of promo.querySelectorAll("[data-promote]")) {
    button.addEventListener("click",async()=>{
      const choice=state.promotion;
      if(!choice)return;
      const uci=choice.from+choice.to+button.dataset.promote;
      state.promotion=null;promo.hidden=true;
      if(choice.premove)queuePremove(choice.from,choice.to,button.dataset.promote);
       else if(choice.candidates.includes(uci))await applyMove(uci);
      else feedback("Promoção inválida nesta posição.",true);
    });
  }
  promo.addEventListener("keydown",event=>{
    if(event.key==="Escape"){
      state.promotion=null;promo.hidden=true;board.focus();
    }
  });
  // Pointer Events unify mouse, touch and pen without a second chess rule
  // implementation. The server still validates game id, revision and UCI.
  function squareFromPoint(x,y) {
    const rect=board.getBoundingClientRect?.();
    if(!rect || !rect.width || !rect.height ||
       x<rect.left || x>=rect.right || y<rect.top || y>=rect.bottom)return null;
    const col=Math.floor((x-rect.left)/rect.width*8);
    const row=Math.floor((y-rect.top)/rect.height*8);
    return board.children[row*8+col]||null;
  }
  // Visual-only right-button annotations. The SVG is a *sibling* of the
  // board, not a 65th child: squareName/orientation still see exactly 64 cells.
  // Unlike moves, annotations may connect any two on-board squares.
  function annotationColor(event,kind) {
    if(event.ctrlKey)return kind==="arrow"?"red":"yellow";
    if(event.altKey)return "blue";
    if(event.shiftKey)return "green";
    return kind==="arrow"?"yellow":"red";
  }
  function annotationPoint(square) {
    if(!FROM.test(square))return null;
    const file=square.charCodeAt(0)-97,rank=Number(square[1]);
    const col=state.flipped?7-file:file;
    const row=state.flipped?rank-1:8-rank;
    return {x:(col+.5)*100,y:(row+.5)*100};
  }
  function svgElement(name,attributes) {
    const node=document.createElementNS("http://www.w3.org/2000/svg",name);
    for(const [key,value] of Object.entries(attributes))
      node.setAttribute(key,String(value));
    return node;
  }
  function annotationArrow(from,to,color,preview=false) {
    const a=annotationPoint(from),b=annotationPoint(to);
    if(!a||!b)return null;
    const dx=b.x-a.x,dy=b.y-a.y,dist=Math.hypot(dx,dy);
    if(dist<50)return null;
    const ux=dx/dist,uy=dy/dist,px=-uy,py=ux;
    const neckX=b.x-ux*41,neckY=b.y-uy*41;
    const tipX=b.x-ux*10,tipY=b.y-uy*10;
    const pair=(x,y)=>x.toFixed(2)+","+y.toFixed(2);
    const d=[
      "M"+pair(a.x+px*10,a.y+py*10),
      "L"+pair(neckX+px*10,neckY+py*10),
      "L"+pair(neckX+px*27,neckY+py*27),
      "L"+pair(tipX,tipY),
      "L"+pair(neckX-px*27,neckY-py*27),
      "L"+pair(neckX-px*10,neckY-py*10),
      "L"+pair(a.x-px*10,a.y-py*10),"Z"
    ].join(" ");
    return svgElement("path",{
      d,fill:annotationColors[color], "fill-opacity":preview?".46":".78",
      stroke:"#132129","stroke-opacity":preview?".08":".18",
      "stroke-width":"1.5",
      "data-arrow":from+"-"+to,"data-color":color
    });
  }
  function annotationSquare(square,color) {
    const p=annotationPoint(square);
    if(!p)return null;
    return svgElement("rect",{
      x:p.x-47,y:p.y-47,width:94,height:94,rx:4,
      fill:annotationColors[color],"fill-opacity":".40",
      "data-mark":square,"data-color":color
    });
  }
  function drawAnnotations() {
    const nodes=[];
    for(const [square,color] of markedSquares) {
      const shape=annotationSquare(square,color);
      if(shape)nodes.push(shape);
    }
    for(const value of arrows.values()) {
      const shape=annotationArrow(value.from,value.to,value.color);
      if(shape)nodes.push(shape);
    }
    if(rightGesture?.hover && rightGesture.hover!==rightGesture.from) {
      const shape=annotationArrow(rightGesture.from,rightGesture.hover,
        rightGesture.arrowColor,true);
      if(shape)nodes.push(shape);
    }
    annotationLayer.replaceChildren(...nodes);
    const total=arrows.size+markedSquares.size;
    annotationClear.disabled=total===0;
    annotationStatus.textContent=total===0?"Nenhuma marcação":
      arrows.size+" seta(s) · "+markedSquares.size+" casa(s) marcada(s)";
  }
  function cancelRightGesture() {
    const gesture=rightGesture;
    if(!gesture)return;
    rightGesture=null;
    try {
      if(board.hasPointerCapture?.(gesture.pointerId))
        board.releasePointerCapture(gesture.pointerId);
    } catch (_) { /* Browser may already have released pointer capture. */ }
    drawAnnotations();
  }
  function clearAnnotations() {
    cancelRightGesture();
    if(!arrows.size && !markedSquares.size)return;
    arrows.clear();markedSquares.clear();
    drawAnnotations();
  }
  function syncAnnotationRevision(g) {
    const current=g?g.id+":"+g.revision:"idle";
    if(annotationRevision!==current) {
      annotationRevision=current;
      clearAnnotations();
    }
  }
  function startRightGesture(event) {
    if(state.mode!=="human" || dragGesture || rightGesture ||
       event.button!==2 || event.isPrimary===false ||
       event.pointerType==="touch")return false;
    const square=event.target.closest?.(".square");
    if(!square || !board.contains(square))return false;
    const from=squareName(square);
    if(!FROM.test(from))return false;
    rightGesture={
      pointerId:event.pointerId,from,hover:from,
      arrowColor:annotationColor(event,"arrow"),
      squareColor:annotationColor(event,"square")
    };
    try {board.setPointerCapture?.(event.pointerId);}
    catch (_) {cancelRightGesture();return false;}
    event.preventDefault?.();
    return true;
  }
  function moveRightGesture(event) {
    if(!rightGesture || event.pointerId!==rightGesture.pointerId)return false;
    // Losing the right button without pointerup must not create a phantom arrow.
    if(event.buttons!==undefined && !(event.buttons&2)) {
      cancelRightGesture();return true;
    }
    const target=squareFromPoint(event.clientX,event.clientY);
    const hover=target&&squareName(target);
    if(hover!==rightGesture.hover) {
      rightGesture.hover=hover;
      drawAnnotations();
    }
    event.preventDefault?.();
    return true;
  }
  function finishRightGesture(event) {
    if(!rightGesture || event.pointerId!==rightGesture.pointerId)return false;
    const gesture=rightGesture;
    const target=squareFromPoint(event.clientX,event.clientY);
    const to=target&&squareName(target);
    cancelRightGesture();
    if(!to)return true; // Drag outside board is always cancelled.
    if(to===gesture.from) {
      const previous=markedSquares.get(to);
      if(previous===gesture.squareColor)markedSquares.delete(to);
      else markedSquares.set(to,gesture.squareColor);
    } else {
      const key=gesture.from+":"+to;
      const previous=arrows.get(key);
      if(previous?.color===gesture.arrowColor)arrows.delete(key);
      else {
        // Prevent unbounded SVG allocation after many annotation gestures.
        if(!arrows.has(key) && arrows.size>=64)
          arrows.delete(arrows.keys().next().value);
        arrows.set(key,{from:gesture.from,to,color:gesture.arrowColor});
      }
    }
    drawAnnotations();
    event.preventDefault?.();
    return true;
  }

  function dragPreview(source, event) {
    const piece=source.querySelector?.(".piece-glyph");
    const rect=board.getBoundingClientRect?.();
    const index=[...board.children].indexOf(source);
    if(!piece || !document.body || !rect?.width || !rect?.height ||
       index<0)return null;
    const width=rect.width/8,height=rect.height/8;
    const offsetX=Math.max(0,Math.min(width,
      event.clientX-(rect.left+(index%8)*width)));
    const offsetY=Math.max(0,Math.min(height,
      event.clientY-(rect.top+Math.floor(index/8)*height)));
    const preview=document.createElement("div");
    preview.className="board human-drag-preview";
    preview.setAttribute("aria-hidden","true");
    for(const key of ["data-board-theme","data-piece-style","data-piece-kind"]) {
      const value=board.getAttribute(key);
      if(value)preview.setAttribute(key,value);
    }
    const cell=document.createElement("div");
    // No light/dark class: the user drags the piece, never a duplicate tile.
    cell.className="square "+(source.classList.contains("whitepiece")?
      "whitepiece":"blackpiece");
    preview.appendChild(cell);
    preview.style.width=width+"px";
    preview.style.height=height+"px";
    // Relocate the actual loaded artwork. Cloning an <img> can flash an
    // unloaded/blank SVG and show a second faded piece on the source square.
    const previousFontSize=piece.style.fontSize;
    if(piece.dataset.artReady!=="1") {
      const originalSize=window.getComputedStyle?.(piece)?.fontSize;
      piece.style.fontSize=originalSize&&originalSize.endsWith("px")?
        originalSize:Math.min(width*.85,68)+"px";
    }
    document.body.appendChild(preview);
    cell.appendChild(piece);
    const floating={element:preview,piece,source,previousFontSize,
      offsetX,offsetY};
    positionDragPreview(floating,event);
    return floating;
  }
  function positionDragPreview(floating,event) {
    // Keep the point where the user grabbed the piece under their cursor.
    // Centering it after the drag threshold would make the artwork jump.
    floating.element.style.left=(event.clientX-floating.offsetX)+"px";
    floating.element.style.top=(event.clientY-floating.offsetY)+"px";
  }
  function releaseDrag() {
    const gesture=dragGesture;
    if(!gesture)return null;
    dragGesture=null;
    if(gesture.preview) {
      // Restore the very same node, even for cancelled/illegal drops. It
      // carries the already-loaded image and must never disappear on polling.
      const {element,piece,source,previousFontSize}=gesture.preview;
      piece.style.fontSize=previousFontSize;
      if(board.contains(source))source.appendChild(piece);
      element.remove();
    }
    gesture.square.classList.remove("human-drag-origin");
    for(const square of board.children)
      square.classList.remove("human-drop-hover");
    try {
      if(gesture.square.hasPointerCapture?.(gesture.pointerId))
        gesture.square.releasePointerCapture(gesture.pointerId);
    } catch (_) { /* Capture may already have been released by the browser. */ }
    return gesture;
  }
  function cancelDrag() {
    const gesture=releaseDrag();
    if(gesture?.dragged) {
      state.selected=null;
      if(state.game && board.children.length===64)decorate(state.game);
    }
  }
  board.addEventListener("pointerdown", event=>{
    if(event.button===2) {
       const square=event.target.closest?.(".square");
       if(premove && square && squareName(square)===premove.from){
         clearPremove("Pré-move cancelado.");
         event.preventDefault?.();return;
       }
       startRightGesture(event);
       return;
     }
    if(rightGesture)return;
    if(event.button===0 && state.mode==="human")clearAnnotations();
    if(dragGesture || event.isPrimary===false ||
       (event.pointerType==="mouse" && event.button!==0) ||
       (event.button!==undefined && event.button!==0))return;
    const square=event.target.closest?.(".square");
    if(!square || !board.contains(square))return;
    const from=squareName(square),g=state.game;
    const staging=canStagePremove(g,from);
    if(!staging && !canMoveFrom(g,from))return;
    dragGesture={pointerId:event.pointerId,square,from,premove:staging,
      gameId:g.id,revision:g.revision,
      startX:event.clientX,startY:event.clientY,
      dragged:false,preview:null};
    try { square.setPointerCapture?.(event.pointerId); }
    catch (_) { dragGesture=null; }
  });
  board.addEventListener("pointermove", event=>{
    if(moveRightGesture(event))return;
    const gesture=dragGesture;
    if(!gesture || event.pointerId!==gesture.pointerId)return;
    const g=state.game;
    if(!(gesture.premove?canStagePremove(g,gesture.from):
         canMoveFrom(g,gesture.from)) || g.id!==gesture.gameId ||
       g.revision!==gesture.revision){cancelDrag();return;}
    if(!gesture.dragged &&
       Math.hypot(event.clientX-gesture.startX,
                  event.clientY-gesture.startY)<DRAG_THRESHOLD_PX)return;
    if(!gesture.dragged) {
      // Capture the grab position from pointerdown, not the first move
      // beyond the 8px threshold; otherwise the piece jumps on pickup.
      gesture.preview=dragPreview(gesture.square,{
        clientX:gesture.startX,clientY:gesture.startY
      });
      if(!gesture.preview){cancelDrag();return;}
      gesture.dragged=true;
      state.selected=gesture.from;
      decorate(g);
    }
    event.preventDefault?.();
    if(gesture.preview)positionDragPreview(gesture.preview,event);
    const target=squareFromPoint(event.clientX,event.clientY);
    const name=target&&squareName(target);
    const legal=!!name&&(gesture.premove?name!==gesture.from:
      g.legal_moves.some(m=>m.slice(0,2)===gesture.from&&m.slice(2,4)===name));
    gesture.hover=legal?target:null;
    for(const square of board.children)
      square.classList.toggle("human-drop-hover",square===gesture.hover);
  });
  board.addEventListener("pointerup",event=>{
    if(finishRightGesture(event))return;
    const gesture=dragGesture;
    if(!gesture || event.pointerId!==gesture.pointerId)return;
    releaseDrag();
    if(!gesture.dragged)return; // Short press still uses click-to-click.
    ignorePointerClickUntil=Date.now()+200;
    state.selected=null;
    if(state.game&&board.children.length===64)decorate(state.game);
    const g=state.game;
    if(!(gesture.premove?canStagePremove(g,gesture.from):
         canMoveFrom(g,gesture.from))||g.id!==gesture.gameId||
       g.revision!==gesture.revision)return;
    const destination=squareFromPoint(event.clientX,event.clientY);
    const to=destination&&squareName(destination);
    if(to)void (gesture.premove?queuePremove(gesture.from,to):
      moveFromTo(gesture.from,to));
  });
  board.addEventListener("pointercancel",event=>{
    if(rightGesture?.pointerId===event.pointerId)cancelRightGesture();
    if(dragGesture?.pointerId===event.pointerId)cancelDrag();
  });
  board.addEventListener("lostpointercapture",event=>{
    if(rightGesture?.pointerId===event.pointerId)cancelRightGesture();
    if(dragGesture?.pointerId===event.pointerId)cancelDrag();
  });
  board.addEventListener("contextmenu",event=>{
    // Pointer capture can retarget contextmenu to the board element itself.
    // Still prevent the native menu, but only inside the human board.
    if(state.mode==="human" && (event.target===board ||
       board.contains(event.target?.closest?.(".square"))))
      event.preventDefault();
  });
  window.addEventListener?.("blur",()=>{
    cancelDrag();cancelRightGesture();
  });
  document.addEventListener?.("visibilitychange",()=>{
    if(document.hidden){cancelDrag();cancelRightGesture();}
  });
  annotationClear.addEventListener("click",clearAnnotations);
  el("human-board").addEventListener("click",e=>{
    if(e.detail===0)clearAnnotations(); // Keyboard-activated click.
    if(e.detail>0 && Date.now()<ignorePointerClickUntil) {
      // Consume exactly one browser compatibility click, not every genuine
      // click the user may make within the next 200 milliseconds.
      ignorePointerClickUntil=0;
      e.preventDefault?.();return;
    }
    const square=e.target.closest(".square");
    if(square&&board.contains(square))clickSquare(square);
  });
  board.addEventListener("keydown",e=>{
    if(e.key==="Escape") {
      clearAnnotations();
      cancelDrag();
      clearPremove("Pré-move cancelado.");
      e.preventDefault();
      return;
    }
    if(e.key!=="Enter"&&e.key!==" ")return;
    const square=e.target.closest(".square");
    if(!square)return;
    e.preventDefault();
    clearAnnotations();
    clickSquare(square);
  });
  for(const id of ["duel","human"]) {
    el("arena-mode-"+id).addEventListener("click",()=>mode(id));
  }
  for(const button of el("human-side-buttons").querySelectorAll("button")) {
    button.addEventListener("click",()=>{
      state.side=button.dataset.side;
      for(const other of el("human-side-buttons").querySelectorAll("button")) {
        const pressed=other===button;
        other.setAttribute("aria-pressed",String(pressed));
        other.classList.toggle("selected",pressed);
      }
    });
  }
  el("human-clock").addEventListener("change",()=>{
    el("human-custom-clock").hidden=el("human-clock").value!=="custom";
  });
  el("human-form").addEventListener("submit",async e=>{
    e.preventDefault();
    if(!el("human-form").reportValidity())return;
    feedback("Iniciando a partida no motor UCI...");
    clearPremove();
    await command("/api/arena/human/start",currentConfig());
  });
  for(const tab of ["moves","info","result"])
    el("human-tab-"+tab).addEventListener("click",()=>setTab(tab));
  for(const [id,action,confirmText] of [
    ["human-draw","offer_draw",null],
    ["human-claim","claim_draw",null],
    ["human-resign","resign","Desistir da partida? O resultado será definitivo."],
    ["human-interrupt","interrupt","Interromper e salvar o jogo para retomada posterior?"]
  ]) el(id).addEventListener("click",async()=>{
    const g=state.game;
    if(!g)return;
    if(confirmText&&!window.confirm(confirmText))return;
    if(["resign","interrupt"].includes(action))clearPremove();
    await command("/api/arena/human/action",{id:g.id,action});
  });
  el("human-resume").addEventListener("click",async()=>{
    if(state.game)await command("/api/arena/human/resume",{id:state.game.id});
  });
  el("human-premove-cancel").addEventListener("click",()=>{
    clearPremove("Pré-move cancelado.");render();
  });
  setTab("moves");
  refresh();
  setInterval(refresh,1100);
  setInterval(advancePlayback,100);
  setInterval(clocks,250);
})();
