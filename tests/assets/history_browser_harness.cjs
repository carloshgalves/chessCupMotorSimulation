"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const calls = [];
class Node {
  constructor(tag="div", id="") {
    this.tagName=tag.toUpperCase();
    this.id=id;
    this.children=[];
    this.attributes={};
    this.events={};
    this._text="";
    this.hidden=id==="history-view" || id==="history-profile";
    this.disabled=false;
  }
  get textContent(){return this._text+this.children.map(c=>c.textContent||"").join("");}
  set textContent(v){this._text=String(v);this.children=[];}
  append(...items){this.children.push(...items);}
  appendChild(item){this.append(item);}
  replaceChildren(...items){this.children=items;this._text="";}
  addEventListener(name,cb){this.events[name]=cb;}
  setAttribute(name,value){this.attributes[name]=String(value);}
  focus(){this.focused=true;}
  replaceWith(other){this.replacement=other;}
}
const elements = new Map();
const document = {
  getElementById(id){if(!elements.has(id)) elements.set(id,new Node("div",id)); return elements.get(id);},
  createElement(tag){return new Node(tag);}
};
const $=id=>document.getElementById(id);
const photo = {
  alt: "Viswanathan Anand em Corus 2010",
  author:"Stefan64",event:"Corus Chess Tournament",event_date:"2010-01",
  source_url:"https://commons.wikimedia.org/wiki/File:VAnand10.jpg",
  license_url:"https://creativecommons.org/licenses/by-sa/3.0/",
  license:"CC BY-SA 3.0",derivative_note:"Recortada para 480×480."
};
const index=[
 {slug:"anand",name:"Viswanathan Anand",era_label:"2007–2013",
  short_bio:"Fui campeão mundial.",review_status:"reviewed",portrait:photo},
 {slug:"capablanca",name:"José Raúl Capablanca",era_label:"1921–1927",
  short_bio:"Fui campeão mundial.",review_status:"pending",portrait:null},
 {slug:"carlsen",name:"Magnus Carlsen",era_label:"2013–2023",
  short_bio:"Fui campeão mundial.",review_status:"pending",portrait:null}
];
const profile={
 ...index[0],
 highlights:[{year:2007,text:"Conquistei a coroa mundial.",source_ids:["a"]}],
 timeline:[{year:2007,title:"Título",summary:"Conquistei a coroa mundial."}],
 sources:[{id:"a",title:"Histórico",publisher:"FIDE",url:"https://fide.com/example"}],
};
const fetch=async(url,options)=>{
  calls.push({url,options});
  if(url==="/api/history/players") return {ok:true,json:async()=>({players:index})};
  if(url==="/api/history/players/anand") return {ok:true,json:async()=>profile};
  if(url==="/api/history/players/anand/games") return {
    ok:true,json:async()=>({
      schema:"chesssim-history-games-v1",slug:"anand",count:0,limit:5,games:[]
    })
  };
  throw new Error("Unexpected request "+url);
};
const ctx=vm.createContext({document,fetch,console,Array,String,Math,window:{},URL});
const source=fs.readFileSync(process.argv[2],"utf8");
vm.runInContext(fs.readFileSync(process.argv[3],"utf8"),ctx,
                {filename:"chess_board.js",timeout:3000});
vm.runInContext(fs.readFileSync(process.argv[4],"utf8"),ctx,
                {filename:"history_replay.js",timeout:3000});
vm.runInContext(source,ctx,{filename:"history.js",timeout:3000});
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function descendants(node) {
  return [node,...node.children.flatMap(descendants)];
}
(async()=>{
  $("tab-history").events.click();
  await tick();
  assert.equal($("arena-view").hidden,true);
  assert.equal($("history-view").hidden,false);
  assert.equal($("tab-history").attributes["aria-pressed"],"true");
  assert.equal(calls.length,1);
  assert.equal(calls[0].url,"/api/history/players");
  assert.equal($("history-position").textContent.includes("1 de 3"),true);
  assert.equal($("history-selected").textContent.includes("Viswanathan Anand"),true);
  const nodes=descendants($("history-selected"));
  assert(nodes.some(x=>x.tagName==="IMG"&&x.src==="/api/history/portraits/anand.jpg"));
  assert(nodes.some(x=>x.tagName==="A"&&x.href===photo.source_url));
  assert(nodes.some(x=>x.tagName==="A"&&x.href===photo.license_url));
  assert(nodes.some(x=>x.textContent.includes("Recortada para 480")));
  assert.equal($("history-right").children[0].tagName,"FIGURE");
  assert.equal($("history-right").children[1].tagName,"BUTTON");

  $("history-next").events.click();
  assert.equal($("history-position").textContent.includes("2 de 3"),true);
  assert($("history-selected").textContent.includes("Em curadoria"));
  assert($("history-selected").textContent.includes("Retrato não disponível"));
  $("history-prev").events.click();
  await $("history-open").events.click();
  await tick();
  assert.equal($("history-profile").hidden,false);
  assert.equal($("history-roster").hidden,true);
  assert.equal($("history-profile-name").textContent,"Viswanathan Anand");
  assert(descendants($("history-profile-body")).some(x=>x.tagName==="A"&&x.href===profile.sources[0].url));
  assert(descendants($("history-profile-body")).some(x=>x.tagName==="A"&&x.href===photo.license_url));
  await tick();
  assert.equal($("history-games-section").hidden,false);
  assert($("history-games-status").textContent.includes("Nenhuma partida"));
  $("history-back").events.click();
  assert.equal($("history-position").textContent.includes("1 de 3"),true);
  $("tab-arena").events.click();
  assert.equal($("arena-view").hidden,false);
  assert.equal($("history-view").hidden,true);
  assert.equal(calls.length,3);
  assert(calls.every(call=>!call.url.includes("token") && call.options.cache==="no-store"));
  console.log("History navigation, first-person facts, safe source links and photo credits: PASS");
})().catch(err=>{console.error(err);process.exitCode=1;});
