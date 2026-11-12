"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const {URLSearchParams} = require("node:url");

const source = fs.readFileSync(process.argv[2], "utf8");
const token = "arena-capability-token-that-is-secret-and-long";
const calls = [];
let current = null;
let customCatalogUnavailable = false;
let customCatalogHang = false;
let timeoutNextId = 0;
const pendingTimeouts = new Map();
const fictional = [{
  id:"user-1234567890abcdef1234567890abcdef",
  name:"Ava <img src=x onerror=alert(1)>",
  kind:"fictional-player",status:"METADATA_ONLY_NOT_PLAYABLE",
  revision:2,installed:false,uci_eligible:false,historically_validated:false
}];
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
  mode:"depth",depth:"6",movetime:"0.5",preset:"none",
  "custom-minutes":"30","custom-increment":"5",
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
  if(path==="/api/arena/custom-players"){
    if(customCatalogHang){
      return new Promise((resolve,reject)=>{
        assert(request.signal, "Hanging catalog request must have abort signal");
        request.signal.addEventListener("abort",()=>{
          reject(new Error("catalog fetch aborted"));
        },{once:true});
      });
    }
    return customCatalogUnavailable ?
      {ok:false,status:503,json:async()=>({error:"Corrupted"})} :
      {ok:true,json:async()=>({
        schema:"chesssim-custom-arena-catalog-v1",
        configured:true,players:fictional,
        warning:"Only metadata, no UCI"
      })};
  }
  if(path==="/api/arena"){
    return {ok:true,json:async()=>({
      busy:!!current,duel_busy:!!current,human_busy:false,session:current,players,opening_books_configured:false
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
  AbortController,
  setTimeout(callback,ms){
    assert.equal(ms,5000);
    const id=++timeoutNextId;
    pendingTimeouts.set(id,callback);
    return id;
  },
  clearTimeout(id){pendingTimeouts.delete(id);},
  setInterval(){return 1;},console
});
vm.runInContext(source,ctx,{filename:"arena.js",timeout:3000});
const flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
  await flush();
  assert.equal($("white").value,"fischer");
  assert.equal($("black").value,"tal");
  assert.equal($("launch").disabled,false);
  assert.equal($("custom-profile-list").children.length,1);
  assert($("custom-profile-list").children[0].textContent.includes(
    "Ava <img src=x onerror=alert(1)>"), "untrusted name remains inert text");
  assert.equal($("white").children.at(-1).tagName,"OPTGROUP");
  assert.equal($("white").children.at(-1).children[0].disabled,true);
  assert.equal($("black").children.at(-1).children[0].disabled,true);
  assert($("custom-profile-status").textContent.includes("nenhum perfil UCI"));
  assert($("white-profile").children.some(el=>
    String(el.textContent).includes("NÃO VALIDADA")));

  $("swap").events.click();
  assert.equal($("white").value,"tal");
  assert.equal($("black").value,"fischer");
  $("swap").events.click();

  // Depth and a selected clock must coexist without hiding either field.
  $("depth").value="16";
  $("preset").value="bullet";
  $("preset").events.change();
  assert.equal($("mode").disabled,false);
  assert.equal($("depth-wrap").hidden,false);
  const bullet=vm.runInContext("configFromForm()",ctx);
  assert.equal(bullet.mode,"depth");
  assert.equal(bullet.depth,16);
  assert.equal(bullet.preset,"bullet");
  $("preset").value="custom";
  $("preset").events.change();
  assert.equal($("custom-clock-wrap").hidden,false);
  const custom=vm.runInContext("configFromForm()",ctx);
  assert.equal(custom.mode,"depth");
  assert.equal(custom.depth,16);
  assert.equal(custom.initial_seconds,1800);
  assert.equal(custom.increment_seconds,5);
  $("preset").value="official";
  $("preset").events.change();
  assert.equal($("mode").disabled,true);
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
  fictional[0].installed=true; // A malicious API must not advertise playable fiction.
  await vm.runInContext("refreshCustomCatalog()",ctx);
  assert.equal($("custom-profile-list").children.length,0);
  assert($("custom-profile-status").textContent.includes("Não foi possível"));
  assert.equal($("white").value,"fischer");
  assert.equal($("black").value,"tal");
  assert.equal($("white").children.at(-1).tagName,"OPTION");
  assert.equal(calls.length,2, "Custom GET must never create an engine POST");
  fictional[0].installed=false;
  await vm.runInContext("refreshCustomCatalog()",ctx);
  assert.equal($("custom-profile-list").children.length,1);
  customCatalogUnavailable=true;
  await vm.runInContext("refreshCustomCatalog()",ctx);
  assert.equal($("custom-profile-list").children.length,0);
  assert.equal($("launch").disabled,true, "Busy state remains independent");
  assert.equal(calls.length,2);
  assert.equal(pendingTimeouts.size,0);
  customCatalogUnavailable=false;
  customCatalogHang=true;
  const hanging=vm.runInContext("refreshCustomCatalog()",ctx);
  await flush();
  assert.equal(pendingTimeouts.size,1);
  const abort=[...pendingTimeouts.values()][0];
  abort();
  await hanging;
  assert.equal(pendingTimeouts.size,0);
  assert($("custom-profile-status").textContent.includes("Não foi possível"));
  assert.equal($("custom-profile-list").children.length,0);
  assert.equal(calls.length,2, "Timeout must not touch engine controls");
  console.log("Arena browser controls: PASS");
})().catch(err=>{console.error(err);process.exitCode=1;});
