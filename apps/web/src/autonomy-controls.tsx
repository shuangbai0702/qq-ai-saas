import {useEffect,useRef,useState} from 'react';
import type {BotConfig} from '../../../packages/contracts/index';
import {Field} from './ui';

type Props={config:BotConfig;onChange:(config:BotConfig)=>void;kind:'group'|'owner';metrics?:any};
export function AutonomyControls({config,onChange,kind,metrics}:Props){
 const b=config.behavior||{} as NonNullable<BotConfig['behavior']>;
 const update=(value:Partial<NonNullable<BotConfig['behavior']>>)=>onChange({...config,behavior:{...b,...value} as NonNullable<BotConfig['behavior']>});
 const normalized=(b.groupParticipationGroups||[]).join(', '),[groups,setGroups]=useState(normalized),input=useRef<HTMLInputElement>(null);
 useEffect(()=>{if(document.activeElement!==input.current)setGroups(normalized)},[normalized]);
 if(kind==='owner')return <section className="panel" aria-label="定时关心主人"><h2>定时判断是否找主人聊天</h2><p>这是无人发来消息时的自主行动。共用定时联系总开关、安静时段、每小时自主发送总额、每日定时发送额度和未回应保护；不受群判断计时影响。</p>
  <label className="toggle-row"><div><b>定时主动联系总开关</b><small>同时控制主人联系和下方其他定时联系人；不控制正常回复与群内有新消息时的参与判断。</small></div><input aria-label="定时主动联系总开关" type="checkbox" checked={config.proactive} onChange={e=>onChange({...config,proactive:e.target.checked})}/></label>
  <label className="toggle-row"><div><b>定时关心主人</b><small>只向设置的主人发起私聊，需已有真实私聊记录。模型判断有自然动机才发；不使用主动联系概率抽签。</small></div><input aria-label="定时关心主人" type="checkbox" checked={b.ownerContactEnabled??true} onChange={e=>update({ownerContactEnabled:e.target.checked})}/></label>
  <Field label="主人 QQ" hint="此身份与认主、QQ工具共用；昵称和聊天内容不能改写它。"><input aria-label="主人 QQ" inputMode="numeric" maxLength={20} value={b.ownerUin||''} onChange={e=>update({ownerUin:e.target.value.replace(/\D/g,'')})}/></Field>
  {(!config.proactive||!b.ownerUin||b.enabled===false)&&<p className="notice">暂不执行：{b.enabled===false?'聊天总开关已关闭':!config.proactive?'定时主动联系总开关已关闭':'尚未设置主人 QQ'}。设置的偏好会保留。</p>}
  <div className="form-grid"><Field label="主人判断间隔（分钟）" hint="默认30分钟；私聊或群里与主人真实互动也会刷新等待时间。"><input aria-label="主人判断间隔（分钟）" type="number" min={15} max={1440} value={b.ownerContactIntervalMinutes??30} onChange={e=>update({ownerContactIntervalMinutes:Number(e.target.value)})}/></Field>
   <Field label="主人安静多久后可联系（分钟）"><input aria-label="主人安静多久后可联系（分钟）" type="number" min={5} max={1440} value={b.ownerContactIdleMinutes??10} onChange={e=>update({ownerContactIdleMinutes:Number(e.target.value)})}/></Field>
   <Field label="每日主人判断上限" hint="判断调用的上限，不等于实际发消息数量。设0不调用；发送仍共用下方每日定时联系额度。"><input aria-label="每日主人判断上限" type="number" min={0} max={48} value={b.ownerContactDailyChecks??48} onChange={e=>update({ownerContactDailyChecks:Number(e.target.value)})}/></Field></div>
  <p>主人没有回应或明确拒绝时停止追问；在其他群与同一个主人交流也会推迟私聊。首次启用先等待一个判断间隔，不会立即发送。</p>
  <p role="status">最近主人判断：{metrics?.lastOwnerDecision?`${new Date(metrics.lastOwnerDecision.at*1000).toLocaleString()} · ${metrics.lastOwnerDecision.contact?'愿意联系，等待发送条件':'保持安静或请求未完成'}`:'暂无记录；需要开启、已有真实私聊并达到等待条件'}</p>
 </section>;
 return <section className="panel" aria-label="白名单群自主参与"><h2>白名单群自主参与</h2><p>这是首次加入或结束后重新加入话题。正常 @ 回复、引用回复与已开始的跟聊走各自链路，不重复等待自主入场的判断间隔。</p>
  <label className="toggle-row"><div><b>群语义参与判断</b><small>判断话题是否与自己有关，或自己是否想参与；替代随机搭话入口，不新增常驻模型。</small></div><input aria-label="群语义参与判断" type="checkbox" checked={b.groupParticipationEnabled??true} onChange={e=>update({groupParticipationEnabled:e.target.checked})}/></label>
  <Field label="自主参与白名单群号" hint="只在这些群主动入场；留空全部不启用。与上方服务群范围取交集，不会自动加入群；会话免打扰或拒绝主动联系仍然有效。"><input aria-label="自主参与白名单群号" ref={input} value={groups} maxLength={2200} placeholder="用逗号或空格分隔群号" onChange={e=>{setGroups(e.target.value);update({groupParticipationGroups:[...new Set(e.target.value.split(/[,，\s]+/).filter(Boolean))]})}}/></Field>
  {!b.groupParticipationGroups?.length&&<p className="notice">尚未选择白名单群，自主入场不会执行；正常被叫回复和已开始的跟聊仍按设置运行。</p>}
  <div className="form-grid"><Field label="群判断最短间隔（分钟）" hint="有新消息，且距上次判断或本群最近真实互动至少这么久；两项同时满足。冷却内收到的消息会保留，到期自动判断，无需再发一条；真实聊天会刷新，普通旁观消息不会刷新。"><input aria-label="群判断最短间隔（分钟）" type="number" min={10} max={1440} value={b.groupParticipationIntervalMinutes??10} onChange={e=>update({groupParticipationIntervalMinutes:Number(e.target.value)})}/></Field>
   <Field label="参与风格"><select aria-label="参与风格" value={b.groupParticipationStyle||'active'} onChange={e=>update({groupParticipationStyle:e.target.value as 'reserved'|'natural'|'active'})}><option value="reserved">保守：有明确补充再参与</option><option value="natural">自然：兴趣、观点和气氛综合判断</option><option value="active">活跃：更愿意分享相关反应</option></select></Field></div>
  <label className="toggle-row"><div><b>允许旁观补充别人之间的对话</b><small>默认关闭；开启后仍由模型判断相关性，不能替别人回答或冒充群成员。</small></div><input aria-label="允许旁观补充别人之间的对话" type="checkbox" checked={!!b.groupParticipationOtherConversations} onChange={e=>update({groupParticipationOtherConversations:e.target.checked})}/></label>
  <details className="advanced"><summary>参与判断的成本与范围</summary><div className="form-grid"><Field label="判断参考消息数"><input aria-label="判断参考消息数" type="number" min={3} max={30} value={b.groupDecisionContextMessages??12} onChange={e=>update({groupDecisionContextMessages:Number(e.target.value)})}/></Field><Field label="群判断超时（秒）" hint="超时保持沉默，保留配置的判断间隔；不影响正常被叫回复。"><input aria-label="群判断超时（秒）" type="number" min={1} max={15} value={b.groupDecisionTimeoutSeconds??10} onChange={e=>update({groupDecisionTimeoutSeconds:Number(e.target.value)})}/></Field></div><p>每群独立计时，A群不会推迟B群。各群自主入场与定时联系共享每机器人每小时自主发言上限；判断后沉默不扣发言名额，正在跟聊不扣新插话名额。</p></details>
  <p role="status">最近群判断：{metrics?.lastGroupDecision?`${new Date(metrics.lastGroupDecision.at*1000).toLocaleString()} · 群 ${metrics.lastGroupDecision.group} · ${metrics.lastGroupDecision.reply?'愿意参与':'保持安静或请求未完成'}`:'暂无记录；等待白名单内新消息及间隔到期'}</p>
 </section>;
}
