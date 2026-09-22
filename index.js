
'use strict';
const rows=A.rows, number=fmt, per=70;let filtered=[],page=0,searchEpoch=0,lastQuery='',lastMode='quick';
const state={q:'',request:'',component:'',category:'',hour:'',from:'',to:'',source:'',dayfile:'',full:false};
function summary(){
 $('source').textContent=A.meta.source; $('total').textContent=fmt(A.meta.events);$('lines').textContent=fmt(A.meta.physical_lines);
 $('multi').textContent=fmt(A.meta.continuation_lines);$('components').textContent=fmt(A.meta.component_count);
 $('date-note').textContent=A.meta.base_date?'Дата первой записи: '+A.meta.base_date:'Исходный журнал без календарной даты — показано D+0, D+1 и т. д.';
 $('integrity').textContent='В исходном файле: '+fmt(A.meta.replacement_chars||0)+' символов замены кодировки; '+fmt(A.meta.out_of_order_timestamps||0)+' нарушений порядка времени; '+fmt(A.meta.midnight_rollovers||0)+' переходов через полночь.';
 $('catview').innerHTML=A.category_counts.map(([c,n])=>`<div class="category-row"><span class="tag" data-cat="${escapeHtml(catLabel(c))}">${escapeHtml(catLabel(c))}</span><b>${fmt(n)}</b></div>`).join('');
 const mx=Math.max(1,...A.hourly.map(h=>h[2]));
 $('hours').innerHTML=A.hourly.map(h=>`<div class="barrow"><button data-hour="${escapeHtml(h[0]+' '+h[1])}" title="Фильтровать этот час">D+${h[0]} ${escapeHtml(h[1])}:xx</button><div class="bartrack"><div class="barfill" style="width:${(100*h[2]/mx).toFixed(2)}%"></div></div><div class="num">${fmt(h[2])}</div></div>`).join('');
 $('hours').querySelectorAll('button').forEach(b=>b.addEventListener('click',()=>{$('hour').value=b.dataset.hour;search()}));
 for(const [i,name] of (A.sources||[]).entries()){const o=document.createElement('option');o.value=String(i);o.textContent=name;$('source-file').appendChild(o)}
 for(const d of [...new Set(rows.map(r=>r[F.day]))].sort((a,b)=>a-b)){const o=document.createElement('option');o.value=String(d);o.textContent=A.meta.base_date?new Date(Date.parse(A.meta.base_date+'T00:00:00Z')+d*86400000).toISOString().slice(0,10):'D+'+d;$('day-file').appendChild(o)}
 for(const [value,label] of A.components){const option=document.createElement('option');option.value=value;option.textContent=label;$('component').appendChild(option)}
 for(const [code,label] of A.categories.map((label,index)=>[index,label])){const option=document.createElement('option');option.value=String(code);option.textContent=label;$('category').appendChild(option)}
 for(const h of A.hourly){const option=document.createElement('option');option.value=h[0]+' '+h[1];option.textContent='D+'+h[0]+' '+h[1]+':xx';$('hour').appendChild(option)}
 $('patterns').innerHTML=A.patterns.map(p=>`<tr><td class="num">${fmt(p[0])}</td><td>${escapeHtml(p[1])}</td><td><span class="tag" data-cat="${escapeHtml(catLabel(p[2]))}">${escapeHtml(catLabel(p[2]))}</span></td><td class="truncate" title="${escapeHtml(p[3])}"><a href="event.html?id=${p[4]}">${escapeHtml(p[3])}</a></td></tr>`).join('');
 $('durations').innerHTML=A.durations.length?A.durations.map(d=>`<tr><td class="num">${fmt(d[0].toFixed(1))}</td><td>${escapeHtml(d[1])}</td><td>${escapeHtml(d[2])}</td><td><a href="event.html?id=${d[3]}">#${d[3]}</a></td></tr>`).join(''):'<tr><td colspan="4">Нет распознанных длительностей</td></tr>';
 $('requests').innerHTML=A.requests.map(d=>`<tr><td class="truncate" title="${escapeHtml(d[0])}">${escapeHtml(d[0])}</td><td class="num">${fmt(d[1])}</td><td><button data-req="${escapeHtml(d[0])}">Показать</button></td></tr>`).join('');
 $('requests').querySelectorAll('button').forEach(b=>b.addEventListener('click',()=>{$('request').value=b.dataset.req;search();window.scrollTo({top:$('explore').offsetTop,behavior:'smooth'})}));
}
function sync(){state.q=$('q').value.trim().toLocaleLowerCase();state.request=$('request').value.trim().toLocaleLowerCase();state.component=$('component').value;state.category=$('category').value;state.hour=$('hour').value;state.from=$('from').value;state.to=$('to').value;state.full=$('mode-full').checked;state.source=$('source-file').value;state.dayfile=$('day-file').value}
function rowMatches(r){
 if(state.source!==''&&r[F.source]!==Number(state.source))return false;
 if(state.dayfile!==''&&r[F.day]!==Number(state.dayfile))return false;
 if(state.component!==''&&r[F.component]!==Number(state.component))return false;
 if(state.category!==''&&r[F.category]!==Number(state.category))return false;
 if(state.hour!==''&&r[F.day]+' '+r[F.time].slice(0,2)!==state.hour)return false;
 if(state.from&&r[F.time].slice(0,5)<state.from)return false;
 if(state.to&&r[F.time].slice(0,5)>state.to)return false;
 if(state.request&&!r[F.request].toLocaleLowerCase().includes(state.request))return false;
 return true;
}
function quickMatch(r){return !state.q||String(r[F.id]).includes(state.q)||((A.sources||[])[r[F.source]]||A.meta.source||'').toLocaleLowerCase().includes(state.q)||A.componentNames[r[F.component]].toLocaleLowerCase().includes(state.q)||A.categories[r[F.category]].toLocaleLowerCase().includes(state.q)||r[F.headline].toLocaleLowerCase().includes(state.q)||r[F.request].toLocaleLowerCase().includes(state.q)||r[F.user].toLocaleLowerCase().includes(state.q)}
function search(){sync();const epoch=++searchEpoch;page=0;filtered=[];$('find').disabled=state.full&&!!state.q;$('cancel').hidden=!(state.full&&!!state.q);
 if(state.full&&state.q){scanFull(epoch);return}
 for(let i=0;i<rows.length;i++){let r=rows[i];if(rowMatches(r)&&quickMatch(r))filtered.push(i)}
 lastQuery=state.q;lastMode='quick';render();$('status').textContent='Быстрый поиск проверяет заголовок, компонент, ID, пользователя и Request ID. Для текста продолжений включите «Весь текст».';
}
async function scanFull(epoch){
 $('status').textContent='Полнотекстовый поиск: обработано 0 из '+fmt(A.meta.chunks)+' частей. Можно отменить.';
 lastQuery=state.q;lastMode='full';const list=[];
 try{
 for(let part=0;part<A.meta.chunks;part++){
   if(epoch!==searchEpoch)return;
   const lo=part*A.meta.chunk_size,hi=Math.min(lo+A.meta.chunk_size,rows.length);
   let eligible=false;for(let i=lo;i<hi;i++){if(rowMatches(rows[i])){eligible=true;break}}if(!eligible)continue;
   // Use the same detail shard that event.html opens; only one shard in RAM.
   await loadScript('data/raw_'+String(part).padStart(5,'0')+'.js');
   if(epoch!==searchEpoch){window.AKUZ_RAW=null;return}
   const raw=window.AKUZ_RAW;
   for(let i=lo;i<hi;i++)if(rowMatches(rows[i])&&raw[i-lo].toLocaleLowerCase().includes(state.q))list.push(i);
   window.AKUZ_RAW=null;
   $('status').textContent='Полнотекстовый поиск: обработано '+fmt(part+1)+' из '+fmt(A.meta.chunks)+' частей · совпадений '+fmt(list.length);
   if((part+1)%5===0)await new Promise(resolve=>requestAnimationFrame(resolve));
 }
 if(epoch!==searchEpoch)return;
 filtered=list;page=0;render();$('status').textContent='Полнотекстовый поиск завершён: '+fmt(list.length)+' событий. Поиск выполнен по полной исходной записи.';
 }catch(e){$('status').textContent=String(e.message||e)+'; убедитесь, что каталог data/ расположен рядом с index.html.'}
 finally{if(epoch===searchEpoch){$('find').disabled=false;$('cancel').hidden=true}}
}
function render(){
 const count=filtered.length,maxPage=Math.max(0,Math.ceil(count/per)-1);page=Math.max(0,Math.min(page,maxPage));
 $('count').textContent=fmt(count)+' событий';$('page-info').textContent=count?(page*per+1)+'–'+Math.min(count,(page+1)*per)+' из '+fmt(count):'Нет совпадений';
 $('prev').disabled=page===0;$('next').disabled=page>=maxPage;
 let html='';for(const i of filtered.slice(page*per,(page+1)*per)){
 const r=rows[i],t=humanTime(r),comp=A.componentNames[r[F.component]],c=catLabel(r[F.category]);
 html+=`<a class="result" href="event.html?id=${r[F.id]}" title="${escapeHtml((A.sources||[])[r[F.source]]||'')} · строки ${r[F.start]}–${r[F.end]}"><span class="time">${escapeHtml(t)}</span><span class="comp" title="${escapeHtml((A.sources||[])[r[F.source]])}">${escapeHtml(comp)}</span><span><span class="tag" data-cat="${escapeHtml(c)}">${escapeHtml(c)}</span></span><span class="message">${escapeHtml(r[F.headline])}</span><span class="id">#${r[F.id]}</span></a>`;
 }
 $('results').innerHTML=html||'<div class="empty">Нет событий для этих условий. Сбросьте часть фильтров.</div>';
}
$('find').addEventListener('click',search);$('cancel').addEventListener('click',()=>{searchEpoch++;$('find').disabled=false;$('cancel').hidden=true;$('status').textContent='Поиск отменён. Неполные результаты не показаны.'});
$('clear').addEventListener('click',()=>{for(const id of ['q','request','component','category','hour','from','to','source-file','day-file'])$(id).value='';$('mode-full').checked=false;search()});
$('prev').addEventListener('click',()=>{page--;render();$('results').scrollIntoView({behavior:'smooth',block:'start'})});
$('next').addEventListener('click',()=>{page++;render();$('results').scrollIntoView({behavior:'smooth',block:'start'})});
$('theme').addEventListener('click',theme);const requested=new URLSearchParams(location.search).get('request');if(requested)$('request').value=requested;$('q').addEventListener('keydown',e=>{if(e.key==='Enter')search()});$('request').addEventListener('keydown',e=>{if(e.key==='Enter')search()});
for(const id of ['component','category','hour','from','to','source-file','day-file'])$(id).addEventListener('change',search);
summary();search();
