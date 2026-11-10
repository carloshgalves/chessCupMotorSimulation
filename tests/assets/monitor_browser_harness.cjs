"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const {URL, URLSearchParams} = require("node:url");

const source = fs.readFileSync(process.argv[2], "utf8");
const FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const E4 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1";
const tournament = {
  status:"RUNNING", champion:null, names:{
    fischer:"Bobby Fischer",tal:"Mikhail Tal",lasker:"Emanuel Lasker",marshall:"Frank Marshall"
  }, matches_completed:1, matches_total_generated:2,stop_requested:false,
  bracket:[{round:1,matches:[
    {id:"r01-m01",players:["fischer","tal"],score:{fischer:2,tal:0},
     status:"COMPLETED",winner:"fischer",pairs_played:1},
    {id:"r01-m02",players:["lasker","marshall"],score:{lasker:0.5,marshall:0.5},
     status:"RUNNING",winner:null,pairs_played:0}
  ]}]
};
const states=[
  {fen:FEN,san:"Início",last:null,turn:"white",clocks:[600,600],uci:null},
  {fen:E4,san:"e4",last:["e2","e4"],turn:"black",clocks:[598,600],
    uci:"e2e4",legal_moves:["e7e5","c7c5"]}
];
const game=(id,white,black)=>({
  id:id+":game-1",number:1,white,black,result:"*",status:"RUNNING",
  live:true,updated_at:Date.now()/1000,states
});
const makeResponse=(match,attempt)=>{
  const id=match||"r01-m02";
  const historical=id==="r01-m01";
  const folder=historical?(attempt===1?"old-1":"old-2"):"current";
  const chosen=historical?game(folder,"Bobby Fischer","Mikhail Tal"):
    game(folder,"Emanuel Lasker","Frank Marshall");
  return {schema:"chesssim-live-board-v1",mode:"tournament",tournament,
    duel:{folder,match_id:id,status:historical?"COMPLETED":"RUNNING",games:[chosen]},
    selection:{match_id:id,attempt:attempt|| (historical?2:1)},
    attempts:historical?
      [{index:1,status:"COMPLETED",updated_at:1000},
       {index:2,status:"COMPLETED",updated_at:2000}]:
      [{index:1,status:"RUNNING",updated_at:3000}],
    server_time:"2026-11-05T12:00:00Z"};
};

