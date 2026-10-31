import {EditDialog} from './edit-dialog';
import {useEffect,useRef,useState} from 'react';
import type {BotConfig} from '../../../packages/contracts/index';
import {applyFishSocialPreset} from '../../../packages/contracts/fish-social-preset';
import {api} from './api';
import {useLifetime} from './hooks';

export function CapabilityManager({id,config,onChange,revision,appliedRevision}:{id:string;config:BotConfig;onChange:(c:BotConfig)=>void;revision:number;appliedRevision:number}){
 const b:Partial<NonNullable<BotConfig['behavior']>>=config.behavior||{};const update=(key:string,value:boolean)=>onChange({...config,behavior:{...b,[key]:value} as NonNullable<BotConfig['behavior']>});
 const capabilities=[
  {key:'typingSimulation',name:'自然打字停顿',value:!!b.typingSimulation,description:'按字数随机等待，默认15字/秒、最长3秒。',link:'chat'},
  {key:'memorySummary',name:'定期对话摘要',value:!!b.memorySummary,description:'每10轮使用本Bot模型整理，持久保存、关键词召回；额外消耗Token。',link:'chat'},
  {key:'pokeReply',name:'被戳后偶尔回戳',value:!!b.pokeReply,description:'默认25%，每会话60秒冷却。',link:'chat'},
  {key:'qqProfile',name:'主人管理QQ资料',value:!!b.qqProfile,description:'主人明确要求时修改，不自动改变。需设置主人QQ号。',link:'plugins'},
  {key:'qzone',name:'主人要求时使用QQ空间',value:!!b.qzone,description:'仅发文字或查看自己的空间，不自动发布。需设置主人QQ号。',link:'plugins'},
  {key:'enabled',name:'机器人聊天总开关',value:b.enabled!==false,description:'关闭后暂停回复、记忆采集、表达学习、配图和主动联系；QQ登录保持独立。重新开启不会丢失下方设置。',link:'chat'},
  {key:'memory',name:'长期事实记忆',value:b.memory!==false,description:'按机器人、会话和人物隔离。下方可查阅、纠正及遗忘。',link:'chat'},
  {key:'memoryAutoCapture',name:'自动记住明确自述',value:!!b.memoryAutoCapture,description:'只识别明确自述，不覆盖手动记忆；需要开启长期事实记忆。',link:'chat'},
  {key:'styleLearning',name:'群体表达学习',value:!!b.styleLearning,description:'至少12条、两位群友佐证；每 Bot 最多4次学习请求/24小时。下方可停用和清理。',link:'chat'},
  {key:'socialAwareness',name:'按语境接话与沉默',value:b.socialAwareness??!!(b.styleLearning||b.memeEnabled),description:'只在消息已被@、跟进或插话规则唤醒后，判断是否适合回应。打开它不会让机器人主动接收所有群消息，也不会增加一轮判断模型调用。',link:'chat'},
  {key:'qqFaces',name:'QQ 内置表情',value:b.qqFaces===true,description:'默认关闭QQ自带小表情，不影响平台表情包与QQ收藏表情。',link:'faces'},
  {key:'memeEnabled',name:'聊天表情包',value:!!b.memeEnabled,description:'从已启用图包、手动表情和原群已审核表情选择；允许不发，与普通图库独立。',link:'images'},
  {key:'stickerCollect',name:'收集群聊表情包',value:!!b.stickerCollect,description:'从授权群接收协议标记的表情，先审核再使用；不会读取手机QQ收藏。',link:'images'},
  {key:'galleryEnabled',name:'普通图库发图',value:!!b.galleryEnabled,description:'独立图库，只在用户明确请求看图时参与选图；不混入自动表情。',link:'gallery'},
 ];
 return <><section className="panel"><div className="section-heading"><h2>能力管理</h2><p>开关修改需点击“保存并应用”。数据操作立即生效。保存 v{revision} / 生效 {appliedRevision?'v'+appliedRevision:'尚未生效'}。</p></div>
 <div className="notice"><b>大肥鱼社交推荐</b><p>一次调整接话、表达、摘要及表情收集等多项设置，适合希望更多群聊互动的用户。会增加回复与模型请求机会；保留模型、Key、主人和主动联系对象。可先应用到草稿，检查后再保存。</p><button type="button" className="secondary" onClick={()=>onChange(applyFishSocialPreset(config))}>应用大肥鱼社交推荐</button><small>应用到表单后，请保存并应用。不会自动开启普通图库、语音或未实现的工具。</small></div>
 {capabilities.map(c=>{const blocked=c.key!=='enabled'&&(b.enabled===false?'聊天总开关已关闭':['memoryAutoCapture','memorySummary'].includes(c.key)&&b.memory===false?'长期事实记忆已关闭':'');return <div key={c.key} className="capability-row"><label className="toggle-row"><div><b>{c.name}</b><small>{c.description}</small>{blocked&&<small className="dependency-note">暂不生效：{blocked}。已保存的偏好会保留。</small>}</div><input type="checkbox" aria-label={c.name} checked={c.value} disabled={!!blocked} onChange={e=>update(c.key,e.target.checked)}/></label><a href={`#/bots/${id}/config/${c.link}`}>详细设置</a></div>})}
 <div className="capability-row"><label className="toggle-row"><div><b>主动联系</b><small>只面向配置授权且已有会话的对象；对方拒绝、未回应或安静时段会抑制发送。</small>{config.proactive&&(!b.proactiveTargets?.length||b.dailyProactiveLimit===0||b.proactiveChance===0)&&<small className="dependency-note">尚不能主动联系：请检查授权目标、每日额度和主动概率。</small>}</div><input type="checkbox" aria-label="主动联系" disabled={b.enabled===false} checked={config.proactive} onChange={e=>onChange({...config,proactive:e.target.checked})}/></label><a href={`#/bots/${id}/config/proactive`}>目标与预算</a></div>
 <div className="notice">依赖：学习、语境接话、配图和主动联系需要有效的模型配置。机器人聊天总开关关闭时，上述行为扩展不会执行。自动收图按允许群范围执行，需人工审核后才用于回复；视觉识别、完整个人风格模仿尚未接入。</div>
 </section><BehaviorData key={id} id={id}/></>;
}

