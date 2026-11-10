"use strict";
const assert=require("node:assert/strict");
const fs=require("node:fs");
const vm=require("node:vm");
const {URLSearchParams}=require("node:url");
const source=fs.readFileSync(process.argv[2],"utf8");

class Element{
  constructor(tag="div",id=""){
    this.tagName=tag.toUpperCase();this.id=id;this.children=[];this.events={};
    this.hidden=false;this.disabled=false;this.value="";this.textContent="";
    this.className="";this.attributes={};this.open=false;this.focused=false;
    this.parentNode=null;this.href="";this.download="";
  }
  append(...els){for(const el of els)this.appendChild(el);}
  appendChild(el){el.parentNode=this;this.children.push(el);return el;}
  replaceChildren(...els){this.children=[];this.append(...els);}
  addEventListener(name,callback){this.events[name]=callback;}
  setAttribute(name,value){this.attributes[name]=String(value);}
  removeAttribute(name){delete this.attributes[name];this[name]="";}
  showModal(){this.open=true;}
  close(){this.open=false;this.events.close?.();}
  focus(){this.focused=true;}
  get firstChild(){return this.children[0]||null;}
}
const elements=new Map();
const get=id=>{
  if(!elements.has(id))elements.set(id,new Element("div",id));
  return elements.get(id);
};
const document={getElementById:get,createElement:tag=>new Element(tag)};
const id1="human-20261109T200000Z-000000000001";
const id2="human-20261109T200000Z-000000000002";
const game=(id,outcome,status="COMPLETED")=>({
  id,opponent:"tal",opponent_name:"Mikhail Tal",color:"white",
  result:outcome==="loss"?"0-1":"*",outcome,status,ply_count:4,
  created_at_utc:"2026-11-09T20:00:00+00:00",rating_eligible:false
});
const all=[game(id1,"loss"),game(id2,"incomplete","INTERRUPTED")];
const stats={
  games:2,completed:1,wins:0,draws:0,losses:1,unfinished:1,
  by_opponent:[{slug:"tal",name:"Mikhail Tal",games:2,wins:0,draws:0,losses:1}]
};
let activeRequests=[],paints=[],navCalls=[],pending=null,blockOnce=false;
function response(path){
  const u=new URL("http://127.0.0.1"+path);
  const page=Number(u.searchParams.get("page")||1);
  const filter=u.searchParams.get("outcome")||"all";
  const rows=all.filter(g=>filter==="all"||g.outcome===filter);
  const pageSize=Number(u.searchParams.get("page_size")||20);
  return {schema:"chesssim-my-games-v1",rating_eligible:false,
    stats,coverage:{scanned:2,rejected:0,partial:false},
    page,page_size:pageSize,total:rows.length,pages:Math.max(1,Math.ceil(rows.length/pageSize)),
    games:rows.slice((page-1)*pageSize,page*pageSize)};
}
const fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
async function fetch(path){
  activeRequests.push(path);
  if(path.includes("/replay")){
    return {ok:true,json:async()=>({
      schema:"chesssim-my-replay-v1",
      game:{...game(path.includes(id1)?id1:id2,"loss")},
      states:[{fen,last:null,san:null},{fen,last:["e2","e4"],san:"e4"}]})};
  }
  if(path.startsWith("/api/my-games?")){
    if(blockOnce){blockOnce=false;return new Promise(resolve=>{pending=resolve;});}
    return {ok:true,json:async()=>response(path)};
  }
  throw Error("Unexpected fetch "+path);
}
const window={
  ChessSimHistoryNavigation:{leave:()=>navCalls.push("history")},
  ChessSimLeagueNavigation:{hide:()=>navCalls.push("league")},
  ChessSimCupNavigation:{hide:()=>navCalls.push("cup")},
  ChessSimBoard:{paint:(el,state,ply)=>{paints.push({el,state,ply});return true;}},
};
vm.runInNewContext(source,{window,document,fetch,URLSearchParams,URL,
  Intl,Date,Math,Number,String,Object,console,Error},{timeout:3000});
const nav=window.ChessSimMyGamesNavigation;
async function flush(){for(let i=0;i<12;i++)await new Promise(resolve=>setImmediate(resolve));}

(async()=>{
  await get("tab-my-games").events.click();
  await flush();
  assert.equal(get("my-games-view").hidden,false);
  assert.equal(get("tab-my-games").attributes["aria-pressed"],"true");
  assert.deepEqual(navCalls,["history","league","cup"]);
  assert.equal(get("my-games-count").textContent,"2");
  assert.equal(get("my-games-losses").textContent,"1");
  assert.equal(get("my-games-unfinished").textContent,"1");
  assert.equal(get("my-games-rows").children.length,2);
  assert.equal(get("my-games-opponents").children.length,1);
  assert(!activeRequests.some(p=>p.includes("/api/history")||p.includes("/rating")));
  const first=get("my-games-rows").children[0].children.at(-1).firstChild;
  await first.events.click();await flush();
  assert.equal(get("my-games-dialog").open,true,"Personal replay dialog must open");
  assert.equal(get("my-games-download").download,id1+".pgn");
  assert.equal(get("my-games-moves").children.length,1);
  assert.equal(paints.at(-1).ply,0);
  get("my-games-forward").events.click();
  assert.equal(paints.at(-1).ply,1);
  get("my-games-board").events.keydown({key:"ArrowLeft",preventDefault(){}});
  assert.equal(paints.at(-1).ply,0);
  get("my-games-close").events.click();
  assert.equal(get("my-games-dialog").open,false);
  assert.equal(get("my-games-download").href,"");
  get("my-games-outcome").value="loss";
  get("my-games-filters").events.submit({preventDefault(){}});
  await flush();
  assert(activeRequests.at(-1).includes("outcome=loss"));
  assert.equal(get("my-games-rows").children.length,1);
  // A slow reply from the now-hidden tab may never repaint its contents.
  blockOnce=true;
  get("my-games-reload").events.click();
  await flush();
  assert(pending,"Expected a deferred GET");
  nav.hide();
  pending({ok:true,json:async()=>response("/api/my-games?page=1")});
  await flush();
  assert.equal(get("my-games-view").hidden,true);
  assert.equal(get("tab-my-games").attributes["aria-pressed"],"false");
  assert.equal(get("my-games-alert").textContent,"Lendo somente as suas partidas salvas…");
  console.log("My Games navigation, filters, replay, isolation: PASS");
})().catch(error=>{console.error(error);process.exitCode=1;});
