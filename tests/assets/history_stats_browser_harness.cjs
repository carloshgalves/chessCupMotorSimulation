"use strict";
const assert=require("node:assert/strict");
const fs=require("node:fs");
const vm=require("node:vm");
const {URL}=require("node:url");

class Node {
  constructor(tag="div",id=""){
    this.tagName=tag.toUpperCase();this.id=id;this.children=[];this.attributes={};
    this._text="";this.className="";this.hidden=id==="history-stats-section";
  }
  get textContent(){return this._text+this.children.map(c=>c.textContent||"").join("");}
  set textContent(v){this._text=String(v);this.children=[];}
  append(...items){this.children.push(...items);}
  replaceChildren(...items){this.children=items;this._text="";}
  setAttribute(key,value){this.attributes[key]=String(value);}
}
const tick=()=>new Promise(resolve=>setImmediate(resolve));
async function flush(){for(let i=0;i<4;i++)await tick();}
function app(options={}) {
  const nodes=new Map(),requests=[],waiting=[];
  const $=id=>{
    if(!nodes.has(id))nodes.set(id,new Node("div",id));
    return nodes.get(id);
  };
  const response=slug=>{
    const have=options.empty?0:2;
    const wins=options.corrupt?2:1;
    const records=have?[{opponent_slug:slug==="tal"?"fischer":"tal",duels_count:1,
      completed_games:2,wins:1,draws:0,losses:1,incomplete_games:0}]:[];
    if(options.corrupt)records[0].wins=1;
    return {
      schema:"chesssim-history-stats-v1",slug,
      source_scope:{kind:"configured_arena_workspace"},
      run_count:have?1:0,tournaments_participated:0,duels_count:have?1:0,
      completed_games:have,wins:have?wins:0,draws:0,losses:have?1:0,
      incomplete_games:0,head_to_head:records,
      coverage:{complete:!options.partial,runs_scanned:1,attempts_scanned:1,
        games_accepted:have,games_excluded:options.partial?2:0,
        sources_rejected:options.partial?1:0},
    };
  };
  const fetch=(uri,opts)=>{
    requests.push({uri,opts});
    if(!/^\/api\/history\/players\/(fischer|tal)\/stats$/.test(uri)){
      throw new Error("Unrecognized URL "+uri);
    }
    const slug=uri.split("/")[4];
    if(options.hold&&slug==="fischer"){
      return new Promise(resolve=>waiting.push(()=>resolve({
        ok:true,json:async()=>response(slug)
      })));
    }
    return Promise.resolve({
      ok:!options.fail,json:async()=>response(slug)
    });
  };
  const window={ChessSimHistoryPlayerName:slug=>
    slug==="tal"?"Mikhail Tal":slug==="fischer"?"Bobby Fischer":slug};
  const document={getElementById:$,createElement:tag=>new Node(tag)};
  const ctx=vm.createContext({window,document,fetch,console,Number,Math,Array,Object,Set});
  vm.runInContext(fs.readFileSync(process.argv[2],"utf8"),ctx,
                  {filename:"history_stats.js",timeout:3000});
  return {window,$,requests,waiting};
}
function descendants(node){return [node,...node.children.flatMap(descendants)];}
(async()=>{
  {
    const a=app();a.window.ChessSimHistoryStats.enter("fischer");
    await flush();
    assert.equal(a.$("history-stats-section").hidden,false);
    assert.equal(a.$("history-stats-metrics").children.length,8);
    assert(a.$("history-stats-metrics").textContent.includes("Partidas concluídas2"));
    assert(a.$("history-stats-rivals").textContent.includes("Mikhail Tal"));
    assert(a.$("history-stats-status").textContent.includes("não da carreira histórica"));
    assert(a.$("history-stats-coverage").textContent.includes("Cobertura sem erros"));
    assert.deepEqual(a.requests.map(x=>x.uri),["/api/history/players/fischer/stats"]);
    assert(a.requests.every(x=>x.opts.cache==="no-store"&&!x.opts.method));
    a.window.ChessSimHistoryStats.suspend();
    a.window.ChessSimHistoryStats.resume();
    await flush();
    assert.equal(a.requests.length,1,"cached valid response must not refetch on resume");
    a.window.ChessSimHistoryStats.leave();
    assert.equal(a.$("history-stats-section").hidden,true);
    assert.equal(a.$("history-stats-metrics").children.length,0);
  }
  {
    const a=app({empty:true});a.window.ChessSimHistoryStats.enter("fischer");
    await flush();
    assert(a.$("history-stats-status").textContent.includes("Sem partidas verificadas"));
    assert.equal(a.$("history-stats-rivals-heading").hidden,true);
    assert(a.$("history-stats-metrics").textContent.includes("Vitórias0"));
  }
  {
    const a=app({partial:true});a.window.ChessSimHistoryStats.enter("fischer");
    await flush();
    assert(a.$("history-stats-coverage").textContent.includes("Cobertura parcial"));
    assert(a.$("history-stats-coverage").className.includes("history-stats-warning"));
  }
  {
    const a=app({corrupt:true});a.window.ChessSimHistoryStats.enter("fischer");
    await flush();
    assert(a.$("history-stats-status").textContent.includes("indisponíveis"));
    assert.equal(a.$("history-stats-metrics").children.length,0);
  }
  {
    const a=app({fail:true});a.window.ChessSimHistoryStats.enter("fischer");
    await flush();
    assert(a.$("history-stats-status").textContent.includes("indisponíveis"));
    assert.equal(a.$("history-stats-metrics").children.length,0);
  }
  // Old HTTP completion must not overwrite a new player selection or a
  // hidden profile, even if the slug is the same.
  {
    const a=app({hold:true});
    a.window.ChessSimHistoryStats.enter("fischer");
    await flush();
    assert.equal(a.waiting.length,1);
    a.window.ChessSimHistoryStats.enter("tal");
    await flush();
    assert.equal(a.$("history-stats-metrics").children.length,8);
    assert(a.$("history-stats-status").textContent.includes("simulação local"));
    a.waiting[0]();
    await flush();
    assert.equal(a.$("history-stats-section").hidden,false);
    assert.equal(a.$("history-stats-metrics").children.length,8);
    a.window.ChessSimHistoryStats.leave();
    assert.equal(descendants(a.$("history-stats-rivals")).length,1);
  }
  {
    const a=app({hold:true});
    a.window.ChessSimHistoryStats.enter("fischer");
    await flush();
    a.window.ChessSimHistoryStats.suspend();
    a.waiting[0]();
    await flush();
    assert.equal(a.$("history-stats-metrics").children.length,0);
    a.window.ChessSimHistoryStats.resume();
    assert.equal(a.waiting.length,2,"interrupted request must restart on resume");
  }
  console.log("History local stats UI: empty/full/partial/corrupt, stale GET, no POST: PASS");
})().catch(err=>{console.error(err);process.exitCode=1;});
