(() => {
  "use strict";
  const make=(tag,text)=>{const el=document.createElement(tag);if(text!==undefined)el.textContent=text;return el;};
  const dlg=make("dialog");dlg.className="cup-replay-dialog";
  const heading=make("h2","Observatório de partidas"), status=make("p"), select=make("select");
  heading.id="competition-observer-heading";dlg.setAttribute("aria-labelledby",heading.id);
  select.setAttribute("aria-label","Partida");
  const bar=make("div"), board=make("div"), clocks=make("p"), moves=make("ol");
  board.className="board competition-observer-board";board.setAttribute("aria-label","Tabuleiro da competição");
  const layout=make("div");layout.className="cup-replay-body";
  const side=make("div");side.append(clocks,moves);layout.append(board,side);
  moves.className="cup-replay-moves";bar.className="competition-observer-bar";
  const controls=make("div"), position=make("span"), speed=make("select");
  speed.setAttribute("aria-label","Velocidade de replay");
  for(const n of [.5,1,2,4]){const o=make("option",n+"×");o.value=n;speed.append(o);}speed.value="1";
  controls.className="cup-replay-controls competition-observer-bar";
  let base="", games=[], chosen=null, data=null, ply=0, follow=true, edge=true, playing=false, epoch=0, busy=false, stamp=0, lastList=0, movesKey="", listKey="";
  const button=(parent,label,fn)=>{const b=make("button",label);b.type="button";b.addEventListener("click",fn);parent.append(b);return b;};
  const previous=button(bar,"Partida anterior",()=>navigate(-1));
  const next=button(bar,"Próxima partida",()=>navigate(1));
  button(bar,"Ao vivo",()=>{epoch++;follow=true;edge=true;playing=false;chosen=null;data=null;lastList=0;poll();});
  bar.append(select);
  const first=button(controls,"|◀",()=>seek(0)), prev=button(controls,"◀",()=>seek(ply-1));
  const forward=button(controls,"▶",()=>seek(ply+1)), end=button(controls,"▶|",()=>seek(Infinity));
  const play=button(controls,"Reproduzir",()=>{follow=false;edge=false;playing=!playing;paint();});
  controls.append(position,speed);
  button(controls,"Fechar",()=>dlg.close());dlg.append(heading,status,bar,layout,controls);document.body.append(dlg);
  const name=s=>s||"A definir";
  const clock=v=>v==null?"—":Math.floor(Math.max(0,v)/60)+":"+String(Math.floor(Math.max(0,v)%60)).padStart(2,"0");
  async function get(path){const r=await fetch(path,{cache:"no-store"});const j=await r.json();if(!r.ok)throw Error(j.error||"Falha de observação");return j;}
  function seek(n){follow=false;edge=false;playing=false;ply=Math.max(0,Math.min(n,(data?.states?.length||1)-1));paint();}
  function navigate(n){const i=games.findIndex(g=>g.id===chosen);const g=games[i+n];if(g)choose(g.id);}
  function choose(id){epoch++;chosen=id;follow=false;edge=games.find(g=>g.id===id)?.status==="RUNNING";playing=false;data=null;ply=0;paint();poll();}
  select.addEventListener("change",()=>choose(select.value));
  function paint(){
    const i=games.findIndex(g=>g.id===chosen);previous.disabled=i<=0;next.disabled=i<0||i>=games.length-1;
    select.value=chosen||"";
    const g=games[i];status.textContent=g?name(g.white_name||g.white)+" × "+name(g.black_name||g.black)+" · "+(g.result||({RUNNING:"Ao vivo",COMPLETED:"Concluída",PENDING:"Pendente"}[g.status]||g.status)):"Nenhuma partida disponível";
    const states=data?.states||[];const max=states.length-1;
    if(edge)ply=Math.max(0,max);else ply=Math.min(ply,Math.max(0,max));
    const p=states[ply];
    if(p){window.ChessSimBoard.paint(board,{fen:p.fen,last:p.last},ply);const c=[...(p.clocks||[null,null])];if(data.status==="RUNNING"&&ply===max){const turn=p.turn==="white"?0:1;if(c[turn]!=null)c[turn]-=(data.elapsed_since_move||0)+(performance.now()-stamp)/1000;}clocks.textContent="Brancas "+clock(c[0])+" · Pretas "+clock(c[1]);}
    else{window.ChessSimBoard.paint(board,null);clocks.textContent="Sem lances disponíveis";}
    const key=chosen+"|"+(data?.moves||[]).map(m=>m.uci).join(" ");
    if(key!==movesKey){movesKey=key;moves.replaceChildren(...(data?.moves||[]).map((m,i)=>{const li=make("li");button(li,(i%2===0?Math.floor(i/2)+1+". ":"")+m.san,()=>seek(i+1));return li;}));}
    for(const [i,li] of Array.from(moves.children).entries())li.firstChild.setAttribute("aria-pressed",String(i+1===ply));
    position.textContent=states.length?ply+"/"+max:"—";first.disabled=prev.disabled=ply===0;forward.disabled=end.disabled=ply>=max;play.disabled=max<=0;play.textContent=playing?"Pausar replay":"Reproduzir";
  }
  async function poll(){
    if(!dlg.open||busy)return;busy=true;const e=epoch;
    try{
      if(!lastList||performance.now()-lastList>2000||!chosen){
        const list=await get(base);if(e!==epoch||!dlg.open)return;
        games=list.games;lastList=performance.now();
        if(follow){const target=list.live[0]||chosen||games.find(g=>g.status==="PENDING")?.id||games.at(-1)?.id;if(chosen!==target){chosen=target;data=null;ply=0;}}
        if(!games.some(g=>g.id===chosen)){chosen=games[0]?.id||null;data=null;}
        if(!data&&games.find(g=>g.id===chosen)?.status==="RUNNING"&&ply===0)edge=true;
        const key=JSON.stringify(games.map(g=>[g.id,g.status,g.result]));
        if(key!==listKey){listKey=key;select.replaceChildren(...games.map(g=>{const o=make("option",name(g.white_name||g.white)+" × "+name(g.black_name||g.black)+" · "+(g.result||g.status));o.value=g.id;return o;}));}
      }
      if(chosen){const id=chosen;const current=await get(base+"/"+id);if(e!==epoch||id!==chosen||!dlg.open)return;data=current;stamp=performance.now();if(current.status==="RUNNING"&&edge)ply=current.states.length-1;}
      paint();
    }catch(err){if(e===epoch&&dlg.open)status.textContent=err.message;}
    finally{busy=false;}
  }
  let lastAdvance=0;
  setInterval(()=>{if(!dlg.open)return;if(playing&&performance.now()-lastAdvance>=1000/Number(speed.value)){lastAdvance=performance.now();if(ply<(data?.states?.length||1)-1)ply++;else playing=false;paint();}},100);
  setInterval(poll,750);
  dlg.addEventListener("close",()=>{epoch++;playing=false;});
  window.CompetitionObserver={open(kind,id,gameId=null){epoch++;base="/api/"+kind+"/"+id+"/observe";games=[];chosen=gameId;data=null;ply=0;follow=!gameId;edge=!gameId;playing=false;lastList=0;listKey="";if(!dlg.open)dlg.showModal();paint();poll();}};
})();
