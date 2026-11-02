import {clamp} from './art.js';

const $=(s,r=document)=>r.querySelector(s);
const $$=(s,r=document)=>[...r.querySelectorAll(s)];
const descriptions={
 tide:{title:'背景跟随当前操作，路由跟随节点位置。',note:'拖动能力节点重排线路；心跳表示示例在线，彩色信号只在测试处理中流动。下方组件被关注时，页级线路标出它的关联方向。',action:'发送测试消息',key:['呼吸环 · 在线心跳','彩色包 · 当前处理','细连线 · 组件关联']},
 post:{title:'细密刻度，逐阶段的操作回执。',note:'控制节点可以拖动。停用的能力不参与测试消息；活动线路和回执只在操作发生时更新。',action:'运行测试消息',key:['连线 · 可用能力','墨点 · 当前步骤','回执 · 操作结果']},
 bloom:{title:'从模块、上下文到统一输出。',note:'模块矩阵反映当前参与状态。下方索引与记忆卡片同步选择，固定记录会显示标记；没有与操作无关的跟随装饰。',action:'运行测试消息',key:['模块 · 运行职责','矩阵 · 当前阶段','标记 · 固定记录']}
};
const names={identity:'机器人身份',personality:'人格',conversation:'对话',memory:'记忆',capability:'能力',resources:'运行曲线',stickers:'媒体',rhythm:'免打扰',states:'过程状态',hero:'控制平面'};
const selectors={hero:'.scene-board',identity:'.identity',personality:'.personality',conversation:'.conversation',memory:'.memory-card',capability:'.capability',resources:'.resources',stickers:'.stickers',rhythm:'.rhythm',states:'.states'};

