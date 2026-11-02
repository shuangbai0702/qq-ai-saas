const $=(s,r=document)=>r.querySelector(s);
const $$=(s,r=document)=>[...r.querySelectorAll(s)];
const clamp=(v,a,b)=>Math.max(a,Math.min(b,v));

/** One clock (Studio), abortable listeners, and owned finite animation effects. */
export function createKawaii({studio,state,icon,avatar,toast}){
  const life=new AbortController(),effects=new Set(),springs=new Map();
  let disposed=false,lastPhase=-1,lastActivity=0,lastOnline=null,dragging=false;
  const on=(el,event,fn,opts={})=>el?.addEventListener(event,fn,{...opts,signal:life.signal});
  document.body.classList.add('sakura-console');
  document.title='01 · 软糖控制台 / 二次元交互样板 R3';
  $('.intro').innerHTML=`<div class="intro-copy"><p class="eyebrow"><span class="online-flower">✦</span> FISHBOT / SOFT CONTROL <span class="edition">ONLY 01 / R3</span></p><div class="hero-label"><i></i>控制台，也可以心动一下 <span>♡</span></div><h1 id="hero-heading">可爱，<span class="headline-pink">全速运行<span class="headline-spark">✧</span></span><span class="headline-stop">。</span></h1><p class="intro-text">把复杂留给系统，把轻盈留给操作。<br>一套有表情、有触感、会回应的控制台元素样本。</p><div class="hero-actions"><a href="#specimens" class="hero-cta">打开组件实验室 ${icon('arrow')}</a><span class="hero-tip"><i></i>拖拽 / 按压 / 逐阶段反馈</span></div><div class="hero-meta"><span>09 <small>组件样本</small></span><span>01 <small>可交互处理平面</small></span><span>100% <small>本地演示</small></span></div></div><div class="operator-art"><div class="operator-disc" aria-hidden="true"><i></i><b>✦</b><span>SOFT CONTROL</span></div><div class="operator-rings" aria-hidden="true"></div><img class="operator-image" src="./assets/operator-01.png" alt="樱粉与浅紫配色的 Q 版二次元控制台操作员" width="1024" height="1536" fetchpriority="high"><span class="operator-bubble">準備 OK！<small>系统已就绪 ♡</small></span><span class="operator-sticker">かわいい<br><b>CONTROL!</b></span><span class="operator-coordinate">DECORATIVE OPERATOR / 01</span><span class="operator-star star-a">✧</span><span class="operator-star star-b">✦</span></div>`;
  $('.intro').insertAdjacentHTML('afterend',`<div class="console-rail"><span class="rail-brand">${avatar('happy','','rail-terminal')} <b>软糖控制平面<small>SOFT CONSOLE / PLAYGROUND</small></b></span><nav aria-label="组件导航"><a href="#identity">实例</a><a href="#persona-card">配置</a><a href="#message-card">消息</a><a href="#monitor-card">观测</a></nav><span class="rail-status"><i></i><b id="rail-status-text">示例在线</b><small>LOCAL</small></span></div>`);
  $('.personality').id='persona-card';$('.conversation').id='message-card';$('.resources').id='monitor-card';
  $('.scene-shell').insertAdjacentHTML('beforeend',`<div class="routing-readout"><span><i></i><b id="routing-label">等待测试消息</b></span><span id="routing-detail">心跳仅表示示例在线</span><span class="route-count"><b id="route-count">03</b> / ENABLED</span></div>`);
  $('.scene-area').insertAdjacentHTML('beforeend',`<div class="core-caption"><b id="core-caption-title">SOFT CORE</b><span id="core-caption-state">ONLINE / HEARTBEAT</span></div><div class="stage-note"><span>✦</span><b>试试拖动这三个节点</b><small>线路会跟着重新计算</small></div>`);
  $('.scene-controls .control-kicker').innerHTML='INTERACTION MIXER '+icon('spark');
  $('.scene-controls h3').textContent='把手感，调可爱。';
  $('.scene-controls>p').innerHTML='活力影响弹性、心跳与演示节奏；<br>感应范围控制背景的局部响应。';
  $('#energy').value=state.energy;$('#energy-out').value=state.energy;$('#energy').style.setProperty('--value',state.energy+'%');studio.setEnergy(state.energy/100);
  $('.scene-controls .range-field:nth-of-type(1) small').innerHTML='轻柔 <i></i> 活泼';
  $('.scene-controls .range-field:nth-of-type(2) small').innerHTML='指尖附近 <i></i> 更大范围';
  $('.section-heading h2').innerHTML='小小组件，<em>满满回应。</em><span class="section-heart">♡</span>';
  $('.section-heading .eyebrow').textContent='THE LITTLE THINGS / COMPONENT LAB';
  $('.section-index').innerHTML='09 MODULES<span>TRY EVERY LITTLE DETAIL</span>';
  $('.closing h2').innerHTML='可爱是表情，<em>可靠是底气。</em>';
  $('#footer-theme').textContent='01 / SOFT CONSOLE · R3';
  const stickers=['开心接收','心动确认','安静待机'];
  $$('[data-sticker] span').forEach((e,i)=>e.textContent=stickers[i]);
  $('.identity-top .handwritten').textContent='HELLO, OPERATOR ♡';
  $('.specimen-stamp')?.remove();
  // Nodes remain the existing accessible controls; positions are shared with Canvas.
  const positions=[[19,29],[80,25],[80,63]];
  $$('[data-machine]').forEach((node,i)=>{
    node.style.left=positions[i][0]+'%';node.style.top=positions[i][1]+'%';
    studio.nodes[i]={x:positions[i][0]/100,y:positions[i][1]/100};
    node.querySelector('small').textContent='DRAG ME / 0'+(i+1);
    const update=()=>{studio.nodes[i].x=parseFloat(node.style.left)/100;studio.nodes[i].y=parseFloat(node.style.top)/100;studio.dirty=true;};
    // Baseline drag owns coordinates. The shared observer also catches keyboard movement.
    const observer=new MutationObserver(update);observer.observe(node,{attributes:true,attributeFilter:['style']});
    on(life.signal,'abort',()=>observer.disconnect(),{once:true});
    on(node,'pointerdown',()=>{dragging=true;document.body.classList.add('is-routing');});
    for(const event of ['pointerup','pointercancel','lostpointercapture'])on(node,event,()=>{dragging=false;document.body.classList.remove('is-routing');});
  });
  const nodesObserver=new MutationObserver(()=>studio.dirty=true);nodesObserver.observe($('.machine-nodes'),{attributes:true,subtree:true});
  // Procedural spring response: one frame clock, no interval or per-control animation loops.
  function springFor(el){if(!springs.has(el))springs.set(el,{x:1,v:0,target:1,rotation:0,rv:0,rt:0});return springs.get(el);}
  on(document,'pointerdown',e=>{const el=e.target.closest('button,.hero-cta');if(!el||el.disabled)return;const s=springFor(el);s.target=.94;s.v=-1.4;s.rt=-1.2;});
  const release=()=>{for(const s of springs.values()){s.target=1;s.rt=0;s.v+=.7;}};
  on(document,'pointerup',release);on(document,'pointercancel',release);on(window,'blur',release);
  on(document,'keydown',e=>{if(!['Enter',' '].includes(e.key))return;const el=e.target.closest('button');if(el&&!el.disabled){const s=springFor(el);s.x=.96;s.v=1;}});
  function animate(el,frames,options){if(!studio.running)return;const fx=el.animate(frames,{...options,fill:'none'});effects.add(fx);fx.finished.catch(()=>{}).finally(()=>effects.delete(fx));}
  function burst(el){if(!studio.running)return;const r=el.getBoundingClientRect();for(let i=0;i<8;i++){const a=i/8*Math.PI*2,chip=document.createElement('i');chip.className='action-confetti';chip.textContent=i%3===0?'♡':i%3===1?'✦':'·';chip.style.left=r.left+r.width/2+'px';chip.style.top=r.top+r.height/2+'px';document.body.append(chip);const fx=chip.animate([{transform:'translate(-50%,-50%) scale(.3)',opacity:0},{opacity:1,offset:.15},{transform:`translate(calc(-50% + ${Math.cos(a)*72}px),calc(-50% + ${Math.sin(a)*58}px)) rotate(${i*45}deg) scale(.65)`,opacity:0}],{duration:650,easing:'cubic-bezier(.2,.7,.3,1)'});effects.add(fx);fx.finished.catch(()=>{}).finally(()=>{effects.delete(fx);chip.remove();});}}
  on(document,'click',e=>{
    const button=e.target.closest('[data-sticker],#persona-save,#memory-pin,#send-sticker,[data-ui-state]');if(button&&!button.disabled)burst(button);
    const tab=e.target.closest('.mini-tabs button,.persona-presets button,.bot-selector button');if(tab){const old=tab.parentElement.querySelector('.tab-follow');if(old)old.remove();const blob=document.createElement('i');blob.className='tab-follow';tab.parentElement.append(blob);blob.style.width=tab.offsetWidth+'px';blob.style.height=tab.offsetHeight+'px';blob.style.left=tab.offsetLeft+'px';blob.style.top=tab.offsetTop+'px';animate(blob,[{transform:'scaleX(.55)',opacity:.3},{transform:'scaleX(1.08)',opacity:.5},{transform:'scaleX(1)',opacity:0}],{duration:420,easing:'cubic-bezier(.2,.8,.3,1)'});}
  });
  let lastPointer={x:0,y:0},lookX=0,lookY=0;
  on($('.intro'),'pointermove',e=>{const r=$('.intro').getBoundingClientRect();lastPointer={x:clamp((e.clientX-r.left)/r.width-.5,-.5,.5),y:clamp((e.clientY-r.top)/r.height-.5,-.5,.5)};},{passive:true});
  on($('.intro'),'pointerleave',()=>lastPointer={x:0,y:0});
  function motionChanged(){const active=studio.running;document.body.classList.toggle('motion-paused',!active);for(const fx of effects)active?fx.play():fx.pause();if(!active){for(const [el,s] of springs){s.x=s.target;s.v=0;el.style.removeProperty('scale');el.style.removeProperty('rotate');}$('.operator-art').style.setProperty('--operator-x','0px');$('.operator-art').style.setProperty('--operator-y','0px');}}
  on($('#motion-toggle'),'click',motionChanged);on(studio.reduced,'change',motionChanged);
  const off=studio.onFrame((dt,motion)=>{
    if(disposed)return;
    if(motion){for(const [el,s] of springs){const k=160+studio.energy*180,d=18+studio.energy*4;s.v+=((s.target-s.x)*k-s.v*d)*dt;s.x+=s.v*dt;s.rv+=((s.rt-s.rotation)*k-s.rv*d)*dt;s.rotation+=s.rv*dt;el.style.scale=String(s.x);el.style.rotate=s.rotation+'deg';if(Math.abs(s.x-s.target)<.0001&&Math.abs(s.v)<.001&&s.target===1){el.style.removeProperty('scale');el.style.removeProperty('rotate');springs.delete(el);}}lookX+=(lastPointer.x*14-lookX)*Math.min(1,dt*6);lookY+=(lastPointer.y*9-lookY)*Math.min(1,dt*6);$('.operator-art').style.setProperty('--operator-x',lookX+'px');$('.operator-art').style.setProperty('--operator-y',lookY+'px');}
    const phase=studio.context.phaseIndex,activity=studio.context.activity;
    if(phase!==lastPhase||activity!==lastActivity||lastOnline!==state.online){
      const working=phase>=0;$('.scene-board').classList.toggle('is-processing',working);
      $('#routing-label').textContent=working?studio.context.phase:state.online?'等待测试消息':'示例实例已停止';
      $('#routing-detail').textContent=working?'彩色信号 · 当前演示处理阶段':state.online?'心跳仅表示示例在线':'已停止 · 保留当前配置';
      $('#core-caption-state').textContent=working?'PROCESSING / '+String(phase+1).padStart(2,'0'):state.online?'ONLINE / HEARTBEAT':'OFFLINE / RETAINED';
      $$('[data-machine]').forEach((el,i)=>el.classList.toggle('phase-active',working&&phase===i));
      if(activity>lastActivity){burst($('#feed'));animate($('.routing-readout'),[{backgroundColor:'#f5c5e5'},{backgroundColor:'#fff9fc'}],{duration:700});toast('处理完成 ♡ 路径与回执已更新。');}
      lastPhase=phase;lastActivity=activity;lastOnline=state.online;
    }
    $('#route-count').textContent=String(state.capabilities.filter(Boolean).length).padStart(2,'0');
    $('#rail-status-text').textContent=state.online?'示例在线':'示例已停止';
    $('.rail-status').classList.toggle('offline',!state.online);
    if(!dragging)document.body.classList.remove('is-routing');
  });
  motionChanged();studio.dirty=true;
  return {dispose(){if(disposed)return;disposed=true;off();life.abort();nodesObserver.disconnect();for(const fx of effects)fx.cancel();effects.clear();springs.clear();$$('.action-confetti').forEach(el=>el.remove());}};
}
