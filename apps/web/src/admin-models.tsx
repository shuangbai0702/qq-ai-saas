import {useRef,useState} from 'react';
import {AdminNav} from './admin-nav';
import {api} from './api';
import {useLifetime,useLiveResource} from './hooks';

type Model={id:string;name:string;base_url:string;model:string;priority:number;enabled:boolean;revision:number;last_status:string;last_checked_at:string|null;last_http_status:number|null};
const states:Record<string,string>={unchecked:'尚未验证',available:'上次请求成功',limited:'上次额度或频率受限',rejected:'上次认证或权限被拒绝',failed:'上次请求失败',unconfirmed:'上次结果无法确认'};
const date=(value:string|null)=>value?new Date(value).toLocaleString('zh-CN'):'—';

export function AdminModels(){
 const {data,error,refresh}=useLiveResource<{items:Model[]}>('/admin/platform-models',0);
 const [editing,setEditing]=useState<Model|'new'>(),[busy,setBusy]=useState(false),[message,setMessage]=useState(''),[failure,setFailure]=useState('');
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
   <p className="muted">只有供应商明确拒绝额度时才按顺序切换；超时、连接中断或用量不明时保留原有保护，避免重复请求和计费。</p>
   {(error||failure)&&<p className="alert" role="alert">{failure||error}</p>}{message&&<p role="status">{message}</p>}
   {!data&&!error&&<p role="status">正在读取模型…</p>}
   {data&&<div className="table-wrap"><table><thead><tr><th>优先级</th><th>名称 / 模型</th><th>接口地址</th><th>状态</th><th>上次检查</th><th>操作</th></tr></thead><tbody>{data.items.map(model=><tr key={model.id}>
    <td>{model.priority}</td><td><b>{model.name}</b><div>{model.model}</div></td><td>{model.base_url}</td>
    <td>{model.enabled?'已启用':'已停用'}<div>{states[model.last_status]||'未知状态'}</div>{model.last_http_status&&<small>HTTP {model.last_http_status}</small>}</td>
    <td>{date(model.last_checked_at)}</td><td><div className="button-row"><button className="secondary" disabled={busy||!!editing} onClick={()=>{setEditing(model);setFailure('');setMessage('')}}>编辑</button><button className="secondary" disabled={busy||!!editing} onClick={()=>void probe(model)}>{busy?'处理中…':'测试连接'}</button></div></td>
   </tr>)}</tbody></table>{!data.items.length&&<p>尚未配置平台模型。添加后可启用。</p>}</div>}
  </section>
  {editing&&<ModelEditor key={editing==='new'?'new':editing.id+':'+editing.revision} model={editing} onCancel={()=>setEditing(undefined)} onSaved={async()=>{setEditing(undefined);setMessage('模型配置已保存，后续请求按新优先级执行。');await refresh()}}/>}
 </>;
}

function ModelEditor({model,onCancel,onSaved}:{model:Model|'new';onCancel:()=>void;onSaved:()=>Promise<void>}){
 const existing=model==='new'?undefined:model;
 const [name,setName]=useState(existing?.name||''),[baseUrl,setBaseUrl]=useState(existing?.base_url||''),[modelName,setModelName]=useState(existing?.model||''),[priority,setPriority]=useState(existing?.priority??100),[enabled,setEnabled]=useState(existing?.enabled??true),[key,setKey]=useState(''),[busy,setBusy]=useState(false),[failure,setFailure]=useState('');
 const owner=useLifetime(),pending=useRef(false);
 async function save(){
  const page=owner.current;if(!page||page.signal.aborted||pending.current)return;
  pending.current=true;setBusy(true);setFailure('');
  try{
   await api('/admin/platform-models'+(existing?'/'+encodeURIComponent(existing.id):''),{name,baseUrl,model:modelName,priority,enabled,...(key?{apiKey:key}:{}),...(existing?{expectedRevision:existing.revision}:{})},existing?'PUT':'POST',page.signal);
   if(!page.signal.aborted){setKey('');await onSaved()}
  }catch(e){if(!page.signal.aborted)setFailure((e as Error).message)}
  finally{pending.current=false;if(!page.signal.aborted)setBusy(false)}
 }
 return <section className="panel" aria-label="平台模型编辑"><h2>{existing?'编辑模型':'添加模型'}</h2><form onSubmit={e=>{e.preventDefault();void save()}}>
  <div className="form-grid">
   <label className="field"><span>显示名称</span><input required maxLength={100} disabled={busy} value={name} onChange={e=>setName(e.target.value)}/></label>
   <label className="field"><span>模型 ID</span><input required maxLength={200} disabled={busy} value={modelName} onChange={e=>setModelName(e.target.value)} placeholder="填写服务商实际模型 ID"/></label>
   <label className="field"><span>接口 Base URL</span><input required type="url" maxLength={2000} disabled={busy} value={baseUrl} onChange={e=>setBaseUrl(e.target.value)} placeholder="https://example.com/v1"/></label>
   <label className="field"><span>API Key{existing?'（留空保留）':''}</span><input required={!existing} type="password" autoComplete="new-password" maxLength={4096} disabled={busy} value={key} onChange={e=>setKey(e.target.value)}/><small>不回显已保存 Key。更换接口地址时需重新填写。</small></label>
   <label className="field"><span>优先级（越小越优先）</span><input type="number" min={0} max={10000} required disabled={busy} value={priority} onChange={e=>setPriority(Number(e.target.value))}/></label>
   <label className="field"><span>是否参与平台请求</span><select disabled={busy} value={enabled?'true':'false'} onChange={e=>setEnabled(e.target.value==='true')}><option value="true">启用</option><option value="false">停用</option></select></label>
  </div>
  {failure&&<p className="alert" role="alert">{failure}</p>}
  <div className="button-row"><button className="primary" disabled={busy||!Number.isInteger(priority)}>{busy?'保存中…':'保存模型'}</button><button type="button" className="secondary" disabled={busy} onClick={onCancel}>取消</button></div>
 </form></section>;
}
