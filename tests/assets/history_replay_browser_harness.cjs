"use strict";
const assert=require("node:assert/strict");
const fs=require("node:fs");
const vm=require("node:vm");
const {URL}=require("node:url");

const initial="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const e4="rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1";
const e5="rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq e6 0 2";
const empty="8/8/8/8/8/8/8/8 w - - 0 1";
const stateData=[
  {fen:initial,san:"Início",last:null,turn:"white",clocks:[null,null]},
  {fen:e4,san:"e4",last:["e2","e4"],turn:"black",clocks:[null,null]},
  {fen:e5,san:"e5",last:["e7","e5"],turn:"white",clocks:[null,null]}
];

class Node {
  constructor(tag="div",id=""){
    this.tagName=tag.toUpperCase();this.id=id;this.children=[];this.events={};
    this.attributes={};this.dataset={};this.className="";this._text="";
    this.disabled=false;this.hidden=id==="history-games-section"||id==="history-game-replay";
    this.value="";this.max="";this.focused=false;
    this.classList={add:(...classes)=>{
      const names=new Set(this.className.split(/\s+/).filter(Boolean));
      classes.forEach(item=>names.add(item));
      this.className=[...names].join(" ");
    }};
  }
  get textContent(){return this._text+this.children.map(x=>x.textContent||"").join("");}
  set textContent(value){this._text=String(value);this.children=[];}
  append(...nodes){this.children.push(...nodes);}
  appendChild(node){this.append(node);return node;}
  replaceChildren(...nodes){this.children=nodes;this._text="";}
  setAttribute(key,value){this.attributes[key]=String(value);}
  addEventListener(name,callback){this.events[name]=callback;}
  focus(){this.focused=true;}
}
function descendants(element) {
  return [element,...element.children.flatMap(descendants)];
}
function fixture(n,opts={}) {
  const elements=new Map();
  const $=id=>{
    if(!elements.has(id))elements.set(id,new Node("div",id));
    return elements.get(id);
  };
  const document={
    getElementById:$,
    createElement:tag=>new Node(tag),
    createDocumentFragment:()=>new Node("fragment")
  };
  const calls=[];
  const deferred=[];
  const games=Array.from({length:n},(_,index)=>{
    const historical=index%2===0;
    return {
      game_id:"h-fixture-"+String(index).padStart(3,"0"),
      origin:historical?"historical":"simulation",
      white_slug:"fischer",black_slug:"tal",
      status:opts.incomplete&&index===0?"INCOMPLETE":"COMPLETED",
      result:opts.incomplete&&index===0?null:"1-0",
      event:opts.injection?"<img src=x onerror=alert(1)>":"Evento "+index,
      date:"1972.01.01",pgn_sha256:"a".repeat(64),
      source:historical?{url:"https://example.org/source",label:"Arquivo autorizado"}:
         {label:"Simulação local",session_id:"session-local"}
    };
  });
  const reply=(game)=>{
    const bad=opts.invalidReplayFor===game.game_id;
    const states=bad?[stateData[0],{...stateData[1],fen:"9/8/8/8/8/8/8/8 w - - 0 1"}]:
      stateData;
    const metadata = opts.mismatchResultFor===game.game_id ?
      {...game,result:"0-1"} : game;
    return {schema:"chesssim-history-replay-v1",game:metadata,states};
  };
  const fetch=(url,options)=>{
    calls.push({url,options});
    if(url==="/api/history/players/fischer/games"){
      return Promise.resolve({ok:!opts.listError,
        json:async()=>({schema:"chesssim-history-games-v1",
                        slug:"fischer",count:n,limit:5,games})});
    }
    if(url.startsWith("/api/history/games/") && url.endsWith("/replay")){
      const id=url.slice("/api/history/games/".length,-"/replay".length);
      const game=games.find(x=>x.game_id===id);
      if(!game)throw new Error("Unknown replay request");
      if(opts.holdFirst&&id===games[0].game_id) {
        return new Promise(resolve=>deferred.push(()=>resolve({ok:true,json:async()=>reply(game)})));
      }
      return Promise.resolve({ok:!opts.replayErrorFor||opts.replayErrorFor!==id,
                             json:async()=>reply(game)});
    }
    throw new Error("Unexpected request "+url);
  };
  const window={ChessSimHistoryPlayerName:slug=>({fischer:"Bobby Fischer",tal:"Mikhail Tal"}[slug]||slug)};
  const ctx=vm.createContext({document,window,fetch,console,URL,Math,Number,Array,Set,String,Object});
  vm.runInContext(fs.readFileSync(process.argv[2],"utf8"),ctx,
                  {filename:"chess_board.js",timeout:3000});
  vm.runInContext(fs.readFileSync(process.argv[3],"utf8"),ctx,
                  {filename:"history_replay.js",timeout:3000});
  return {$,document,window,calls,games,deferred};
}
const tick=()=>new Promise(resolve=>setImmediate(resolve));
async function settle(){for(let i=0;i<5;i++)await tick();}
const countSquares=element=>descendants(element).filter(node=>node.className.split(/\s+/).includes("square"));

