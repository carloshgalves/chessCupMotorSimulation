"use strict";
/* #59. Ligas: approved sports dashboard, all dynamic data comes from local APIs.
 * This UI does not execute engines, fabricate PGNs or assign Elo to seasons. */
(() => {
  const $ = id => document.getElementById(id);
  const token = new URLSearchParams(window.location.hash.slice(1)).get("token");
  const state = {
    id:null,league:null,leagues:[],players:[],elo:new Map(),
    tab:"standings",round:null,historyOffset:0,historyPlayer:"",
    loading:false,pending:false,request:0,rosterReady:false,
    leagueSignature:null,lastHistoryKey:null,lastRoundKey:null,
    arenaBusy:false
  };
  const statuses = {
    READY:"Nova",RUNNING:"Em andamento",PAUSED:"Pausada",
    COMPLETED:"Concluída",FAILED:"Atenção"
  };
  const modes = {rapid:"Rápido · 15+10",classical:"Clássico · 60+30",
                 blitz:"Blitz · 3+2",bullet:"Bullet · 1+0"};
  const formats = {single:"Turno único",double:"Turno e returno"};
  const executions = {engine_full:"Motor UCI real",
                      fast_statistical:"Projeção estatística"};
  const $view=$("league-view");
  const el=(tag,cls,text)=>{
    const node=document.createElement(tag);
    if(cls)node.className=cls;
    if(text!==undefined)node.textContent=String(text);
    return node;
  };
  const replace=(id,...kids)=>$(id).replaceChildren(...kids);
  function notify(message,isError=false){
    const target=$("league-alert");
    target.textContent=message;
    target.className="league-alert"+(isError?" error":"");
  }
  function date(iso){
    if(!iso)return "Não registrado";
    const value=new Date(iso);
    if(!Number.isFinite(value.getTime()))return "Horário não disponível";
    return new Intl.DateTimeFormat("pt-BR",{
      dateStyle:"short",timeStyle:"short"
    }).format(value);
  }
  function meaningfulName(slug){
    return state.players.find(p=>p.slug===slug)?.name||slug;
  }
  function active(){
    return !$view.hidden;
  }
  function showLeague(){
    // The History module preserves its own navigation/replay state.
    window.ChessSimHistoryNavigation?.leave();
    $("arena-view").hidden=true;
    $("history-view").hidden=true;
    $view.hidden=false;
    for(const id of ["arena","history","leagues"])
      $("tab-"+id).setAttribute("aria-pressed",String(id==="leagues"));
    if(!state.rosterReady)loadRoster();
    refresh(true);
    $("league-heading").focus();
  }
  function hideLeague(){
    $view.hidden=true;
    state.request++;
  }
  window.ChessSimLeagueNavigation=Object.freeze({hide:hideLeague,show:showLeague});
  $("tab-leagues").addEventListener("click",showLeague);
  function safeUrl(path){
    // IDs/filters are strictly generated or validated, never interpolated HTML.
    return path;
  }
  async function get(path){
    const response=await fetch(safeUrl(path),{cache:"no-store"});
    const payload=await response.json();
    if(!response.ok)throw new Error(payload.error||"Falha ao consultar a temporada");
    return payload;
  }
  async function send(path,body={}){
    if(!token||token.length<32)
      throw new Error("Abra a URL com token completo informada pelo comando arena.");
    const response=await fetch(safeUrl(path),{
      method:"POST",cache:"no-store",
      headers:{"Content-Type":"application/json","X-Chesssim-Token":token},
      body:JSON.stringify(body)
    });
    const payload=await response.json();
    if(!response.ok)throw new Error(payload.error||"Ação recusada");
    return payload;
  }
  async function loadRoster(){
    try{
      const arena=await get("/api/arena");
      if(!Array.isArray(arena.players)||arena.players.length!==64)
        throw new Error("O catálogo não contém 64 enxadristas");
      state.players=arena.players;
      state.rosterReady=true;
      state.arenaBusy=Boolean(arena.busy);
      selectCount(true);
    }catch(error){notify(error.message,true);}
  }
  function selectedIds(){
    return [...$("league-participant-options").querySelectorAll("input[type=checkbox]")]
      .filter(input=>input.checked).map(input=>input.value);
  }
  function selectCount(reset=false){
    if(!state.players.length)return;
    const n=Number($("league-create-count").value);
    if(![4,8,16,32,64].includes(n))return;
    const box=$("league-participant-options");
    const chosen=reset||!box.children.length
      ?state.players.filter(p=>p.installed).slice(0,n).map(p=>p.slug)
      :selectedIds();
    for(const player of state.players){
      if(chosen.length>=n)break;
      if(!chosen.includes(player.slug))chosen.push(player.slug);
    }
    const current=new Set(chosen.slice(0,n));
    box.replaceChildren();
    for(const player of state.players){
      const label=el("label");
      const checkbox=document.createElement("input");
      checkbox.type="checkbox";checkbox.value=player.slug;
      checkbox.checked=current.has(player.slug);
      checkbox.addEventListener("change",()=>{
        if(checkbox.checked&&selectedIds().length>n){
          checkbox.checked=false;
          notify("Selecione no máximo "+n+" enxadristas. Desmarque um antes de trocar.",true);
        }
        updateParticipantNote();
      });
      const caption=el("span",null,player.name+
        (player.installed?"":" · perfil UCI ausente"));
      label.append(checkbox,caption);box.appendChild(label);
    }
    updateParticipantNote();
  }
  function updateParticipantNote(){
    const n=selectedIds().length;
    const need=Number($("league-create-count").value);
    $("league-participant-caption").textContent=n+" de "+need+
      " jogadores selecionados. O catálogo histórico não garante motor instalado.";
    $("league-create-submit").disabled=state.pending||n!==need||!token;
  }
  $("league-create-count").addEventListener("change",()=>selectCount(true));
  $("league-new-shortcut").addEventListener("click",()=>{
    $("league-create-panel").scrollIntoView({behavior:"smooth",block:"nearest"});
    $("league-create-name").focus();
  });
  function configFromForm(){
    return {
      name:$("league-create-name").value.trim(),
      season:$("league-create-season").value.trim(),
      modality:$("league-create-modality").value,
      format:$("league-create-format").value,
      execution_mode:$("league-create-execution").value,
      participants:selectedIds(),
      depth:Number($("league-create-depth").value),
      points:{
        win:Number($("league-points-win").value),
        draw:Number($("league-points-draw").value),
        loss:Number($("league-points-loss").value)
      },
    };
  }
  async function execute(handler){
    if(state.pending)return;
    state.pending=true;
    updateParticipantNote();
    try{await handler();}
    catch(error){notify(error.message,true);}
    finally{
      state.pending=false;
      updateParticipantNote();
      await refresh(true);
    }
  }
  $("league-create-form").addEventListener("submit",async event=>{
    event.preventDefault();
    if(!$("league-create-form").reportValidity())return;
    const config=configFromForm();
    const count=Number($("league-create-count").value);
    if(config.participants.length!==count){
      notify("Selecione exatamente "+count+" jogadores.",true);return;
    }
    if(config.points.win<=config.points.draw||
       config.points.draw<config.points.loss){
      notify("Pontuação: vitória > empate >= derrota.",true);return;
    }
    await execute(async()=>{
      const created=await send("/api/leagues/create",config);
      state.id=created.id;state.tab="standings";state.round=null;
      state.historyOffset=0;state.historyPlayer="";
      state.lastRoundKey=null;state.lastHistoryKey=null;
      notify("Liga criada. Escolha Executar rodada para iniciar.");
    });
  });
  async function refresh(force=false){
    if(!active()||state.loading||state.pending)return;
    state.loading=true;
    const request=++state.request;
    try{
      const [listing,arena]=await Promise.all([
        get("/api/leagues"),get("/api/arena")
      ]);
      if(request!==state.request||!active())return;
      state.leagues=listing.leagues;
      state.arenaBusy=Boolean(arena.busy);
      if(!state.rosterReady){
        state.players=arena.players;
        state.rosterReady=true;
        selectCount(true);
      }
      if(!state.id||!state.leagues.some(l=>l.id===state.id)){
        state.id=state.leagues[0]?.id||null;
        state.tab="standings";state.round=null;
        state.historyOffset=0;state.historyPlayer="";
        state.lastHistoryKey=null;state.lastRoundKey=null;
      }
      renderList();
      if(!state.id){
        state.league=null;state.leagueSignature=null;renderEmpty();
        return;
      }
      const detail=await get("/api/leagues/"+state.id);
      if(request!==state.request||!active())return;
      state.league=detail;
      render();
      if(force||!state.elo.has(detail.modality))await loadElo(detail.modality);
    }catch(error){notify(error.message,true);}
    finally{state.loading=false;}
  }
  async function loadElo(modality){
    if(!["rapid","classical","blitz","bullet"].includes(modality))return;
    try{
      const data=await get("/api/competition/ratings?modality="+modality);
      const mapping=new Map();
      for(const row of data.ratings||[])mapping.set(row.slug,row.display_rating);
      state.elo.set(modality,mapping);
      if(state.league?.modality===modality)drawTable();
    }catch(_){/* Rating unavailable must not block league standings. */}
  }
  function renderEmpty(){
    $("league-selection-empty").hidden=false;
    $("league-selection").hidden=true;
    $("league-run").disabled=true;
    $("league-pause").disabled=true;
    $("league-export-pgn").disabled=true;
    $("league-export-json").disabled=true;
    $("league-progress-bar").style.width="0%";
    $("league-progress-caption").textContent="Aguardando temporada";
    $("league-next-round").textContent="Aguardando liga";
    $("league-next-match").textContent="—";
    $("league-progress-games").textContent="0/0";
    $("league-progress-complete").textContent="0";
    $("league-progress-running").textContent="0";
    $("league-run-note").textContent="";
  }
  function renderList(){
    const list=$("league-list");list.replaceChildren();
    $("league-list-empty").hidden=state.leagues.length>0;
    for(const item of state.leagues){
      const button=el("button","league-list-item");
      button.type="button";
      button.setAttribute("aria-pressed",String(item.id===state.id));
      const icon=el("span","league-list-icon",
        item.status==="COMPLETED"?"♛":item.status==="PAUSED"?"♜":"♔");
      const info=el("span");
      const title=el("strong",null,item.name);
      const caption=el("small",null,item.season+" · "+
                            ({classical:"Clássico",rapid:"Rápido",blitz:"Blitz",
                              bullet:"Bullet"})[item.modality]);
      const status=el("small","league-mini-state",statuses[item.status]||item.status);
      info.append(title,caption,status);
      button.append(icon,info,el("span",null,"›"));
      button.addEventListener("click",()=>{
        state.id=item.id;state.tab="standings";state.round=null;
        state.historyOffset=0;state.historyPlayer="";
        state.lastRoundKey=null;state.lastHistoryKey=null;
        refresh(true);
      });
      list.appendChild(button);
    }
  }
  function tag(text){return el("span","league-tag",text);}
  function render(){
    const g=state.league;
    if(!g){renderEmpty();return;}
    $("league-selection-empty").hidden=true;
    $("league-selection").hidden=false;
    $("league-season-name").textContent="♜ "+g.name;
    replace("league-season-tags",
      tag(g.season+" · Simulação local"),
      tag(modes[g.modality]||g.modality),
      tag(g.participants_count+" jogadores"),
      tag(formats[g.format]||g.format));
    $("league-season-state").textContent=statuses[g.status]||g.status;
    const percent=g.total_games?100*g.completed_games/g.total_games:0;
    $("league-progress-bar").style.width=percent.toFixed(2)+"%";
    $("league-progress-caption").textContent="Rodada "+g.round_current+
      " de "+g.rounds_total+" ("+percent.toFixed(1).replace(".",",")+"%)";
    $("league-progress-games").textContent=g.completed_games+"/"+g.total_games;
    $("league-progress-complete").textContent=String(g.completed_games);
    $("league-progress-running").textContent=String(g.running_games);
    const missingProfiles=g.execution_mode==="engine_full" ?
      g.config.participants.filter(slug=>
        !state.players.find(p=>p.slug===slug)?.installed):[];
    const next=g.games.find(m=>m.status!=="COMPLETED");
    $("league-next-round").textContent=g.status==="COMPLETED"?
      "Temporada concluída":"Rodada "+g.round_current;
    $("league-next-match").textContent=next?
      (next.name_white+" × "+next.name_black):"Nenhuma partida pendente";
    $("league-run").disabled=state.pending||
      g.status==="COMPLETED"||g.status==="RUNNING"||
      (state.arenaBusy&&g.status!=="RUNNING")||!token||
      missingProfiles.length>0;
    $("league-run").textContent=g.status==="PAUSED"||g.status==="FAILED"?
      "▶ Retomar rodada":"▶ Executar rodada";
    $("league-pause").disabled=state.pending||g.status!=="RUNNING"||!token;
    $("league-export-json").disabled=state.pending;
    $("league-export-pgn").disabled=state.pending||g.available_pgn===0;
    $("league-run-note").textContent=missingProfiles.length?
      missingProfiles.length+" perfil(is) Rodent IV indisponível(is). Instale os motores para executar.":
      g.message||(g.execution_mode==="fast_statistical"?
       "Projeção, sem lances UCI nem PGN.":"Execução real: 1 partida por vez");
    updateTabs();
    if(state.tab==="standings")drawTable();
    if(state.tab==="rounds")drawRounds();
    if(state.tab==="games")drawGames();
    if(state.tab==="details")drawDetails();
  }
  function updateTabs(){
    for(const name of ["standings","rounds","games","details"]){
      const selected=state.tab===name;
      $("league-tab-"+name).setAttribute("aria-pressed",String(selected));
      $("league-tab-"+name).classList.toggle("selected",selected);
      $("league-pane-"+name).hidden=!selected;
    }
  }
  function setTab(name){
    if(!["standings","rounds","games","details"].includes(name))return;
    state.tab=name;
    state.lastRoundKey=null;state.lastHistoryKey=null;
    render();
  }
  for(const name of ["standings","rounds","games","details"])
    $("league-tab-"+name).addEventListener("click",()=>setTab(name));
  function drawTable(){
    const g=state.league;if(!g)return;
    const body=$("league-standings-body");
    const filter=$("league-player-search").value.trim().toLocaleLowerCase("pt-BR");
    const rating=state.elo.get(g.modality)||new Map();
    const signature=JSON.stringify([g.id,g.standings,filter,
                                     [...rating.entries()]]);
    if(signature===state.leagueSignature)return;
    state.leagueSignature=signature;
    body.replaceChildren();
    for(const row of g.standings){
      if(filter&&!row.name.toLocaleLowerCase("pt-BR").includes(filter))continue;
      const tr=el("tr");
      const rank=el("td");
      rank.appendChild(el("span","league-rank",row.rank));
      const identity=el("td");
      const player=el("button","league-player-button",row.name);
      player.type="button";player.title="Abrir partidas de "+row.name;
      player.addEventListener("click",()=>{
        state.historyPlayer=row.slug;state.historyOffset=0;
        $("league-game-player").value=row.slug;
        setTab("games");
      });
      identity.appendChild(player);
      const cell=value=>el("td",null,String(value));
      const points=el("td","league-points",row.points);
      tr.append(rank,identity,
        cell(row.played),cell(row.wins),cell(row.draws),cell(row.losses),
        points,cell(rating.get(row.slug)||"—"));
      body.appendChild(tr);
    }
    if(!body.children.length){
      const empty=el("tr");
      const td=el("td",null,"Nenhum enxadrista corresponde à busca.");
      td.colSpan=8;empty.appendChild(td);body.appendChild(empty);
    }
  }
  $("league-player-search").addEventListener("input",()=>{
    state.leagueSignature=null;drawTable();
  });
  function buildMatchItem(match){
    const item=el("div","league-game-item");
    const prefix=el("small",null,"Rodada "+match.round);
    const players=el("span",null,
      (match.white_name||match.name_white||meaningfulName(match.white))+" × "+
      (match.black_name||match.name_black||meaningfulName(match.black)));
    const result=el("strong",null,match.result||"Pendente");
    item.append(prefix,players,result);
    if(match.source==="fast_statistical")
      item.title="Resultado sintético: não há PGN nem jogo UCI";
    else if(match.finished_utc)
      item.title="Concluída "+date(match.finished_utc)+" (horário local)";
    return item;
  }
  function drawRounds(){
    const g=state.league;if(!g)return;
    const selector=$("league-round-selector");
    if(selector.children.length!==g.rounds_total){
      selector.replaceChildren();
      for(const row of g.round_progress){
        const option=el("option",null,"Rodada "+row.round+
          " ("+row.completed+"/"+row.total+")");
        option.value=String(row.round);selector.appendChild(option);
      }
    }else{
      for(const row of g.round_progress)
        selector.children[row.round-1].textContent="Rodada "+row.round+
          " ("+row.completed+"/"+row.total+")";
    }
    if(state.round===null)state.round=g.round_current;
    selector.value=String(state.round);
    const token=g.id+"|"+state.round+"|"+g.completed_games+"|"+g.status;
    if(state.lastRoundKey===token)return;
    state.lastRoundKey=token;
    $("league-round-matches").textContent="Carregando rodadas...";
    get("/api/leagues/"+g.id+"/round/"+state.round).then(report=>{
      if(!state.league||state.league.id!==g.id||
         state.round!==report.round||state.tab!=="rounds")return;
      replace("league-round-matches",...report.games.map(buildMatchItem));
    }).catch(error=>notify(error.message,true));
  }
  $("league-round-selector").addEventListener("change",()=>{
    state.round=Number($("league-round-selector").value);
    state.lastRoundKey=null;drawRounds();
  });
  $("league-view-matches").addEventListener("click",()=>{
    state.round=null;setTab("rounds");
  });
  function drawGames(){
    const g=state.league;if(!g)return;
    const filter=$("league-game-player");
    if(filter.children.length!==g.players.length+1||
       filter.dataset.league!==g.id){
      filter.replaceChildren();
      const all=el("option",null,"Todos");
      all.value="";filter.appendChild(all);
      for(const p of g.players){
        const option=el("option",null,p.name);
        option.value=p.slug;filter.appendChild(option);
      }
      filter.dataset.league=g.id;
    }
    filter.value=state.historyPlayer;
    const key=g.id+"|"+state.historyPlayer+"|"+state.historyOffset+"|"+
      g.completed_games;
    if(state.lastHistoryKey===key)return;
    state.lastHistoryKey=key;
    $("league-games-list").textContent="Carregando partidas...";
    const query=new URLSearchParams({
      offset:String(state.historyOffset),limit:"20"
    });
    if(state.historyPlayer)query.set("player",state.historyPlayer);
    get("/api/leagues/"+g.id+"/games?"+query.toString()).then(report=>{
      if(state.league?.id!==g.id||state.tab!=="games"||
         state.lastHistoryKey!==key)return;
      const items=report.games.map(buildMatchItem);
      replace("league-games-list",...(items.length?items:[
        el("p","league-muted","Nenhuma partida concluída nesta seleção.")
      ]));
      $("league-games-page").textContent=report.total?
        (report.offset+1)+"–"+(report.offset+report.games.length)+
          " de "+report.total:"0 partidas";
      $("league-games-prev").disabled=report.offset===0;
      $("league-games-next").disabled=report.offset+report.games.length>=report.total;
    }).catch(error=>notify(error.message,true));
  }
  $("league-game-player").addEventListener("change",()=>{
    state.historyPlayer=$("league-game-player").value;
    state.historyOffset=0;state.lastHistoryKey=null;drawGames();
  });
  $("league-games-prev").addEventListener("click",()=>{
    state.historyOffset=Math.max(0,state.historyOffset-20);
    state.lastHistoryKey=null;drawGames();
  });
  $("league-games-next").addEventListener("click",()=>{
    state.historyOffset+=20;state.lastHistoryKey=null;drawGames();
  });
  function drawDetails(){
    const g=state.league;if(!g)return;
    const config=g.config;
    const rows=[
      ["Nome",g.name],["Temporada fictícia",g.season],
      ["Participantes",g.participants_count+" enxadristas"],
      ["Formato",formats[g.format]],
      ["Controle de tempo",modes[g.modality]],
      ["Execução",executions[g.execution_mode]],
      ["Motor","Rodent IV (apenas no modo UCI real)"],
      ["Profundidade",config.depth+" níveis"],
      ["Pontuação","Vitória "+config.points.win+
                    ", empate "+config.points.draw+
                    ", derrota "+config.points.loss],
      ["Desempates","Pontos, vitórias, confronto direto completo (2), SB, slug"],
      ["Criada (local)",date(g.created_utc)],
      ["Iniciada (local)",date(g.execution_started_utc)],
      ["Atualizada (local)",date(g.updated_utc)]
    ];
    const dl=$("league-detail-facts");dl.replaceChildren();
    for(const [title,value] of rows)
      dl.append(el("dt",null,title),el("dd",null,value));
    $("league-source-warning").textContent=g.source_warning;
  }
  $("league-run").addEventListener("click",()=>{
    const g=state.league;if(!g)return;
    execute(async()=>{
      const data=await send("/api/leagues/"+g.id+"/run");
      notify("Rodada "+(data.round||"")+" iniciada. Execução serial no servidor.");
    });
  });
  $("league-pause").addEventListener("click",()=>{
    const g=state.league;if(!g)return;
    execute(async()=>{
      const data=await send("/api/leagues/"+g.id+"/pause");
      notify(data.message||"Pausa solicitada após a partida atual.");
    });
  });
  for(const fmt of ["json","pgn"])
    $("league-export-"+fmt).addEventListener("click",()=>{
      const g=state.league;if(!g)return;
      // Exact allowlisted ID from the server; GET never causes a write.
      if(!/^league-[a-f0-9]{16}$/.test(g.id))return;
      window.location.assign("/api/leagues/"+g.id+"/export/"+fmt);
    });
  refresh();
  setInterval(()=>{if(active())refresh();},2500);
})();
