import {useState} from 'react';
import {AdminNav} from './admin-nav';
import {useLiveResource} from './hooks';
const date=(v:string|null)=>v?new Date(v).toLocaleString('zh-CN'):'—';
type User={id:string;name:string;email:string;generated:boolean;invited:number;copies:number;last_generated_at:string|null;last_copied_at:string|null;last_success_at:string|null};
type List<T>={items:T[];total:number;page:number;pageSize:number};
export function AdminReferrals(){
 const [text,setText]=useState(''),[q,setQ]=useState(''),[page,setPage]=useState(1),[selected,setSelected]=useState<User>();
 const {data,error,refresh}=useLiveResource<List<User>>('/admin/referrals?'+new URLSearchParams({q,page:String(page)}),0);
 return <>
  <div className="page-title"><div><span className="eyebrow">平台管理</span><h1>用户邀请</h1><p>查看用户的邀请链接记录与实际注册结果。</p></div></div><AdminNav active="referrals"/>
  <section className="panel"><h2>用户邀请情况</h2>
   <p className="muted">「已生成」依据平台邀请记录；复制次数为客户端报告。平台无法确认链接是否已发到外部聊天。历史链接的生成时间和过去的复制次数可能没有记录。</p>
   <form className="admin-toolbar" onSubmit={e=>{e.preventDefault();setSelected(undefined);setQ(text.trim());setPage(1);if(q===text.trim()&&page===1)void refresh()}}><label className="field"><span>搜索用户名称或邮箱</span><input maxLength={100} value={text} onChange={e=>setText(e.target.value)}/></label><button className="secondary">搜索</button><button type="button" className="secondary" onClick={()=>void refresh()}>刷新</button></form>
   {error&&<p className="alert" role="alert">{error}</p>}{!data&&!error&&<p role="status">正在读取邀请记录…</p>}
   {data&&<><p className="muted">共 {data.total} 个用户</p><div className="table-wrap"><table><thead><tr><th>用户</th><th>链接生成</th><th>复制记录</th><th>成功邀请</th><th>最近成功时间</th><th>操作</th></tr></thead><tbody>{data.items.map(user=><tr key={user.id}><td><b>{user.name}</b><div>{user.email}</div></td><td>{user.generated?'已生成':'暂无生成记录'}<div>{user.last_generated_at?date(user.last_generated_at):user.generated?'历史时间未知':'—'}</div></td><td>{user.copies?user.copies+' 次':'暂无复制记录'}<div>{date(user.last_copied_at)}</div></td><td>{user.invited} 人</td><td>{date(user.last_success_at)}</td><td><button className="secondary" aria-expanded={selected?.id===user.id} onClick={()=>setSelected(selected?.id===user.id?undefined:user)}>查看邀请结果</button></td></tr>)}</tbody></table>{!data.items.length&&<p>没有符合条件的用户。</p>}</div><div className="button-row"><button className="secondary" disabled={page<=1} onClick={()=>{setSelected(undefined);setPage(page-1)}}>上一页</button><span>第 {page} / {Math.max(1,Math.ceil(data.total/data.pageSize))} 页</span><button className="secondary" disabled={page*data.pageSize>=data.total} onClick={()=>{setSelected(undefined);setPage(page+1)}}>下一页</button></div></>}
  </section>
  {selected&&<ReferralDetails key={selected.id} user={selected} onClose={()=>setSelected(undefined)}/>}
 </>;
}
function ReferralDetails({user,onClose}:{user:User;onClose:()=>void}){
 const [page,setPage]=useState(1);
 const {data,error}=useLiveResource<List<{id:string;name:string;email:string;rewarded_at:string}>>('/admin/referrals/'+encodeURIComponent(user.id)+'?page='+page,0);
 return <section className="panel"><div className="section-heading"><h2>{user.name} · 成功邀请明细</h2><button className="secondary" onClick={onClose}>收起</button></div><p className="muted">只统计已完成注册并提交邀请奖励的用户，不将链接生成或复制计为成功。</p>{error&&<p className="alert" role="alert">{error}</p>}{!data&&!error&&<p role="status">正在读取该用户的邀请结果…</p>}{data&&<><div className="table-wrap"><table><thead><tr><th>受邀用户</th><th>邮箱</th><th>注册奖励提交时间</th></tr></thead><tbody>{data.items.map(item=><tr key={item.id}><td>{item.name}</td><td>{item.email}</td><td>{date(item.rewarded_at)}</td></tr>)}</tbody></table>{!data.items.length&&<p>还没有成功邀请记录。</p>}</div><div className="button-row"><button className="secondary" disabled={page<=1} onClick={()=>setPage(page-1)}>上一页</button><span>第 {page} 页 · 共 {data.total} 人</span><button className="secondary" disabled={page*data.pageSize>=data.total} onClick={()=>setPage(page+1)}>下一页</button></div></>}</section>;
}
