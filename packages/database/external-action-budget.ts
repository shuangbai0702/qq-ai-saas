import type {Sql} from './index.js';
import {runtimeStatus} from '../shared/runtime-status.js';

const conflict=(message:string)=>Object.assign(new Error(message),{statusCode:409});
// Reservations belong to the existing Work/effect rows. Every platform locks
// the same cycle before reading or changing those rows, including settlement.
export async function lockExternalCycle(db:Sql,bot:any,workId:string,epoch:number,active=true){
 const params=[workId,bot.id??bot.bot_id,bot.tenant_id,epoch];
 await db.query(`SELECT c.id FROM autonomy_actions a JOIN autonomy_cycles c ON c.id=a.cycle_id AND c.bot_id=a.bot_id AND c.tenant_id=a.tenant_id WHERE a.id=$1 AND a.bot_id=$2 AND a.tenant_id=$3 AND a.epoch=$4 FOR UPDATE OF c`,params);
 // Refresh in a new statement after a contended lock: joined action JSON read
 // before the lock wait is not evidence of the latest reservation ownership.
 const [row]=await db.query(`SELECT c.*,a.external_effects,a.status AS action_status,a.expires_at,s.epoch AS current_epoch,s.config AS current_config,b.generation AS current_generation,b.revision AS current_revision,b.health,b.desired_state,b.deleted_at
 FROM autonomy_actions a JOIN autonomy_cycles c ON c.id=a.cycle_id AND c.bot_id=a.bot_id AND c.tenant_id=a.tenant_id
 JOIN autonomy_settings s ON s.bot_id=a.bot_id AND s.tenant_id=a.tenant_id JOIN bots b ON b.id=a.bot_id AND b.tenant_id=a.tenant_id
 WHERE a.id=$1 AND a.bot_id=$2 AND a.tenant_id=$3 AND a.epoch=$4`,params);
 if(!row||active&&(row.finished_at||!['planned','executing'].includes(row.action_status)||+new Date(row.expires_at)<=Date.now()||row.current_epoch!==epoch||row.current_config.mode!=='active'||row.current_generation!==row.generation||row.current_revision!==row.config_revision||row.desired_state!=='running'||!runtimeStatus(row).online))throw conflict('自主外部动作工作已失效');
 return row;
}
export async function externalCount(db:Sql,cycleId:string){
 const [r]=await db.query(`SELECT (SELECT count(*) FROM autonomy_actions a CROSS JOIN LATERAL jsonb_each(a.external_effects) e WHERE a.cycle_id=$1 AND e.value->>'status'<>'released')+(SELECT count(*) FROM tool_effects e JOIN autonomy_actions a ON a.id=e.work_id AND a.bot_id=e.bot_id AND a.tenant_id=e.tenant_id WHERE a.cycle_id=$1 AND e.authority='autonomous' AND e.budget_reserved) AS total`,[cycleId]);
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
  const effects=cycle.external_effects||{},old=effects[v.logicalActionId];
  if(v.phase==='reserve'){
   if(old&&old.status!=='released')return{reserved:true,status:old.status};
   await checkExternalCapacity(tx,cycle);
  }else if(!old)throw conflict('外部动作没有预留');
  // A protocol uncertainty/confirmation is irrevocable. Rejected platform
  // replies are still dispatched; only a proven pre-dispatch stop releases.
  const status=old&&['unknown','confirmed'].includes(old.status)?(v.phase==='confirmed'?'confirmed':old.status):v.phase==='reserve'?'reserved':v.phase;
  effects[v.logicalActionId]={status};
  await tx.query('UPDATE autonomy_actions SET external_effects=$2 WHERE id=$1',[v.workId,JSON.stringify(effects)]);
  return{reserved:status!=='released',status,used:await settleCycleExternal(tx,cycle)};
 });
}
export async function biliWorkEffects(db:Sql,bot:string,tenant:string,workId:string){
 const rows=await db.query("SELECT id,operation,state,result FROM tool_effects WHERE bot_id=$1 AND tenant_id=$2 AND work_id=$3 AND authority='autonomous' AND budget_reserved ORDER BY created_at",[bot,tenant,workId]);
 return rows.map(r=>({id:r.id,logicalActionId:'bilibili:'+r.id,action:'bilibili_'+r.operation,status:r.result?.receipt?.status==='confirmed'?'confirmed':r.result?.receipt?.status==='rejected'?'rejected':'unknown',receipt:r.result?.receipt,source:'bilibili'}));
}
