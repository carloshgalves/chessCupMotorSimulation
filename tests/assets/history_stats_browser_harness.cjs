"use strict";
const assert=require("node:assert/strict");
const fs=require("node:fs");
const vm=require("node:vm");

class Node {
  constructor(tag="div",id=""){
    this.tagName=tag.toUpperCase();this.id=id;this.children=[];
    this._text="";this.hidden=id==="history-stats-section";
  }
  get textContent(){return this._text+this.children.map(child=>child.textContent||"").join("");}
  set textContent(value){this._text=String(value);this.children=[];}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=nodes;this._text="";}
}
const descendants=node=>[node,...node.children.flatMap(descendants)];
const flush=()=>new Promise(resolve=>setImmediate(resolve));

function record(slug="fischer",extra={}) {
  return {
    schema:"chesssim-player-stats-v1",slug,
    scope:{directory:"<img onerror=alert(1)>",sources:["arena_local","tournament_local","cup_local"],
           from_utc:"2026-11-06T12:00:00+00:00",to_utc:"2026-11-06T13:00:00+00:00"},
    totals:{tournaments:1,duels:2,games:3,completed:2,wins:1,draws:1,losses:0,incomplete:1},
    opponents:[{slug:"tal",duels:2,games:3,completed:2,wins:1,draws:1,losses:0,incomplete:1}],
    coverage:{sessions_seen:1,tournaments_seen:1,cups_seen:1,cup_synthetic_excluded:0,candidate_games:4,
              verified_games:3,arena_verified_games:1,tournament_verified_games:2,cup_verified_games:0,rejected_games:1,rejected_records:1,
              excluded_attempts:1,failed_attempts:0,duplicate_records:0,partial:true},
    ...extra
  };
}
function browser(handler){
  const elements=new Map(),calls=[];
  const $=id=>{
    if(!elements.has(id))elements.set(id,new Node("div",id));
    return elements.get(id);
  };
  const document={getElementById:$,createElement:tag=>new Node(tag)};
  const window={ChessSimHistoryPlayerName:slug=>slug==="tal"?"Mikhail Tal":slug};
  const fetch=(url,options)=>{
    calls.push({url,options});
    return handler(url,options);
  };
  const context=vm.createContext({document,window,fetch,Number,String,Array,Set,Object,console});
  vm.runInContext(fs.readFileSync(process.argv[2],"utf8"),context,
                  {filename:"history_stats.js",timeout:3000});
  return {window,document,$,calls};
}
const response=record=>({ok:true,json:async()=>record});
(async()=>{
  {
    const app=browser((url)=>Promise.resolve(response(record())));
    const {$,window,calls}=app;
    window.ChessSimHistoryStats.enter("fischer");
    await flush();await flush();
    assert.equal($("history-stats-section").hidden,false);
    assert.equal($("history-stats-totals").children.length,8);
    assert($("history-stats-totals").textContent.includes("Vitórias1"));
    assert($("history-stats-scope").textContent.includes("2026-11-06"));
    assert($("history-stats-coverage").textContent.includes("1 partidas e 1 registros rejeitados"));
    assert($("history-stats-warning").textContent.includes("cobertura parcial"));
    assert($("history-stats-opponents").textContent.includes("Mikhail Tal"));
    assert($("history-stats-scope").textContent.includes("<img onerror"));
    assert(descendants($("history-stats-section")).every(element=>element.tagName!=="IMG"),
           "untrusted directory names cannot become HTML elements");
    assert(calls.every(x=>x.options.cache==="no-store" &&
      (!x.options.method||x.options.method==="GET")));
    const number=calls.length;
    window.ChessSimHistoryStats.suspend();
    window.ChessSimHistoryStats.resume();
    await flush();
    assert.equal(calls.length,number,"resume uses cached results");
    window.ChessSimHistoryStats.leave();
    assert.equal($("history-stats-section").hidden,true);
    assert.equal($("history-stats-totals").children.length,0);
  }
  {
    const empty=record("fischer",{
      totals:{tournaments:0,duels:0,games:0,completed:0,wins:0,draws:0,losses:0,incomplete:0},
      opponents:[],
      scope:{directory:"runs",sources:["arena_local","tournament_local","cup_local"],
             from_utc:null,to_utc:null},
      coverage:{sessions_seen:0,tournaments_seen:0,cups_seen:0,cup_synthetic_excluded:0,candidate_games:0,
                verified_games:0,arena_verified_games:0,tournament_verified_games:0,cup_verified_games:0,rejected_games:0,rejected_records:0,
                excluded_attempts:0,failed_attempts:0,duplicate_records:0,partial:false}
    });
    const app=browser(()=>Promise.resolve(response(empty)));
    app.window.ChessSimHistoryStats.enter("fischer");
    await flush();await flush();
    assert(app.$("history-stats-empty").textContent.includes("Sem partidas"));
    assert.equal(app.$("history-stats-warning").textContent,"");
  }
  {
    const deferred=[];
    const app=browser(url=>{
      if(url.includes("/fischer/"))return new Promise(resolve=>deferred.push(resolve));
      return Promise.resolve(response(record("tal",{
        totals:{tournaments:0,duels:0,games:0,completed:0,wins:0,draws:0,losses:0,incomplete:0},
        opponents:[],
        coverage:{sessions_seen:0,tournaments_seen:0,cups_seen:0,cup_synthetic_excluded:0,candidate_games:0,
                  verified_games:0,arena_verified_games:0,tournament_verified_games:0,cup_verified_games:0,rejected_games:0,rejected_records:0,
                  excluded_attempts:0,failed_attempts:0,duplicate_records:0,partial:false}
      })));
    });
    app.window.ChessSimHistoryStats.enter("fischer");
    app.window.ChessSimHistoryStats.enter("tal");
    await flush();await flush();
    assert(app.$("history-stats-status").textContent.includes("não representam"));
    deferred[0](response(record()));
    await flush();await flush();
    assert(app.$("history-stats-empty").textContent.includes("Sem partidas"),
           "stale earlier player cannot overwrite newer player panel");
    assert.equal(app.$("history-stats-totals").textContent.includes("Vitórias1"),false);
  }
  {
    const forged=record("fischer",{
      totals:{tournaments:0,duels:2,games:3,completed:2,wins:3,draws:0,losses:0,incomplete:1}
    });
    const app=browser(()=>Promise.resolve(response(forged)));
    app.window.ChessSimHistoryStats.enter("fischer");
    await flush();await flush();
    assert(app.$("history-stats-status").textContent.includes("indisponíveis"),
           "a contradictory API response must not display numeric data");
    assert.equal(app.$("history-stats-totals").children.length,0);
  }
  console.log("History statistics DOM / stale requests / invalid totals / XSS / offline GET: PASS");
})().catch(err=>{console.error(err);process.exitCode=1;});
