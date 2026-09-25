#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AKUZ Log Explorer: offline browser UI for AKUZ logs or parser JSONL archives.

Python >=3.9, standard library only. No HTTP server or external JavaScript/CDN.
Every original event is stored in static, on-demand JS shards to work over file://.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, timedelta
import gzip
import json
from pathlib import Path
import sys
from typing import Any, Iterator

# Existing AKUZ parser is bundled separately: the explorer also supports JSONL
# archives without importing it or requiring the original .log file.

CAT = ("прочее", "не найдено", "отказ/NACK", "ошибка/исключение", "таймаут")

CSS = r"""
:root{color-scheme:dark;--bg:#0a101b;--pane:#121c2a;--pane2:#182638;--line:#2a384b;--txt:#eaf2ff;--muted:#9badc1;--accent:#69d4bb;--blue:#7ab6ff;--warn:#ffbd78;--danger:#fa879b;--radius:15px}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--bg);color:var(--txt);font:14px/1.55 system-ui,-apple-system,'Segoe UI',sans-serif}
body.light{color-scheme:light;--bg:#f4f7fb;--pane:#fff;--pane2:#edf4fa;--line:#d4e0ed;--txt:#1d2e43;--muted:#4f667d;--accent:#087e69;--blue:#1565b8;--warn:#a54e00;--danger:#b32445}
a{color:var(--blue);text-decoration:none}a:hover{text-decoration:underline}
.wrap{max-width:1540px;margin:auto;padding:22px 28px 70px}.top{display:flex;justify-content:space-between;align-items:center;gap:20px;flex-wrap:wrap;margin-bottom:20px}.brand{display:flex;align-items:center;gap:12px}.logo{width:44px;height:44px;border:1px solid #488575;border-radius:12px;display:grid;place-items:center;background:#143932;color:#b4f8df;font-weight:800;font-size:18px}.eyebrow{color:var(--accent);font-weight:800;text-transform:uppercase;letter-spacing:.15em;font-size:10px}.brand h1{margin:0;font-size:23px;letter-spacing:-.6px}.sub{color:var(--muted);font-size:12px}.actions{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
button,.btn,select,input{font:inherit;color:var(--txt);border:1px solid var(--line);background:var(--pane2);border-radius:9px;padding:9px 11px;outline-offset:2px}button,.btn{cursor:pointer}button:hover,.btn:hover{border-color:var(--accent);text-decoration:none}button.primary,.btn.primary{background:var(--accent);border-color:var(--accent);color:#071913;font-weight:750}button:disabled{opacity:.45;cursor:not-allowed}input::placeholder{color:var(--muted)}input[type=search]{width:100%}input[type=checkbox]{accent-color:var(--accent)}.stack{display:grid;gap:17px}.panel{background:var(--pane);border:1px solid var(--line);border-radius:var(--radius);padding:20px;min-width:0}.hero{background:linear-gradient(115deg,var(--pane),var(--pane2));border:1px solid var(--line);border-radius:var(--radius);padding:22px;margin-bottom:17px}.hero h2{margin:0 0 4px;font-size:19px}.hero .note{margin:4px 0 0;color:var(--muted);font-size:13px;word-break:break-word}.metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:13px;margin-bottom:17px}.metric{background:var(--pane);border:1px solid var(--line);border-radius:var(--radius);padding:17px}.metric strong{font-size:25px;display:block;letter-spacing:-.8px}.metric span{color:var(--muted);font-size:12px}.cols{display:grid;grid-template-columns:minmax(0,1.1fr) minmax(310px,.9fr);gap:17px}.panel h2{font-size:15px;margin:0 0 14px}.panel .small{color:var(--muted);font-size:12px}.category-row{display:flex;justify-content:space-between;align-items:center;padding:7px 0;gap:12px;border-bottom:1px solid var(--line)}.category-row:last-child{border:0}.tag{padding:3px 8px;border:1px solid var(--line);border-radius:999px;display:inline-block;font-size:11px;white-space:nowrap}.tag[data-cat='таймаут'],.tag[data-cat='ошибка/исключение']{color:var(--danger)}.tag[data-cat='отказ/NACK']{color:var(--warn)}.tag[data-cat='не найдено']{color:var(--blue)}.barrow{display:grid;grid-template-columns:77px 1fr 62px;align-items:center;gap:10px;margin:6px 0}.bartrack{height:10px;background:var(--pane2);border-radius:10px;overflow:hidden}.barfill{height:100%;background:linear-gradient(90deg,#299b8d,#72dfc1);min-width:1px;border-radius:10px}.barrow button{background:none;border:0;text-align:left;padding:0;color:var(--muted)}.barrow button:hover{color:var(--accent)}.barrow .num{text-align:right;font-variant-numeric:tabular-nums;color:var(--muted);font-size:12px}
.filters{display:grid;grid-template-columns:2fr 1fr 1fr 1fr 1fr;gap:9px}.field{display:grid;gap:5px;min-width:0}.field label{font-size:11px;color:var(--muted)}.field input,.field select{min-width:0;width:100%}.searchline{display:flex;align-items:center;gap:15px;margin-top:12px;flex-wrap:wrap}.searchline label{color:var(--muted);font-size:12px}.resulthead{display:flex;align-items:center;gap:12px;justify-content:space-between;flex-wrap:wrap;margin:18px 0 10px}.resulthead h2{font-size:16px;margin:0}.results{display:grid;gap:7px}.result{display:grid;grid-template-columns:135px minmax(100px,170px) 135px minmax(0,1fr) 38px;gap:10px;align-items:center;padding:11px 13px;border:1px solid var(--line);border-radius:10px;background:var(--pane)}.result:hover{border-color:var(--accent)}.result .time{color:var(--muted);font-variant-numeric:tabular-nums;font-size:12px}.result .comp{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px;color:var(--blue)}.result .message{white-space:nowrap;text-overflow:ellipsis;overflow:hidden;min-width:0}.result .id{font-size:11px;color:var(--muted);text-align:right}.pager{display:flex;justify-content:space-between;align-items:center;margin:14px 0;gap:10px;flex-wrap:wrap}.pager .buttons{display:flex;gap:7px}.muted{color:var(--muted)}.warning{font-size:12px;color:var(--muted);border-left:3px solid var(--warn);padding:8px 12px;background:var(--pane);margin:15px 0}.status{min-height:22px;color:var(--muted);font-size:12px}mark{background:#5c5432;color:#fff3ac;padding:0 2px;border-radius:3px}.tablewrap{overflow:auto}table{border-collapse:collapse;width:100%;font-size:12px}th,td{border-bottom:1px solid var(--line);padding:9px;text-align:left;vertical-align:top}th{color:var(--muted);font-weight:650}td.num{text-align:right;font-variant-numeric:tabular-nums}.truncate{max-width:450px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.detail-header{display:flex;flex-wrap:wrap;align-items:center;gap:10px}.detail-header h2{margin:0;font-size:20px}.meta{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin:15px 0}.meta>div{min-width:0;word-break:break-word}.meta dt{color:var(--muted);font-size:11px}.meta dd{margin:2px 0;font-size:13px}.code{white-space:pre-wrap;overflow-wrap:anywhere;padding:18px;border:1px solid var(--line);border-radius:11px;background:var(--bg);line-height:1.5;font:12px/1.55 ui-monospace,Consolas,'Cascadia Code',monospace;max-height:75vh;overflow:auto}.rowtools{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:12px}.empty{padding:35px;color:var(--muted);text-align:center;border:1px dashed var(--line);border-radius:10px}.footer{font-size:12px;color:var(--muted);margin-top:20px}.skip{position:absolute;top:-100px}.skip:focus{top:10px;background:var(--pane);padding:10px}
@media(max-width:1100px){.filters{grid-template-columns:repeat(3,1fr)}.metrics{grid-template-columns:repeat(2,1fr)}.result{grid-template-columns:126px 1fr 1fr;gap:6px}.result .message{grid-column:1/-1}.result .id{display:none}}
@media(max-width:700px){.wrap{padding:12px}.cols,.filters{grid-template-columns:1fr}.metrics{grid-template-columns:repeat(2,minmax(0,1fr))}.meta{grid-template-columns:1fr 1fr}.result{grid-template-columns:1fr 1fr}.result .comp{grid-column:1/-1}.result .message{grid-column:1/-1}.result .tag{justify-self:end}}
"""

