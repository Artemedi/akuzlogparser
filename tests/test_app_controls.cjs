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

test('report library groups versions by verified source identity, without summing events',async()=>{
  const nodes=new Map();
  const get=id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);};
  const make=(id,created,events,remote_path,kind='single',host='app1')=>({
    id,created,events,kind,url:'/reports/'+id+'/index.html',
    label:'20260923_server.log · 2026-09-23',
    sources:[{host,remote_path,date:'2026-09-23'}]
  });
  const reports=[
    make('new','2026-09-23T12:03:24',101282,'/srv/a/20260923_server.log'),
    make('old','2026-09-23T10:17:13',74356,'/srv/a/20260923_server.log'),
    make('another-path','2026-09-23T09:00:00',500,'/srv/b/20260923_server.log'),
    make('another-host','2026-09-23T08:00:00',500,'/srv/a/20260923_server.log','single','app2'),
    make('combined','2026-09-23T07:00:00',200000,'/srv/a/20260923_server.log','combined')
  ];
  const context={
    document:{getElementById:get,createElement:tag=>{const el=new Element();el.tagName=tag.toUpperCase();return el;}},
    location:{hostname:'127.0.0.1',protocol:'http:',pathname:'/',assign(){}},
    setTimeout:()=>1,clearTimeout(){},confirm:()=>true,
    fetch:async url=>({ok:true,json:async()=>url==='/api/status'?
      {busy:false,source:'linux',listing:[],stage:'Ready'}:
      url==='/api/reports'?{reports}:{started:true}})
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../app_controls.js'),'utf8'),context);
  await new Promise(resolve=>setImmediate(resolve));
  const entries=get('report-items').children;
  assert.equal(entries.length,4,'different paths, hosts and merged reports remain separate');
  assert.equal(entries[0].className,'library-report-group');
  assert.equal(entries[0].tagName,'DETAILS','snapshot history is natively collapsible');
  assert.equal(entries[0].open,undefined,'snapshot history is collapsed by default');
  assert.equal(entries[0].children[0].tagName,'SUMMARY');
  assert.equal(entries[0].children.length,3);
  assert.match(entries[0].children[0].children[1].textContent,/не суммируются/);
  assert.equal(entries[0].children[1].children[0].href,'/reports/new/index.html');
  assert.equal(entries[0].children[1].children[1].textContent,'Последний снимок');
  assert.match(entries[0].children[1].children[2].textContent,/101/);
  assert.equal(entries[0].children[2].children[0].href,'/reports/old/index.html');
  assert.equal(entries[0].children[2].children[1].textContent,'Предыдущий снимок');
  assert.match(entries[0].children[2].children[2].textContent,/74/);
  assert.equal(entries[1].children[0].href,'/reports/another-path/index.html');
  assert.equal(entries[2].children[0].href,'/reports/another-host/index.html');
  assert.equal(entries[3].children[0].href,'/reports/combined/index.html');
});

test('local input is sent to list/latest, restored from status and clears stale file choices',async()=>{
  const nodes=new Map();
  const get=id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);};
  const posted=[];
  const context={
    document:{getElementById:get,createElement:()=>new Element()},
    location:{hostname:'127.0.0.1',protocol:'http:',pathname:'/',assign(){}},
    setTimeout:()=>1,clearTimeout(){},confirm:()=>true,
    fetch:async (url,options)=>{
      if(options&&options.method==='POST')posted.push([url,JSON.parse(options.body)]);
      return {ok:true,json:async()=>url==='/api/status'?
        {busy:false,source:'local',local_path:'C:\\AKUZ\\Logs',listing:[],stage:'Ready'}:
        url==='/api/reports'?{reports:[]}:{started:true}};
    }
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../app_controls.js'),'utf8'),context);
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(get('fetch-source').value,'local');
  assert.equal(get('local-path-panel').hidden,false);
  assert.equal(get('local-path').value,'C:\\AKUZ\\Logs');
  get('local-path').value='D:\\AKUZ Logs';
  get('local-path').listeners.input();
  assert.equal(get('picker-files').children.length,0);
  await get('fetch-list').listeners.click();
  assert.equal(posted[0][0],'/api/list');
  assert.equal(posted[0][1].local_path,'D:\\AKUZ Logs');
  await get('fetch-latest').listeners.click();
  assert.equal(posted[1][0],'/api/fetch');
  assert.equal(posted[1][1].source,'local');
  get('fetch-source').value='linux';
  get('fetch-source').listeners.change();
  assert.equal(get('local-path-panel').hidden,true);
});

test('legacy HTML reports without source controls do not break the reader',()=>{
  const nodes=new Map();
  const get=id=>{if(id==='fetch-source'||id==='local-path')return null;
    if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../app_controls.js'),'utf8'),{
    document:{getElementById:get},
    location:{hostname:'127.0.0.1',protocol:'http:',pathname:'/reports/v4_old/index.html'}
  });
  assert.match(get('fetch-status').textContent,/старой версией/);
  assert.equal(get('fetch-latest').disabled,true);
});
test('clear cache is cache-only and works without legacy report-deletion checkbox',async()=>{
  const nodes=new Map();
  const get=id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);};
  const posted=[],prompts=[];
  const context={
    document:{getElementById:get,createElement:()=>new Element()},
    location:{hostname:'127.0.0.1',protocol:'http:',pathname:'/',assign(){}},
    setTimeout:()=>1,clearTimeout(){},
    confirm:text=>{prompts.push(text);return true;},
    fetch:async (url,options)=>{
      if(options?.method==='POST')posted.push([url,JSON.parse(options.body)]);
      return {ok:true,json:async()=>url==='/api/status'?
        {busy:false,source:'linux',listing:[],stage:'Ready'}:
        url==='/api/reports'?{reports:[]}:{started:true}};
    }
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../app_controls.js'),'utf8'),context);
  await new Promise(resolve=>setImmediate(resolve));
  await get('fetch-cache').listeners.click();
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(posted.length,1);
  assert.equal(posted[0][0],'/api/clear');
  assert.equal(posted[0][1].reports,false);
  assert.match(prompts[0],/Готовые отчёты сохранятся/);
  assert.equal(nodes.has('clear-reports'),false);
});
