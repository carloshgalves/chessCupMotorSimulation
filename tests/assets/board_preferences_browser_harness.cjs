"use strict";
const assert=require("node:assert/strict");
const vm=require("node:vm");
const fs=require("node:fs");
class Node {
  constructor(tag="div",id="") {
    this.tagName=tag.toUpperCase();this.id=id;this.events={};this.attributes={};
    this.children=[];this.dataset={};this.className="";this.value="";this._text="";
    this.classList={add:(...items)=>{
      const names=new Set(this.className.split(/\s+/).filter(Boolean));
      for(const value of items)names.add(value);
      this.className=[...names].join(" ");
    }};
  }
  setAttribute(k,v){this.attributes[k]=String(v);}
  getAttribute(k){return this.attributes[k]??null;}
  addEventListener(k,cb){this.events[k]=cb;}
  appendChild(element){this.children.push(element);return element;}
  replaceChildren(...items) {
    this.children=items.flatMap(x=>x.tagName==="FRAGMENT"?x.children:[x]);
    this._text="";
  }
  get textContent(){return this._text+this.children.map(x=>x.textContent).join("");}
  set textContent(t){this.children=[];this._text=String(t);}
}
const INITIAL="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const E4="rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1";
const source=fs.readFileSync(process.argv[2],"utf8");
const renderer=fs.readFileSync(process.argv[3],"utf8");
const KEY="chesssim.board-presentation.v1";
const names=["board","history-replay-board","board-pref-theme",
             "board-pref-pieces","board-pref-motion",
             "board-pref-reset","board-pref-status"];