COMMON_JS = r"""
'use strict';
const A=window.AKUZ_DATA, F={id:0,day:1,time:2,component:3,category:4,request:5,user:6,headline:7,start:8,end:9,chunk:10,pos:11};
const $=id=>document.getElementById(id); const fmt=n=>Number(n).toLocaleString('ru-RU');
const escapeHtml=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const catLabel=c=>A.categories[c]??'прочее';
function humanTime(r){return (A.meta.base_date?new Date(Date.parse(A.meta.base_date+'T00:00:00Z')+r[F.day]*86400000).toISOString().slice(0,10):'D+'+r[F.day])+' '+r[F.time]}
function theme(){document.body.classList.toggle('light');try{localStorage.setItem('akuz-theme',document.body.classList.contains('light')?'light':'dark')}catch(_){}}
try{if(localStorage.getItem('akuz-theme')==='light')document.body.classList.add('light')}catch(_){}
function loadScript(path){return new Promise((ok,fail)=>{const s=document.createElement('script');s.src=path;s.onload=()=>{s.remove();ok()};s.onerror=()=>{s.remove();fail(new Error('Не удалось открыть локальный файл: '+path))};document.head.appendChild(s)})}
function reveal(id){location.href='event.html?id='+id}
"""

INDEX_JS = r"""
'use strict';
const rows=A.rows, number=fmt, per=70;let filtered=[],page=0,searchEpoch=0,lastQuery='',lastMode='quick';
const state={q:'',request:'',component:'',category:'',hour:'',from:'',to:'',full:false};
function summary(){
 $('source').textContent=A.meta.source; $('total').textContent=fmt(A.meta.events);$('lines').textContent=fmt(A.meta.physical_lines);
 $('multi').textContent=fmt(A.meta.continuation_lines);$('components').textContent=fmt(A.meta.component_count);
 $('date-note').textContent=A.meta.base_date?'Дата первой записи: '+A.meta.base_date:'Исходный журнал без календарной даты — показано D+0, D+1 и т. д.';
 $('integrity').textContent='В исходном файле: '+fmt(A.meta.replacement_chars||0)+' символов замены кодировки; '+fmt(A.meta.out_of_order_timestamps||0)+' нарушений порядка времени; '+fmt(A.meta.midnight_rollovers||0)+' переходов через полночь.';
 $('catview').innerHTML=A.category_counts.map(([c,n])=>`<div class="category-row"><span class="tag" data-cat="${escapeHtml(catLabel(c))}">${escapeHtml(catLabel(c))}</span><b>${fmt(n)}</b></div>`).join('');
 const mx=Math.max(1,...A.hourly.map(h=>h[2]));
 $('hours').innerHTML=A.hourly.map(h=>`<div class="barrow"><button data-hour="${escapeHtml(h[0]+' '+h[1])}" title="Фильтровать этот час">D+${h[0]} ${escapeHtml(h[1])}:xx</button><div class="bartrack"><div class="barfill" style="width:${(100*h[2]/mx).toFixed(2)}%"></div></div><div class="num">${fmt(h[2])}</div></div>`).join('');
 $('hours').querySelectorAll('button').forEach(b=>b.addEventListener('click',()=>{$('hour').value=b.dataset.hour;search()}));
 for(const [value,label] of A.components){const option=document.createElement('option');option.value=value;option.textContent=label;$('component').appendChild(option)}
 for(const [code,label] of A.categories.map((label,index)=>[index,label])){const option=document.createElement('option');option.value=String(code);option.textContent=label;$('category').appendChild(option)}
 for(const h of A.hourly){const option=document.createElement('option');option.value=h[0]+' '+h[1];option.textContent='D+'+h[0]+' '+h[1]+':xx';$('hour').appendChild(option)}
 $('patterns').innerHTML=A.patterns.map(p=>`<tr><td class="num">${fmt(p[0])}</td><td>${escapeHtml(p[1])}</td><td><span class="tag" data-cat="${escapeHtml(catLabel(p[2]))}">${escapeHtml(catLabel(p[2]))}</span></td><td class="truncate" title="${escapeHtml(p[3])}"><a href="event.html?id=${p[4]}">${escapeHtml(p[3])}</a></td></tr>`).join('');
 $('durations').innerHTML=A.durations.length?A.durations.map(d=>`<tr><td class="num">${fmt(d[0].toFixed(1))}</td><td>${escapeHtml(d[1])}</td><td>${escapeHtml(d[2])}</td><td><a href="event.html?id=${d[3]}">#${d[3]}</a></td></tr>`).join(''):'<tr><td colspan="4">Нет распознанных длительностей</td></tr>';
 $('requests').innerHTML=A.requests.map(d=>`<tr><td class="truncate" title="${escapeHtml(d[0])}">${escapeHtml(d[0])}</td><td class="num">${fmt(d[1])}</td><td><button data-req="${escapeHtml(d[0])}">Показать</button></td></tr>`).join('');
 $('requests').querySelectorAll('button').forEach(b=>b.addEventListener('click',()=>{$('request').value=b.dataset.req;search();window.scrollTo({top:$('explore').offsetTop,behavior:'smooth'})}));
}
function sync(){state.q=$('q').value.trim().toLocaleLowerCase();state.request=$('request').value.trim().toLocaleLowerCase();state.component=$('component').value;state.category=$('category').value;state.hour=$('hour').value;state.from=$('from').value;state.to=$('to').value;state.full=$('mode-full').checked}
function rowMatches(r){
 if(state.component!==''&&r[F.component]!==Number(state.component))return false;
 if(state.category!==''&&r[F.category]!==Number(state.category))return false;
 if(state.hour!==''&&r[F.day]+' '+r[F.time].slice(0,2)!==state.hour)return false;
 if(state.from&&r[F.time].slice(0,5)<state.from)return false;
 if(state.to&&r[F.time].slice(0,5)>state.to)return false;
 if(state.request&&!r[F.request].toLocaleLowerCase().includes(state.request))return false;
 return true;
}
function quickMatch(r){return !state.q||String(r[F.id]).includes(state.q)||A.componentNames[r[F.component]].toLocaleLowerCase().includes(state.q)||A.categories[r[F.category]].toLocaleLowerCase().includes(state.q)||r[F.headline].toLocaleLowerCase().includes(state.q)||r[F.request].toLocaleLowerCase().includes(state.q)||r[F.user].toLocaleLowerCase().includes(state.q)}
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
 html+=`<a class="result" href="event.html?id=${r[F.id]}" title="Строки ${r[F.start]}–${r[F.end]}"><span class="time">${escapeHtml(t)}</span><span class="comp">${escapeHtml(comp)}</span><span><span class="tag" data-cat="${escapeHtml(c)}">${escapeHtml(c)}</span></span><span class="message">${escapeHtml(r[F.headline])}</span><span class="id">#${r[F.id]}</span></a>`;
 }
 $('results').innerHTML=html||'<div class="empty">Нет событий для этих условий. Сбросьте часть фильтров.</div>';
}
$('find').addEventListener('click',search);$('cancel').addEventListener('click',()=>{searchEpoch++;$('find').disabled=false;$('cancel').hidden=true;$('status').textContent='Поиск отменён. Неполные результаты не показаны.'});
$('clear').addEventListener('click',()=>{for(const id of ['q','request','component','category','hour','from','to'])$(id).value='';$('mode-full').checked=false;search()});
$('prev').addEventListener('click',()=>{page--;render();$('results').scrollIntoView({behavior:'smooth',block:'start'})});
$('next').addEventListener('click',()=>{page++;render();$('results').scrollIntoView({behavior:'smooth',block:'start'})});
$('theme').addEventListener('click',theme);const requested=new URLSearchParams(location.search).get('request');if(requested)$('request').value=requested;$('q').addEventListener('keydown',e=>{if(e.key==='Enter')search()});$('request').addEventListener('keydown',e=>{if(e.key==='Enter')search()});
for(const id of ['component','category','hour','from','to'])$(id).addEventListener('change',search);
summary();search();
"""

