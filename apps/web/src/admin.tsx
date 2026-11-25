import {lazy,Suspense,useState} from 'react';
import {AdminNav} from './admin-nav';
import {Status} from './ui';
import {useLiveResource} from './hooks';
const AdminOperations=lazy(()=>import('./admin-operations').then(m=>({default:m.AdminOperations})));
const AdminAutonomous=lazy(()=>import('./admin-autonomous').then(m=>({default:m.AdminAutonomous})));
const AdminMemory=lazy(()=>import('./admin-memory').then(m=>({default:m.AdminMemory})));
export function Admin({onError,section='operations'}:{onError:(s:string)=>void;section?:string}){const memory=section==='memory',autonomous=section==='autonomous';return <><div className="page-title"><div><span className="eyebrow">平台管理</span><h1>{memory?'内存与 QQ 监控':autonomous?'自主任务与执行回执':'平台运营与监控'}</h1><p>{memory?'按实际进程查看 RAM、Swap 和 QQ 运行状态。':autonomous?'分别查看QQ连接、自主脑状态与真实执行结果。':'按账号处理部署名额、连接排障和操作记录。'}</p></div></div><AdminNav active={memory?'memory':autonomous?'autonomous':'operations'}/><Suspense fallback={<div className="empty" role="status">正在加载管理分区…</div>}>{memory?<><AdminMemory/><AdminRuntimeDetails/></>:autonomous?<AdminAutonomous/>:<AdminOperations/>}</Suspense></>}
function AdminRuntimeTable(){const {data:d,error}=useLiveResource('/admin/overview',30000);return <>{error&&<p role="alert">{error}</p>}{d&&<section><h2>运行实例</h2><div className="table-wrap"><table><thead><tr><th>名称</th><th>协议</th><th>状态</th><th>AI PID</th><th>协议 PID</th><th>异常</th></tr></thead><tbody>{d.bots.map((b:any)=><tr key={b.id}><td>{b.name}</td><td>{b.protocol}</td><td><Status value={b.observed_state} mock={b.protocol==='mock'}/></td><td>{b.health?.ai?.pid||'—'}</td><td>{b.health?.protocol?.pid||'—'}</td><td>{b.last_error||'—'}</td></tr>)}</tbody></table></div></section>}</>}

function AdminRuntimeDetails(){const [open,setOpen]=useState(false);return <details className="panel" onToggle={event=>setOpen(event.currentTarget.open)}><summary>实例原始状态与错误</summary>{open&&<AdminRuntimeTable/>}</details>}
