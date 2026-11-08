"use strict";
const assert=require("node:assert/strict");
const fs=require("node:fs");
const vm=require("node:vm");
const {URLSearchParams}=require("node:url");
const source=fs.readFileSync(process.argv[2],"utf8");
const TOKEN="league-test-local-capability-token-from-terminal";
let league=null;
let lastConfig=null;
let started=false;
let download="";
let runCount=0;
let historyCalls=0;
const requests=[];
const players=Array.from({length:64},(_,i)=>({
  slug:"player"+i,name:"Jogador "+(i+1),installed:i<21,
  historically_validated:false
}));
class Classes{
  constructor(node){this.node=node;}
  toggle(name,on){
    const parts=new Set(this.node.className.split(/\s+/).filter(Boolean));
    if(on)parts.add(name);else parts.delete(name);
    this.node.className=[...parts].join(" ");
  }
  contains(name){return this.node.className.split(/\s+/).includes(name);}
}
class Element{
  constructor(tag="div",id=""){
    this.tagName=tag.toUpperCase();this.id=id;this.children=[];
    this.className="";this.classList=new Classes(this);this.dataset={};
    this.style={width:""};this.attributes={};this.events={};
    this.value="";this.textContent="";this.hidden=false;this.checked=false;
    this.disabled=false;this.type=tag.toLowerCase();this.colSpan=1;
  }
  appendChild(item){this.children.push(item);return item;}
  append(...items){this.children.push(...items);}
  replaceChildren(...items){this.children=[...items];}
  addEventListener(event,fn){this.events[event]=fn;}
  setAttribute(key,value){this.attributes[key]=String(value);}
  getAttribute(key){return this.attributes[key]||null;}
  querySelectorAll(selector){
    if(selector==="input[type=checkbox]"){
      const result=[];
      const visit=node=>{
        if(node.tagName==="INPUT"&&node.type==="checkbox")result.push(node);
        for(const child of node.children)visit(child);
      };
      visit(this);return result;
    }
    return [];
  }
  focus(){}
  scrollIntoView(){}
  reportValidity(){return true;}
}
const map=new Map();
const defaults={
  "league-create-name":"Liga dos Imortais",
  "league-create-season":"Temporada 1",
  "league-create-count":"8",
  "league-create-modality":"rapid",
  "league-create-format":"double",
  "league-create-execution":"fast_statistical",
  "league-create-depth":"6",
  "league-points-win":"3","league-points-draw":"1",
  "league-points-loss":"0",
};
const document={
  getElementById(id){
    if(!map.has(id)){
      const node=new Element("div",id);
      if(Object.hasOwn(defaults,id))node.value=defaults[id];
      if(id==="league-view")node.hidden=true;
      map.set(id,node);
    }
    return map.get(id);
  },
  createElement(tag){return new Element(tag);}
};
const $=id=>document.getElementById(id);
const id="league-abcdef0123456789";
const reply=data=>({ok:true,json:async()=>data});
const mkRow=(i,games)=>({
  slug:players[i].slug,name:players[i].name,rank:i+1,
  played:games,wins:games,draws:0,losses:0,points:games*3,sb:0
});
function snapshot(){
  if(!league)return null;
  const complete=started?2:0;
  return {
    ...league,completed_games:complete,pending_games:56-complete,
    total_games:56,round_current:started?2:1,rounds_total:14,
    running_games:0,available_pgn:0,
    players:players.slice(0,8),
    config:lastConfig,standings:Array.from({length:8},(_,i)=>mkRow(i,i<2&&started?1:0)),
    games:[{id:"r001-g01",round:1,white:"player0",black:"player1",
           name_white:players[0].name,name_black:players[1].name,
           status:started?"COMPLETED":"PENDING",
           result:started?"1-0":null,source:started?"fast_statistical":null}],
    recent:[],round_progress:Array.from({length:14},(_,i)=>({
      round:i+1,total:4,completed:started&&i===0?2:0
    })),
    message:null,created_utc:"2026-11-08T00:00:00+00:00",
    execution_started_utc:null,updated_utc:"2026-11-08T00:00:00+00:00",
    current_match:null,
    source_warning:"Projeções estatísticas não são partidas UCI e não geram PGN."
  };
}
const fetch=async(path,request={})=>{
  requests.push(path);
  if(request.method==="POST"){
    assert.equal(request.headers["X-Chesssim-Token"],TOKEN);
    const payload=JSON.parse(request.body);
    if(path==="/api/leagues/create"){
      assert.equal(payload.participants.length,8);
      assert.equal(payload.format,"double");
      assert.equal(payload.points.win,3);
      assert.equal(payload.points.draw,1);
      assert.equal(payload.points.loss,0);
      assert.equal(payload.execution_mode,"fast_statistical");
      lastConfig=payload;
      league={
        id,name:payload.name,season:payload.season,format:payload.format,
        modality:payload.modality,execution_mode:payload.execution_mode,
        participants_count:8,status:"READY",total_games:56,
        completed_games:0,round_current:1,rounds_total:14
      };
      return reply({id,status:"READY"});
    }
    if(path==="/api/leagues/"+id+"/run"){
      assert.deepEqual(payload,{});
      started=true;runCount++;
      league.status="PAUSED";return reply({id,status:"RUNNING",round:1});
    }
    if(path==="/api/leagues/"+id+"/pause"){
      return reply({id,pause_requested:true});
    }
    throw Error("Unexpected POST: "+path);
  }
  if(path==="/api/arena")return reply({
    players,busy:false,duel_busy:false,human_busy:false,league_busy:false
  });
  if(path==="/api/leagues")return reply({leagues:league?[{
    ...league,completed_games:started?2:0,total_games:56
  }]:[]});
  if(path==="/api/leagues/"+id)return reply(snapshot());
  if(path==="/api/competition/ratings?modality=rapid")return reply({
    ratings:[{slug:"player0",display_rating:"2515.0"}]
  });
  if(path==="/api/leagues/"+id+"/round/1"||
     path==="/api/leagues/"+id+"/round/2")return reply({
    id,round:Number(path.split("/").at(-1)),rounds_total:14,games:[{
      id:"r001-g01",round:1,
      white_name:"Jogador 1",black_name:"Jogador 2",
      status:started?"COMPLETED":"PENDING",result:started?"1-0":null
    }]
  });
  if(path.startsWith("/api/leagues/"+id+"/games?")){
    historyCalls++;
    return reply({id,total:started?2:0,offset:0,limit:20,
      games:started?[{
        match_id:"r001-g01",round:1,white_name:"Jogador 1",
        black_name:"Jogador 2",white:"player0",black:"player1",
        result:"1-0",source:"fast_statistical"
      }]:[]});
  }
  throw Error("Unexpected GET: "+path);
};
const window={
  location:{hash:"#token="+TOKEN,assign(url){download=url;}},
  ChessSimHistoryNavigation:{leave(){
    $("history-view").hidden=true;
    $("arena-view").hidden=false;
  }}
};
const timers=[];
const context=vm.createContext({
  document,window,fetch,URLSearchParams,
  setInterval(fn,ms){timers.push({fn,ms});return timers.length;},
  console,Intl,Date,Number,String,Math
});
vm.runInContext(source,context,{filename:"league.js",timeout:3000});
const flush=async()=>{
  for(let i=0;i<8;i++)await new Promise(resolve=>setImmediate(resolve));
};
(async()=>{
  assert.equal($("league-view").hidden,true);
  $("tab-leagues").events.click();
  await flush();
  assert.equal($("league-view").hidden,false);
  assert.equal($("arena-view").hidden,true);
  assert.equal($("history-view").hidden,true);
  assert.equal($("tab-leagues").attributes["aria-pressed"],"true");
  assert.equal($("league-selection-empty").hidden,false);
  assert.equal($("league-create-submit").disabled,false);
  assert.equal($("league-participant-options")
               .querySelectorAll("input[type=checkbox]").filter(x=>x.checked).length,8);
  await $("league-create-form").events.submit({preventDefault(){}});
  await flush();
  assert.equal(league.name,"Liga dos Imortais");
  assert.equal($("league-list").children.length,1);
  assert.equal($("league-selection").hidden,false);
  assert.equal($("league-standings-body").children.length,8);
  assert.equal($("league-progress-games").textContent,"0/56");
  assert.equal($("league-export-pgn").disabled,true);
  assert.equal($("league-export-json").disabled,false);
  assert.equal($("league-season-name").textContent,"♜ Liga dos Imortais");
  assert.equal($("league-standings-body").children[0].children[7].textContent,
               "2515.0");
  $("league-export-json").events.click();
  assert.equal(download,"/api/leagues/"+id+"/export/json");

  await $("league-run").events.click();
  await flush();
  assert.equal(runCount,1);
  assert.equal($("league-progress-games").textContent,"2/56");
  assert.equal($("league-progress-complete").textContent,"2");
  assert.equal($("league-run").disabled,false);

  $("league-tab-rounds").events.click();
  await flush();
  assert.equal($("league-pane-rounds").hidden,false);
  assert.equal($("league-round-selector").children.length,14);
  assert.equal($("league-round-matches").children.length,1);

  $("league-tab-games").events.click();
  await flush();
  assert.equal($("league-pane-games").hidden,false);
  assert.equal($("league-games-list").children.length,1);
  assert(historyCalls>0);
  $("league-tab-details").events.click();
  assert.equal($("league-pane-details").hidden,false);
  assert($("league-detail-facts").children.length>=14);

  $("league-player-search").value="Jogador 1";
  $("league-tab-standings").events.click();
  assert.equal($("league-standings-body").children.length,1);
  assert(!map.has("league-clear"));
  // A full 64-player engine league cannot start with only 21 installed profiles.
  league.execution_mode="engine_full";
  lastConfig.execution_mode="engine_full";
  lastConfig.participants=players.map(p=>p.slug);
  league.participants_count=64;
  timers.find(t=>t.ms===2500).fn();
  await flush();
  assert.equal($("league-run").disabled,true);
  assert($("league-run-note").textContent.includes("43 perfil"));
  console.log("League browser smoke: PASS");
})().catch(error=>{console.error(error);process.exitCode=1;});