EVENT_JS = r"""
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
 <p class="muted">${escapeHtml(humanTime(row))} · строка ${row[F.start]}${row[F.end]>row[F.start]?'–'+row[F.end]:''}</p>
 <dl class="meta"><div><dt>Компонент</dt><dd>${escapeHtml(comp)}</dd></div><div><dt>Request ID</dt><dd>${escapeHtml(row[F.request]||'—')}</dd></div><div><dt>Пользователь</dt><dd>${escapeHtml(row[F.user]||'—')}</dd></div></dl>
 <div class="rowtools"><button id="copy">Копировать исходную запись</button><button id="copy-link">Копировать ссылку</button><a class="btn" href="index.html?request=${encodeURIComponent(row[F.request])}">События Request ID</a><button id="wrap">Перенос строк: вкл.</button></div>
 <div id="copy-status" class="status" role="status"></div><pre id="raw" class="code"></pre>
 <div class="pager"><div class="buttons"><a class="btn${prev?'':' muted'}" href="${prev?'event.html?id='+prev[F.id]:'#'}">← Предыдущее</a><a class="btn${next?'':' muted'}" href="${next?'event.html?id='+next[F.id]:'#'}">Следующее →</a></div><a class="btn" href="index.html">← На главную</a></div>`;
 $('raw').textContent=raw;$('copy').addEventListener('click',()=>copyText(raw));$('copy-link').addEventListener('click',()=>copyText(location.href));
 $('wrap').addEventListener('click',()=>{const p=$('raw'),wrapped=p.style.whiteSpace!=='pre';p.style.whiteSpace=wrapped?'pre':'pre-wrap';$('wrap').textContent='Перенос строк: '+(wrapped?'выкл.':'вкл.')});
}
async function copyText(s){try{await navigator.clipboard.writeText(s);$('copy-status').textContent='Скопировано'}catch(_){$('raw').focus();$('copy-status').textContent='Браузер запретил копирование из file://. Выделите текст и нажмите Ctrl+C.'}}
$('theme').addEventListener('click',theme);start();
"""