(async()=>{
  for(const n of [0,1,4,5]){
    const app=fixture(n),{$,window,calls,games}=app;
    window.ChessSimHistoryGames.enter("fischer");
    await settle();
    assert.equal($("history-games-section").hidden,false);
    assert.equal($("history-game-list").children.length,n);
    if(n===0){
      assert($("history-games-status").textContent.includes("Nenhuma partida"));
      assert.equal($("history-game-replay").hidden,true);
      assert.equal(countSquares($("history-replay-board")).length,0);
    } else {
      assert.equal(countSquares($("history-replay-board")).length,64);
      assert.equal($("history-game-replay").hidden,false);
      assert.equal($("history-replay-progress").textContent,"0 / 2");
      assert($("history-game-list").children[0].textContent.includes("Histórica documentada"));
      const first=$("history-game-list").children[0];
      assert.equal(first.attributes["aria-pressed"],"true");
      const initialSquares=countSquares($("history-replay-board"));
      assert(initialSquares[0].className.includes("blackpiece"));
      assert(initialSquares[56].className.includes("whitepiece"));
      $("history-replay-next").events.click();
      assert.equal($("history-replay-progress").textContent,"1 / 2");
      assert($("history-replay-headline").textContent.includes("e4"));
      $("history-replay-end").events.click();
      assert.equal($("history-replay-progress").textContent,"2 / 2");
      assert.equal($("history-replay-next").disabled,true);
      $("history-replay-start").events.click();
      assert.equal($("history-replay-progress").textContent,"0 / 2");
      const keyEvent={key:"ArrowRight",preventDefault(){this.prevented=true}};
      $("history-replay-board").events.keydown(keyEvent);
      assert.equal(keyEvent.prevented,true);
      assert.equal($("history-replay-progress").textContent,"1 / 2");
      $("history-replay-board").events.keydown({key:"End",preventDefault(){}});
      assert.equal($("history-replay-progress").textContent,"2 / 2");
      const slider=$("history-replay-seek");
      slider.value="0";
      slider.events.input({target:slider});
      assert.equal($("history-replay-progress").textContent,"0 / 2");
      assert.equal($("history-replay-prev").disabled,true);
      const moveButton=descendants($("history-replay-moves")).find(node=>
        node.tagName==="BUTTON"&&node.textContent.includes("e5"));
      assert(moveButton);
      moveButton.events.click();
      assert.equal($("history-replay-progress").textContent,"2 / 2");
      assert($("history-replay-meta").textContent.includes("Bobby Fischer"));
      assert(calls.every(c=>!c.options.method||c.options.method==="GET"));
      assert(calls.every(c=>c.options.cache==="no-store"));
      for(let index=1;index<n;index++){
        const card=$("history-game-list").children[index];
        card.events.click();
        await settle();
        assert.equal(card.attributes["aria-pressed"],"true");
        assert.equal($("history-game-list").children[0].attributes["aria-pressed"],"false");
        assert($("history-replay-meta").textContent.includes("Evento "+index));
        if(index%2===1){
          assert($("history-replay-meta").textContent.includes("Simulação local"));
          assert($("history-replay-meta").textContent.includes("não é partida histórica"));
        } else {
          assert($("history-replay-meta").textContent.includes("Histórica documentada"));
        }
        assert.equal(countSquares($("history-replay-board")).length,64);
      }
      assert.equal(games.length,n);
    }
    window.ChessSimHistoryGames.leave();
    assert.equal($("history-games-section").hidden,true);
    assert.equal(countSquares($("history-replay-board")).length,0);
  }
  // A response arriving after choosing another game must never replace the
  // newly selected replay or wrongly re-enable its controls.
  {
    const app=fixture(4,{holdFirst:true}),{$,window,deferred}=app;
    window.ChessSimHistoryGames.enter("fischer");
    await settle();
    assert.equal(deferred.length,1);
    assert.equal($("history-game-replay").hidden,true);
    $("history-game-list").children[1].events.click();
    await settle();
    assert($("history-replay-meta").textContent.includes("Simulação local"));
    const current=$("history-replay-meta").textContent;
    deferred[0]();
    await settle();
    assert.equal($("history-replay-meta").textContent,current);
    assert.equal($("history-game-list").children[1].attributes["aria-pressed"],"true");
    window.ChessSimHistoryGames.suspend();
    window.ChessSimHistoryGames.resume();
    await settle();
    assert.equal($("history-replay-meta").textContent,current);
  }
  // An invalid replay cannot keep or display a different game's old board.
  {
    const app=fixture(4,{invalidReplayFor:"h-fixture-001"}),{$,window}=app;
    window.ChessSimHistoryGames.enter("fischer");
    await settle();
    assert.equal(countSquares($("history-replay-board")).length,64);
    $("history-game-list").children[1].events.click();
    assert.equal(countSquares($("history-replay-board")).length,0);
    await settle();
    assert.equal($("history-game-replay").hidden,true);
    assert($("history-games-status").textContent.includes("PGN inválido"));
    $("history-game-list").children[2].events.click();
    await settle();
    assert.equal(countSquares($("history-replay-board")).length,64);
  }
  {
    const app=fixture(1,{incomplete:true}),{$,window}=app;
    window.ChessSimHistoryGames.enter("fischer");
    await settle();
    assert($("history-replay-meta").textContent.includes("sem resultado final"));
    assert(!$("history-replay-meta").textContent.includes("1–0"));
  }
  {
    const app=fixture(1,{listError:true}),{$,window}=app;
    window.ChessSimHistoryGames.enter("fischer");
    await settle();
    assert($("history-games-status").textContent.includes("Não foi possível"));
    assert.equal($("history-game-list").children.length,0);
  }
  {
    const app=fixture(1,{injection:true}),{$,window}=app;
    window.ChessSimHistoryGames.enter("fischer");
    await settle();
    assert($("history-game-list").textContent.includes("<img src=x onerror"));
    assert(descendants($("history-game-list")).every(node=>node.tagName!=="IMG"));
  }
  // A forged/changed outcome must be rejected despite matching game ID/hash.
  {
    const app=fixture(4,{mismatchResultFor:"h-fixture-001"}),{$,window}=app;
    window.ChessSimHistoryGames.enter("fischer");
    await settle();
    $("history-game-list").children[1].events.click();
    await settle();
    assert.equal(countSquares($("history-replay-board")).length,0);
    assert($("history-games-status").textContent.includes("PGN inválido"));
  }
  // Leaving a profile discards late replies even after the same slug reopens.
  {
    const app=fixture(1,{holdFirst:true}),{$,window,deferred}=app;
    window.ChessSimHistoryGames.enter("fischer");
    await settle();
    assert.equal(deferred.length,1);
    window.ChessSimHistoryGames.leave();
    deferred[0]();
    await settle();
    assert.equal($("history-games-section").hidden,true);
    assert.equal(countSquares($("history-replay-board")).length,0);
  }
  // Valid empty FEN ranks retain all 64 squares (regression #43).
  {
    const app=fixture(0),{$,window}=app;
    const board=$("history-replay-board");
    assert.equal(window.ChessSimBoard.paint(board,{fen:empty,last:null},0),true);
    assert.equal(countSquares(board).length,64);
    assert(countSquares(board).every(s=>s.children.length===0));
    assert.equal(window.ChessSimBoard.paint(board,{fen:"9/8/8/8/8/8/8/8 w - - 0 1"},1),false);
    assert.equal(countSquares(board).length,0);
  }
  console.log("History replay DOM (0/1/4/5, rapid switch, invalid FEN, keyboard, offline GET): PASS");
})().catch(error=>{console.error(error);process.exitCode=1;});