function BehaviorData({id}:{id:string}){
 const [data,setData]=useState<any>(),[scopeType,setScopeType]=useState(''),[scopeNumber,setScopeNumber]=useState(''),[subject,setSubject]=useState(''),[query,setQuery]=useState(''),[error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false),[loading,setLoading]=useState(false),[editing,setEditing]=useState<any>(null);
 const scope=scopeType?scopeType+':'+scopeNumber:'';
 const owner=useLifetime(),order=useRef(0),pending=useRef(false);
 async function load(){const page=owner.current;if(!page||page.signal.aborted||pending.current)return;if(scopeType&&!/^\d{4,20}$/.test(scopeNumber)){setError('请填写4–20位数字群号或QQ号');return}const seq=++order.current;setError('');setLoading(true);
  try{const params=new URLSearchParams();if(scope)params.set('scope',scope);if(subject)params.set('subject',subject);if(query)params.set('query',query);const value=await api(`/bots/${id}/behavior-data?${params}`,undefined,undefined,page.signal);if(!page.signal.aborted&&seq===order.current)setData(value)}catch(e){if(!page.signal.aborted&&seq===order.current)setError((e as Error).message)}finally{if(!page.signal.aborted&&seq===order.current)setLoading(false)}
 }
 useEffect(()=>{void load();return()=>{order.current++}},[id]);
 async function mutate(body:any){const page=owner.current;if(!page||page.signal.aborted||pending.current)return '操作仍在进行，请稍后重试';pending.current=true;order.current++;setBusy(true);setError('');setNotice('');
  try{await api(`/bots/${id}/behavior-data`,body,'POST',page.signal);if(page.signal.aborted)return;setNotice('数据操作已生效，无需重启。');pending.current=false;await load()}catch(e){if(!page.signal.aborted){setError((e as Error).message);return (e as Error).message}}finally{if(!page.signal.aborted){pending.current=false;setBusy(false)}}
 }
 return <section className="panel" aria-label="行为数据管理"><div className="section-heading"><h2>记忆、表达与主动会话</h2><p>查询仅返回当前机器人数据，每类最多100条；可按会话缩小范围。人物QQ同时筛选事实和人物状态，关键词只筛选事实。</p></div>
 <div className="form-grid"><label className="field"><span>会话类型</span><select aria-label="会话类型" value={scopeType} onChange={e=>setScopeType(e.target.value)}><option value="">全部会话</option><option value="group">群聊</option><option value="private">私聊</option></select></label>{scopeType&&<label className="field"><span>会话号码</span><input aria-label="会话号码" inputMode="numeric" maxLength={20} placeholder={scopeType==='group'?'请输入群号':'请输入QQ号'} value={scopeNumber} onChange={e=>setScopeNumber(e.target.value.replace(/\D/g,''))}/></label>}<label className="field"><span>人物 QQ</span><input aria-label="人物QQ筛选" value={subject} onChange={e=>setSubject(e.target.value)}/></label><label className="field"><span>事实关键词</span><input aria-label="事实关键词" value={query} onChange={e=>setQuery(e.target.value)}/></label></div>
 <button type="button" className="secondary" disabled={busy||loading} onClick={()=>void load()}>{loading?'正在查询…':'查询行为数据'}</button>{error&&<p role="alert" className="alert">{error}</p>}{notice&&<p role="status" className="success">{notice}</p>}
 {!data?<p>{error?'未能读取数据，请点击查询重试。':'正在读取行为数据…'}</p>:<><p>本机器人累计：采样 {data.stats.social_samples??0} 条 · 表达 {data.stats.social_patterns??0} 条 · 学习批次 {data.stats.social_learning??0} 次{!data.initialized?' · 尚未产生数据':''}</p>
 <h3>长期事实</h3>{!data.facts.length&&<p>当前条件下没有长期事实。</p>}{data.facts.map((f:any)=><article className="managed-item" key={[f.scope,f.subject,f.name].join('|')}><b>{f.name}：{f.value}</b><small>{f.scope} · QQ {f.subject} · v{f.version} · 来源消息 {f.event}</small><div className="button-row"><button type="button" disabled={busy} className="secondary" onClick={()=>setEditing(f)}>纠正</button><button type="button" disabled={busy} className="secondary" onClick={()=>{if(confirm('遗忘该事实？自动自述采集不会把它重新记回；本人仍可明确要求记住。'))void mutate({action:'forget_fact',scope:f.scope,subject:f.subject,name:f.name,expectedVersion:f.version})}}>遗忘</button></div></article>)}
 <h3>对话摘要</h3><p>整理自对应会话，不是已核实的人物事实；筛选人物时不显示多人摘要。纠正或遗忘事实会清除此会话的衍生摘要，防止旧内容重新被召回。</p>{!data.summaries?.length&&<p>尚无摘要；需要开启定期摘要并达到指定对话轮数。</p>}{data.summaries?.map((s:any)=><article className="managed-item" key={s.id}><b>{s.scope}</b><p>{s.text}</p><small>{new Date(s.updated*1000).toLocaleString()}</small><button type="button" className="secondary" disabled={busy} onClick={()=>{if(confirm('遗忘该摘要？当前会话待整理材料也会清空，旧任务不会写回。'))void mutate({action:'forget_summary',scope:s.scope,id:s.id,expectedUpdated:s.updated})}}>遗忘摘要</button></article>)}<h3>学到的表达</h3>{!data.patterns.length&&<p>尚无符合条件的群体表达；开启学习不代表已经学到内容。</p>}{data.patterns.map((p:any)=><article className="managed-item" key={p.scope+p.expression}><b>{p.expression} {p.disabled?'· 已停用':p.expired?'· 已过期':'· 可使用'}</b><p>适用：{p.situation}</p><small>{p.scope} · 证据样本 {p.sources} · 权重 {p.weight}</small><button type="button" disabled={busy} className="secondary" onClick={()=>void mutate({action:'set_pattern',scope:p.scope,expression:p.expression,expectedUpdated:p.updated,expectedEpoch:data.epoch,disabled:!p.disabled})}>{p.disabled?'恢复表达':'停用表达'}</button></article>)}
 <label className="field"><span>清理学习材料的会话</span><small>在上方选择群聊并填写群号。清空样本、表达和停用记录，旧批次不会写回。后续新聊天仍可重新学习；需要暂停时请关闭学习开关。不会删除事实记忆。</small></label><button type="button" className="secondary" disabled={busy||!/^group:\d{4,20}$/.test(scope)} onClick={()=>{if(confirm('清空群 '+scopeNumber+' 的学习材料和停用记录？以后可以从新聊天重新学习。事实记忆不会删除。'))void mutate({action:'clear_learning',scope})}}>清空该群学习材料</button>
 <h3>主动联系会话</h3>{!data.contacts.length&&<p>尚无会话记录。</p>}{data.contacts.map((c:any)=><article className="managed-item" key={c.scope}><b>{c.scope} · {c.opted_out?'已暂停或对方已拒绝':'未拒绝'}</b><small>未回应次数 {c.unanswered} · 最后尝试 {c.last_attempt?new Date(c.last_attempt*1000).toLocaleString():'暂无'}</small><button type="button" className="secondary" disabled={busy||!!c.opted_out} onClick={()=>void mutate({action:'pause_contact',scope:c.scope})}>暂停主动联系</button></article>)}<p>允许被动回复不等于授权主动联系。已拒绝的会话须由对方明确表示“可以主动找我”恢复；后台不覆盖其拒绝。</p>
 <h3>轻量关系与情绪</h3><p>真实定向互动累计亲近状态：普通交流每 5 分钟最多 +1；明确感谢或负面表达间隔至少 1 分钟，每人每会话每日最多变化 10 次。重复消息不累计，路过群消息不加分。情绪约 5 分钟后恢复平静；亲近状态持续保存，与长期记忆开关及主人权限分开。重置保留事实记忆。</p>{!data.relations.length&&<p>尚无人物状态。</p>}{data.relations.map((r:any)=><article className="managed-item" key={r.scope+r.subject}><b>QQ {r.subject} · 亲近状态 {r.affinity}</b><small>{r.scope} · 当前情绪：{r.expires*1000>Date.now()?r.emotion:'平静（情绪已自然恢复，亲近状态保留）'}</small><button type="button" className="secondary" disabled={busy} onClick={()=>{if(confirm('重置该人物在此会话的关系和情绪？事实记忆会保留。'))void mutate({action:'reset_relation',scope:r.scope,subject:r.subject,expectedLastSeen:r.last_seen})}}>重置人物状态</button></article>)}
 </>}
 {editing&&<EditDialog title={'纠正记忆：'+editing.name} fields={[{key:'value',label:'事实内容',value:editing.value,maxLength:300,required:true}]} onClose={()=>setEditing(null)} onSave={async values=>{const error=await mutate({action:'edit_fact',scope:editing.scope,subject:editing.subject,name:editing.name,expectedVersion:editing.version,value:values.value});if(error)throw new Error(error)}}/>}
 </section>;
}
