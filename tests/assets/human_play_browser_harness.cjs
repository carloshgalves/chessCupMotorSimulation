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
    // CSSStyleDeclaration returns "" for an unset inline property.
    this.parentNode=null;this.style={fontSize:""};this.captured=null;
  }
  append(...children){for(const c of children)this.appendChild(c);}
  appendChild(child){
    // DOM appendChild *moves* a node; it never makes a duplicate piece.
    if(child.parentNode)
      child.parentNode.children=child.parentNode.children.filter(c=>c!==child);
    child.parentNode=this;this.children.push(child);return child;
  }
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
  createElementNS(namespace,tag){
    assert.equal(namespace,"http://www.w3.org/2000/svg");
    return new Node(tag);
  },
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
      if(game.id==="human-promote"){
        assert.equal(request.revision,0);
        assert.equal(request.uci,"a7a8n","underpromotion is selected explicitly");
        game.revision=1;game.turn="black";game.human_turn=false;
        game.legal_moves=[];
        return reply({id:game.id,revision:1,status:"RUNNING"});
      }
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
      sq.className="square"+([8,52,62].includes(i)?" whitepiece":
                       i===12?" blackpiece":"");
      // Fake a rendered glyph on the squares used in pointer drag tests.
      if([8,12,52,62].includes(i)){
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
  getComputedStyle:()=>({fontSize:"44px"}),
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

  // #91: right-button gestures are annotations, not UCI moves.
  const overlay=$("human-annotations");
  const clearMarks=$("human-annotations-clear");
  const right=(id,square,x,y,mods={})=>({
    pointerId:id,pointerType:"mouse",button:2,buttons:2,isPrimary:true,
    target:square,clientX:x,clientY:y,
    ctrlKey:!!mods.ctrlKey,altKey:!!mods.altKey,shiftKey:!!mods.shiftKey,
    preventDefault(){this.prevented=true;}
  });
  const drawRightArrow=(id,fromIndex,fx,fy,toIndex,tx,ty,mods={})=>{
    const from=board.children[fromIndex],to=board.children[toIndex];
    board.events.pointerdown(right(id,from,fx,fy,mods));
    assert.equal(board.captured,id,"annotation captures the pointer");
    board.events.pointermove(right(id,to,tx,ty,mods));
    assert.equal(overlay.children.at(-1).getAttribute("fill-opacity"),".46",
                 "annotation is previewed during drag");
    board.events.pointerup(right(id,to,tx,ty,mods));
    assert.equal(board.captured,null,"capture released after annotation");
  };
  const rightMark=(id,index,x,y,mods={})=>{
    const cell=board.children[index],ev=right(id,cell,x,y,mods);
    board.events.pointerdown(ev);
    board.events.pointerup(ev);
  };
  const noUci=calls.length,initialRevision=game.revision;
  drawRightArrow(50,56,40,600,7,600,40);
  assert.equal(overlay.children.length,1);
  assert.equal(overlay.children[0].tagName,"PATH");
  assert.equal(overlay.children[0].getAttribute("data-arrow"),"a1-h8");
  assert.equal(overlay.children[0].getAttribute("data-color"),"yellow");
  assert.equal(overlay.children[0].getAttribute("fill"),"#f2c94c");
  assert.equal(board.children.length,64,"SVG is a sibling, not a 65th square");
  assert.equal(calls.length,noUci,"an illegal chess path is a legal visual arrow");
  drawRightArrow(51,56,40,600,15,600,120,{ctrlKey:true});
  drawRightArrow(52,56,40,600,23,600,200,{altKey:true});
  drawRightArrow(53,56,40,600,31,600,280,{shiftKey:true});
  assert.deepEqual(overlay.children.map(n=>n.getAttribute("data-color")),
                   ["yellow","red","blue","green"]);
  rightMark(54,62,520,600); // g1: red by default
  rightMark(55,61,440,600,{ctrlKey:true});
  rightMark(56,60,360,600,{altKey:true});
  rightMark(57,59,280,600,{shiftKey:true});
  assert.equal(overlay.children.length,8);
  assert.deepEqual(overlay.children.slice(0,4).map(n=>n.getAttribute("data-color")),
                   ["red","yellow","blue","green"]);
  assert.equal($("human-annotations-status").textContent,
               "4 seta(s) · 4 casa(s) marcada(s)");
  assert.equal(clearMarks.disabled,false);
  let contextPrevented=false;
  board.events.contextmenu({target:board.children[62],
    preventDefault(){contextPrevented=true;}});
  assert.equal(contextPrevented,true,"no browser menu inside annotated board");
  contextPrevented=false;
  board.events.contextmenu({target:board,
    preventDefault(){contextPrevented=true;}});
  assert.equal(contextPrevented,true,
    "pointer capture can retarget contextmenu to the board itself");
  drawRightArrow(58,56,40,600,7,600,40);
  assert.equal(overlay.children.length,7,"same arrow toggles off");
  rightMark(59,62,520,600);
  assert.equal(overlay.children.length,6,"same marked square toggles off");
  // A right-button drag that leaves the board never creates an arrow.
  const outsideBefore=overlay.children.length;
  const outsideSource=board.children[56];
  board.events.pointerdown(right(60,outsideSource,40,600));
  board.events.pointermove(right(60,outsideSource,-50,-50));
  board.events.pointerup(right(60,outsideSource,-50,-50));
  assert.equal(overlay.children.length,outsideBefore);
  // Interrupted right-click must not commit an annotation.
  board.events.pointerdown(right(65,outsideSource,40,600));
  board.events.pointermove(right(65,board.children[7],600,40));
  board.events.pointercancel(right(65,board.children[7],600,40));
  assert.equal(overlay.children.length,outsideBefore);
  board.events.pointerdown(right(66,outsideSource,40,600));
  board.events.pointermove(right(66,board.children[7],600,40));
  board.events.lostpointercapture(right(66,board.children[7],600,40));
  assert.equal(overlay.children.length,outsideBefore);
  assert.equal(board.captured,null);
  clearMarks.events.click();
  assert.equal(overlay.children.length,0);
  assert.equal(clearMarks.disabled,true);
  assert.equal($("human-annotations-status").textContent,"Nenhuma marcação");
  assert.equal(game.revision,initialRevision);
  assert.equal(calls.length,noUci);

  rightMark(61,62,520,600);
  board.events.keydown({key:"Escape",target:board.children[62],
                        preventDefault(){}});
  assert.equal(overlay.children.length,0,"Escape clears arrows and marks");
  rightMark(62,62,520,600);
  board.events.pointerdown({pointerId:63,pointerType:"mouse",button:0,
    target:board.children[56],isPrimary:true,clientX:40,clientY:600});
  assert.equal(overlay.children.length,0,"left click clears annotations");
  assert.equal(calls.length,noUci);

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
  const originalPiece=sq.children[0];
  board.events.pointerdown(pointer(5,sq,520,600));
  board.events.pointermove(pointer(5,sq,520,40));
  assert.equal(document.body.children.length,1,"pointer drag has visual preview");
  const floating=document.body.children[0];
  const previewGlyph=floating.children[0].children[0];
  assert.strictEqual(previewGlyph,originalPiece,
    "must relocate original loaded artwork instead of cloning a white tile");
  assert.equal(sq.children.length,0,
    "source square must become empty (never show faded duplicate)");
  assert.equal(floating.style.left,"480px",
    "drag position must preserve original 40px horizontal grab offset");
  assert.equal(floating.style.top,"0px",
    "drag position must preserve original 40px vertical grab offset");
  assert.equal(previewGlyph.style.fontSize,"44px",
    "fallback glyph preserves computed size of original piece");
  assert.equal(board.children[6].classList.contains("human-drop-hover"),false);
  board.events.pointerup(pointer(5,sq,520,40));
  await flush();
  assert.equal(document.body.children.length,0,"invalid drop removes preview");
  assert.strictEqual(sq.children[0],originalPiece,
    "invalid drop returns identical loaded piece to its origin");
  assert.equal(sq.children[0].style.fontSize,"",
    "an inline font override must not leak into the original board");
  assert.equal(calls.length,unchanged,"illegal drop did not POST");
  board.events.click({detail:1,target:sq,preventDefault(){}});
  assert.equal(calls.length,unchanged,"synthetic click after drag is suppressed");
  assert.equal(sq.classList.contains("human-selected"),false);
  board.events.click({detail:1,target:board.children[51]});
  assert.equal(board.children[51].classList.contains("human-selected"),true,
               "the next genuine mouse click must not remain suppressed");

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
  assert.strictEqual(sq.children[0],originalPiece,"cancel returns original piece");

  // Legal drag is translated to exactly one UCI with the observed revision.
  board.events.pointerdown(pointer(8,sq,520,600));
  board.events.pointermove(pointer(8,sq,440,440));
  assert.strictEqual(document.body.children[0].children[0].children[0],
    originalPiece,"legal drag moves actual piece, not a square");
  assert.equal(board.children[45].classList.contains("human-drop-hover"),true);
  // The Arena polls live state every 1.1s. Polling can overwrite square
  // classes, but it must never make the held piece reappear or lose hints.
  sq.classList.remove("human-drag-origin");
  board.children[45].classList.remove("human-drop-hover");
  await intervals.find(x=>x.delay===1100).fn();
  await flush();
  assert.equal(sq.classList.contains("human-drag-origin"),true,
               "live poll must restore empty-source drag highlight");
  assert.equal(board.children[45].classList.contains("human-drop-hover"),true,
               "live poll must restore legal destination highlight");
  assert.equal(sq.children.length,0,"live poll must not duplicate held piece");
  board.events.pointerup(pointer(8,sq,440,440));
  assert.strictEqual(sq.children[0],originalPiece,
    "release restores the DOM before the backend validates the move");
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
  const beforeThinkingAnnotation=calls.length;
  rightMark(67,board.children[51]===undefined?52:51,280,520);
  assert.equal(overlay.children.length,1,
    "right-click analysis still works while the engine thinks");
  assert.equal(calls.length,beforeThinkingAnnotation,
    "visual-only annotations never become a pre-move or POST");

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
  assert.equal(overlay.children.length,0,
    "new session clears previous annotation even if no move is submitted");
  // Flipped orientation must draw visually from e7 to e5, not from
  // the canonical white-view squares. Right-button arrows need no legal move.
  drawRightArrow(64,51,280,520,35,280,360);
  assert.equal(overlay.children[0].getAttribute("data-arrow"),"e7-e5");
  assert.match(overlay.children[0].getAttribute("d"),/^M360\.00,650\.00 /);
  assert.equal(calls.filter(x=>x.path==="/api/arena/human/move").length,2);
  // This overlay is local and not part of the board's 64 square children.
  assert.equal(board.children.length,64);
  const blackSource=board.children[51]; // e7 when black looks from bottom
  // Real SVGs are already loaded; moving the node must not trigger a
  // second image request, change it into a Unicode glyph or resize it.
  const loaded=blackSource.children[0];
  const svg=new Node("img");svg.className="piece-svg";
  loaded.appendChild(svg);loaded.dataset.artReady="1";
  board.setAttribute("data-piece-kind","svg");
  board.setAttribute("data-piece-style","chessnut");
  board.events.pointerdown(pointer(10,blackSource,280,520,"touch"));
  board.events.pointermove(pointer(10,blackSource,280,360,"touch"));
  assert.strictEqual(document.body.children[0].children[0].children[0],loaded);
  assert.strictEqual(loaded.children[0],svg,"already-loaded SVG is reused");
  assert.equal(loaded.style.fontSize,"",
               "loaded SVG must keep CSS font-size:0, no glyph overlay");
  board.events.pointerup(pointer(10,blackSource,280,360,"touch"));
  assert.strictEqual(blackSource.children[0],loaded);
  assert.strictEqual(loaded.children[0],svg);
  await flush();
  assert.equal(calls.at(-1).request.uci,"e7e5");
  assert.equal(calls.at(-1).request.revision,1);
  assert.equal(calls.filter(x=>x.path==="/api/arena/human/move").length,3);

  // A promoted pawn has four legal UCI candidates. Drag only opens the
  // existing selector; no automatic queen or move before explicit choice.
  const PROMO="4k3/P7/8/8/8/8/8/4K3 w - - 0 1";
  game={
    id:"human-promote",status:"RUNNING",revision:0,
    config:{color:"white",opponent:"fischer",depth:2,preset:"blitz"},
    white:"Você",black:"Bobby Fischer",fen:PROMO,turn:"white",
    human_turn:true,legal_moves:["a7a8q","a7a8r","a7a8b","a7a8n"],
    last:null,result:"*",termination:null,clocks:[180,180],moves:[],
    states:[{fen:PROMO,last:null,turn:"white",clocks:[180,180]}],
    draw_offer:null
  };
  await intervals.find(x=>x.delay===1100).fn();
  await flush();
  const promotionSource=board.children[8]; // a7
  const beforePromotion=calls.length;
  board.events.pointerdown(pointer(11,promotionSource,40,120));
  board.events.pointermove(pointer(11,promotionSource,40,40));
  board.events.pointerup(pointer(11,promotionSource,40,40));
  await flush();
  assert.equal($("human-promotion").hidden,false);
  assert.equal(calls.length,beforePromotion,"must not auto-queen");
  await $("human-promotion").children.find(x=>x.dataset.promote==="n").events.click();
  await flush();
  assert.equal($("human-promotion").hidden,true);
  assert.equal(calls.at(-1).request.uci,"a7a8n");
  assert.equal(calls.length,beforePromotion+1);

  $("arena-mode-duel").events.click();
  assert.equal(overlay.children.length,0,"switching modes clears analysis");
  assert.equal($("duel-viewer").hidden,false);
  assert.equal($("human-viewer").hidden,true);
  assert(!nodes.has("human-clear"),"No reset button in approved design");
  console.log("Human play browser smoke: PASS");
})().catch(err=>{console.error(err);process.exitCode=1;});