INDEX_HTML = r"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AKUZ Log Explorer</title><link rel="stylesheet" href="style.css"></head><body>
<a class="skip" href="#explore">К поиску событий</a><div class="wrap"><header class="top"><div class="brand"><div class="logo">A·</div><div><div class="eyebrow">Offline investigation workspace</div><h1>AKUZ Log Explorer</h1></div></div><div class="actions"><span class="sub">Локально · Данные на вашем ПК</span><button id="theme">◐ Тема</button></div></header>
<section class="hero"><div class="eyebrow">Источник / Source</div><h2 id="source">Загрузка индекса...</h2><p id="date-note" class="note"></p><p id="integrity" class="note"></p></section>
<section class="metrics"><div class="metric"><strong id="total">—</strong><span>Событий</span></div><div class="metric"><strong id="lines">—</strong><span>Физических строк</span></div><div class="metric"><strong id="multi">—</strong><span>Строк продолжений</span></div><div class="metric"><strong id="components">—</strong><span>Компонентов</span></div></section>
<section class="cols"><div class="panel"><h2>Плотность событий по часам</h2><div id="hours"></div><div class="small">Нажатие на час применяет фильтр к списку событий.</div></div><div class="panel"><h2>Текстовые признаки событий</h2><div id="catview"></div><p class="small">Формат лога не содержит уровней INFO/WARN/ERROR. Эти группы — результат поиска слов в сообщении, а не подтверждённая серьёзность или диагноз причины.</p></div></section>
<section class="panel" id="explore" style="margin-top:17px"><h2>Поиск и фильтрация</h2><div class="filters">
<div class="field"><label for="q">Текст / ID события / компонент</label><input id="q" type="search" placeholder="timeout, Socket.Receive, #12345, GUID…" autocomplete="off"></div>
<div class="field"><label for="component">Компонент</label><select id="component"><option value="">Все компоненты</option></select></div>
<div class="field"><label for="category">Текстовый признак</label><select id="category"><option value="">Все признаки</option></select></div>
<div class="field"><label for="hour">День / час</label><select id="hour"><option value="">Любое время</option></select></div>
<div class="field"><label for="request">Request ID / подстрока</label><input id="request" type="search" placeholder="GUID или часть"></div>
<div class="field"><label for="from">Время с (включительно)</label><input id="from" type="time" step="60"></div>
<div class="field"><label for="to">Время до (включительно)</label><input id="to" type="time" step="60"></div>
</div><div class="searchline"><label><input id="mode-full" type="checkbox"> Искать по всему тексту, включая stack trace / XML</label><button id="find" class="primary">Найти</button><button id="cancel" hidden>Отмена</button><button id="clear">Сбросить всё</button></div><div class="status" id="status" role="status"></div></section>
<div class="resulthead"><h2>Журнал · <span id="count">—</span></h2><span class="sub">В исходном порядке · по 70 на странице</span></div><div class="results" id="results"></div>
<div class="pager"><span id="page-info" class="muted"></span><div class="buttons"><button id="prev">← Назад</button><button id="next">Вперёд →</button></div></div>
<div class="cols"><section class="panel"><h2>Повторяющиеся сообщения</h2><div class="small">Нормализация первой строки: GUID, URL и длинные числовые значения скрыты только в шаблоне, исходные записи не изменены.</div><div class="tablewrap"><table><thead><tr><th>Раз</th><th>Компонент</th><th>Признак</th><th>Шаблон / пример</th></tr></thead><tbody id="patterns"></tbody></table></div></section>
<section class="stack"><section class="panel"><h2>Долгие операции</h2><div class="small">Только явно помеченные интервалы «общее время» или «за Nms».</div><div class="tablewrap"><table><thead><tr><th>мс</th><th>Компонент</th><th>Тип</th><th>Событие</th></tr></thead><tbody id="durations"></tbody></table></div></section><section class="panel"><h2>Частые Request ID</h2><div class="tablewrap"><table><thead><tr><th>Request ID</th><th>Событий</th><th></th></tr></thead><tbody id="requests"></tbody></table></div></section></section></div>
<p class="warning">ВНИМАНИЕ: каталог может содержать персональные и медицинские данные, GUID, адреса сервисов и полные запросы. Храните и пересылайте его как исходный журнал. Никаких внешних библиотек, CDN или аналитики в интерфейсе нет. Полнотекстовый поиск последовательно читает локальные части и может занимать время.</p>
<footer class="footer">AKUZ Log Explorer · Локальный статический отчёт · <a href="README_EXPLORER.md">Помощь</a></footer></div>
<script src="data/catalog.js"></script><script src="common.js"></script><script src="index.js"></script></body></html>
"""

EVENT_HTML = r"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Событие · AKUZ Log Explorer</title><link rel="stylesheet" href="style.css"></head><body><div class="wrap"><header class="top"><div class="brand"><div class="logo">A·</div><div><div class="eyebrow">Raw event detail</div><h1>AKUZ Log Explorer</h1></div></div><div class="actions"><a href="index.html" class="btn">← Обзор</a><button id="theme">◐ Тема</button></div></header><main class="panel" id="main"><p class="muted">Загрузка события...</p></main><p class="warning">Текст отображается буквально, без выполнения HTML или скриптов из журнала.</p></div><script src="data/catalog.js"></script><script src="common.js"></script><script src="event.js"></script></body></html>
"""

