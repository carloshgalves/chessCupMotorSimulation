"use strict";
/* Historical profile dashboard and complete local-game archive (#70).
 * Text-only DOM. Documentary biographies and simulated outcomes stay separate.
 * PGN replay only from evidence-verified local games. */
(() => {
  const $ = id => document.getElementById(id);
  const state = {
    slug:null,players:[],profile:null,active:false,version:0,
    pane:"overview",filters:{q:"",origin:"all",modality:"all",result:"all",color:"all",opponent:"",engine_sha256:"",date_from:"",date_to:"",year_from:"",year_to:""},
    page:1,perPage:20,report:null,initial:null,replay:null,ply:0,
    ratingMode:"classical",loading:false,
  };
  const el=(tag,value,cls)=>{
    const n=document.createElement(tag);
    if(value!==undefined&&value!==null)n.textContent=String(value);
    if(cls)n.className=cls;
    return n;
  };
  const modeLabel={
    classical:"Clássico",rapid:"Rápido",blitz:"Blitz",bullet:"Bullet",
    unclassified:"Sem ritmo definido"
  };
  const originLabel={
    arena:"UCI · Arena",tournament:"UCI · Torneio",league:"UCI · Liga",
    cup:"UCI · Copa",projection:"Projeção sintética"
  };
  const resultLabel={
    win:"Vitória",loss:"Derrota",draw:"Empate",
    incomplete:"Incompleta"
  };
  function status(message,bad=false){
    $("archive-hub-status").textContent=message;
    $("archive-hub-status").style.color=bad?"#ffb5a9":"#f2d494";
  }
  async function get(url){
    const response=await fetch(url,{cache:"no-store"});
    const data=await response.json();
    if(!response.ok)throw new Error(data.error||"Consulta indisponível");
    return data;
  }
  function pageQuery(){
    const params=new URLSearchParams();
    for(const [key,value] of Object.entries(state.filters))
      if(value&&value!=="all")params.set(key,value);
    params.set("page",String(state.page));
    params.set("page_size",String(state.perPage));
    return params.toString();
  }
  function playerName(slug){
    return state.players.find(x=>x.slug===slug)?.name||slug;
  }
  function formatDate(raw){
    if(!raw)return "Sem data";
    const date=new Date(raw);
    return Number.isFinite(date.getTime())
      ?new Intl.DateTimeFormat("pt-BR",{day:"2-digit",month:"2-digit",year:"numeric",hour:"2-digit",minute:"2-digit",timeZoneName:"short"})
        .format(date)
      :"Não informada";
  }
  function overviewStat(label,value){
    const cell=el("div");
    cell.append(el("small",label),el("strong",value));
    return cell;
  }
  function setPane(name){
    if(!["overview","games","tournaments","elo","records"].includes(name))return;
    state.pane=name;
    for(const tab of ["overview","games","tournaments","elo","records"]){
      $("archive-tab-"+tab).setAttribute("aria-pressed",String(tab===name));
      $("archive-pane-"+tab).hidden=tab!==name;
    }
    $("history-original").hidden=name!=="records";
    if(name==="elo")loadRating();
  }
  for(const name of ["overview","games","tournaments","elo","records"]){
    $("archive-tab-"+name).addEventListener("click",()=>setPane(name));
  }
  $("archive-all-games").addEventListener("click",()=>setPane("games"));
  $("archive-curated-open").addEventListener("click",()=>setPane("records"));
  $("archive-roster-mode").addEventListener("change",()=>{
    $("archive-modality").value=$("archive-roster-mode").value;
    state.filters.modality=$("archive-roster-mode").value;
    state.page=1;
    load();
  });
  $("archive-roster-search").addEventListener("input",drawRoster);
  $("archive-rating-modality").addEventListener("change",()=>{
    state.ratingMode=$("archive-rating-modality").value;
    loadRating();
  });
  function drawRoster(){
    const q=$("archive-roster-search").value.trim().toLocaleLowerCase("pt-BR");
    const box=$("archive-player-roster");box.replaceChildren();
    for(const p of state.players){
      if(!p.name.toLocaleLowerCase("pt-BR").includes(q))continue;
      const button=el("button",null,"archive-roster-person");
      button.type="button";
      button.setAttribute("aria-pressed",String(p.slug===state.slug));
      const desc=el("span");
      desc.append(el("strong",p.name),el("small",p.era_label||"Período em revisão"));
      button.append(desc,el("span","›"));
      button.addEventListener("click",()=>{
        if(state.slug!==p.slug)window.ChessSimHistoryNavigation?.open(p.slug);
      });
      box.appendChild(button);
    }
    if(!box.children.length)box.append(el("p","Nenhum enxadrista encontrado."));
  }
  function profileDetails(){
    const p=state.profile;
    if(!p)return;
    $("archive-player-name").textContent=p.name;
    $("archive-player-era").textContent=p.era_label||"Período em revisão";
    $("archive-player-bio").textContent=p.short_bio||
      "Biografia ainda não disponível no acervo local.";
    $("archive-player-status").textContent=
      p.review_status==="reviewed"?"Perfil documentado":"Perfil em curadoria";
    const tags=$("archive-profile-tags");tags.replaceChildren();
    tags.append(el("span","História real ≠ simulação"),
                el("span",p.review_status==="reviewed"?
                  "Fontes documentais revisadas":"Fontes em curadoria"),
                el("span","Rating do simulador isolado por modalidade"));
    const timeline=$("archive-career-timeline");timeline.replaceChildren();
    for(const event of (p.timeline||[]).slice(0,10)){
      const item=el("li");
      item.append(el("strong",String(event.year)+" · "),
                  el("span",event.title||event.summary));
      timeline.append(item);
    }
    if(!timeline.children.length)timeline.append(el("li",
      "Nenhum marco cronológico aprovado para este perfil."));
  }
  function fillOverview(data){
    const o=data.overview;
    $("archive-overview-stats").replaceChildren(
      overviewStat("UCI verificados",o.verified),
      overviewStat("Concluídas",o.completed),
      overviewStat("Vitórias",o.wins),
      overviewStat("Empates",o.draws),
      overviewStat("Derrotas",o.losses),
      overviewStat("Projeções",o.projections),
      overviewStat("Cobertura parcial",data.coverage.partial?"Sim":"Não"),
      overviewStat("Registros rejeitados",data.coverage.rejected_records));
    const recent=$("archive-recent-games");recent.replaceChildren();
    for(const game of data.games.slice(0,5)){
      const line=el("div",null,"archive-recent-row");
      line.append(el("small",formatDate(game.date)),
        el("span",game.competition+" · "+game.opponent_name),
        el("strong",game.result||"—"));
      recent.append(line);
    }
    if(!recent.children.length)recent.append(el("p","Sem partidas locais."));
    const modes=$("archive-mode-performance");modes.replaceChildren();
    const table=el("table",null,"archive-mode-table");
    const header=el("thead"),row=el("tr");
    for(const title of ["Modalidade","Partidas","V","E","D","Aproveitamento"])
      row.append(el("th",title));
    header.append(row);table.append(header);
    const body=el("tbody");
    for(const [key,value] of Object.entries(o.modalities)){
      const tr=el("tr");
      const p=value.games?(100*(value.wins+value.draws/2)/value.games).toFixed(1)+"%":"—";
      for(const x of [modeLabel[key],value.games,value.wins,value.draws,value.losses,p])
        tr.append(el("td",x));
      body.append(tr);
    }
    table.append(body);modes.append(table);
    fillTournaments(data.competitions);
  }
  function fillTournaments(competitions){
    const box=$("archive-tournament-list");box.replaceChildren();
    for(const g of competitions||[]){
      const card=el("div",null,"archive-tournament-row");
      card.append(el("strong",g.name),
        el("small",g.games+" registros · "+g.verified+
             " UCI verificados · "+g.projections+" projeções"));
      box.append(card);
    }
    if(!box.children.length)box.append(el("p",
      "Nenhum registro local de torneio para este enxadrista."));
  }
  function fillRows(data){
    $("archive-total-count").textContent=data.total+" registros encontrados";
    const cards=$("archive-source-cards");cards.replaceChildren();
    for(const [title,count] of [
      ["UCI verificadas",data.overview.verified],
      ["Projeções sintéticas",data.overview.projections],
      ["Exclusões / rejeições",data.coverage.rejected_records]
    ]){
      const item=el("div");
      item.append(el("small",title),el("strong",count));
      cards.append(item);
    }
    const box=$("archive-games-table-body");box.replaceChildren();
    for(const game of data.games){
      const tr=el("tr");
      const badge=el("span",originLabel[game.origin]||"Registro");
      if(game.origin==="projection")badge.className="archive-projection";
      const result=el("span",resultLabel[game.outcome]||"—",
        game.outcome==="win"?"archive-win":game.outcome==="loss"?"archive-loss":"");
      const action=el("button",game.pgn?"▶ Abrir replay":
        game.origin==="projection"?"Sem PGN":"Ver registro");
      action.type="button";
      action.disabled=!game.pgn;
      if(game.pgn)action.addEventListener("click",()=>openReplay(game));
      const cell=el("td");cell.append(badge);
      const buttonCell=el("td");buttonCell.append(action);
      for(const value of [formatDate(game.date),game.competition,game.opponent_name,
                            game.color==="white"?"Brancas":"Pretas",
                            modeLabel[game.modality]||game.modality]){
        tr.append(el("td",value));
      }
      const res=el("td");res.append(result);
      tr.append(res,cell,buttonCell);box.append(tr);
    }
    if(!data.games.length){
      const empty=el("tr"),cell=el("td",
        "Nenhum registro corresponde aos filtros atuais. Nenhum resultado foi inventado.");
      cell.colSpan=8;empty.append(cell);box.append(empty);
    }
    const begin=(data.page-1)*data.page_size;
    $("archive-page-status").textContent=data.total?
      "Exibindo "+(begin+1)+"–"+(begin+data.games.length)+" de "+data.total:
      "Nenhuma partida";
    $("archive-current-page").textContent=
      "Página "+data.page+" de "+data.pages;
    $("archive-prev-page").disabled=data.page<=1;
    $("archive-next-page").disabled=data.page>=data.pages;
    $("archive-export-pgn").disabled=!data.games.some(g=>g.pgn);
    const warning=data.coverage.partial?
      "Cobertura parcial: existem registros rejeitados ou indisponíveis.":"";
    $("archive-search-status").textContent=warning;
  }
  function syncForm(){
    $("archive-search").value=state.filters.q;
    $("archive-origin").value=state.filters.origin;
    $("archive-modality").value=state.filters.modality;
    $("archive-result").value=state.filters.result;
    $("archive-color").value=state.filters.color;
    $("archive-opponent").value=state.filters.opponent;
    $("archive-engine").value=state.filters.engine_sha256;
    $("archive-date-from").value=state.filters.date_from;
    $("archive-date-to").value=state.filters.date_to;
    $("archive-year-from").value=state.filters.year_from;
    $("archive-year-to").value=state.filters.year_to;
    $("archive-roster-mode").value=state.filters.modality;
  }
  async function load(){
    if(!state.active||!state.slug)return;
    // Concurrent queries are intentional; generation rejects stale replies.
    state.loading=true;
    const version=++state.version,slug=state.slug;
    const url="/api/history/players/"+slug+"/archive?"+pageQuery();
    status("Consultando somente arquivos locais verificados…");
    try{
      const report=await get(url);
      if(!state.active||version!==state.version||slug!==state.slug)return;
      if(report.schema!=="chesssim-player-archive-v1"||report.slug!==slug)
        throw new Error("Schema do arquivo incompatível");
      state.report=report;
      fillRows(report);
      if(!state.initial){
        state.initial=report;
        fillOverview(report);
      }
      status("");
    }catch(error){
      if(version===state.version)status(error.message,true);
    }finally{if(version===state.version)state.loading=false;}
  }
  $("archive-filters").addEventListener("submit",event=>{
    event.preventDefault();
    state.filters={
      q:$("archive-search").value.trim(),origin:$("archive-origin").value,
      modality:$("archive-modality").value,result:$("archive-result").value,
      color:$("archive-color").value,opponent:$("archive-opponent").value.trim().toLowerCase(),
      engine_sha256:$("archive-engine").value.trim().toLowerCase(),
      date_from:$("archive-date-from").value,date_to:$("archive-date-to").value,
      year_from:$("archive-year-from").value,
      year_to:$("archive-year-to").value
    };
    state.page=1;
    load();
  });
  $("archive-prev-page").addEventListener("click",()=>{
    if(state.page>1){state.page--;load();}
  });
  $("archive-next-page").addEventListener("click",()=>{
    if(state.report&&state.page<state.report.pages){state.page++;load();}
  });
  for(const kind of ["csv","json","pgn"]){
    $("archive-export-"+kind).addEventListener("click",()=>{
      if(!state.slug)return;
      window.location.assign("/api/history/players/"+state.slug+
        "/archive/export/"+kind+"?"+pageQuery());
    });
  }
  async function loadRating(){
    if(!state.active||!state.slug)return;
    const version=state.version,slug=state.slug;
    $("archive-rating-info").textContent="Carregando Elo experimental…";
    try{
      const data=await get("/api/history/players/"+slug+"/rating?modality="+
        encodeURIComponent(state.ratingMode));
      if(!state.active||slug!==state.slug||version!==state.version)return;
      const row=data.ratings?.[0];
      if(!row)throw new Error("Elo do jogador indisponível");
      $("archive-rating-info").textContent=
        "Elo ChessSimulation ("+modeLabel[state.ratingMode]+"): "+
        row.display_rating+" · "+row.games+" partidas verificadas. "+
        "Condição: "+row.status+". Início experimental em 2500; "+
        "não corresponde ao Elo da carreira real.";
    }catch(error){
      if(version===state.version)$("archive-rating-info").textContent=error.message;
    }
  }
  const replayState={states:null,id:null,ply:0};
  async function openReplay(game){
    if(!game.pgn||!state.active)return;
    const slug=state.slug,version=state.version;
    try{
      const p=new URLSearchParams({slug,id:game.id});
      const data=await get("/api/history/archive/replay?"+p.toString());
      if(!state.active||slug!==state.slug||version!==state.version)return;
      if(data.schema!=="chesssim-archive-replay-v1"||
         !Array.isArray(data.states)||!data.states.length)
        throw new Error("Replay incompatível");
      replayState.states=data.states;
      replayState.id=game.id;
      $("archive-replay-title").textContent="Replay · "+playerName(data.game.white)+
        " × "+playerName(data.game.black);
      $("archive-replay-metadata").textContent=
        game.competition+" · "+game.result+" · PGN local verificado";
      const moves=$("archive-replay-moves");moves.replaceChildren();
      data.states.slice(1).forEach((step,i)=>{
        const li=el("li");
        const btn=el("button",
          (i%2===0?Math.floor(i/2)+1+". ":"… ")+(step.san||"lance "+(i+1)));
        btn.type="button";btn.addEventListener("click",()=>paint(i+1));
        li.append(btn);moves.append(li);
      });
      paint(0);
      $("archive-replay-dialog").showModal();
    }catch(error){status(error.message,true);}
  }
  function paint(ply){
    if(!replayState.states)return;
    const max=replayState.states.length-1;
    replayState.ply=Math.max(0,Math.min(max,ply));
    const current=replayState.states[replayState.ply];
    if(!window.ChessSimBoard?.paint(
         $("archive-replay-board"),current,replayState.ply)){
      $("archive-replay-metadata").textContent="Posição PGN inválida";
      return;
    }
    $("archive-replay-counter").textContent=replayState.ply+"/"+max;
    for(const [id,disabled] of [
      ["first",replayState.ply===0],["prev",replayState.ply===0],
      ["next",replayState.ply===max],["last",replayState.ply===max]
    ])$("archive-replay-"+id).disabled=disabled;
    for(const [i,li] of [...$("archive-replay-moves").children].entries()){
      li.children[0]?.setAttribute("aria-current",
        replayState.ply===i+1?"step":"false");
    }
  }
  for(const [name,fn] of [
    ["first",()=>0],["prev",n=>n-1],
    ["next",n=>n+1],["last",()=>Infinity]]){
    $("archive-replay-"+name).addEventListener("click",()=>{
      paint(fn(replayState.ply));
    });
  }
  $("archive-replay-close").addEventListener("click",()=>
    $("archive-replay-dialog").close());
  async function enter(profile,players){
    if(!profile||!/^[a-z0-9-]{2,48}$/.test(profile.slug))return;
    ++state.version;
    state.active=true;
    state.slug=profile.slug;
    state.profile=profile;
    state.players=players||[];
    state.page=1;
    state.initial=null;
    state.report=null;
    state.loading=false;
    state.filters={q:"",origin:"all",modality:"all",result:"all",
                   color:"all",opponent:"",engine_sha256:"",date_from:"",date_to:"",
                   year_from:"",year_to:""};
    $("history-modern").hidden=false;
    $("history-original").hidden=true;
    $("archive-roster-search").value="";
    syncForm();
    drawRoster();
    profileDetails();
    setPane("overview");
    await load();
    if(!state.active||state.slug!==profile.slug)return;
    try{
      const history=await get("/api/history/players/"+profile.slug+"/games");
      if(!state.active||state.slug!==profile.slug)return;
      const list=$("archive-curated-games");list.replaceChildren();
      for(const game of (history.games||[]).slice(0,3)){
        const row=el("div",null,"archive-curated-row");
        row.append(el("strong",game.event||"Partida documentada"),
          el("span",game.date||"Data não registrada"));
        list.append(row);
      }
      if(!list.children.length)list.append(el("p",
        "Nenhum PGN histórico documentado neste acervo local."));
    }catch(_){
      $("archive-curated-note").textContent="Catálogo histórico indisponível no momento.";
    }
  }
  function leave(){
    state.version++;
    state.active=false;
    state.slug=null;
    state.report=null;
    state.initial=null;
    replayState.states=null;
    $("history-modern").hidden=true;
    $("history-original").hidden=true;
    if($("archive-replay-dialog").open)$("archive-replay-dialog").close();
  }
  function suspend(){state.version++;state.active=false;}
  function resume(){if(!state.slug)return;state.active=true;load();}
  window.ChessSimHistoryArchive=Object.freeze({
    enter,leave,suspend,resume,setPane
  });
})();
