"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const {URLSearchParams} = require("node:url");
const source = fs.readFileSync(process.argv[2], "utf8");
const token = "correct-human-capability-token-from-loopback-arena";
const calls = [];
let now = 1000000;
let game = null;
const intervals = [];
const START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const E4 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1";
const E5 = "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq e6 0 2";
class ClassList {
  constructor(owner){this.owner=owner;}
  contains(name){return this.owner.className.split(/\s+/).includes(name);}
  toggle(name,on){
    const all=new Set(this.owner.className.split(/\s+/).filter(Boolean));
    if(on===undefined? !all.has(name):on)all.add(name);
    else all.delete(name);
    this.owner.className=[...all].join(" ");
  }
}
class Node {
  constructor(tag="div",id=""){
    this.tagName=tag.toUpperCase();this.id=id;this.children=[];
    this.events={};this.attributes={};this.dataset={};
    this.className="";this.classList=new ClassList(this);
    this.textContent="";this.value="";this.hidden=false;this.disabled=false;
    this.scrollHeight=0;this.scrollTop=0;this.clientHeight=280;
    this.parentNode=null;
  }
  append(...children){for(const c of children)this.appendChild(c);}
  appendChild(child){child.parentNode=this;this.children.push(child);return child;}
  replaceChildren(...children){
    this.children=[];for(const child of children)this.appendChild(child);
  }
  replaceChild(next,prior){
    const index=this.children.indexOf(prior);
    assert(index>=0);
    next.parentNode=this;
    this.children[index]=next;
    return prior;
  }
  get lastElementChild(){return this.children.at(-1)||null;}
  addEventListener(type,fn){this.events[type]=fn;}
  setAttribute(name,value){this.attributes[name]=String(value);}
  getAttribute(name){return this.attributes[name]??null;}
  querySelectorAll(selector){
    if(selector==="button")return this.children.filter(x=>x.tagName==="BUTTON");
    if(selector==="[data-promote]")return this.children.filter(x=>x.dataset.promote);
    return [];
  }
  querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
  contains(other){return this===other||this.children.some(x=>x.contains(other));}
  closest(selector){
    if(selector===".square"&&this.classList.contains("square"))return this;
    return this.parentNode?.closest(selector)||null;
  }
  focus(){}
  reportValidity(){return true;}
}
const nodes=new Map();
const document={
  getElementById(id){
    if(!nodes.has(id))nodes.set(id,new Node("div",id));
    return nodes.get(id);
  },
  createElement(tag){return new Node(tag);}
};
const $=id=>document.getElementById(id);
for(const side of ["white","black","random"]){
  const b=new Node("button");b.dataset.side=side;
  $("human-side-buttons").appendChild(b);
}
for(const promo of ["q","r","b","n"]){
  const b=new Node("button");b.dataset.promote=promo;
  $("human-promotion").appendChild(b);
}
$("human-clock").value="blitz";
$("human-depth").value="6";
$("human-opening").value="none";
$("human-minutes").value="10";
$("human-increment").value="2";
$("human-promotion").hidden=true;
const players=[
  {slug:"fischer",name:"Bobby Fischer",installed:true},
  {slug:"tal",name:"Mikhail Tal",installed:true},
  {slug:"mequinho",name:"Henrique Mecking",installed:false}
];
const reply=payload=>({ok:true,json:async()=>payload});
const fetch=async(path,options={})=>{
  if(options.method==="POST"){
    assert.equal(options.headers["X-Chesssim-Token"],token);
    assert.equal(options.headers["Content-Type"],"application/json");
    const request=JSON.parse(options.body);
    calls.push({path,request});
    if(path==="/api/arena/human/start"){
      assert.equal(request.opponent,"fischer");
      game={
        id:"human-test",status:"RUNNING",revision:0,config:{...request,color:"white"},
        white:"Você",black:"Bobby Fischer",fen:START,turn:"white",
        human_turn:true,legal_moves:["e2e4","g1f3"],last:null,
        result:"*",termination:null,clocks:[180,180],moves:[],
        states:[{fen:START,last:null,turn:"white",clocks:[180,180]}],
        draw_offer:null
      };
      return reply({id:game.id,status:game.status});
    }
    if(path==="/api/arena/human/move"){
      assert.equal(request.id,game.id);
      assert.equal(request.revision,0);
      assert.equal(request.uci,"e2e4");
      // Simulate a UCI burst arriving as a single HTTP snapshot.
      game.revision=2;game.fen=E5;game.turn="white";game.human_turn=true;
      game.last=["e7","e5"];game.clocks=[179,179];
      game.legal_moves=["g1f3","d2d4"];
      game.moves=[{ply:1,san:"e4",uci:"e2e4"},
                  {ply:2,san:"e5",uci:"e7e5"}];
      game.states=[
        {fen:START,last:null,turn:"white",clocks:[180,180]},
        {fen:E4,last:["e2","e4"],turn:"black",clocks:[179,180]},
        {fen:E5,last:["e7","e5"],turn:"white",clocks:[179,179]}
      ];
      return reply({id:game.id,revision:2,status:"RUNNING"});
    }
    if(path==="/api/arena/human/action"){
      assert.equal(request.action,"resign");
      game.status="COMPLETED";game.result="0-1";
      game.termination="HumanResignation";game.legal_moves=[];
      game.human_turn=false;
      return reply({id:game.id,status:game.status});
    }
    throw Error("Unexpected POST: "+path);
  }
  if(path==="/api/arena")return reply({
    players,busy:!!game&&game.status==="RUNNING",human_busy:!!game&&game.status==="RUNNING",
    duel_busy:false,session:null
  });
  if(path==="/api/arena/human")return reply({schema:"chesssim-human-game-v1",game});
  throw Error("Unexpected GET: "+path);
};
const board=$("human-board");
const paint=(element,position)=>{
  element.dataset.lastFen=position.fen;
  if(element.children.length!==64){
    const squares=[];
    for(let i=0;i<64;i++){
      const sq=new Node("div");
      sq.className="square";
      squares.push(sq);
    }
    element.replaceChildren(...squares);
  }
  return true;
};
const clock={now:()=>now};
const window={
  location:{hash:"#token="+token},confirm:()=>true,
  ChessSimBoard:{paint,refreshArtwork(){}}
};
const context=vm.createContext({
  document,window,location:window.location,Date:clock,fetch,
  URLSearchParams,setInterval(fn,delay){intervals.push({fn,delay});return intervals.length;},
  Number,String,Math,console,Promise
});
vm.runInContext(source,context,{filename:"human_play.js",timeout:3000});
const flush=async()=>{await new Promise(resolve=>setImmediate(resolve));await new Promise(resolve=>setImmediate(resolve));};
(async()=>{
  await flush();
  $("arena-mode-human").events.click();
  assert.equal($("human-form").hidden,false);
  assert.equal($("duel-form").hidden,true);
  assert.equal($("human-viewer").hidden,false);
  assert.equal($("duel-viewer").hidden,true);
  assert.equal($("human-opponent").value,"fischer");
  assert.equal($("human-launch").disabled,false);
  await $("human-form").events.submit({preventDefault(){}});
  await flush();
  assert.equal(calls[0].path,"/api/arena/human/start");
  assert.equal(calls[0].request.preset,"blitz");
  assert.equal($("human-white-name").textContent,"Você");
  assert.equal($("human-black-name").textContent,"Bobby Fischer");
  assert.equal(board.children.length,64);
  assert.equal($("human-turn-title").textContent,"Sua vez");

  // e2 to e4 by clicking the source and destination: canonical A8..H1.
  board.events.click({target:board.children[52]});
  board.events.click({target:board.children[36]});
  await flush();
  assert.equal(calls.at(-1).path,"/api/arena/human/move");
  assert.equal(calls.at(-1).request.uci,"e2e4");
  assert.equal($("human-turn-title").textContent,"Reproduzindo os lances");
  assert.equal($("human-moves").children.length,0);
  assert.equal(board.dataset.lastFen,START,"must not skip the human move");
  assert(intervals.some(x=>x.delay===100));
  now+=650;intervals.find(x=>x.delay===100).fn();
  assert.equal(board.dataset.lastFen,E4);
  assert.equal($("human-moves").children.length,1);
  assert.equal($("human-moves").children[0].children[1].textContent,"e4");
  now+=650;intervals.find(x=>x.delay===100).fn();
  assert.equal(board.dataset.lastFen,E5);
  assert.equal($("human-moves").children[0].children[2].textContent,"e5");
  assert.equal($("human-turn-title").textContent,"Sua vez");
  await $("human-resign").events.click();
  await flush();
  assert.equal($("human-turn-title").textContent,"Partida concluída");
  $("arena-mode-duel").events.click();
  assert.equal($("duel-viewer").hidden,false);
  assert.equal($("human-viewer").hidden,true);
  assert(!nodes.has("human-clear"),"No reset button in approved design");
  console.log("Human play browser smoke: PASS");
})().catch(err=>{console.error(err);process.exitCode=1;});
