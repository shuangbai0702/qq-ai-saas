import {useEffect,useRef,useState} from 'react';
import {AdminNav} from './admin-nav';
import {api} from './api';
import {useLifetime,useLiveResource} from './hooks';

type Model={id:string;name:string;base_url:string;model:string;priority:number;enabled:boolean;revision:number;last_status:string;last_checked_at:string|null;last_http_status:number|null};
const states:Record<string,string>={unchecked:'尚未验证',available:'上次请求成功',limited:'上次额度或频率受限',rejected:'上次认证或权限被拒绝',failed:'上次请求失败',unconfirmed:'上次结果无法确认'};
const date=(value:string|null)=>value?new Date(value).toLocaleString('zh-CN'):'—';

export function AdminModels(){
 const {data,error,refresh}=useLiveResource<{items:Model[]}>('/admin/platform-models',0);
 const [editing,setEditing]=useState<Model|'new'>(),[busy,setBusy]=useState(false),[message,setMessage]=useState(''),[failure,setFailure]=useState('');
 const [showReservations,setShowReservations]=useState(false);
 const owner=useLifetime(),pending=useRef(false);
 async function probe(model:Model){
  const page=owner.current;if(!page||page.signal.aborted||pending.current)return;
  pending.current=true;setBusy(true);setFailure('');setMessage('');
  try{
   const result=await api('/admin/platform-models/'+encodeURIComponent(model.id)+'/probe',{expectedRevision:model.revision},'POST',page.signal);
   if(page.signal.aborted)return;
   setMessage(result.applied?'测试完成：'+(states[result.status]||result.status):'测试期间配置已变化，旧结果未覆盖新配置。');
   await refresh();
  }catch(e){if(!page.signal.aborted)setFailure((e as Error).message)}
  finally{pending.current=false;if(!page.signal.aborted)setBusy(false)}
 }
 return <>
  <div className="page-title"><div><span className="eyebrow">平台管理</span><h1>平台模型</h1><p>管理「平台提供」使用的模型与调用顺序。</p></div><button className="primary" disabled={busy||!!editing} onClick={()=>{setEditing('new');setFailure('');setMessage('')}}>添加模型</button></div>
  <AdminNav active="models"/>
  <section className="panel">
   <div className="section-heading"><h2>模型与优先级</h2><button className="secondary" disabled={busy} onClick={()=>void refresh()}>刷新状态</button></div>
   <p className="muted">优先级数字越小越优先；保存后用于后续请求。状态是上次请求的结果，不代表持续在线。仅点击「测试连接」才发送一条小额测试请求。</p>
   <p className="muted">供应商明确拒绝且符合切换条件时按顺序切换；超时、连接中断或用量不明时保留原有保护，避免重复请求和计费。</p>
   {(error||failure)&&<p className="alert" role="alert">{failure||error}</p>}{message&&<p role="status">{message}</p>}
   {!data&&!error&&<p role="status">正在读取模型…</p>}
   {data&&<div className="table-wrap"><table><thead><tr><th>优先级</th><th>名称 / 模型</th><th>接口地址</th><th>状态</th><th>上次检查</th><th>操作</th></tr></thead><tbody>{data.items.map(model=><tr key={model.id}>
    <td>{model.priority}</td><td><b>{model.name}</b><div>{model.model}</div></td><td>{model.base_url}</td>
    <td>{model.enabled?'已启用':'已停用'}<div>{states[model.last_status]||'未知状态'}</div>{model.last_http_status&&<small>HTTP {model.last_http_status}</small>}</td>
    <td>{date(model.last_checked_at)}</td><td><div className="button-row"><button className="secondary" disabled={busy||!!editing} onClick={()=>{setEditing(model);setFailure('');setMessage('')}}>编辑</button><button className="secondary" disabled={busy||!!editing} onClick={()=>void probe(model)}>{busy?'处理中…':'测试连接'}</button></div></td>
   </tr>)}</tbody></table>{!data.items.length&&<p>尚未配置平台模型。添加后可启用。</p>}</div>}
  </section>
  <section className="panel"><button className="secondary" aria-expanded={showReservations} onClick={()=>setShowReservations(!showReservations)}>{showReservations?'收起额度对账':'查看额度预留与对账'}</button>{showReservations&&<CreditReservations/>}</section>
  {editing&&<ModelEditor key={editing==='new'?'new':editing.id+':'+editing.revision} model={editing} onCancel={()=>setEditing(undefined)} onSaved={async()=>{setEditing(undefined);setMessage('模型配置已保存，后续请求按新优先级执行。');await refresh()}}/>}
 </>;
}

