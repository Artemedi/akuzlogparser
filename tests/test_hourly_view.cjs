const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');

function fixture(){
  const rows=[
    [1,0,'08:00:00',0,0,'','','',1,1,0,0,0],
    [2,0,'08:15:00',0,0,'','','',2,2,0,1,0],
    [3,1,'08:00:00',0,0,'','','',3,3,0,2,0],
    [4,1,'09:00:00',0,0,'','','',4,4,0,3,0]
  ];
  const A={rows,hourly:[[0,'08',2],[1,'08',1],[1,'09',1]],
    errorFingerprints:{2:'fp1',4:'fp2'},meta:{base_date:'2026-09-21'}};
  const nodes=new Map();
  const get=id=>{if(!nodes.has(id))nodes.set(id,{
    value:id==='hour-view'?'profile':id==='hour-metric'?'events':'',
    innerHTML:'',textContent:'',querySelectorAll:()=>[]
  });return nodes.get(id);};
  const context={A,F:{id:0,day:1,time:2},fmt:n=>Number(n).toLocaleString('ru-RU'),
    escapeHtml:s=>String(s),$:get,api:null};
  const code=fs.readFileSync(path.join(__dirname,'../index.js'),'utf8');
  const prefix=code.slice(0,code.indexOf('function quickMatch(r){'));
  vm.runInNewContext(prefix+'\napi={hourlyProfile,renderHours,calendarDay,rowMatches,state};',context);
  return {get,api:context.api};
}

test('24-hour profile merges dates, retains error counts and distinguishes metrics',()=>{
  const {get,api}=fixture(),p=api.hourlyProfile();
  assert.equal(p.length,24);
  assert.equal(p[8].events,3);
  assert.equal(p[8].errors,1);
  assert.equal(p[9].events,1);
  assert.equal(p[9].errors,1);
  assert.deepEqual([p[8].parts.length,p[9].parts.length],[2,1]);
  api.renderHours();
  assert.equal((get('hours').innerHTML.match(/class="barrow"/g)||[]).length,24);
  assert.match(get('hours').innerHTML,/data-hour="\* 08"/);
  assert.match(get('hours-note').textContent,/2 дн/);
  get('hour-metric').value='errors';
  api.renderHours();
  assert.match(get('hours-note').textContent,/распознанные исключения/);
  get('hour-metric').value='average';
  api.renderHours();
  assert.match(get('hours').innerHTML,/1,5/);
});
test('chronology keeps day/hour drilldown and does not average individual dates',()=>{
  const {get,api}=fixture();
  get('hour-view').value='timeline';
  get('hour-metric').value='average';
  api.renderHours();
  assert.equal(get('hour-metric').value,'events');
  assert.equal((get('hours').innerHTML.match(/class="barrow"/g)||[]).length,3);
  assert.match(get('hours').innerHTML,/2026-09-21 08:xx/);
  assert.match(get('hours').innerHTML,/data-hour="1 09"/);
  assert.equal(api.calendarDay(1),'2026-09-22');
});

test('profile hour filters all dates while chronology targets one day',()=>{
  const {api}=fixture();
  api.state.hour='* 08';
  assert.equal(api.rowMatches([99,0,'08:45:00']),true);
  assert.equal(api.rowMatches([99,1,'08:45:00']),true);
  assert.equal(api.rowMatches([99,1,'09:45:00']),false);
  api.state.hour='1 09';
  assert.equal(api.rowMatches([99,0,'09:45:00']),false);
  assert.equal(api.rowMatches([99,1,'09:45:00']),true);
});
