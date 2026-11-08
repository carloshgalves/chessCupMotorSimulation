"use strict";
/* Opt-in historical representation laboratory; not a source of invented Elo. */
(() => {
  const $ = id => document.getElementById(id);
  const valid = s => typeof s === "string" && /^[a-z0-9-]{2,48}$/.test(s);
  const token = new URLSearchParams(location.hash.slice(1)).get("token") || "";
  const state = {slug:null,players:[],version:0,active:false,pane:"representation",
                 report:null,polling:false,loaded:false};
  const el = (tag,text,cls) => {
    const n = document.createElement(tag);
    if (text!==null&&text!==undefined)n.textContent=String(text);
    if (cls)n.className=cls;
    return n;
  };
  function alert(text,bad=false){
    $("rep-feedback").textContent=text;
    $("rep-feedback").className=bad?"rep-error":"";
  }
  async function fetchJson(url,options){
    const response=await fetch(url,{cache:"no-store",...options});
    const payload=await response.json();
    if(!response.ok)throw new Error(payload.error||"Falha no laboratório");
    return payload;
  }
  function view(pane){
    if(!["representation","tests","analysis"].includes(pane))return;
    state.pane=pane;
    for(const name of ["representation","tests","analysis"]){
      $("rep-pane-"+name).hidden=name!==pane;
      $("rep-tab-"+name).setAttribute("aria-pressed",String(name===pane));
    }
  }
  for(const pane of ["representation","tests","analysis"]){
    $("rep-tab-"+pane).addEventListener("click",()=>view(pane));
  }
  $("rep-go-tests").addEventListener("click",()=>view("tests"));
  $("rep-depth").addEventListener("input",()=>{
    $("rep-depth-label").textContent=$("rep-depth").value;
  });
  $("rep-search").addEventListener("input",drawRoster);
  function drawRoster(){
    const text=$("rep-search").value.trim().toLocaleLowerCase("pt-BR");
    const box=$("rep-roster");box.replaceChildren();
    for(const person of state.players){
      if(!person.name.toLocaleLowerCase("pt-BR").includes(text))continue;
      const button=el("button",null,"rep-roster-player");
      button.type="button";
      button.setAttribute("aria-pressed",String(person.slug===state.slug));
      const title=el("strong",person.name);
      const detail=el("small",person.reference_period||"Período não revisado");
      button.append(title,detail);
      button.addEventListener("click",()=>select(person.slug));
      box.append(button);
    }
    if(!box.children.length)box.append(el("p","Nenhum perfil corresponde à busca."));
  }
  function draw(report){
    state.report=report;
    $("rep-name").textContent=report.name;
    $("rep-period").textContent="Período de referência: "+
      (report.period||"não disponível")+" · Janela ainda não validada";
    const badges=$("rep-state-tags");badges.replaceChildren();
    badges.append(el("span",report.installed?"Motor local encontrado":"Motor local ausente"));
    badges.append(el("span",report.source_available?"PGN local encontrado":"Sem PGN local"));
    badges.append(el("span",report.review_status==="reviewed"?"Biografia revisada":"Biografia em curadoria"));
    $("rep-motor-status").textContent=report.installed
      ?"Rodent IV com perfil local; força histórica não foi calibrada."
      :"Perfil Rodent IV não instalado neste ambiente; teste UCI indisponível.";
    $("rep-identity").textContent=report.personality
      ?"Personalidade: "+report.personality+". Sem garantia de reconstrução."
      :"Sem arquivo de personalidade UCI disponível.";
    $("rep-source-status").textContent=report.source_available
      ?"Corpus PGN opt-in disponível (SHA-256 "+report.source_sha256.slice(0,12)+"…)."
      :"Adicione manualmente um PGN de origem permitida em representation_sources/"+
       state.slug+".pgn (dentro do workspace --out). Não há download automático.";
    $("rep-historical-rating").textContent="Dados não verificados — nenhum gráfico exibido";
    $("rep-run").disabled=!(report.installed&&report.source_available&&token.length>=32)
      ||!!report.active;
    const box=$("rep-tests");box.replaceChildren();
    if(!report.reports.length){
      box.append(el("p","Nenhum teste concluído. Nenhum valor de precisão foi estimado."));
    }
    for(const test of report.reports){
      const row=el("div",null,"rep-test-row");
      const meta=el("span",test.created_utc.slice(0,16).replace("T"," ")+" UTC · "+
        test.sample.validation_games+" jogos · profundidade "+test.depth);
      const result=el("strong",test.sample.correct_top1+"/"+test.sample.evaluated_decisions+
        " · "+(test.sample.top1_rate*100).toFixed(1)+"% top-1");
      row.append(meta,result);box.append(row);
    }
    $("rep-analysis").replaceChildren();
    if(report.reports.length){
      const last=report.reports[0];
      $("rep-analysis").append(
        el("p","Última medição: "+last.sample.correct_top1+" de "+
        last.sample.evaluated_decisions+" decisões no conjunto de validação. "+
        "PGN "+last.evidence.source_pgn_sha256.slice(0,12)+"…"),
        el("p","Isso não mede identidade histórica, potência do motor ou acurácia geral.")
      );
    }else{
      $("rep-analysis").append(el("p","Métricas pendentes de corpus PGN e comparação UCI."));
    }
    if(report.active){
      alert("Teste UCI em execução. Rodent processa posições de validação. Não feche o servidor.");
    }else if(report.last?.status==="FAILED"){
      alert("Teste falhou: "+(report.last.message||"dados insuficientes"),true);
    }else if(report.last?.status==="COMPLETED"){
      alert("Teste concluído. A concordância top-1 é experimental, não fidelidade histórica.");
    }
  }
  async function select(slug){
    if(!state.active||!valid(slug))return;
    state.slug=slug;
    state.report=null;
    const p=state.players.find(x=>x.slug===slug);
    if(p){
      $("rep-match").value=p.name.split(" ").at(-1);
      const years=(p.reference_period||"").match(/^(\d{4})\s*[–-]\s*(\d{4})/);
      if(years){$("rep-from").value=years[1];$("rep-to").value=years[2];}
    }
    drawRoster();
    const n=++state.version;
    alert("Consultando perfil experimental…");
    try{
      const report=await fetchJson("/api/history/representation/"+slug);
      if(!state.active||n!==state.version)return;
      if(report.schema!=="chesssim-representation-v1"||report.slug!==slug){
        throw new Error("Resposta de representação incompatível");
      }
      alert("");
      draw(report);
    }catch(error){
      if(n===state.version)alert(error.message,true);
    }
  }
  async function start(){
    if(!state.slug||!state.report||$("rep-run").disabled)return;
    const fields={
      match_name:$("rep-match").value.trim(),
      from_year:Number($("rep-from").value),
      to_year:Number($("rep-to").value),
      depth:Number($("rep-depth").value),
      positions:Number($("rep-positions").value),
      seed:Number($("rep-seed").value),
    };
    $("rep-run").disabled=true;
    try{
      const started=await fetchJson("/api/history/representation/"+state.slug+"/run",{
        method:"POST",
        headers:{"Content-Type":"application/json","X-Chesssim-Token":token},
        body:JSON.stringify(fields),
      });
      if(started.status!=="RUNNING")throw new Error("Estado de execução inesperado");
      alert("Teste "+started.id+" iniciado. Acompanhe a atualização do relatório.");
      view("tests");
    }catch(error){alert(error.message,true);}
    finally{if(state.active&&state.slug)await select(state.slug);}
  }
  $("rep-run").addEventListener("click",start);
  async function enter(slug){
    state.active=true;
    $("representation-view").hidden=false;
    $("history-profile").hidden=true;
    $("history-roster").hidden=true;
    $("rep-heading").focus();
    if(!state.loaded){
      try{
        const roster=await fetchJson("/api/arena");
        if(!state.active)return;
        state.players=roster.players;
        state.loaded=true;
      }catch(error){alert(error.message,true);return;}
    }
    view("representation");
    await select(slug);
  }
  function leave(){
    state.version++;
    state.active=false;
    $("representation-view").hidden=true;
  }
  function suspend(){state.version++;state.active=false;}
  function resume(){
    if($("representation-view").hidden)return;
    state.active=true;
    if(state.slug)select(state.slug);
  }
  window.ChessSimRepresentation=Object.freeze({enter,leave,suspend,resume});
  setInterval(()=>{
    if(!state.active||!state.slug||state.polling||!state.report?.active)return;
    state.polling=true;
    select(state.slug).finally(()=>{state.polling=false;});
  },2500);
})();
