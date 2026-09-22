'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const fmt = n => Number(n || 0).toLocaleString('ru-RU');
  const state = {selected:'', scale:'day', bucket:'', from:'', to:'', page:0, detail:null};
  const data = window.AKUZ_ANALYTICS;
  const el=(tag,cls,value)=>{
    const e=document.createElement(tag);
    if(cls)e.className=cls;
    if(value!==undefined)e.textContent=String(value);
    return e;
  };
  function matchesDate(day){return (!state.from||day>=state.from)&&(!state.to||day<=state.to);}
  function seriesFor(detail){
    const rows=(state.scale==='day'?detail.days:detail.hours)
      .filter(x=>matchesDate(x[0].slice(0,10)));
    if(!rows.length)return [];
    const grouped=new Map(rows), start=Date.parse(rows[0][0].slice(0,10)+'T00:00:00Z'),
          end=Date.parse(rows[rows.length-1][0].slice(0,10)+'T00:00:00Z');
    const span=Math.round((end-start)/86400000)+1;
    if(span<=120)for(let t=start;t<=end;t+=86400000){
      const day=new Date(t).toISOString().slice(0,10);
      if(state.scale==='day'){if(!grouped.has(day))grouped.set(day,0);}
      else for(let h=0;h<24;h++){
        const label=day+' '+String(h).padStart(2,'0');
        if(!grouped.has(label))grouped.set(label,0);
      }
    }
    return [...grouped.entries()].sort((a,b)=>a[0].localeCompare(b[0])).slice(-240);
  }
  function chart(where,points){
    if(!points.length){where.appendChild(el('div','empty','Нет событий с подтверждённой датой.'));return;}
    const wrapper=el('div','error-chart-scroll');
    const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
    const width=Math.max(700,points.length*39+58),max=Math.max(1,...points.map(p=>p[1]));
    svg.setAttribute('viewBox','0 0 '+width+' 240');svg.setAttribute('width',width);
    svg.setAttribute('height',240);svg.setAttribute('role','img');
    svg.setAttribute('aria-label','Диаграмма ошибок. Столбец фильтрует события по дате или часу.');
    function part(tag,attrs){
      const n=document.createElementNS('http://www.w3.org/2000/svg',tag);
      for(const [k,v] of Object.entries(attrs))n.setAttribute(k,String(v));
      svg.appendChild(n);return n;
    }
    for(let i=0;i<=4;i++){
      const y=199-40*i;
      part('line',{x1:40,y1:y,x2:width-5,y2:y,stroke:'var(--line)'});
      const text=part('text',{x:34,y:y+4,fill:'var(--muted)','text-anchor':'end','font-size':10});
      text.textContent=fmt(Math.round(max*i/4));
    }
    const every=Math.max(1,Math.ceil(points.length/13));
    points.forEach(([stamp,n],i)=>{
      const x=47+i*39,h=n?Math.max(2,Math.floor(157*n/max)):1;
      const r=part('rect',{x:x,y:199-h,width:26,height:h,rx:4,role:'button',
        tabindex:0,fill:stamp===state.bucket?'var(--blue)':'var(--accent)',opacity:n?1:.25});
      const title=document.createElementNS('http://www.w3.org/2000/svg','title');
      title.textContent=stamp+' · '+fmt(n);r.appendChild(title);
      const select=()=>{state.bucket=state.bucket===stamp?'':stamp;state.page=0;renderDetail();};
      r.addEventListener('click',select);
      r.addEventListener('keydown',ev=>{if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();select();}});
      if(i%every===0){
        const label=part('text',{x:x+13,y:218,fill:'var(--muted)',
          'text-anchor':'middle','font-size':10});
        label.textContent=stamp.slice(5);
      }
    });
    wrapper.appendChild(svg);where.appendChild(wrapper);
  }
  function renderEvents(where,detail){
    const rows=detail.items.filter(x=>{
      if(x.day&&!matchesDate(x.day))return false;
      if((state.from||state.to)&&!x.day)return false;
      if(state.bucket){
        const key=state.scale==='day'?x.day:x.day+' '+String(x.clock||'').slice(0,2);
        if(key!==state.bucket)return false;
      }
      return true;
    });
    const header=el('div','analytics-tablebar');
    header.appendChild(el('strong',null,'Исходные события · '+fmt(rows.length)));
    if(state.bucket){
      const b=el('button',null,'Снять фильтр '+state.bucket);
      b.onclick=()=>{state.bucket='';state.page=0;renderDetail();};header.appendChild(b);
    }
    where.appendChild(header);
    if(!rows.length){where.appendChild(el('p','muted','Для выбранного периода записей нет.'));return;}
    const table=el('table','analytics-table'),thead=el('thead'),tr=el('tr');
    for(const s of ['Дата и время','Отчёт','Событие'])tr.appendChild(el('th',null,s));
    thead.appendChild(tr);table.appendChild(thead);
    const body=el('tbody'),start=state.page*50;
    for(const r of rows.slice(start,start+50)){
      const tr=el('tr');
      tr.appendChild(el('td',null,(r.day||'Без календарной даты')+' '+(r.clock||'')));
      tr.appendChild(el('td','sub',r.report_id));
      const td=el('td'),a=el('a',null,'#'+r.event_id+' ↗');
      a.href='reports/'+encodeURIComponent(r.report_id)+'/event.html?id='+encodeURIComponent(r.event_id);
      a.target='_blank';a.rel='noopener';td.appendChild(a);tr.appendChild(td);
      if(r.ambiguous)tr.appendChild(el('td','warning','Пересечение снимков не подтверждено'));
      body.appendChild(tr);
    }
    table.appendChild(body);const scroller=el('div','tablewrap');
    scroller.appendChild(table);where.appendChild(scroller);
    const footer=el('div','pager');
    footer.appendChild(el('span','muted',fmt(start+1)+'–'+fmt(Math.min(start+50,rows.length))+' из '+fmt(rows.length)));
    const actions=el('div','actions');
    for(const [text,delta] of [['← Назад',-1],['Вперёд →',1]]){
      const b=el('button',null,text);
      b.disabled=delta<0?state.page===0:start+50>=rows.length;
      b.onclick=()=>{state.page+=delta;renderDetail();};actions.appendChild(b);
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
    const cards=el('div','analytics-stats');
    for(const [label,value] of [['Совпадений',d.total],['С датой',d.dated],
                                 ['Неопределённых',d.ambiguous],
                                 ['Без даты',d.total-d.dated-d.ambiguous]]){
      const c=el('div','analytics-stat');c.appendChild(el('strong',null,fmt(value)));
      c.appendChild(el('span','sub',label));cards.appendChild(c);
    }
    host.appendChild(cards);
    const toolbar=el('div','analytics-toolbar');
    for(const [value,label] of [['day','По дням'],['hour','По часам']]){
      const b=el('button',state.scale===value?'primary':'',label);
      b.onclick=()=>{state.scale=value;state.bucket='';state.page=0;renderDetail();};
      toolbar.appendChild(b);
    }
    for(const [value,label] of [['from','С'],['to','По']]){
      const wrap=el('label','small',label+' '),inp=el('input');inp.type='date';inp.value=state[value];
      inp.onchange=()=>{state[value]=inp.value;state.bucket='';state.page=0;renderDetail();};
      wrap.appendChild(inp);toolbar.appendChild(wrap);
    }
    host.appendChild(toolbar);
    const values=seriesFor(d);
    host.appendChild(el('p','small',
      'Нажмите столбец для списка событий. Если период очень длинный, показаны последние 240 интервалов.'));
    chart(host,values);
    renderEvents(host,d);
    if(d.total-d.dated-d.ambiguous>0)host.appendChild(
      el('p','small','Недатированные события доступны в таблице, но не отображаются на календарном графике.'));
  }
  function renderGroups(){
    const target=$('error-groups');target.replaceChildren();
    const q=$('error-search').value.trim().toLocaleLowerCase(),family=$('error-family').value;
    const groups=data.groups.filter(g=>(!family||g.family===family)&&
      (!q||[g.exception,g.template,g.method,g.family].join(' ').toLocaleLowerCase().includes(q)));
    $('groups-found').textContent='Групп: '+fmt(groups.length);
    for(const g of groups){
      const b=el('button','analytics-group'+(state.selected===g.fp?' active':''));
      b.appendChild(el('strong',null,g.exception));
      b.appendChild(el('span','sub',g.template));
      const footer=el('div','analytics-group-footer');
      footer.appendChild(el('span','tag',g.family));
      footer.appendChild(el('span','analytics-count',fmt(g.total)+' совпадений'));
      b.appendChild(footer);b.onclick=()=>choose(g.fp);target.appendChild(b);
    }
    if(!groups.length)target.appendChild(el('div','empty','Не найдено подходящих ошибок.'));
  }
  async function choose(fp){
    if(!/^[0-9a-f]{24}$/.test(fp))return;
    state.selected=fp;state.filter='';state.bucket='';state.from='';state.to='';state.page=0;
    renderGroups();$('error-detail').replaceChildren(el('p','muted','Загрузка группы…'));
    try{
      window.AKUZ_ERROR_DETAIL=null;
      await new Promise((ok,fail)=>{
        const script=document.createElement('script');script.src='data/error_'+fp+'.js';
        script.onload=()=>{script.remove();ok();};
        script.onerror=()=>{script.remove();fail(Error('Не удалось загрузить файл аналитики группы.'));};
        document.head.appendChild(script);
      });
      if(state.selected!==fp)return;
      if(!window.AKUZ_ERROR_DETAIL||window.AKUZ_ERROR_DETAIL.fp!==fp)throw Error('Неверный индекс группы');
      state.detail=window.AKUZ_ERROR_DETAIL;window.AKUZ_ERROR_DETAIL=null;
      renderDetail();
      const u=new URL(location.href);u.searchParams.set('fp',fp);history.replaceState({},'',u);
    }catch(e){$('error-detail').textContent=String(e.message||e);}
  }
  $('theme').onclick=()=>{
    document.body.classList.toggle('light');
    try{localStorage.setItem('akuz-theme',document.body.classList.contains('light')?'light':'dark');}catch(_){}
  };
  try{if(localStorage.getItem('akuz-theme')==='light')document.body.classList.add('light');}catch(_){}
  if(!data){
    $('analytics-status').textContent='Индекс пока отсутствует. Создайте отчёт или откройте эту страницу через локальный Explorer.';
    $('error-groups').textContent='Нет готовой аналитики.';return;
  }
  $('metric-errors').textContent=fmt(data.distinct_errors);
  $('metric-groups').textContent=fmt(data.groups.length);
  $('metric-reports').textContent=fmt(data.report_count);
  $('metric-ambiguous').textContent=fmt(data.ambiguous);
  $('analytics-status').textContent='Только обработанные отчёты AKUZ · совпадающие исходные записи учитываются один раз.'+
    (data.date_conflicts?' Внимание: источников с противоречащими датами первой записи: '+fmt(data.date_conflicts)+'. Они исключены из календарного графика.':'');
  $('error-search').oninput=renderGroups;$('error-family').onchange=renderGroups;
  renderGroups();
  const requested=new URLSearchParams(location.search).get('fp')||'';
  if(data.groups.length)choose(data.groups.some(g=>g.fp===requested)?requested:data.groups[0].fp);
  else $('error-detail').replaceChildren(el('div','empty','В отчётах не обнаружены распознанные ошибки.'));
})();
