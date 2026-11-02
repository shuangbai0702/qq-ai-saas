import {useRef,useState} from 'react';
import {api} from './api';
import {useLifetime,useLiveResource} from './hooks';
import {autonomousStates,autonomousOutcomes,autonomousDate,autonomousQQStatus} from './autonomous-status';

export function AdminAutonomous(){
 const [filter,setFilter]=useState('attention'),[page,setPage]=useState(1),[selected,setSelected]=useState<any>(),[edit,setEdit]=useState<any>();
 const [reason,setReason]=useState(''),[busy,setBusy]=useState(false),[notice,setNotice]=useState(''),[failure,setFailure]=useState('');
 const pending=useRef(false),owner=useLifetime();
 const {data,error,refresh}=useLiveResource('/admin/autonomous?'+new URLSearchParams({filter,page:String(page)}),15000);
 const pages=data?Math.max(1,Math.ceil(data.total/data.pageSize)):1;
 async function pause(){
  const lifetime=owner.current,target=edit;if(!lifetime||lifetime.signal.aborted||pending.current||!target||reason.trim().length<2)return;
  pending.current=true;setBusy(true);setFailure('');
  try{
   await api('/admin/autonomous/'+target.bot_id+'/pause',{expectedEpoch:target.epoch,reason:reason.trim()},'POST',lifetime.signal);
   if(lifetime.signal.aborted)return;
   setEdit(undefined);setNotice('已保存暂停操作和原因，等待运行实例确认；QQ 连接保持运行。');await refresh();
  }catch(e){
   if(!lifetime.signal.aborted){setEdit(undefined);setFailure((e as Error).message);await refresh()}
  }finally{pending.current=false;if(!lifetime.signal.aborted)setBusy(false)}
 }
 function changeFilter(value:string){if(pending.current)return;setFilter(value);setPage(1);setSelected(undefined);setEdit(undefined)}
 return <section className="panel admin-operations" aria-label="自主任务运维"><h2>自主任务运维</h2><p className="muted">查看各机器人的自主状态与后台用量，核对原始回执，或带原因暂停指定机器人。</p>
  <div className="admin-toolbar"><label className="field"><span>自主任务筛选</span><select disabled={busy} value={filter} onChange={e=>changeFilter(e.target.value)}><option value="attention">需要关注</option><option value="active">主动模式</option><option value="all">全部自主状态</option></select></label><button className="secondary" disabled={busy} onClick={()=>void refresh()}>刷新自主状态</button></div>
  {(error||failure)&&<p className="alert" role="alert">{failure||error}</p>}{notice&&<p role="status">{notice}</p>}
  {edit&&<section className="notice" aria-label="暂停单个机器人的主动模式"><h3>暂停主动模式：{edit.name}</h3><p>保留目标、账号配置和已有执行回执。已派发的操作继续按真实回执结算。</p><label className="field"><span>暂停原因</span><input value={reason} maxLength={200} disabled={busy} onChange={e=>setReason(e.target.value)}/><small>填写2至200字，随操作记录保存。</small></label><div className="button-row"><button className="danger" disabled={busy||reason.trim().length<2} onClick={()=>void pause()}>{busy?'正在提交…':'确认暂停主动模式'}</button><button className="secondary" disabled={busy} onClick={()=>setEdit(undefined)}>取消暂停</button></div></section>}
  {!data?<p role="status">正在读取自主任务…</p>:<><p className="muted">共 {data.total} 个匹配机器人 · 第 {page} / {pages} 页 · 每15秒更新</p><div className="table-wrap"><table><thead><tr><th>机器人</th><th>QQ / 自主模式</th><th>自主脑状态</th><th>今日后台用量</th><th>执行结果</th><th>操作</th></tr></thead><tbody>{data.items.map((item:any)=><tr key={item.bot_id}>
   <td><b>{item.name}</b><small className="admin-group-id">{item.bot_id}</small><details><summary>所属工作区</summary><code>{item.tenant_id}</code></details></td>
   <td><span>{autonomousQQStatus(item)}</span><div>{item.mode==='active'?'主动':'被动'} · {item.rollout==='live'?'实际执行':'影子运行'}</div><small>自主配置 v{item.epoch} / 已生效 v{item.runtime_epoch}{item.epoch!==item.runtime_epoch?' · 待实例确认':''}</small></td>
   <td>{autonomousStates[item.state]||item.state}<small>下次唤醒：{autonomousDate(item.next_wake_at)}</small>{item.last_error&&<p className="notice">{item.last_error}</p>}</td>
   <td>{item.usage?<>{Number(item.usage.usedTokens).toLocaleString('zh-CN')} Token<small>预留 {item.usage.reservedTokens} · 模型 {item.usage.modelCalls} 次<br/>工具 {item.usage.toolCalls} 次 · 对外操作 {item.usage.externalActions} 次</small></>:'暂无用量记录'}</td>
   <td>{item.unconfirmed?'有结果待核对':'暂无未确认效果'}<small>结果不明时核对原动作，暂停后仍会继续查证。</small></td>
   <td><div className="button-row"><button className="secondary" disabled={busy} onClick={()=>setSelected({id:item.bot_id,name:item.name})}>查看回执</button><button className="danger" disabled={busy||item.mode!=='active'} onClick={()=>{setEdit(item);setReason('');setFailure('');setNotice('')}}>暂停主动模式</button></div></td>
  </tr>)}</tbody></table>{!data.items.length&&<p>当前没有符合筛选条件的自主状态。</p>}</div><div className="button-row"><button className="secondary" disabled={busy||page<=1} onClick={()=>{setPage(p=>p-1);setSelected(undefined);setEdit(undefined)}}>上一页</button><button className="secondary" disabled={busy||page>=pages} onClick={()=>{setPage(p=>p+1);setSelected(undefined);setEdit(undefined)}}>下一页</button></div></>}
  {selected&&<ReceiptDetails key={selected.id} id={selected.id} name={selected.name} onClose={()=>setSelected(undefined)}/>}
 </section>
}

function ReceiptDetails({id,name,onClose}:{id:string;name:string;onClose:()=>void}){
 const {data,error,refresh}=useLiveResource('/admin/autonomous/'+id+'/actions',30000);
 return <section className="memory-group" aria-label="未确认动作回执"><div className="memory-heading"><h3>{name} · 原始动作回执</h3><button className="secondary" onClick={onClose}>关闭回执详情</button></div>
  {error&&<p className="alert" role="alert">{error}</p>}{!data?<p>正在读取原始回执…</p>:<><p>{data.reason}</p><small>每{data.automaticReceiptCheckSeconds}秒核对一次；此处仅展示原动作状态。</small>{data.items.length?data.items.map((action:any)=><article className="memory-group" key={action.id}><b>{autonomousOutcomes[action.status]||action.status} · {action.kind}</b><p>目标：{action.target} · {autonomousDate(action.created_at)}</p><details><summary>动作身份与回执</summary><p>动作 <code>{action.id}</code><br/>周期 <code>{action.cycle_id}</code> · 自主配置 v{action.epoch}</p>{(action.effects||[]).map((effect:any)=><p key={effect.id}><code>{effect.id}</code> · {effect.status==='confirmed'?'已确认':effect.status==='unknown'?'未确认':effect.status}<br/>协议回执：{effect.receipt==null?'尚无确认':String(effect.receipt)}</p>)}</details></article>):<p>暂无未确认的外部效果。</p>}</>}
  <button className="secondary" onClick={()=>void refresh()}>刷新原始回执</button>
 </section>
}
