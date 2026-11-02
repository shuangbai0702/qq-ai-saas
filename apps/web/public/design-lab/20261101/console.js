const $=(s,r=document)=>r.querySelector(s);
const $$=(s,r=document)=>[...r.querySelectorAll(s)];
const clamp=(v,a,b)=>Math.max(a,Math.min(b,v));
export function createConsole({studio,state,icon,avatar,toast,atelier,machinePos,updateMachine}){
  const life=new AbortController(),springs=new Map(),effects=new Set(),records=[];
  let disposed=false,layout=null,selected=null,lastProjection='',lastStatus='',sequence=0;
  const on=(el,event,fn,opts={})=>el?.addEventListener(event,fn,{...opts,signal:life.signal});
  document.body.classList.add('console-lab');
  document.title='01 · 清爽控制台 / 程序化交互实验';
  $('.intro').innerHTML=`<div class="intro-copy"><p class="eyebrow">CONTROL SYSTEM / INTERACTION LAB <span class="edition">01 / R4</span></p><h1 id="hero-heading">控制台交互实验<span class="intro-status"><i></i> LOCAL PREVIEW</span></h1><p class="intro-text">清晰的状态，直接的控制。拖动节点、逐步执行、回看轨迹，观察每一次变化如何传递。</p></div><div class="intro-tools"><button id="command-open">${icon('grid')}快速操作 <kbd>Ctrl K</kbd></button><span>大肥鱼 · DeepSeek 娘主题 / 组件样本</span></div>`;
  $('.brand b').innerHTML='FishBot<span>机器人控制台 / 元素实验</span>';
  $('.intro').insertAdjacentHTML('afterend',`<div class="console-rail"><nav aria-label="实验导航"><a href="#topology" class="active">处理平面</a><a href="#trace-panel">执行轨迹</a><a href="#specimens">组件实验</a></nav><div class="rail-summary"><span><i></i><b id="rail-status">示例在线</b></span><span><b id="enabled-total">03</b> 能力</span><span><b id="completed-total">00</b> 已完成</span></div></div>`);
  $('.scene-board').id='topology';
  $('.scene-heading h2').insertAdjacentHTML('afterend','<span class="inspection-label" id="inspection-label" hidden>历史状态投影</span>');
  $('.scene-shell').insertAdjacentHTML('beforeend',`<div class="routing-readout"><span><i></i><b id="routing-label">等待测试消息</b></span><span id="routing-detail">连接心跳 · 非真实业务流量</span><span class="route-count"><b id="route-count">03</b> / ENABLED</span></div>`);
  $('.scene-area').insertAdjacentHTML('beforeend',`<div class="core-caption"><b>MODEL CORE</b><span id="core-caption-state">IDLE / CONNECTED</span></div><span class="stage-note">拖动能力节点 · 线路实时重算</span>`);
  $('.scene-controls .control-kicker').innerHTML='SIMULATION / CONTROL '+icon('grid');
  $('.scene-controls h3').textContent='过程控制';
  $('.scene-controls>p').innerHTML='自动运行，或在下方切换手动步进。<br>参数仅影响这次本地演示。';
  $('#energy').value=state.energy;$('#energy-out').value=state.energy;$('#energy').style.setProperty('--value',state.energy+'%');studio.setEnergy(state.energy/100);
  $('[data-copy="motion"]').textContent='执行速率';$('[data-copy="density"]').textContent='背景关联范围';
  $('.scene-controls .range-field:nth-of-type(1) small').innerHTML='慢 <i></i> 快';
  $('.scene-controls .range-field:nth-of-type(2) small').innerHTML='当前组件 <i></i> 全部关联';
  $('.motion-control b').textContent='视图动态';$('#motion-toggle').setAttribute('aria-label','视图动态');
  $('#reset-scene').innerHTML=icon('grid')+'自动整理节点';
  $('#reset-scene').onclick=null;
  $('.material-note').innerHTML='<span>SIMULATION BOUNDARY</span><small>演示数据 · 不调用模型或业务 API</small>';
  $('.scene-caption>span').textContent='拖拽 / 自动布局 / 单步执行 / 快照回看';
  $('.world-key').insertAdjacentHTML('afterend',`<section class="trace-panel" id="trace-panel" aria-label="执行轨迹与状态快照"><div class="trace-toolbar"><span class="inspector-title">${icon('link')}执行观察器 <small>LOCAL TRACE</small></span><label class="manual-toggle"><input type="checkbox" id="manual-mode">手动步进</label><button id="trace-next" disabled>${icon('chevron')}下一步</button><button id="trace-run">${icon('play')}运行测试</button><button id="trace-clear" aria-label="清空执行轨迹">${icon('reset')}</button></div><div class="inspector-grid"><div class="trace-side"><div class="trace-meta"><span>阶段 / 回执 <b id="trace-count">0 EVENTS</b></span><span>点击阶段查看快照</span></div><div class="trace-log" id="trace-log" role="log" aria-label="本地演示事件"><div class="trace-empty"><span>↳</span>尚无执行轨迹。运行一次测试，或开启手动步进。</div></div></div><div class="snapshot-side"><div class="snapshot-head"><span id="snapshot-title">当前状态 / LIVE</span><button id="snapshot-live" aria-pressed="true">回到当前</button></div><pre id="state-projection" aria-label="演示状态快照"></pre></div></div></section>`);
  $('.section-heading h2').innerHTML='组件实验<span>09 个可交互样本</span>';
  $('.section-heading .eyebrow').textContent='COMPONENTS / ADJUST · INSPECT · VERIFY';
  $('.section-index').innerHTML='01 — 09';
  $('.personality').id='persona-card';$('.conversation').id='message-card';$('.resources').id='monitor-card';
  $('.closing h2').innerHTML='状态清楚，<em>过程可控。</em>';
  $('.closing>div>span:last-child').textContent='CONTROL / OBSERVE / VERIFY';
  $('#footer-theme').textContent='01 / CONTROL LAB · R4';
  $('.identity-top .handwritten').textContent='INSTANCE / SHARED';
  const positions=[[24,24],[50,18],[76,24]];
  function moveNodes(values){values.forEach(([x,y],i)=>Object.assign(machinePos[i],{x,y}));updateMachine();}
  moveNodes(positions);
  function autoLayout(){
    if(!studio.running){moveNodes(positions);return;}
    layout=positions.map(([x,y],i)=>({x:machinePos[i].x,y:machinePos[i].y,vx:0,vy:0,tx:x,ty:y}));
    toast('节点按能力顺序重新排布，连接保持同步。');
  }
  on($('#reset-scene'),'click',autoLayout);
  $$('[data-machine]').forEach(node=>{on(node,'pointerdown',()=>layout=null);on(node,'keydown',e=>{if(e.key.startsWith('Arrow'))layout=null;},{capture:true});});
  function projection(){return {instance:['bot-alpha','bot-beta','bot-gamma'][state.bot],online:state.online,capabilities:{memory:state.capabilities[0],attention:state.capabilities[1],initiative:state.capabilities[2]},persona:{tone:state.warm,explore:state.curious,expression:state.chatty},memoryIndex:state.memory,phase:studio.context.phase,completed:studio.context.activity};}
  function showSnapshot(value,title,live=false){$('#state-projection').textContent=JSON.stringify(value,null,2);$('#snapshot-title').textContent=title;$('#snapshot-live').setAttribute('aria-pressed',String(live));}
  function returnLive(){selected=null;studio.inspection=null;lastProjection='';studio.dirty=true;$('#inspection-label').hidden=true;renderLog();}
  function renderLog(){
    const root=$('#trace-log');root.replaceChildren();
    for(const record of records){const button=document.createElement('button');button.className='trace-event '+record.kind;button.dataset.trace=record.key;button.setAttribute('aria-pressed',String(record.key===selected));const index=document.createElement('span');index.className='trace-id';index.textContent='#'+String(record.id).padStart(3,'0');const name=document.createElement('b');name.textContent=record.label;const time=document.createElement('small');time.textContent='+'+record.elapsed+' ms';button.append(index,name,time);root.append(button);}root.scrollTop=root.scrollHeight;
    $('#trace-count').textContent=records.length+' EVENTS';
  }
  on(document,'lab:trace',e=>{
    const data=e.detail;
    if(data.kind==='waiting'){$('#routing-label').textContent='等待手动步进';return;}
    const record={...data,key:String(++sequence),snapshot:{...projection(),run:data.id,instance:data.bot,capabilities:{memory:data.caps[0],attention:data.caps[1],initiative:data.caps[2]},persona:{...data.persona},memoryIndex:data.memoryIndex,memorySource:data.memory}};
    records.push(record);if(records.length>16)records.shift();if(selected&&!records.some(r=>r.key===selected))selected=null;
    renderLog();
  });
  on($('#trace-log'),'click',e=>{const target=e.target.closest('[data-trace]');if(!target)return;selected=target.dataset.trace;const record=records.find(r=>r.key===selected);studio.inspection={...record.snapshot,phaseIndex:record.phase,phase:record.label,activity:record.snapshot.completed,time:record.elapsed/1000};studio.dirty=true;$('#inspection-label').hidden=false;showSnapshot(record.snapshot,'快照 / #'+String(record.id).padStart(3,'0')+' · '+record.label);renderLog();});
  on($('#snapshot-live'),'click',returnLive);
  on($('#manual-mode'),'change',e=>atelier.setManual(e.target.checked));
  on($('#trace-next'),'click',()=>atelier.step());
  on($('#trace-run'),'click',()=>atelier.run());
  on($('#trace-clear'),'click',()=>{records.length=0;returnLive();$('#trace-log').innerHTML='<div class="trace-empty">执行轨迹已清空。</div>';$('#trace-count').textContent='0 EVENTS';});
  on(document,'pointerdown',e=>{if(selected!==null&&e.target.closest('.scene-controls,.specimen,[data-machine]'))returnLive();});
  on(document,'input',e=>{if(selected!==null&&e.target.closest('.scene-controls,.specimen'))returnLive();});
  // Short spring presses share the scene clock. No independent per-control frame loops.
  const spring=el=>{if(!springs.has(el))springs.set(el,{x:1,v:0,target:1});return springs.get(el);};
  on(document,'pointerdown',e=>{const el=e.target.closest('button');if(!el||el.disabled||el.matches('[data-drag]'))return;const v=spring(el);v.target=.965;v.v=-.8;});
  const release=()=>{for(const s of springs.values())s.target=1;};on(document,'pointerup',release);on(document,'pointercancel',release);on(window,'blur',release);
  on(document,'keydown',e=>{if([' ','Enter'].includes(e.key)&&e.target.matches('button')&&!e.target.disabled){const s=spring(e.target);s.x=.97;s.v=.5;}});
  function motionChanged(){document.body.classList.toggle('motion-paused',!studio.running);if(!studio.running){for(const [el] of springs)el.style.removeProperty('scale');springs.clear();if(layout){moveNodes(positions);layout=null;}}}
  on($('#motion-toggle'),'click',motionChanged);on(studio.reduced,'change',motionChanged);
  // Keyboard operations act on the same visible controls, never hidden production actions.
  document.body.insertAdjacentHTML('beforeend',`<dialog id="command-dialog"><div class="command-heading"><span>快速操作</span><kbd>ESC</kbd></div><input id="command-search" placeholder="搜索本地操作…" aria-label="搜索快速操作"><div id="command-results"></div><p>全部操作仅作用于当前样板</p></dialog>`);
  const commands=[['运行本地测试',()=>atelier.run()],['自动整理节点',autoLayout],['切换手动步进',()=>$('#manual-mode').click()],['聚焦人格参数',()=>$('#warm').focus()],['切换视图动态',()=>$('#motion-toggle').click()]];
  function renderCommands(){const query=$('#command-search').value.trim();const root=$('#command-results');root.replaceChildren();commands.forEach(([name],i)=>{if(query&&!name.includes(query))return;const button=document.createElement('button');button.dataset.command=i;button.innerHTML=icon('chevron');button.append(document.createTextNode(name));root.append(button);});if(!root.children.length)root.textContent='没有匹配操作';}
  function openCommands(){const dialog=$('#command-dialog');if(dialog.open)return;$('#command-search').value='';renderCommands();dialog.showModal();$('#command-search').focus();}
  on($('#command-open'),'click',openCommands);on(document,'keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();$('#command-dialog').open?$('#command-dialog').close():openCommands();}});
  on($('#command-search'),'input',renderCommands);on($('#command-search'),'keydown',e=>{if(e.key==='ArrowDown'){e.preventDefault();$('#command-results button')?.focus();}if(e.key==='Enter'){$('#command-results button')?.click();}});
  on($('#command-results'),'click',e=>{const button=e.target.closest('[data-command]');if(!button)return;$('#command-dialog').close();commands[+button.dataset.command][1]();});
  on($('#command-dialog'),'click',e=>{if(e.target===$('#command-dialog')){const r=e.target.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)e.target.close();}});
  const off=studio.onFrame((dt,motion)=>{
    if(disposed)return;
    if(motion){
      for(const [el,s] of springs){s.v+=((s.target-s.x)*300-s.v*20)*dt;s.x+=s.v*dt;el.style.scale=String(s.x);if(Math.abs(s.x-1)<.0001&&Math.abs(s.v)<.001&&s.target===1){el.style.removeProperty('scale');springs.delete(el);}}
      if(layout){let settled=true;for(const [i,n] of layout.entries()){n.vx+=((n.tx-n.x)*115-n.vx*17)*dt;n.vy+=((n.ty-n.y)*115-n.vy*17)*dt;n.x+=n.vx*dt;n.y+=n.vy*dt;Object.assign(machinePos[i],{x:n.x,y:n.y});if(Math.hypot(n.x-n.tx,n.y-n.ty)>.01)settled=false;}updateMachine();if(settled){moveNodes(positions);layout=null;}}
    }
    $('#trace-next').disabled=!atelier.waiting;
    const busy=studio.context.phaseIndex>=0,waiting=atelier.waiting,status=[state.online,busy,waiting,studio.context.phase,studio.context.activity,state.capabilities.join(''),selected].join('/');
    if(status!==lastStatus){
      $('#rail-status').textContent=state.online?'示例在线':'实例已停止';$('.rail-summary').classList.toggle('offline',!state.online);
      $('#enabled-total').textContent=String(state.capabilities.filter(Boolean).length).padStart(2,'0');$('#route-count').textContent=$('#enabled-total').textContent;
      $('#completed-total').textContent=String(studio.context.activity).padStart(2,'0');
      const view=studio.inspection,phase=view?.phaseIndex??studio.context.phaseIndex;
      $('#routing-label').textContent=view?'历史快照 · '+view.phase:waiting?'等待手动步进':busy?studio.context.phase:state.online?'等待测试消息':'实例已停止';
      $('#routing-detail').textContent=view?'只读投影 · 当前运行状态保留':busy?'处理阶段 · 本地模拟':'连接心跳 · 非真实业务流量';
      $('#core-caption-state').textContent=view?'INSPECT / HISTORICAL':busy?'PHASE / '+String(phase+1).padStart(2,'0'):state.online?'IDLE / CONNECTED':'STOPPED / RETAINED';
      const caps=view?Object.values(view.capabilities):state.capabilities;
      $('#route-count').textContent=String(caps.filter(Boolean).length).padStart(2,'0');
      $$('[data-machine]').forEach((e,i)=>{e.classList.toggle('phase-active',phase>=0&&i===phase);e.classList.toggle('route-disabled',!caps[i]);});
      $('#trace-run').innerHTML=icon(busy?'pause':'play')+(busy?'中止测试':'运行测试');
      document.body.classList.toggle('is-processing',busy);lastStatus=status;
    }
    if(selected===null){const value=projection(),json=JSON.stringify(value);if(lastProjection!==json){lastProjection=json;showSnapshot(value,'当前状态 / LIVE',true);}}
  });
  motionChanged();studio.dirty=true;
  return {dispose(){if(disposed)return;disposed=true;off();life.abort();for(const effect of effects)effect.cancel();effects.clear();springs.clear();layout=null;$('#command-dialog').close();}};
}
