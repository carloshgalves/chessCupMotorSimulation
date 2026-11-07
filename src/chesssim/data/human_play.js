"use strict";
/* #57 Human vs UCI. Only the loopback server validates chess moves/clocks. */
(() => {
  const el = id => document.getElementById(id);
  const capability = new URLSearchParams(location.hash.slice(1)).get("token");
  const board = el("human-board");
  const state = {game:null, selected:null, side:"white", pending:false,
                 ready:false, mode:"duel", snapshotAt:0, boardId:null,
                 flipped:false, movesId:null, renderedPly:0,
                 tab:"moves", updateSeq:0, displayPly:0,
                 displayedAt:0,displayGameId:null,promotion:null};
  const FROM = /^[a-h][1-8]$/;
  const MOVE_DISPLAY_INTERVAL_MS = 650;
  const signed = {white:"Brancas",black:"Pretas"};
  const initial = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

  function mode(next) {
    if (!["duel","human"].includes(next)) return;
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
      const isActive=!!g&&g.human_turn&&!state.pending&&
                     state.displayPly===g.revision;
      square.tabIndex=isActive?0:-1;
      square.setAttribute("role","button");
      const selectable=isActive&&g.legal_moves.some(m=>m.startsWith(name));
      square.setAttribute("aria-disabled",String(!selectable&&
        !allowed.some(m=>m.slice(2,4)===name)));
      square.classList.toggle("human-selected",name===state.selected);
      const legal=allowed.some(m=>m.slice(2,4)===name);
      square.classList.toggle("human-legal",legal&&!square.classList.contains("whitepiece")&&
        !square.classList.contains("blackpiece"));
      square.classList.toggle("human-capture",legal&&
        (square.classList.contains("whitepiece")||square.classList.contains("blackpiece")));
    }
  }
  function displayBoard(g) {
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
      !ongoing?"Partida "+g.status.toLowerCase():
      yours?"Sua vez":"Motor pensando";
    el("human-turn-detail").textContent=!g?
      "Selecione um adversário para jogar.":
      pacing?"O jogo continua no tempo real; o tabuleiro apresenta cada lance em sequência.":
      !ongoing?"Resultado "+g.result+" · "+(g.termination||g.status):
      yours?"Selecione uma peça e sua casa de destino.":
      "O servidor está calculando o próximo lance.";
    const selfMoved=ongoing&&!state.pending;
    el("human-draw").disabled=!selfMoved;
    el("human-claim").disabled=!yours||state.pending;
    el("human-resign").disabled=!selfMoved;
    el("human-interrupt").disabled=!selfMoved;
    el("human-resume").hidden=g?.status!=="INTERRUPTED";
    el("human-resume").disabled=state.pending;
    el("human-result-details").textContent=g?
      ("Estado: "+g.status+" · Placar: "+g.result+
       " · Encerramento: "+(g.termination||"ainda não definido")):
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
  }
  async function applyMove(uci) {
    const g=state.game;
    if(!g||!g.human_turn||state.displayPly!==g.revision)return;
    state.selected=null;
    await command("/api/arena/human/move",{
      id:g.id,revision:g.revision,uci
    });
  }
  async function clickSquare(square) {
    const g=state.game;
    if(!g?.human_turn||state.pending||
       state.displayPly!==g.revision||state.promotion)return;
    const name=squareName(square);
    if(!name||!FROM.test(name))return;
    if(state.selected) {
      const candidates=g.legal_moves.filter(m=>m.startsWith(state.selected+name));
      if(candidates.length===1) {
        await applyMove(candidates[0]);return;
      }
      if(candidates.length>1) {
        state.promotion={from:state.selected,to:name,candidates};
        el("human-promotion").hidden=false;
        el("human-promotion").querySelector("button")?.focus();
        return;
      }
    }
    state.selected=g.legal_moves.some(m=>m.startsWith(name))?name:null;
    decorate(g);
  }
  const promo=el("human-promotion");
  for(const button of promo.querySelectorAll("[data-promote]")) {
    button.addEventListener("click",async()=>{
      const choice=state.promotion;
      if(!choice)return;
      const uci=choice.from+choice.to+button.dataset.promote;
      state.promotion=null;promo.hidden=true;
      if(choice.candidates.includes(uci))await applyMove(uci);
      else feedback("Promoção inválida nesta posição.",true);
    });
  }
  promo.addEventListener("keydown",event=>{
    if(event.key==="Escape"){
      state.promotion=null;promo.hidden=true;board.focus();
    }
  });
  el("human-board").addEventListener("click",e=>{
    const square=e.target.closest(".square");
    if(square&&board.contains(square))clickSquare(square);
  });
  board.addEventListener("keydown",e=>{
    if(e.key!=="Enter"&&e.key!==" ")return;
    const square=e.target.closest(".square");
    if(!square)return;
    e.preventDefault();
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
    await command("/api/arena/human/action",{id:g.id,action});
  });
  el("human-resume").addEventListener("click",async()=>{
    if(state.game)await command("/api/arena/human/resume",{id:state.game.id});
  });
  setTab("moves");
  refresh();
  setInterval(refresh,1100);
  setInterval(advancePlayback,100);
  setInterval(clocks,250);
})();
