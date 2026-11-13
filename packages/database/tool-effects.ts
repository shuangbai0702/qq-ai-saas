import {lockExternalCycle,checkExternalCapacity,settleCycleExternal} from './external-action-budget.js';
import {randomUUID} from 'node:crypto';
import type {Sql} from './index.js';
import {digest} from '../shared/security.js';
import {BilibiliConfigSchema,biliOperations,biliOperationAllowed,biliAccessAllowed,type BiliCall,type ToolResult} from '../contracts/bilibili.js';
import {runtimeStatus} from '../shared/runtime-status.js';

export const toolConflict=(message:string,code=409)=>Object.assign(new Error(message),{statusCode:code});
export function canonical(value:any):string{return JSON.stringify(value,(_,v)=>v&&typeof v==='object'&&!Array.isArray(v)?Object.fromEntries(Object.keys(v).sort().map(k=>[k,v[k]])):v)}
export class ToolEffects {
 constructor(public db:Sql){}
 async recentRead(job:any){
  // Only identical public video searches are reusable. Keep writes on their
  // existing action IDs; read reuse never grants permission to send a message.
  if(job.operation!=='search'||job.args.fresh===true)return null;
  const [goal]=job.authority==='autonomous'?await this.db.query('SELECT g.source FROM autonomy_actions a JOIN autonomy_goals g ON g.id=a.goal_id AND g.bot_id=a.bot_id AND g.tenant_id=a.tenant_id WHERE a.id::text=$1 AND a.bot_id=$2 AND a.tenant_id=$3',[job.work_id,job.bot_id,job.tenant_id]):[];
  const scope=job.origin_scope||goal?.source?.scope||'self';
  const [row]=await this.db.query(`SELECT e.id,e.result,e.updated_at FROM tool_effects e
   LEFT JOIN autonomy_actions a ON a.id::text=e.work_id AND a.bot_id=e.bot_id AND a.tenant_id=e.tenant_id AND e.authority='autonomous'
   LEFT JOIN autonomy_goals g ON g.id=a.goal_id AND g.bot_id=a.bot_id AND g.tenant_id=a.tenant_id
   WHERE e.tenant_id=$1 AND e.bot_id=$2 AND e.id<>$3 AND e.operation='search'
   AND e.args-'fresh'=($4::jsonb)-'fresh' AND e.state='finished' AND NOT e.cancel_requested
   AND e.result->>'execution'='completed' AND e.result->'data' IS NOT NULL
   AND e.result->'data'->'reused' IS NULL
   AND e.generation=$5 AND e.config_revision=$6 AND e.settings_revision=$7
   AND e.account_revision IS NOT DISTINCT FROM $8::integer AND e.account_uid IS NOT DISTINCT FROM $9::text
   AND COALESCE(NULLIF(e.origin_scope,''),g.source->>'scope','self')=$10
   AND e.updated_at BETWEEN now()-interval '2 minutes' AND now()
   ORDER BY e.updated_at DESC LIMIT 1`,[job.tenant_id,job.bot_id,job.id,JSON.stringify(job.args),job.generation,job.config_revision,job.settings_revision,job.account_revision??null,job.account_uid??null,scope]);
  return row??null;
 }
 async settings(tenant:string,bot:string,db=this.db){
  const config=BilibiliConfigSchema.parse({});await db.query('INSERT INTO bilibili_settings(bot_id,tenant_id,config) VALUES($1,$2,$3) ON CONFLICT DO NOTHING',[bot,tenant,JSON.stringify(config)]);
  const [s]=await db.query('SELECT * FROM bilibili_settings WHERE bot_id=$1 AND tenant_id=$2',[bot,tenant]);if(!s)throw toolConflict('B站配置不存在',404);return{...s,config:BilibiliConfigSchema.parse(s.config)};
 }
 async configure(tenant:string,bot:string,revision:number,config:unknown,modelSecret?:string|null){return this.db.transaction(async tx=>{
  await this.settings(tenant,bot,tx);const rows=await tx.query('UPDATE bilibili_settings SET config=$4,revision=revision+1,model_secret=CASE WHEN $5 THEN $6 ELSE model_secret END,updated_at=now() WHERE bot_id=$1 AND tenant_id=$2 AND revision=$3 RETURNING revision',[bot,tenant,revision,JSON.stringify(BilibiliConfigSchema.parse(config)),modelSecret!==undefined,modelSecret??null]);
  if(!rows.length)throw toolConflict('B站配置已变化，请刷新');await tx.query("UPDATE tool_effects SET cancel_requested=true,updated_at=now() WHERE bot_id=$1 AND tenant_id=$2 AND state IN ('queued','claimed','dispatching')",[bot,tenant]);return rows[0];
 })}
 async admit(bot:any,call:BiliCall){const operation=biliOperations[call.operation],args=operation.args.parse(call.args),semanticArgs=['comment','delete_comment'].includes(call.operation)?Object.fromEntries(Object.entries(args).filter(([key])=>key!=='bvid')):args,hash=digest(canonical({operation:call.operation,args:semanticArgs}));
  if(call.deadlineAt>Date.now()+(call.operation==='analyze'?600000:180000)||call.deadlineAt<Date.now()+500)throw toolConflict('工作时限无效',400);
  return this.db.transaction(async tx=>{
   await tx.query('SELECT id FROM bots WHERE id=$1 AND tenant_id=$2 FOR UPDATE',[bot.id,bot.tenant_id]);
   const [canceled]=await tx.query('SELECT 1 FROM tool_cancellations WHERE bot_id=$1 AND tenant_id=$2 AND work_id=$3 AND step_id=$4 AND expires_at>now()',[bot.id,bot.tenant_id,call.workId,call.stepId]);if(canceled)throw toolConflict('工作已取消');
   const [old]=await tx.query('SELECT * FROM tool_effects WHERE bot_id=$1 AND work_id=$2 AND step_id=$3',[bot.id,call.workId,call.stepId]);
   if(old){
    if(call.continuation)await this.validateContinuation({...old,origin_scope:call.origin?.scope??'',origin_event:call.origin?.eventId??'',continuation:{sourceActionId:call.continuation.actionId,sourceOwner:call.continuation.owner}},tx);
    if(old.args_digest!==hash)throw toolConflict('同一工具步骤参数不一致');
    if(old.authority!==call.authority||(old.sender_id??null)!==(call.senderId??null)||(old.mode_epoch??null)!==(call.epoch??null)||((!call.continuation||old.origin_scope)&&(old.origin_scope||'')!==(call.origin?.scope||''))||((!call.continuation||old.origin_event)&&(old.origin_event||'')!==(call.origin?.eventId||'')))throw toolConflict('工具步骤的原始身份或会话不一致',403);
    await this.validate(old,undefined,tx);return old;
   }
   const [fresh]=await tx.query('SELECT * FROM bots WHERE id=$1 AND tenant_id=$2',[bot.id,bot.tenant_id]);
   const s=await this.settings(bot.tenant_id,bot.id,tx),[account]=await tx.query('SELECT uid,revision,state FROM bilibili_accounts WHERE bot_id=$1 AND tenant_id=$2',[bot.id,bot.tenant_id]);
   if(call.origin&&call.authority==='foreground'&&call.origin.scope.startsWith('private:')&&call.origin.scope!=='private:'+call.senderId)throw toolConflict('恢复目标与真实发送者不符',403);
   if(operation.write){const [unknown]=await tx.query("SELECT id FROM tool_effects WHERE bot_id=$1 AND tenant_id=$2 AND args_digest=$3 AND state IN ('dispatching','unknown') LIMIT 1",[bot.id,bot.tenant_id,hash]);if(unknown)throw toolConflict('相同操作结果尚未确认，禁止再次提交')}
   const record={...fresh,operation:call.operation,args,bot_id:bot.id,settings_revision:s.revision,account_revision:account?.revision,account_uid:account?.uid,authority:call.authority,sender_id:call.senderId,mode_epoch:call.epoch,work_id:call.workId,config_revision:bot.revision,cancel_requested:false,deadline_at:new Date(call.deadlineAt),origin_scope:call.origin?.scope??'',origin_event:call.origin?.eventId??'',continuation:{objective:call.origin?.objective??'',...(call.continuation?{sourceActionId:call.continuation.actionId,sourceOwner:call.continuation.owner}:{})}};
   await this.validate(record,s,tx);
   // A different model step is not a new execution responsibility. Reuse only
   // unresolved analysis with exact arguments and the same work/goal owner.
   // Changed questions/ranges remain independent observations.
   if(call.operation==='analyze'){
    const [pending]=await tx.query(`SELECT e.* FROM tool_effects e
     WHERE e.bot_id=$1 AND e.tenant_id=$2 AND e.operation='analyze' AND e.args_digest=$3
      AND e.authority=$4 AND e.sender_id IS NOT DISTINCT FROM $5
      AND e.mode_epoch IS NOT DISTINCT FROM $6 AND e.origin_scope=$7
      AND e.generation=$8 AND e.config_revision=$9 AND e.settings_revision=$10
      AND e.account_revision IS NOT DISTINCT FROM $12
      AND e.account_uid IS NOT DISTINCT FROM $13
      AND NOT e.cancel_requested AND e.state IN ('queued','claimed','dispatching','unknown')
      AND (e.work_id=$11 OR EXISTS(SELECT 1 FROM autonomy_actions original
       JOIN autonomy_actions candidate ON candidate.goal_id=original.goal_id
        AND candidate.bot_id=original.bot_id AND candidate.tenant_id=original.tenant_id
       WHERE original.id=e.work_id AND candidate.id=$11 AND original.bot_id=e.bot_id
        AND original.tenant_id=e.tenant_id AND original.goal_id IS NOT NULL))
     ORDER BY e.created_at,e.id LIMIT 1`,[bot.id,bot.tenant_id,hash,call.authority,call.senderId??null,call.epoch??null,call.origin?.scope??'',bot.generation,bot.revision,s.revision,call.workId,account?.revision??null,account?.uid??null]);
    if(pending)return pending;
   }
   const [row]=await tx.query('INSERT INTO tool_effects(id,tenant_id,bot_id,work_id,step_id,operation,args,args_digest,authority,sender_id,mode_epoch,generation,config_revision,settings_revision,account_revision,deadline_at,account_uid,origin_scope,origin_event,continuation) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20) RETURNING *',[randomUUID(),bot.tenant_id,bot.id,call.workId,call.stepId,call.operation,JSON.stringify(args),hash,call.authority,call.senderId??null,call.epoch??null,bot.generation,bot.revision,s.revision,account?.revision??null,new Date(call.deadlineAt),account?.uid??null,call.origin?.scope??'',call.origin?.eventId??'',JSON.stringify(record.continuation)]);
   return row;
  });
 }
 async validateContinuation(job:any,db=this.db){
  const source=job.continuation;if(!source?.sourceActionId)return;
  const [parent]=await db.query('SELECT * FROM tool_effects WHERE id=$1 AND bot_id=$2 AND tenant_id=$3',[source.sourceActionId,job.bot_id,job.tenant_id]);
  if(!parent||parent.operation!=='analyze'||parent.authority!=='foreground'||job.authority!=='foreground'||parent.work_id!==job.work_id||parent.sender_id!==job.sender_id||parent.origin_scope!==job.origin_scope||parent.origin_event!==job.origin_event||parent.generation!==job.generation||parent.config_revision!==job.config_revision||parent.settings_revision!==job.settings_revision||parent.account_revision!==job.account_revision||parent.account_uid!==job.account_uid||parent.cancel_requested||parent.state!=='finished'||parent.completion_state!=='claimed'||parent.completion_owner!==source.sourceOwner||+new Date(parent.completion_until)<=Date.now()||+new Date(parent.deadline_at)<=Date.now())throw toolConflict('原任务续接身份、配置或完成租约已失效',403);
 }
 async validate(job:any,settings?:any,db=this.db){
  const [bot]=await db.query('SELECT * FROM bots WHERE id=$1 AND tenant_id=$2',[job.bot_id,job.tenant_id]),s=settings??await this.settings(job.tenant_id,job.bot_id,db);
  if(!runtimeStatus(bot).online||bot.generation!==job.generation||bot.revision!==job.config_revision||s.revision!==job.settings_revision||job.cancel_requested||+new Date(job.deadline_at)<=Date.now())throw toolConflict('工作或配置已失效，QQ必须真实在线');
  await this.validateContinuation(job,db);
  const op=biliOperations[job.operation as keyof typeof biliOperations];if(!op)throw toolConflict('工具未实现',400);
  if(!biliOperationAllowed(s.config,job.operation))throw toolConflict('未授权该B站能力',403);
  if(job.authority==='autonomous'){
   // Only an already-admitted asynchronous read may outlive its parent task.
   // Admission records contain the bot's `id`; truthiness is not evidence that
   // a tool effect exists, and must never revive completed work for new writes.
   const [a]=await db.query("SELECT a.id,s.config AS autonomy_config FROM autonomy_actions a JOIN autonomy_settings s ON s.bot_id=a.bot_id AND s.tenant_id=a.tenant_id WHERE a.id=$1 AND a.bot_id=$2 AND a.tenant_id=$3 AND a.epoch=$4 AND s.epoch=a.epoch AND s.config->>'mode'='active' AND ((a.status IN ('planned','executing') AND a.expires_at>now()) OR a.status IN ('completed','partial') AND EXISTS(SELECT 1 FROM tool_effects e WHERE e.id=$5 AND e.bot_id=a.bot_id AND e.tenant_id=a.tenant_id AND e.work_id=a.id AND e.mode_epoch=a.epoch AND e.operation='analyze' AND e.authority='autonomous' AND NOT e.cancel_requested AND e.deadline_at>now() AND e.state IN ('queued','claimed')))",[job.work_id,bot.id,bot.tenant_id,job.mode_epoch,job.id??null]);
   if(op.write&&(a?.autonomy_config?.rollout??'live')!=='live')throw toolConflict('shadow禁止真实外部写入',403);
   if(!s.config.allowAutonomous||!a)throw toolConflict('自主工作授权已失效',403);
  }else if(op.write||op.scope!=='public.read'){
   const [cfg]=await db.query('SELECT config FROM bot_config_revisions WHERE bot_id=$1 AND revision=$2',[bot.id,bot.revision]);
   if(!job.sender_id||cfg?.config?.behavior?.ownerUin!==job.sender_id)throw toolConflict('私有读取和写操作仅允许真实主人发起',403);
  }
  if(!['task_result','analysis_history'].includes(job.operation)){
   const [account]=await db.query('SELECT uid,revision,state,refresh_state FROM bilibili_accounts WHERE bot_id=$1 AND tenant_id=$2',[bot.id,bot.tenant_id]);
   if(!biliAccessAllowed(s.config,job.operation,account))throw toolConflict(account?'B站账号未就绪，请重新登录或等待凭证刷新；不会自动使用匿名状态':'请先登录B站账号；未绑定时需手动开启匿名访问，匿名请求可能失败',403);
   if(account?(account.revision!==job.account_revision||account.uid!==job.account_uid):job.account_revision!=null)throw toolConflict('B站账号已变化，请重新发起任务',403);
  }
  if(job.operation==='dm_send'&&!s.config.dmRecipients.includes(job.args.uid))throw toolConflict('私信对象未授权',403);
  if(['like','coin','favorite','watch_later','comment','delete_comment'].includes(job.operation)&&s.config.videoWriteAllowlist.length&&(!job.args.bvid||!s.config.videoWriteAllowlist.includes(job.args.bvid)))throw toolConflict('该视频未在B站写入许可范围；评论必须提供真实BV号',403);
  return s;
 }
 async cancelWork(bot:any,workId:string,stepId:string){return this.db.transaction(async tx=>{
  await tx.query('SELECT id FROM bots WHERE id=$1 AND tenant_id=$2 FOR UPDATE',[bot.id,bot.tenant_id]);
  await tx.query("INSERT INTO tool_cancellations(bot_id,tenant_id,work_id,step_id) VALUES($1,$2,$3,$4) ON CONFLICT(bot_id,work_id,step_id) DO UPDATE SET expires_at=now()+interval '10 minutes'",[bot.id,bot.tenant_id,workId,stepId]);
  await tx.query("UPDATE tool_effects SET cancel_requested=true,updated_at=now() WHERE bot_id=$1 AND tenant_id=$2 AND work_id=$3 AND step_id=$4 AND state IN ('queued','claimed','dispatching')",[bot.id,bot.tenant_id,workId,stepId]);return{cancelRequested:true};
 })}
 async claim(owner:string,excludeMedia=false){return this.db.transaction(async tx=>{
  await tx.query('DELETE FROM tool_cancellations WHERE expires_at<now()');
  // A crash while dispatching is ambiguous, never replay a POST. Reads can be
  // retried by a new Work; no unbounded automatic retries or model polling.
  await tx.query("UPDATE tool_effects SET state='unknown',completion_state=CASE WHEN operation='analyze' AND (origin_scope<>'' OR authority='autonomous') THEN 'pending' ELSE completion_state END,result=jsonb_build_object('schemaVersion','fishbot.tool-result.v1','actionId',id,'execution','failed','error',jsonb_build_object('code','executor_lost','retry','reconcile','userMessage','执行器丢失回执，只能核对原动作，不得重复发送'),'receipt',jsonb_build_object('status','unknown'),'usage',jsonb_build_object('modelRequests',coalesce((usage_checkpoint->>'modelRequests')::integer,0),'costStatus','unknown'),'versions',jsonb_build_object('adapter','bilibili-web-v1')),updated_at=now() WHERE (state='dispatching' OR state='claimed' AND operation='analyze' AND COALESCE((usage_checkpoint->>'modelRequests')::int,0)>0) AND lease_until<now()");
  await tx.query("UPDATE tool_effects SET state='canceled',completion_state=CASE WHEN operation='analyze' AND (origin_scope<>'' OR authority='autonomous') THEN 'pending' ELSE completion_state END,result=jsonb_build_object('schemaVersion','fishbot.tool-result.v1','actionId',id,'execution','canceled','receipt',jsonb_build_object('status','rejected'),'error',jsonb_build_object('code','canceled_before_completion','retry','never','userMessage','后台任务已取消或超过期限，未获得分析结果'),'usage',jsonb_build_object('modelRequests',coalesce((usage_checkpoint->>'modelRequests')::integer,0),'costStatus','unknown'),'versions',jsonb_build_object('adapter','bilibili-web-v1')),updated_at=now() WHERE (state='queued' OR state='claimed' AND lease_until<now()) AND (deadline_at<now() OR cancel_requested)");
  await tx.query("UPDATE tool_effects SET state='queued',lease_owner=NULL WHERE state='claimed' AND lease_until<now() AND NOT cancel_requested AND deadline_at>now()");
  // Recovery/cancellation also publishes a durable terminal fact, without a new scheduler.
  await tx.query("INSERT INTO autonomy_wakes(bot_id,tenant_id,dedupe_key,epoch,due_at,reason) SELECT e.bot_id,e.tenant_id,'bilibili:'||e.id,s.epoch,now(),'tool-completed' FROM tool_effects e JOIN autonomy_settings s ON s.bot_id=e.bot_id AND s.tenant_id=e.tenant_id AND s.epoch=e.mode_epoch JOIN bots b ON b.id=e.bot_id AND b.tenant_id=e.tenant_id AND b.generation=e.generation AND b.revision=e.config_revision WHERE e.authority='autonomous' AND e.operation='analyze' AND e.completion_state='pending' AND e.state IN ('finished','unknown','canceled') AND s.config->>'mode'='active' ON CONFLICT(bot_id,dedupe_key) DO NOTHING");
  const [job]=await tx.query("SELECT e.* FROM tool_effects e JOIN bots b ON b.id=e.bot_id AND b.tenant_id=e.tenant_id WHERE e.state='queued' AND ($1=false OR e.operation<>'analyze') AND NOT e.cancel_requested AND e.deadline_at>now() AND NOT EXISTS(SELECT 1 FROM tool_effects active WHERE active.bot_id=e.bot_id AND active.state IN ('claimed','dispatching')) ORDER BY CASE WHEN e.authority='foreground' THEN 0 ELSE 1 END,e.created_at LIMIT 1 FOR UPDATE OF e,b SKIP LOCKED",[excludeMedia]);
  if(!job)return null;await tx.query("UPDATE tool_effects SET state='claimed',lease_owner=$2,lease_until=now()+interval '210 seconds',updated_at=now() WHERE id=$1",[job.id,owner]);return{...job,state:'claimed',lease_owner:owner};
 })}
 async beginDispatch(job:any,signal:AbortSignal){return this.db.transaction(async tx=>{
  // State transitions belong to the ledger. An expired owner cannot dispatch
  // merely because recovery has not reclaimed its row yet.
  const [candidate]=await tx.query('SELECT * FROM tool_effects WHERE id=$1 AND lease_owner=$2',[job.id,job.lease_owner]);
  if(!candidate||candidate.state!=='claimed'||+new Date(candidate.lease_until)<=Date.now())return false;
  const reserveCycle=candidate.authority==='autonomous'&&biliOperations[candidate.operation as keyof typeof biliOperations].write?await lockExternalCycle(tx,{id:candidate.bot_id,tenant_id:candidate.tenant_id},candidate.work_id,candidate.mode_epoch):null;
  const [fresh]=await tx.query('SELECT * FROM tool_effects WHERE id=$1 AND lease_owner=$2 FOR UPDATE',[job.id,job.lease_owner]);
  if(!fresh||fresh.state!=='claimed'||+new Date(fresh.lease_until)<=Date.now())return false;
  await this.validate(fresh,undefined,tx);
  if(signal.aborted)return false;
  const reserve=fresh.authority==='autonomous'&&biliOperations[fresh.operation as keyof typeof biliOperations].write;
  if(reserve){
   const cycle=reserveCycle??await lockExternalCycle(tx,{id:fresh.bot_id,tenant_id:fresh.tenant_id},fresh.work_id,fresh.mode_epoch);
   if(!fresh.budget_reserved)await checkExternalCapacity(tx,cycle);
  }
  if(signal.aborted)return false;
  // Reservation and dispatch transition commit together. An expired lease
  // cannot leave behind a reservation for a proven unstarted platform call.
  const rows=await tx.query("UPDATE tool_effects SET state='dispatching',budget_reserved=budget_reserved OR $3,updated_at=now() WHERE id=$1 AND lease_owner=$2 AND state='claimed' AND lease_until>clock_timestamp() RETURNING id",[job.id,job.lease_owner,reserve]);
  if(signal.aborted&&rows.length)throw toolConflict('工具派发已取消'); // rolls back before returning any dispatch permit
  return rows.length>0;
 })}
 async renew(job:any){
  const [fresh]=await this.db.query('SELECT * FROM tool_effects WHERE id=$1 AND lease_owner=$2 AND state=\'claimed\'',[job.id,job.lease_owner]);
  if(!fresh)throw toolConflict('工具租约已失效');
  await this.validate(fresh);
  const rows=await this.db.query("UPDATE tool_effects SET lease_until=clock_timestamp()+interval '210 seconds' WHERE id=$1 AND lease_owner=$2 AND state='claimed' AND NOT cancel_requested AND lease_until>clock_timestamp() RETURNING id",[job.id,job.lease_owner]);
  if(!rows.length)throw toolConflict('工具租约已失效');
 }
 async finish(job:any,result:ToolResult){return this.db.transaction(async tx=>{
  // Consistent cycle -> effect lock order with model budget reservation.
  const cycle=job.authority==='autonomous'?await lockExternalCycle(tx,{id:job.bot_id,tenant_id:job.tenant_id},job.work_id,job.mode_epoch,false):null;
  const [original]=await tx.query('SELECT * FROM tool_effects WHERE id=$1 FOR UPDATE',[job.id]);
  const rows=await tx.query("UPDATE tool_effects SET state=$3,result=$4,completion_state=CASE WHEN operation='analyze' AND (origin_scope<>'' OR authority='autonomous') THEN 'pending' ELSE completion_state END,lease_until=NULL,updated_at=now() WHERE id=$1 AND lease_owner=$2 AND (state IN ('claimed','dispatching') OR state='unknown' AND result->'receipt'->>'status'='unknown' AND $5<>'unknown') RETURNING id",[job.id,job.lease_owner,result.receipt.status==='unknown'?'unknown':result.execution==='canceled'?'canceled':'finished',JSON.stringify(result),result.receipt.status]);
  if(cycle){
   await settleCycleExternal(tx,cycle);
   if(rows.length){
    const checkpoint=original?.usage_checkpoint||{},calls=Math.max(Number(checkpoint.modelRequests||0),Number(result.usage?.modelRequests||0));
    const raw=(result.usage as any)?.providerUsage,actualTokens=Number.isSafeInteger(raw?.total_tokens)&&raw.total_tokens>=0;
    const tokens=Math.max(Number(checkpoint.settledTokens||0),actualTokens?raw.total_tokens:0);
    const modelDelta=Math.max(0,calls-Number(checkpoint.settledModelRequests||0)),tokenDelta=Math.max(0,tokens-Number(checkpoint.settledTokens||0));
    // Journaled model requests settle through their request identity. Keep the
    // existing checkpoint as the fallback for effects without that journal;
    await tx.query("UPDATE tool_effects SET usage_checkpoint=usage_checkpoint||$2::jsonb WHERE id=$1",[job.id,JSON.stringify({modelRequests:calls,settledModelRequests:calls,settledTokens:tokens,tokenStatus:actualTokens||checkpoint.tokenStatus==='actual'?'actual':'unknown'})]);
    const journaled=Object.values(cycle.budget.modelRequests||{}).some((r:any)=>r.source==='media'&&r.effectId===job.id);
    if(cycle.finished_at&&!journaled){
     await tx.query("UPDATE autonomy_cycles SET usage=jsonb_set(jsonb_set(jsonb_set(jsonb_set(usage,'{modelCalls}',to_jsonb(COALESCE((usage->>'modelCalls')::int,0)+$2::int)),'{tokens}',to_jsonb(COALESCE((usage->>'tokens')::bigint,0)+$3::bigint)),'{mediaModelCalls}',to_jsonb(COALESCE((usage->>'mediaModelCalls')::int,0)+$2::int)),'{mediaTokens}',to_jsonb(COALESCE((usage->>'mediaTokens')::bigint,0)+$3::bigint)) WHERE id=$1",[cycle.id,modelDelta,tokenDelta]);
     await tx.query("UPDATE autonomy_cycles SET usage=usage||jsonb_build_object('mediaTokenStatus',$2::text) WHERE id=$1",[cycle.id,!actualTokens&&checkpoint.tokenStatus!=='actual'||cycle.usage?.mediaTokenStatus==='unknown'?'unknown':'actual']);
     await tx.query('UPDATE autonomy_daily_usage SET model_calls=model_calls+$3,used_tokens=used_tokens+$4 WHERE bot_id=$1 AND tenant_id=$2 AND day=$5',[job.bot_id,job.tenant_id,modelDelta,tokenDelta,cycle.budget.day]);
    }
   }
  }
  if(rows.length&&job.authority==='autonomous'&&job.operation==='analyze'){
   // Wake the existing outer owner, never introduce a Bilibili scheduler.
   await tx.query("INSERT INTO autonomy_wakes(bot_id,tenant_id,dedupe_key,epoch,due_at,reason) SELECT bot_id,tenant_id,$3,epoch,now(),'tool-completed' FROM autonomy_settings WHERE bot_id=$1 AND tenant_id=$2 AND epoch=$4 AND config->>'mode'='active' ON CONFLICT(bot_id,dedupe_key) DO NOTHING",[job.bot_id,job.tenant_id,'bilibili:'+job.id,job.mode_epoch]);
   await tx.query("UPDATE autonomy_settings SET observation=jsonb_set(observation,'{bilibiliCompletion}',$3),updated_at=now() WHERE bot_id=$1 AND tenant_id=$2 AND epoch=$4",[job.bot_id,job.tenant_id,JSON.stringify({id:job.id,workId:job.work_id,result}),job.mode_epoch]);
  }
 })}
 async completion(bot:any){return this.db.transaction(async tx=>{
  const [job]=await tx.query("SELECT * FROM tool_effects WHERE bot_id=$1 AND tenant_id=$2 AND generation=$3 AND config_revision=$4 AND authority='foreground' AND origin_scope<>'' AND state IN ('finished','unknown','canceled') AND (completion_state='pending' OR completion_state='claimed' AND completion_until<now()) ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED",[bot.id,bot.tenant_id,bot.generation,bot.revision]);
  if(!job||!runtimeStatus(bot).online)return null;
  const settings=await this.settings(bot.tenant_id,bot.id,tx);if(!settings.config.enabled||settings.revision!==job.settings_revision){await tx.query("UPDATE tool_effects SET completion_state='stale' WHERE id=$1",[job.id]);return null}
  const owner=randomUUID();await tx.query("UPDATE tool_effects SET completion_state='claimed',completion_owner=$2,completion_until=now()+interval '120 seconds' WHERE id=$1",[job.id,owner]);
  return{id:job.id,owner,workId:job.work_id,scope:job.origin_scope,senderId:job.sender_id,eventId:job.origin_event,objective:job.continuation.objective,result:job.result,generation:job.generation,revision:job.config_revision};
 })}
 async acknowledgeCompletion(bot:any,id:string,owner:string,status:'done'|'unknown'){
  await this.db.query("UPDATE tool_effects SET completion_state=$4,completion_until=NULL WHERE id=$1 AND bot_id=$2 AND tenant_id=$3 AND completion_owner=$5 AND completion_state='claimed'",[id,bot.id,bot.tenant_id,status,owner]);return{ok:true};
 }
 async get(tenant:string,bot:string,id:string){const [r]=await this.db.query('SELECT id,state,operation,result,cancel_requested,created_at,updated_at FROM tool_effects WHERE id=$1 AND bot_id=$2 AND tenant_id=$3',[id,bot,tenant]);return r}
}
