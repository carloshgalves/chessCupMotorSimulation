"use strict";
/* #84: personal archive — never mixed into historical profiles or motor Elo.
 * All user-visible dynamic strings are textContent, with no innerHTML. */
(() => {
  const $ = id => document.getElementById(id);
  const state = {
    active:false, version:0, page:1, perPage:20, report:null,
    replay:null, ply:0, gameId:null, filters:{status:"all",outcome:"all",color:"all",opponent:""}
  };
  const node = (tag,text,cls) => {
    const value=document.createElement(tag);
    if(text!==undefined&&text!==null)value.textContent=String(text);
    if(cls)value.className=cls;
    return value;
  };
  const statusText={
    COMPLETED:"Concluída",RUNNING:"Em andamento",INTERRUPTED:"Interrompida",
    INCOMPLETE:"Incompleta",FAILED:"Falha"
  };
  const resultText={win:"Vitória",draw:"Empate",loss:"Derrota",incomplete:"Sem resultado"};
  const colorText={white:"Brancas",black:"Pretas"};
  function inform(message,bad=false){
    const target=$("my-games-alert");
    target.textContent=message;
    target.className="my-games-notice"+(bad?" error":"");
  }
  async function get(url){
    const response=await fetch(url,{cache:"no-store"});
    const data=await response.json();
    if(!response.ok)throw new Error(data.error||"Erro ao consultar as partidas pessoais");
    return data;
  }
  function date(value){
    if(!value)return "Não documentado";
    const d=new Date(value);
    return Number.isFinite(d.getTime())
      ?new Intl.DateTimeFormat("pt-BR",{dateStyle:"short",timeStyle:"short"}).format(d)
      :"Não documentado";
  }
  function query(){
    const params=new URLSearchParams({page:String(state.page),
                                     page_size:String(state.perPage)});
    for(const [key,value] of Object.entries(state.filters)){
      if(value&&value!=="all")params.set(key,value);
    }
    return params.toString();
  }
  function fillStats(report){
    const s=report.stats;
    for(const key of ["completed","wins","draws","losses","unfinished"])
      $("my-games-"+key).textContent=String(s[key]);
    $("my-games-count").textContent=String(s.games);
    const options=$("my-games-opponent");
    const selected=state.filters.opponent;
    options.replaceChildren();
    const all=node("option","Todos os motores");all.value="";
    options.append(all);
    const groups=$("my-games-opponents");groups.replaceChildren();
    for(const item of s.by_opponent){
      const option=node("option",item.name);option.value=item.slug;
      options.append(option);
      const card=node("div",null,"my-games-opponent-card");
      card.append(node("strong",item.name),
                  node("small",item.games+" partidas · "+item.wins+" V · "+
                     item.draws+" E · "+item.losses+" D"));
      groups.append(card);
    }
    options.value=selected;
    if(!groups.children.length)
      groups.append(node("p","Nenhum adversário no seu histórico."));
  }
  function fillGames(report){
    const list=$("my-games-rows");list.replaceChildren();
    for(const game of report.games){
      const tr=node("tr");
      for(const value of [
        date(game.created_at_utc),game.opponent_name,
        colorText[game.color]||game.color,statusText[game.status]||game.status
      ])tr.append(node("td",value));
      const result=node("td",resultText[game.outcome]||"Indisponível",
                        "my-games-result-"+game.outcome);
      tr.append(result,node("td",String(game.ply_count)));
      const action=node("td");
      const button=node("button","Ver partida");
      button.type="button";
      button.addEventListener("click",()=>openReplay(game.id));
      action.append(button);tr.append(action);list.append(tr);
    }
    if(!list.children.length){
      const empty=node("tr"),cell=node("td",
        "Nenhuma partida pessoal encontrada para os filtros escolhidos.");
      cell.colSpan=7;empty.append(cell);list.append(empty);
    }
    const first=(report.page-1)*report.page_size;
    $("my-games-range").textContent=report.total
      ?"Exibindo "+(first+1)+"–"+(first+report.games.length)+" de "+report.total
      :"Nenhuma partida";
    $("my-games-page").textContent="Página "+report.page+" de "+report.pages;
    $("my-games-prev").disabled=report.page<=1;
    $("my-games-next").disabled=report.page>=report.pages;
    $("my-games-coverage").textContent=report.coverage.partial
      ?"Cobertura parcial: "+report.coverage.rejected+
        " registro(s) inválido(s) excluído(s), sem atribuir resultados."
      :"Dados pessoais lidos do workspace local. Nenhum Elo é calculado.";
  }
  async function load(){
    if(!state.active)return;
    const version=++state.version;
    inform("Lendo somente as suas partidas salvas…");
    try{
      const report=await get("/api/my-games?"+query());
      if(!state.active||version!==state.version)return;
      if(report.schema!=="chesssim-my-games-v1"||report.rating_eligible!==false||
         !Array.isArray(report.games)||!report.stats)
        throw new Error("Contrato do histórico pessoal incompatível");
      state.report=report;
      fillStats(report);
      fillGames(report);
      inform("Histórico pessoal carregado. Partidas humanas não alteram o Elo.");
    }catch(error){
      if(version===state.version)inform(error.message,true);
    }
  }
  function paint(ply){
    if(!state.replay)return;
    const states=state.replay.states;
    state.ply=Math.max(0,Math.min(states.length-1,ply));
    if(!window.ChessSimBoard?.paint($("my-games-board"),states[state.ply],state.ply)){
      $("my-games-replay-meta").textContent="Posição PGN inválida";
      return;
    }
    $("my-games-ply").textContent=state.ply+"/"+(states.length-1);
    for(const [id,isDisabled] of [
      ["first",state.ply===0],["back",state.ply===0],
      ["forward",state.ply===states.length-1],["last",state.ply===states.length-1]
    ])$("my-games-"+id).disabled=isDisabled;
    [...$("my-games-moves").children].forEach((li,index)=>
      li.firstChild.setAttribute("aria-current",
        index+1===state.ply?"step":"false"));
  }
  async function openReplay(id){
    if(!state.active||!/^human-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$/.test(id))return;
    const version=state.version;
    try{
      const data=await get("/api/my-games/"+encodeURIComponent(id)+"/replay");
      if(!state.active||version!==state.version)return;
      if(data.schema!=="chesssim-my-replay-v1"||data.game?.id!==id||
         !Array.isArray(data.states)||!data.states.length)
        throw new Error("Replay pessoal incompatível");
      state.replay=data;state.gameId=id;
      $("my-games-replay-title").textContent="Replay · Você × "+data.game.opponent_name;
      $("my-games-replay-meta").textContent=
        statusText[data.game.status]+" · "+resultText[data.game.outcome]+
        " · "+colorText[data.game.color]+" · "+date(data.game.created_at_utc)+
        " · arquivo local consistente; não é prova de autoria externa.";
      const moves=$("my-games-moves");moves.replaceChildren();
      data.states.slice(1).forEach((step,index)=>{
        const li=node("li");
        const button=node("button",
          (index%2===0?Math.floor(index/2)+1+". ":"… ")+step.san);
        button.type="button";
        button.addEventListener("click",()=>paint(index+1));
        li.append(button);moves.append(li);
      });
      const download=$("my-games-download");
      download.href="/api/my-games/"+encodeURIComponent(id)+"/pgn";
      download.download=id+".pgn";
      paint(0);
      $("my-games-dialog").showModal();
    }catch(error){inform(error.message,true);}
  }
  function closeReplay(){
    const dialog=$("my-games-dialog");
    if(dialog.open)dialog.close();
    state.replay=null;state.gameId=null;
    $("my-games-download").removeAttribute("href");
  }
  function show(){
    window.ChessSimHistoryNavigation?.leave();
    window.ChessSimLeagueNavigation?.hide();
    window.ChessSimCupNavigation?.hide();
    for(const id of ["arena","history","leagues","cups"]){
      $(id==="leagues"?"league-view":id==="cups"?"cup-view":id+"-view").hidden=true;
      $("tab-"+id).setAttribute("aria-pressed","false");
    }
    $("cup-create-view").hidden=true;
    $("my-games-view").hidden=false;
    $("tab-my-games").setAttribute("aria-pressed","true");
    state.active=true;
    load();
    $("my-games-heading").focus();
  }
  function hide(){
    if(!state.active)return;
    state.active=false;state.version++;
    $("my-games-view").hidden=true;
    $("tab-my-games").setAttribute("aria-pressed","false");
    closeReplay();
  }
  window.ChessSimMyGamesNavigation=Object.freeze({show,hide});
  $("tab-my-games").addEventListener("click",show);
  $("my-games-reload").addEventListener("click",load);
  $("my-games-filters").addEventListener("submit",event=>{
    event.preventDefault();
    state.filters={
      opponent:$("my-games-opponent").value,
      status:$("my-games-status").value,
      outcome:$("my-games-outcome").value,
      color:$("my-games-color").value
    };
    state.page=1;load();
  });
  $("my-games-prev").addEventListener("click",()=>{
    if(state.page>1){state.page--;load();}
  });
  $("my-games-next").addEventListener("click",()=>{
    if(state.report&&state.page<state.report.pages){state.page++;load();}
  });
  for(const [control,position] of [
    ["first",()=>0],["back",n=>n-1],
    ["forward",n=>n+1],["last",()=>Infinity]
  ])$("my-games-"+control).addEventListener("click",()=>paint(position(state.ply)));
  $("my-games-close").addEventListener("click",closeReplay);
  $("my-games-dialog").addEventListener("close",()=>{
    state.replay=null;state.gameId=null;
    $("my-games-download").removeAttribute("href");
  });
  $("my-games-board").addEventListener("keydown",event=>{
    if(!state.replay)return;
    if(event.key==="ArrowLeft"||event.key==="ArrowRight"){
      event.preventDefault();
      paint(state.ply+(event.key==="ArrowLeft"?-1:1));
    }
  });
})();
