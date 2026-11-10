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
  add(name){this.toggle(name,true);}
  remove(name){this.toggle(name,false);}
}
class Node {
  constructor(tag="div",id=""){
    this.tagName=tag.toUpperCase();this.id=id;this.children=[];
    this.events={};this.attributes={};this.dataset={};
    this.className="";this.classList=new ClassList(this);
    this.textContent="";this.value="";this.hidden=false;this.disabled=false;
    this.scrollHeight=0;this.scrollTop=0;this.clientHeight=280;
    this.parentNode=null;this.style={};this.captured=null;
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
  getBoundingClientRect(){
    return {left:0,top:0,right:640,bottom:640,width:640,height:640};
  }
  setPointerCapture(id){this.captured=id;}
  hasPointerCapture(id){return this.captured===id;}
  releasePointerCapture(id){if(this.captured===id)this.captured=null;}
  remove(){
    if(this.parentNode)
      this.parentNode.children=this.parentNode.children.filter(x=>x!==this);
    this.parentNode=null;
  }
  cloneNode(deep=false){
    const clone=new Node(this.tagName,this.id);
    clone.className=this.className;clone.textContent=this.textContent;
    clone.attributes={...this.attributes};
    if(deep)for(const child of this.children)clone.appendChild(child.cloneNode(true));
    return clone;
  }
  addEventListener(type,fn){this.events[type]=fn;}
  setAttribute(name,value){this.attributes[name]=String(value);}
  getAttribute(name){return this.attributes[name]??null;}
  querySelectorAll(selector){
    if(selector==="button")return this.children.filter(x=>x.tagName==="BUTTON");
    if(selector==="[data-promote]")return this.children.filter(x=>x.dataset.promote);
    return [];
  }
  querySelector(selector){
    if(selector===".piece-glyph")
      return this.children.find(x=>x.classList.contains("piece-glyph"))||null;
    return this.querySelectorAll(selector)[0]||null;
  }
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
  createElement(tag){return new Node(tag);},
  body:new Node("body"),
  addEventListener(){}
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
      if(game.id==="human-black"){
        assert.equal(request.revision,1);
        assert.equal(request.uci,"e7e5");
        game.revision=2;game.human_turn=false;game.turn="white";
        game.legal_moves=[];
        return reply({id:game.id,revision:2,status:"RUNNING"});
      }
      if(game.revision===2) {
        assert.equal(request.revision,2);
        assert.equal(request.uci,"g1f3");
        game.revision=3;game.human_turn=false;game.turn="black";
        game.last=["g1","f3"];game.legal_moves=[];
        game.moves.push({ply:3,san:"Nf3",uci:"g1f3"});
        game.states.push({fen:
          "rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq - 1 2",
          last:["g1","f3"],turn:"black",clocks:[179,179]});
        return reply({id:game.id,revision:3,status:"RUNNING"});
      }
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
      // Fake a rendered glyph on the squares used in pointer drag tests.
      if([52,62,12].includes(i)){
        const piece=new Node("span");piece.className="piece-glyph";
        sq.appendChild(piece);
      }
      squares.push(sq);
    }
    element.replaceChildren(...squares);
  }
  return true;
};
const clock={now:()=>now};
const window={
  location:{hash:"#token="+token},confirm:()=>true,
  addEventListener(){},
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
  const pointer=(id,square,x,y,kind="mouse")=>({
    pointerId:id,pointerType:kind,button:0,isPrimary:true,target:square,
    clientX:x,clientY:y,preventDefault(){this.prevented=true;}
  });
  const unchanged=calls.length;
  // An impossible knight drop (g1 -> g8) must not hit the backend.
  let sq=board.children[62];
  board.events.pointerdown(pointer(5,sq,520,600));
  board.events.pointermove(pointer(5,sq,520,40));
  assert.equal(document.body.children.length,1,"pointer drag has visual preview");
  assert.equal(board.children[6].classList.contains("human-drop-hover"),false);
  board.events.pointerup(pointer(5,sq,520,40));
  await flush();
  assert.equal(document.body.children.length,0,"invalid drop removes preview");
  assert.equal(calls.length,unchanged,"illegal drop did not POST");
  board.events.click({detail:1,target:sq,preventDefault(){}});
  assert.equal(calls.length,unchanged,"synthetic click after drag is suppressed");

  // Outside the 8x8 board (e.g. g9) must cancel, without staging a pre-move.
  board.events.pointerdown(pointer(6,sq,520,600,"touch"));
  board.events.pointermove(pointer(6,sq,520,-40,"touch"));
  board.events.pointerup(pointer(6,sq,520,-40,"touch"));
  await flush();
  assert.equal(calls.length,unchanged);

  // OS/browser cancellation or loss of pointer capture also cancels.
  board.events.pointerdown(pointer(7,sq,520,600,"pen"));
  board.events.pointermove(pointer(7,sq,440,440,"pen"));
  board.events.pointercancel(pointer(7,sq,440,440,"pen"));
  await flush();
  assert.equal(calls.length,unchanged);
  assert.equal(document.body.children.length,0);

  // Legal drag is translated to exactly one UCI with the observed revision.
  board.events.pointerdown(pointer(8,sq,520,600));
  board.events.pointermove(pointer(8,sq,440,440));
  assert.equal(board.children[45].classList.contains("human-drop-hover"),true);
  board.events.pointerup(pointer(8,sq,440,440));
  await flush();
  assert.equal(calls.length,unchanged+1);
  assert.equal(calls.at(-1).path,"/api/arena/human/move");
  assert.equal(calls.at(-1).request.uci,"g1f3");
  assert.equal(calls.at(-1).request.revision,2);
  assert.equal(document.body.children.length,0);
  board.events.click({detail:1,target:board.children[45],preventDefault(){}});
  assert.equal(calls.length,unchanged+1,"compatibility click cannot double-submit");

  // While the engine thinks, no pointerdown can create a pre-move.
  board.events.pointerdown(pointer(9,board.children[51],280,520));
  board.events.pointermove(pointer(9,board.children[51],280,360));
  board.events.pointerup(pointer(9,board.children[51],280,360));
  await flush();
  assert.equal(calls.length,unchanged+1,"no pre-moves in increment 1");

  await $("human-resign").events.click();
  await flush();
  assert.equal($("human-turn-title").textContent,"Reproduzindo os lances");
  now+=650;intervals.find(x=>x.delay===100).fn();
  assert.equal($("human-turn-title").textContent,"Partida concluída");

  // Black-side orientation reverses DOM order; the coordinate mapping must
  // still send e7e5, never d2d4 or an out-of-bounds destination.
  game={
    id:"human-black",status:"RUNNING",revision:1,
    config:{color:"black",opponent:"fischer",depth:2,preset:"blitz"},
    white:"Bobby Fischer",black:"Você",
    fen:E4,turn:"black",human_turn:true,legal_moves:["e7e5"],last:["e2","e4"],
    result:"*",termination:null,clocks:[180,180],
    moves:[{ply:1,san:"e4",uci:"e2e4"}],
    states:[{fen:START,last:null,turn:"white",clocks:[180,180]},
            {fen:E4,last:["e2","e4"],turn:"black",clocks:[180,180]}],
    draw_offer:null
  };
  await intervals.find(x=>x.delay===1100).fn();
  await flush();
  assert.equal($("human-turn-title").textContent,"Sua vez");
  assert.equal(board.children.length,64);
  const blackSource=board.children[51]; // e7 when black looks from bottom
  board.events.pointerdown(pointer(10,blackSource,280,520,"touch"));
  board.events.pointermove(pointer(10,blackSource,280,360,"touch"));
  board.events.pointerup(pointer(10,blackSource,280,360,"touch"));
  await flush();
  assert.equal(calls.at(-1).request.uci,"e7e5");
  assert.equal(calls.at(-1).request.revision,1);
  assert.equal(calls.filter(x=>x.path==="/api/arena/human/move").length,3);

  $("arena-mode-duel").events.click();
  assert.equal($("duel-viewer").hidden,false);
  assert.equal($("human-viewer").hidden,true);
  assert(!nodes.has("human-clear"),"No reset button in approved design");
  console.log("Human play browser smoke: PASS");
})().catch(err=>{console.error(err);process.exitCode=1;});
