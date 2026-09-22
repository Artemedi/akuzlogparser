'use strict';
(() => {
  const analytics=document.getElementById('open-analytics');
  if(analytics)analytics.href=location.pathname.includes('/reports/')?'../../errors.html':'errors.html';
  const panel=document.getElementById('fetch-panel'); if(!panel)return;
  const $=id=>document.getElementById(id);
  const buttons=['fetch-latest','fetch-list','fetch-cache','picker-build','picker-select-all','picker-select-none'];
  const isLocal=/^(127\.0\.0\.1|localhost)$/.test(location.hostname)&&location.protocol==='http:';
  if(!isLocal){$('fetch-status').textContent='Офлайн-просмотр работает. Для SSH и управления отчётами запустите START_EXPLORER.bat.';for(const id of buttons)$(id).disabled=true;return}
  let files=[],renderKey='',awaitAction='',shownResult='',pollTimer=null,pickerCollapsed=true,initialSourceLoaded=false,busyNow=true;
  const status=$('fetch-status'), picker=$('remote-picker'), collection=$('picker-files'), link=$('fetch-open');
  const url='/api/status';
  const label=s=>String(s??'');
  const size=n=>n>=1048576?(n/1048576).toFixed(1)+' МБ':(n/1024).toFixed(0)+' КБ';
  if(analytics)analytics.addEventListener('click',event=>{
    if(busyNow){event.preventDefault();status.textContent='Дождитесь завершения обработки всех выбранных журналов.';}
  });
  function setBusy(busy){busyNow=busy;
    if(analytics){
      analytics.setAttribute('aria-disabled',String(busy));
      analytics.classList.toggle('disabled',busy);
      analytics.title=busy?'Дождитесь завершения обработки всех выбранных журналов':'Открыть аналитику ошибок';
      if(busy)analytics.removeAttribute('href');
      else analytics.href=location.pathname.includes('/reports/')?'../../errors.html':'errors.html';
    }
    for(const id of ['fetch-latest','fetch-list','fetch-cache'])$(id).disabled=busy;
    $('fetch-source').disabled=busy;
    $('picker-build').disabled=busy||!files.some(x=>x.checked)||files.filter(x=>x.checked).length>30;
  }
  async function jsonRequest(path,body){const r=await fetch(path,{method:'POST',cache:'no-store',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});let data=await r.json();if(!r.ok)throw Error(data.error||'HTTP '+r.status);return data}
  function showPicker(expanded){
    pickerCollapsed=!expanded;
    picker.hidden=pickerCollapsed || !files.length;
    const toggle=$('picker-toggle');
    toggle.hidden=!files.length;
    toggle.textContent=pickerCollapsed?'▾ Показать файлы':'▴ Свернуть файлы';
    toggle.setAttribute('aria-expanded',String(!picker.hidden));
  }
  function renderFiles(){
    const from=$('picker-from').value,to=$('picker-to').value;
    const visible=files.filter(f=>(!from||f.suggested_date>=from)&&(!to||f.suggested_date<=to));
    collection.replaceChildren();
    for(const f of visible){
      const row=document.createElement('div');row.className='fileitem';row.dataset.fid=f.id;
      const select=document.createElement('input');select.type='checkbox';select.className='file-select';select.checked=!!f.checked;select.addEventListener('change',()=>{f.checked=select.checked;updateCount()});row.appendChild(select);
      const title=document.createElement('div');title.className='name';title.textContent=f.name;
      const path=document.createElement('span');path.className='sub';path.textContent=f.path;title.appendChild(path);row.appendChild(title);
      const modified=document.createElement('div');modified.className='sizedate';modified.textContent=f.suggested_date;
      const hm=document.createElement('span');hm.className='sub';hm.textContent='mtime · '+f.modified_utc.slice(11,19)+' UTC';modified.appendChild(hm);row.appendChild(modified);
      const amount=document.createElement('span');amount.className='sub';amount.textContent=size(f.size)+(f.cached?' · кэш':'');row.appendChild(amount);
      const group=document.createElement('label');group.className='firstdate';group.textContent='Дата первой записи';const date=document.createElement('input');date.type='date';date.value=f.date??'';date.title='Дата из имени YYYYMMDD_*.log. При необходимости исправьте вручную.';date.addEventListener('change',()=>f.date=date.value);group.appendChild(date);row.appendChild(group);
      collection.appendChild(row);
    }
    showPicker(!pickerCollapsed);updateCount();
  }
  function updateCount(){const sel=files.filter(x=>x.checked).length;$('picker-count').textContent='Доступно '+files.length+' · показано '+collection.children.length+' · выбрано '+sel+' (максимум 30)';$('picker-build').disabled=busyNow||!sel||sel>30}
  async function getReports(){try{const r=await fetch('/api/reports',{cache:'no-store'});if(!r.ok)throw Error('Не удалось прочитать библиотеку');const data=await r.json();const lib=$('report-library'),items=$('report-items');items.replaceChildren();for(const report of data.reports){const row=document.createElement('div');row.className='library-item';const a=document.createElement('a');a.href=report.url;a.textContent=(report.kind==='combined'?'▦ ':'▤ ')+report.label;row.appendChild(a);const detail=document.createElement('span');detail.className='sub';detail.textContent=Number(report.events).toLocaleString('ru-RU')+' событий · '+report.created;row.appendChild(detail);items.appendChild(row)}lib.hidden=!data.reports.length}catch(e){status.textContent='Библиотека: '+e.message}}
  async function refresh(){
    try{
      const r=await fetch(url,{cache:'no-store'});if(!r.ok)throw Error('HTTP '+r.status);const s=await r.json();setBusy(!!s.busy);
      if(!initialSourceLoaded){$('fetch-source').value=s.source||'linux';initialSourceLoaded=true}
      if(!s.busy && !s.error && ['list','build','latest'].includes(awaitAction))$('fetch-source').value=s.source||'linux';
      status.textContent=s.error?'Ошибка: '+s.error:s.stage;
      const signature=(s.source||'linux')+'|'+s.listing.map(f=>f.id+String(f.cached)).join('|');
      if(signature!==renderKey){
        renderKey=signature;files=s.listing.map(f=>Object.assign({checked:false,date:''},f));
        if(files.length)renderFiles();else showPicker(false);
      }
      if(!s.busy && awaitAction==='list' && !s.error){
        showPicker(true);awaitAction='';
      }
      if(!s.busy && (s.result||s.error)){
        if(s.result && s.result.report_url){
          link.href=s.result.report_url;link.hidden=false;
          if(['build','latest'].includes(awaitAction))showPicker(false);
        }
        if(s.result && s.result.reused)status.textContent+=' · повторная загрузка не потребовалась';
        if(s.result && s.result.active_snapshots)status.textContent+=' · снимков активного журнала: '+s.result.active_snapshots;
        if(s.result && s.result.cleanup){await getReports();if(awaitAction==='clear'&&$('clear-reports').checked&&location.pathname.startsWith('/reports/')){location.assign('/');return}}
        if(s.result&&s.result.report_url&&['build','latest'].includes(awaitAction)){
          awaitAction='';await getReports();location.assign(s.result.report_url);return;
        }
        if(s.error)awaitAction='';
        if(!shownResult||shownResult!==JSON.stringify(s.result)){shownResult=JSON.stringify(s.result);await getReports()}
      }
      pollTimer=setTimeout(refresh,s.busy?1100:2500)
    }catch(e){status.textContent='Нет связи с локальным сервисом: '+String(e.message||e);setBusy(true)}
  }
  async function action(endpoint,body,name){
    if(pollTimer)clearTimeout(pollTimer);
    awaitAction=name;setBusy(true);status.textContent='Отправляю команду…';link.hidden=true;
    try{await jsonRequest(endpoint,body);refresh()}catch(e){awaitAction='';status.textContent='Ошибка: '+e.message;setBusy(false)}
  }
  $('fetch-source').addEventListener('change',()=>{
    files=[];renderKey='';collection.replaceChildren();showPicker(false);updateCount();
    status.textContent='Выбран источник: '+($('fetch-source').value==='windows'?'Windows · SMB':'Linux · SSH')+'. Нажмите «Список файлов».';
  });
  $('fetch-latest').addEventListener('click',()=>action('/api/fetch',{source:$('fetch-source').value},'latest'));
  $('fetch-list').addEventListener('click',()=>action('/api/list',{source:$('fetch-source').value},'list'));
  $('picker-toggle').addEventListener('click',()=>showPicker(pickerCollapsed));
  $('picker-build').addEventListener('click',()=>{
    const selected=files.filter(f=>f.checked).map(f=>({id:f.id,date:f.date||''}));
    if(!selected.length){status.textContent='Выберите файлы.';return}
    if(selected.length>30){status.textContent='Максимум 30 файлов за операцию.';return}
    if(selected.length>1&&!selected.every(f=>f.date)){
      if(!confirm('У части файлов нет даты в имени YYYYMMDD_*.log и дата не указана вручную. Создать отдельные отчёты без общей временной шкалы?'))return;
    }
    action('/api/build',{selections:selected},'build');
  });
  $('fetch-cache').addEventListener('click',()=>{
    const include=$('clear-reports').checked;
    if(!confirm(include?'Удалить кэш скачанных v4 файлов И все созданные отчёты v4? Старые v2/v3 отчёты не затрагиваются.':'Удалить только скачанные файлы из кэша v4? Готовые отчёты сохранятся.'))return;
    action('/api/clear',{reports:include},'clear');
  });
  for(const id of ['picker-from','picker-to'])$(id).addEventListener('change',renderFiles);
  $('picker-today').addEventListener('click',()=>{$('picker-from').value='';$('picker-to').value='';renderFiles()});
  $('picker-select-all').addEventListener('click',()=>{const ids=new Set([...collection.children].map(x=>x.dataset.fid));for(const f of files)if(ids.has(f.id))f.checked=true;renderFiles()});
  $('picker-select-none').addEventListener('click',()=>{for(const f of files)f.checked=false;renderFiles()});
  setBusy(true);refresh();getReports();
})();
