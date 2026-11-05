"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const {URLSearchParams} = require("node:url");

const source = fs.readFileSync(process.argv[2], "utf8");
const token = "arena-capability-token-that-is-secret-and-long";
const calls = [];
let current = null;
const players = [
  {slug:"fischer",name:"Bobby Fischer",installed:true,alias:"Fischer",
   historically_validated:false,reference_period:"1970–1972",
   reference_status:"RESEARCH_HYPOTHESIS",reference_note:"Hipótese de pesquisa",
   provenance:"Alias Rodent IV reconhecido"},
  {slug:"tal",name:"Mikhail Tal",installed:true,alias:"Tal",
   historically_validated:false,reference_period:null,reference_status:"UNREVIEWED",
   reference_note:"Não revisado",provenance:"Alias Rodent IV reconhecido"},
  {slug:"mequinho",name:"Henrique Mecking",installed:false,alias:null,
   historically_validated:false,reference_period:"1973–1978",
   reference_status:"CATALOG_REFERENCE_UNVERIFIED",reference_note:"Metadado do catálogo",
   provenance:"Não registrado"}
];

class Element {
  constructor(tag="div",id=""){
    this.tagName=tag.toUpperCase();this.id=id;this.value="";
    this.checked=false;this.disabled=false;this.hidden=false;this.children=[];
    this.textContent="";this.className="";this.events={};
  }
  append(...kids){this.children.push(...kids);}
  appendChild(kid){this.children.push(kid);return kid;}
  replaceChildren(...kids){this.children=[...kids];}
  addEventListener(event,fn){this.events[event]=fn;}
  querySelector(){return {disabled:false};}
  reportValidity(){return true;}
}
const map=new Map();
const defaults={
  mode:"depth",depth:"6",movetime:"0.5",preset:"official",
  opening:"none",seed:"2029","max-plies":"400",
  reverse:true
};
const document={
  getElementById(id){
    if(!map.has(id)){
      const element=new Element("div",id);
      if(id in defaults){
        if(id==="reverse")element.checked=defaults[id];
        else element.value=defaults[id];
      }
      map.set(id,element);
    }
    return map.get(id);
  },
  createElement(tag){return new Element(tag);}
};
const $=id=>document.getElementById(id);
const fetch=async(path,request={})=>{
  if(path==="/api/arena"){
    return {ok:true,json:async()=>({
      busy:!!current,session:current,players,opening_books_configured:false
    })};
  }
  calls.push({path,request});
  assert.equal(request.headers["X-Chesssim-Token"],token);
  assert.equal(request.headers["Content-Type"],"application/json");
  if(path==="/api/arena/launch"){
    const config=JSON.parse(request.body);
    current={id:"session-test",status:"RUNNING",config,duel:null,error:null};
    return {ok:true,json:async()=>({id:"session-test",status:"STARTING"})};
  }
  if(path==="/api/arena/stop"){
    return {ok:true,json:async()=>({stop_requested:true})};
  }
  throw new Error("unexpected POST path "+path);
};
const window={
  location:{hash:"#token="+token},
  confirm(){return true;}
};
const ctx=vm.createContext({
  document,window,URLSearchParams,fetch,Number,String,Date,Math,
  setInterval(){return 1;},console
});
vm.runInContext(source,ctx,{filename:"arena.js",timeout:3000});
const flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
  await flush();
  assert.equal($("white").value,"fischer");
  assert.equal($("black").value,"tal");
  assert.equal($("launch").disabled,false);
  assert($("white-profile").children.some(el=>
    String(el.textContent).includes("NÃO VALIDADA")));

  $("swap").events.click();
  assert.equal($("white").value,"tal");
  assert.equal($("black").value,"fischer");
  $("swap").events.click();

  $("mode").value="preset";
  $("preset").value="official";
  $("mode").events.change();
  assert.equal($("opening").value,"internal");
  assert.equal($("opening").disabled,true);
  assert.equal($("reverse").checked,true);
  assert.equal($("reverse").disabled,true);
  assert.equal($("max-plies").value,"600");

  await $("duel-form").events.submit({preventDefault(){}});
  await flush();
  assert.equal(calls.length,1);
  assert.equal(calls[0].path,"/api/arena/launch");
  const payload=JSON.parse(calls[0].request.body);
  assert.equal(payload.white,"fischer");
  assert.equal(payload.black,"tal");
  assert.equal(payload.mode,"preset");
  assert.equal(payload.preset,"official");
  assert.equal(payload.opening_mode,"internal");
  assert.equal(payload.seed,undefined);
  assert.equal(payload.max_plies,600);
  assert.equal($("stop").disabled,false);
  assert.equal($("launch").disabled,true);

  await $("stop").events.click();
  assert.equal(calls[1].path,"/api/arena/stop");
  assert($("launch-feedback").textContent.includes("Parada solicitada"));
  assert.equal($("board-frame").children.length,0);
  console.log("Arena browser controls: PASS");
})().catch(err=>{console.error(err);process.exitCode=1;});