type Reservation={id:string;bot_id:string;tokens:number;created_at:string;dispatch_state:string;diagnostic_code:string|null;provider_id:string|null;dispatched_at:string|null;usage_tokens:number|null};

function CreditReservations(){
 const {data,error,refresh}=useLiveResource<{summary:{dispatch_state:string;count:number;tokens:string;oldest:string}[];items:Reservation[]}>('/admin/platform-model-reservations',0);
 const owner=useLifetime(),pending=useRef(false);const [busy,setBusy]=useState(false),[result,setResult]=useState('');
 const labels:Record<string,string>={reserved:'尚未派发',dispatched:'已派发，等待回执',unknown:'消耗无法确认',receipt:'已有用量回执，待结算'};
 async function reconcile(){
  const page=owner.current;if(!page||page.signal.aborted||pending.current)return;
  pending.current=true;setBusy(true);setResult('');
  try{const r=await api('/admin/platform-model-reservations/reconcile',{},'POST',page.signal);if(!page.signal.aborted){setResult(`已结算 ${r.settled} 笔真实回执。`);await refresh()}}
  catch(e){if(!page.signal.aborted)setResult((e as Error).message)}finally{pending.current=false;if(!page.signal.aborted)setBusy(false)}
 }
 async function waive(row:Reservation){
  const page=owner.current;if(!page||page.signal.aborted||pending.current)return;
  if(!window.confirm(`由平台承担这笔无法核实的历史消耗，解除 ${Number(row.tokens).toLocaleString()} Token 预留？原始 unknown 证据会保留，过期活动额度不会复活。`))return;
  pending.current=true;setBusy(true);setResult('');
  try{const r=await api('/admin/platform-model-reservations/'+encodeURIComponent(row.id)+'/waive',{expectedTokens:Number(row.tokens),expectedCreatedAt:new Date(row.created_at).toISOString(),reason:'管理员明确决定由平台承担无法核实的历史成本'},'POST',page.signal);if(!page.signal.aborted){setResult(r.released?'已解除历史预留并保存承担决定。':'该笔已处理，未重复解除。');await refresh()}}
  catch(e){if(!page.signal.aborted)setResult((e as Error).message)}finally{pending.current=false;if(!page.signal.aborted)setBusy(false)}
 }
 return <><h2>额度预留与对账</h2><p>仅结算已经保存的用量回执。未知消耗保留预留，不按时间自动退款，也不重新请求模型。只有管理员明确决定平台承担时，才可解除无派发记录的历史 unknown；原始证据留在审计中。</p><div className="button-row"><button className="secondary" disabled={busy} onClick={()=>void refresh()}>刷新预留</button><button className="secondary" disabled={busy||!data} onClick={()=>void reconcile()}>结算已有回执</button></div>{error&&<p role="alert">{error}</p>}{result&&<p role="status">{result}</p>}{data&&<><ul>{data.summary.map(r=><li key={r.dispatch_state}>{labels[r.dispatch_state]||r.dispatch_state}：{r.count} 笔，{Number(r.tokens).toLocaleString()} Token；最早 {date(r.oldest)}</li>)}</ul><div className="table-wrap"><table><thead><tr><th>预留 / Bot</th><th>状态</th><th>Token</th><th>时间</th><th>处理</th></tr></thead><tbody>{data.items.map(r=><tr key={r.id}><td>{r.id}<div>{r.bot_id}</div></td><td>{labels[r.dispatch_state]||r.dispatch_state}{r.diagnostic_code&&<div>{r.diagnostic_code}</div>}</td><td>{Number(r.tokens).toLocaleString()}</td><td>{date(r.created_at)}</td><td>{r.dispatch_state==='unknown'&&r.provider_id===null&&r.dispatched_at===null&&r.usage_tokens===null&&<button className="secondary" disabled={busy} onClick={()=>void waive(r)}>平台承担并解除</button>}</td></tr>)}</tbody></table></div><p className="muted">明细按时间显示最早 200 笔；汇总覆盖全部预留。</p></>}</>;
}

