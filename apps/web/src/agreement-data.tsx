import {useState} from 'react';
import {EditDialog} from './edit-dialog';
type Agreement={id:string;revision:number;topic:string;detail:string;status:string;due:string|null;source_scope:string;handoff_id:string|null};
const labels:Record<string,string>={proposed:'提议',accepted:'已接受',fulfilled:'已履行',canceled:'已取消',expired:'已过期',uncertain:'待确认'};
export function AgreementData({rows,busy,onMutate}:{rows?:Agreement[];busy:boolean;onMutate:(value:unknown)=>Promise<string|undefined>}){
 const [editing,setEditing]=useState<Agreement|null>(null);
 return <details className="advanced"><summary>约定</summary><p className="muted">可直接纠正约定内容与状态。手动更正不会伪造消息回执或工具执行结果；已关联任务的约定请在任务入口管理。</p>{!rows?.length?<p>暂无约定。</p>:rows.map(row=><article className="managed-item" key={row.id}><b>{row.topic}</b><p>{row.detail}</p><small>{labels[row.status]??row.status} · {row.source_scope}{row.due?' · '+row.due:''}</small><button type="button" className="secondary" disabled={busy||!!row.handoff_id} onClick={()=>setEditing(row)}>编辑约定</button></article>)}
 {editing&&<EditDialog title="编辑约定" fields={[{key:'topic',label:'主题',value:editing.topic,maxLength:120,required:true},{key:'detail',label:'具体内容',value:editing.detail,maxLength:200},{key:'due',label:'时间或期限',value:editing.due??'',maxLength:120},{key:'status',label:'状态：提议、已接受、已履行、已取消、已过期或待确认',value:labels[editing.status]??editing.status,maxLength:8,required:true}]} onClose={()=>setEditing(null)} onSave={async values=>{const status=Object.keys(labels).find(key=>labels[key]===values.status.trim());if(!status)throw Error('请选择所列的约定状态');const error=await onMutate({action:'edit_agreement',id:editing.id,expectedRevision:editing.revision,...values,status});if(error)throw Error(error)}}/>}
 </details>;
}
