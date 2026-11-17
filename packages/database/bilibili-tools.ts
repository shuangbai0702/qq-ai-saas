import type {Sql} from './index.js';
import {runtimeClient,type RuntimeCall} from '../shared/runtime-client.js';
import {runtimeStatus} from '../shared/runtime-status.js';
import {digest} from '../shared/security.js';
import {BilibiliConfigSchema,biliOperations,biliOperationAllowed,biliAccessAllowed,type BiliCall,type ToolResult} from '../contracts/bilibili.js';
import {bilibiliModelConfigured,bilibiliModelRequired} from '../shared/bilibili-model.js';
import {qqExternalBudget,qqExternalBudgetInTransaction} from './external-action-budget.js';
import {cycleModelBudget} from './model-request-budget.js';

export const toolConflict=(message:string,code=409)=>Object.assign(new Error(message),{statusCode:code});
export function canonical(value:any):string{return JSON.stringify(value,(_,v)=>v&&typeof v==='object'&&!Array.isArray(v)?Object.fromEntries(Object.keys(v).sort().map(k=>[k,v[k]])):v)}

/** Permissions live in PostgreSQL; execution lives in the original Work only. */
export class BilibiliTools {
 constructor(public db:Sql,private call?:RuntimeCall){}
 async rpc(bot:any,operation:string,data:any={},db=this.db){
  const [current]=await db.query('SELECT id AS bot_id,tenant_id,generation,revision FROM bots WHERE id=$1 AND tenant_id=$2 AND deleted_at IS NULL',[bot.bot_id??bot.id,bot.tenant_id]);
  if(!current)throw toolConflict('机器人不存在',404);
  return (this.call??runtimeClient(db))(current,'tool',{operation,data});
 }
 async settings(tenant:string,bot:string,db=this.db){
  const config=BilibiliConfigSchema.parse({});await db.query('INSERT INTO bilibili_settings(bot_id,tenant_id,config) VALUES($1,$2,$3) ON CONFLICT DO NOTHING',[bot,tenant,JSON.stringify(config)]);
  const [s]=await db.query('SELECT * FROM bilibili_settings WHERE bot_id=$1 AND tenant_id=$2',[bot,tenant]);if(!s)throw toolConflict('B站配置不存在',404);return{...s,config:BilibiliConfigSchema.parse(s.config)};
 }
 async configure(tenant:string,bot:string,revision:number,config:unknown,modelSecret?:string|null){return this.db.transaction(async tx=>{
  await tx.query('SELECT id FROM bots WHERE id=$1 AND tenant_id=$2 FOR UPDATE',[bot,tenant]);
  await this.settings(tenant,bot,tx);const rows=await tx.query('UPDATE bilibili_settings SET config=$4,revision=revision+1,model_secret=CASE WHEN $5 THEN $6 ELSE model_secret END,updated_at=now() WHERE bot_id=$1 AND tenant_id=$2 AND revision=$3 RETURNING revision',[bot,tenant,revision,JSON.stringify(BilibiliConfigSchema.parse(config)),modelSecret!==undefined,modelSecret??null]);
  if(!rows.length)throw toolConflict('B站配置已变化，请刷新');return rows[0];
 })}
 async admit(bot:any,call:BiliCall,delegation?:{sessionId:string;delegationId:string}){
  const operation=biliOperations[call.operation],args=operation.args.parse(call.args);
  const semanticArgs=['comment','delete_comment'].includes(call.operation)?Object.fromEntries(Object.entries(args).filter(([key])=>key!=='bvid')):args;
  const hash=digest(canonical({operation:call.operation,args:semanticArgs}));
  if(call.deadlineAt>Date.now()+(call.operation==='analyze'?600000:180000)||call.deadlineAt<Date.now()+500)throw toolConflict('工作时限无效',400);
  const record=await this.db.transaction(async tx=>{
   const [fresh]=await tx.query('SELECT * FROM bots WHERE id=$1 AND tenant_id=$2 FOR UPDATE',[bot.id,bot.tenant_id]);
   const s=await this.settings(bot.tenant_id,bot.id,tx),[account]=await tx.query('SELECT uid,revision,state FROM bilibili_accounts WHERE bot_id=$1 AND tenant_id=$2',[bot.id,bot.tenant_id]);
   if(call.origin&&call.authority==='foreground'&&call.origin.scope.startsWith('private:')&&call.origin.scope!=='private:'+call.senderId)throw toolConflict('恢复目标与真实发送者不符',403);
   const value={tenant_id:fresh.tenant_id,generation:fresh.generation,operation:call.operation,args,args_digest:hash,bot_id:bot.id,settings_revision:s.revision,account_revision:account?.revision,account_uid:account?.uid,authority:call.authority,sender_id:call.senderId,mode_epoch:call.epoch,work_id:call.workId,step_id:call.stepId,config_revision:bot.revision,cancel_requested:false,deadlineAt:call.deadlineAt,deadline_at:new Date(call.deadlineAt),origin_scope:call.origin?.scope??'',origin_event:call.origin?.eventId??'',cycle_id:call.authority==='autonomous'?call.workId:null,write:operation.write,continuation:{objective:call.origin?.objective??'',...(call.continuation?{sourceActionId:call.continuation.actionId,sourceOwner:call.continuation.owner}:{})}};
   if(delegation){
    if(call.operation!=='analyze'||!/^fishbot-[a-f0-9]{64}$/.test(delegation.sessionId)||!delegation.delegationId||delegation.delegationId.length>256)throw toolConflict('委托媒体身份无效',400);
    Object.assign(value.continuation,{dsh:{sessionId:delegation.sessionId,delegationId:delegation.delegationId}});
   }
   await this.validate(value,s,tx);
   if(call.operation==='analyze'){
    const [model]=await tx.query('SELECT r.config,(s.ciphertext IS NOT NULL) AS has_key FROM bot_config_revisions r LEFT JOIN secrets s ON s.bot_id=r.bot_id AND s.tenant_id=$3 WHERE r.bot_id=$1 AND r.revision=$2',[bot.id,bot.revision,bot.tenant_id]);
    if(!bilibiliModelConfigured(s,model?.config,model?.has_key)&&!await this.rpc(bot,'get',{workId:call.workId,stepId:call.stepId},tx))throw Object.assign(toolConflict(bilibiliModelRequired),{code:'model_not_configured'});
   }
   // Versions/permissions commit before admission to the existing member Work.
   // A later revision is revalidated before claim/dispatch/model invocation.
   return value;
  });
  return this.rpc(bot,'admit',{record});
 }
 async validateContinuation(job:any,db=this.db){
  const source=job.continuation;if(!source?.sourceActionId)return;
  // A DSH child media job was admitted under the parent's live completion
  // lease. Its own original Work record carries that proof; ACK/retirement of
  // the consumed parent must not invalidate an already queued child Worker.
  if(job.id&&job.operation==='analyze'&&source.dsh){
   const admitted=await this.get(job.tenant_id,job.bot_id,job.id,db);
   if(!admitted||admitted.operation!=='analyze'||admitted.authority!=='foreground'||['bot_id','tenant_id','authority','operation','work_id','sender_id','origin_scope','origin_event','generation','config_revision','settings_revision','account_revision','account_uid','mode_epoch','args_digest'].some(key=>admitted[key]!==job[key])||canonical(admitted.args)!==canonical(job.args)||canonical(admitted.continuation)!==canonical(source))throw toolConflict('已准入媒体任务身份不一致',403);
   return;
  }
  const parent=await this.get(job.tenant_id,job.bot_id,source.sourceActionId,db);
  if(!parent||parent.operation!=='analyze'||parent.authority!=='foreground'||job.authority!=='foreground'||parent.work_id!==job.work_id||parent.sender_id!==job.sender_id||parent.origin_scope!==job.origin_scope||parent.origin_event!==job.origin_event||parent.generation!==job.generation||parent.config_revision!==job.config_revision||parent.settings_revision!==job.settings_revision||parent.account_revision!==job.account_revision||parent.account_uid!==job.account_uid||parent.cancel_requested||!['finished','unknown','canceled'].includes(parent.state)||parent.completion_state!=='claimed'||parent.completion_owner!==source.sourceOwner||+new Date(parent.completion_until)<=Date.now()||biliOperations[job.operation as keyof typeof biliOperations].write&&+new Date(parent.deadline_at)<=Date.now())throw toolConflict('原任务续接身份、配置或完成租约已失效',403);
 }
 async validate(job:any,settings?:any,db=this.db){
  const [bot]=await db.query('SELECT * FROM bots WHERE id=$1 AND tenant_id=$2',[job.bot_id,job.tenant_id]),s=settings??await this.settings(job.tenant_id,job.bot_id,db);
  if(!runtimeStatus(bot).online||bot.generation!==job.generation||bot.revision!==job.config_revision||s.revision!==job.settings_revision||job.cancel_requested||+new Date(job.deadline_at)<=Date.now())throw toolConflict('工作或配置已失效，QQ必须真实在线');
  await this.validateContinuation(job,db);
  const op=biliOperations[job.operation as keyof typeof biliOperations];if(!op)throw toolConflict('工具未实现',400);
  if(!biliOperationAllowed(s.config,job.operation))throw toolConflict('未授权该B站能力',403);
  if(job.authority==='autonomous'){
   const [c]=await db.query("SELECT c.*,s.config AS autonomy_config,s.epoch AS current_epoch,s.lease_owner,s.lease_until FROM autonomy_cycles c JOIN autonomy_settings s ON s.bot_id=c.bot_id AND s.tenant_id=c.tenant_id WHERE c.id=$1 AND c.bot_id=$2 AND c.tenant_id=$3 AND c.epoch=$4",[job.cycle_id??job.work_id,bot.id,bot.tenant_id,job.mode_epoch]);
   const active=c&&!c.finished_at&&c.state==='acting'&&+new Date(c.work_expires_at)>Date.now()&&c.lease_owner===c.owner&&+new Date(c.lease_until)>Date.now();
   let admitted=false;
   if(c?.finished_at&&job.id&&job.operation==='analyze'){
    const original=await this.get(job.tenant_id,job.bot_id,job.id,db);
    admitted=!!original&&original.work_id===c.id&&original.mode_epoch===c.epoch&&['queued','claimed'].includes(original.state)&&!original.cancel_requested&&+new Date(original.deadline_at)>Date.now();
   }
   if(op.write&&(c?.autonomy_config?.rollout??'live')!=='live')throw toolConflict('shadow禁止真实外部写入',403);
   if(!s.config.allowAutonomous||!c||c.current_epoch!==c.epoch||c.autonomy_config.mode!=='active'||!active&&!admitted)throw toolConflict('自主工作授权已失效',403);
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
 async get(tenant:string,bot:string,id:string,db=this.db){return this.rpc({id:bot,tenant_id:tenant},'get',{id},db)}
 async list(bot:any,limit=20,summary=false){return(await this.rpc(bot,'list',{limit,summary})).jobs as any[]}
 async pending(bot:any,workId?:string){
  const s=await this.settings(bot.tenant_id,bot.id??bot.bot_id),[a]=await this.db.query('SELECT epoch,config FROM autonomy_settings WHERE bot_id=$1 AND tenant_id=$2',[bot.id??bot.bot_id,bot.tenant_id]);
  return(await this.rpc(bot,'pending',{workId,settingsRevision:s.revision,enabled:s.config.enabled,autonomyEpoch:a?.epoch??null,autonomyEnabled:a?.config?.mode==='active'})).pending as any[];
 }
 async cancelWork(bot:any,workId:string,stepId:string){return this.rpc(bot,'cancel',{workId,stepId})}
 async cancel(bot:any,id:string){const job=await this.get(bot.tenant_id,bot.id??bot.bot_id,id);if(!job)throw toolConflict('工具任务不存在',404);return this.cancelWork(bot,job.work_id,job.step_id)}
 async claim(owner:string,excludeMedia=false){
  const bots=await this.db.query("SELECT * FROM bots WHERE desired_state='running' AND deleted_at IS NULL ORDER BY id");
  for(const bot of bots){
   if(!runtimeStatus(bot).online||!runtimeStatus(bot).aiReady)continue;
   try{const job=await this.rpc(bot,'claim',{owner,excludeMedia});if(job)return job}
   catch{console.error('原Work工具队列暂不可达，保留执行责任')}
  }
  return null;
 }
 async recentRead(job:any){
  if(job.operation!=='search'||job.args.fresh===true)return null;
  const jobs=await this.list(job,48);
  return jobs.find(e=>e.id!==job.id&&e.operation==='search'&&e.state==='finished'&&!e.cancel_requested&&e.result?.execution==='completed'&&e.result?.data&&!e.result.data.reused&&Date.now()-+new Date(e.updated_at)>=0&&Date.now()-+new Date(e.updated_at)<=120000&&['generation','config_revision','settings_revision','account_revision','account_uid','origin_scope'].every(k=>(e[k]??null)===(job[k]??null))&&canonical({...e.args,fresh:undefined})===canonical({...job.args,fresh:undefined}))??null;
 }
 async renew(job:any){await this.validate(job);return this.rpc(job,'advance',{id:job.id,owner:job.lease_owner,phase:'renew'})}
 async checkpoint(job:any,usage:any){return this.db.transaction(async tx=>{
  await tx.query('SELECT id FROM bots WHERE id=$1 AND tenant_id=$2 FOR UPDATE',[job.bot_id,job.tenant_id]);await this.validate(job,undefined,tx);
  return this.rpc(job,'advance',{id:job.id,owner:job.lease_owner,phase:'checkpoint',usage},tx);
 })}
 async beginDispatch(job:any,signal:AbortSignal){
  return this.db.transaction(async tx=>{
   await tx.query('SELECT id FROM bots WHERE id=$1 AND tenant_id=$2 FOR UPDATE',[job.bot_id,job.tenant_id]);await this.validate(job,undefined,tx);if(signal.aborted)return false;
   await this.rpc(job,'advance',{id:job.id,owner:job.lease_owner,phase:'check'},tx);
   const reserve=job.authority==='autonomous'&&biliOperations[job.operation as keyof typeof biliOperations].write;
   if(reserve)await qqExternalBudgetInTransaction(tx,{id:job.bot_id,tenant_id:job.tenant_id},{workId:job.work_id,epoch:job.mode_epoch,logicalActionId:'bilibili:'+job.id,phase:'reserve'});
   if(signal.aborted){
    // Never let a late former owner release a replacement's reservation.
    // A failed native owner check rolls this PG transaction back instead.
    await this.rpc(job,'advance',{id:job.id,owner:job.lease_owner,phase:'check'},tx);
    if(reserve)await qqExternalBudgetInTransaction(tx,{id:job.bot_id,tenant_id:job.tenant_id},{workId:job.work_id,epoch:job.mode_epoch,logicalActionId:'bilibili:'+job.id,phase:'released'});
    return false;
   }
   const response=await this.rpc(job,'advance',{id:job.id,owner:job.lease_owner,phase:'dispatch'},tx);
   return response.accepted===true;
  });
 }
 async finish(job:any,result:ToolResult,dispatchStarted?:boolean){
  const finished=await this.rpc(job,'advance',{id:job.id,owner:job.lease_owner,phase:'finish',result});
  if(finished.accepted===true&&job.authority==='autonomous'&&biliOperations[job.operation as keyof typeof biliOperations].write){
   const [cycle]=await this.db.query('SELECT budget FROM autonomy_cycles WHERE id=$1 AND bot_id=$2 AND tenant_id=$3',[job.work_id,job.bot_id,job.tenant_id]);
   if(cycle?.budget?.qqExternalRequests?.[job.work_id]?.['bilibili:'+job.id])await qqExternalBudget(this.db,{id:job.bot_id,tenant_id:job.tenant_id},{workId:job.work_id,epoch:job.mode_epoch,logicalActionId:'bilibili:'+job.id,phase:result.receipt.status==='unknown'?'unknown':dispatchStarted===false?'released':'confirmed'});
  }
  return finished;
 }
 async completion(bot:any,authority:'foreground'|'autonomous'='foreground',epoch?:number,consumerWorkId?:string){
  const s=await this.settings(bot.tenant_id,bot.id??bot.bot_id);if(!s.config.enabled)return null;
  const job=await this.rpc(bot,'completion',{authority,epoch,consumerWorkId,settingsRevision:s.revision});
  return job?{...job,workId:job.work_id,scope:job.origin_scope,senderId:job.sender_id,eventId:job.origin_event,objective:job.continuation?.objective??'',revision:job.config_revision}:null;
 }
 async ownedReceipt(job:any,operation:string,objectId:string,aid?:string){return(await this.rpc(job,'receipt',{operation,objectId,aid,accountUid:job.account_uid})).confirmed===true}
 async reconcileBudgets(bot:any){
  // Retry settlement from the original cycle reservations and actual Work
  // receipts. No executor replay and no second accounting/history ledger.
  const entries=await this.db.query(`SELECT c.id,c.epoch,r.key AS request_id,r.value AS request FROM autonomy_cycles c CROSS JOIN LATERAL jsonb_each(COALESCE(c.budget->'modelRequests','{}')) r WHERE c.bot_id=$1 AND c.tenant_id=$2 AND r.value->>'source'='media' AND r.value->>'status' IN ('reserved','unknown')`,[bot.id??bot.bot_id,bot.tenant_id]);
  for(const e of entries){
   const receipt=await this.rpc(bot,'budget_receipt',{id:e.request.effectId});
   if(!receipt?.terminal||receipt.owner!==e.request.owner)continue;
   const tokens=receipt.usage?.providerUsage?.total_tokens;
   await cycleModelBudget(this.db,bot,{workId:e.id,epoch:e.epoch,requestId:e.request_id,source:'media',effectId:e.request.effectId,owner:e.request.owner,phase:receipt.usage?.modelRequestStatus==='released'?'released':Number.isSafeInteger(tokens)&&tokens>=0?'settled':'unknown',...(Number.isSafeInteger(tokens)&&tokens>=0?{tokens}:{})});
  }
  const external=await this.db.query(`SELECT c.id,c.epoch,r.key AS logical_id FROM autonomy_cycles c CROSS JOIN LATERAL jsonb_each(COALESCE(c.budget->'qqExternalRequests','{}')) w CROSS JOIN LATERAL jsonb_each(w.value) r WHERE c.bot_id=$1 AND c.tenant_id=$2 AND w.key=c.id AND r.key LIKE 'bilibili:%' AND r.value->>'status' IN ('reserved','unknown')`,[bot.id??bot.bot_id,bot.tenant_id]);
  for(const e of external){
   const receipt=await this.rpc(bot,'budget_receipt',{id:e.logical_id.slice('bilibili:'.length)});
   if(!receipt?.terminal)continue;
   await qqExternalBudget(this.db,bot,{workId:e.id,epoch:e.epoch,logicalActionId:e.logical_id,phase:!receipt.dispatched?'released':receipt.receipt?.status==='unknown'?'unknown':'confirmed'});
  }
 }
 async acknowledgeCompletion(bot:any,id:string,owner:string,status:'done'|'unknown'|'pending',consumerWorkId?:string){return this.rpc(bot,'ack',{id,owner,status,consumerWorkId})}
}