function ModelEditor({model,onCancel,onSaved}:{model:Model|'new';onCancel:()=>void;onSaved:()=>Promise<void>}){
 const existing=model==='new'?undefined:model;
 const [name,setName]=useState(existing?.name||''),[baseUrl,setBaseUrl]=useState(existing?.base_url||''),[modelName,setModelName]=useState(existing?.model||''),[priority,setPriority]=useState(existing?.priority??100),[enabled,setEnabled]=useState(existing?.enabled??true),[key,setKey]=useState(''),[busy,setBusy]=useState(false),[failure,setFailure]=useState('');
 const owner=useLifetime(),pending=useRef(false);
 async function save(){
  const page=owner.current;if(!page||page.signal.aborted||pending.current)return;
  request.current?.abort();edited.current=false;
  pending.current=true;setBusy(true);setFailure('');
  try{
   await api('/admin/platform-models'+(existing?'/'+encodeURIComponent(existing.id):''),{name,baseUrl,model:modelName,priority,enabled,...(key?{apiKey:key}:{}),...(existing?{expectedRevision:existing.revision}:{})},existing?'PUT':'POST',page.signal);
   if(!page.signal.aborted){setKey('');await onSaved()}
  }catch(e){if(!page.signal.aborted)setFailure((e as Error).message)}
  finally{pending.current=false;if(!page.signal.aborted)setBusy(false)}
 }
 const [catalog,setCatalog]=useState<any>(),[finding,setFinding]=useState(false);const edited=useRef(false),request=useRef<AbortController|null>(null),latest=useRef({baseUrl,key,modelName});latest.current={baseUrl,key,modelName};
 useEffect(()=>()=>request.current?.abort(),[]);
 useEffect(()=>{
  request.current?.abort();setFinding(false);setCatalog((current:any)=>current?.baseUrl===baseUrl?current:undefined);
  if(!edited.current||!baseUrl.trim()||(!key.trim()&&!existing))return;
  const controller=new AbortController();request.current=controller;
  const timer=setTimeout(async()=>{
   if(controller.signal.aborted)return;edited.current=false;setFinding(true);
   try{const result=await api('/admin/platform-models/discover',{baseUrl,...(key?{apiKey:key}:{}),...(existing?{providerId:existing.id}:{})},'POST',controller.signal);
    if(!controller.signal.aborted&&latest.current.baseUrl===baseUrl&&latest.current.key===key){setCatalog(result);if(result.ok){if(result.baseUrl!==baseUrl)setBaseUrl(result.baseUrl);if(!latest.current.modelName&&result.models?.length)setModelName(result.models[0].id)}}
   }catch(e){if(!controller.signal.aborted)setCatalog({message:(e as Error).message})}finally{if(!controller.signal.aborted)setFinding(false)}
  },700);
  return()=>{clearTimeout(timer);controller.abort()};
 },[baseUrl,key]);
 return <section className="panel" aria-label="平台模型编辑"><h2>{existing?'编辑模型':'添加模型'}</h2><form onSubmit={e=>{e.preventDefault();void save()}}>
  <div className="form-grid">
   <label className="field"><span>显示名称</span><input required maxLength={100} disabled={busy} value={name} onChange={e=>setName(e.target.value)}/></label>
   <label className="field"><span>模型 ID</span><input required list="platform-discovered-models" maxLength={200} disabled={busy} value={modelName} onChange={e=>setModelName(e.target.value)} placeholder="填写服务商实际模型 ID"/><datalist id="platform-discovered-models">{catalog?.models?.map((m:any)=><option key={m.id} value={m.id}/>)}</datalist></label>
   <label className="field"><span>接口 Base URL</span><input required type="text" inputMode="url" maxLength={6000} spellCheck={false} disabled={busy} value={baseUrl} onChange={e=>{edited.current=true;setBaseUrl(e.target.value)}} placeholder="直接粘贴服务商地址"/><small>自动整理地址、并行探测接口，成功后回填。</small></label>
   <label className="field"><span>API Key{existing?'（留空保留）':''}</span><input required={!existing} type="password" autoComplete="new-password" maxLength={4096} disabled={busy} value={key} onChange={e=>{edited.current=true;setKey(e.target.value)}}/><small>不回显已保存 Key。更换接口地址时需重新填写。</small></label>
   <label className="field"><span>优先级（越小越优先）</span><input type="number" min={0} max={10000} required disabled={busy} value={priority} onChange={e=>setPriority(Number(e.target.value))}/></label>
   <label className="field"><span>是否参与平台请求</span><select disabled={busy} value={enabled?'true':'false'} onChange={e=>setEnabled(e.target.value==='true')}><option value="true">启用</option><option value="false">停用</option></select></label>
  </div>
  {finding&&<p role="status">正在自动识别模型接口…</p>}{catalog?.message&&<p role="status">{catalog.message}</p>}{failure&&<p className="alert" role="alert">{failure}</p>}
  <div className="button-row"><button className="primary" disabled={busy||finding||!Number.isInteger(priority)}>{busy?'保存中…':'保存模型'}</button><button type="button" className="secondary" disabled={busy} onClick={onCancel}>取消</button></div>
 </form></section>;
}