class FakeElement {
  constructor(tag){
    this.tagName=tag.toUpperCase();
    this.children=[];this.attributes={};this.events={};
    this.dataset={};this.value="";this.textContent="";
    this.className="";this.scrollTop=0;this.style={};
    this.classList={
      contains:name=>this.className.split(/\s+/).includes(name),
      add:(...names)=>{
        const current=new Set(this.className.split(/\s+/).filter(Boolean));
        for(const name of names) current.add(name);
        this.className=[...current].join(" ");
      },
      toggle:(name,force)=>{
        const current=new Set(this.className.split(/\s+/).filter(Boolean));
        const enabled=force===undefined?!current.has(name):!!force;
        if(enabled) current.add(name);
        else current.delete(name);
        this.className=[...current].join(" ");
        return enabled;
      }
    };
  }
  append(...items){this.children.push(...items);}
  appendChild(item){this.children.push(item);return item;}
  replaceChildren(...items){this.children=items;}
  setAttribute(key,value){this.attributes[key]=value;}
  addEventListener(name,listener){this.events[name]=listener;}
  querySelectorAll(selector){return selector==="button"?
    this.children.filter(x=>x.tagName==="BUTTON"):[];}
  querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
  cloneNode(deep=false){
    const node=new FakeElement(this.tagName);
    node.className=this.className;node.textContent=this.textContent;
    node.dataset={...this.dataset};node.attributes={...this.attributes};
    if(deep)node.children=this.children.map(child=>child.cloneNode(true));
    return node;
  }
}
function descendants(node) {
  return [node,...node.children.flatMap(descendants)];
}
function browser(initialHash="") {
  const elements=new Map();
  const events={};
  const requests=[];
  let hash=initialHash;
  const location={
    get hash(){return hash;},
    set hash(value){
      hash=value;
      if(events.hashchange) events.hashchange();
    }
  };
  const document={
    getElementById(id){
      if(!elements.has(id)) elements.set(id,new FakeElement("div"));
      return elements.get(id);
    },
    createElement(tag){return new FakeElement(tag);},
    createDocumentFragment(){return new FakeElement("fragment");},
    addEventListener(name,listener){events[name]=listener;}
  };
  const fetch=async (uri)=>{
    requests.push(uri);
    const params=new URL("http://127.0.0.1:8765"+uri).searchParams;
    const match=params.get("match");
    const raw=params.get("attempt");
    const attempt=raw?Number(raw):null;
    return {ok:true,json:async()=>makeResponse(match,attempt)};
  };
  const context=vm.createContext({
    document,window:{location,addEventListener(name,listener){events[name]=listener;}},
    HTMLElement:FakeElement,URLSearchParams,fetch,Date,Math,Number,String,
    setInterval(){return 1;},console
  });
  const shared=fs.readFileSync(process.argv[3],"utf8");
  vm.runInContext(shared,context,{filename:"chess_board.js",timeout:3000});
  vm.runInContext(source,context,{filename:"monitor.js",timeout:3000});
  return {elements,requests,location,events,context};
}
const flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
  const app=browser();
  await flush();
  assert.equal(app.requests[0],"/api/state");
  const panel=app.elements.get("bracket-panel");
  assert.equal(panel.hidden,false);
  const buttons=descendants(app.elements.get("bracket")).filter(n=>n.tagName==="BUTTON");
  assert.equal(buttons.length,2,"both knockout matches rendered");
  assert(buttons[0].attributes["aria-label"].includes("Bobby Fischer"));
  assert.equal(app.elements.get("match-title").textContent,
               "Emanuel Lasker × Frank Marshall");
  assert(buttons[0].children.some(n=>n.tagName==="DIV"));

  buttons[0].events.click();
  await flush();
  assert.equal(app.location.hash,"#match=r01-m01");
  assert(app.requests.includes("/api/state?match=r01-m01"));
  assert.equal(app.elements.get("match-title").textContent,
               "Bobby Fischer × Mikhail Tal");
  const attempts=app.elements.get("attempts");
  assert.equal(attempts.children.length,3,"latest plus two saved attempts");
  attempts.value="1";
  attempts.events.change();
  await flush();
  assert.equal(app.location.hash,"#match=r01-m01&attempt=1");
  assert(app.requests.includes("/api/state?match=r01-m01&attempt=1"));

  const replay=browser(app.location.hash);
  await flush();
  assert(replay.requests.includes("/api/state?match=r01-m01&attempt=1"),
         "historical replay survives reload");
  assert.equal(replay.elements.get("match-title").textContent,
               "Bobby Fischer × Mikhail Tal");

  const oldOption=app.elements.get("attempts").children[1];
  await vm.runInContext("poll()",app.context);
  assert.equal(app.elements.get("attempts").children[1],oldOption,
               "attempt dropdown must stay stable during refresh");

  const pause=app.elements.get("pause");
  pause.events.click();
  assert.equal(pause.attributes["aria-pressed"],"true");
  pause.events.click();
  assert.equal(pause.attributes["aria-pressed"],"false");
  app.elements.get("live").events.click();
  await flush();
  assert.equal(app.location.hash,"");
  assert.equal(app.elements.get("match-title").textContent,
               "Emanuel Lasker × Frank Marshall");
  assert.equal(app.requests.at(-1),"/api/state");
  // Layout regression: empty ranks remain 8 full rows, not implicit auto tracks.
  const paint=fen=>vm.runInContext(
    "paintBoard({fen:" + JSON.stringify(fen) +
    ",last:null,turn:'white',clocks:[null,null]})",app.context
  );
  const squares=()=>descendants(app.elements.get("board"))
    .filter(n=>n.tagName==="DIV" && n.className.split(/\s+/).includes("square"));
  paint(FEN);
  assert.equal(squares().length,64);
  assert.equal(squares().filter(n=>n.children.length===1).length,32);
  assert(squares()[0].className.includes("blackpiece"));
  assert(squares()[56].className.includes("whitepiece"));
  assert.equal(squares()[0].children[0].textContent,"♜");
  assert.equal(squares()[56].children[0].textContent,"♜",
               "both colors use the same silhouette and CSS contrasts the sides");
  assert.equal(squares()[0].children[0].className,"piece-glyph");
  assert.equal(squares()[0].children[0].attributes["aria-hidden"],"true");
  assert.equal(squares()[0].attributes["aria-label"],"a8 torre preta");
  paint("8/8/8/8/8/8/8/4K2k w - - 0 1");
  assert.equal(squares().length,64);
  for(let rank=0;rank<7;rank++){
    assert(squares().slice(rank*8,(rank+1)*8).every(n=>n.children.length===0),
           "an empty rank still contains exactly eight empty square nodes");
  }
  assert.equal(squares().filter(n=>n.children.length===0).length,62);
  assert.equal(squares()[60].attributes["aria-label"],"e1 rei branco");
  assert.equal(squares()[63].attributes["aria-label"],"h1 rei preto");
  paint("8/8/8/8/8/8/8/8 w - - 0 1");
  assert.equal(squares().length,64);
  assert(squares().every(n=>n.children.length===0),
         "even a completely empty display retains 64 squares");
  // A long game renders every numbered row, with one white/black pair each.
  const long=browser();
  await flush();
  const allStates=Array.from({length:77},(_,ply)=>({
    fen:ply%2?E4:FEN,
    san:ply===0?"Início":(ply%2?"Nf3":"Nc6"),
    last:ply?["g1","f3"]:null,turn:ply%2?"black":"white",clocks:[60,60]
  }));
  vm.runInContext("feed.duel.games[0].states=" + JSON.stringify(allStates) +
                  "; cursor=76; render()",long.context);
  const log=long.elements.get("moves");
  assert.equal(log.children.length,38,"never truncate to the last 20 half-moves");
  assert.equal(log.children[0].children[0].textContent,"1. Nf3");
  assert.equal(log.children[0].children[1].textContent,"1... Nc6");
  assert.equal(log.children[1].children[0].textContent,"2. Nf3");
  assert.equal(log.children[1].children[1].textContent,"2... Nc6");
  assert.equal(log.children.at(-1).children[1].textContent,"38... Nc6");
  const originalMoveRow=log.children[0];
  vm.runInContext("render()",long.context);
  assert.strictEqual(log.children[0],originalMoveRow,
                     "identical polls must preserve the move list DOM");
  assert.equal(log.children.length,38);
  log.scrollHeight=900;
  log.clientHeight=210;
  log.scrollTop=0;
  log.events.scroll();
  vm.runInContext("feed.duel.games[0].states.push(" +
                  JSON.stringify({fen:E4,san:"h4",last:["h2","h4"],
                                  turn:"black",clocks:[60,60]}) +
                  "); cursor=77; render()",long.context);
  assert.equal(log.scrollTop,0,"manual scroll must not jump back to newest move");
  assert.equal(log.children.length,39);
  long.elements.get("live").events.click();
  assert.equal(log.scrollTop,900,"return to live resumes automatic following");
  // Board nodes are stable across identical positions/PGN polls.
  const board=long.elements.get("board");
  const before=descendants(board).filter(n=>n.className.includes("square"));
  vm.runInContext("render()",long.context);
  const after=descendants(board).filter(n=>n.className.includes("square"));
  assert.strictEqual(after[0],before[0],"no full board replacement on same FEN");
  assert.strictEqual(after[63],before[63]);
  // A single backend snapshot may contain many fast engine moves. Every ply
  // must be shown in order, with a presentation-only minimum interval.
  const paced=browser();
  await flush();
  const initialPly=Number(vm.runInContext("cursor",paced.context));
  assert.equal(initialPly,1);
  const burst=[
    {fen:FEN,san:"e5",last:["e7","e5"],turn:"white",clocks:[598,598]},
    {fen:E4,san:"Nf3",last:["g1","f3"],turn:"black",clocks:[597,598]},
    {fen:FEN,san:"Nc6",last:["b8","c6"],turn:"white",clocks:[597,597]}
  ];
  vm.runInContext("feed.duel.games[0].states.push(..."+JSON.stringify(burst)+")",paced.context);
  const ply=()=>Number(vm.runInContext("cursor",paced.context));
  const advance=delay=>vm.runInContext(
    "advanceLivePlayback(lastPresentedAt + "+delay+")",paced.context);
  advance(649);
  assert.equal(ply(),1,"no premove before display interval");
  advance(650);
  assert.equal(ply(),2,"only one half-move per presentation interval");
  assert.equal(paced.elements.get("headline").textContent,"1... e5");
  assert.equal(paced.elements.get("moves").children.length,1);
  assert.equal(paced.elements.get("moves").children[0].children[1].textContent,
               "1... e5");
  const savedRow=paced.elements.get("moves").children[0];
  const savedWhite=savedRow.children[0];
  advance(650);
  assert.strictEqual(paced.elements.get("moves").children[0],savedRow,
                     "adding a new white move must retain all previous rows");
  assert.strictEqual(paced.elements.get("moves").children[0].children[0],savedWhite,
                     "incremental replay must preserve existing move nodes");
  assert.equal(ply(),3);
  assert.equal(paced.elements.get("headline").textContent,"2. Nf3");
  assert.equal(paced.elements.get("moves").children.length,2);
  const secondRow=paced.elements.get("moves").children[1];
  const secondWhite=secondRow.children[0];
  advance(650);
  assert.strictEqual(paced.elements.get("moves").children[1],secondRow,
                     "black move must fill the existing half-empty row");
  assert.strictEqual(secondRow.children[0],secondWhite,
                     "black move must not repaint white's button");
  assert.equal(ply(),4);
  assert.equal(paced.elements.get("headline").textContent,"2... Nc6");
  assert.equal(paced.elements.get("moves").children[1].children[1].textContent,
               "2... Nc6");
  advance(650);
  assert.equal(ply(),4,"never fabricate new positions");

  const nextGame={...game("next","Mikhail Tal","Bobby Fischer"),
                  states:[{...states[0]},{...states[1]}]};
  vm.runInContext("feed.duel.games.push("+JSON.stringify(nextGame)+")",paced.context);
  advance(650);
  assert.equal(ply(),0,"second game starts from initial position");
  assert.equal(paced.elements.get("headline").textContent,"Posição inicial");
  advance(650);
  assert.equal(ply(),1,"second game's initial move also gets its own tick");
  paced.elements.get("pause").events.click();
  assert.equal(paced.elements.get("pause").attributes["aria-pressed"],"true");
  const frozen=ply();
  advance(650);
  assert.equal(ply(),frozen,"pausing review disables pacing");
  paced.elements.get("live").events.click();
  assert.equal(paced.elements.get("pause").attributes["aria-pressed"],"false");


  // Earlier playback tests mutate a shared fixture. Start the observer test
  // with an isolated two-ply feed (not the burst/replay fixture).
  states.splice(2);
  // The local observer prediction never creates POST or engine commands.
  const predictor=browser();
  await flush();
  const toggle=predictor.elements.get("guess-toggle");
  toggle.events.click();
  assert.equal(toggle.attributes["aria-pressed"],"true");
  vm.runInContext("guessSelect('e7');guessSelect('e5')",predictor.context);
  assert.equal(predictor.elements.get("guess-status").textContent.includes("e7e5"),true);
  assert.equal(predictor.elements.get("guess-cancel").disabled,false);
  const predictionLayer=predictor.elements.get("guess-piece-preview");
  assert.equal(predictionLayer.children.length,1,"one noninteractive visual piece preview");
  const realBoard=predictor.elements.get("board");
  assert.equal(realBoard.children.length,64,"preview cannot add a 65th real square");
  assert.equal(predictionLayer.children[0].style.gridColumnStart,"5");
  assert.equal(predictionLayer.children[0].style.gridRowStart,"4");
  assert.equal(realBoard.children[12].children.length,1,
    "the official board still retains the unmoved source artwork");
  assert(predictor.requests.every(uri=>uri.startsWith("/api/state")));
  // One actual PGN half-move resolves the guess exactly once.
  states.push({fen:"rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq e6 0 2",
    san:"e5",last:["e7","e5"],turn:"white",clocks:[598,598],
    uci:"e7e5",legal_moves:["g1f3","d2d4"]});
  await vm.runInContext("poll()",predictor.context);
  assert.equal(predictor.elements.get("guess-score").textContent,"1 acerto(s) em 1 tentativa(s)");
  assert(predictor.elements.get("guess-status").textContent.includes("Acertou"));
  assert.equal(predictionLayer.children.length,0,"actual move clears the ghost");
  await vm.runInContext("poll()",predictor.context);
  assert.equal(predictor.elements.get("guess-score").textContent,"1 acerto(s) em 1 tentativa(s)",
    "identical polls must not score twice");
  // An incorrect guess is scored once; pausing cancels any pending intent.
  vm.runInContext("cursor=2;render();guessSelect('g1');guessSelect('f3')",
    predictor.context);
  states.push({fen:"rnbqkbnr/pppp1ppp/8/4p3/3PP3/8/PPP2PPP/RNBQKBNR b KQkq d3 0 2",
    san:"d4",last:["d2","d4"],turn:"black",clocks:[597,598],
    uci:"d2d4",legal_moves:["b8c6"]});
  await vm.runInContext("poll()",predictor.context);
  assert.equal(predictor.elements.get("guess-score").textContent,"1 acerto(s) em 2 tentativa(s)");
  assert(predictor.elements.get("guess-status").textContent.includes("Não foi"));
  assert(predictor.requests.every(uri=>uri.startsWith("/api/state")));
  vm.runInContext("cursor=3;render();guessSelect('b8')",predictor.context);
  predictor.elements.get("pause").events.click();
  assert.equal(vm.runInContext("guess.pending",predictor.context),null);

  console.log("Live bracket browser smoke: PASS");
})().catch(error=>{console.error(error);process.exitCode=1;});
