import {useEffect,useRef,useState} from 'react';
import {api} from './api';
import {Field} from './ui';
import {CapabilityLayout} from './capability-layout';
import type {BotConfig} from '../../../packages/contracts/index';
import {AutonomyConfigSchema,defaultAutonomyConfig,type AutonomyConfig} from '../../../packages/contracts/autonomous';
import {autonomousStates as states,autonomousOutcomes as outcomes,autonomousDate,autonomousQQStatus,autonomousReason} from './autonomous-status';


export function ProactiveControls({id,botConfig,onError,onDirtyChange}:{id:string;botConfig:BotConfig;onError:(text:string)=>void;onDirtyChange?:(value:boolean)=>void}){
 const [view,setView]=useState<any>(),[config,setConfig]=useState<AutonomyConfig>(defaultAutonomyConfig),[dirty,setDirty]=useState(false),[busy,setBusy]=useState(false),[notice,setNotice]=useState('');
 const [loadError,setLoadError]=useState('');
 const [section,setSection]=useState('overview');
 const commonGroups=botConfig.behavior?.groupParticipationGroups||[];
 const owner=useRef<{signal:AbortSignal;seq:number}|undefined>(undefined);const dirtyRef=useRef(false),writing=useRef(false);
 useEffect(()=>{dirtyRef.current=dirty},[dirty]);
 useEffect(()=>{onDirtyChange?.(dirty);return()=>onDirtyChange?.(false)},[dirty,onDirtyChange]);
 useEffect(()=>{
  const controller=new AbortController(),lease={signal:controller.signal,seq:0};owner.current=lease;setView(undefined);setDirty(false);dirtyRef.current=false;writing.current=false;setBusy(false);setNotice('');setLoadError('');
  let polling=false;
  const load=async()=>{if(polling||writing.current)return;polling=true;const seq=++lease.seq;try{const next=await api('/bots/'+id+'/autonomous',undefined,undefined,lease.signal);if(owner.current!==lease||lease.signal.aborted||seq!==lease.seq)return;setView(next);setLoadError('');if(!dirtyRef.current){const saved=AutonomyConfigSchema.parse(next.settings.config);setConfig(saved)}}catch(e){if(!lease.signal.aborted&&owner.current===lease&&seq===lease.seq)setLoadError((e as Error).message)}finally{polling=false}};
  void load();const timer=setInterval(()=>void load(),10000);
  return()=>{controller.abort();clearInterval(timer);if(owner.current===lease)owner.current=undefined};
 },[id]);
 const update=(patch:Partial<AutonomyConfig>)=>{setConfig(c=>({...c,...patch}));setDirty(true);dirtyRef.current=true};
 async function submit(kind:'save'|'wake'|'stop'){
  const lease=owner.current;if(!lease||lease.signal.aborted||writing.current||!view||kind==='wake'&&dirtyRef.current)return;setBusy(true);writing.current=true;lease.seq++;
  const keepDraft=kind==='stop'&&dirtyRef.current;
  try{
   if(kind==='wake')await api('/bots/'+id+'/autonomous/wake',{expectedEpoch:view.settings.epoch},'POST',lease.signal);
   else await api('/bots/'+id+'/autonomous',{expectedEpoch:view.settings.epoch,config:AutonomyConfigSchema.parse(kind==='stop'?{...view.settings.config,mode:'passive'}:config)},'PUT',lease.signal);
   if(owner.current!==lease||lease.signal.aborted)return;
   dirtyRef.current=keepDraft;setDirty(keepDraft);if(keepDraft)setConfig(c=>({...c,mode:'passive'}));setNotice(kind==='wake'?'已加入唤醒队列；离线时不会启动 QQ。':keepDraft?'主动模式已暂停，其余未保存编辑仍保留在页面。':'已保存，等待运行实例确认。QQ 连接保持运行。');
   const next=await api('/bots/'+id+'/autonomous',undefined,undefined,lease.signal);
   if(owner.current===lease&&!lease.signal.aborted){setView(next);setLoadError('');if(!keepDraft){const saved=AutonomyConfigSchema.parse(next.settings.config);setConfig(saved)}}
  }catch(e){if(owner.current===lease&&!lease.signal.aborted)onError((e as Error).message)}finally{if(owner.current===lease&&!lease.signal.aborted){writing.current=false;setBusy(false)}}
 }
 async function goalAction(goal:any,operation:'pause'|'resume'|'abandon'){
  const lease=owner.current;if(!lease||lease.signal.aborted||writing.current||!view||dirtyRef.current)return;
  setBusy(true);writing.current=true;lease.seq++;
  try{
   await api('/bots/'+id+'/autonomous/goals',{id:goal.id,expectedEpoch:view.settings.epoch,expectedRevision:goal.revision,operation},'POST',lease.signal);
   const next=await api('/bots/'+id+'/autonomous',undefined,undefined,lease.signal);
   if(owner.current===lease&&!lease.signal.aborted){setView(next);setNotice('目标已更新，当前自主工作会重新核验。')}
  }catch(e){if(owner.current===lease&&!lease.signal.aborted)onError((e as Error).message)}finally{if(owner.current===lease&&!lease.signal.aborted){writing.current=false;setBusy(false)}}
 }
 const latest=view?.cycles?.[0],executing=view?.actions?.find((action:any)=>action.status==='executing');
 return <CapabilityLayout label="主动模式" active={section} onSelect={setSection} items={[
  {id:'overview',label:'运行概览',group:'主动模式'},{id:'goals',label:'持久目标',group:'主动模式'},
  {id:'settings',label:'模式与节奏',group:'设置'},{id:'fatigue',label:'活动与疲劳',group:'设置'},{id:'limits',label:'安全保护与用量',group:'设置'},
  {id:'activity',label:'行动与回执',group:'运行记录'},
 ]}><section className="panel" hidden={section!=='overview'} aria-label="主动型 AI 生命周期" onKeyDown={event=>{if(event.key==='Enter'&&event.target instanceof HTMLInputElement)event.preventDefault()}}><div className="section-heading"><span className="eyebrow">AUTONOMY / OVERVIEW</span><h2>主动模式</h2><p>被动模式保留现有聊天、跟聊和定时功能。主动模式增加持续目标和模型自主决策；每完成一件事，根据真实结果重新选择行动或休眠。休眠不持续调用模型。</p></div>
  <p role="status">{view?states[view.settings.state]||view.settings.state:'正在读取自主状态'}{view?.pendingRuntime?' · 配置待运行实例确认':''}{view?' · '+(loadError?'状态更新失败，等待核对':autonomousQQStatus(view)):''}</p>
  {loadError&&<p className="alert" role="alert">{loadError} {view?'当前展示上次读取的状态。':'当前状态暂不可用。'}</p>}
  {view&&<div className="memory-cards" aria-label="自主运行概览"><div className="memory-card"><span>执行范围</span><strong>{view.settings.config.mode==='passive'?'被动':view.settings.config.rollout==='shadow'?'影子运行':'主动执行'}</strong><small>{view.pendingRuntime?'配置已保存，等待运行实例应用':'运行实例已确认当前配置'}</small></div><div className="memory-card"><span>下一次唤醒</span><strong>{autonomousDate(view.settings.next_wake_at)}</strong><small>离线期间挂起，重新登录后恢复判断。</small></div><div className="memory-card"><span>今日后台 Token</span><strong>{view.usage?Number(view.usage.used_tokens).toLocaleString('zh-CN'):'暂无记录'}</strong><small>预留 {view.usage?.reserved_tokens??0} · {view.settings.config.dailyTokenBudget===0?'不设每日上限':'每日上限 '+view.settings.config.dailyTokenBudget.toLocaleString('zh-CN')}</small></div></div>}
  {view&&<section aria-label="当前自主工作"><h3>现在在做什么</h3>{executing?<p>正在执行：{executing.objective}</p>:<p>{states[view.settings.state]||view.settings.state}；当前没有执行中的动作。</p>}{view.settings.summary&&<p>持续状态：{view.settings.summary}</p>}{latest?.decision_reason&&<p>最近决策理由：{latest.decision_reason}</p>}{view.cycles?.find((c:any)=>c.orientation)?.orientation&&(()=>{const o=view.cycles.find((c:any)=>c.orientation).orientation;return <p>最近意图重评：{o.motive}；期望：{o.desiredOutcome||"暂不行动"}。{o.reason}（{autonomousDate(o.at)}）</p>})()}{latest&&<small>最近思考：{autonomousDate(latest.finished_at||latest.started_at)}。模型摘要不等于工具执行成功，实际结果见下方行动记录。</small>}</section>}
  </section>{notice&&<p className="notice" role="status">{notice}</p>}
  <div className="save-bar"><div><b>自主配置 · {dirty?'有未保存的修改':'已保存'}</b><small>保存不会重启QQ，模式与工具权限分别管理。</small></div><div className="button-row"><button type="button" className="primary" disabled={!view||busy||!dirty} onClick={()=>void submit('save')}>保存自主配置</button><button type="button" className="secondary" disabled={!view||busy||!dirty} onClick={()=>{const saved=AutonomyConfigSchema.parse(view.settings.config);setConfig(saved);setDirty(false);dirtyRef.current=false}}>撤销未保存编辑</button><button type="button" className="secondary" disabled={!view||busy||dirty||view.settings.config.mode!=='active'||view.pendingRuntime} onClick={()=>void submit('wake')}>唤醒并重新判断</button><button type="button" className="danger" disabled={!view||busy||view.settings.config.mode!=='active'} onClick={()=>void submit('stop')}>暂停主动模式</button></div></div>
  <fieldset disabled={busy||!view}><section className="panel" hidden={section!=='settings'}><div className="section-heading"><span className="eyebrow">AUTONOMY / SETTINGS</span><h2>模式与节奏</h2><p>选择由谁发起行动，以及空闲后何时再次思考。</p></div><div className="form-grid"><Field label="行为模式"><select aria-label="主动行为模式" value={config.mode} onChange={e=>update({mode:e.target.value as AutonomyConfig['mode']})}><option value="passive">被动：保留现有功能</option><option value="active">主动：目标、行动与休眠闭环</option></select></Field><Field label="执行范围"><select aria-label="主动执行范围" value={config.rollout} onChange={e=>update({rollout:e.target.value as AutonomyConfig['rollout']})}><option value="shadow">影子：思考与只读验证，不对外行动</option><option value="live">实际执行已授权的工具</option></select></Field></div>
  <p>实际执行时，自主层接管主人联系和群内重新入场，避免两套调度重复发送；正常消息回复与正在进行的跟聊保持原有管线。主动模式不单独授权能力，外层读取内层实际可用工具；功能开关、真实身份、白名单和发送限制对两种模式共同生效。</p>
  <p>能力设置请在对应功能页面调整；开启主动模式不会额外开启空间、签名或联系权限。</p>
  <div aria-label="共用群参与白名单"><b>共用群参与白名单</b><p>{commonGroups.length?commonGroups.join('、'):'尚未设置，暂不主动参与群聊'}</p><p>主动、被动模式沿用同一份已保存的白名单，并与允许服务的群取交集。<a href={'#/bots/'+id+'/config/chat'}>修改群参与白名单与群判断设置</a></p>{botConfig.behavior?.groupParticipationEnabled===false&&<p className="notice">群语义参与已关闭；开启后才会主动参与白名单群。</p>}</div>
  <Field label="自主思考间隔（分钟）"><input aria-label="自主思考间隔（分钟）" type="number" min={1} max={1440} value={config.minSleepSeconds/60} onChange={e=>update({minSleepSeconds:Math.round(Number(e.target.value)*60)})}/></Field><p className="muted">默认5分钟，闲下来后再次自主判断。完成任务会立即重新评估；模型可根据明确的等待原因延长休眠，重要新事件可提前唤醒。默认不设每日用量上限；此间隔不替代群聊冷却、安静时段和单轮防失控保护。</p>
  </section><section className="panel" hidden={section!=='fatigue'}><div className="section-heading"><span className="eyebrow">AUTONOMY / ACTIVITY</span><h2>活动与疲劳</h2><p>把真实活动和空闲信息交给模型参考，不以疲劳数值强制选择聊天或休息。</p></div><label className="toggle-row"><div><b>启用自主活动调节与意图重评</b><small>依据实际活动与空闲时间提供节奏信息。</small></div><input aria-label="提供自主活动节奏信息" type="checkbox" checked={config.fatigueEnabled} onChange={e=>update({fatigueEnabled:e.target.checked})}/></label><div className="form-grid">{([['fatigueWindowMinutes','活动统计窗口（分钟）',10,1440],['fatigueRecoveryMinutes','休息多久负荷减半（分钟）',5,240],['fatigueCapacityMinutes','活动负荷尺度（分钟）',1,240],['initiativeAfterIdleMinutes','空闲多久积累一半行动倾向（分钟）',5,1440]] as const).map(([key,label,min,max])=><Field key={key} label={label}><input aria-label={label} type="number" min={min} max={max} value={config[key]} onChange={e=>update({[key]:Number(e.target.value)})}/></Field>)}</div><p>实际自主任务增加负荷，休息使负荷逐渐恢复；长期没有实际行动会积累行动倾向。连续判断后休眠不会算成已经行动。恢复且连续空转时，外层会先重新形成自身意图，再编排任务；方向由模型选择，不强制发消息或绕过权限。尺度是活动估计，与CPU占用、标准模式的局部聊天疲劳分别处理。</p></section>
  <section className="panel" hidden={section!=='limits'}><div className="section-heading"><span className="eyebrow">AUTONOMY / LIMITS</span><h2>安全保护与用量</h2><p>限制单轮失控与用量，达到额度后保存进度并让出调度。</p></div><div className="form-grid">{([['cycleModelCalls','单周期模型请求',2,64],['cycleToolCalls','单周期工具调用',1,128],['cycleExternalActions','单周期对外操作',0,4],['cycleTokenBudget','单周期 Token 预算',4000,2000000],['dailyTokenBudget','每日 Token 上限（0为不限）',0,100000000],['cycleSeconds','连续无进展超时（秒）',30,180],['maxSleepSeconds','最长休眠秒数',900,604800]] as const).map(([key,label,min,max])=><Field key={key} label={label}><input type="number" min={min} max={max} value={config[key]} onChange={e=>update({[key]:Number(e.target.value)})}/></Field>)}</div><p>一次聊天行动可投递1到4条并只计一次行动，各条仍需独立协议回执；收藏、签名等写操作分别计量。历史记录保留原计量方式。默认每周期24次模型请求、32次工具调用及50万Token，每日不限；模型或工具实际返回会刷新无进展计时，任务持续推进时不因总时长超过90秒而截断；单次工具仍有超时。周期额度用于让出调度，不是用完就休息。用完保存进度，15秒后继续评估；模型明确选择休息时才按普通间隔休眠。缺失模型用量或结果不明时保守记账。当前未接入的工具会明确显示不可用。</p></section>
  <p className="notice">B站发现、合集、字幕、画面分析与互动权限在 <a href={'#/bots/'+id+'/bilibili'}>Bilibili</a> 中统一配置；外层仅使用内层已开放的能力。空间、签名等仍在对应功能中授权。</p>
  {dirty&&<p>自主配置尚未保存。先保存或撤销编辑，再唤醒或操作目标；仍可直接暂停主动模式。</p>}
</fieldset>
  {view&&<>{view.settings.wait?.scope&&<p>等待范围：{view.settings.wait.scope}；重要新消息可以打断等待。</p>}{view.settings.last_error&&<p className="notice">最近等待原因：{autonomousReason(view.settings.last_error)}</p>}<section className="panel" hidden={section!=='goals'}><div className="section-heading"><span className="eyebrow">AUTONOMY / GOALS</span><h2>持久目标</h2><p>目标跨休眠保存，可暂停、恢复或放弃。</p></div>{view.goals.length?view.goals.map((g:any)=><article key={g.id}><b>{g.title}</b> · {outcomes[g.status]||g.status}<p>{g.progress||g.reason}</p>{['active','paused','paused_user','paused_mode'].includes(g.status)&&<div className="actions"><button type="button" disabled={busy||dirty} onClick={()=>void goalAction(g,g.status==='active'?'pause':'resume')}>{g.status==='active'?'暂停目标':'恢复目标'}</button><button type="button" disabled={busy||dirty} onClick={()=>void goalAction(g,'abandon')}>放弃目标</button></div>}</article>):<p>尚无目标；主动层可根据真实兴趣形成目标，也可以判断暂时无事可做。</p>}</section><section className="panel" hidden={section!=='activity'}><div className="section-heading"><span className="eyebrow">AUTONOMY / ACTIVITY</span><h2>最近行动与真实结果</h2><p>决策理由不等于执行完成，以工具证据和真实回执为准。</p></div>{view.actions.map((a:any)=><article key={a.id}><b>{a.kind} · {outcomes[a.status]||a.status}</b><p>{a.objective}</p><small>{a.result?.summary||'等待执行结果'}{a.result?.shadow?'（影子记录，未执行对外操作）':''}</small><details><summary>执行身份、停止原因与回执</summary><p>动作 <code>{a.id}</code><br/>周期 <code>{a.cycle_id}</code> · 自主配置 v{a.epoch}<br/>时间：{autonomousDate(a.finished_at||a.created_at)}</p>{a.result?.stopReason&&<p>{autonomousReason(a.result.stopReason)}</p>}{(a.result?.effects||[]).map((e:any)=><p key={e.id}>序列 {e.sequence??e.id} · {outcomes[e.status]||e.status} · 回执 {e.receipt??'未确认'}</p>)}</details></article>)}{!view.actions.length&&<p className="muted">暂无自主行动记录。</p>}</section></>}
 </CapabilityLayout>;
}