const shared=new Map();
function browser(options={}) {
  const elements=new Map(names.map(id=>[id,new Node("div",id)]));
  const listeners={};
  const document={
    getElementById:id=>elements.get(id)||null,
    createElement:tag=>new Node(tag),
    createDocumentFragment:()=>new Node("fragment")
  };
  const localStorage={
    getItem:key=>{
      if(options.storageDenied)throw Error("denied");
      return shared.get(key)??null;
    },
    setItem:(key,value)=>{
      if(options.storageDenied)throw Error("denied");
      shared.set(key,value);
    }
  };
  const window={
    localStorage,
    addEventListener:(type,handler)=>{listeners[type]=handler;}
  };
  const context=vm.createContext({document,window,console,Math,Number,String,Set,Object,Array,JSON});
  vm.runInContext(renderer,context,{filename:"chess_board.js",timeout:3000});
  vm.runInContext(source,context,{filename:"board_preferences.js",timeout:3000});
  return {context,document,window,elements,listeners,
          $:id=>elements.get(id),prefs:window.ChessSimBoardPreferences,
          board:window.ChessSimBoard};
}
const squares=root=>root.children.filter(x=>x.className.split(/\s+/).includes("square"));
const flashes=root=>squares(root).filter(x=>x.className.split(/\s+/).includes("fresh-last"));
shared.set(KEY,JSON.stringify({theme:"wood",pieces:"outline",motion:"fast"}));
{
  const app=browser(),board=app.$("board"),hist=app.$("history-replay-board");
  assert.equal(board.getAttribute("data-board-theme"),"wood");
  assert.equal(hist.getAttribute("data-piece-style"),"outline");
  assert.equal(app.$("board-pref-motion").value,"fast");
  assert.equal(app.board.paint(board,{fen:INITIAL,last:null},0),true);
  assert.equal(squares(board).length,64);
  const savedNodes=board.children;
  assert.equal(app.prefs.set("theme","contrast"),true);
  assert.equal(board.getAttribute("data-board-theme"),"contrast");
  assert.strictEqual(board.children,savedNodes,"pure style change must not repaint board");
  assert.equal(board.getAttribute("aria-label"),"Tabuleiro após 0 meios-lances");
  assert.equal(hist.getAttribute("data-board-theme"),"contrast");
  assert.equal(app.board.paint(board,{fen:E4,last:["e2","e4"]},1),true);
  assert.equal(flashes(board).length,2,"new move gets 2 highlight squares");
  assert.equal(app.board.paint(board,{fen:E4,last:["e2","e4"]},1),true);
  assert.equal(flashes(board).length,0,"repeated polling does not animate");
  assert.equal(squares(board).length,64);
  assert.equal(app.prefs.set("motion","normal"),true);
  assert.equal(board.getAttribute("data-board-motion"),"normal");
  assert.equal(app.prefs.set("pieces","outline"),true);
  assert.equal(hist.getAttribute("data-piece-style"),"outline");
  assert.equal(app.prefs.set("theme","url(javascript:alert(1))"),false);
  assert.equal(board.getAttribute("data-board-theme"),"contrast");
  assert.equal(app.$("board-pref-theme").value,"contrast");
  assert.equal(app.prefs.set("constructor","evil"),false);
  assert.equal(board.getAttribute("data-board-theme"),"contrast");
  const saved=JSON.parse(shared.get(KEY));
  assert.equal(saved.theme,"contrast");
  assert.equal(saved.motion,"normal");
  assert.equal(Object.keys(saved).length,3);
  // A second same-origin frame (the Arena viewer) receives the storage event.
  const frame=browser();
  assert.equal(frame.$("board").getAttribute("data-board-theme"),"contrast");
  app.$("board-pref-theme").value="slate";
  app.$("board-pref-theme").events.change();
  frame.listeners.storage({key:KEY,newValue:shared.get(KEY)});
  assert.equal(frame.$("board").getAttribute("data-board-theme"),"slate");
  frame.listeners.storage({key:"unrelated-token",newValue:"evil"});
  assert.equal(frame.$("board").getAttribute("data-board-theme"),"slate");
  app.$("board-pref-reset").events.click();
  assert.equal(app.$("board").getAttribute("data-board-motion"),"off");
  assert.equal(app.$("board-pref-pieces").value,"solid");
  frame.listeners.storage({key:KEY,newValue:shared.get(KEY)});
  assert.equal(frame.$("board").getAttribute("data-board-theme"),"classic");
  assert.deepEqual(JSON.parse(shared.get(KEY)),
                   {theme:"classic",pieces:"solid",motion:"off"});
  // Corrupt frames cannot smuggle attributes or HTML into the board.
  frame.listeners.storage({key:KEY,newValue:JSON.stringify({
    theme:"<img src=x onerror=alert(1)>",
    pieces:"evil",motion:"fast",token:"secret",
  })});
  assert.equal(frame.$("board").getAttribute("data-board-theme"),"classic");
  assert.equal(frame.$("board").getAttribute("data-piece-style"),"solid");
  assert.equal(frame.$("board").getAttribute("data-board-motion"),"fast");
  assert.equal(frame.$("board").children.length,0);
  assert.equal(app.board.paint(board,{fen:"9/8/8/8/8/8/8/8 w - - 0 1",last:null},2),false);
  assert.equal(squares(board).length,0);
  assert.equal(app.board.paint(board,{fen:INITIAL,last:null},0),true);
  assert.equal(flashes(board).length,0);
}
shared.set(KEY,"{broken json");
{
  const app=browser();
  assert.equal(app.prefs.get().theme,"classic");
  assert.equal(app.$("board").getAttribute("data-piece-style"),"solid");
}
shared.set(KEY,"x".repeat(5000));
{
  const app=browser();
  assert.equal(app.prefs.get().theme,"classic");
}
shared.clear();
{
  const app=browser({storageDenied:true});
  assert.equal(app.prefs.get().motion,"off");
  assert.equal(app.prefs.set("theme","wood"),true);
  assert.equal(app.$("board").getAttribute("data-board-theme"),"wood");
  assert(app.$("board-pref-status").textContent.includes("armazenamento indisponível"));
  assert.equal(app.board.paint(app.$("history-replay-board"),{
    fen:"8/8/8/8/8/8/8/8 w - - 0 1",last:null},0),true);
  assert.equal(squares(app.$("history-replay-board")).length,64);
}
console.log("Board preferences: persist/sync, strict allowlists, blocked storage, 64 squares, real-change highlights: PASS");
