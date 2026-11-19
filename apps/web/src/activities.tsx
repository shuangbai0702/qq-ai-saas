import {useEffect,useId,useRef,useState} from 'react';
import {createPortal} from 'react-dom';
import {api} from './api';
import {useLifetime} from './hooks';
import {tokenCount,type Balance} from './referrals';
import {platformDisclaimer} from '../../../packages/shared/promotion';

export type Activity={id:string;title:string;tokens:number;startsAt:string;endsAt:string;validDays:number;serverTime:string;claimOpen:boolean;enabled:boolean;balance:Balance};
const date=(value:string)=>new Date(value).toLocaleString('zh-CN',{hour12:false});
function Rules({data}:{data:Activity}){return <><p>每个工作区手动领取一次 <strong>{tokenCount(data.tokens)} Token</strong>，工作区内的机器人共用。</p><p>领取截止：<strong>{date(data.endsAt)}</strong>。从领取时起 {data.validDays} 天内可用，优先消耗活动额度；到期未使用部分失效，已有邀请奖励不受影响。</p><p className="muted">Token 包含输入与输出，实际消耗以模型用量回执为准。领取不会更改你的模型配置，可在模型设置中选择「平台提供」或随时切回自备模型。</p><p className="platform-disclaimer">{platformDisclaimer}</p></>}
function useClaim(refresh:()=>Promise<unknown>,onClaimed?:()=>void){
 const [busy,setBusy]=useState(false),[error,setError]=useState('');const owner=useLifetime(),lock=useRef(false);
 async function claim(){const page=owner.current;if(!page||page.signal.aborted||lock.current)return;lock.current=true;setBusy(true);setError('');try{await api('/activities/claim',{},'POST',page.signal);if(!page.signal.aborted){onClaimed?.();await refresh()}}catch(e){if(!page.signal.aborted)setError((e as Error).message)}finally{lock.current=false;if(!page.signal.aborted)setBusy(false)}}
 return {busy,error,claim};
}
export function Activities({data,error,refresh}:{data?:Activity;error:string;refresh:()=>Promise<unknown>}){
 const action=useClaim(refresh);const campaign=data?.balance.campaign,claimed=!!campaign?.claimedAt;
 return <section className="panel activity-page"><small>活动专区 · 限时赠送</small><h1>{data?.title??'500 万 Token 体验活动'}</h1>{error&&<p role="alert" className="alert">{error}</p>}{data?<><div className="activity-summary"><div><small>工作区可用额度</small><strong>{tokenCount(data.balance.available)} <small>Token</small></strong></div><div><small>其中活动可用</small><strong>{tokenCount(campaign?.available??0)} <small>Token</small></strong></div></div><Rules data={data}/>{claimed?<p role="status">已领取。活动额度到期时间：<strong>{date(campaign!.expiresAt!)}</strong>。{new Date(data.serverTime)>=new Date(campaign!.expiresAt!)?'活动额度已到期。':''}</p>:<p>{data.claimOpen?'领取后计时，有效期 7 天。':'当前不在领取时间内。'}</p>}{action.error&&<p className="alert" role="alert">{action.error}</p>}<div className="button-row">{!claimed&&<button className="primary" disabled={!data.claimOpen||action.busy} onClick={()=>void action.claim()}>{action.busy?'正在领取…':'手动领取 500 万 Token'}</button>}<a className="secondary" href="#/bots">去配置机器人</a><button className="secondary" onClick={()=>void refresh()}>刷新额度</button></div>{!data.enabled&&<p className="muted">平台模型暂不可用，领取后仍按上述时间到期；请自行决定是否现在领取。</p>}</>:!error&&<p role="status">正在读取活动…</p>}</section>;
}
export function ActivityPopup({data,userId,enabled,refresh}:{data?:Activity;userId:string;enabled:boolean;refresh:()=>Promise<unknown>}){
 const key=data?'fishbot:activity:'+userId+':'+data.id:'';
 const [dismissed,setDismissed]=useState(''),ref=useRef<HTMLDialogElement>(null),label=useId();
 const [claimedHere,setClaimedHere]=useState(false);
 const action=useClaim(refresh,()=>setClaimedHere(true));
 let read=false;try{read=!!key&&localStorage.getItem(key)==='read'}catch{}
 const open=enabled&&!!data&&data.claimOpen&&(!data.balance.campaign?.claimedAt||claimedHere)&&!read&&dismissed!==key;
 useEffect(()=>{if(!open)return;const dialog=ref.current,previous=document.activeElement;dialog?.showModal();return()=>{dialog?.close();if(previous instanceof HTMLElement&&previous.isConnected)previous.focus()}},[open]);
 function close(){try{localStorage.setItem(key,'read')}catch{}setDismissed(key)}
 if(!open||!data)return null;
 return createPortal(<dialog className="edit-dialog activity-dialog" ref={ref} aria-labelledby={label} onCancel={e=>{e.preventDefault();close()}}><small>限时活动 · 需手动领取</small><h2 id={label}>{data.title}</h2><Rules data={data}/>{claimedHere&&<p role="status"><strong>领取成功，500 万 Token 已到账。</strong>请在机器人模型设置中选择「平台提供」。</p>}{action.error&&<p className="alert" role="alert">{action.error}</p>}<div className="button-row">{!claimedHere&&<button className="primary" disabled={action.busy} onClick={()=>void action.claim()}>{action.busy?'正在领取…':'领取 500 万 Token'}</button>}{claimedHere&&<a className="primary" href="#/bots" onClick={close}>去配置机器人</a>}<a className="secondary" href="#/activities" onClick={close}>查看活动</a><button className="secondary" autoFocus onClick={close}>{claimedHere?'知道了':'稍后再说'}</button></div></dialog>,document.body);
}
