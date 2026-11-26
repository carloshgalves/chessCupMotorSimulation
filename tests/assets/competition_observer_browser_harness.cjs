"use strict";
const assert=require("node:assert/strict");
const fs=require("node:fs"), vm=require("node:vm");
class El{
  constructor(tag){this.tag=tag;this.children=[];this.events={};this.attributes={};this.value="";this.disabled=false;}
  append(...items){this.children.push(...items);}
  replaceChildren(...items){this.children=items;}
  addEventListener(k,fn){this.events[k]=fn;}
  setAttribute(k,v){this.attributes[k]=v;}
  get firstChild(){return this.children[0];}
  showModal(){this.open=true;}
  close(){this.open=false;this.events.close?.();}
}
const body=new El("body"), document={body,createElement:tag=>new El(tag)}, timers=[];
let now=1, lastPly=-1, live="g2", moves=1, painted=[];
const games=[{id:"g1",white:"fischer",black:"tal",status:"COMPLETED",result:"1-0"},
             {id:"g2",white:"karpov",black:"kasparov",status:"RUNNING"},
             {id:"g3",white:"lasker",black:"anand",status:"PENDING"}];
const states=n=>Array.from({length:n+1},(_,i)=>({fen:"position"+i,last:null,turn:i%2?"black":"white",clocks:[60,60]}));
const window={ChessSimBoard:{paint(board,p,ply){lastPly=p?ply:-1;painted.push(p?.fen);}}};
const fetch=async path=>({ok:true,json:async()=>{
  const id=path.split("/").at(-1);
  if(id==="observe")return {games:games.map(g=>({...g})),live:live?[live]:[]};
  if(id==="g1")return {status:"COMPLETED",states:states(2),moves:[{san:"e4",uci:"e2e4"},{san:"e5",uci:"e7e5"}]};
  if(id===live)return {status:"RUNNING",states:states(moves),moves:Array.from({length:moves},(_,i)=>({san:"e"+i,uci:"m"+i})),elapsed_since_move:0};
  return {status:"UNAVAILABLE"};
}});
vm.runInNewContext(fs.readFileSync(process.argv[2],"utf8"),{window,document,fetch,performance:{now:()=>now},setInterval:(fn,ms)=>timers.push([fn,ms])});
const flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
  window.CompetitionObserver.open("leagues","league-abc");
  await flush();
  const dlg=body.children[0], bar=dlg.children[2], controls=dlg.children[4];
  assert.equal(lastPly,1);
  assert.equal(bar.children[0].disabled,false);
  bar.children[0].events.click();await flush();
  assert.equal(lastPly,0);
  controls.children[3].events.click();assert.equal(lastPly,2);
  bar.children[2].events.click();await flush();assert.equal(lastPly,1);
  moves=3;now+=3000;await timers.find(t=>t[1]===750)[0]();assert.equal(lastPly,3);
  controls.children[1].events.click();assert.equal(lastPly,2);
  moves=4;now+=3000;await timers.find(t=>t[1]===750)[0]();assert.equal(lastPly,2);
  bar.children[1].events.click();await flush();assert.equal(lastPly,-1);
  dlg.close();
  console.log("Competition observer browser smoke: PASS");
})().catch(e=>{console.error(e);process.exitCode=1;});
