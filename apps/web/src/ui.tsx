import {runtimeStatus} from '../../../packages/shared/runtime-status';
import type {ReactNode} from 'react';
import {Fish,MessageCircle,Sparkles,KeyRound} from 'lucide-react';

export const go=(route:string)=>{location.hash=route};
export const memoryLabel=(bytes?:number)=>bytes?Math.round(bytes/1048576)+' MiB':'未采集';
export const memoryPolicyLabel=(policy?:any)=>{
 if(!policy)return '等待巡检';
 if(policy.decision==='disabled')return '已关闭';
 if(policy.reason==='no-swap')return '系统管理（未启用 Swap）';
 if(policy.reason==='native-operation-denied')return '系统管理（回收权限不可用）';
 if(policy.decision==='already-reclaimed'||policy.strategy==='windows-idle-working-set'||policy.strategy==='linux-idle-pageout')return '空闲回收已执行';
 if(policy.decision==='active')return '活动中，保持响应';
 if(policy.decision==='no-memory-pressure')return '内存充足，按需回收';
 if(policy.decision==='reserve-protection')return '低内存保护';
 if(policy.decision==='skipped')return '系统管理（等待有效实例）';
 return '自动优化，等待空闲';
};
export const labels:Record<string,string>={stale:'状态过期',offline:'离线（资源已释放）',stopped:'已停止',stopping:'停止中',starting:'启动中',online:'运行中',degraded:'等待QQ连接',failed:'异常',running:'运行',created:'已创建',qr_ready:'二维码已生成',waiting_scan:'等待扫码',waiting_confirm:'等待手机确认',verification_required:'需要额外验证',authenticating:'正在登录',expired:'二维码已过期',canceled:'已取消',provisioning:'准备中'};
export function Status({value,mock=false}:{value:string;mock?:boolean}){return <span className={'badge '+(value==='online'?'green':value==='failed'?'red':'')}><i/>{mock&&value==='online'?'模拟在线':labels[value]||value}</span>}
export function Field({label,hint,children}:{label:string;hint?:string;children:ReactNode}){return <label className="field"><span>{label}</span>{children}{hint&&<small>{hint}</small>}</label>}
export function Stat({title,value,icon,note}:{title:string;value:number|null;icon:ReactNode;note:string}){return <div className="stat"><div><span>{title}</span><strong>{value===null?'未知':value.toString().padStart(2,'0')}</strong><small>{note}</small></div><span className="stat-icon">{icon}</span></div>}
export function BotAvatar({persona='dafeiyu',large=false}:{persona?:string;large?:boolean}){return <span className={'bot-avatar '+(large?'large':'')}>{persona==='dafeiyu'?<img src="/illustrations/dafeiyu-rice.png" alt=""/>:<Fish size={large?32:24}/>}</span>}
export function BotSignals({bot,hasKey}:{bot:any;hasKey:boolean}){const r=runtimeStatus(bot),stale=!!bot.health?.sampledAt&&!r.fresh;return <div className="bot-signals"><span className={r.online?'signal good':'signal'}><MessageCircle size={14}/><b>QQ</b><span>{bot.protocol==='mock'?(r.online?'模拟在线':'模拟协议'):r.online?'在线':stale?'状态过期':'未连接'}</span></span><span className={r.aiReady?'signal good':'signal'}><Sparkles size={14}/><b>AI</b><span>{r.aiReady?'就绪':r.aiAlive?'初始化中':stale?'状态过期':'未运行'}</span></span><span className={hasKey?'signal good':'signal'}><KeyRound size={14}/><b>Key</b><span>{hasKey?'已保存':'未配置'}</span></span></div>}
