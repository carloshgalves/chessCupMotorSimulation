"use strict";
const assert=require("node:assert/strict");
const fs=require("node:fs");
const vm=require("node:vm");
const {URLSearchParams}=require("node:url");
const source=fs.readFileSync(process.argv[2],"utf8");
class E {
  constructor(tag="div",id=""){
    this.tagName=tag.toUpperCase();this.id=id;this.children=[];this.events={};
    this.hidden=false;this.disabled=false;this.value="";this.dataset={};
    this.style={};this.textContent="";this.className="";this.attributes={};
    this.parentNode=null;this.colSpan=1;this.open=false;
  }
  append(...nodes){for(const n of nodes)this.appendChild(n);}
  appendChild(n){n.parentNode=this;this.children.push(n);return n;}
  replaceChildren(...nodes){this.children=[];this.append(...nodes);}
  addEventListener(type,callback){this.events[type]=callback;}
  setAttribute(name,value){this.attributes[name]=String(value);}
  showModal(){this.open=true;}
  close(){this.open=false;}
  focus(){}
}
const els=new Map(),get=id=>{
  if(!els.has(id))els.set(id,new E("div",id));
  return els.get(id);
};
const document={getElementById:get,createElement:tag=>new E(tag)};
const playerNames=[
  {slug:"fischer",name:"Bobby Fischer",era_label:"1970–1972"},
  {slug:"tal",name:"Mikhail Tal",era_label:"1957–1961"},
];
const profile={slug:"fischer",name:"Bobby Fischer",era_label:"1970–1972",
  short_bio:"Campeão mundial.",review_status:"reviewed",
  timeline:[{year:1972,title:"Conquista mundial",summary:"Marco documentado"}]};
const first={id:"cup:cup-0000000000000001:r01-m01-s0-g1",
  opponent:"tal",opponent_name:"Mikhail Tal",white:"fischer",black:"tal",
  color:"white",result:"1-0",outcome:"win",status:"COMPLETED",origin:"cup",
  modality:"classical",competition:"Copa · cup-0000000000000001",
  date:"2026-11-08T10:10:00+00:00",pgn:true,rating_eligible:true};
const second={...first,id:"projection:cup-0000000000000002:r01-m01-s0-g1",
  origin:"projection",competition:"Copa · projeção",pgn:false,
  rating_eligible:false,result:"0-1",outcome:"loss"};
const overview={
  verified:1,completed:1,projections:1,wins:1,draws:0,losses:0,
  modalities:{
    classical:{games:1,wins:1,draws:0,losses:0},
    rapid:{games:0,wins:0,draws:0,losses:0},
    blitz:{games:0,wins:0,draws:0,losses:0},
    bullet:{games:0,wins:0,draws:0,losses:0},
    unclassified:{games:0,wins:0,draws:0,losses:0},
  },
};
const fen0="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
let replayRequests=0,opened=[],downloads=[],painted=[],requests=[],archivePages=0;
function report(url){
  const parsed=new URL("http://127.0.0.1"+url);
  const page=Number(parsed.searchParams.get("page")||1);
  const q=parsed.searchParams.get("q")||"";
  archivePages++;
  let games=q==="nothing"?[]:[first,second];
  const origin=parsed.searchParams.get("origin")||"all";
  games=games.filter(x=>origin==="all"||x.origin===origin);
  const data={
    schema:"chesssim-player-archive-v1",slug:"fischer",
    filters:{},total:games.length,all_count:2,page,page_size:20,pages:1,
    games,overview,coverage:{rejected_records:0,partial:false},
    competitions:[
      {origin:"cup",name:"Copa local",games:1,verified:1,projections:0},
      {origin:"projection",name:"Copa projeção",games:1,verified:0,projections:1}
    ],disclaimer:"Sem PGNs inventados"
  };
  return data;
}
const fetch=async(url,opts={})=>{
  requests.push(url);
  if(url.includes("/archive/replay?")){
    replayRequests++;
    return {ok:true,json:async()=>({
      schema:"chesssim-archive-replay-v1",game:{
        id:first.id,white:"fischer",black:"tal",origin:"cup",result:"1-0"},
      states:[{fen:fen0,last:null,san:null},
              {fen:fen0,last:["e2","e4"],san:"e4"}],
    })};
  }
  let obj;
  if(url.includes("/archive?"))obj=report(url);
  else if(url.includes("/rating?"))obj={ratings:[{
    display_rating:"2510.0",games:1,status:"provisional"}]};
  else if(url.includes("/games"))obj={games:[]};
  else throw Error("Unexpected fetch "+url);
  return {ok:true,json:async()=>obj};
};
const win={
  ChessSimHistoryNavigation:{open:slug=>opened.push(slug)},
  ChessSimBoard:{paint:(container,position,ply)=>{
    painted.push({position,ply});return true;}},
  location:{assign:url=>downloads.push(url)},
};
const context=vm.createContext({window:win,document,fetch,
  URLSearchParams,URL,Intl,Date,Number,String,Math,Object,console});