export function createAtelier(env){
  const {studio,state,bots,memories,avatar,icon,bubble,reply,toast,updateRadar,updateMemory,updateClock}=env;
  const life=new AbortController();
  let disposed=false,operation=null,manual=false,epoch=0,replyId=0,selectedSource='personality',savedPersona={warm:state.warm,curious:state.curious,chatty:state.chatty};
  let memoryUndo=null,night=false,pressure=0,pressureVelocity=0,pressureTarget=0,maxPressure=0,holding=false,chartPlays=false,chartTime=0;
  const pinned=new Set();
  const on=(el,type,fn,opts={})=>el?.addEventListener(type,fn,{...opts,signal:life.signal});
  const personaKeys=['warm','curious','chatty'];

  $('.scene-heading').insertAdjacentHTML('afterend',`<div class="scene-story"><span class="story-label">当前处理状态</span><b id="world-status" role="status">本地示例已就绪，等待操作。</b><span class="story-phase" id="world-phase">AT EASE</span></div>`);
  $('.scene-caption').insertAdjacentHTML('afterend',`<div class="world-key"><p id="world-explanation"></p><div id="world-symbols"></div></div>`);
  $('.scene-bottom').insertAdjacentHTML('beforeend','<span class="world-coordinate" id="world-coordinate">关联 · 人格 → 控制平面</span>');
  $('.personality .card-description').textContent='拖动雷达节点，试调表达风格并收藏配置。';
  $('.personality-body').insertAdjacentHTML('beforebegin',`<div class="persona-presets" role="group" aria-label="人格预设">${[['soft','简洁稳重'],['curious','均衡交流'],['lively','主动表达']].map(([k,v])=>`<button data-persona-preset="${k}" aria-pressed="false">${v}</button>`).join('')}</div>`);
  $('.personality').insertAdjacentHTML('beforeend',`<div class="persona-save"><span id="persona-draft">正在试调 · 草稿</span><button id="persona-undo" aria-label="撤回人格调整">${icon('reset')}</button><button id="persona-save">收藏当前风格 ${icon('check')}</button></div>`);
  $('#radar').insertAdjacentHTML('beforeend',personaKeys.map(k=>`<circle class="radar-handle" data-radar="${k}" r="5" tabindex="0" role="slider" aria-label="${{warm:'语气',curious:'探索',chatty:'表达'}[k]}雷达调节" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${state[k]}"/>`).join(''));
  $('.memory-detail').insertAdjacentHTML('beforeend',`<div class="memory-tools"><button id="memory-pin" aria-pressed="false">${icon('star')}固定当前记录</button><button id="memory-edit">编辑这段记忆 ${icon('chevron')}</button></div><form id="memory-editor" hidden><label for="memory-text">编辑当前索引记录</label><textarea id="memory-text" rows="2" maxlength="160"></textarea><div><span>仅保留在这次预览中</span><button type="button" id="memory-cancel">收起</button><button type="submit">保存 ${icon('check')}</button></div></form><div id="memory-receipt" hidden><span>记录已更新。</span><button id="memory-undo">撤销</button></div>`);
  $('.capability .card-description').textContent='节点可独立开关；测试消息会按当前能力逐步处理。';
  $('.flow-diagram').insertAdjacentHTML('afterend',`<div class="flow-paper"><span id="flow-ticket">WAITING / LOCAL TEST</span><span class="ticket-bars" aria-hidden="true">▎▏▎▍▏▍▏▎▍</span></div>`);
  $('.resources').insertAdjacentHTML('beforeend',`<div class="chart-playback"><button id="chart-play" aria-pressed="false">${icon('play')}回放这条曲线</button><span id="chart-event">移动查看采样 · 仅为示例</span></div>`);
  $('.identity').insertAdjacentHTML('beforeend',`<button class="hug-button" id="hug" aria-label="按住验证操作确认">${icon('heart')}<span id="hug-label">按住确认 · 仅演示</span><i><b id="hug-fill"></b></i></button>`);
  $('.clock-art>svg').insertAdjacentHTML('beforeend','<circle id="quiet-handle" r="6" tabindex="0" role="slider" aria-label="安静时段时钟调节" aria-valuemin="19" aria-valuemax="24" aria-valuenow="23"/>');
  $('.rhythm').insertAdjacentHTML('beforeend',`<div class="light-preview" role="group" aria-label="环境光线预览"><span>环境光线预览</span><button data-time="day" aria-pressed="true">日间</button><button data-time="night" aria-pressed="false">晚间</button></div>`);
  $('.identity').insertAdjacentHTML('beforeend',`<div class="identity-foot"><span id="identity-note">当前实例配置与连接状态分别保存。</span><span class="mini-seal">LOCAL ONLY</span></div>`);
  $$('.flow-node').forEach((n,i)=>n.insertAdjacentHTML('beforeend',`<span class="node-receipt" data-node-receipt="${i}">待命</span>`));
  $$('.machine-node').forEach((n,i)=>{$('b',n).textContent=['召回记忆','话题注意力','主动联系'][i];});
  const anchors={};
  function measure(){if(disposed)return;for(const [key,sel] of Object.entries(selectors)){const r=$(sel).getBoundingClientRect();anchors[key]={x:r.x,y:r.y,width:r.width,height:r.height};}studio.setAnchors(anchors);}
  const resize=new ResizeObserver(measure);resize.observe($('#app'));
  on(window,'scroll',measure,{passive:true});on(window,'resize',measure,{passive:true});

  function sync(source=selectedSource,target='hero',sendSignal=false){
    if(disposed)return;selectedSource=source;
    studio.setContext({source,target,selected:state.memory,online:state.online,night,color:bots[state.bot].color,pinned:[...pinned],memories:memories.map(m=>({name:m.name,info:m.info}))});
    document.body.dataset.worldState=state.online?(operation?'thinking':'calm'):'resting';
    $('#world-coordinate').textContent=`关注 · ${names[source]} → ${names[target]}`;
    if(sendSignal)studio.signal(source,target);
    updateRadarHandles();updateClockHandle();updateMemoryTools();
    $('#identity-note').textContent=state.online?`${bots[state.bot].name} · 配置与连接分别维护。`:'实例停止，配置与记忆保留。';
    $('.annotation-left b').textContent=state.online?`${bots[state.bot].name} · 实例运行中`:`${bots[state.bot].name} · 已停止`;
    $('.annotation-left>span:last-child').textContent='配置、记忆、消息与处理阶段联动';
    if(!operation)$('#motion-status').textContent=studio.running?(state.online?'等待测试消息':'已停止'):'时间暂停';
    measure();
  }
  function tell(text,source=selectedSource,target='hero'){if(disposed)return;$('#world-status').textContent=text;sync(source,target,true);}
  function onTheme(){
    const d=descriptions[state.theme];$('#world-explanation').textContent=d.note;$('#world-symbols').innerHTML=d.key.map((v,i)=>`<span><i class="symbol-${i}"></i>${v}</span>`).join('');
    $('[data-copy="action"]').textContent=d.action;
    $('[data-copy="motion"]').textContent='执行速率';$('[data-copy="density"]').textContent='背景关联范围';
    $('[data-copy="hint"]').textContent='拖动能力节点 · 选择底部记忆 · 运行消息';
    $('.scene-controls>p').innerHTML='自动运行，或在下方切换手动步进。<br>参数仅影响这次本地演示。';
    $('.scene-caption p>span:last-child').textContent=d.title;
    $('#reset-scene').innerHTML=icon('grid')+'自动整理节点';
    $('.intro .edition').textContent='ONLY 01 / R4';
    $('.world-key').dataset.world=state.theme;sync();
  }

  for(const [key,sel] of Object.entries(selectors)){
    if(key==='hero')continue;const card=$(sel);card.dataset.material=key;
    on(card,'pointerenter',()=>{card.classList.add('is-inspected');if(!operation)sync(key,'hero');});
    on(card,'pointerleave',()=>card.classList.remove('is-inspected'));
    on(card,'focusin',()=>{if(!operation)sync(key,'hero');});
    on(card,'pointermove',e=>{const r=card.getBoundingClientRect(),x=(e.clientX-r.left)/r.width,y=(e.clientY-r.top)/r.height;card.style.setProperty('--mx',x*100+'%');card.style.setProperty('--my',y*100+'%');card.style.setProperty('--look-x',(x-.5)*2+'px');card.style.setProperty('--look-y',(y-.5)*1.5+'px');},{passive:true});
  }

  function updateRadarHandles(){
    const axes={warm:[90,18],curious:[22,135],chatty:[158,135]};
    for(const k of personaKeys){const el=$(`[data-radar="${k}"]`),v=state[k]/100;el.setAttribute('cx',90+(axes[k][0]-90)*v);el.setAttribute('cy',96+(axes[k][1]-96)*v);el.setAttribute('aria-valuenow',state[k]);}
    const dirty=personaKeys.some(k=>state[k]!==savedPersona[k]);$('#persona-draft').textContent=dirty?'正在试调 · 草稿':'已收藏 · 本地配置';$('#persona-save').disabled=!dirty;
    for(const b of $$('[data-persona-preset]'))b.setAttribute('aria-pressed',String(personaKeys.every(k=>state[k]===presets[b.dataset.personaPreset][k])));
  }
  function setPersona(values){for(const k of personaKeys){const input=$('#'+k);input.value=values[k];input.dispatchEvent(new Event('input',{bubbles:true}));}sync('personality');}
  const presets={soft:{warm:91,curious:36,chatty:23},curious:{warm:67,curious:92,chatty:52},lively:{warm:78,curious:74,chatty:88}};
  $$('[data-persona-preset]').forEach(b=>on(b,'click',()=>{setPersona(presets[b.dataset.personaPreset]);b.setAttribute('aria-pressed','true');tell('表达风格已更新，可运行测试消息查看输出差异。','personality','conversation');}));
  personaKeys.forEach(k=>on($('#'+k),'input',()=>sync('personality')));
  on($('#persona-save'),'click',()=>{savedPersona=Object.fromEntries(personaKeys.map(k=>[k,state[k]]));sync('personality');tell('当前风格已保存到当前预览。','personality');$('#persona-save').classList.remove('ink-stamped');void $('#persona-save').offsetWidth;$('#persona-save').classList.add('ink-stamped');});
  on($('#persona-undo'),'click',()=>{setPersona(savedPersona);tell('已撤回到保存时的配置。','personality');});
  for(const key of personaKeys){const node=$(`[data-radar="${key}"]`);let dragId=null;
    const change=e=>{const r=$('#radar').getBoundingClientRect(),x=(e.clientX-r.left)/r.width*180-90,y=(e.clientY-r.top)/r.height*170-96,axis={warm:[0,-78],curious:[-68,39],chatty:[68,39]}[key];const value=clamp(Math.round((x*axis[0]+y*axis[1])/(axis[0]**2+axis[1]**2)*100),0,100);const input=$('#'+key);input.value=value;input.dispatchEvent(new Event('input',{bubbles:true}));};
    on(node,'pointerdown',e=>{if(e.button!==0)return;e.preventDefault();dragId=e.pointerId;node.setPointerCapture(dragId);node.classList.add('dragging');change(e);});
    on(node,'pointermove',e=>{if(e.pointerId===dragId)change(e);});
    const end=()=>{dragId=null;node.classList.remove('dragging');};on(node,'pointerup',end);on(node,'pointercancel',end);
    on(node,'keydown',e=>{let v=state[key];if(['ArrowUp','ArrowRight'].includes(e.key))v+=e.shiftKey?10:2;else if(['ArrowDown','ArrowLeft'].includes(e.key))v-=e.shiftKey?10:2;else if(e.key==='Home')v=0;else if(e.key==='End')v=100;else return;e.preventDefault();$('#'+key).value=clamp(v,0,100);$('#'+key).dispatchEvent(new Event('input',{bubbles:true}));});
  }

  function updateMemoryTools(){
    const yes=pinned.has(state.memory);$('#memory-pin').setAttribute('aria-pressed',String(yes));$('#memory-pin').innerHTML=icon(yes?'check':'star')+(yes?'已固定当前记录':'固定当前记录');
    $$('.memory-node').forEach((n,i)=>n.classList.toggle('memory-pinned',pinned.has(i)));
    $('#memory-undo').disabled=!memoryUndo;
  }
  function selectMemory(i,source='memory'){
    state.memory=i;const m=memories[i];$('#memory-tag').textContent=m.tag;$('#memory-source').textContent=m.source;$('#memory-info').textContent=m.info;$('#memory-editor').hidden=true;updateMemory();
    tell(`当前记录「${m.name}」：控制平面与索引同步选择。`,source,'hero');
  }
  $$('[data-memory]').forEach((n,i)=>on(n,'click',()=>{if(+n.dataset.memory===state.memory)selectMemory(i);}));
  on($('#memory-pin'),'click',()=>{const i=state.memory;if(pinned.has(i))pinned.delete(i);else pinned.add(i);sync('memory');tell(pinned.has(i)?`「${memories[i].name}」已固定，控制平面显示固定标记。`:'已取消固定，记录内容保留。','memory');$('#memory-pin').classList.remove('ink-stamped');void $('#memory-pin').offsetWidth;$('#memory-pin').classList.add('ink-stamped');});
  on($('#memory-edit'),'click',()=>{const editor=$('#memory-editor');editor.hidden=!editor.hidden;$('#memory-text').value=memories[state.memory].info;if(!editor.hidden)$('#memory-text').focus();});
  on($('#memory-cancel'),'click',()=>$('#memory-editor').hidden=true);
  on($('#memory-editor'),'submit',e=>{e.preventDefault();const text=$('#memory-text').value.trim();if(!text){$('#memory-text').focus();return;}const i=state.memory;memoryUndo={i,info:memories[i].info};memories[i].info=text;$('#memory-info').textContent=text;$('#memory-editor').hidden=true;$('#memory-receipt').hidden=false;sync('memory');tell('记录已更新。启用记忆能力时，后续测试将显示对应来源。','memory','conversation');});
  on($('#memory-undo'),'click',()=>{if(!memoryUndo)return;const {i,info}=memoryUndo;memories[i].info=info;memoryUndo=null;selectMemory(i);$('#memory-receipt').hidden=true;tell('已撤销本次编辑，恢复先前内容。','memory');});
  studio.onPress=(x,y)=>{
    if(state.theme==='post'){const w=studio.stage.clientWidth,h=studio.stage.clientHeight;let i=0;studio.nodes.forEach((n,j)=>{if(Math.hypot(n.x*w-x,n.y*h-y)<Math.hypot(studio.nodes[i].x*w-x,studio.nodes[i].y*h-y))i=j;});tell(['记忆召回','话题注意力','主动联系'][i]+'：可在能力组件中启用或停用该节点。','capability');return;}
    if(y<studio.stage.clientHeight*.76||y>studio.stage.clientHeight*.93)return;
    const i=clamp(Math.round((x/studio.stage.clientWidth-.17)/.22),0,3);selectMemory(i,'hero');studio.setMood('selected');
  };

  function wait(ms,signal){return new Promise((resolve,reject)=>{if(signal.aborted){reject(new DOMException('Aborted','AbortError'));return;}const abort=()=>{clearTimeout(timer);signal.removeEventListener('abort',abort);reject(new DOMException('Aborted','AbortError'));};const timer=setTimeout(()=>{signal.removeEventListener('abort',abort);resolve();},ms);signal.addEventListener('abort',abort,{once:true});});}
  function trace(kind,run,label){if(disposed)return;document.dispatchEvent(new CustomEvent('lab:trace',{detail:{kind,id:run.id,bot:bots[run.bot].name,label,elapsed:Math.round(performance.now()-run.started),phase:studio.context.phaseIndex,caps:[...run.caps],memory:run.memory.name,memoryIndex:run.memoryIndex,persona:{...run.persona}}}));}
  function gate(run){
    if(!manual)return Promise.resolve();
    return new Promise((resolve,reject)=>{const signal=run.controller.signal;if(signal.aborted){reject(new DOMException('Aborted','AbortError'));return;}const clean=()=>{signal.removeEventListener('abort',abort);run.resume=null;};const abort=()=>{clean();reject(new DOMException('Aborted','AbortError'));};run.resume=()=>{clean();resolve();};signal.addEventListener('abort',abort,{once:true});trace('waiting',run,'等待手动步进');});
  }
  function clearFlow(){for(const n of $$('.flow-start,.flow-node,.flow-finish'))n.classList.remove('active');for(const n of $$('[data-node-receipt]')){n.textContent=state.capabilities[+n.dataset.nodeReceipt]?'待命':'已停用';n.parentElement.classList.remove('node-complete');}}
  function idleControls(){
    $('#flow-run').innerHTML=icon('play')+'运行一次测试'+icon('arrow');$('#flow-run').disabled=false;$('#flow-run').dataset.running='false';
    const send=$('#chat-form button');send.type='submit';send.innerHTML=icon('send');send.setAttribute('aria-label','发送示例消息');send.onclick=null;
    $('#world-phase').textContent='AT EASE';studio.setContext({phase:'idle',phaseIndex:-1});
  }
  function cancel(reason='当前测试已中止。'){
    if(!operation)return;const old=operation;operation=null;epoch++;old.controller.abort();old.pending?.closest('.chat-message')?.remove();
    if(old.committed){if(!disposed){idleControls();sync('conversation');}return;}
    if(disposed)return;trace('canceled',old,reason);clearFlow();idleControls();$('#flow-result').textContent=reason;$('#flow-ticket').textContent='CANCELED / 可重新测试';tell(reason,'capability');
  }
  function valid(run){return !disposed&&operation===run&&!run.controller.signal.aborted&&run.bot===state.bot&&state.online;}
  function renderReply(body,run){
    if(run.mode==='sticker'){body.innerHTML=avatar('love',run.color,'atelier-reply-'+(++replyId));body.classList.add('sticker-reply');return;}
    if(run.mode==='voice'){body.innerHTML='<span class="voice-wave" aria-label="语音节奏示例，无音频">'+Array.from({length:22},(_,i)=>`<i style="--h:${6+Math.sin(i*2.7)**2*20}px;--d:${i*.06}s"></i>`).join('')+'</span><small>语音节奏示例 · 无音频</small>';const caption=document.createElement('span');caption.className='voice-caption';caption.textContent=run.answer;body.append(caption);return;}
    body.textContent=run.answer;
    if(run.memoryEnabled&&run.memoryPinned){const recall=document.createElement('small');recall.className='reply-recall';recall.textContent='记忆来源：'+run.memory.name;body.append(recall);}
  }
  async function run(text='今天过得怎么样？',origin='capability',withMessage=false){
    if(!state.online){toast('示例实例已停止，请先启动。');return false;}
    cancel('新测试已开始，旧测试已失效。');
    const current={id:++epoch,started:performance.now(),resume:null,bot:state.bot,controller:new AbortController(),caps:[...state.capabilities],mode:state.chatMode,color:bots[state.bot].color,answer:reply(),persona:{tone:state.warm,explore:state.curious,expression:state.chatty},memoryIndex:state.memory,memory:{...memories[state.memory]},memoryEnabled:state.capabilities[0],memoryPinned:pinned.has(state.memory),pending:null};operation=current;
    const pace=.65+(100-state.energy)/100*.75;
    if(withMessage){bubble(text,true);current.pending=bubble('');current.pending.classList.add('reply-pending');current.pending.innerHTML='<span class="thinking-dots" aria-label="正在处理"><i></i><i></i><i></i></span>';}
    $('#flow-run').innerHTML=icon('pause')+'中止本次测试'+icon('close');$('#flow-run').dataset.running='true';
    const send=$('#chat-form button');send.type='button';send.innerHTML=icon('pause');send.setAttribute('aria-label','中止这次示例回应');send.onclick=()=>cancel();
    clearFlow();$('.flow-start').classList.add('active');$('#world-phase').textContent='01 / 接收入站';$('#flow-ticket').textContent='HELLO / '+String(current.id).padStart(3,'0');
    $('#flow-result').textContent='① 收到测试消息：'+text;studio.setContext({phase:'接收入站',phaseIndex:0});tell('已收到测试消息，按当前能力逐步处理。',origin,'capability');
    trace('start',current,'接收入站');
    try{
      await gate(current);if(!valid(current))return false;
      await wait(260*pace,current.controller.signal);if(!valid(current))return false;
      const phaseNames=['记忆召回','话题注意力','输出规划'];
      for(let i=0;i<3;i++){
        if(!valid(current))return false;const node=$(`[data-cap="${i}"]`),receipt=$(`[data-node-receipt="${i}"]`);node.classList.add('active');receipt.textContent=current.caps[i]?'处理中':'跳过';
        $('#flow-result').textContent=current.caps[i]?['当前记录「'+current.memory.name+'」','构建话题与语气上下文','按当前人格组织输出'][i]:'跳过已停用的'+['记忆','注意力','主动联系'][i];
        $('#world-phase').textContent=`0${i+2} / ${phaseNames[i]}`;studio.setContext({phase:phaseNames[i],phaseIndex:i});studio.signal(i===0?'memory':'capability','conversation');
        trace('stage',current,(current.caps[i]?'':'跳过 · ')+phaseNames[i]);
        await gate(current);if(!valid(current))return false;
        await wait((current.caps[i]?410:140)*pace,current.controller.signal);if(!valid(current))return false;
        node.classList.remove('active');node.classList.toggle('node-complete',current.caps[i]);receipt.textContent=current.caps[i]?'已参与':'已跳过';
      }
      if(!valid(current))return false;
      if(current.pending){current.pending.classList.remove('reply-pending');renderReply(current.pending,current);state.likes++;$('#conversation-count').innerHTML=(128+state.likes)+'<small>条</small>';current.pending=null;}
      current.committed=true;
      $('.flow-finish').classList.add('active');$('#flow-result').textContent='✓ 示例已完成 · '+current.caps.filter(Boolean).length+' 项能力参与回应';$('#flow-ticket').textContent='DELIVERED / 回执已更新';
      $('#world-phase').textContent='DELIVERED';studio.setContext({phase:'投递完成',phaseIndex:3,activity:studio.context.activity+1});studio.feed();
      trace('complete',current,'投递完成');
      $('#chart-event').textContent='刚刚完成 1 次本地回应';$('#resource-chart').classList.add('has-new-event');tell('测试已完成，处理路径与输出回执已更新。','capability','conversation');
      await wait(380*pace,current.controller.signal);if(!valid(current))return false;
      operation=null;idleControls();sync('conversation');return true;
    }catch(error){if(error.name!=='AbortError'){cancel('这次示例没有完成，可以重新试一次。');throw error;}return false;}
  }
  function send(text){run(text,'conversation',true);}
  function greet(){if(!state.online){toast('示例实例已停止，请先启动。');return;}state.feeds++;$('#feed-count').textContent=String(state.feeds).padStart(2,'0')+' 次测试消息';run('请检查当前示例配置。','hero',true);}
  function runButton(){if(operation)cancel();else run();}

  $$('[data-bot]').forEach(b=>on(b,'click',()=>{cancel('已切换实例，旧测试已失效。');sync('identity');}));
  on($('#bot-power'),'click',()=>{if(!state.online)cancel('实例已停止，当前测试已中止。');sync('identity');});
  $$('[data-cap]').forEach(b=>on(b,'click',()=>{cancel('能力已变化，请按新的能力配置重新试一次。');sync('capability');clearFlow();}));
  for(const id of ['energy','density'])on($('#'+id),'input',()=>sync('personality'));
  $$('[data-sticker]').forEach(b=>on(b,'click',()=>tell('媒体预览已更新，可插入调试消息。','stickers','conversation')));
  on($('#send-sticker'),'click',()=>{if(state.online)tell('媒体已沿关联路径插入调试消息。','stickers','conversation');});
  on($('#motion-toggle'),'click',()=>sync());

  function updateClockHandle(){const a=(state.quiet/24*360-90)*Math.PI/180;$('#quiet-handle').setAttribute('cx',90+Math.cos(a)*66);$('#quiet-handle').setAttribute('cy',90+Math.sin(a)*66);$('#quiet-handle').setAttribute('aria-valuenow',state.quiet);}
  let clockDrag=null;const clock=$('#quiet-handle');
  function moveClock(e){const r=$('.clock-art>svg').getBoundingClientRect(),angle=(Math.atan2(e.clientY-r.top-r.height/2,e.clientX-r.left-r.width/2)+Math.PI/2+Math.PI*2)%(Math.PI*2);let hour=angle/(Math.PI*2)*24;if(hour<6)hour=24;hour=clamp(Math.round(hour),19,24);$('#quiet').value=hour;$('#quiet').dispatchEvent(new Event('input',{bubbles:true}));}
  on(clock,'pointerdown',e=>{if(e.button!==0)return;e.preventDefault();clockDrag=e.pointerId;clock.setPointerCapture(clockDrag);moveClock(e);});on(clock,'pointermove',e=>{if(e.pointerId===clockDrag)moveClock(e);});on(clock,'pointerup',()=>{clockDrag=null;tell('免打扰时段已调整，仅影响当前示例。','rhythm');});on(clock,'pointercancel',()=>clockDrag=null);
  on(clock,'keydown',e=>{if(!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End'].includes(e.key))return;e.preventDefault();const v=e.key==='Home'?19:e.key==='End'?24:state.quiet+(['ArrowRight','ArrowUp'].includes(e.key)?1:-1);$('#quiet').value=clamp(v,19,24);$('#quiet').dispatchEvent(new Event('input',{bubbles:true}));});
  on($('#quiet'),'input',()=>{updateClockHandle();sync('rhythm');});
  $$('[data-time]').forEach(b=>on(b,'click',()=>{night=b.dataset.time==='night';document.body.dataset.time=night?'night':'day';$$('[data-time]').forEach(x=>x.setAttribute('aria-pressed',String(x===b)));tell(night?'已切换晚间材质预览。':'已切换日间材质预览。','rhythm');}));

  function updatePressure(){const factor=clamp(pressure,0,1);$('#hug-fill').style.width=factor*100+'%';$('#hug-label').textContent=holding?'按住确认 '+Math.round(factor*100)+'%':'按住确认 · 仅演示';$('#hug').setAttribute('aria-description','按压进度 '+Math.round(factor*100)+'%');}
  function beginHug(){if(holding)return;holding=true;maxPressure=0;pressureTarget=1;$('#hug').classList.add('is-held');if(!studio.running){pressure=1;maxPressure=1;}updatePressure();}
  function endHug(cancelled=false){if(!holding)return;holding=false;pressureTarget=0;$('#hug').classList.remove('is-held');if(!cancelled){studio.setMood('love');tell(maxPressure>.65?'按住确认已完成，仅更新本地确认状态。':'按压未达到阈值，未确认操作。','stickers','hero');}if(!studio.running){pressure=0;pressureVelocity=0;}updatePressure();}
  on($('#hug'),'pointerdown',e=>{if(e.button!==0)return;$('#hug').setPointerCapture(e.pointerId);beginHug();});on($('#hug'),'pointerup',()=>endHug());on($('#hug'),'pointercancel',()=>endHug(true));on($('#hug'),'keydown',e=>{if(e.key===' '||e.key==='Enter'){e.preventDefault();beginHug();}});on($('#hug'),'keyup',e=>{if(e.key===' '||e.key==='Enter'){e.preventDefault();endHug();}});on($('#hug'),'blur',()=>endHug(true));
  on($('#chart-play'),'click',()=>{chartPlays=!chartPlays;$('#chart-play').setAttribute('aria-pressed',String(chartPlays));$('#chart-play').innerHTML=icon(chartPlays?'pause':'play')+(chartPlays?'停在这个采样点':'回放这条曲线');if(chartPlays){chartTime=0;$('#resource-chart').focus();}sync('resources');});
  const offFrame=studio.onFrame((dt,motion)=>{
    if(disposed)return;
    if(motion&&(Math.abs(pressure-pressureTarget)>.001||Math.abs(pressureVelocity)>.001)){pressureVelocity+=((pressureTarget-pressure)*75-pressureVelocity*16)*dt;pressure+=pressureVelocity*dt;pressure=clamp(pressure,0,1.05);maxPressure=Math.max(maxPressure,pressure);updatePressure();}
    if(chartPlays&&motion){chartTime+=dt;if(chartTime>.3){chartTime=0;$('#resource-chart').dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}));}}
  });

  function dispose(){if(disposed)return;disposed=true;if(operation){operation.controller.abort();operation=null;}life.abort();resize.disconnect();offFrame();studio.onPress=null;}
  on(window,'pagehide',dispose,{once:true});
  onTheme();measure();
  return {sync,onTheme,send,greet,run:runButton,cancel,dispose,selectMemory,setManual(value){manual=!!value;if(!manual)operation?.resume?.();},step(){operation?.resume?.();},get waiting(){return !!operation?.resume;}};
}
