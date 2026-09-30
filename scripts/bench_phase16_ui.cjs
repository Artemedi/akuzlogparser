'use strict';

const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {performance}=require('node:perf_hooks');

const ROOT=path.resolve(__dirname,'..');
const common=fs.readFileSync(path.join(ROOT,'common.js'),'utf8');
const indexJs=fs.readFileSync(path.join(ROOT,'index.js'),'utf8');
const errorsJs=fs.readFileSync(path.join(ROOT,'errors.js'),'utf8');

class Element{
  constructor(tag='div'){
    this.tagName=tag.toUpperCase();this.children=[];this.listeners={};this.attrs={};
    this.dataset={};this.value='';this.textContent='';this.innerHTML='';this.hidden=false;
    this.disabled=false;this.checked=false;this.style={};this.className='';
    this.classList={toggle(){},contains(){return false;},add(){},remove(){}};
  }
  appendChild(x){this.children.push(x);return x}
  replaceChildren(...x){this.children=[...x]}
  addEventListener(n,fn){this.listeners[n]=fn}
  setAttribute(n,v){this.attrs[n]=String(v)}
  removeAttribute(n){delete this.attrs[n]}
  querySelectorAll(){return []}
  scrollIntoView(){}
  remove(){}
  focus(){}
}

function dom(){
  const nodes=new Map();
  const get=id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id)};
  const document={
    getElementById:get,
    createElement:tag=>new Element(tag),
    createElementNS:(_ns,tag)=>new Element(tag),
    body:new Element('body'),
    head:new Element('head'),
    querySelector:()=>new Element(),
  };
  return {nodes,get,document};
}

function makeReport(events){
  const rows=new Array(events);
  const hourly=new Map();
  const errorFingerprints={};
  for(let i=0;i<events;i++){
    const id=i+1,day=Math.floor(i/100000)%7,hour=Math.floor(i/4000)%24;
    const hh=String(hour).padStart(2,'0');
    const minute=String(i%60).padStart(2,'0');
    rows[i]=[
      id,day,hh+':'+minute+':00.000',i%20,i%5,
      'req-'+(i%10000),'user-'+(i%200),
      (i%10000===0?'needle ':'')+'synthetic event '+id,
      id,id,Math.floor(i/1000),i%1000,0
    ];
    const key=day+'|'+hh;
    hourly.set(key,(hourly.get(key)||0)+1);
    if(i%1000===0)errorFingerprints[String(id)]='a'.repeat(24);
  }
  return {
    meta:{source:'synthetic.log',events,physical_lines:events,continuation_lines:0,
      component_count:20,chunks:Math.ceil(events/1000),chunk_size:1000,
      replacement_chars:0,out_of_order_timestamps:0,midnight_rollovers:0,
      base_date:'2026-09-23'},
    rows,sources:['synthetic.log'],errorFingerprints,
    categories:['other','missing','nack','error','timeout'],
    componentNames:Array.from({length:20},(_,i)=>'component-'+i),
    components:Array.from({length:20},(_,i)=>[i,'component-'+i]),
    hourly:[...hourly].map(([k,n])=>{const [d,h]=k.split('|');return [Number(d),h,n]}),
    category_counts:[[0,Math.floor(events/5)]],
    patterns:[],durations:[],requests:[['req-1',Math.ceil(events/10000)]],
  };
}

function baseContext(document,windowObj,location){
  return vm.createContext({
    window:windowObj,document,location,
    localStorage:{getItem(){return null},setItem(){}},
    URL,URLSearchParams,Date,Map,Set,Math,Number,String,Object,Array,Promise,
    console,requestAnimationFrame:fn=>fn(),setTimeout,clearTimeout,
    history:{replaceState(){}},
    navigator:{clipboard:{writeText:async()=>{}}},
  });
}

function reportTrial(A){
  const {get,document}=dom();
  const windowObj={AKUZ_DATA:A,scrollTo(){}};
  const context=baseContext(document,windowObj,{
    hostname:'127.0.0.1',protocol:'http:',pathname:'/',search:'',href:'http://127.0.0.1/'
  });
  for(const id of ['hour-view','hour-metric'])get(id).value=id==='hour-view'?'profile':'events';
  const t0=performance.now();
  vm.runInContext(common,context,{filename:'common.js'});
  vm.runInContext(indexJs,context,{filename:'index.js'});
  const initial=performance.now()-t0;
  get('q').value='needle';
  const t1=performance.now();
  get('find').listeners.click();
  const quick=performance.now()-t1;
  get('component').value='7';
  const t2=performance.now();
  get('component').listeners.change();
  const filtered=performance.now()-t2;
  return {initial_ms:initial,quick_search_ms:quick,component_filter_ms:filtered};
}

