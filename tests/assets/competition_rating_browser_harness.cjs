"use strict";
const assert=require("node:assert/strict");
const fs=require("node:fs");
const vm=require("node:vm");
const tick=()=>new Promise(resolve=>setImmediate(resolve));
class Node {
  constructor(tag="div",id=""){
    this.tagName=tag.toUpperCase();this.id=id;this.children=[];
    this.events={};this.open=false;this.hidden=id==="history-rating-profile";this._text="";
    this.value=id.endsWith("rating-mode") ? "rapid" : "";
  }
  addEventListener(type,handler){this.events[type]=handler;}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.children=children;this._text="";}
  set textContent(value){this._text=String(value);this.children=[];}
  get textContent(){return this._text+this.children.map(x=>x.textContent).join("");}
}
const players=[
  {slug:"fischer",rating:"2505",display_rating:"2505.0",initial_rating:"2500",
   games:1,wins:1,draws:0,losses:0,points_3_1_0:3,rank:1,
   status:"provisional",sample_score:1,score_range_exploratory:[0,1],
   move_accuracy:null,strength_evaluation:null},
  ...Array.from({length:63},(_,i)=>({
    slug:"player-"+(i+1),rating:"2500",display_rating:"2500.0",
    initial_rating:"2500",games:0,wins:0,draws:0,losses:0,
    points_3_1_0:0,rank:null,status:"sem_amostra",sample_score:null,
    score_range_exploratory:null,move_accuracy:null,strength_evaluation:null
  }))
];
function report(slug,mode="rapid"){
  return {
    schema:"chesssim-competition-elo-v2",baseline:"2500",
    universe:"local-uci-observed-v2",k:"10",modality:mode,
    available_modalities:{rapid:1,classical:0,blitz:0,bullet:0,unclassified:0},
    ratings:slug?[players.find(x=>x.slug===slug)]:players,
    counts:{scored_games:1},coverage:{partial:false},
    scale_note:"Elo experimental não indica força UCI",
    modalities:["rapid","blitz"],mixed_modalities_warning:true,
    ...(slug?{slug}:{})
  };
}
function browser(handler){
  const nodes=new Map(),calls=[];
  const $=id=>{
    if(!nodes.has(id))nodes.set(id,new Node("div",id));
    return nodes.get(id);
  };
  const document={getElementById:$,createElement:t=>new Node(t)};
  const window={ChessSimHistoryPlayerName:slug=>
    slug==="fischer" ? "<img src=x onerror=alert(1)>" : slug};
  const fetch=(url,options)=>{
    calls.push({url,options});return handler(url,options);
  };
  vm.runInContext(fs.readFileSync(process.argv[2],"utf8"),vm.createContext({
    document,window,fetch,console,Number,Math,Object,Array,String,Set
  }),{timeout:3000,filename:"competition_rating.js"});
  return {$,window,calls};
}
const response=data=>({ok:true,json:async()=>data});
(async()=>{
  {
    const app=browser(async url=>{
      const mode=url.includes("modality=blitz") ? "blitz" : "rapid";
      return response(url.includes("/rating?") ? report("fischer",mode) : report(null,mode));
    });
    const {$,window,calls}=app;
    assert.equal($("history-rating-profile").hidden,true);
    window.ChessSimRatings.enter("fischer");
    await tick();await tick();
    assert.equal($("history-rating-profile").hidden,false);
    assert($("history-rating-value").textContent.includes("2505.0"));
    assert($("history-rating-proof").textContent.includes("não medida"));
    assert(!$("history-rating-proof").textContent.includes("FIDE: 2505"));
    $("competition-rating-leaderboard").open=true;
    $("competition-rating-leaderboard").events.toggle();
    await tick();await tick();
    assert.equal($("competition-rating-rows").children.length,64);
    const first=$("competition-rating-rows").children[0];
    assert(first.textContent.includes("<img src=x onerror=alert(1)>"),
           "Names must be inserted as text");
    assert(first.children.every(c=>c.tagName==="TD"),"No injected HTML nodes");
    assert($("competition-rating-meta").textContent.includes("Rápido"));
    $("competition-rating-mode").value="blitz";
    $("competition-rating-mode").events.change();
    await tick();await tick();
    assert($("competition-rating-status").textContent.includes("Blitz"));
    $("history-rating-mode").value="blitz";
    $("history-rating-mode").events.change();
    await tick();await tick();
    assert($("history-rating-value").textContent.includes("Blitz"));
    assert($("competition-rating-meta").textContent.toLowerCase().includes("não"));
    assert(calls.every(x=>x.options.cache==="no-store" &&
      (x.options.method===undefined||x.options.method==="GET")));
    window.ChessSimRatings.suspend();
    window.ChessSimRatings.resume();
    await tick();await tick();
    window.ChessSimRatings.leave();
    assert.equal($("history-rating-profile").hidden,true);
    assert.equal($("history-rating-value").textContent,"");
    $("competition-rating-leaderboard").open=false;
    $("competition-rating-leaderboard").events.toggle();
    assert.equal($("competition-rating-rows").children.length,0);
  }
  {
    const pending=[];
    const app=browser(url=>new Promise(resolve=>pending.push({url,resolve})));
    app.window.ChessSimRatings.enter("fischer");
    assert.equal(pending.length,1);
    app.window.ChessSimRatings.leave();
    pending.shift().resolve(response(report("fischer")));
    await tick();await tick();
    assert.equal(app.$("history-rating-value").textContent,"",
                 "Late profile must not repaint a hidden view");
    app.$("competition-rating-leaderboard").open=true;
    app.$("competition-rating-leaderboard").events.toggle();
    assert.equal(pending.length,1);
    app.$("competition-rating-leaderboard").open=false;
    app.$("competition-rating-leaderboard").events.toggle();
    pending.shift().resolve(response(report()));
    await tick();await tick();
    assert.equal(app.$("competition-rating-rows").children.length,0,
                 "Late leaderboard may not render after close");
  }
  {
    const hacked=report("fischer");
    hacked.ratings[0]={...hacked.ratings[0],
                       strength_evaluation:2800,move_accuracy:99};
    const app=browser(async()=>response(hacked));
    app.window.ChessSimRatings.enter("fischer");
    await tick();await tick();
    assert(app.$("history-rating-status").textContent.includes("indisponível"));
    assert.equal(app.$("history-rating-value").textContent,"");
  }
  console.log("Experimental Elo browser: PASS");
})().catch(error=>{console.error(error);process.exitCode=1});