README = """# AKUZ Log Explorer — автономный HTML-отчёт

## Открыть

Распакуйте архив **целиком** и откройте `index.html` двойным кликом в Chrome / Edge / Firefox.
Не переносите один `index.html` отдельно от `data/`, `common.js`, `index.js`, `event.js`, `style.css`.
**Интернет и локальный HTTP-сервер не требуются.** Логи нигде не публикуются.

## Что работает

- Обзор, категории-текстовые признаки, плотность событий по часам, повторения, длительности, Request ID.
- Быстрый поиск по первой строке сообщения, компоненту, пользователю, Request ID, категории и номеру события.
- Полнотекстовый поиск по **полной исходной записи** (включая stack trace, JSON, XML и продолжения): отметьте флажок и нажмите «Найти». Он читает части с диска последовательно, не загружая все полные события сразу.
- Фильтры по компоненту, категории, дню/часу, локальному времени, Request ID; все применяются совместно.
- Клик по событию открывает `event.html?id=N` с исходной записью и номерами строк, быстрыми переходами к соседним событиям.
- Тема, разбивка по 70 результатов, кнопка отмены полного поиска.

## Ограничения

- Лог не содержит формального INFO/WARN/ERROR; категории получены эвристическим поиском слов и не устанавливают причину инцидента.
- Если дата не была передана, отображаются D+0, D+1 и т. д.; дата **не выдумывается**.
- Границы событий совпадают с распознанными строками заголовка; переводы строк нормализованы, повреждённые UTF-8 символы могли быть заменены. Оригинальный лог сохраните для forensic-проверки.
- Время «с/до» сравнивается в каждом дне по времени суток; граница минуты 12:20 включает все события до 12:20:59.999.
- Во время **полнотекстового** поиска не изменяйте фильтры; если изменили — нажмите «Найти» ещё раз.
- Вызов браузером API clipboard на `file://` может быть запрещён. В таком случае выделите текст и нажмите Ctrl+C.
- Фрагмент события открывается из соответствующего файла `data/raw_XXXXX.js`. Открытие **одного** `index.html` из архива ZIP без распаковки не работает.
- Сам каталог содержит данные пациента и секреты, если они есть в исходном логе. Не размещайте в открытом доступе.

## CLI (Python 3.9+, без pip)

```powershell
# Уже полученный архив от v1:
python akuz_html_explorer.py .\\AKUZ_Report\\events.jsonl.gz -o .\\AKUZ_Explorer

# Из исходного лога, если akuz_log_parser.py лежит рядом:
python akuz_html_explorer.py .\\latest_akuz_sevas.log -o .\\AKUZ_Explorer

# Если реальная дата первой записи установлена по другим источникам:
python akuz_html_explorer.py .\\latest_akuz_sevas.log -o .\\AKUZ_Explorer --date 2026-09-22
```

После генерации откройте `AKUZ_Explorer/index.html`.
"""


def js_json(value: Any) -> str:
    """Produce inert JS data, including malicious-looking log fragments safely."""
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            .replace("<", "\\u003c").replace(">", "\\u003e")
            .replace("&", "\\u0026").replace("\u2028", "\\u2028")
            .replace("\u2029", "\\u2029"))


def read_input(source: Path, base: date | None, stats: Counter[str]) -> Iterator[dict[str, Any]]:
    if source.name.endswith(".jsonl.gz") or source.suffix.lower() == ".jsonl":
        open_file = gzip.open if source.suffix.lower() == ".gz" else open
        with open_file(source, "rt", encoding="utf-8") as src:
            for ln, text in enumerate(src, 1):
                try:
                    ev = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Недопустимый JSONL, строка {ln}: {exc}") from exc
                required = {"event_id", "day_offset", "time", "component", "request_id", "user", "message", "raw", "start_line", "end_line"}
                absent = required - ev.keys()
                if absent:
                    raise ValueError(f"JSONL, строка {ln}: нет полей {', '.join(sorted(absent))}")
                stats["events"] += 1
                yield ev
    else:
        try:
            from akuz_log_parser import event_stream, classify
        except ImportError as exc:
            raise RuntimeError("Для обработки .log положите akuz_log_parser.py рядом с akuz_html_explorer.py") from exc
        from time import perf_counter
        from inspect import signature
        supports_diagnostics = "diagnostics" in signature(classify).parameters
        for ev in event_stream(source, stats):
            stamp = perf_counter()
            # Preserve injected one-argument classifiers for legacy comparisons.
            ev["category"] = (classify(ev["message"], diagnostics=stats)
                              if supports_diagnostics else classify(ev["message"]))
            stats["classify_s"] += perf_counter() - stamp
            stats["events"] += 1
            yield ev


