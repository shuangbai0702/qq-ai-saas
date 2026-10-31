import type {Health} from '../../../packages/contracts/index.js';

export interface OfflineObservation {since:number;lastObservedAt:number;observations:number;reason:string;graceMs:number;evictedAt?:number;windows?:Record<string,number>}
export const offlineGrace = (value:string|undefined, fallback:number) => Math.max(30,Math.min(fallback,Number(value)||fallback))*1000;

export function observeOffline(previous:OfflineObservation|undefined, protocol:Health, now:number,
 grace:{offline:number;fault:number;login:number;startup?:number},
 lifecycle:{ai?:Health;onlineSince?:number;requestedAt?:number}={}):OfflineObservation|undefined {
 if(protocol.online&&(!lifecycle.ai||lifecycle.ai.ready))return;
 const reason=lifecycle.ai&&!lifecycle.ai.ready?(lifecycle.onlineSince?'protocol-unavailable':'runtime-starting'):
  !protocol.alive||!protocol.ready||['created','starting','authenticating'].includes(protocol.loginState||'')?
   lifecycle.onlineSince?'protocol-unavailable':'runtime-starting':
  ['qr_ready','waiting_scan','waiting_confirm','verification_required'].includes(protocol.loginState||'')?'login-incomplete':'account-offline';
 // Fresh observations are required after a gap, but automatic retries and
 // service restarts do not erase an already observed phase's deadline.
 const continuous=previous&&previous.reason===reason&&Number.isFinite(previous.since)&&previous.since<=now&&
  Number.isFinite(previous.lastObservedAt)&&previous.lastObservedAt<=now&&now-previous.lastObservedAt<=60000;
 const valid=previous&&Number.isFinite(previous.since)&&previous.since<=now&&previous.lastObservedAt<=now;
 const windows:Record<string,number>={};if(valid){for(const [phase,at] of Object.entries(previous.windows||{}))if(Number.isFinite(at)&&at<=now)windows[phase]=at;windows[previous.reason]??=previous.since;}
 const requested=lifecycle.requestedAt;windows[reason]??=reason==='runtime-starting'&&requested!==undefined&&Number.isFinite(requested)&&requested<=now?requested:now;
 return {since:windows[reason],windows,lastObservedAt:now,
  observations:continuous?Math.min(1000000,previous.observations+1):1,reason,
  graceMs:reason==='runtime-starting'?Math.min(grace.startup??120000,120000):reason==='protocol-unavailable'?Math.min(grace.fault,300000):reason==='login-incomplete'?Math.min(grace.login,180000):Math.min(grace.offline,300000)};
}

export function evictionDue(value:OfflineObservation|undefined,now:number){
 return !!value&&value.observations>=3&&now-value.since>=value.graceMs;
}
