import {useEffect,useRef,useState} from 'react';
import {api} from './api';
import {Field} from './ui';
import {CapabilityLayout} from './capability-layout';
import {BilibiliConfigSchema,biliScopes,type BilibiliConfig} from '../../../packages/contracts/bilibili';
const scopeLabels:Record<string,string>={'public.read':'公开视频、搜索与字幕','private.read':'账号推荐、收藏、历史与未读数量','media.analyze':'采样帧分析（无音频）','like.write':'点赞和取消点赞','coin.write':'投币（消耗硬币）','favorite.write':'添加和取消收藏','watch_later.write':'添加和移出稍后再看','folder.manage':'创建、修改和删除收藏夹（含收藏条目）','comment.write':'评论、楼中楼及删除自己的评论','follow.write':'关注和取消关注','dynamic.write':'发布文字动态、删除自己的动态','dm.read':'读取私信（不标记已读）','dm.write':'向指定对象发私信','notification.ack':'读取可能标记已读的通知明细','profile.write':'更新B站签名'};
const states:Record<string,string>={created:'正在申请二维码',waiting_scan:'等待扫码',waiting_confirm:'已扫码，请在手机确认',ready:'已验证登录身份',expired:'二维码已过期',canceled:'已取消',failed:'登录失败，请重试',risk_control:'平台风控，请在B站处理后重试',identity_mismatch:'账号不一致，请先解除旧绑定',queued:'排队中',claimed:'执行中',dispatching:'已开始平台请求',unknown:'结果待确认，禁止重发',finished:'已结束',idle:'正常',requested:'等待检查',checking:'正在检查',refreshing:'正在轮换凭证',confirm_pending:'新凭证已保存，等待确认旧凭证',refresh_unknown:'刷新结果不明，请重新扫码',auth_required:'需要重新扫码'};
export function BilibiliControls({id,onError,onDirtyChange}:{id:string;onError:(s:string)=>void;onDirtyChange:(v:boolean)=>void}){
 const [view,setView]=useState<any>(),[draft,setDraft]=useState<BilibiliConfig>(BilibiliConfigSchema.parse({})),[dirty,setDirty]=useState(false),[busy,setBusy]=useState(false),[modelKey,setModelKey]=useState(''),[message,setMessage]=useState(''),[loadError,setLoadError]=useState('');
 const [section,setSection]=useState('overview');
 const owner=useRef<{signal:AbortSignal;seq:number}|undefined>(undefined),dirtyRef=useRef(false),writing=useRef(false);
 useEffect(()=>{onDirtyChange(dirty);return()=>onDirtyChange(false)},[dirty,onDirtyChange]);
 useEffect(()=>{
  const ctrl=new AbortController(),lease={signal:ctrl.signal,seq:0};owner.current=lease;let polling=false;setView(undefined);setBusy(false);setModelKey('');setMessage('');setLoadError('');dirtyRef.current=false;setDirty(false);writing.current=false;
  const load=async()=>{if(polling||writing.current)return;polling=true;const seq=++lease.seq;try{const next=await api('/bots/'+id+'/bilibili',undefined,undefined,lease.signal);if(owner.current===lease&&!lease.signal.aborted&&seq===lease.seq){setView(next);setLoadError('');if(!dirtyRef.current)setDraft(BilibiliConfigSchema.parse(next.config))}}catch(e){if(owner.current===lease&&!lease.signal.aborted&&seq===lease.seq)setLoadError((e as Error).message)}finally{polling=false}};
  void load();const timer=setInterval(()=>void load(),5000);return()=>{ctrl.abort();clearInterval(timer);if(owner.current===lease)owner.current=undefined};
 },[id]);
 const change=(value:BilibiliConfig)=>{setDraft(value);dirtyRef.current=true;setDirty(true)};
 async function mutate(kind:string,job?:string){const lease=owner.current;if(!lease||lease.signal.aborted||writing.current||!view)return;writing.current=true;lease.seq++;setBusy(true);setMessage('');
  try{
   const base='/bots/'+id+'/bilibili';
   if(kind==='save')await api(base,{expectedRevision:view.revision,config:BilibiliConfigSchema.parse(draft),...(modelKey?{modelKey}:{})},'PUT',lease.signal);
   else if(kind==='login')await api(base+'/login',{},'POST',lease.signal);
   else if(kind==='refresh')await api(base+'/refresh',{},'POST',lease.signal);
   else if(kind==='unlink'){if(!confirm('解除本地B站绑定？不会退出QQ，不会删除聊天记忆；已发生的B站操作保留审计。'))return;await api(base+'/account',{expectedRevision:view.account.revision},'DELETE',lease.signal)}
   else if(kind==='cancel-login')await api(base+'/login',undefined,'DELETE',lease.signal);
   else if(kind==='cancel')await api(base+'/jobs/'+job+'/cancel',{},'POST',lease.signal);
   if(lease.signal.aborted||owner.current!==lease)return;
   const next=await api(base,undefined,undefined,lease.signal);if(lease.signal.aborted||owner.current!==lease)return;setView(next);
   if(kind==='save'){dirtyRef.current=false;setDirty(false);setModelKey('');setDraft(BilibiliConfigSchema.parse(next.config));setMessage('B站配置已保存，不会重启QQ。运行工具入口将在Agent下次同步后更新。')}
   else setMessage(kind==='unlink'?'本地绑定已解除；未宣称远端会话已注销。':'操作已提交，以实际状态和回执为准。');
  }catch(e){if(!lease.signal.aborted&&owner.current===lease)onError((e as Error).message)}finally{if(!lease.signal.aborted&&owner.current===lease){writing.current=false;setBusy(false)}}
 }
 if(!view)return <section className="panel"><div className="section-heading"><span className="eyebrow">BILIBILI</span><h2>B站能力</h2><p>{loadError||'正在加载B站能力…'}</p></div></section>;
 const accountReady=view.account?.state==='ready'&&['idle','confirm_pending'].includes(view.account?.refresh_state??'idle');
 const accessReady=accountReady||!view.account&&draft.allowAnonymous;
 const toggleEnabled=(enabled:boolean)=>{
  if(enabled&&!accountReady){
   if(view.account){setMessage('B站账号尚未就绪，请重新扫码或等待凭证刷新；不会自动切换匿名状态。');setSection('account');return}
   if(!confirm('尚未登录B站账号，将启用匿名读取。匿名请求可能被B站拒绝、风控或返回不完整信息；评论、点赞等账号操作仍不可用。建议先扫码登录。仍要开启吗？'))return;
  }
  change({...draft,enabled,allowAnonymous:!view.account?enabled:draft.allowAnonymous});
 };
 return <CapabilityLayout label="B站能力" active={section} onSelect={setSection} items={[
  {id:'overview',label:'运行概览',group:'B站能力'},{id:'account',label:'账号与登录',group:'B站能力'},
  {id:'permissions',label:'工具与授权',group:'设置'},{id:'model',label:'理解模型与资源',group:'设置'},
  {id:'activity',label:'执行记录',group:'运行记录'},
 ]}>
  {loadError&&<p role="alert" className="notice">{loadError}；当前显示上次状态。</p>}
  {message&&<p role="status" className="notice">{message}</p>}
  <div className="save-bar"><div><b>B站配置 · {dirty?'有未保存的修改':'已保存 v'+view.revision}</b><small>主动与被动模式共用，保存不会重启QQ。</small></div><div className="button-row"><button type="button" className="secondary" disabled={!dirty||busy} onClick={()=>{setDraft(BilibiliConfigSchema.parse(view.config));setModelKey('');dirtyRef.current=false;setDirty(false)}}>撤销编辑</button><button type="button" className="primary" disabled={!dirty||busy} onClick={()=>void mutate('save')}>{busy?'正在提交…':'保存B站配置'}</button></div></div>
  <section className="panel" hidden={section!=='overview'}>
   <div className="section-heading"><span className="eyebrow">BILIBILI / OVERVIEW</span><h2>B站能力</h2><p>发现视频、查看合集，按需理解内容并执行已授权的互动。与QQ聊天共用内层工具和真实回执。</p></div>
   <div className="memory-cards"><div className="memory-card"><span>账号</span><strong>{view.account?.label||'未绑定'}</strong><small>{view.account?'UID '+view.account.uid:'未登录，匿名访问默认关闭'}</small></div><div className="memory-card"><span>工具状态</span><strong>{view.config.enabled&&(accountReady||!view.account&&view.config.allowAnonymous)?'已启用':view.config.enabled?'需要登录或开启匿名':'未启用'}</strong><small>以已保存配置和实际工具回执为准</small></div><div className="memory-card"><span>视频理解</span><strong>{view.mediaReady?'媒体服务就绪':'暂不可用'}</strong><small>采样帧与字幕，不包含声音理解</small></div></div>
   <label className="toggle-row"><div><b>启用B站工具</b><small>已登录时使用本账号；未登录默认关闭，手动开启将提示匿名请求可能失败。</small></div><input aria-label="启用B站工具" type="checkbox" checked={draft.enabled&&accessReady} disabled={busy} onChange={e=>toggleEnabled(e.target.checked)}/></label>
   {view.cooldown&&<p className="notice">B站正在风控冷却，至 {new Date(view.cooldown.until_at).toLocaleString()}；不会绕过或反复重试。</p>}
   <div className="button-row"><button type="button" className="secondary" onClick={()=>setSection('permissions')}>配置工具与授权</button><button type="button" className="secondary" onClick={()=>setSection('activity')}>查看执行记录</button></div>
  </section>
  <section className="panel" hidden={section!=='account'}><div className="section-heading"><span className="eyebrow">BILIBILI / ACCOUNT</span><h2>绑定账号</h2><p>每个QQ机器人最多保存一个B站账号；登录状态与工具权限分别管理。</p></div>{view.account?<p><strong>{view.account.label}</strong> · UID {view.account.uid}<br/>登录：{states[view.account.state]||view.account.state}；凭证：{states[view.account.refresh_state]||view.account.refresh_state}</p>:<p>尚未绑定，B站访问默认关闭。推荐先扫码；也可在运行概览手动开启匿名读取并确认失败风险。账号互动必须登录并逐项授权。</p>}
   <div className="actions"><button type="button" disabled={busy||dirty} onClick={()=>void mutate('login')}>{view.account?'重新扫码验证原账号':'扫码绑定B站账号'}</button>{view.account&&<><button type="button" className="secondary" disabled={busy||dirty} onClick={()=>void mutate('refresh')}>检查与刷新凭证</button><button type="button" className="secondary" disabled={busy||dirty} onClick={()=>void mutate('unlink')}>解除绑定</button></>}</div>
   {view.login&&<div role="status"><p>{states[view.login.state]||view.login.state}</p>{['waiting_scan','waiting_confirm'].includes(view.login.state)&&<><img width="260" height="260" alt="使用B站App扫码登录" src={'/v1/bots/'+id+'/bilibili/login/'+view.login.id+'/qr'}/><p>扫码后请在手机确认；随后服务端核对真实UID。</p><button type="button" className="secondary" disabled={busy} onClick={()=>void mutate('cancel-login')}>取消扫码</button></>}</div>}
  </section>
  <form onSubmit={e=>{e.preventDefault();void mutate('save')}}><fieldset disabled={busy}><section className="panel" hidden={section!=='permissions'}><div className="section-heading"><span className="eyebrow">BILIBILI / TOOLS</span><h2>工具与授权</h2><p>能力逐项授权，外层从内层读取实际可用的工具列表。</p></div><p>默认只允许公开读取。私有读取和写操作在普通聊天中仅向已配置的真实主人开放；自主任务还需打开下方授权。B站使用网页接口，平台限制时会暂停。</p>
   {biliScopes.map(scope=><label className="toggle-row" key={scope}><span>{scopeLabels[scope]}</span><input type="checkbox" aria-label={scopeLabels[scope]} checked={draft.scopes.includes(scope)} onChange={e=>change({...draft,scopes:e.target.checked?[...draft.scopes,scope]:draft.scopes.filter(v=>v!==scope)})}/></label>)}
   <label className="toggle-row"><div><b>查看合集与系列</b><small>需要“公开视频、搜索与字幕”权限。允许查UP主的合集、系列和分页视频目录；关闭后两种模式都不能调用合集工具。默认开启，不等于自动观看全部视频。</small></div><input aria-label="查看合集与系列" type="checkbox" checked={draft.collectionsEnabled} onChange={e=>change({...draft,collectionsEnabled:e.target.checked})}/></label>
   <label className="toggle-row"><div><b>允许外层自主发起已授权B站任务</b><small>不增加额外权限；不改变主动模式开关。</small></div><input aria-label="允许自主B站任务" type="checkbox" checked={draft.allowAutonomous} onChange={e=>change({...draft,allowAutonomous:e.target.checked})}/></label>
   <Field label="允许私信的B站UID" hint="逗号或空格分隔；不接受昵称猜测收件人。"><input value={draft.dmRecipients.join(',')} onChange={e=>change({...draft,dmRecipients:e.target.value.split(/[,，\s]+/).filter(Boolean)})}/></Field>
   <Field label="允许写入的视频（可选）" hint="填BV号，逗号分隔。限制点赞、投币、收藏、评论及删评；留空时由已勾选权限决定范围。"><input value={draft.videoWriteAllowlist.join(',')} onChange={e=>change({...draft,videoWriteAllowlist:e.target.value.split(/[,，\s]+/).filter(Boolean)})}/></Field>
  </section><section className="panel" hidden={section!=='model'}><div className="section-heading"><span className="eyebrow">BILIBILI / MODEL & RESOURCES</span><h2>理解模型与资源</h2><p>字幕读取无需视觉模型；画面分析按指定片段调用图片模型。</p></div><p className="notice">{view.mediaReason}。字幕与帧分析分别记录覆盖范围；当前模型没有声音理解能力。</p>
   <div className="form-grid"><Field label="图片理解模型"><select value={draft.model.provider} onChange={e=>change({...draft,model:{...draft.model,provider:e.target.value as 'none'|'bot'|'openai'|'gemini'}})}><option value="none">暂不配置</option><option value="bot">使用当前机器人的图片模型</option><option value="openai">独立图片模型（OpenAI兼容接口）</option></select></Field>
   <Field label="独立模型服务地址" hint="仅独立模型使用；填写支持图片输入的API地址。"><input disabled={draft.model.provider!=='openai'} maxLength={2000} placeholder="https://example.com/v1" value={draft.model.baseUrl} onChange={e=>change({...draft,model:{...draft.model,baseUrl:e.target.value}})}/></Field>
   <Field label="独立图片模型ID" hint="机器人模型选项直接跟随其对话配置；两种方式均只上传采样帧。"><input disabled={draft.model.provider!=='openai'} maxLength={120} value={draft.model.model} onChange={e=>change({...draft,model:{...draft.model,model:e.target.value}})}/></Field>
   <Field label="独立图片模型Key" hint={view.hasModelKey?'已有Key加密保存；留空保留。':'使用机器人模型时复用其Key；独立模型的Key按租户和机器人加密保存。'}><input disabled={draft.model.provider!=='openai'} autoComplete="new-password" type="password" value={modelKey} onChange={e=>{setModelKey(e.target.value);dirtyRef.current=true;setDirty(true)}}/></Field></div>
   <div className="form-grid">{([['maxDownloadMiB','单任务下载上限 MiB',8,256],['maxClipSeconds','单片段上限 秒',5,120],['cacheMiB','磁盘缓存上限 MiB',64,1024],['concurrency','媒体并发上限',1,2]] as const).map(([k,label,min,max])=><Field key={k} label={label}><input type="number" disabled={k==='concurrency'} min={min} max={max} value={k==='concurrency'?1:draft.resources[k]} onChange={e=>change({...draft,resources:{...draft.resources,[k]:Number(e.target.value)}})}/></Field>)}</div><p className="muted">当前全机共享一个媒体槽；并发扩容待Linux容量实测后开放。媒体文件任务后立即清理，缓存额度同时限制在途文件。媒体任务受资源准入控制，不会改动48MiB阈值、40秒PAGEOUT或Guard。</p>
  </section></fieldset></form>
  <section className="panel" hidden={section!=='activity'}><div className="section-heading"><span className="eyebrow">BILIBILI / ACTIVITY</span><h2>最近执行与真实回执</h2><p>查看真实执行结果、证据覆盖和用量。</p></div><p>“已结束”不等于平台操作成功；结果不明时禁止重复发送。仅显示本机器人最近20项。</p>{!view.jobs.length?<p className="muted">暂无B站工具执行。</p>:view.jobs.map((j:any)=><article className="command" key={j.id}><b>{j.operation}</b><span>{states[j.state]||j.state} · {j.result?.receipt?.status||'尚无回执'}</span><p>{j.result?.error?.userMessage||j.result?.receipt?.meaning||''}</p>{j.result?.receipt?.platformObjectId&&<p>平台对象ID：{j.result.receipt.platformObjectId}</p>}{['queued','claimed','dispatching'].includes(j.state)&&<button type="button" className="secondary" disabled={busy||j.cancel_requested} onClick={()=>void mutate('cancel',j.id)}>{j.cancel_requested?'已申请取消':'取消任务'}</button>}<details><summary>证据与用量</summary><pre style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{JSON.stringify({result:j.result,updatedAt:j.updated_at},null,2)}</pre></details></article>)}</section>
 </CapabilityLayout>;
}
