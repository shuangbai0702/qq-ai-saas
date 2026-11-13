import type {Sql} from './index.js';
import {runtimeStatus} from '../shared/runtime-status.js';

const conflict=(message:string)=>Object.assign(new Error(message),{statusCode:409});
export type ModelRequestBudget={workId:string;epoch:number;requestId:string;phase:'reserve'|'settled'|'released'|'unknown';source:'main'|'media';tokens?:number;effectId?:string;owner?:string};
export function modelRequestTotals(budget:any,source?:'main'|'media'){
 const entries=Object.values(budget?.modelRequests||{}).filter((r:any)=>r.status!=='released'&&(!source||r.source===source)) as any[];
 return{modelCalls:entries.length,tokens:entries.reduce((n,r)=>n+Number(r.tokens||0),0)};
}
// The request journal is part of the existing cycle budget, not a second ledger.
// Retain reservations after process loss: a missing receipt cannot prove no call.
export async function cycleModelBudget(db:Sql,bot:any,v:ModelRequestBudget){return db.transaction(tx=>cycleModelBudgetInTransaction(tx,bot,v))}
export async function cycleModelBudgetInTransaction(tx:Sql,bot:any,v:ModelRequestBudget){
 const params=[v.workId,bot.id??bot.bot_id,bot.tenant_id,v.epoch];
 await tx.query('SELECT c.id FROM autonomy_cycles c JOIN autonomy_actions a ON a.cycle_id=c.id AND a.bot_id=c.bot_id AND a.tenant_id=c.tenant_id WHERE a.id=$1 AND a.bot_id=$2 AND a.tenant_id=$3 AND a.epoch=$4 FOR UPDATE OF c',params);
 const [c]=await tx.query(`SELECT c.*,a.status AS action_status,a.expires_at,s.epoch AS current_epoch,s.config AS current_config,b.generation AS current_generation,b.revision AS current_revision,b.health,b.desired_state,b.deleted_at FROM autonomy_cycles c JOIN autonomy_actions a ON a.cycle_id=c.id AND a.bot_id=c.bot_id AND a.tenant_id=c.tenant_id JOIN autonomy_settings s ON s.bot_id=a.bot_id AND s.tenant_id=a.tenant_id JOIN bots b ON b.id=a.bot_id AND b.tenant_id=a.tenant_id WHERE a.id=$1 AND a.bot_id=$2 AND a.tenant_id=$3 AND a.epoch=$4`,params);
 if(!c)throw conflict('model_budget_owner_missing');
 const requests={...(c.budget.modelRequests||{})},old=requests[v.requestId];
 if(old&&(old.workId!==v.workId||old.source!==v.source||old.effectId!==(v.effectId??null)||old.owner!==(v.owner??null)))throw conflict('model_request_identity_conflict');
 if(v.tokens!==undefined&&(!Number.isSafeInteger(v.tokens)||v.tokens<0))throw conflict('model_request_tokens_invalid');
 if(v.phase==='reserve'){
  if(old)throw conflict('model_request_already_reserved'); // transport retries must never dispatch a request twice
  if((v.source==='main'&&c.finished_at)||c.current_epoch!==v.epoch||c.current_config.mode!=='active'||c.generation!==c.current_generation||c.config_revision!==c.current_revision||!runtimeStatus(c).online)throw conflict('model_budget_owner_expired');
  if(v.source==='main'&&(!['executing'].includes(c.action_status)||+new Date(c.expires_at)<=Date.now()))throw conflict('model_budget_action_expired');
  if(v.source==='media'){
   const [effect]=await tx.query("SELECT id FROM tool_effects WHERE id=$1 AND work_id=$2 AND bot_id=$3 AND tenant_id=$4 AND mode_epoch=$5 AND authority='autonomous' AND operation='analyze' AND lease_owner=$6 AND state='claimed' AND NOT cancel_requested AND lease_until>clock_timestamp() AND deadline_at>clock_timestamp() FOR UPDATE",[v.effectId,v.workId,c.bot_id,c.tenant_id,v.epoch,v.owner]);
   if(!effect)throw conflict('model_budget_effect_expired');
  }
  const used=modelRequestTotals(c.budget),limit=c.budget.cycleModelCalls;
  if(!Number.isSafeInteger(limit)||limit<0||used.modelCalls+1>limit)throw conflict('model_budget_exhausted');
  if(v.tokens===undefined)throw conflict('model_request_tokens_required');
  if(Math.max(used.tokens,c.reason==='lease_expired'?Number(c.usage?.tokens||0):0)+v.tokens>c.budget.cycleTokenBudget)throw conflict('model_token_budget_exhausted');
 }else if(!old)throw conflict('model_request_not_reserved');
 // Confirmed usage is immutable to cancellation or a delayed failure. Unknown
 // can be corrected only by the authoritative successful provider receipt.
 if(old?.status==='settled'||old?.status==='released'||old?.status==='unknown'&&v.phase!=='settled')return{status:old.status,...modelRequestTotals(c.budget)};
 const status=v.phase==='reserve'?'reserved':v.phase;
 requests[v.requestId]={...old,workId:v.workId,source:v.source,effectId:v.effectId??null,owner:v.owner??null,status,tokens:v.tokens??old?.tokens??0};
 const budget={...c.budget,modelRequests:requests};
 await tx.query('UPDATE autonomy_cycles SET budget=$2 WHERE id=$1',[c.id,JSON.stringify(budget)]);
 // While live, the whole cycle ceiling is already reserved in the daily row.
 // Finish charges every outstanding request conservatively. A later media
 // reservation must acquire fresh capacity in that same original-day row.
 if(c.finished_at){
  const before=modelRequestTotals(c.budget,v.source),after=modelRequestTotals(budget,v.source);
  // Recovery already consumed the full ceiling; its late receipt must not
  // consume or refund that conservative charge a second time.
  const tokenDelta=c.reason==='lease_expired'?0:after.tokens-before.tokens,callDelta=after.modelCalls-before.modelCalls;
  if(v.phase==='reserve'){
   const limit=Number(c.current_config.dailyTokenBudget??c.budget.dailyTokenBudget??0);
   const rows=await tx.query('UPDATE autonomy_daily_usage SET used_tokens=used_tokens+$3,model_calls=model_calls+$4 WHERE bot_id=$1 AND tenant_id=$2 AND day=$5 AND ($6::bigint=0 OR used_tokens+reserved_tokens+$3<=$6::bigint) RETURNING bot_id',[c.bot_id,c.tenant_id,tokenDelta,callDelta,c.budget.day,limit]);
   if(!rows.length)throw conflict('model_daily_budget_exhausted');
  }else if(tokenDelta||callDelta){
   await tx.query('UPDATE autonomy_daily_usage SET used_tokens=used_tokens+$3,model_calls=model_calls+$4 WHERE bot_id=$1 AND tenant_id=$2 AND day=$5',[c.bot_id,c.tenant_id,tokenDelta,callDelta,c.budget.day]);
  }
  if(tokenDelta||callDelta){
   await tx.query("UPDATE autonomy_cycles SET usage=usage||jsonb_build_object('tokens',COALESCE((usage->>'tokens')::bigint,0)+$2::bigint,'modelCalls',COALESCE((usage->>'modelCalls')::int,0)+$3::int) WHERE id=$1",[c.id,tokenDelta,callDelta]);
   if(v.source==='media')await tx.query("UPDATE autonomy_cycles SET usage=usage||jsonb_build_object('mediaTokens',COALESCE((usage->>'mediaTokens')::bigint,0)+$2::bigint,'mediaModelCalls',COALESCE((usage->>'mediaModelCalls')::int,0)+$3::int) WHERE id=$1",[c.id,tokenDelta,callDelta]);
  }
  if(v.source==='media'){
   const media=Object.values(requests).filter((r:any)=>r.source==='media') as any[];
   const ids=[...new Set(media.map(r=>r.effectId))];
   const [legacy]=await tx.query("SELECT EXISTS(SELECT 1 FROM tool_effects e JOIN autonomy_actions a ON a.id=e.work_id AND a.bot_id=e.bot_id AND a.tenant_id=e.tenant_id WHERE a.cycle_id=$1 AND e.authority='autonomous' AND NOT (e.id::text=ANY($2::text[])) AND COALESCE((e.usage_checkpoint->>'settledModelRequests')::int,0)>0 AND COALESCE(e.usage_checkpoint->>'tokenStatus','unknown')='unknown') AS unknown",[c.id,ids]);
   await tx.query("UPDATE autonomy_cycles SET usage=usage||jsonb_build_object('mediaTokenStatus',$2::text) WHERE id=$1",[c.id,legacy.unknown||media.some(r=>!['settled','released'].includes(r.status))?'unknown':'actual']);
  }
 }
 return{status,...modelRequestTotals(budget)};
}
