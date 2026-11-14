import type {Sql} from './index.js';
import {runtimeStatus} from '../shared/runtime-status.js';

const conflict=(message:string)=>Object.assign(new Error(message),{statusCode:409});
// QQ reservations belong to the original cycle budget, alongside model usage.
// Every platform locks that cycle before changing a reservation or settlement.
export async function lockExternalCycle(db:Sql,bot:any,workId:string,epoch:number,active=true){
 const params=[workId,bot.id??bot.bot_id,bot.tenant_id,epoch];
 await db.query('SELECT id FROM autonomy_cycles WHERE id=$1 AND bot_id=$2 AND tenant_id=$3 AND epoch=$4 FOR UPDATE',params);
 const [row]=await db.query(`SELECT c.*,c.state AS action_status,c.work_expires_at AS expires_at,s.epoch AS current_epoch,s.config AS current_config,b.generation AS current_generation,b.revision AS current_revision,b.health,b.desired_state,b.deleted_at
 FROM autonomy_cycles c JOIN autonomy_settings s ON s.bot_id=c.bot_id AND s.tenant_id=c.tenant_id JOIN bots b ON b.id=c.bot_id AND b.tenant_id=c.tenant_id
 WHERE c.id=$1 AND c.bot_id=$2 AND c.tenant_id=$3 AND c.epoch=$4`,params);
 if(active&&row?.current_config?.rollout==='shadow')throw conflict('shadow禁止真实外部写入');
 if(!row||active&&(row.finished_at||row.action_status!=='acting'||+new Date(row.expires_at)<=Date.now()||row.current_epoch!==epoch||row.current_config.mode!=='active'||(row.current_config.rollout??'live')!=='live'||row.current_generation!==row.generation||row.current_revision!==row.config_revision||!runtimeStatus(row).online))throw conflict('自主外部动作工作已失效');
 return row;
}
export async function externalCount(db:Sql,cycleId:string){
 const [r]=await db.query(`SELECT (SELECT count(*) FROM autonomy_cycles c CROSS JOIN LATERAL jsonb_each(COALESCE(c.budget->'qqExternalRequests','{}'::jsonb)) work CROSS JOIN LATERAL jsonb_each(work.value) e WHERE c.id=$1 AND e.value->>'status'<>'released')+(SELECT count(*) FROM tool_effects e WHERE e.cycle_id=$1 AND e.authority='autonomous' AND e.budget_reserved) AS total`,[cycleId]);
 return Number(r.total);
}
export async function checkExternalCapacity(db:Sql,cycle:any){
 const used=await externalCount(db,cycle.id),limit=cycle.budget.cycleExternalActions;
 if(!Number.isSafeInteger(limit)||limit<0||used>=limit)throw conflict('external_action_budget');
}
export async function settleCycleExternal(db:Sql,cycle:any){
 const count=await externalCount(db,cycle.id);
 // Late authoritative receipts update the original cycle/day, even after the
 // cycle owner stopped. Reserved/unknown effects always remain charged.
 if(cycle.finished_at){
  const delta=count-Number(cycle.usage?.externalActions||0);
  await db.query("UPDATE autonomy_cycles SET usage=jsonb_set(usage,'{externalActions}',$2::jsonb) WHERE id=$1",[cycle.id,JSON.stringify(count)]);
  if(delta)await db.query('UPDATE autonomy_daily_usage SET external_actions=external_actions+$3 WHERE bot_id=$1 AND tenant_id=$2 AND day=$4',[cycle.bot_id,cycle.tenant_id,delta,cycle.budget.day]);
 }
 return count;
}
export async function qqExternalBudget(db:Sql,bot:any,v:{workId:string;epoch:number;logicalActionId:string;phase:'reserve'|'confirmed'|'unknown'|'released'}){
 return db.transaction(async tx=>{
  const cycle=await lockExternalCycle(tx,bot,v.workId,v.epoch,v.phase==='reserve');
  const requests={...(cycle.budget.qqExternalRequests||{})};
  const effects={...(requests[v.workId]||{})},old=effects[v.logicalActionId];
  if(v.phase==='reserve'){
   if(old&&old.status!=='released')return{reserved:true,status:old.status};
   await checkExternalCapacity(tx,cycle);
  }else if(!old)throw conflict('外部动作没有预留');
  // A protocol uncertainty/confirmation is irrevocable. Rejected platform
  // replies are still dispatched; only a proven pre-dispatch stop releases.
  const status=old&&['unknown','confirmed'].includes(old.status)?(v.phase==='confirmed'?'confirmed':old.status):v.phase==='reserve'?'reserved':v.phase;
  effects[v.logicalActionId]={status};
  requests[v.workId]=effects;
  await tx.query('UPDATE autonomy_cycles SET budget=$2 WHERE id=$1',[cycle.id,JSON.stringify({...cycle.budget,qqExternalRequests:requests})]);
  return{reserved:status!=='released',status,used:await settleCycleExternal(tx,cycle)};
 });
}
export async function biliWorkEffects(db:Sql,bot:string,tenant:string,workId:string){
 const rows=await db.query("SELECT id,operation,state,result FROM tool_effects WHERE bot_id=$1 AND tenant_id=$2 AND work_id=$3 AND authority='autonomous' AND budget_reserved ORDER BY created_at",[bot,tenant,workId]);
 return rows.map(r=>({id:r.id,logicalActionId:'bilibili:'+r.id,action:'bilibili_'+r.operation,status:r.result?.receipt?.status==='confirmed'?'confirmed':r.result?.receipt?.status==='rejected'?'rejected':'unknown',receipt:r.result?.receipt,source:'bilibili'}));
}
