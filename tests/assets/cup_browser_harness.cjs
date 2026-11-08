"use strict";
/* Cup browser smoke #58. Runtime DOM/fetch harness, no external browser dependency. */
const assert=require("node:assert/strict");
const fs=require("node:fs");
const vm=require("node:vm");
const {URLSearchParams}=require("node:url");
const source=fs.readFileSync(process.argv[2],"utf8");
const TOKEN="local-cup-test-token-0123456789abcdef0123456789";
const FEN0="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const FEN1="rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1";
const FEN2="rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq e6 0 2";
let sequence=0;
let cup=null;
let original=null;
let completed=false;
let fakeReal=false;
let assigned="";
let rendered=[];
let replayCount=0;
const seen=[];
const timers=[];
class ClassList{
  constructor(o){this.o=o;}
  toggle(n,b){
    const c=new Set(this.o.className.split(/\s+/).filter(Boolean));
    if(b)c.add(n);else c.delete(n);
    this.o.className=[...c].join(" ");
  }
  contains(n){return this.o.className.split(/\s+/).includes(n);}
}
class Elem{
  constructor(tag="div",id=""){
    this.tagName=tag.toUpperCase();this.id=id;this.children=[];
    this.value="";this.dataset={};this.style={};this.className="";
    this.classList=new ClassList(this);this.hidden=false;
    this.disabled=false;this.checked=false;this.events={};
    this.textContent="";this.type="";
    this.attributes={};this.parentNode=null;
  }
  append(...args){for(const x of args)this.appendChild(x);}
  appendChild(item){item.parentNode=this;this.children.push(item);return item;}
  replaceChildren(...items){this.children=[];for(const item of items)this.appendChild(item);}
  addEventListener(name,fn){this.events[name]=fn;}
  setAttribute(key,val){this.attributes[key]=String(val);}
  querySelectorAll(selector){
    if(selector==="button")return this.children.filter(x=>x.tagName==="BUTTON");
    return [];
  }
  focus(){}
  reportValidity(){return true;}
  showModal(){this.open=true;}
  close(){this.open=false;}
  replaceWith(newNode){
    if(!this.parentNode)return;
    const index=this.parentNode.children.indexOf(this);
    if(index>=0)this.parentNode.children[index]=newNode;
  }
}
const ids=new Map();
const defaults={
  "cup-quick-preset":"standard","cup-quick-count":"8",
  "cup-quick-name":"Copa teste","cup-quick-edition":"Edição 1",
  "cup-count":"8","cup-name":"Copa teste","cup-edition":"Edição 1",
  "cup-seed":"7","cup-depth":"6","cup-execution":"fast_statistical",
  "cup-seed-method":"deterministic_draw",
  "cup-white-seconds":"240","cup-increment":"2",
  "cup-bid-min":"60","cup-bid-max":"240",
};
const document={
  getElementById(id){
    if(!ids.has(id)){
      const node=new Elem("div",id);
      if(Object.hasOwn(defaults,id))node.value=defaults[id];
      if(["cup-view","cup-create-view"].includes(id))node.hidden=true;
      ids.set(id,node);
    }
    return ids.get(id);
  },
  createElement(tag){return new Elem(tag);}
};
const $=id=>document.getElementById(id);
const players=Array.from({length:64},(_,i)=>({
  slug:"player"+i,name:"Jogador "+(i+1),installed:i<21,
}));
const women=["player60","player61","player62","player63","player59"];
const presets={
  standard:{participants:players.map(x=>x.slug),format:"knockout"},
  world_champions:{participants:players.slice(0,18).map(x=>x.slug),format:"knockout"},
  women:{participants:women,format:"double_round_robin"},
  custom:{participants:[],format:"knockout"},
};
for(const kind of ["standard","world_champions","women","custom"]){
  const b=new Elem("button");
  b.dataset.cupPreset=kind;
  $("cup-creation-presets").appendChild(b);
}
const reply=payload=>({ok:true,json:async()=>payload});
const cid="cup-0123456789abcdef";
const gId="r01-m01-s0-g1";
function gameRecord(){
  return {
    id:gId,round:1,stage:"classical",
    white:"player0",black:"player1",
    source:fakeReal?"engine_full":"fast_statistical",
    result:"1-0",pgn:fakeReal?"games/"+gId+".pgn":null,
    finished_utc:"2026-11-07T22:00:00-03:00",
    bid_proof:null
  };
}
function detail(){
  if(!cup)return null;
  const r=completed?[gameRecord()]:[];
  const playersPair=["player0","player1"];
  const game={
    id:"r01-m01",round:1,players:playersPair,
    winner:null,status:"PENDING",
    score:completed?{player0:1,player1:0}:{player0:0,player1:0},
    games:r,stage:"classical",
    next_game:{id:"r01-m01-s0-g"+(completed?"2":"1"),
      round:1,match_id:"r01-m01",stage:"classical",
      clock:[5400,30],white:completed?"player1":"player0",
      black:completed?"player0":"player1"},
  };
  const config={...original,format:"knockout",armageddon:{
    white_seconds:240,increment_seconds:2,min_black_seconds:60,
    max_black_seconds:240
  }};
  return {
    schema:"chesssim-cup-v1",id:cid,
    name:cup.name,edition:cup.edition,preset:cup.preset,
    participants_count:2,format:"knockout",
    execution_mode:fakeReal?"engine_full":cup.execution_mode,
    status:completed?"PAUSED":"READY",champion:null,
    completed_games:completed?1:0,completed_matches:0,total_matches:1,
    current_game:null,available_pgn:fakeReal?1:0,
    config,bracket:[{round:1,matches:[game]}],
    next_game:game.next_game,
    recent:r,source_warning:fakeReal?
      "Motor real: fidelidade histórica não comprovada":
      "Projeção sintética sem PGN/Elo",
    created_utc:"2026-11-08T00:00:00+00:00",
    updated_utc:"2026-11-08T00:00:00+00:00",
  };
}
const fetch=async(path,options={})=>{
  seen.push(path);
  if(options.method==="POST"){
    assert.equal(options.headers["X-Chesssim-Token"],TOKEN);
    const data=JSON.parse(options.body);
    if(path==="/api/cups/create"){
      assert.equal(data.participants.length,2);
      assert.equal(data.armageddon_white_seconds,240);
      assert.equal(data.execution_mode,"fast_statistical");
      original=data;
      cup={
        id:cid,name:data.name,edition:data.edition,preset:data.preset,
        execution_mode:data.execution_mode,participants_count:2,
        completed_games:0,total_matches:1,status:"READY"
      };
      return reply({id:cid,status:"READY"});
    }
    if(path==="/api/cups/"+cid+"/run"){
      assert.deepEqual(data,{});
      completed=true;sequence++;
      cup.status="PAUSED";
      return reply({id:cid,status:"RUNNING",game:gId,stage:"classical"});
    }
    if(path==="/api/cups/"+cid+"/pause")return reply({id:cid,pause_requested:true});
    throw Error("Unexpected POST "+path);
  }
  if(path==="/api/cups/presets")return reply(presets);
  if(path==="/api/arena")return reply({players,busy:false,duel_busy:false,
                                      league_busy:false,human_busy:false,cup_busy:false});
  if(path==="/api/cups")return reply({cups:cup?[cup]:[]});
  if(path==="/api/cups/"+cid)return reply(detail());
  if(path==="/api/history/players/player0")return reply({
    era_label:"Século XX",short_bio:"Fui um grande mestre mundial."
  });
  if(path==="/api/history/players/player1")return reply({
    era_label:"Século XIX",short_bio:"Disputei diversos campeonatos."
  });
  if(path==="/api/cups/"+cid+"/games/"+gId+"/replay"){
    replayCount++;
    return reply({schema:"chesssim-cup-replay-v1",game:gameRecord(),
      moves:[{uci:"e2e4",san:"e4"},{uci:"e7e5",san:"e5"}],
      states:[{fen:FEN0,last:null,turn:"white"},
        {fen:FEN1,last:["e2","e4"],turn:"black"},
        {fen:FEN2,last:["e7","e5"],turn:"white"}]
    });
  }
  throw Error("Unexpected GET "+path);
};
const window={
  location:{hash:"#token="+TOKEN,assign(path){assigned=path;}},
  ChessSimHistoryNavigation:{leave(){ $("history-view").hidden=true; }},
  ChessSimLeagueNavigation:{hide(){$("league-view").hidden=true;}},
  ChessSimBoard:{paint(element,view,ply){rendered.push({element,fen:view.fen,ply});}}
};
const context=vm.createContext({
  window,document,location:window.location,fetch,URLSearchParams,
  setInterval(fn,ms){timers.push({fn,ms});return timers.length;},
  Intl,Date,Number,String,Math,Object,console,
});
vm.runInContext(source,context,{filename:"cup.js",timeout:3000});
async function flush(){
  for(let i=0;i<12;i++)await new Promise(res=>setImmediate(res));
}
(async()=>{
  $("tab-cups").events.click();
  await flush();
  assert.equal($("cup-view").hidden,false);
  assert.equal($("arena-view").hidden,true);
  assert.equal($("cup-no-selection").hidden,false);
  assert.equal($("cup-creation-presets").children.length,4);

  $("cup-new").events.click();
  await flush();
  assert.equal($("cup-create-view").hidden,false);
  assert.equal($("cup-view").hidden,true);
  assert.equal($("cup-summary-players").textContent,"8");
  assert.equal($("cup-participants").children.length,64);
  $("cup-count").value="2";
  $("cup-count").events.change();
  assert.equal($("cup-summary-players").textContent,"2");
  assert.equal($("cup-summary-matches").textContent,"1");
  assert.equal($("cup-count-label").textContent,"2/2");

  await $("cup-create-form").events.submit({preventDefault(){}});
  await flush();
  assert.equal($("cup-view").hidden,false);
  assert.equal(cup.preset,"standard");
  assert.equal($("cup-list").children.length,1);
  assert.equal($("cup-selection").hidden,false);
  assert.equal($("cup-bracket").children.length,1);
  assert.equal($("cup-bracket").children[0].children.length,2);
  assert.equal($("cup-run").disabled,false);
  assert.equal($("cup-progress-games").textContent,"0");
  assert.equal($("cup-progress-matches").textContent,"0/1");
  assert($("cup-performance-name").textContent.includes("Jogador 1"));
  await flush();
  assert.equal($("cup-performance-era").textContent,"Século XX");

  await $("cup-run").events.click();
  await flush();
  assert.equal(sequence,1);
  assert.equal($("cup-progress-games").textContent,"1");
  assert($("cup-performance-record").textContent.includes("1 partidas"));
  assert.equal($("cup-export-pgn").disabled,true);
  $("cup-export-json").events.click();
  assert.equal(assigned,"/api/cups/"+cid+"/export/json");

  $("cup-tab-matches").events.click();
  assert.equal($("cup-pane-matches").hidden,false);
  assert($("cup-matches").children.length>=2);
  $("cup-tab-calendar").events.click();
  assert.equal($("cup-calendar").children.length,1);
  $("cup-tab-rules").events.click();
  assert.equal($("cup-pane-rules").hidden,false);
  assert($("cup-rules-content").children.length>=8);

  $("cup-tab-bracket").events.click();
  fakeReal=true;
  timers.find(x=>x.ms===2500).fn();
  await flush();
  assert.equal($("cup-export-pgn").disabled,false);
  $("cup-open-match").events.click();
  await flush();
  assert.equal(replayCount,1);
  assert.equal($("cup-replay-dialog").open,true);
  assert.equal(rendered.at(-1).fen,FEN0);
  $("cup-replay-next").events.click();
  assert.equal(rendered.at(-1).fen,FEN1);
  $("cup-replay-last").events.click();
  assert.equal(rendered.at(-1).fen,FEN2);
  $("cup-replay-close").events.click();
  assert.equal($("cup-replay-dialog").open,false);
  $("cup-new").events.click();
  await flush();
  const custom=$("cup-creation-presets").children.find(
    button=>button.dataset.cupPreset==="custom");
  custom.events.click();
  $("cup-country-filter").value="brazil";
  $("cup-country-filter").events.change();
  assert.equal($("cup-country-control").hidden,false);
  assert.equal($("cup-participants").children.length,1);
  assert.equal($("cup-count-label").textContent,"1/2");
  assert.equal($("cup-save").disabled,true);
  assert($("cup-country-warning").textContent.includes("Mínimo de 2"));
  assert(!ids.has("cup-clear"));
  console.log("Cup browser smoke: PASS");
})().catch(e=>{console.error(e);process.exitCode=1;});
