const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

class Element {
  constructor(){this.children=[];this.listeners={};this.attrs={};this.dataset={};this.value='';this.classList={toggle(){}};}
  appendChild(child){this.children.push(child);return child;}
  replaceChildren(){this.children=[];}
  addEventListener(name,fn){this.listeners[name]=fn;}
  setAttribute(name,value){this.attrs[name]=value;}
  removeAttribute(name){delete this[name];delete this.attrs[name];}
}

test('analytics stays locked during a batch, including file-list rendering, and unlocks on completion',async()=>{
  const nodes=new Map();
  const get=id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);};
  let state={busy:false,source:'linux',listing:[],stage:'Ready'};
  let nextPoll;
  const context={document:{getElementById:get,createElement:()=>new Element()},
    location:{hostname:'127.0.0.1',protocol:'http:',pathname:'/',assign(){}},
    setTimeout:fn=>{nextPoll=fn;return 1;},clearTimeout(){},confirm:()=>true,
    fetch:async url=>({ok:true,json:async()=>url==='/api/status'?state:
      url==='/api/reports'?{reports:[]}:{started:true}})};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../app_controls.js'),'utf8'),context);
  const flush=()=>new Promise(resolve=>setImmediate(resolve));
  assert.equal(get('open-analytics').attrs['aria-disabled'],'true');
  await flush();
  assert.equal(get('open-analytics').href,'errors.html');
  state={...state,busy:true,listing:[{id:'one',name:'20260923_app.log',path:'/logs/20260923_app.log',
    date:'2026-09-23',suggested_date:'2026-09-24',modified_utc:'2026-09-24T12:00:00',size:123}]};
  await nextPoll();
  assert.equal(get('open-analytics').href,undefined);
  const row=get('picker-files').children[0];
  assert.equal(row.children[4].children[0].value,'2026-09-23');
  const checkbox=row.children[0];checkbox.checked=true;checkbox.listeners.change();
  assert.equal(get('picker-build').disabled,true);
  let prevented=false;
  get('open-analytics').listeners.click({preventDefault(){prevented=true;}});
  assert.equal(prevented,true);
  state={...state,busy:false};
  await nextPoll();
  assert.equal(get('open-analytics').attrs['aria-disabled'],'false');
  assert.equal(get('open-analytics').href,'errors.html');
  assert.equal(get('picker-build').disabled,false);
  // A new action locks navigation immediately, before the server responds.
  const action=get('fetch-list').listeners.click();
  assert.equal(get('open-analytics').attrs['aria-disabled'],'true');
  await action;await flush();
});
