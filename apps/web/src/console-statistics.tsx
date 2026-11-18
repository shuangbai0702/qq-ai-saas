import {useState} from 'react';
import {AdminNav} from './admin-nav';
import {useLiveResource} from './hooks';
import './console-statistics.css';

type Bot={id:string;name:string;qq:string|null;workspace:string;protocol:string;online:boolean;aiReady:boolean};
type RequestStat={id:string;at:number;model:string|null;phase:string|null;status:string|null;wireCount:number|null;inputTokens:number|null;cachedTokens:number|null;textChars:number|null;toolsChars:number|null;imageCount:number|null};
type Statistics={sampledAt:number;rounds:{count:number|null;threshold:number;status:string};requests:RequestStat[];partial:boolean};
const metric=(value:number|null)=>value===null?'未返回':value.toLocaleString();
const date=(at:number)=>new Date(at*1000).toLocaleString();
const phases:Record<string,string>={foreground:'主线对话',inner_execution:'工具续接',small_summary:'轮次摘要',big_merge:'记忆合并'};

export function ConsoleStatistics(){
 const [page,setPage]=useState(1),[filter,setFilter]=useState('online'),[selected,setSelected]=useState<Bot>();
 const {data,error,refresh}=useLiveResource<{items:Bot[];total:number;pageSize:number}>('/admin/bots?'+new URLSearchParams({filter,page:String(page),realOnly:'true'}),0);
 return <><AdminNav active="statistics"/><section className="panel console-statistics"><div className="section-heading"><div><h1>运行统计</h1><p>点开账号查看当前轮数与最近模型请求；手动刷新，不自动轮询。</p></div><button className="secondary" onClick={()=>void refresh()}>刷新账号</button></div>
 <div className="tabs" aria-label="账号范围">{[['online','当前在线'],['all','全部账号']].map(([value,label])=><button key={value} className={filter===value?'active':''} onClick={()=>{setSelected(undefined);setPage(1);setFilter(value)}}>{label}</button>)}</div>
 {error&&<p role="alert" className="alert">{error}</p>}{!data&&!error&&<p role="status">正在读取账号…</p>}
 {data&&<><p>共 {data.total} 个{filter==='online'?'在线':''}账号</p><div className="stats-accounts">{data.items.map(bot=><button key={bot.id} className={'secondary stats-account'+(selected?.id===bot.id?' active':'')} aria-pressed={selected?.id===bot.id} onClick={()=>setSelected(selected?.id===bot.id?undefined:bot)}><strong>{bot.name}</strong><span>QQ {bot.qq||'未绑定'}</span><small>{bot.workspace} · {bot.online?'QQ 在线':'QQ 未连接'} · {bot.aiReady?'AI 就绪':'AI 未运行'}</small></button>)}</div>{!data.items.length&&<p>当前没有符合条件的账号。</p>}
 <div className="button-row"><button className="secondary" disabled={page<=1} onClick={()=>{setSelected(undefined);setPage(page-1)}}>上一页</button><span>第 {page} / {Math.max(1,Math.ceil(data.total/data.pageSize))} 页</span><button className="secondary" disabled={page*data.pageSize>=data.total} onClick={()=>{setSelected(undefined);setPage(page+1)}}>下一页</button></div></>}
 </section>{selected&&<AccountStatistics key={selected.id} bot={selected} onClose={()=>setSelected(undefined)}/>}</>;
}

function AccountStatistics({bot,onClose}:{bot:Bot;onClose:()=>void}){
 const {data,error,refresh}=useLiveResource<Statistics>(`/admin/bots/${encodeURIComponent(bot.id)}/statistics`,0);
 return <section className="panel console-statistics" aria-label={bot.name+'统计'}><div className="section-heading"><h2>{bot.name} · 请求与轮次</h2><div className="button-row"><button className="secondary" onClick={()=>void refresh()}>刷新统计</button><button className="secondary" onClick={onClose}>收起</button></div></div>
 {error&&<p className="alert" role="alert">{error}</p>}{!data&&!error&&<p role="status">正在读取该账号统计…</p>}
 {data&&<><div className="stats-round"><strong>当前有效业务轮：{data.rounds.count===null?'暂不可读':metric(data.rounds.count)} / {data.rounds.threshold}</strong><span>bot_shared · 上次压缩后已结算轮次{data.rounds.count!==null&&data.rounds.count>=data.rounds.threshold?' · 已到压缩阈值':''}</span><small>读取于 {date(data.sampledAt)}</small></div>
 <h3>最近 {data.requests.length} 次已结束的模型调用</h3><p className="muted">输入 Token 为服务商实际回执，含缓存命中部分。字符数为最后一次发送的消息序列化长度（去除图片数据），工具定义单独计数；不将字符数换算成 Token。</p>
 {data.partial&&<p role="status">诊断记录存在截断或不可读片段，仅展示可确认的数据。</p>}
 <div className="stats-request-list">{data.requests.map(row=><article key={row.id} className="stats-request"><header><strong>{row.model||'模型未记录'}</strong><time>{date(row.at)}</time><span>{phases[row.phase||'']||row.phase||'阶段未知'} · {row.status==='completed'?'完成':row.status==='canceled'?'已取消':row.status==='failed'?'失败':row.status||'未知'}</span></header><dl>{[['输入 Token',row.inputTokens],['命中缓存 Token',row.cachedTokens],['上下文字符',row.textChars],['工具定义字符',row.toolsChars],['图片数',row.imageCount]].map(([label,value])=><div key={label}><dt>{label}</dt><dd>{metric(value as number|null)}</dd></div>)}</dl>{row.wireCount!==null&&row.wireCount>1&&<small>本次包含 {row.wireCount} 次发送；上列长度对应最后一次发送。</small>}</article>)}</div>{!data.requests.length&&<p>保留的诊断记录中暂无可展示的模型请求。</p>}</>}
 </section>;
}
