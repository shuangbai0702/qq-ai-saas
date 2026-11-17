import {useState} from 'react';
import {api} from './api';
import {useLifetime,useLiveResource} from './hooks';

type Balance={enabled:boolean;available:number;granted:number;spent:number;reserved:number};
type ReferralDetails=Balance&{code:string|null;invited:number;inviterTokens:number;inviteeTokens:number};

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
 const [note,setNote]=useState(''),[busy,setBusy]=useState(false),[activation,setActivation]=useState('');
 const owner=useLifetime();
 const inviteCode=activation.trim();
 const link=data?.code&&inviteCode?`${location.origin}${location.pathname}#/invite/${data.code}?${new URLSearchParams({activation:inviteCode})}`:'';
 async function generate(){
  const page=owner.current;
  if(!page||page.signal.aborted||busy)return;
  setBusy(true);setNote('');
  try{await api('/referrals',{},'POST',page.signal);if(!page.signal.aborted)await refresh()}
  catch(error){if(!page.signal.aborted)setNote((error as Error).message)}
  finally{if(!page.signal.aborted)setBusy(false)}
 }
 async function copy(){
  const page=owner.current;
  try{await navigator.clipboard.writeText(link);if(!page?.signal.aborted)setNote('邀请链接已复制')}
  catch{if(!page?.signal.aborted)setNote('可以选中下方链接手动复制。')}
 }
 return <section className="panel"><h1>邀请奖励</h1>
  <p>好友通过你的邀请链接完成注册后，你获赠 <strong>3,000 万 Token</strong>，好友获赠 <strong>2,000 万 Token</strong>。</p>
  <p>额度由工作区内的机器人共享。在模型设置里选择「平台提供」即可使用。</p>
  {error&&<p className="alert" role="alert">{error}</p>}
  {!data&&!error&&<p role="status">正在读取额度…</p>}
  {data&&<><dl><dt>剩余可用</dt><dd><strong>{tokenCount(data.available)} Token</strong></dd><dt>已成功邀请</dt><dd>{data.invited} 人</dd></dl>
  {!data.enabled&&<p className="notice">邀请活动暂不可用，请稍后再来。</p>}
  {data.enabled&&<>
   <label className="field"><span>要随链接分享的激活码</span><input value={activation} onChange={e=>{setActivation(e.target.value);setNote('')}} maxLength={100} autoComplete="off" placeholder="填入管理员提供的可用激活码"/></label>
   <p className="muted">好友打开链接时会自动带入激活码。请把链接发给你要邀请的人。</p>
   {data.code?link&&<><label className="field"><span>我的邀请链接</span><input value={link} readOnly onFocus={e=>e.currentTarget.select()}/></label><button type="button" className="primary" onClick={()=>void copy()}>复制邀请链接</button></>:<button type="button" className="primary" disabled={busy||!inviteCode} onClick={()=>void generate()}>{busy?'正在生成…':'生成邀请链接'}</button>}
  </>}
  <p><a href="#/bots">前往我的机器人</a></p></>}
  {note&&<p role="status">{note}</p>}
 </section>;
}
