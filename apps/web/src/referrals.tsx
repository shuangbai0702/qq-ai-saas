import {useState} from 'react';
import {api} from './api';
import {useLifetime,useLiveResource} from './hooks';

export type Balance={enabled:boolean;available:number;granted:number;spent:number;reserved:number;expired?:number;campaign?:{id:string|null;claimedAt:string|null;expiresAt:string|null;granted:number;spent:number;reserved:number;available:number}};
type ReferralDetails=Balance&{canInvite:boolean;remainingUses:number;invited:number;inviterTokens:number;inviteeTokens:number};

export function usePlatformBalance(){return useLiveResource<Balance>('/platform-model/balance',15000)}
export const tokenCount=(n:number)=>n.toLocaleString('zh-CN');
export function referralFromLocation(){
 const [route,query]=location.hash.split('?',2);
 const referralCode=route.match(/^#\/invite\/([A-Za-z0-9_-]{24})$/)?.[1];
 if(!referralCode)return;
 const inviteCode=new URLSearchParams(query).get('activation')?.trim();
 return {referralCode,inviteCode:inviteCode&&inviteCode.length<=100?inviteCode:undefined};
}

export function Referrals(){
 const {data,error,refresh}=useLiveResource<ReferralDetails>('/referrals',10000);
 const [note,setNote]=useState(''),[busy,setBusy]=useState(false),[link,setLink]=useState(''),[activation,setActivation]=useState('');
 const owner=useLifetime();
 async function generate(){
  const page=owner.current;
  if(!page||page.signal.aborted||busy)return;
  setBusy(true);setNote('');
  try{
   const inviteCode=activation.trim();
   const result:{code:string;activation:string}=await api('/referrals',inviteCode?{inviteCode}:{},'POST',page.signal);
   if(!page.signal.aborted){
    setLink(`${location.origin}${location.pathname}#/invite/${result.code}?${new URLSearchParams({activation:result.activation})}`);
    await refresh();
   }
  }
  catch(error){if(!page.signal.aborted)setNote((error as Error).message)}
  finally{if(!page.signal.aborted)setBusy(false)}
 }
 async function copy(){
  const page=owner.current;
  try{
   await navigator.clipboard.writeText(link);
   if(!page?.signal.aborted){
    setNote('邀请链接已复制');
    const code=link.match(/#\/invite\/([A-Za-z0-9_-]{24})\?/)?.[1];
    if(code)void api('/referrals/copied',{code},'POST',page?.signal).catch(()=>{});
   }
  }
  catch{if(!page?.signal.aborted)setNote('复制失败，请手动选中邀请链接复制。')}
 }
 return <section className="panel"><h1>邀请奖励</h1>
  <p>好友通过你的邀请链接完成注册后，你获赠 <strong>3,000 万 Token</strong>，好友获赠 <strong>2,000 万 Token</strong>。</p>
  <p>额度由工作区内的机器人共享。在模型设置里选择「平台提供」即可使用。</p>
  {error&&<p className="alert" role="alert">{error}</p>}
  {!data&&!error&&<p role="status">正在读取额度…</p>}
  {data&&<><dl><dt>剩余可用</dt><dd><strong>{tokenCount(data.available)} Token</strong></dd><dt>已成功邀请</dt><dd>{data.invited} 人</dd></dl>
  {!data.enabled&&<p className="notice">邀请活动暂不可用，请稍后再来。</p>}
  {data.enabled&&!data.canInvite&&<p className="notice">仅使用管理员直接发放的激活码注册的账号可邀请好友。你仍可使用已有的平台模型额度。</p>}
  {data.enabled&&data.canInvite&&<>
   <p className="muted">当前激活码剩余 {data.remainingUses} 次注册机会（同一激活码共享）。生成、复制链接或注册失败均不消耗次数；只有好友成功注册才扣一次。次数用完、过期或撤销后，链接失效。</p>
   {link&&<><label className="field"><span>我的邀请链接</span><input value={link} readOnly onFocus={e=>e.currentTarget.select()}/></label><button type="button" className="primary" onClick={()=>void copy()}>复制邀请链接</button></>}
   <details><summary>使用其他管理员激活码</summary><label className="field"><span>其他激活码（可选）</span><input value={activation} disabled={busy} onChange={e=>{setActivation(e.target.value);setLink('');setNote('')}} maxLength={100} autoComplete="off" placeholder="留空使用账号现有邀请次数"/></label></details>
   <button type="button" className={link?'secondary':'primary'} disabled={busy||data.remainingUses<=0&&!activation.trim()} onClick={()=>void generate()}>{busy?'正在生成…':link?'重新生成邀请链接':'生成邀请链接'}</button>
   {data.remainingUses<=0&&<p className="notice">当前没有可用邀请次数，请联系管理员。</p>}
  </>}
  <p><a href="#/bots">前往我的机器人</a></p></>}
  {note&&<p role="status">{note}</p>}
 </section>;
}