function fp(n){return n.toString(16).padStart(24,'0').slice(-24)}
function makeAnalytics(groupsCount,itemsCount){
  const groups=Array.from({length:groupsCount},(_,i)=>({
    fp:fp(i+1),family:'Synthetic',exception:'SyntheticException'+(i%20),
    template:(i%100===0?'needle ':'')+'template '+i,method:'Synthetic.Method',
    total:itemsCount,dated:itemsCount,ambiguous:0
  }));
  return {
    distinct_errors:groupsCount,report_count:4,ambiguous:0,date_conflicts:0,
    groups,types:groups,families:groups.slice(0,100)
  };
}
function makeDetail(id,itemsCount){
  const items=new Array(itemsCount);
  const days=[];
  for(let d=0;d<30;d++)days.push(['2026-09-'+String((d%8)+21).padStart(2,'0'),Math.ceil(itemsCount/30)]);
  for(let i=0;i<itemsCount;i++)items[i]={
    day:'2026-09-'+String((i%8)+21).padStart(2,'0'),relative_day:i%8,
    clock:String(i%24).padStart(2,'0')+':00:00',report_id:'v4_synthetic',event_id:i+1
  };
  return {fp:id,family:'Synthetic',exception:'SyntheticException',template:'template',
    method:'Synthetic.Method',total:itemsCount,dated:itemsCount,ambiguous:0,
    days,hours:days.map(([d,n])=>[d+' 12',n]),relative_days:[['D+0',itemsCount]],
    relative_hours:[['D+0 12',itemsCount]],items};
}

async function analyticsTrial(data,detail){
  const {get,document}=dom();
  const windowObj={AKUZ_ANALYTICS:data,AKUZ_ERROR_DETAIL:null};
  document.head.appendChild=node=>{
    if(node.tagName==='SCRIPT'){
      windowObj.AKUZ_ERROR_DETAIL=detail;
      if(node.onload)node.onload();
    }
    return node;
  };
  const location={
    hostname:'example.invalid',protocol:'file:',pathname:'/errors.html',search:'',
    href:'file:///errors.html',reload(){}
  };
  const context=baseContext(document,windowObj,location);
  const t0=performance.now();
  vm.runInContext(errorsJs,context,{filename:'errors.js'});
  await new Promise(resolve=>setImmediate(resolve));
  await new Promise(resolve=>setImmediate(resolve));
  const initial=performance.now()-t0;
  get('error-search').value='needle';
  const t1=performance.now();
  get('error-search').oninput();
  const search=performance.now()-t1;
  return {initial_detail_ms:initial,group_search_ms:search};
}

function median(values){
  const x=[...values].sort((a,b)=>a-b);
  return x[Math.floor(x.length/2)];
}

(async()=>{
  const events=Number(process.argv[2]||657738);
  const groups=Number(process.argv[3]||5000);
  const detailItems=Number(process.argv[4]||100000);
  const built0=performance.now();
  const report=makeReport(events);
  const analytics=makeAnalytics(groups,detailItems);
  const detail=makeDetail(analytics.types[0].fp,detailItems);
  const dataBuild=performance.now()-built0;

  const reportTrials=[],analyticsTrials=[];
  for(let i=0;i<3;i++)reportTrials.push(reportTrial(report));
  for(let i=0;i<3;i++)analyticsTrials.push(await analyticsTrial(analytics,detail));

  const result={
    events,groups,detail_items:detailItems,data_build_ms:Number(dataBuild.toFixed(3)),
    report_initial_ms:Number(median(reportTrials.map(x=>x.initial_ms)).toFixed(3)),
    report_quick_search_ms:Number(median(reportTrials.map(x=>x.quick_search_ms)).toFixed(3)),
    report_component_filter_ms:Number(median(reportTrials.map(x=>x.component_filter_ms)).toFixed(3)),
    analytics_initial_detail_ms:Number(median(analyticsTrials.map(x=>x.initial_detail_ms)).toFixed(3)),
    analytics_group_search_ms:Number(median(analyticsTrials.map(x=>x.group_search_ms)).toFixed(3)),
    heap_used_mb:Number((process.memoryUsage().heapUsed/1048576).toFixed(1)),
    note:'Node VM CPU/DOM-shim baseline; excludes catalog disk read, JSON parse and browser layout/paint.'
  };
  console.log('PHASE16_UI_METRICS '+JSON.stringify(result));
})().catch(err=>{console.error(err);process.exit(1)});
