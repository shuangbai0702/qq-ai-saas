import {AdminNav} from './admin-nav';
import {AdminOperations} from './admin-operations';
import {AdminAutonomous} from './admin-autonomous';
import {useEffect,useState,useRef,type FormEvent} from 'react';
import {AdminMemory} from './admin-memory';
import {Fish,ArrowUpRight,Check,ChevronRight,CheckCircle2,Shield,ArrowLeft,Loader2} from 'lucide-react';
import {defaultConfig,personas,type BotConfig} from '../../../packages/contracts/index';
import {api} from './api';import {ModelFields} from './model-fields';import {Field,Status,go,BotAvatar} from './ui';import {useLiveResource,useLifetime} from './hooks';
export function Create({onDirtyChange}:{onDirtyChange:(dirty:boolean,guardWithinPage?:boolean)=>void}){
 const [step,setStep]=useState(0),[name,setName]=useState('我的大肥鱼'),[customName,setCustomName]=useState(false);
 const [cfg,setCfg]=useState<BotConfig>(defaultConfig),[key,setKey]=useState(''),[protocol,setProtocol]=useState('napcat');
 const [runtimeMode,setRuntimeMode]=useState('shared'),[uin,setUin]=useState(''),[busy,setBusy]=useState(false),[createError,setCreateError]=useState('');
 const {data:availability,error:quotaError,refresh:refreshQuota}=useLiveResource('/bots',2000);
 const owner=useLifetime(),pending=useRef(false),heading=useRef<HTMLHeadingElement>(null),previousStep=useRef(step);
 const hasDraft=cfg!==defaultConfig||customName||key!==''||protocol!=='napcat'||runtimeMode!=='shared'||uin!=='';
 useEffect(()=>{onDirtyChange(hasDraft,true);return()=>onDirtyChange(false)},[hasDraft,onDirtyChange]);
 useEffect(()=>{
  if(previousStep.current!==step)heading.current?.focus();
  previousStep.current=step;
 },[step]);
 function move(next:number){setCreateError('');setStep(next)}
 function selectPersona(persona:typeof personas[number]){
  setCfg({...cfg,personaId:persona.id,systemPrompt:persona.prompt});
  if(!customName)setName(persona.id==='dafeiyu'?'我的大肥鱼':persona.name);
 }
 async function submit(event:FormEvent<HTMLFormElement>){
  event.preventDefault();
  if(pending.current)return;
  if(step<2){move(step+1);return}
  const page=owner.current;
  if(!page||page.signal.aborted||!availability?.quota?.canCreate)return;
  pending.current=true;setBusy(true);setCreateError('');
  try{
   const result=await api('/bots',{name,protocol,runtimeMode,expectedUin:uin,config:cfg,apiKey:key});
   if(!page.signal.aborted){onDirtyChange(false);go('/bots/'+result.id+'/qq')}
  }catch(e){
   if(!page.signal.aborted){setCreateError((e as Error).message);void refreshQuota()}
  }finally{
   if(!page.signal.aborted){pending.current=false;setBusy(false)}
  }
 }
 if(!availability)return <section className="panel"><h1>创建机器人</h1>{quotaError?<><p role="alert">名额查询失败：{quotaError}</p><button className="secondary" onClick={()=>void refreshQuota()}>重试</button></>:<p>正在查询机器人名额…</p>}</section>;
 if(!availability.quota.canCreate)return <section className="panel quota-block"><h1>机器人名额已用完</h1><p>已使用 {availability.quota.used} / {availability.quota.limit} 个名额。每个机器人对应一个 QQ。</p><p>创建新机器人前，请先删除原机器人。停止运行不会释放名额，删除完成后才可重新创建。</p><p className="muted">进入机器人概览，展开“更多操作”，再点击“删除机器人”。删除会停止旧实例并撤销模型 Key 与 QQ 绑定。</p><div className="button-row">{availability.items.map((b:any)=><a className="secondary" key={b.id} href={'#/bots/'+b.id+'/overview'}>管理 {b.name}</a>)}</div><div className="button-row"><button className="secondary" onClick={()=>void refreshQuota()}>刷新名额</button><a className="back" href="#/bots">返回机器人列表</a></div>{quotaError&&<p role="alert">刷新失败：{quotaError}</p>}</section>;
 return <>
  <a className="back" href="#/bots"><ArrowLeft size={16}/>返回机器人列表</a>
  <div className="page-title"><div><h1>创建机器人</h1><p>剩余 {availability.quota.available} 个名额。创建后再启动并连接 QQ。</p></div></div>
  <div className="wizard-steps" aria-label="创建步骤">{['角色与名称','模型和协议','确认创建'].map((label,index)=><div key={label} className={step>=index?'current':''} aria-current={step===index?'step':undefined}><span aria-hidden="true">{step>index?<Check size={15}/>:index+1}</span>{label}</div>)}</div>
  <form className="panel wizard" onSubmit={submit}>
   {step===0?<><h2 ref={heading} tabIndex={-1}>选择人格模板</h2><p className="muted">之后可以随时修改人格与提示词。</p><div className="persona-grid">{personas.map(persona=><button type="button" key={persona.id} className={'persona '+(cfg.personaId===persona.id?'selected':'')} aria-pressed={cfg.personaId===persona.id} onClick={()=>selectPersona(persona)}><BotAvatar persona={persona.id}/><b>{persona.name}</b><small>{persona.description}</small>{cfg.personaId===persona.id&&<CheckCircle2 size={17}/>}</button>)}</div><Field label="机器人名称"><input value={name} onChange={e=>{setCustomName(true);setName(e.target.value)}} required maxLength={80}/></Field></>
   :step===1?<><h2 ref={heading} tabIndex={-1}>模型与 QQ 协议</h2><p className="muted">没有 API Key 可以先创建机器人并连接 QQ，稍后再配置 AI 回复。</p><ModelFields cfg={cfg} setCfg={setCfg} apiKey={key} setKey={setKey}/><div className="form-grid"><Field label="QQ 协议端"><select value={protocol} onChange={e=>setProtocol(e.target.value)}><option value="mock">模拟协议（体验流程，不连接QQ）</option><option value="euler">EulerOneBot（需要管理员配置签名）</option><option value="napcat">NapCat（需要管理员部署运行端）</option></select></Field><Field label="AI运行模式"><select value={runtimeMode} onChange={e=>setRuntimeMode(e.target.value)}><option value="dedicated">独立实例</option><option value="shared">共享运行组（仅平台受控功能）</option></select></Field><Field label="计划登录的 QQ 号" hint={protocol==='euler'?'Euler 需要指定 QQ 号。扫码后仍会核对实际身份。':'可留空。填写后，扫码时会核对实际登录的 QQ 号。'}><input inputMode="numeric" pattern="[0-9]{5,20}" maxLength={20} title="请输入 5–20 位数字 QQ 号" required={protocol==='euler'} value={uin} onChange={e=>setUin(e.target.value.replace(/\D/g,''))} placeholder={protocol==='euler'?'5–20 位 QQ 号':'可稍后填写'}/></Field></div></>
   :<><div className="confirm-fish"><Fish size={48}/></div><h2 ref={heading} tabIndex={-1}>确认创建机器人：{name}</h2><p className="muted">{cfg.model} · {personas.find(p=>p.id===cfg.personaId)?.name} · {protocol==='mock'?'模拟协议':protocol}</p><div className="notice">{protocol==='mock'?'模拟协议只用于体验连接流程，无法用手机 QQ 扫码登录。':'创建后进入 QQ 连接页。点击“启动并连接QQ”，按页面提示扫码并在手机上确认。'}{!key&&<p>尚未填写模型 Key，配置后才能收到 AI 回复。</p>}</div></>}
   {createError&&<p className="alert" role="alert">{createError}</p>}
   <div className="wizard-actions"><button type="button" className="secondary" disabled={busy} onClick={()=>step?move(step-1):go('/bots')}>{step?'上一步':'取消'}</button><button type="submit" className="primary" disabled={busy||!name.trim()}>{busy?'创建中…':step<2?'下一步':'创建并设置QQ'}<ChevronRight size={16}/></button></div>
  </form>
 </>;
}