vm.runInContext(source,context,{filename:"history_archive.js",timeout:3000});
const nav=win.ChessSimHistoryArchive;
async function flush(){for(let i=0;i<15;i++)await new Promise(resolve=>setImmediate(resolve));}
(async()=>{
  await nav.enter(profile,playerNames);await flush();
  assert.equal(get("history-modern").hidden,false);
  assert.equal(get("history-original").hidden,true);
  assert.equal(get("archive-player-roster").children.length,2);
  assert.equal(get("archive-player-name").textContent,"Bobby Fischer");
  assert.equal(get("archive-overview-stats").children.length,8);
  assert.equal(get("archive-tournament-list").children.length,2);
  assert.equal(get("archive-recent-games").children.length,2);
  get("archive-tab-games").events.click();
  assert.equal(get("archive-pane-games").hidden,false);
  assert.equal(get("archive-games-table-body").children.length,2);
  const projectionAction=get("archive-games-table-body").children[1].children.at(-1).children[0];
  assert.equal(projectionAction.disabled,true);
  const realAction=get("archive-games-table-body").children[0].children.at(-1).children[0];
  assert.equal(realAction.disabled,false);
  await realAction.events.click();await flush();
  assert.equal(replayRequests,1);
  assert.equal(get("archive-replay-dialog").open,true);
  assert.equal(painted.at(-1).ply,0);
  get("archive-replay-next").events.click();
  assert.equal(painted.at(-1).ply,1);
  get("archive-replay-close").events.click();
  assert.equal(get("archive-replay-dialog").open,false);

  get("archive-export-json").events.click();
  assert(downloads.at(-1).includes("/archive/export/json"));
  get("archive-tab-elo").events.click();
  await flush();
  assert(get("archive-rating-info").textContent.includes("2510.0"));
  get("archive-tab-records").events.click();
  assert.equal(get("history-original").hidden,false);
  get("archive-tab-overview").events.click();
  assert.equal(get("history-original").hidden,true);
  get("archive-player-roster").children[1].events.click();
  assert.deepEqual(opened,["tal"]);
  get("archive-search").value="nothing";
  get("archive-origin").value="all";
  get("archive-modality").value="all";
  get("archive-result").value="all";
  get("archive-year-from").value="";
  get("archive-year-to").value="";
  await get("archive-filters").events.submit({preventDefault(){}});
  await flush();
  assert.equal(get("archive-games-table-body").children.length,1);
  assert.equal(get("archive-games-table-body").children[0].children[0].colSpan,8);
  assert(archivePages>=2);
  nav.leave();
  assert.equal(get("history-modern").hidden,true);
  console.log("History archive DOM smoke: PASS");
})().catch(error=>{console.error(error);process.exitCode=1;});
