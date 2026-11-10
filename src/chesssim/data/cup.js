"use strict";
/* #58 Copas — approved UI. All results and PGNs come from verified server state.
 * No HTML injection, arbitrary URLs, invented moves or fabricated historical Elo. */
(() => {
  const $=id=>document.getElementById(id);
  const token=new URLSearchParams(location.hash.slice(1)).get("token");
  const state={
    cups:[],detail:null,presets:null,players:[],id:null,
    selectedMatch:null,selectedWomanGameId:null,selectedRound:1,pane:"bracket",createPreset:"standard",
    selection:[],view:"none",busy:false,loading:false,
    commandPending:false,version:0,playback:null,
    seedFromPreset:[],lastRun:null,
    listSignature:null,detailSignature:null,renderedId:null,
    focusedPlayer:null,profileCache:new Map(),profilePending:new Set(),
  };
  const labels={
    standard:"Standard",world_champions:"Campeões Mundiais",
    women:"Feminino",custom:"Personalizada",
    READY:"Não iniciada",RUNNING:"Em andamento",PAUSED:"Pausada",
    FAILED:"Revisar",COMPLETED:"Concluída",
  };
  const stageLabel={
    classical:"Clássico · 2 partidas",
    rapid15:"Rápido · 15+10",
    rapid10:"Rápido · 10+10",
    blitz5:"Blitz · 5+3",
    blitz3:"Blitz · 3+2",
    armageddon:"Armagedom · lance de tempo",
    women_double_round_robin:"Mini-liga · 15+10",
  };
  const stages=[
    ["classical","2 partidas clássicas · 90+30 após 40 lances"],
    ["rapid15","Rápido 15+10 · 2 partidas"],
    ["rapid10","Rápido 10+10 · 2 partidas"],
    ["blitz5","Blitz 5+3 · 2 partidas"],
    ["blitz3","Blitz 3+2 · 2 partidas"],
    ["armageddon","Armagedom · brancas 4+2, menor lance de pretas"],
  ];
  const node=(tag,className,text)=>{
    const element=document.createElement(tag);
    if(className)element.className=className;
    if(text!==undefined)element.textContent=String(text);
    return element;
  };
  const replace=(id,...items)=>$(id).replaceChildren(...items);
  const num=x=>Number(x);
  function note(text,error=false){
    const target=state.view==="create"?$("cup-create-feedback"):$("cup-feedback");
    target.textContent=text;
    target.className="cup-feedback"+(error?" error":"");
  }
  function formatDate(value){
    if(!value)return "Aguardando";
    const date=new Date(value);
    return Number.isFinite(date.getTime())
      ?new Intl.DateTimeFormat("pt-BR",{dateStyle:"short",timeStyle:"short"}).format(date)
      :"Não disponível";
  }
  function player(slug){
    return state.players.find(p=>p.slug===slug);
  }
  function name(slug){
    return slug?(player(slug)?.name||slug):"A definir";
  }
  async function get(path){
    const response=await fetch(path,{cache:"no-store"});
    const data=await response.json();
    if(!response.ok)throw new Error(data.error||"Falha na consulta");
    return data;
  }
  async function post(path,body={}){
    if(!token||token.length<32)
      throw new Error("Use o endereço local completo informado no terminal.");
    const response=await fetch(path,{
      method:"POST",cache:"no-store",
      headers:{"Content-Type":"application/json","X-Chesssim-Token":token},
      body:JSON.stringify(body),
    });
    const data=await response.json();
    if(!response.ok)throw new Error(data.error||"Ação recusada");
    return data;
  }
  function nav(selected){
    $("arena-view").hidden=true;
    $("history-view").hidden=true;
    $("league-view").hidden=true;
    $("cup-view").hidden=selected!=="main";
    $("cup-create-view").hidden=selected!=="create";
    state.view=selected;
    for(const section of ["arena","history","leagues","cups"]){
      $("tab-"+section).setAttribute("aria-pressed",
                                   String(section==="cups"));
    }
  }
  function show(){
    window.ChessSimMyGamesNavigation?.hide();
    window.ChessSimHistoryNavigation?.leave();
    window.ChessSimLeagueNavigation?.hide();
    nav("main");
    load(true);
    $("cup-heading").focus();
  }
  function hide(){
    $("cup-view").hidden=true;
    $("cup-create-view").hidden=true;
    state.view="none";
    state.version++;
    $("tab-cups").setAttribute("aria-pressed","false");
  }
  window.ChessSimCupNavigation=Object.freeze({show,hide});
  $("tab-cups").addEventListener("click",show);
  $("cup-back").addEventListener("click",()=>{nav("main");load(true);});
  $("cup-cancel").addEventListener("click",()=>{nav("main");load(true);});
  async function bootstrap(){
    if(state.presets)return;
    const [presets,arena]=await Promise.all([
      get("/api/cups/presets"),get("/api/arena"),
    ]);
    if(!Array.isArray(arena.players)||arena.players.length!==64){
      throw new Error("Catálogo de 64 enxadristas indisponível");
    }
    state.presets=presets;
    state.players=arena.players;
    state.busy=Boolean(arena.busy);
    setPreset(state.createPreset,true);
  }
  async function load(force=false){
    if(!["main","create"].includes(state.view)||state.loading||
       state.commandPending)return;
    state.loading=true;
    const version=++state.version;
    try{
      await bootstrap();
      const [listing,arena]=await Promise.all([
        get("/api/cups"),get("/api/arena")
      ]);
      if(version!==state.version||state.view==="none")return;
      const oldBusy=state.busy;
      state.cups=listing.cups;
      state.busy=Boolean(arena.busy);
      if(!state.id||!state.cups.some(x=>x.id===state.id)){
        state.id=state.cups[0]?.id||null;
        state.selectedMatch=null;state.selectedWomanGameId=null;
        state.selectedRound=1;state.pane="bracket";
      }
      if(state.view==="create")return;
      const listingSignature=JSON.stringify([state.id,state.cups]);
      if(force||listingSignature!==state.listSignature){
        drawList();
        state.listSignature=listingSignature;
      }
      if(!state.id){
        state.detail=null;
        state.detailSignature=null;
        state.renderedId=null;
        empty();return;
      }
      const detail=await get("/api/cups/"+state.id);
      if(version!==state.version||state.view!=="main")return;
      const detailSignature=JSON.stringify(detail);
      const changed=force||detailSignature!==state.detailSignature||
        state.renderedId!==state.id||oldBusy!==state.busy;
      state.detail=detail;
      if(state.lastRun?.id===detail.id&&detail.status!=="RUNNING"){
        if(detail.completed_games>state.lastRun.games){
          note("Partida concluída e resultado registrado. "+
               "Confira o placar; replay disponível apenas para jogos UCI.");
        }else if(detail.status==="FAILED"){
          note("Falha na execução. Nenhum resultado inconclusivo foi pontuado.",true);
        }else{
          note("Execução interrompida sem novo resultado contabilizado.");
        }
        state.lastRun=null;
      }
      if(detail.format==="double_round_robin"&&!state.selectedWomanGameId&&
         detail.recent?.length){
        state.selectedWomanGameId=detail.recent.at(-1).id;
      }
      const available=new Set(
        (detail.bracket||[]).flatMap(r=>r.matches.map(m=>m.id))
      );
      if(state.selectedMatch&&!available.has(state.selectedMatch))
        state.selectedMatch=null;
      if(!state.selectedMatch){
        state.selectedMatch=detail.next_game?.match_id||
          detail.bracket?.[0]?.matches?.find(m=>m.status==="PENDING")?.id||null;
      }
      if(changed){
        render();
        state.detailSignature=detailSignature;
        state.renderedId=state.id;
      }
    }catch(error){note(error.message,true);}
    finally{state.loading=false;}
  }
  function empty(){
    $("cup-no-selection").hidden=false;
    $("cup-selection").hidden=true;
    $("cup-progress-bar").style.width="0%";
    $("cup-progress-matches").textContent="0/0";
    $("cup-progress-games").textContent="0";
    $("cup-progress-running").textContent="0";
    $("cup-progress-description").textContent="Aguardando Copa";
    $("cup-run").disabled=true;$("cup-pause").disabled=true;
    $("cup-open-match").disabled=true;
    $("cup-export-pgn").disabled=true;
    $("cup-export-json").disabled=true;
    replace("cup-selected-players",node("span",null,"Selecione um confronto na chave"));
  }
  function drawList(){
    const list=$("cup-list");list.replaceChildren();
    $("cup-empty-list").hidden=state.cups.length>0;
    for(const item of state.cups){
      const button=node("button","cup-item");
      button.type="button";
      button.setAttribute("aria-pressed",String(item.id===state.id));
      const icon=node("span","cup-item-icon",item.champion?"♛":"🏆");
      const desc=node("span");
      desc.append(
        node("strong",null,item.name),
        node("small",null,item.edition+" · "+labels[item.preset]+
             " · "+item.participants_count+" jogadores"),
        node("em",null,labels[item.status]||item.status),
      );
      button.append(icon,desc,node("span",null,"›"));
      button.addEventListener("click",()=>{
        state.id=item.id;state.selectedMatch=null;state.selectedWomanGameId=null;
        state.selectedRound=1;state.pane="bracket";
        load(true);
      });
      list.appendChild(button);
    }
  }
  function pill(text){return node("span",null,text);}
  function selectedMatch(){
    return state.detail?.bracket?.flatMap(r=>r.matches)
      .find(m=>m.id===state.selectedMatch)||null;
  }
  function matchScores(match){
    const players=match.players||[];
    const scores=match.score||{};
    return players.map(slug=>slug?Number(scores[slug]||0).toLocaleString("pt-BR",
      {maximumFractionDigits:1}):"—");
  }
  function render(){
    const data=state.detail;if(!data){empty();return;}
    $("cup-no-selection").hidden=true;$("cup-selection").hidden=false;
    $("cup-title").textContent="🏆 "+data.name;
    replace("cup-tags",
      pill(data.edition+" · Simulação local"),
      pill(data.participants_count+" participantes"),
      pill(data.format==="double_round_robin"?"Duplo turno":"Eliminatória com byes"),
      pill(data.execution_mode==="engine_full"?"Motor Rodent IV":"Projeção sintética"),
    );
    $("cup-status-pill").textContent=labels[data.status]||data.status;
    const percentage=data.total_matches
      ?100*data.completed_matches/data.total_matches:0;
    $("cup-progress-bar").style.width=percentage.toFixed(2)+"%";
    $("cup-progress-description").textContent=data.champion
      ?"Campeão: "+name(data.champion)
      :data.format==="double_round_robin"
       ?"Mini-liga · "+data.completed_games+"/20 jogos"
       :"Confrontos decididos: "+data.completed_matches+"/"+data.total_matches+
        " ("+percentage.toFixed(1).replace(".",",")+"%)";
    $("cup-progress-matches").textContent=
      data.completed_matches+"/"+data.total_matches;
    $("cup-progress-games").textContent=String(data.completed_games);
    $("cup-progress-running").textContent=String(data.status==="RUNNING"?1:0);
    $("cup-export-json").disabled=state.commandPending;
    $("cup-export-pgn").disabled=state.commandPending||data.available_pgn===0;
    const missingProfiles=data.execution_mode==="engine_full"?
      data.config.participants.filter(slug=>!player(slug)?.installed):[];
    const mayRun=Boolean(data.next_game)&&!state.commandPending&&
      data.status!=="RUNNING"&&!state.busy&&Boolean(token)&&
      missingProfiles.length===0;
    $("cup-run").disabled=!mayRun;
    $("cup-run-warning").textContent=missingProfiles.length?
      missingProfiles.length+" perfil(is) Rodent IV ausente(s). "+
      "Instale e registre os motores antes de executar partidas reais.":"";
    $("cup-run").textContent=data.next_game?.stage==="armageddon"
      ?"▶ Executar Armagedom"
      :data.next_game?.stage==="women_double_round_robin"
       ?"▶ Executar próxima partida"
       :"▶ Executar "+(data.next_game?.stage&&data.next_game.stage!=="classical"
                        ?"desempate":"próxima partida");
    $("cup-pause").disabled=state.commandPending||data.status!=="RUNNING"||!token;
    const selectedWomen=data.format==="double_round_robin"&&
      data.rounds.flatMap(round=>round.games).find(g=>g.id===state.selectedWomanGameId);
    const selectedHasGame=Boolean(
      data.format==="double_round_robin"
       ?selectedWomen?.result
       :selectedMatch()?.games?.length);
    $("cup-open-match").disabled=!selectedHasGame;
    $("cup-open-match").textContent=data.execution_mode==="fast_statistical"
      ?"▦ Ver placares (sem tabuleiro)"
      :"▣ Abrir confronto / replay";
    drawSelected();
    drawStages();
    setPane(state.pane,true);
  }
  function drawSelected(){
    const data=state.detail;if(!data)return;
    const match=selectedMatch();
    const next=data.next_game;
    if(data.format==="double_round_robin"){
      const chosen=data.rounds.flatMap(round=>round.games).find(
        g=>g.id===state.selectedWomanGameId);
      const game=chosen||next;
      $("cup-selected-round").textContent="Torneio feminino · "+
        (game?"Rodada "+game.round:"Temporada concluída");
      replace("cup-selected-players",node("span",null,
        game?name(game.white)+" × "+name(game.black):
        "Campeã: "+name(data.champion)));
      $("cup-selected-score").textContent=chosen?
        (chosen.result||"Pendente")+" · 15+10":
        next?"Próxima partida · 15+10":"Classificação final concluída";
      $("cup-next-stage").textContent=
        data.execution_mode==="fast_statistical"?
        "Projeção auditável, sem lances UCI":"Motor real · uma partida por vez";
      $("cup-bid-proof").hidden=true;
      if(!state.focusedPlayer||!data.config.participants.includes(state.focusedPlayer))
        state.focusedPlayer=game?.white||data.champion||
          data.config.participants[0];
      renderPerformance();
      return;
    }
    if(!match){
      $("cup-bid-proof").hidden=true;
      $("cup-selected-round").textContent="Nenhum confronto disponível";
      replace("cup-selected-players",node("span",null,"Aguardando chaveamento"));
      $("cup-selected-score").textContent="—";
      $("cup-next-stage").textContent="Selecione uma partida";
      return;
    }
    $("cup-selected-round").textContent="Rodada "+match.round+" · "+match.id;
    const cells=[];
    const matchedPlayers=match.players.filter(Boolean);
    if(!matchedPlayers.includes(state.focusedPlayer)){
      state.focusedPlayer=matchedPlayers[0]||null;
    }
    for(const slug of match.players){
      const line=node("button","cup-selected-player");
      line.type="button";
      line.disabled=!slug;
      line.setAttribute("aria-pressed",String(state.focusedPlayer===slug));
      const desc=node("div");
      desc.append(node("strong",null,name(slug)),
        node("small",null,slug?
          (data.config.preset==="world_champions"?"Elegível por título":"Perfil do catálogo"):"Posição pendente"));
      line.append(desc);
      if(slug){
        line.addEventListener("click",()=>{
          state.focusedPlayer=slug;
          drawSelected();
        });
      }
      cells.push(line);
    }
    replace("cup-selected-players",...cells);
    const scores=matchScores(match);
    $("cup-selected-score").textContent=
      name(match.players[0])+" "+scores[0]+" — "+scores[1]+" "+name(match.players[1]);
    $("cup-next-stage").textContent=match.winner
      ?"Classificado: "+name(match.winner)
      :match.status==="BYE"?"Passagem automática por bye — sem PGN"
      :match.next_game
       ?"Próxima etapa: "+(stageLabel[match.next_game.stage]||match.next_game.stage)
       :"Confronto ainda não definido";
    const arm=match.games?.find(g=>g.stage==="armageddon"&&g.bid_proof);
    $("cup-bid-proof").hidden=!arm;
    if(arm){
      const proof=arm.bid_proof;
      const offers=Object.entries(proof.offers).map(([slug,bid])=>
        name(slug)+": "+bid.seconds+" s").join(" · ");
      $("cup-bid-proof").textContent="Ofertas sintéticas registradas: "+offers+
        ". Pretas: "+name(proof.black)+" ("+proof.winning_black_seconds+
        " s)."+(proof.tie?" Empate nos lances: sorteio registrado.":"");
    }
    renderPerformance();
  }
  function recordFor(slug){
    const data=state.detail;
    if(!data||!slug)return {games:0,wins:0,draws:0,losses:0};
    const all=data.format==="double_round_robin"?
      data.rounds.flatMap(r=>r.games):
      data.bracket.flatMap(r=>r.matches.flatMap(m=>m.games));
    const record={games:0,wins:0,draws:0,losses:0};
    for(const game of all){
      if(!game.result||![game.white,game.black].includes(slug))continue;
      record.games++;
      if(game.result==="1/2-1/2")record.draws++;
      else if((game.result==="1-0"&&game.white===slug)||
              (game.result==="0-1"&&game.black===slug))record.wins++;
      else record.losses++;
    }
    return record;
  }
  function renderPerformance(){
    const data=state.detail;
    const slug=data?.format==="double_round_robin"?
      state.focusedPlayer||data.next_game?.white||data.champion:
      state.focusedPlayer;
    $("cup-performance-name").textContent=slug?name(slug):
      "Selecione um enxadrista";
    $("cup-performance-link").disabled=!slug;
    $("cup-performance-link").dataset.player=slug||"";
    const record=recordFor(slug);
    $("cup-performance-record").textContent=
      (data?.execution_mode==="fast_statistical"
        ?"Nesta Copa (projeção de placar, não registrada na História): "
        :"Na Copa (partidas UCI): ")+record.games+" partidas · "+record.wins+
      " vitórias · "+record.draws+" empates · "+record.losses+" derrotas.";
    const pack=slug?state.profileCache.get(slug):null;
    $("cup-performance-era").textContent=pack?.era_label||
      "Perfil histórico do catálogo";
    $("cup-performance-biography").textContent=pack?.short_bio||
      "Dados históricos revisados disponíveis na História. "+
      "Esses dados não medem fidelidade da simulação.";
    if(slug&&!pack&&!state.profilePending.has(slug)){
      state.profilePending.add(slug);
      get("/api/history/players/"+slug).then(profile=>{
        state.profileCache.set(slug,profile);
        if(state.view==="main"&&state.focusedPlayer===slug){
          renderPerformance();
        }
      }).catch(()=>{}).finally(()=>state.profilePending.delete(slug));
    }
  }
  function drawStages(){
    const data=state.detail;if(!data)return;
    const list=$("cup-tiebreak-list");list.replaceChildren();
    if(data.format==="double_round_robin"){
      list.append(node("li",null,"5 participantes · todos contra todos em turno e returno"),
                  node("li",null,"10 rodadas · 2 jogos e 1 folga por rodada"),
                  node("li",null,"20 partidas possíveis · classificação 3/1/0"));
      return;
    }
    const match=selectedMatch();
    const stage=match?.next_game?.stage||
      match?.stage||data.next_game?.stage;
    const stageIndex=stages.findIndex(x=>x[0]===stage);
    for(let i=0;i<stages.length;i++){
      const entry=node("li",
        i===stageIndex?"active":
        i<stageIndex?"done":"",stages[i][1]);
      list.appendChild(entry);
    }
  }
  function setPane(pane,renderNow=false){
    if(!["bracket","matches","calendar","rules"].includes(pane))return;
    state.pane=pane;
    for(const name of ["bracket","matches","calendar","rules"]){
      $("cup-tab-"+name).setAttribute("aria-pressed",String(name===pane));
      $("cup-tab-"+name).classList.toggle("active",name===pane);
      $("cup-pane-"+name).hidden=name!==pane;
    }
    $("cup-women-standings").hidden=true;
    if(!state.detail)return;
    if(pane==="bracket"){
      if(state.detail.format==="double_round_robin"){
        $("cup-pane-bracket").hidden=true;
        $("cup-women-standings").hidden=false;
        womenTable();
      }else bracket();
    }else if(pane==="matches")matches();
    else if(pane==="calendar")calendar();
    else if(pane==="rules")rules();
  }
  for(const pane of ["bracket","matches","calendar","rules"])
    $("cup-tab-"+pane).addEventListener("click",()=>setPane(pane));
  function selectMatch(id){
    state.selectedMatch=id;
    state.focusedPlayer=null;
    drawSelected();drawStages();
    if(state.pane==="bracket")bracket();
    if(state.pane==="matches")matches();
  }
  function bracket(){
    const data=state.detail;if(!data?.bracket)return;
    const box=$("cup-bracket");box.replaceChildren();
    $("cup-stage-heading").textContent="Quadro eliminatório";
    $("cup-stage-caption").textContent=
      data.participants_count+" participantes → 1 campeão";
    const rounds=data.bracket;
    const names={1:"Primeira rodada"};
    for(const round of rounds){
      const col=node("div","cup-bracket-col");
      const count=round.matches.length;
      const header=node("div","cup-bracket-round-heading");
      header.append(node("span","cup-bracket-round-number",String(round.round)),
        node("strong",null,count===1?"Final":count===2?"Semifinais":
          count===4?"Quartas de final":names[round.round]||"Rodada "+round.round),
        node("small",null,count+" "+(count===1?"confronto":"confrontos")));
      col.appendChild(header);
      const group=node("div","cup-bracket-games");
      for(const match of round.matches){
        const card=node("button","cup-bracket-game");
        card.type="button";
        card.setAttribute("aria-pressed",String(match.id===state.selectedMatch));
        card.appendChild(node("span","cup-bracket-match-id",match.id));
        const scores=matchScores(match);
        for(let i=0;i<2;i++){
          const line=node("span","cup-bracket-line");
          const slug=match.players[i];
          line.append(node("span",null,slug?"♟":"◌"),
                      node("span",null,slug?name(slug):
                        match.status==="WAITING"?"Aguardando vencedor":"Bye"),
                      node("strong",null,match.winner===slug&&slug?"✓":
                        match.games.length?scores[i]:"—"));
          card.appendChild(line);
        }
        card.addEventListener("click",()=>selectMatch(match.id));
        group.appendChild(card);
      }
      if(round.matches.length===1){
        const champ=node("div","cup-champion-card");
        champ.append(node("span",null,"♛"),
                     node("strong",null,data.champion?name(data.champion):
                          "Campeão da Copa"),
                     node("small",null,data.champion?"Copa concluída":"Aguardando final"));
        group.appendChild(champ);
      }
      col.appendChild(group);box.appendChild(col);
    }
    $("cup-bracket-hint").textContent=data.source_warning+
      " · Byes não representam partidas realizadas.";
  }
  function matchListEntry(match){
    const button=node("button");
    button.type="button";
    button.setAttribute("aria-pressed",String(match.id===state.selectedMatch));
    const desc=node("span",null,
      name(match.players[0])+" × "+name(match.players[1]));
    button.append(node("small",null,match.id+" · R"+match.round),desc,
                  node("strong",null,match.winner?
                    "✓ "+name(match.winner):match.status));
    button.addEventListener("click",()=>selectMatch(match.id));
    return button;
  }
  function matches(){
    const data=state.detail;const box=$("cup-matches");box.replaceChildren();
    if(data?.format==="double_round_robin"){
      for(const round of data.rounds){
        for(const game of round.games){
          const card=node("div","cup-calendar-row");
          card.append(node("small",null,"Rodada "+round.round),
                      node("span",null,name(game.white)+" × "+name(game.black)),
                      node("strong",null,game.result||"Pendente"));
          box.appendChild(card);
        }
      }
      return;
    }
    for(const round of data.bracket){
      for(const match of round.matches){
        box.appendChild(matchListEntry(match));
        if(match.id===state.selectedMatch&&match.games.length){
          for(const entry of match.games){
            const item=node("div","cup-calendar-row");
            item.append(node("small",null,entry.stage),
                        node("span",null,name(entry.white)+" × "+name(entry.black)),
                        node("strong",null,entry.result));
            box.appendChild(item);
          }
        }
      }
    }
  }
  function calendar(){
    const data=state.detail;
    const box=$("cup-calendar");box.replaceChildren();
    if(!data)return;
    const female=data.format==="double_round_robin";
    const rounds=female?data.rounds:data.bracket;
    for(const round of rounds){
      const wrap=node("section","cup-calendar-round");
      const expanded=state.selectedRound===round.round;
      const header=node("button","cup-calendar-toggle");
      header.type="button";
      header.setAttribute("aria-expanded",String(expanded));
      const entries=female?round.games:round.matches;
      const completed=female?
        entries.filter(g=>g.status==="COMPLETED").length:
        entries.filter(m=>["COMPLETED","BYE"].includes(m.status)).length;
      header.append(node("strong",null,"Rodada "+round.round),
        node("span",null,entries.length+
          (entries.length===1?" confronto":" confrontos")+
          (female?" · Folga: "+name(round.bye):"")),
        node("small",null,completed+"/"+entries.length+" concluídos"),
        node("span","cup-calendar-chevron",expanded?"▾":"▸"));
      header.addEventListener("click",()=>{
        state.selectedRound=expanded?null:round.round;
        calendar();
      });
      wrap.appendChild(header);
      if(expanded){
        const matches=node("div","cup-calendar-entries");
        for(const entry of entries){
          const chosen=female?state.selectedWomanGameId===entry.id:
            state.selectedMatch===entry.id;
          const row=node("button","cup-calendar-game");
          row.type="button";
          row.setAttribute("aria-pressed",String(chosen));
          const a=female?entry.white:entry.players[0];
          const b=female?entry.black:entry.players[1];
          const result=female?entry.result:
            entry.status==="BYE"?"BYE":
            entry.winner?"Classificado: "+name(entry.winner):
            entry.games.length+" partidas · "+entry.status;
          row.append(node("span","cup-calendar-game-name",
            name(a)+" × "+name(b)),
            node("span","cup-calendar-game-result",result||"Aguardando"));
          row.addEventListener("click",()=>{
            if(female){
              state.selectedWomanGameId=entry.id;
              state.focusedPlayer=entry.white;
              drawSelected();
              render();
            }else{
              selectMatch(entry.id);
              calendar();
            }
          });
          matches.appendChild(row);
          if(female&&entry.pgn){
            const replay=node("button","cup-calendar-replay","♟ Abrir replay verificado");
            replay.type="button";
            replay.addEventListener("click",()=>openReplay(entry.id));
            matches.appendChild(replay);
          }
          if(!female&&entry.games?.length){
            for(const game of entry.games){
              const detail=node("button","cup-calendar-replay",
                (stageLabel[game.stage]||game.stage)+": "+name(game.white)+
                " × "+name(game.black)+" · "+game.result+
                (game.pgn?" · Ver lances":" · Projeção, sem PGN"));
              detail.type="button";
              detail.addEventListener("click",()=>{
                selectMatch(entry.id);
                if(game.pgn)openReplay(game.id);
                else note("Projeção estatística: o placar é auditável, mas não existem lances nem tabuleiro de replay.");
              });
              matches.appendChild(detail);
            }
          }
        }
        wrap.appendChild(matches);
      }
      box.appendChild(wrap);
    }
  }
  function womenTable(){
    const body=$("cup-women-table");body.replaceChildren();
    for(const item of state.detail.standings){
      const tr=node("tr");
      for(const value of [item.rank,item.name,item.played,item.wins,item.draws,
                           item.losses,item.points])
        tr.appendChild(node("td",null,value));
      body.appendChild(tr);
    }
  }
  function rules(){
    const cfg=state.detail.config;
    const box=$("cup-rules-content");box.replaceChildren();
    const lines=[
      ["Fonte primária","Regulamento da Copa do Mundo FIDE 2025, §§ 4.7.1–4.7.5."],
      ["Semeadura","Sorteio determinístico registrado; byes explícitos, sem resultados inventados."],
      ["Títulos","Campeões mundiais clássicos e título FIDE da cisão, com taxonomia distinta."],
      ["Motor",cfg.execution_mode==="engine_full"?
        "Somente perfis UCI instalados e PGNs reais. Execução serial.":
        "Projeção estatística determinística. Sem jogo UCI, PGN ou alteração de Elo."],
      ["Armagedom",cfg.armageddon.white_seconds+" s para brancas, incremento "+
        cfg.armageddon.increment_seconds+" s. Pretas ofertam entre "+
        cfg.armageddon.min_black_seconds+" e "+
        cfg.armageddon.max_black_seconds+" s."],
      ["Ofertas","Política sintética determinística com compromisso SHA-256 e revelação; o motor não dá lances secretos UCI."],
      ["Desempate","Após séries clássica, rápida e blitz empatadas, menor tempo de pretas vence lance; empate no tabuleiro dá vitória às pretas."],
    ];
    for(const [title,desc] of lines){
      box.append(node("h4",null,title),node("p",null,desc));
    }
    const a=node("a",null,"Consultar regulamento primário da FIDE");
    a.href="https://calendar.fide.com/event_files/2644/WorldCup2025Regulations.pdf";
    a.target="_blank";a.rel="noopener noreferrer";
    box.appendChild(a);
  }
  function choosePresetCount(available,requested){
    const allow=available.length;
    return Math.max(2,Math.min(Math.max(2,requested),Math.max(2,allow)));
  }
  function setPreset(preset,initial=false){
    if(!["standard","world_champions","women","custom"].includes(preset))return;
    state.createPreset=preset;
    for(const button of $("cup-creation-presets").querySelectorAll("button")){
      button.classList.toggle("active",button.dataset.cupPreset===preset);
      button.setAttribute("aria-pressed",String(button.dataset.cupPreset===preset));
    }
    if(!state.presets||!state.players.length)return;
    let eligible=state.presets[preset].participants.slice();
    const countrySelect=$("cup-country-filter");
    $("cup-country-control").hidden=preset!=="custom";
    if(preset!=="custom")countrySelect.value="";
    if(preset==="custom"){
      const country=state.presets.country_filters?.[countrySelect.value];
      eligible=country?.participants.slice()||
        state.players.map(p=>p.slug);
    }
    if(preset==="standard")eligible=state.players.map(p=>p.slug);
    const count=$("cup-count");
    const oldCount=num(count.value)||8;
    const requested=preset==="women"?5:preset==="world_champions"?
      eligible.length:oldCount;
    count.replaceChildren();
    const sizes=preset==="women"?[5]:preset==="world_champions"?
      Array.from({length:eligible.length-1},(_,i)=>i+2):
      Array.from({length:63},(_,i)=>i+2);
    for(const n of sizes){
      const opt=node("option",null,n+" participantes");
      opt.value=String(n);count.appendChild(opt);
    }
    count.value=String(choosePresetCount(eligible,requested));
    count.disabled=preset==="women";
    $("cup-format").value=preset==="women"?"Mini-liga dupla · 10 rodadas":
      "Mata-mata com byes explícitos";
    $("cup-preset-description").textContent=preset==="world_champions"
      ?"Títulos mundiais documentados: clássicos e divisão FIDE 1993–2006."
      :preset==="women"?"Cinco perfis de mulheres do catálogo, todos contra todos, turno e returno."
      :preset==="standard"?"Eliminatória principal do elenco, com jogos clássicos e desempates."
      :"Seleção livre de 2 a 64 jogadores do catálogo.";
    if(preset==="women"&&!initial){
      $("cup-execution").value="fast_statistical";
    }
    const installed=eligible.filter(slug=>player(slug)?.installed);
    const standard=[...installed,...eligible.filter(slug=>!installed.includes(slug))];
    state.seedFromPreset=preset==="world_champions"||preset==="women"?
      eligible.slice():standard;
    fillParticipants(true);
    updateSummary();
  }
  function fillParticipants(reset=false){
    if(!state.players.length)return;
    const eligible=state.seedFromPreset;
    const n=num($("cup-count").value)||8;
    const selected=new Set(reset?eligible.slice(0,n):state.selection);
    if(selected.size<n){
      for(const slug of eligible)if(selected.size<n)selected.add(slug);
    }
    state.selection=[...selected].filter(s=>eligible.includes(s)).slice(0,n);
    const box=$("cup-participants");box.replaceChildren();
    for(const slug of eligible){
      const label=node("label");
      const input=node("input");
      input.type="checkbox";input.value=slug;
      input.checked=state.selection.includes(slug);
      input.disabled=state.createPreset==="women";
      input.addEventListener("change",()=>{
        let selected=new Set(state.selection);
        if(input.checked){
          if(selected.size>=n){
            input.checked=false;
            note("Desmarque alguém antes de substituir o participante.",true);
            return;
          }
          selected.add(slug);
        }else selected.delete(slug);
        state.selection=[...selected];
        updateSummary();
      });
      const text=node("span",null,name(slug));
      label.append(input,text);
      box.appendChild(label);
    }
    updateSummary();
  }
  $("cup-count").addEventListener("change",()=>fillParticipants(true));
  $("cup-country-filter").addEventListener("change",()=>setPreset("custom"));
  function updateSummary(){
    if(!state.players.length)return;
    const n=num($("cup-count").value)||8;
    const selected=state.selection.length;
    const women=state.createPreset==="women";
    const rounds=women?10:Math.ceil(Math.log2(n));
    const matches=women?20:n-1;
    $("cup-count-label").textContent=selected+"/"+n;
    $("cup-summary-players").textContent=String(n);
    $("cup-summary-rounds").textContent=String(rounds);
    $("cup-summary-matches").textContent=String(matches);
    const missing=state.selection.filter(slug=>!player(slug)?.installed);
    const engine=$("cup-execution").value==="engine_full";
    const country=state.createPreset==="custom"?
      state.presets.country_filters?.[$("cup-country-filter").value]:null;
    const insufficient=country&&country.participants.length<2;
    $("cup-country-warning").textContent=insufficient?
      country.label+": apenas "+country.participants.length+
      " enxadrista documentado no catálogo. Mínimo de 2 para Copa; não serão criados participantes fictícios.":"";
    $("cup-roster-note").textContent=insufficient?
      "Copa por país indisponível com o elenco atual. Fonte: Federação FIDE.":
      missing.length
      ?missing.length+" perfil(is) Rodent IV ausente(s) na seleção. "+
        (engine?"Execução UCI real será bloqueada.":"Projeção será identificada como sintética.")
      :"Todos os perfis selecionados possuem alias cadastrado; a instalação real será comprovada ao executar.";
    $("cup-summary-note").textContent=women?
      "Folga explícita por rodada; 20 confrontos sem desempate eliminatório.":
      "Byes explícitos quando a quantidade não é potência de 2.";
    $("cup-save").disabled=state.commandPending||selected!==n||!token;
  }
  $("cup-execution").addEventListener("change",updateSummary);
  for(const button of $("cup-creation-presets").querySelectorAll("button")){
    button.addEventListener("click",()=>setPreset(button.dataset.cupPreset));
  }
  function openCreate(preset="standard"){
    nav("create");
    if(state.presets){
      setPreset(preset);
    }else{
      bootstrap().then(()=>setPreset(preset)).catch(e=>note(e.message,true));
    }
    $("cup-create-heading").focus();
  }
  $("cup-new").addEventListener("click",()=>openCreate());
  for(const kind of ["standard","world_champions","women","custom"]){
    $("cup-preset-shortcut-"+kind).addEventListener("click",()=>openCreate(kind));
  }
  $("cup-quick-configure").addEventListener("click",()=>{
    const preset=$("cup-quick-preset").value;
    const n=$("cup-quick-count").value;
    const name=$("cup-quick-name").value;
    const edition=$("cup-quick-edition").value;
    openCreate(preset);
    $("cup-name").value=name;
    $("cup-edition").value=edition;
    if(preset!=="women"&&preset!=="world_champions"){
      $("cup-count").value=n;
      fillParticipants(true);
    }
  });
  function config(){
    return {
      name:$("cup-name").value.trim(),
      edition:$("cup-edition").value.trim(),
      preset:state.createPreset,
      participants:state.selection.slice(),
      seed:num($("cup-seed").value),
      seed_method:$("cup-seed-method").value,
      depth:num($("cup-depth").value),
      execution_mode:$("cup-execution").value,
      armageddon_white_seconds:num($("cup-white-seconds").value),
      armageddon_increment_seconds:num($("cup-increment").value),
      armageddon_min_black_seconds:num($("cup-bid-min").value),
      armageddon_max_black_seconds:num($("cup-bid-max").value),
    };
  }
  async function action(fn){
    if(state.commandPending)return;
    state.commandPending=true;
    updateSummary();
    try{await fn();}
    catch(error){note(error.message,true);}
    finally{
      state.commandPending=false;
      updateSummary();
      if(state.view==="main")await load(true);
    }
  }
  $("cup-create-form").addEventListener("submit",async event=>{
    event.preventDefault();
    if(!$("cup-create-form").reportValidity())return;
    const raw=config();
    if(raw.participants.length!==num($("cup-count").value)){
      note("Selecione exatamente a quantidade de participantes escolhida.",true);
      return;
    }
    await action(async()=>{
      const created=await post("/api/cups/create",raw);
      state.id=created.id;state.selectedMatch=null;state.pane="bracket";
      nav("main");
      note("Copa criada. As regras e participantes ficaram congelados.");
    });
  });
  $("cup-run").addEventListener("click",()=>action(async()=>{
    if(!state.id)return;
    const id=state.id;
    const priorGames=state.detail?.completed_games||0;
    const start=await post("/api/cups/"+id+"/run");
    state.lastRun={id,games:priorGames};
    note("Executando "+start.game+" · "+(stageLabel[start.stage]||start.stage));
  }));
  $("cup-pause").addEventListener("click",()=>action(async()=>{
    if(!state.id)return;
    const outcome=await post("/api/cups/"+state.id+"/pause");
    note(outcome.message||"Parada solicitada");
  }));
  for(const type of ["json","pgn"]){
    $("cup-export-"+type).addEventListener("click",()=>{
      if(!/^cup-[a-f0-9]{16}$/.test(state.id||""))return;
      window.location.assign("/api/cups/"+state.id+"/export/"+type);
    });
  }
  function viewMatch(){
    const data=state.detail;
    if(!data)return;
    if(data.format==="double_round_robin"){
      const game=data.rounds.flatMap(r=>r.games).find(
        x=>x.id===state.selectedWomanGameId)||
        data.rounds.flatMap(r=>r.games).find(x=>x.pgn);
      if(game?.pgn){openReplay(game.id);return;}
      note("A projeção feminina registra placares, não lances de xadrez.");
      setPane("matches");return;
    }
    const match=selectedMatch();
    const real=match?.games?.findLast(game=>game.pgn);
    if(real){openReplay(real.id);return;}
    note("Projeção estatística: os placares são visíveis em Confrontos, mas não existe tabuleiro nem PGN.");
    setPane("matches");
  }
  $("cup-open-match").addEventListener("click",viewMatch);
  $("cup-performance-link").addEventListener("click",()=>{
    const slug=$("cup-performance-link").dataset.player;
    if(slug&&/^[a-z0-9-]+$/.test(slug)){
      window.ChessSimHistoryNavigation?.open(slug);
    }
  });
  $("cup-performance-link").closest?.(".cup-player-summary")?.addEventListener("click",event=>{
    if(event.target.closest?.("button"))return;
    const slug=$("cup-performance-link").dataset.player;
    if(slug)window.ChessSimHistoryNavigation?.open(slug);
  });
  async function openReplay(id){
    try{
      const data=await get("/api/cups/"+state.id+"/games/"+id+"/replay");
      state.playback={...data,ply:0};
      $("cup-replay-heading").textContent=
        "Replay · "+name(data.game.white)+" × "+name(data.game.black);
      $("cup-replay-meta").textContent=
        (stageLabel[data.game.stage]||data.game.stage)+" · "+data.game.result+
        " · "+formatDate(data.game.finished_utc);
      replace("cup-replay-moves",...data.moves.map((move,index)=>{
        const li=node("li");
        const button=node("button",null,
          (index%2===0?Math.floor(index/2)+1+".":"")+move.san);
        button.type="button";
        button.addEventListener("click",()=>replayPly(index+1));
        li.appendChild(button);return li;
      }));
      replayPly(0);
      $("cup-replay-dialog").showModal();
    }catch(error){note(error.message,true);}
  }
  function replayPly(ply){
    const p=state.playback;if(!p)return;
    p.ply=Math.max(0,Math.min(ply,p.states.length-1));
    const position=p.states[p.ply];
    window.ChessSimBoard.paint($("cup-replay-board"),
      {fen:position.fen,last:position.last},p.ply);
    $("cup-replay-ply").textContent=p.ply+"/"+(p.states.length-1);
    $("cup-replay-first").disabled=p.ply===0;
    $("cup-replay-prev").disabled=p.ply===0;
    $("cup-replay-next").disabled=p.ply===p.states.length-1;
    $("cup-replay-last").disabled=p.ply===p.states.length-1;
  }
  $("cup-replay-close").addEventListener("click",()=>
    $("cup-replay-dialog").close());
  for(const [button,change] of [
    ["cup-replay-first",()=>0],["cup-replay-prev",n=>n-1],
    ["cup-replay-next",n=>n+1],["cup-replay-last",()=>Infinity]
  ]){
    $(button).addEventListener("click",()=>{
      if(state.playback)replayPly(change(state.playback.ply));
    });
  }
  setInterval(()=>{if(state.view==="main")load();},2500);
})();
