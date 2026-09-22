'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const fmt = n => Number(n || 0).toLocaleString('ru-RU');
  const data = window.AKUZ_ANALYTICS;
  const state = {
    selected:'',mode:'type',scale:'day',temporal:'calendar',
    bucket:'',from:'',to:'',page:0,detail:null,loading:0
  };
  const el=(tag,cls,value)=>{
    const e=document.createElement(tag);
    if(cls)e.className=cls;
    if(value!==undefined)e.textContent=String(value);
    return e;
  };
  const matchesDate=day=>(!state.from||day>=state.from)&&(!state.to||day<=state.to);
  const collection=()=>state.mode==='type'?(data.types||data.groups):
                      state.mode==='family'?(data.families||[]):data.groups;
  const isLocal=/^(127\.0\.0\.1|localhost)$/.test(location.hostname)&&location.protocol==='http:';

  function seriesFor(detail){
    if(state.temporal==='relative'){
      return (state.scale==='day'?detail.relative_days:detail.relative_hours).slice(-240);
    }
    const source=state.scale==='day'?detail.days:detail.hours;
    const rows=source.filter(x=>matchesDate(x[0].slice(0,10)));
    if(!rows.length)return [];
    const grouped=new Map(rows);
    const first=Date.parse(rows[0][0].slice(0,10)+'T00:00:00Z');
    const last=Date.parse(rows[rows.length-1][0].slice(0,10)+'T00:00:00Z');
    if(Math.round((last-first)/86400000)<=120){
      for(let t=first;t<=last;t+=86400000){
        const day=new Date(t).toISOString().slice(0,10);
        if(state.scale==='day'){
          if(!grouped.has(day))grouped.set(day,0);
        }else{
          for(let hour=0;hour<24;hour++){
            const key=day+' '+String(hour).padStart(2,'0');
            if(!grouped.has(key))grouped.set(key,0);
          }
        }
      }
    }
    return [...grouped.entries()].sort((a,b)=>a[0].localeCompare(b[0])).slice(-240);
  }

  function chart(where,points){
    if(!points.length){
      where.appendChild(el('div','empty',state.temporal==='calendar'
        ?'Нет событий с подтверждённой календарной датой. Назначьте даты источникам выше или переключитесь на дни журнала.'
        :'В выбранной группе нет интервалов для графика.'));
      return;
    }
    const wrap=el('div','error-chart-scroll');
    const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
    const width=Math.max(700,points.length*39+58);
    const maximum=Math.max(1,...points.map(x=>x[1]));
    svg.setAttribute('viewBox','0 0 '+width+' 240');
    svg.setAttribute('width',width);
    svg.setAttribute('height',240);
    svg.setAttribute('role','img');
    svg.setAttribute('aria-label','Диаграмма ошибок по выбранному масштабу; столбцы открывают список исходных событий');
    function part(tag,attrs){
      const node=document.createElementNS('http://www.w3.org/2000/svg',tag);
      for(const [key,value] of Object.entries(attrs))node.setAttribute(key,String(value));
      svg.appendChild(node);
      return node;
    }
    for(let i=0;i<=4;i++){
      const y=199-40*i;
      part('line',{x1:40,y1:y,x2:width-5,y2:y,stroke:'var(--line)'});
      part('text',{x:35,y:y+4,fill:'var(--muted)','text-anchor':'end',
                    'font-size':11}).textContent=fmt(Math.round(maximum*i/4));
    }
    const every=Math.max(1,Math.ceil(points.length/13));
    points.forEach(([stamp,count],i)=>{
      const x=47+i*39;
      const height=count?Math.max(2,Math.floor(157*count/maximum)):1;
      const rect=part('rect',{x,y:199-height,width:26,height,rx:4,
        tabindex:0,role:'button',fill:stamp===state.bucket?'var(--blue)':'var(--accent)',
        opacity:count?1:.25});
      const tooltip=document.createElementNS('http://www.w3.org/2000/svg','title');
      tooltip.textContent=stamp+' · '+fmt(count);
      rect.appendChild(tooltip);
      const pick=()=>{state.bucket=state.bucket===stamp?'':stamp;state.page=0;renderDetail();};
      rect.addEventListener('click',pick);
      rect.addEventListener('keydown',ev=>{
        if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();pick();}
      });
      if(i%every===0){
        part('text',{x:x+13,y:218,fill:'var(--muted)',
          'text-anchor':'middle','font-size':10}).textContent=
            state.temporal==='relative'?stamp:(stamp.slice(5));
      }
    });
    wrap.appendChild(svg);
    where.appendChild(wrap);
  }

  function renderEvents(where,detail){
    const rows=detail.items.filter(row=>{
      if(state.temporal==='calendar'){
        if(row.day&&!matchesDate(row.day))return false;
        if((state.from||state.to)&&!row.day)return false;
      }
      if(state.bucket){
        const bucket=state.temporal==='relative'?'D+'+row.relative_day:row.day;
        const key=state.scale==='day'?bucket:bucket+' '+String(row.clock||'').slice(0,2);
        if(key!==state.bucket)return false;
      }
      return true;
    });
    const header=el('div','analytics-tablebar');
    header.appendChild(el('strong',null,'Исходные события · '+fmt(rows.length)));
    if(state.bucket){
      const clear=el('button',null,'Снять фильтр: '+state.bucket);
      clear.onclick=()=>{state.bucket='';state.page=0;renderDetail();};
      header.appendChild(clear);
    }
    where.appendChild(header);
    if(!rows.length){
      where.appendChild(el('p','muted','Для выбранного периода записей нет. Сбросьте даты или выберите относительное время.'));
      return;
    }
    const table=el('table','analytics-table'),thead=el('thead'),headRow=el('tr');
    for(const label of ['Дата / день и время','Отчёт','Событие'])
      headRow.appendChild(el('th',null,label));
    thead.appendChild(headRow);table.appendChild(thead);
    const body=el('tbody'),start=state.page*50;
    for(const row of rows.slice(start,start+50)){
      const tr=el('tr');
      tr.appendChild(el('td',null,(row.day||'D+'+row.relative_day+' (дата не задана)')+
                               ' '+(row.clock||'')));
      tr.appendChild(el('td','sub',row.report_id));
      const td=el('td'),a=el('a',null,'#'+row.event_id+' ↗');
      a.href='reports/'+encodeURIComponent(row.report_id)+'/event.html?id='+encodeURIComponent(row.event_id);
      a.target='_blank';a.rel='noopener';
      td.appendChild(a);tr.appendChild(td);
      body.appendChild(tr);
    }
    table.appendChild(body);
    const scroller=el('div','tablewrap');scroller.appendChild(table);where.appendChild(scroller);
    const footer=el('div','pager');
    footer.appendChild(el('span','muted',fmt(start+1)+'–'+fmt(Math.min(start+50,rows.length))+
                                ' из '+fmt(rows.length)));
    const actions=el('div','actions');
    for(const [label,delta] of [['← Назад',-1],['Вперёд →',1]]){
      const button=el('button',null,label);
      button.disabled=delta<0?state.page===0:start+50>=rows.length;
      button.onclick=()=>{state.page+=delta;renderDetail();};
      actions.appendChild(button);
    }
    footer.appendChild(actions);where.appendChild(footer);
  }

  function renderDetail(){
    const host=$('error-detail');host.replaceChildren();
    const d=state.detail;if(!d)return;
    host.appendChild(el('div','eyebrow',d.family));
    host.appendChild(el('h2','analytics-title',d.exception));
    host.appendChild(el('p','muted',d.template));
    if(d.method)host.appendChild(el('p','small','Первый кадр stack trace: '+d.method));
    if(state.mode!=='exact')
      host.appendChild(el('p','analytics-relative-note',
        'Объединение по типу/семейству показывает похожие события. Для одной конкретной ошибки выберите «По точному шаблону».'));
    const cards=el('div','analytics-stats');
    for(const [label,count] of [['Совпадений',d.total],['С датой',d.dated],
      ['Неопределённых',d.ambiguous],
      ['Без даты',d.total-d.dated-d.ambiguous]]){
      const card=el('div','analytics-stat');
      card.appendChild(el('strong',null,fmt(count)));
      card.appendChild(el('span','sub',label));
      cards.appendChild(card);
    }
    host.appendChild(cards);
    const toolbar=el('div','analytics-toolbar');
    for(const [mode,label] of [['day','По дням'],['hour','По часам']]){
      const button=el('button',state.scale===mode?'primary':'',label);
      button.onclick=()=>{state.scale=mode;state.bucket='';state.page=0;renderDetail();};
      toolbar.appendChild(button);
    }
    const timeMode=el('select');
    for(const [mode,label] of [['calendar','Календарные даты'],['relative','Дни/часы журнала (D+N)']]){
      const option=el('option',null,label);option.value=mode;timeMode.appendChild(option);
    }
    timeMode.value=state.temporal;
    timeMode.onchange=()=>{state.temporal=timeMode.value;state.bucket='';state.page=0;renderDetail();};
    const timeLabel=el('label','small','Ось времени ');
    timeLabel.appendChild(timeMode);toolbar.appendChild(timeLabel);
    for(const [key,label] of [['from','С'],['to','По']]){
      const wrap=el('label','small',label+' '),input=el('input');
      input.type='date';input.value=state[key];
      input.disabled=state.temporal!=='calendar';
      input.onchange=()=>{state[key]=input.value;state.bucket='';state.page=0;renderDetail();};
      wrap.appendChild(input);toolbar.appendChild(wrap);
    }
    host.appendChild(toolbar);
    host.appendChild(el('p','small',
      state.temporal==='relative'
        ?'D+0 — первый день внутри каждого журнала. Для нескольких файлов это не единая календарная ось. Подтвердите даты источников для сравнения по календарю.'
        :'Нажмите столбец для списка событий. Если период длинный, показаны последние 240 интервалов.'));
    chart(host,seriesFor(d));
    renderEvents(host,d);
  }

  function renderGroups(){
    const target=$('error-groups');target.replaceChildren();
    const query=$('error-search').value.trim().toLocaleLowerCase();
    const family=$('error-family').value;
    const groups=collection().filter(g=>(!family||g.family===family)&&
      (!query||[g.exception,g.template,g.method,g.family].join(' ').toLocaleLowerCase().includes(query)));
    $('groups-found').textContent='Групп: '+fmt(groups.length);
    for(const g of groups){
      const button=el('button','analytics-group'+(state.selected===g.fp?' active':''));
      button.type='button';
      button.title=g.exception+' · '+g.template+' · '+fmt(g.total)+' событий';
      button.setAttribute('aria-label',button.title);
      button.appendChild(el('strong',null,g.exception));
      button.appendChild(el('span','sub',g.template));
      const footer=el('div','analytics-group-footer');
      footer.appendChild(el('span','tag',g.family));
      footer.appendChild(el('span','analytics-count',fmt(g.total)+' событий'));
      button.appendChild(footer);
      button.onclick=()=>choose(g.fp,state.mode);
      target.appendChild(button);
    }
    if(!groups.length)
      target.appendChild(el('div','empty','Нет подходящих групп. Измените поиск или тип группировки.'));
  }

  async function choose(fp,mode){
    if(!/^[0-9a-f]{24}$/.test(fp))return;
    state.selected=fp;state.mode=mode;state.bucket='';state.from='';state.to='';
    state.page=0;state.detail=null;
    $('group-mode').value=mode;
    renderGroups();
    $('error-detail').replaceChildren(el('p','muted','Загрузка группы…'));
    const sequence=++state.loading;
    try{
      window.AKUZ_ERROR_DETAIL=null;
      await new Promise((ok,fail)=>{
        const script=document.createElement('script');
        const prefix=mode==='exact'?'error':mode;
        script.src='data/'+prefix+'_'+fp+'.js';
        script.onload=()=>{script.remove();ok();};
        script.onerror=()=>{script.remove();fail(Error('Не удалось загрузить индекс группы. Обновите аналитику через локальный Explorer.'));};
        document.head.appendChild(script);
      });
      if(sequence!==state.loading||state.selected!==fp||state.mode!==mode)return;
      const detail=window.AKUZ_ERROR_DETAIL;
      if(!detail||detail.fp!==fp)throw Error('Некорректный индекс группы');
      state.detail=detail;window.AKUZ_ERROR_DETAIL=null;
      state.temporal=detail.dated?'calendar':'relative';
      renderDetail();
      const url=new URL(location.href);
      url.search='';
      url.searchParams.set(mode==='exact'?'fp':mode,fp);
      history.replaceState({},'',url);
    }catch(err){
      if(sequence===state.loading)
        $('error-detail').textContent=String(err.message||err);
    }
  }

  async function showSources(){
    const panel=$('date-sources'),status=$('date-status');
    if(!isLocal){
      status.textContent='Назначение дат доступно через запущенный START_EXPLORER.bat: откройте http://127.0.0.1:8765/errors.html. HTML-отчёты остаются доступными офлайн.';
      return;
    }
    try{
      const response=await fetch('/api/analytics/sources',{cache:'no-store'});
      if(!response.ok)throw Error('HTTP '+response.status);
      const result=await response.json();
      panel.replaceChildren();
      if(!result.sources.length){
        status.textContent='Сохранённых источников пока нет. Сначала создайте отчёт.';
        return;
      }
      const missing=result.sources.filter(x=>!x.date).length;
      status.textContent='Источников: '+fmt(result.sources.length)+
        ' · Без даты первой записи: '+fmt(missing)+
        '. Проверяйте дату по самому журналу или правилам ротации, а не по mtime.';
      for(const source of result.sources){
        const row=el('div','analytics-source');
        const name=el('div','source-name');
        name.appendChild(el('strong',null,source.name));
        name.appendChild(el('span','sub',source.host+' · '+source.remote_path));
        name.appendChild(el('span','sub','Сохранено в отчётах: '+source.reports.length+
          (source.conflict?' · конфликт дат':'')));
        row.appendChild(name);
        const field=el('label','small','Дата первой записи');
        const picker=el('input');picker.type='date';picker.value=source.date||'';
        field.appendChild(picker);row.appendChild(field);
        const save=el('button','primary',source.date?'Изменить':'Подтвердить');
        save.disabled=!picker.value;
        picker.oninput=()=>{save.disabled=!picker.value;};
        save.onclick=async()=>{
          if(!picker.value)return;
          save.disabled=true;
          status.textContent='Пересчитываю календарные интервалы без скачивания логов…';
          try{
            const response=await fetch('/api/analytics/source-date',{
              method:'POST',headers:{'Content-Type':'application/json'},
              body:JSON.stringify({id:source.id,date:picker.value})
            });
            const result=await response.json();
            if(!response.ok)throw Error(result.error||'HTTP '+response.status);
            location.reload();
          }catch(err){
            status.textContent='Ошибка сохранения даты: '+String(err.message||err);
            save.disabled=false;
          }
        };
        row.appendChild(save);panel.appendChild(row);
      }
    }catch(err){status.textContent='Не удалось получить источники: '+String(err.message||err);}
  }

  $('date-toggle').onclick=()=>{
    const panel=$('date-sources');
    panel.hidden=!panel.hidden;
    $('date-toggle').textContent=panel.hidden?'Назначить даты ↴':'Свернуть даты ↟';
    if(!panel.hidden)showSources();
  };
  $('theme').onclick=()=>{
    document.body.classList.toggle('light');
    try{localStorage.setItem('akuz-theme',document.body.classList.contains('light')?'light':'dark');}catch(_){}
  };
  try{if(localStorage.getItem('akuz-theme')==='light')document.body.classList.add('light');}catch(_){}

  if(!data){
    $('analytics-status').textContent='Индекс отсутствует. Откройте аналитику через локальный Explorer после создания отчёта.';
    $('error-groups').textContent='Нет готовой аналитики.';
    return;
  }
  $('metric-errors').textContent=fmt(data.distinct_errors);
  $('metric-groups').textContent=fmt((data.types||data.groups).length);
  $('metric-reports').textContent=fmt(data.report_count);
  $('metric-ambiguous').textContent=fmt(data.ambiguous);
  const dated=data.groups.reduce((n,g)=>n+Number(g.dated||0),0);
  $('analytics-status').textContent='Только обработанные отчёты AKUZ · точные шаблоны и более широкая группировка разделены.'+
    (dated===0&&data.distinct_errors?
       ' Календарные даты не назначены: показана относительная временная шкала D+N. Откройте «Назначить даты» выше.':'')+
    (data.date_conflicts?' Конфликтов дат источников: '+fmt(data.date_conflicts)+'.':'');
  $('group-mode').onchange=()=>{
    state.mode=$('group-mode').value;
    state.selected='';state.detail=null;renderGroups();
    const first=collection()[0];
    if(first)choose(first.fp,state.mode);
    else $('error-detail').textContent='Нет подходящих групп.';
  };
  $('error-search').oninput=renderGroups;
  $('error-family').onchange=renderGroups;
  renderGroups();
  const query=new URLSearchParams(location.search);
  let mode='type',id=query.get('type');
  if(query.get('fp')){mode='exact';id=query.get('fp');}
  else if(query.get('family')){mode='family';id=query.get('family');}
  state.mode=mode;
  $('group-mode').value=mode;
  const list=collection();
  const selected=list.some(g=>g.fp===id)?id:(list[0]||{}).fp;
  if(selected)choose(selected,mode);
  else $('error-detail').replaceChildren(el('div','empty','В отчётах не обнаружены распознанные ошибки.'));
  if(!dated&&data.distinct_errors){
    $('date-sources').hidden=false;
    $('date-toggle').textContent='Свернуть даты ↟';
    showSources();
  }
})();
