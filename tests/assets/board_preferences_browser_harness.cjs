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
  focus(){this.focused=true;}
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
const ALL_BOARD_IDS=["board","human-board","cup-replay-board",
  "history-replay-board","archive-replay-board","my-games-board"];
const names=[...ALL_BOARD_IDS,"board-prefs","board-theme-gallery",
             "board-piece-gallery","board-prefs-summary","board-prefs-popup",
             "board-pref-toggle-theme","board-pref-toggle-pieces",
             "board-pref-toggle-motion","board-pref-panel-theme",
             "board-pref-panel-pieces","board-pref-panel-motion",
             "board-pref-theme",
             "board-pref-pieces","board-pref-motion",
             "board-pref-reset","board-pref-status"];
const shared=new Map();
function browser(options={}) {
  const elements=new Map(names.map(id=>[id,new Node("div",id)]));
  const listeners={};
  for(const theme of ["classic","wood","slate","light","dark","contrast",
                      "green","blue","walnut","marble","lavender","terracotta"]){
    const button=new Node("button");button.setAttribute("data-theme",theme);
    elements.get("board-theme-gallery").appendChild(button);
  }
  for(const set of ["cburnett","merida","chessnut","fantasy","spatial","celtic","rhosgfx"]){
    const button=new Node("button");button.setAttribute("data-piece-set",set);
    elements.get("board-piece-gallery").appendChild(button);
  }
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
    localStorage,parent:options.embedded?{}:null,
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
  assert.equal(hist.getAttribute("data-piece-style"),"spatial");
  for (const id of ALL_BOARD_IDS) {
    const element=app.$(id);
    assert.equal(element.getAttribute("data-board-theme"),"wood",id+" initial theme");
    assert.equal(element.getAttribute("data-piece-style"),"spatial",id+" initial SVG");
    assert.equal(element.getAttribute("data-board-motion"),"fast",id+" initial motion");
  }
  // Full Arena: all six boards must render the same chosen 64-square theme,
  // even when their game UI first paints the board after the preferences load.
  for (const id of ALL_BOARD_IDS) {
    assert.equal(app.board.paint(app.$(id),{fen:INITIAL,last:null},0),true);
    assert.equal(squares(app.$(id)).length,64,id+" initial squares");
    const rook=squares(app.$(id))[0].children[0].children[0];
    assert.equal(rook.getAttribute("src"),"/piece-assets/spatial/bR.svg",
                 id+" should use the chosen SVG before first replay");
  }
  assert.equal(app.$("board-piece-gallery").children.length,7);
  assert.equal(app.$("board-theme-gallery").children.length,12);
  assert.equal(app.$("board-theme-gallery").children[1].getAttribute("aria-pressed"),"true");
  assert.equal(app.$("board-pref-motion").value,"fast");
  const sections=["theme","pieces","motion"];
  const visibleSections=()=>sections.filter(name=>!app.$("board-pref-panel-"+name).hidden);
  assert.deepEqual(visibleSections(),[],"all visual galleries start collapsed");
  for(const section of sections)assert.equal(
    app.$("board-pref-toggle-"+section).getAttribute("aria-expanded"),"false");
  app.$("board-pref-toggle-theme").events.click();
  assert.deepEqual(visibleSections(),["theme"]);
  assert.equal(app.$("board-pref-toggle-theme").getAttribute("aria-expanded"),"true");
  app.$("board-pref-toggle-pieces").events.click();
  assert.deepEqual(visibleSections(),["pieces"],"switching tabs hides previous gallery");
  assert.equal(app.$("board-pref-toggle-theme").getAttribute("aria-expanded"),"false");
  app.$("board-pref-toggle-motion").events.click();
  assert.deepEqual(visibleSections(),["motion"]);
  app.$("board-pref-toggle-motion").events.click();
  assert.deepEqual(visibleSections(),[],"clicking active choice collapses it");
  app.$("board-pref-toggle-theme").events.click();
  const escape={key:"Escape",preventDefault(){this.prevented=true}};
  app.$("board-prefs-popup").events.keydown(escape);
  assert.equal(app.$("board-prefs").open,false);
  assert.equal(app.$("board-prefs-summary").focused,true);
  assert.equal(escape.prevented,true);
  assert.equal(app.board.paint(board,{fen:INITIAL,last:null},0),true);
  assert.equal(squares(board).length,64);
  const initialSVGs=new Set(squares(board)
    .filter(square=>square.children.length>0)
    .map(square=>square.children[0].children[0].getAttribute("src")));
  assert.equal(initialSVGs.size,12,"both colors and all six piece types need SVG");
  for(const side of ["w","b"])for(const role of ["K","Q","R","B","N","P"]){
    assert(initialSVGs.has("/piece-assets/spatial/"+side+role+".svg"),
           "missing artwork for "+side+role);
  }
  const firstRook=squares(board)[0].children[0];
  const firstImage=firstRook.children[0];
  assert.equal(firstImage.getAttribute("src"),"/piece-assets/spatial/bR.svg");
  assert.equal(firstRook.getAttribute("data-art-ready"),"0");
  firstImage.events.load();
  assert.equal(firstRook.getAttribute("data-art-ready"),"1");
  const savedNodes=board.children;
  const boardNodesBeforeTheme=new Map(ALL_BOARD_IDS.map(
    id=>[id,app.$(id).children]));
  assert.equal(app.prefs.set("theme","contrast"),true);
  assert.equal(board.getAttribute("data-board-theme"),"contrast");
  assert.strictEqual(board.children,savedNodes,"pure style change must not repaint board");
  assert.equal(board.getAttribute("aria-label"),"Tabuleiro após 0 meios-lances");
  assert.equal(hist.getAttribute("data-board-theme"),"contrast");
  for (const id of ALL_BOARD_IDS) {
    const element=app.$(id);
    assert.equal(element.getAttribute("data-board-theme"),"contrast",id);
    assert.strictEqual(element.children,boardNodesBeforeTheme.get(id),
      id+" must preserve its 64 squares after a palette change");
  }
  assert.equal(app.board.paint(board,{fen:E4,last:["e2","e4"]},1),true);
  assert.equal(flashes(board).length,2,"new move gets 2 highlight squares");
  assert.strictEqual(squares(board)[0].children[0],firstRook,
                     "unchanged rook artwork must survive another PGN position");
  assert.equal(app.board.paint(board,{fen:E4,last:["e2","e4"]},1),true);
  assert.equal(flashes(board).length,0,"repeated polling does not animate");
  assert.equal(squares(board).length,64);
  assert.equal(app.prefs.set("motion","normal"),true);
  assert.equal(board.getAttribute("data-board-motion"),"normal");
  const currentRook=squares(board)[0].children[0];
  const currentImage=currentRook.children[0];
  assert.equal(app.prefs.set("pieces","chessnut"),true);
  assert.equal(hist.getAttribute("data-piece-style"),"chessnut");
  for (const id of ALL_BOARD_IDS) {
    const element=app.$(id);
    assert.equal(element.getAttribute("data-piece-style"),"chessnut",id);
    const img=squares(element)[0].children[0].children[0];
    assert.equal(img.getAttribute("src"),"/piece-assets/chessnut/bR.svg",id);
  }
  assert.equal(currentImage.getAttribute("src"),"/piece-assets/chessnut/bR.svg");
  assert.equal(currentRook.getAttribute("data-art-ready"),"0");
  currentImage.events.load();
  assert.equal(currentRook.getAttribute("data-art-ready"),"1");
  currentImage.events.error();
  assert.equal(currentRook.getAttribute("data-art-ready"),"0",
               "failed SVG must reveal Unicode fallback");
  assert.equal(app.prefs.set("pieces","outline"),false);
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
  const frame=browser({embedded:true});
  assert.equal(frame.$("board-prefs").hidden,true,
               "embedded viewer must not show duplicated settings");
  assert.equal(frame.$("board").getAttribute("data-board-theme"),"contrast");
  app.$("board-pref-theme").value="slate";
  app.$("board-pref-theme").events.change();
  frame.listeners.storage({key:KEY,newValue:shared.get(KEY)});
  assert.equal(frame.$("board").getAttribute("data-board-theme"),"slate");
  for (const id of ALL_BOARD_IDS) {
    assert.equal(frame.$(id).getAttribute("data-board-theme"),"slate",
                 "same-origin storage event: "+id);
  }
  const reload=browser();
  for (const id of ALL_BOARD_IDS) {
    assert.equal(reload.$(id).getAttribute("data-board-theme"),"slate",
                 "persisted after reload: "+id);
    assert.equal(reload.$(id).getAttribute("data-piece-style"),"chessnut",
                 "persisted piece style: "+id);
  }
  frame.listeners.storage({key:"unrelated-token",newValue:"evil"});
  assert.equal(frame.$("board").getAttribute("data-board-theme"),"slate");
  app.$("board-pref-reset").events.click();
  assert.equal(app.$("board").getAttribute("data-board-motion"),"off");
  assert.equal(app.$("board-pref-pieces").value,"cburnett");
  frame.listeners.storage({key:KEY,newValue:shared.get(KEY)});
  assert.equal(frame.$("board").getAttribute("data-board-theme"),"classic");
  for (const id of ALL_BOARD_IDS) {
    assert.equal(frame.$(id).getAttribute("data-board-theme"),"classic",
                 "reset across windows: "+id);
    assert.equal(frame.$(id).getAttribute("data-piece-style"),"cburnett",
                 "reset piece style: "+id);
  }
  assert.deepEqual(JSON.parse(shared.get(KEY)),
                   {theme:"classic",pieces:"cburnett",motion:"off"});
  // Corrupt frames cannot smuggle attributes or HTML into the board.
  frame.listeners.storage({key:KEY,newValue:JSON.stringify({
    theme:"<img src=x onerror=alert(1)>",
    pieces:"evil",motion:"fast",token:"secret",
  })});
  assert.equal(frame.$("board").getAttribute("data-board-theme"),"classic");
  assert.equal(frame.$("board").getAttribute("data-piece-style"),"cburnett");
  assert.equal(frame.$("board").getAttribute("data-board-motion"),"fast");
  assert.equal(frame.$("board").children.length,0);
  assert.equal(app.board.paint(board,{fen:"9/8/8/8/8/8/8/8 w - - 0 1",last:null},2),false);
  assert.equal(squares(board).length,0);
  assert.equal(app.board.paint(board,{fen:INITIAL,last:null},0),true);
  assert.equal(flashes(board).length,0);
}
shared.set(KEY,JSON.stringify({theme:"blue",pieces:"solid",motion:"off"}));
{
  const old=browser();
  assert.equal(old.$("board").getAttribute("data-piece-style"),"cburnett");
}
shared.set(KEY,"{broken json");
{
  const app=browser();
  assert.equal(app.prefs.get().theme,"classic");
  assert.equal(app.$("board").getAttribute("data-piece-style"),"cburnett");
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
  const selected=app.$("board-piece-gallery").children[2];
  selected.events.click();
  assert.equal(app.$("board").getAttribute("data-piece-style"),"chessnut");
  assert.equal(selected.getAttribute("aria-pressed"),"true");
  assert.equal(app.prefs.set("theme","wood"),true);
  assert.equal(app.$("board").getAttribute("data-board-theme"),"wood");
  assert(app.$("board-pref-status").textContent.includes("armazenamento indisponível"));
  assert.equal(app.board.paint(app.$("history-replay-board"),{
    fen:"8/8/8/8/8/8/8/8 w - - 0 1",last:null},0),true);
  assert.equal(squares(app.$("history-replay-board")).length,64);
}
console.log("Board preferences: persist/sync, strict allowlists, blocked storage, 64 squares, real-change highlights: PASS");