def generate(source: Path, out: Path, base: date | None, chunk_size: int, top: int,
             *, event_source=None, input_bytes=None) -> dict[str, Any]:
    from akuz_log_parser import classify, normalize, extract_duration
    from akuz_analytics import recognize_error
    from akuz_diagnostics import event as perf_event, phase as perf_phase
    from time import perf_counter, thread_time
    from inspect import signature
    supports_diagnostics = "diagnostics" in signature(classify).parameters
    if not source.is_file():
        raise ValueError(f"Исходный файл не найден: {source}")
    source, out = source.resolve(), out.resolve()
    if source.is_relative_to(out):
        raise ValueError("Нельзя выбрать выходной каталог, содержащий исходный файл")
    if chunk_size < 10 or chunk_size > 10000:
        raise ValueError("--chunk-size должен быть в пределах 10–10000")
    data = out / "data"
    data.mkdir(parents=True, exist_ok=True)
    perf_root = out.parent.parent if out.parent.name == 'reports' else out.parent
    started = perf_counter()
    cpu_started = thread_time()
    shard_time = 0.0
    read_time = 0.0
    classify_time = 0.0
    normalize_time = 0.0
    error_time = 0.0
    duration_time = 0.0
    raw_chars = 0
    max_event_chars = 0
    perf_event(perf_root, 'generate.input', 'start',
               input_bytes=source.stat().st_size if input_bytes is None else input_bytes)
    stats: Counter[str] = Counter()
    category = Counter()
    component = Counter()
    hour = Counter()
    request = Counter()
    pattern = Counter()
    pattern_first = {}
    duration = []
    source_ids: dict[str, int] = {}
    sources: list[str] = []
    component_ids: dict[str, int] = {}
    components: list[str] = []
    rows: list[list[Any]] = []
    raw_shard: list[str] = []
    error_fingerprints: dict[str,str] = {}
    prev_end = 0
    replacement_chars = 0
    rollover = 0
    out_of_order = 0
    prev_day = prev_ms = None

    def flush(part: int) -> None:
        nonlocal shard_time
        stamp = perf_counter()
        dest = data / f"raw_{part:05d}.js"
        dest.write_text("window.AKUZ_RAW=" + js_json(raw_shard) + ";\n", encoding="utf-8")
        shard_time += perf_counter() - stamp

    events_iter = iter(read_input(source, base, stats) if event_source is None else event_source)
    while True:
        stamp = perf_counter()
        ev = next(events_iter, None)
        read_time += perf_counter() - stamp
        if ev is None:
            break
        if event_source is not None:
            stats['events'] += 1
        # Preserve an already documented date from a v1 JSONL archive unless
        # the caller explicitly supplies the date for this investigation.
        if base is None and ev.get("date"):
            try:
                base = date.fromisoformat(ev["date"]) - timedelta(days=int(ev["day_offset"]))
            except (ValueError, TypeError) as exc:
                raise ValueError(f"Некорректное поле date у события #{ev['event_id']}") from exc
        n = len(rows)
        if n and n % 50000 == 0:
            src_classify = stats.get("classify_s", 0.0)
            perf_event(perf_root, 'generate.parse', 'progress', events=n,
                       raw_chars=raw_chars, max_event_chars=max_event_chars,
                       elapsed_s=round(perf_counter() - started, 3),
                       source_next_s=round(read_time - src_classify, 3),
                       classify_s=round(classify_time + src_classify, 3),
                       normalize_s=round(normalize_time, 3),
                       errors_s=round(error_time, 3),
                       shard_write_s=round(shard_time, 3),
                       duration_s=round(duration_time, 3),
                       classify_calls=stats["classify_calls"],
                       classify_regex_fallback_events=stats["classify_regex_fallback_events"],
                       classify_literal_path_events=stats["classify_calls"]-stats["classify_regex_fallback_events"])
        eid = ev["event_id"]
        if not isinstance(eid, int) or eid <= 0:
            raise ValueError(f"Недопустимый event_id: {eid}")
        if rows and eid <= rows[-1][0]:
            raise ValueError(f"event_id не возрастают у записи #{eid}")
        if not isinstance(ev["raw"], str):
            raise ValueError(f"Неверный raw для события #{eid}")
        event_chars = len(ev["raw"])
        raw_chars += event_chars
        max_event_chars = max(max_event_chars, event_chars)
        if not isinstance(ev["message"], str):
            raise ValueError(f"Неверный message для события #{eid}")
        if not isinstance(ev["start_line"], int) or not isinstance(ev["end_line"], int) or ev["start_line"] > ev["end_line"]:
            raise ValueError(f"Неверные номера строк для события #{eid}")
        if ev["start_line"] != prev_end + 1:
            raise ValueError(f"Разрыв покрытия строк у события #{eid}: expected {prev_end+1}, got {ev['start_line']}")
        prev_end = ev["end_line"]
        text = ev["message"]
        source_label = ev.get("source_file") or source.name
        if source_label not in source_ids:
            source_ids[source_label] = len(sources)
            sources.append(source_label)
        sid = source_ids[source_label]
        label = ev.get("category")
        if not label:
            stamp = perf_counter()
            label = (classify(text, diagnostics=stats)
                     if supports_diagnostics else classify(text))
            classify_time += perf_counter() - stamp
        if label not in CAT:
            # Future parser categories are kept, not incorrectly collapsed to "прочее".
            CAT_EXTRA.add(label)
        comp = ev["component"]
        if comp not in component_ids:
            component_ids[comp] = len(components)
            components.append(comp)
        cid = component_ids[comp]
        category[label] += 1
        component[comp] += 1
        hour[(ev["day_offset"], ev["time"][:2] if ev["time"] else "??")] += 1
        if ev["request_id"]:
            request[ev["request_id"]] += 1
        stamp = perf_counter()
        norm = normalize(text)
        normalize_time += perf_counter() - stamp
        key = (comp, label, norm)
        pattern[key] += 1
        if key not in pattern_first:
            pattern_first[key] = eid
        stamp = perf_counter()
        dur = extract_duration(text)
        duration_time += perf_counter() - stamp
        if dur is not None:
            millis, kind = dur
            duration.append((millis, comp, kind, eid))
        replacement_chars += ev["raw"].count("\ufffd")
        newline = text.find("\n")
        headline = (text if newline < 0 else text[:newline]).strip()[:1400]
        # The raw JS stores the ORIGINAL raw event, not the normalized headline.
        rows.append([eid, ev["day_offset"], ev["time"], cid, label, ev["request_id"], ev["user"],
                     headline, ev.get("original_start_line", ev["start_line"]),
                     ev.get("original_end_line", ev["end_line"]), n // chunk_size, n % chunk_size, sid])
        stamp = perf_counter()
        match = recognize_error(ev["raw"])
        error_time += perf_counter() - stamp
        if match:
            error_fingerprints[str(eid)] = match["fp"]
        raw_shard.append(ev["raw"])
        if len(raw_shard) == chunk_size:
            flush(n // chunk_size)
            raw_shard.clear()
        t = ev["time"]
        if len(t) >= 12:
            try:
                hh, mm, ss = t.split(":")
                sec, milli = ss.split(".")
                ms = ((int(hh) * 60 + int(mm)) * 60 + int(sec)) * 1000 + int(milli[:3])
                if prev_day is not None and ev["day_offset"] > prev_day:
                    rollover += ev["day_offset"] - prev_day
                elif prev_ms is not None and ms < prev_ms and prev_day == ev["day_offset"]:
                    out_of_order += 1
                prev_ms, prev_day = ms, ev["day_offset"]
            except ValueError:
                pass
    if raw_shard:
        flush((len(rows)-1)//chunk_size)
    total = perf_counter() - started
    thread_cpu = thread_time() - cpu_started
    src_classify = stats.get("classify_s", 0.0)
    classify_total = classify_time + src_classify
    read_total = read_time - src_classify
    other_total = max(0.0, total - shard_time - read_total - classify_total
                      - normalize_time - error_time - duration_time)
    perf_event(perf_root, 'generate.parse', 'done', events=len(rows),
               lines=prev_end, shards=(len(rows)+chunk_size-1)//chunk_size,
               raw_chars=raw_chars, max_event_chars=max_event_chars,
               elapsed_s=round(total, 3), shard_write_s=round(shard_time, 3),
               source_next_s=round(read_total, 3), classify_s=round(classify_total, 3),
               normalize_s=round(normalize_time, 3), errors_s=round(error_time, 3),
               duration_s=round(duration_time, 3), other_s=round(other_total, 3),
               thread_cpu_s=round(thread_cpu, 3),
               classify_calls=stats["classify_calls"],
               classify_regex_fallback_events=stats["classify_regex_fallback_events"],
               classify_literal_path_events=stats["classify_calls"]-stats["classify_regex_fallback_events"])
    if not rows:
        raise ValueError("Нет распознанных событий")
    # A rerun into the same directory must not leave obsolete old event shards
    # with possibly sensitive records that are no longer in the new report.
    actual_shards = (len(rows) + chunk_size - 1) // chunk_size
    for stale in data.glob("raw_*.js"):
        if stale.name[4:-3].isdigit() and int(stale.name[4:-3]) >= actual_shards:
            stale.unlink()
    cats = list(CAT) + sorted(CAT_EXTRA)
    catids = {c: i for i, c in enumerate(cats)}
    for r in rows:
        r[4] = catids[r[4]]
    patterns = [[count, comp, catids[cat], pat, pattern_first[(comp,cat,pat)]]
                for (comp,cat,pat), count in pattern.most_common(top)]
    dur = sorted(duration, key=lambda x: x[0], reverse=True)[:top]
    meta = dict(source=source.name, base_date=str(base) if base else None,
                events=len(rows), physical_lines=prev_end, continuation_lines=prev_end-len(rows),
                component_count=len(components), chunks=(len(rows)+chunk_size-1)//chunk_size,
                chunk_size=chunk_size, replacement_chars=replacement_chars,
                out_of_order_timestamps=out_of_order, midnight_rollovers=rollover)
    catalog = dict(meta=meta, rows=rows, sources=sources, errorFingerprints=error_fingerprints,
                   categories=cats, componentNames=components,
                   components=sorted([[i, comp or "(пустой)"] for comp,i in component_ids.items()],key=lambda p:p[1].casefold()),
                   hourly=[[d,h,c] for (d,h),c in sorted(hour.items())],
                   category_counts=[[catids[k],v] for k,v in category.most_common()],
                   patterns=patterns, durations=dur, requests=request.most_common(15))
    with perf_phase(perf_root, 'generate.catalog', events=len(rows)):
        (data / "catalog.js").write_text("window.AKUZ_DATA="+js_json(catalog)+";\n", encoding="utf-8")
    # One authoritative UI source for the initial page and every generated report.
    # The embedded v2 strings above remain as historical fallback, not a second v4 UI.
    ui = Path(__file__).resolve().parent
    for name, text in (("index.html", (ui/"index.html").read_text(encoding="utf-8")),
                       ("event.html", (ui/"event.html").read_text(encoding="utf-8")),
                       ("style.css", (ui/"style.css").read_text(encoding="utf-8")),
                       ("common.js", (Path(__file__).with_name("common.js")).read_text(encoding="utf-8")),
                       ("index.js", (Path(__file__).with_name("index.js")).read_text(encoding="utf-8")),
                       ("event.js", (Path(__file__).with_name("event.js")).read_text(encoding="utf-8")),
                       ("app_controls.js", (Path(__file__).with_name("app_controls.js")).read_text(encoding="utf-8"))):
        (out / name).write_text(text, encoding="utf-8")
    from akuz_runtime import DOCUMENTS
    for name in DOCUMENTS:
        (out/name).write_text((ui/name).read_text(encoding="utf-8"), encoding="utf-8")
    perf_event(perf_root, 'generate.assets', 'done', elapsed_s=round(perf_counter()-started, 3),
               events=len(rows))
    return meta


# v3: preserve offline reader and add a button only enabled on localhost.
FETCH_PANEL = r"""<section class="panel" id="fetch-panel" style="margin-bottom:17px">
<div class="fetchbar"><div><div class="eyebrow">SSH / Windows / Local · коллекция журналов · v4.5.0</div><h2 style="font-size:18px;margin:4px 0">Журналы по датам</h2>
<p class="small">Выбери один или несколько файлов, проверь дату из имени и открой отдельные отчёты или общую выборку.</p>
<div id="fetch-status" class="status" role="status">Проверка локального сервиса…</div></div>
<div class="actions"><label class="small">Источник <select id="fetch-source" aria-label="Источник журналов"><option value="linux">Linux · SSH</option><option value="windows">Windows · SMB / UNC</option><option value="local">Локальный .log / папка</option></select></label><button class="primary" id="fetch-latest">↓ Последний лог</button><button id="fetch-list">↻ Список файлов</button><button id="picker-toggle" type="button" aria-controls="remote-picker" aria-expanded="false" hidden>▾ Показать файлы</button><button id="fetch-cache" title="Удалить скачанные файлы кэша; готовые отчёты сохраняются">⌫ Очистить кэш</button><a class="btn" id="fetch-open" href="#" hidden>Открыть отчёт →</a></div></div>
<div id="local-path-panel" class="local-source" hidden><label for="local-path">Путь к локальному .log или папке журналов на компьютере, где запущен Explorer</label><input id="local-path" type="text" placeholder="C:\AKUZ\Logs или C:\AKUZ\Logs\20260923_server.log" autocomplete="off" spellcheck="false" maxlength="2048"><p class="small">Укажите полный путь. Только файлы .log, без вложенных каталогов; исходные файлы не меняются. UNC-папки — через Windows · SMB.</p></div>
<div id="remote-picker" hidden><div class="pickerbar"><span id="picker-count" class="sub"></span><label>Дата изменения на сервере с <input type="date" id="picker-from"></label><label>по <input type="date" id="picker-to"></label><button id="picker-today">Сбросить даты</button><button id="picker-select-all">Выбрать видимые</button><button id="picker-select-none">Снять выделение</button></div>
<div class="filelist" id="picker-files"></div><div class="pickerbar"><button class="primary" id="picker-build">Создать отчёты по выбранным файлам</button><span class="small">Дата первой записи берётся из имени YYYYMMDD_*.log. Её можно исправить вручную.</span></div></div>
<div id="report-library" class="library" hidden><h3>Мои отчёты</h3><div id="report-items"></div></div>
</section>"""
INDEX_HTML = INDEX_HTML.replace('<section class="hero">', FETCH_PANEL+'<section class="hero">', 1)
INDEX_HTML = INDEX_HTML.replace('<div class="field"><label for="request">', '<div class="field"><label for="source-file">Файл журнала</label><select id="source-file"><option value="">Все файлы</option></select></div><div class="field"><label for="day-file">Дата / день</label><select id="day-file"><option value="">Все даты</option></select></div><div class="field"><label for="request">', 1)
INDEX_HTML = INDEX_HTML.replace('<script src="data/catalog.js"></script>',
                                '<script src="app_controls.js"></script><script src="data/catalog.js"></script>', 1)
CSS += "\n.fetchbar{display:flex;align-items:center;justify-content:space-between;gap:20px;flex-wrap:wrap}.fetchbar .small{margin:3px 0}.fetchbar .status{font-weight:600;margin-top:9px}.fetchbar .actions{max-width:100%}\n"

CSS += r"""
.pickerbar{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:16px 0}.pickerbar label{color:var(--muted);font-size:12px;display:flex;align-items:center;gap:5px}.filelist{max-height:420px;overflow:auto;border:1px solid var(--line);border-radius:10px}.fileitem{display:grid;grid-template-columns:22px minmax(190px,1fr) 155px 88px 175px;align-items:center;gap:12px;padding:11px;border-bottom:1px solid var(--line)}.fileitem:last-child{border-bottom:0}.fileitem .name{overflow-wrap:anywhere;font-weight:600}.fileitem .sub{display:block}.fileitem input[type=date]{max-width:155px}.library{border-top:1px solid var(--line);margin-top:17px;padding-top:12px}.library h3{font-size:14px}.library-item{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:7px 0;border-bottom:1px solid var(--line);flex-wrap:wrap}.library-item a{overflow-wrap:anywhere}.fetchbar{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap}
@media(max-width:700px){.fileitem{grid-template-columns:22px minmax(0,1fr);gap:8px}.fileitem .name{grid-column:2}.fileitem .sizedate{grid-column:2}.fileitem .firstdate{grid-column:2}}
"""
CSS += r"""
.library-report-group{border:1px solid var(--line);border-radius:10px;margin:10px 0;overflow:hidden}
.library-report-heading{display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;padding:10px 12px;background:var(--pane2)}
.library-report-heading strong{overflow-wrap:anywhere}
.library-report-group .library-item{padding:9px 12px}
.library-report-group .library-item:last-child{border-bottom:0}
.library-current{color:var(--accent);border-color:var(--accent)}

.local-source{display:grid;gap:7px;margin-top:14px;max-width:100%}
.local-source[hidden]{display:none}
.local-source label{font-size:12px;color:var(--muted)}
.local-source input{width:100%;font-family:ui-monospace,Consolas,monospace}
.local-source .small{margin:0}
"""
CAT_EXTRA: set[str] = set()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="AKUZ .log / events.jsonl.gz → локальный HTML Log Explorer")
    p.add_argument("input", type=Path, help="Исходный .log или events.jsonl[.gz] парсера v1")
    p.add_argument("-o", "--out", type=Path, help="Каталог результата")
    p.add_argument("--date", type=date.fromisoformat, help="Подтверждённая дата первой записи YYYY-MM-DD")
    p.add_argument("--chunk-size", type=int, default=1000, help="Событий в части полного текста, 10–10000")
    p.add_argument("--top", type=int, default=35, help="Строк в таблицах повторов и длительностей")
    args = p.parse_args(argv)
    if args.top < 1:
        p.error("--top должен быть >0")
    dest = args.out or args.input.with_name(args.input.stem.replace(".jsonl", "") + "_explorer")
    try:
        meta = generate(args.input, dest, args.date, args.chunk_size, args.top)
    except (ValueError, RuntimeError, OSError) as e:
        p.exit(2, f"ERROR: {e}\n")
    print(f"OK: {meta['events']:,} events, {meta['physical_lines']:,} physical lines, {meta['chunks']} detail shards")
    print(f"OPEN: {(dest / 'index.html').resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