export function Logs({id}:{id:string}){const {data,error,refresh}=useLiveResource('/bots/'+id+'/logs',3000);const lines:string[]=data?.lines||[];return <section className="panel"><div className="list-toolbar"><div><h2>运行日志</h2><span className="muted">每 3 秒刷新 · 最近 160 行 · 凭据脱敏</span></div><button className="secondary" onClick={()=>void refresh()}>立即刷新</button></div>{error&&<div className="alert" role="alert">日志加载失败：{error}</div>}<pre className="logs">{!data?'正在读取运行日志…':lines.length?lines.join('\n'):'暂无运行日志。启动机器人后，这里会显示真实进程输出。'}</pre></section>}
export function Admin({onError,section='operations'}:{onError:(s:string)=>void;section?:string}){const memory=section==='memory',autonomous=section==='autonomous';return <><div className="page-title"><div><span className="eyebrow">平台管理</span><h1>{memory?'内存与 QQ 监控':autonomous?'自主任务与执行回执':'平台运营与监控'}</h1><p>{memory?'按实际进程查看 RAM、Swap 和 QQ 运行状态。':autonomous?'分别查看QQ连接、自主脑状态与真实执行结果。':'按账号处理部署名额、连接排障和操作记录。'}</p></div></div><AdminNav active={memory?'memory':autonomous?'autonomous':'operations'}/>{memory?<><AdminMemory/><AdminRuntimeDetails/></>:autonomous?<AdminAutonomous/>:<AdminOperations/>}</>}
function AdminRuntimeDetails(){const {data:d,error}=useLiveResource('/admin/overview',30000);return <>{error&&<p role="alert">{error}</p>}{d&&<details className="panel"><summary>实例原始状态与错误</summary><h2>运行实例</h2><div className="table-wrap"><table><thead><tr><th>名称</th><th>协议</th><th>状态</th><th>AI PID</th><th>协议 PID</th><th>异常</th></tr></thead><tbody>{d.bots.map((b:any)=><tr key={b.id}><td>{b.name}</td><td>{b.protocol}</td><td><Status value={b.observed_state} mock={b.protocol==='mock'}/></td><td>{b.health?.ai?.pid||'—'}</td><td>{b.health?.protocol?.pid||'—'}</td><td>{b.last_error||'—'}</td></tr>)}</tbody></table></div></details>}</>}
