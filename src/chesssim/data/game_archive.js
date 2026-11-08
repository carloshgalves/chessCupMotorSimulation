"use strict";
/* Issue #70: evidence-only game archive and ChessSimulation profile dashboard. */
(() => {
  const $=id=>document.getElementById(id);
  const safeSlug=s=>typeof s==="string"&&/^[a-z0-9-]{2,48}$/.test(s);
  const state={slug:null,seq:0,active:false,pane:"overview",page:0,limit:20,
               players:[],loaded:false,snapshot:null,rows:[],overview:null,
               replay:null,ply:0,renderedRows:0};
  const mk=(tag,txt,cls)=>{
    const el=document.createElement(tag);
    if(txt!==null&&txt!==undefined)el.textContent=String(txt);
    if(cls)el.className=cls;
    return el;
  };
  const name=slug=>state.players.find(p=>p.slug===slug)?.name||slug;
  const modes={classical:"Clássico",rapid:"Rápido",blitz:"Blitz",bullet:"Bullet",
               unclassified:"Sem relógio/Armagedom"};
  const formatDate=s=>{
    if(!s)return "—";
    const d=new Date(s);
    return Number.isFinite(d.getTime())?
      new Intl.DateTimeFormat("pt-BR",{dateStyle:"short",timeStyle:"short"}).format(d):
      "—";
  };
  const pad=n=>Number(n).toLocaleString("pt-BR");
  const status=(text,error=false)=>{
    $("ha-status").textContent=text;
    $("ha-status").className=error?"ha-error":"";
  };
  async function get(url){
    const res=await fetch(url,{cache:"no-store"});
    const data=await res.json();
    if(!res.ok)throw new Error(data.error||"Arquivo indisponível");
    return data;
  }
  function showTab(tab){
    if(!["overview","games","events","rating","records"].includes(tab))return;
    state.pane=tab;
    for(const part of ["overview","games","events","rating","records"]){
      $("ha-"+part+"-pane").hidden=part!==tab;
      $("ha-tab-"+part).setAttribute("aria-pressed",String(part===tab));
    }
    if(tab==="games")loadPage();
    if(tab==="rating")loadRating();
  }
  for(const tab of ["overview","games","events","rating","records"]){
    $("ha-tab-"+tab).addEventListener("click",()=>showTab(tab));
  }
  $("ha-view-all").addEventListener("click",()=>showTab("games"));
  $("ha-back-to-overview").addEventListener("click",()=>showTab("overview"));
  function roster(){
    const list=$("ha-player-list");list.replaceChildren();
    const search=$("ha-player-search").value.trim().toLocaleLowerCase("pt-BR");
    for(const player of state.players){
      if(!player.name.toLocaleLowerCase("pt-BR").includes(search))continue;
      const btn=mk("button",null,"ha-player-choice");
      btn.type="button";
      btn.setAttribute("aria-pressed",String(player.slug===state.slug));
      btn.append(mk("strong",player.name),mk("small",player.era_label||"Período histórico"));
      btn.addEventListener("click",()=>window.ChessSimHistoryNavigation?.open(player.slug));
      list.append(btn);
    }
    if(!list.children.length)list.append(mk("p","Nenhum jogador encontrado."));
  }
  $("ha-player-search").addEventListener("input",roster);
  function query(offset=state.page){
    const fields={
      q:$("ha-query").value.trim(),competition:$("ha-competition").value.trim(),
      opponent:$("ha-opponent").value,
      modality:$("ha-mode").value,color:$("ha-color").value,
      result:$("ha-result").value,source:$("ha-source").value,
      date_from:$("ha-date-from").value,date_to:$("ha-date-to").value,
      offset:String(offset),limit:String(state.limit),
    };
    return new URLSearchParams(fields).toString();
  }
  function tag(source){
    const good=source==="engine_full";
    return mk("span",good?"● UCI verificado":"● Projeção",
      good?"ha-source-real":"ha-source-synthetic");
  }
  function result(row){
    if(row.status!=="COMPLETED")return "Incompleta";
    return row.result==="1/2-1/2"?"½–½":
      row.outcome==="win"?"Vitória":row.outcome==="loss"?"Derrota":row.result;
  }
  function rowGame(row){
    const tr=mk("tr");
    const items=[
      formatDate(row.finished_at_utc),
      row.competition+(row.round?" · R"+row.round:""),
      name(row.opponent),
      row.color==="white"?"Brancas":"Pretas",
      modes[row.modality]||row.modality,
      result(row),
    ];
    for(const val of items)tr.append(mk("td",val));
    const source=mk("td");source.append(tag(row.source_kind));tr.append(source);
    const action=mk("td");
    if(row.has_replay && row.source_kind==="engine_full"){
      const btn=mk("button","Abrir replay","ha-row-replay");
      btn.type="button";
      btn.addEventListener("click",()=>openReplay(row));
      action.append(btn);
    }else action.append(mk("span",row.source_kind==="fast_statistical"?
      "Sem PGN":"Replay indisponível","ha-muted"));
    tr.append(action);
    return tr;
  }
  function tiles(report){
    const overview=report.overview;
    $("ha-total-real").textContent=pad(overview.uci);
    $("ha-total-projected").textContent=pad(overview.projections);
    $("ha-total-incomplete").textContent=pad(overview.incomplete);
    const cards=$("ha-overview-stats");cards.replaceChildren();
    for(const [label,num] of [
      ["Partidas UCI",overview.uci],["Vitórias",overview.wins],
      ["Empates",overview.draws],["Derrotas",overview.losses],
      ["Projeções (separadas)",overview.projections],
      ["Competições",overview.competitions]
    ]){
      const tile=mk("div",null,"ha-stat");
      tile.append(mk("small",label),mk("strong",pad(num)));
      cards.append(tile);
    }
    const recent=$("ha-recent");recent.replaceChildren();
    for(const row of report.games.slice(0,5)){
      const line=mk("div",null,"ha-recent-row");
      line.append(mk("span",formatDate(row.finished_at_utc)),
        mk("span",name(row.opponent)),
        mk("span",result(row)));
      if(row.has_replay){
        const b=mk("button","▣ Abrir partida");
        b.type="button";
        b.addEventListener("click",()=>openReplay(row));
        line.append(b);
      }else line.append(mk("span","Sem PGN","ha-muted"));
      recent.append(line);
    }
    if(!report.games.length)recent.append(mk("p","Nenhuma partida do simulador encontrada."));
    const eventBox=$("ha-event-list");eventBox.replaceChildren();
    for(const item of report.facets.competitions){
      const r=mk("div",null,"ha-event-row");
      r.append(mk("strong",item.name),mk("span",pad(item.games)+" partidas"));
      eventBox.append(r);
    }
    if(!report.facets.competitions.length)eventBox.append(
      mk("p","Sem participações registradas neste workspace."));
    state.overview=overview;
  }
  function paint(report){
    if(report.schema!=="chesssim-game-archive-v1"||report.slug!==state.slug||
       !Array.isArray(report.games))throw new Error("Contrato de arquivo incompatível");
    const previously=state.snapshot;
    state.snapshot=report.snapshot;
    state.page=report.offset;
    state.rows=report.games;
    tiles(report);
    const body=$("ha-rows");body.replaceChildren();
    for(const row of report.games)body.append(rowGame(row));
    if(!report.games.length){
      const tr=mk("tr"),td=mk("td","Nenhuma partida corresponde aos filtros.");
      td.colSpan=8;tr.append(td);body.append(tr);
    }
    $("ha-page-meta").textContent=pad(report.total)+" partidas · "+
      (report.total?pad(report.offset+1)+"–"+
        pad(Math.min(report.total,report.offset+report.games.length)):"0")+
      " · "+pad(report.limit)+" por página";
    $("ha-page-index").textContent=String(Math.floor(report.offset/report.limit)+1);
    $("ha-prev").disabled=report.offset===0;
    $("ha-next").disabled=report.next_offset===null;
    $("ha-next").dataset.next=report.next_offset===null?"":String(report.next_offset);
    $("ha-export-pgn").disabled=report.overview.uci===0;
    status(report.coverage.legacy_rejections?
      "Atenção: "+report.coverage.legacy_rejections+
        " registros antigos foram rejeitados pela verificação.":"");
    if(previously&&previously!==report.snapshot&&report.offset>0){
      status("O arquivo mudou; volte à primeira página para consultar o novo estado.");
    }
  }
  async function loadPage(){
    if(!state.active||!safeSlug(state.slug))return;
    const request=++state.seq;
    if(state.pane==="games")status("Consultando partidas verificadas…");
    try{
      const report=await get("/api/history/archive/"+state.slug+"?"+query());
      if(!state.active||request!==state.seq)return;
      paint(report);
    }catch(error){if(request===state.seq)status(error.message,true);}
  }
  $("ha-filters").addEventListener("submit",event=>{
    event.preventDefault();state.page=0;loadPage();
  });
  $("ha-reset").addEventListener("click",()=>{
    $("ha-filters").reset();state.page=0;loadPage();
  });
  $("ha-prev").addEventListener("click",()=>{state.page=Math.max(0,state.page-state.limit);loadPage();});
  $("ha-next").addEventListener("click",()=>{
    const next=Number($("ha-next").dataset.next);
    if($("ha-next").dataset.next&&Number.isSafeInteger(next)){
      state.page=next;loadPage();
    }
  });
  for(const fmt of ["json","csv","pgn"]){
    $("ha-export-"+fmt).addEventListener("click",()=>{
      if(!state.slug)return;
      window.location.assign("/api/history/archive/"+state.slug+"/export/"+fmt+
        "?"+query(0));
    });
  }
  $("ha-rating-mode").addEventListener("change",loadRating);
  async function loadRating(){
    if(!state.active||state.pane!=="rating")return;
    const request=++state.seq;
    $("ha-rating-status").textContent="Consultando Elo experimental…";
    try{
      const mode=$("ha-rating-mode").value;
      const data=await get("/api/history/archive/"+state.slug+
                           "/elo?modality="+mode);
      if(!state.active||request!==state.seq||state.pane!=="rating")return;
      const target=$("ha-rating-plot");target.replaceChildren();
      if(!data.points||data.points.length<2){
        target.append(mk("p","Sem partidas UCI verificadas nesta modalidade. Nenhuma curva é presumida."));
      }else{
        const SVG="http://www.w3.org/2000/svg";
        const svg=document.createElementNS(SVG,"svg");
        svg.setAttribute("viewBox","0 0 700 210");
        svg.setAttribute("role","img");
        svg.setAttribute("aria-label","Elo experimental por partida comprovada");
        const values=data.points.map(x=>Number(x.rating));
        const low=Math.min(...values)-10,high=Math.max(...values)+10;
        const points=values.map((y,i)=>(24+(650*i/(values.length-1))).toFixed(1)+","+
          (180-(150*(y-low)/(high-low))).toFixed(1)).join(" ");
        const line=document.createElementNS(SVG,"polyline");
        line.setAttribute("points",points);line.setAttribute("fill","none");
        line.setAttribute("stroke","#51d9a2");line.setAttribute("stroke-width","3");
        svg.appendChild(line);target.append(svg);
      }
      $("ha-rating-status").textContent=data.points.length-1+
        " partidas UCI pontuadas neste pool; rating experimental, não FIDE.";
    }catch(err){if(request===state.seq)$("ha-rating-status").textContent=err.message;}
  }
  function replayPly(index){
    const data=state.replay;if(!data)return;
    state.ply=Math.max(0,Math.min(index,data.states.length-1));
    const frame=data.states[state.ply];
    window.ChessSimBoard.paint($("ha-replay-board"),frame,state.ply);
    $("ha-replay-progress").textContent=state.ply+"/"+(data.states.length-1);
    $("ha-replay-first").disabled=$("ha-replay-prev").disabled=state.ply===0;
    $("ha-replay-last").disabled=$("ha-replay-next").disabled=
      state.ply===data.states.length-1;
  }
  async function openReplay(row){
    if(!state.slug||!row.has_replay||row.source_kind!=="engine_full")return;
    try{
      const data=await get("/api/history/archive/"+state.slug+"/replay/"+row.id);
      if(!Array.isArray(data.states)||!data.states.length)throw new Error("PGN sem posições");
      state.replay=data;
      $("ha-replay-title").textContent=name(row.white)+" × "+name(row.black);
      $("ha-replay-meta").textContent=row.competition+" · "+(modes[row.modality]||row.modality)+
        " · "+(row.result||"Incompleta");
      const list=$("ha-replay-moves");list.replaceChildren();
      data.states.slice(1).forEach((item,index)=>{
        const li=mk("li");
        const btn=mk("button",(Math.floor(index/2)+1)+(index%2?".… ":". ")+
          (item.san||data.moves?.[index]?.san||"Lance "+(index+1)));
        btn.type="button";btn.addEventListener("click",()=>replayPly(index+1));
        li.append(btn);list.append(li);
      });
      replayPly(0);
      $("ha-replay-dialog").showModal();
    }catch(error){status("Replay indisponível: "+error.message,true);}
  }
  $("ha-replay-close").addEventListener("click",()=>$("ha-replay-dialog").close());
  for(const [id,next] of [
    ["ha-replay-first",()=>0],["ha-replay-prev",v=>v-1],
    ["ha-replay-next",v=>v+1],["ha-replay-last",()=>Infinity]]){
    $(id).addEventListener("click",()=>replayPly(next(state.ply)));
  }
  function leave(){
    state.seq++;
    state.active=false;state.slug=null;
    state.replay=null;state.overview=null;state.snapshot=null;
    if($("ha-replay-dialog").open)$("ha-replay-dialog").close();
  }
  function suspend(){state.seq++;state.active=false;}
  function resume(){if(state.slug&&!state.active){state.active=true;loadPage();}}
  async function enter(slug){
    leave();
    if(!safeSlug(slug))return;
    state.active=true;state.slug=slug;state.page=0;
    showTab("overview");
    if(!state.loaded){
      try{
        const catalog=await get("/api/history/players");
        if(!state.active)return;
        state.players=catalog.players;
        state.loaded=true;
      }catch(error){status(error.message,true);return;}
    }
    const person=state.players.find(p=>p.slug===slug);
    $("ha-identity-name").textContent=person?.name||slug;
    $("ha-identity-era").textContent=person?.era_label||"Período histórico não revisado";
    roster();
    const opponent=$("ha-opponent");opponent.replaceChildren();
    const all=mk("option","Todos");all.value="";opponent.append(all);
    for(const p of state.players){
      if(p.slug===slug)continue;
      const option=mk("option",p.name);option.value=p.slug;opponent.append(option);
    }
    $("ha-filters").reset();
    state.page=0;
    await loadPage();
  }
  window.ChessSimArchive=Object.freeze({enter,leave,suspend,resume});
})();
