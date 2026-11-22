import {runtimeStatus,deliveryWarning} from '../../../packages/shared/runtime-status';
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
export function botStatus(bot:any){
 if(bot.desired_state==='deleting')return 'deleting';
 if(bot.desired_state==='offline')return bot.observed_state==='offline'?'offline':bot.observed_state==='failed'?'failed':'stopping';
 if(bot.desired_state==='stopped')return ['stopped','created','offline'].includes(bot.observed_state)?'stopped':'stopping';
 const r=runtimeStatus(bot);if(r.online)return 'online';
 if(bot.observed_state==='failed')return 'failed';
 if(bot.observed_state==='starting'||bot.observed_state==='stopped')return 'starting';
 if(!r.fresh)return 'stale';return 'degraded';
}
export const labels:Record<string,string>={deleting:'删除中',stale:'状态过期',offline:'离线（资源已释放）',stopped:'已停止',stopping:'停止中',starting:'启动中',online:'运行中',degraded:'等待QQ连接',failed:'异常',running:'运行',created:'已创建',qr_ready:'二维码已生成',waiting_scan:'等待扫码',waiting_confirm:'等待手机确认',verification_required:'需要额外验证',authenticating:'正在登录',expired:'二维码已过期',canceled:'已取消',provisioning:'准备中'};
const commandLabels:Record<string,string>={start:'启动机器人',stop:'停止机器人',restart:'重启实例',delete:'删除机器人',config_apply:'应用配置',apply_config:'应用配置',apply:'应用配置',qq_login:'登录 QQ',qq_logout:'退出 QQ',qq_resume:'恢复 QQ 会话',login:'登录 QQ',logout:'退出 QQ',mode:'切换运行模式',runtime:'切换运行模式',reconcile:'状态巡检'};
const commandStatusLabels:Record<string,string>={succeeded:'已完成',done:'已完成',completed:'已完成',applied:'已生效',running:'执行中',in_progress:'执行中',pending:'等待执行',queued:'排队中',failed:'失败',error:'失败',canceled:'已取消',cancelled:'已取消'};
export const commandLabel=(kind?:string)=>kind?commandLabels[kind]||kind:'操作';
export const commandStatusLabel=(status?:string)=>status?commandStatusLabels[status]||status:'未知';
export const commandStatusTone=(status?:string)=>['succeeded','done','completed','applied'].includes(status||'')?'green':['failed','error'].includes(status||'')?'red':['running','in_progress','pending','queued'].includes(status||'')?'amber':'';
export function Status({value,mock=false}:{value:string;mock?:boolean}){const tone=value==='online'?'green':value==='failed'?'red':['degraded','starting','stopping','stale','waiting_scan','waiting_confirm','authenticating','verification_required'].includes(value)?'amber':'';return <span className={'badge'+(tone?' '+tone:'')}><i/>{mock&&value==='online'?'模拟在线':labels[value]||value}</span>}
export function Field({label,hint,children}:{label:string;hint?:string;children:ReactNode}){return <label className="field"><span>{label}</span>{children}{hint&&<small>{hint}</small>}</label>}
export function Stat({title,value,icon,note}:{title:string;value:number|null;icon:ReactNode;note:string}){return <div className="stat"><div><span>{title}</span><strong>{value===null?'未知':value.toString().padStart(2,'0')}</strong><small>{note}</small></div><span className="stat-icon">{icon}</span></div>}
export function BotAvatar({persona='dafeiyu',large=false}:{persona?:string;large?:boolean}){return <span className={'bot-avatar '+(large?'large':'')}>{persona==='dafeiyu'?<img src="/illustrations/dafeiyu-rice.png" alt=""/>:<Fish size={large?32:24}/>}</span>}
export function BotSignals({bot,hasKey}:{bot:any;hasKey:boolean}){const r=runtimeStatus(bot),stale=!!bot.health?.sampledAt&&!r.fresh,warning=deliveryWarning(bot),wantRun=bot.desired_state==='running';return <div className="bot-signals"><span className={r.online&&!warning?'signal good':wantRun?'signal amber':'signal'}><MessageCircle size={14}/><b>QQ</b><span>{bot.protocol==='mock'?(r.online?'模拟在线':'模拟协议'):r.online?'在线':stale?'状态过期':'未连接'}</span></span><span className={r.aiReady?'signal good':wantRun&&r.aiAlive?'signal amber':'signal'}><Sparkles size={14}/><b>AI</b><span>{r.aiReady?'就绪':r.aiAlive?'初始化中':stale?'状态过期':'未运行'}</span></span>{warning&&<span className="signal amber" role="status" title="登录在线不代表消息送达。后续真实发送确认后清除此提示；不会自动补发旧消息。">{warning}</span>}<span className={hasKey?'signal good':'signal red'}><KeyRound size={14}/><b>Key</b><span>{hasKey?'已保存':'未配置'}</span></span></div>}
