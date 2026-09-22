
'use strict';
let row=null,raw='';
function entry(id){return A.rows.find(r=>r[F.id]===id)}
async function start(){const id=Number(new URLSearchParams(location.search).get('id'));
 if(!Number.isSafeInteger(id)||id<1){$('main').innerHTML='<div class="empty">Укажите корректный ID события.</div>';return}
 // The archive is normally sequential; the map-free fast path still handles sparse IDs.
 row=A.rows[id-1]?.[F.id]===id?A.rows[id-1]:entry(id);
 if(!row){$('main').innerHTML='<div class="empty">Событие #'+id+' не найдено.</div>';return}
 const part=row[F.chunk];try{await loadScript('data/raw_'+String(part).padStart(5,'0')+'.js');raw=window.AKUZ_RAW[row[F.pos]];window.AKUZ_RAW=null;if(typeof raw!=='string')throw Error('Неверная позиция записи в архиве');show()}
 catch(e){$('main').textContent='Не удалось открыть запись: '+String(e.message||e)}
}
function show(){const id=row[F.id],prev=A.rows[id-2],next=A.rows[id],cat=catLabel(row[F.category]),comp=A.componentNames[row[F.component]];
 $('main').innerHTML=`<div class="detail-header"><h2>Событие #${id}</h2><span class="tag" data-cat="${escapeHtml(cat)}">${escapeHtml(cat)}</span></div>
 <p class="muted">${escapeHtml((A.sources||[])[row[F.source]]||A.meta.source)} · ${escapeHtml(humanTime(row))} · строка ${row[F.start]}${row[F.end]>row[F.start]?'–'+row[F.end]:''}</p>
 <dl class="meta"><div><dt>Компонент</dt><dd>${escapeHtml(comp)}</dd></div><div><dt>Request ID</dt><dd>${escapeHtml(row[F.request]||'—')}</dd></div><div><dt>Пользователь</dt><dd>${escapeHtml(row[F.user]||'—')}</dd></div></dl>
 <div class="rowtools"><button id="copy">Копировать исходную запись</button><button id="copy-link">Копировать ссылку</button><a class="btn" href="index.html?request=${encodeURIComponent(row[F.request])}">События Request ID</a><button id="wrap">Перенос строк: вкл.</button></div>
 <div id="copy-status" class="status" role="status"></div><pre id="raw" class="code"></pre>
 <div class="pager"><div class="buttons"><a class="btn${prev?'':' muted'}" href="${prev?'event.html?id='+prev[F.id]:'#'}">← Предыдущее</a><a class="btn${next?'':' muted'}" href="${next?'event.html?id='+next[F.id]:'#'}">Следующее →</a></div><a class="btn" href="index.html">← На главную</a></div>`;
 const fp=(A.errorFingerprints||{})[String(id)];
 if(fp){
   const analytics=document.createElement('a');analytics.className='btn';
   analytics.textContent='▥ Аналитика этой ошибки ↗';
   analytics.href=(location.pathname.includes('/reports/')?'../../errors.html?fp=':'errors.html?fp=')+encodeURIComponent(fp);
   analytics.target='_blank';analytics.rel='noopener';
   document.querySelector('.rowtools').appendChild(analytics);
 }
 $('raw').textContent=raw;$('copy').addEventListener('click',()=>copyText(raw));$('copy-link').addEventListener('click',()=>copyText(location.href));
 $('wrap').addEventListener('click',()=>{const p=$('raw'),wrapped=p.style.whiteSpace!=='pre';p.style.whiteSpace=wrapped?'pre':'pre-wrap';$('wrap').textContent='Перенос строк: '+(wrapped?'выкл.':'вкл.')});
}
async function copyText(s){try{await navigator.clipboard.writeText(s);$('copy-status').textContent='Скопировано'}catch(_){$('raw').focus();$('copy-status').textContent='Браузер запретил копирование из file://. Выделите текст и нажмите Ctrl+C.'}}
$('theme').addEventListener('click',theme);start();
