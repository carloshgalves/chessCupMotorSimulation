(() => {
  "use strict";
  const make=(tag,text)=>{const el=document.createElement(tag);if(text!==undefined)el.textContent=text;return el;};
  const dlg=make("dialog");dlg.className="cup-replay-dialog";
  const heading=make("h2","Observatório de partidas"), status=make("p"), select=make("select");
  select.setAttribute("aria-label","Partida");
  const bar=make("div"), board=make("div"), clocks=make("p"), moves=make("ol");
  board.className="chess-board";board.setAttribute("aria-label","Tabuleiro da competição");
  const layout=make("div");layout.className="cup-replay-layout";
  const side=make("div");side.append(clocks,moves);layout.append(board,side);
  const controls=make("div"), position=make("span"), speed=make("select");
  speed.setAttribute("aria-label","Velocidade de replay");
  for(const n of [.5,1,2,4]){const o=make("option",n+"×");o.value=n;speed.append(o);}speed.value="1";
  let base="", games=[], chosen=null, data=null, ply=0, follow=true, playing=false, epoch=0, busy=false, stamp=0, lastList=0;
  const button=(parent,label,fn)=>{const b=make("button",label);b.type="button";b.addEventListener("click",fn);parent.append(b);return b;};
  const previous=button(bar,"Partida anterior",()=>navigate(-1));
  const next=button(bar,"Próxima partida",()=>navigate(1));
  button(bar,"Ao vivo",()=>{follow=true;playing=false;chosen=null;poll();});
  bar.append(select);
  const first=button(controls,"|◀",()=>seek(0)), prev=button(controls,"◀",()=>seek(ply-1));
  const forward=button(controls,"▶",()=>seek(ply+1)), end=button(controls,"▶|",()=>seek(Infinity));
  const play=button(controls,"Reproduzir",()=>{follow=false;playing=!playing;paint();});
  controls.append(position,speed);
  button(controls,"Fechar",()=>dlg.close());dlg.append(heading,status,bar,layout,controls);document.body.append(dlg);
  const name=s=>s||"A definir";
  const clock=v=>v==null?"—":Math.floor(Math.max(0,v)/60)+":"+String(Math.floor(Math.max(0,v)%60)).padStart(2,"0");
  async function get(path){const r=await fetch(path,{cache:"no-store"});const j=await r.json();if(!r.ok)throw Error(j.error||"Falha de observação");return j;}
  function seek(n){follow=false;playing=false;ply=Math.max(0,Math.min(n,(data?.states?.length||1)-1));paint();}
  function navigate(n){const i=games.findIndex(g=>g.id===chosen);const g=games[i+n];if(g)choose(g.id);}
  function choose(id){epoch++;chosen=id;follow=false;playing=false;data=null;ply=0;paint();poll();}
  select.addEventListener("change",()=>choose(select.value));
  function paint(){
    const i=games.findIndex(g=>g.id===chosen);previous.disabled=i<=0;next.disabled=i<0||i>=games.length-1;
    select.value=chosen||"";
    const g=games[i];status.textContent=g?name(g.white)+" × "+name(g.black)+" · "+(g.result||({RUNNING:"Ao vivo",COMPLETED:"Concluída",PENDING:"Pendente"}[g.status]||g.status)):"Nenhuma partida disponível";
    const states=data?.states||[];const max=states.length-1;
    if(follow)ply=Math.max(0,max);else ply=Math.min(ply,Math.max(0,max));
    const p=states[ply];
    if(p){window.ChessSimBoard.paint(board,{fen:p.fen,last:p.last},ply);const c=[...(p.clocks||[null,null])];if(data.status==="RUNNING"&&ply===max){const turn=p.turn==="white"?0:1;if(c[turn]!=null)c[turn]-=(data.elapsed_since_move||0)+(performance.now()-stamp)/1000;}clocks.textContent="Brancas "+clock(c[0])+" · Pretas "+clock(c[1]);}
    else{board.replaceChildren();clocks.textContent="Sem lances disponíveis";}
    moves.replaceChildren(...(data?.moves||[]).map((m,i)=>{const li=make("li");button(li,(i%2===0?Math.floor(i/2)+1+". ":"")+m.san,()=>seek(i+1));return li;}));
    position.textContent=states.length?ply+"/"+max:"—";first.disabled=prev.disabled=ply===0;forward.disabled=end.disabled=ply>=max;play.disabled=max<=0;play.textContent=playing?"Pausar replay":"Reproduzir";
  }
  async function poll(){
    if(!dlg.open||busy)return;busy=true;const e=epoch;
    try{
      if(!lastList||performance.now()-lastList>2000||!chosen){
        const list=await get(base);if(e!==epoch||!dlg.open)return;
        games=list.games;lastList=performance.now();
        if(follow)chosen=list.live[0]||chosen||games.find(g=>g.status==="PENDING")?.id||games.at(-1)?.id;
        if(!games.some(g=>g.id===chosen))chosen=games[0]?.id||null;
        select.replaceChildren(...games.map(g=>{const o=make("option",name(g.white)+" × "+name(g.black)+" · "+(g.result||g.status));o.value=g.id;return o;}));
      }
      if(chosen){const id=chosen;const current=await get(base+"/"+id);if(e!==epoch||id!==chosen||!dlg.open)return;data=current;stamp=performance.now();}
      paint();
    }catch(err){if(e===epoch&&dlg.open)status.textContent=err.message;}
    finally{busy=false;}
  }
  let lastAdvance=0;
  setInterval(()=>{if(!dlg.open)return;if(playing&&performance.now()-lastAdvance>=1000/Number(speed.value)){lastAdvance=performance.now();if(ply<(data?.states?.length||1)-1)ply++;else playing=false;paint();}},100);
  setInterval(poll,750);
  dlg.addEventListener("close",()=>{epoch++;playing=false;});
  window.CompetitionObserver={open(kind,id){epoch++;base="/api/"+kind+"/"+id+"/observe";games=[];chosen=null;data=null;ply=0;follow=true;playing=false;lastList=0;if(!dlg.open)dlg.showModal();paint();poll();}};
})();
