
'use strict';
const A=window.AKUZ_DATA, F={id:0,day:1,time:2,component:3,category:4,request:5,user:6,headline:7,start:8,end:9,chunk:10,pos:11,source:12};
const $=id=>document.getElementById(id); const fmt=n=>Number(n).toLocaleString('ru-RU');
const escapeHtml=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const catLabel=c=>A.categories[c]??'прочее';
function humanTime(r){return (A.meta.base_date?new Date(Date.parse(A.meta.base_date+'T00:00:00Z')+r[F.day]*86400000).toISOString().slice(0,10):'D+'+r[F.day])+' '+r[F.time]}
function theme(){document.body.classList.toggle('light');try{localStorage.setItem('akuz-theme',document.body.classList.contains('light')?'light':'dark')}catch(_){}}
try{if(localStorage.getItem('akuz-theme')==='light')document.body.classList.add('light')}catch(_){}
function loadScript(path){return new Promise((ok,fail)=>{const s=document.createElement('script');s.src=path;s.onload=()=>{s.remove();ok()};s.onerror=()=>{s.remove();fail(new Error('Не удалось открыть локальный файл: '+path))};document.head.appendChild(s)})}
function reveal(id){location.href='event.html?id='+id}
