import {runtimeIpc} from '../../../packages/shared/runtime-ipc.js';
import {externalCount} from '../../../packages/database/external-action-budget.js';
import {randomUUID} from 'node:crypto';
import type {Sql} from '../../../packages/database/index.js';
import {AutonomyRepository,AutonomyConflict} from '../../../packages/database/autonomous.js';
import {AutonomyConfigSchema,type InternalWork} from '../../../packages/contracts/autonomous.js';
import {runtimeStatus} from '../../../packages/shared/runtime-status.js';

/** Usage reconciliation classes. A charge records where its numbers came
 * from: an actual reported usage, an estimate the inner runtime returned, or
 * an unknown request whose reserved ceiling was applied instead of zero.
 * Repeated results for the same request never charge twice. */
export type UsageCharge={requestId:string;kind:string;source:'actual'|'estimated'|'unknown';estimateReason?:'execution_outcome_unknown'|'missing_model_usage'|'invalid_model_usage';tokens:number;modelCalls:number;toolCalls:number;externalActions:number;reported:{tokens:boolean;modelCalls:boolean;toolCalls:boolean}};
type Usage={tokens:number;modelCalls:number;toolCalls:number;externalActions:number;conservative?:boolean;charges?:UsageCharge[]};
type RuntimeCall=(bot:any,operation:string,body:unknown,timeoutMs?:number)=>Promise<any>;
const emptyUsage=():Usage=>({tokens:0,modelCalls:0,toolCalls:0,externalActions:0});
const notStarted=(objective:string)=>({status:'canceled',stopReason:'not_started',summary:'目标资料读取或执行前准备失败，尚未派发；保留原任务责任供合法重试',effects:[],evidence:[],unfinished:[objective],continuationAccepted:false,usage:emptyUsage()});
function online(bot:any){return runtimeStatus(bot).online}
function cycleTokens(claim:any):number{return claim.tokenLimit??claim.config.cycleTokenBudget}

export type FaultClass='auth'|'rate_limit'|'protocol'|'network'|'contract'|'identity'|'decision'|'unknown';
/** Deterministic failure classes stay separated so one broken account never
 * reads as model idleness; fault details stay with their cycle ledger. */
export function classifyFault(error:unknown):{reason:string;faultClass:FaultClass}{
 const issues=(error as any)?.issues;
 if(Array.isArray(issues))return{reason:'decision_contract:'+issues.slice(0,4).map((issue:any)=>issue.path.join('.')+':'+issue.code).join(','),faultClass:'decision'};
 if(error instanceof AutonomyConflict)return{reason:'decision_conflict',faultClass:'decision'};
 const message=String((error as any)?.message||error);
 const ipc=/^autonomous_ipc_(\d{3})$/.exec(message);
 if(ipc){const status=Number(ipc[1]);return{reason:message,faultClass:status===401||status===403?'auth':status===429?'rate_limit':'protocol'}}
 if(message==='missing_model_usage'||message==='invalid_model_usage')return{reason:message,faultClass:'contract'};
 if(message==='runtime_identity_changed')return{reason:message,faultClass:'identity'};
 if(/TimeoutError|AbortError/i.test(String((error as any)?.name||''))||/timeout|fetch failed|ECONN|ENOTFOUND|EAI_AGAIN|socket hang up|network/i.test(message))return{reason:'runtime_network_failed',faultClass:'network'};
 return{reason:'runtime_or_decision_failed',faultClass:'unknown'};
}

/** A spoken sentence is task content, not an authoritative transport result.
 * Project old and current records consistently without rewriting the ledger.
 */
export function taskResultFacts(kind:string,result:any,limit=350){
 if(!result)return null;
 const effects=Array.isArray(result.effects)?result.effects:[];
 const confirmed=effects.filter((e:any)=>e.status==='confirmed').length,unknown=effects.filter((e:any)=>e.status==='unknown').length;
 const summary=kind==='message'?`协议投递：确认${confirmed}，不明${unknown}；状态${result.status}。是否完成任务需核对消息内容。`:String(result.summary||'');
 const response=kind==='message'?String(result.response??(confirmed?result.summary||'':'' )).slice(0,limit):undefined;
 const reason=kind==='message'&&!effects.length?String(result.summary||'').slice(0,200):undefined;
 return{summary:summary.slice(0,limit),response,reason,stopReason:String(result.stopReason||'').slice(0,120),effects,contextScope:result.contextScope};
}

/** One shared scheduler. PostgreSQL owns clocks and work; Python owns execution.
 * Only coalesced service wakes activate the main AI; Goal semantics never
 * select an execution plan or dispatch a message from this scheduler. */
export class AutonomyCoordinator {
 readonly repo:AutonomyRepository;readonly owner='autonomy-'+randomUUID();
 timer?:NodeJS.Timeout;heartbeatTimer?:NodeJS.Timeout;heartbeatTask?:Promise<void>;closed=false;busy=false;active?:{claim:any;actionId?:string;feedbackVersion?:number;task:Promise<void>};
 private policyCursor=0;
 private policySerial=0;
 private events=new Map<string,number>();private sampled=new Map<string,{at:number;revision:number}>();
 constructor(public db:Sql,private call:RuntimeCall){this.repo=new AutonomyRepository(db)}
 start(){if(this.timer||this.closed)return;this.timer=setInterval(()=>void this.tick().catch(()=>{}),5000);this.timer.unref();this.heartbeatTimer=setInterval(()=>void this.heartbeat(),4000);this.heartbeatTimer.unref();void this.tick().catch(()=>{})}
 async heartbeat(){
  if(this.closed||this.heartbeatTask||!this.active)return;
  const active=this.active;
  const task=(async()=>{if(await this.repo.renew(active.claim)&&this.active===active&&!this.closed)await this.call(active.claim,'policy',await this.policy(active.claim,active.claim.cycleId,active.actionId),2000)})().catch(()=>{});
  this.heartbeatTask=task;try{await task}finally{if(this.heartbeatTask===task)this.heartbeatTask=undefined}
 }
 async stop(){this.closed=true;if(this.timer)clearInterval(this.timer);if(this.heartbeatTimer)clearInterval(this.heartbeatTimer);this.timer=undefined;this.heartbeatTimer=undefined;await this.heartbeatTask;while(this.busy)await new Promise(resolve=>setTimeout(resolve,10));const active=this.active;if(active){await this.call(active.claim,'cancel',{},2000).catch(()=>{});await active.task.catch(()=>{})}}
 private async policy(bot:any,cycleId='',actionId=''){
  const issuedAt=this.policySerial=Math.max(Date.now(),this.policySerial+1);
  const experience=await this.repo.experience(bot.tenant_id,bot.bot_id);
  return{tenantId:bot.tenant_id,botId:bot.bot_id,epoch:bot.epoch,generation:bot.generation,revision:bot.revision,config:AutonomyConfigSchema.parse(bot.config),observedThrough:Number(bot.observation?.at||0),latestFeedback:bot.observation?.latestFeedback||(bot.observation?.phase==='foreground_result'?bot.observation:undefined),experience,issuedAt,until:Date.now()+14000,cycleId,actionId};
 }
 private async freshSnapshot(bot:any){
  const snapshot=await this.call(bot,'snapshot',{ledgerOnly:true},3000);
  await this.repo.reconcileCancellations(bot,Array.isArray(snapshot.canceledHandoffs)?snapshot.canceledHandoffs:[]);
  await this.repo.reconcileAgreements(bot,Array.isArray(snapshot.agreementStates)?snapshot.agreementStates:[]);
  for(const request of (Array.isArray(snapshot.pendingHandoffs)?snapshot.pendingHandoffs:[]).slice(0,16)){
   const receipt=await this.repo.acceptHandoff(bot,request);
   await this.call(bot,'handoff_ack',{epoch:bot.epoch,id:request.id,receipt},2000);
  }
  await this.repo.latestFeedback(bot,snapshot.foregroundFeedback||(snapshot.event?.phase==='foreground_result'?snapshot.event:null));
  const at=Number(snapshot.event?.at||0);
  if(at>(this.events.get(bot.bot_id)||0)){
   await this.repo.event(bot,at,snapshot.event,Number(snapshot.nextEventCheckSeconds||bot.config.minSleepSeconds));
   this.events.set(bot.bot_id,at);
  }
  return snapshot;
 }
 async tick(){
  if(this.closed||this.busy)return;this.busy=true;
  try{
   await this.repo.suspendOffline();
   const all=await this.repo.policies();
   const owner=all.find(bot=>bot.bot_id===this.active?.claim.bot_id);
   const others=all.filter(bot=>bot!==owner);
   const policies=owner?[owner]:[];
   for(let n=0;n<Math.min(8,others.length);n++)policies.push(others[(this.policyCursor+n)%others.length]);
   if(others.length)this.policyCursor=(this.policyCursor+8)%others.length;
   // Slow/offline members do not monopolize renewal or discovery of due work.
   // A single process-wide heartbeat renews in-flight work independently.
   await Promise.all(policies.map(async bot=>{
    if(this.closed)return;
    if(!online(bot)){if(bot.config.mode==='passive')await this.repo.applied(bot.tenant_id,bot.bot_id,bot.epoch);return}
    const owned=this.active?.claim.bot_id===bot.bot_id?this.active:undefined;
    const current=owned&&owned.claim.epoch===bot.epoch&&owned.claim.generation===bot.generation&&owned.claim.revision===bot.revision&&online(bot)&&await this.repo.renew(owned.claim);
    try{
     const installed=await this.call(bot,'policy',await this.policy(bot,current?owned.claim.cycleId:'',current?owned.actionId:''),2000);
     await this.repo.applied(bot.tenant_id,bot.bot_id,bot.epoch);
     const sampled=this.sampled.get(bot.bot_id),revisionChanged=sampled?.revision!==bot.revision;
     if(!revisionChanged&&!installed?.pendingHandoffs&&Date.now()-(sampled?.at||0)<30000)return;
     // A config revision may re-enable model-origin work whose disabled wake
     // was already consumed. Reuse discovery sampling, not a new polling loop.
     if(revisionChanged&&bot.config.mode==='active')await this.repo.rearmHandoffs(bot.tenant_id,bot.bot_id,bot.epoch);
     this.sampled.set(bot.bot_id,{at:Date.now(),revision:bot.revision});
     for(const pending of await this.repo.unconfirmed(bot.tenant_id,bot.bot_id)){
      const receipt=await this.call(bot,'receipt',{id:pending.id},4000).catch(()=>null);
      if(receipt?.result&&receipt.result.status!=='unknown')await this.repo.feedback({...bot,cycleId:pending.cycle_id},pending.id,receipt.result);
      else if(receipt&&receipt.active===false&&pending.kind==='research'&&!(receipt.effects||[]).length)await this.repo.feedback({...bot,cycleId:pending.cycle_id},pending.id,{...pending.result,status:'blocked',summary:'只读任务已不在执行；没有对外副作用，结束等待并重新评估。',stopReason:'read_execution_interrupted',effects:[],unfinished:[pending.objective]});
      else await this.repo.receiptChecked(bot,pending);
     }
     // Querying an existing receipt grants no new effect. Keep settling old
     // actions after a mode switch, without waking a model or restarting QQ.
     if(bot.config.mode!=='active')return;
     await this.freshSnapshot(bot);
     await this.repo.scheduleActivity(bot);
    }catch{
     // No fresh permit means no effects, even if a model call is still running.
     if(bot.config.mode==='passive'&&!online(bot))await this.repo.applied(bot.tenant_id,bot.bot_id,bot.epoch);
    }
   }));
   if(this.active||this.closed)return;
   const claim=await this.repo.claim(this.owner);if(!claim)return;
   const active={claim,actionId:undefined as string|undefined,task:Promise.resolve()};this.active=active;
   active.task=this.cycle(active).catch(async()=>{
    // Unreturned model usage is deliberately charged at the reserved ceiling.
    // Due handoff work keeps a wake so a later cycle can rediscover it.
    await this.repo.wake(claim.tenant_id,claim.bot_id,claim.epoch,'handoff-due',30,'').catch(()=>{});
    await this.repo.finish(claim,{tokens:cycleTokens(claim),modelCalls:claim.config.cycleModelCalls,toolCalls:0,externalActions:0},'runtime_unavailable').catch(()=>{});
   }).finally(()=>{if(this.active===active)this.active=undefined});
  }finally{this.busy=false}
 }
 private deadline(claim:any,usage:Usage){
  // A finite identity horizon covers all remaining bounded steps. Runtime
  // inactivity is checked between actual model/tool results, not cycle age.
  const steps=Math.max(1,claim.config.cycleModelCalls-usage.modelCalls+claim.config.cycleToolCalls-usage.toolCalls+1);
  return Date.now()+claim.config.cycleSeconds*1000*steps;
 }
 private work(active:{claim:any;feedbackVersion?:number},usage:Usage,id:string,deadline:number):InternalWork{
  const c=active.claim;return{id,tenantId:c.tenant_id,botId:c.bot_id,epoch:c.epoch,generation:c.generation,revision:c.revision,cycleId:c.cycleId,principal:'autonomous-service',feedbackVersion:active.feedbackVersion||0,expiresAt:deadline,
   budget:{modelCalls:Math.max(0,c.config.cycleModelCalls-usage.modelCalls),toolCalls:Math.max(0,c.config.cycleToolCalls-usage.toolCalls),tokens:Math.max(0,cycleTokens(c)-usage.tokens),seconds:c.config.cycleSeconds,externalActions:Math.max(0,c.config.cycleExternalActions-usage.externalActions)}};
 }
 private charge(usage:Usage,requestId:string,kind:string,result:any){
  // Idempotent per request: a repeated result (retry racing a receipt, a
  // re-delivered feedback) must not bill the same consumption twice.
  if(usage.charges?.some(c=>c.requestId===requestId&&c.kind===kind))return;
  const record=(source:UsageCharge['source'],values:Omit<UsageCharge,'requestId'|'kind'|'source'>)=>{
   usage.charges=[...(usage.charges||[]),{requestId,kind,source,...values}].slice(-64)};
  const raw=result?.usage;
  const reported=(name:string)=>Boolean(raw&&Number.isSafeInteger(raw[name])&&raw[name]>=0);
  if(!raw){
   record('unknown',{tokens:0,modelCalls:0,toolCalls:0,externalActions:0,reported:{tokens:false,modelCalls:false,toolCalls:false}});
   throw new Error('missing_model_usage');
  }
  for(const name of ['tokens','modelCalls','toolCalls'] as const){const value=raw[name];if(!Number.isSafeInteger(value)||value<0)throw new Error('invalid_model_usage')}
  const effects=(result.effects||[]).filter((e:any)=>e.status==='confirmed'||e.status==='unknown');
  // Versioned results preserve historical physical accounting. A new chat
  // action owns its OutputPlan; every bubble still has a separate receipt.
  const externalActions=result.actionUnitVersion===2?new Set(effects.map((e:any)=>e.logicalActionId||e.id)).size:effects.length;
  for(const name of ['tokens','modelCalls','toolCalls'] as const)usage[name]+=raw[name];
  usage.externalActions+=externalActions;
  const source:UsageCharge['source']=raw.estimated?'estimated':'actual';
  if(source==='estimated')usage.conservative=true;
  const estimateReason=source==='estimated'&&['execution_outcome_unknown','missing_model_usage','invalid_model_usage'].includes(raw.estimateReason)?raw.estimateReason:undefined;
  record(source,{...(estimateReason?{estimateReason}:{}),tokens:raw.tokens,modelCalls:raw.modelCalls,toolCalls:raw.toolCalls,externalActions,reported:{tokens:reported('tokens'),modelCalls:reported('modelCalls'),toolCalls:reported('toolCalls')}});
 }
 /** Wake only; existing transport owners still settle in-flight receipts and budgets. */
 private async cycle(active:{claim:any;actionId?:string;feedbackVersion?:number}){
  const c=active.claim,config=c.config,tokenBudget=cycleTokens(c),usage=emptyUsage();let deadline=this.deadline(c,usage);
  let reason='no_due_work',sleep=0,feedback:any=null,faultContext:any=undefined;
  try{
   while(!this.closed){
    if(!await this.repo.renew(c)){reason='ownership_changed';break}
    deadline=this.deadline(c,usage);
    active.actionId=undefined;await this.call(c,'policy',await this.policy(c,c.cycleId),2000);
    const fresh=await this.freshSnapshot(c);
    if(fresh.foregroundBusy){reason='foreground_work_busy';sleep=15;break}
    active.feedbackVersion=Number(fresh.feedbackVersion||0);
    const continuation=await this.repo.planToolContinuation(c,usage);
    if(continuation){
     if(!await this.repo.markExecuting(c,continuation.id)){await this.repo.settleToolContinuation(c,continuation,'pending');reason='continuation_invalidated';break}
     active.actionId=continuation.id;
     const work=this.work(active,usage,continuation.id,deadline);
     let result:any,executeStarted=false;
     try{
      await this.call(c,'policy',await this.policy(c,c.cycleId,continuation.id),2000);
      executeStarted=true;result=await this.call(c,'execute',{work,action:{...continuation}},Math.max(1000,deadline-Date.now()+2000));
      await this.repo.settleToolContinuation(c,continuation,result.continuationAccepted===false?'pending':'done');
     }catch{
      if(!executeStarted){
       result=notStarted(continuation.objective);
       await this.repo.settleToolContinuation(c,continuation,'pending');
       await this.repo.wake(c.tenant_id,c.bot_id,c.epoch,'tool-completed',30,continuation.continuation.id);
      }else{
      await this.call(c,'cancel',{},2000).catch(()=>{});
      const receipt=await this.call(c,'receipt',{id:continuation.id},2000).catch(()=>null);
      // A durable original receipt may settle the race. An absent receipt does
      // not prove no model ran; retain unknown instead of replaying the effect.
      result=receipt?.result||{status:'unknown',summary:'异步结果续接回执未知；保留原结果和责任，不重复派发',effects:receipt?.effects||[],evidence:[],unfinished:[continuation.objective],usage:{tokens:work.budget.tokens,modelCalls:work.budget.modelCalls,toolCalls:work.budget.toolCalls,estimated:true}};
      await this.repo.settleToolContinuation(c,continuation,receipt?.result?(result.continuationAccepted===false?'pending':'done'):'unknown');
      }
     }
     if(!result.usage||['tokens','modelCalls','toolCalls'].some(name=>!Number.isSafeInteger(result.usage[name])||result.usage[name]<0))result={...result,usage:{tokens:work.budget.tokens,modelCalls:work.budget.modelCalls,toolCalls:work.budget.toolCalls,estimated:true}};
     this.charge(usage,continuation.id,'execute',result);usage.externalActions=await externalCount(this.db,c.cycleId);await this.repo.feedback(c,continuation.id,result);
     reason='tool_continuation_'+result.status;break;
    }
    const trigger=c.wakes.find((w:any)=>(w.reason==='activity'&&config.activityEnabled)||w.reason==='handoff-due'||w.reason==='operator-wake');
    if(trigger&&usage.modelCalls===0){
      const action=await this.repo.planActivity(c,usage);
      if(!await this.repo.markExecuting(c,action.id)){await this.repo.cancelPlanned(c,action.id,'自主时刻身份变化');reason='action_invalidated';break}
      active.actionId=action.id;
      const work=this.work(active,usage,action.id,deadline);
      work.budget.modelCalls=Math.min(work.budget.modelCalls,config.activityModelCalls);
      work.budget.toolCalls=Math.min(work.budget.toolCalls,config.activityToolCalls);
      work.budget.tokens=Math.min(work.budget.tokens,config.activityTokenBudget);
      work.budget.externalActions=Math.min(work.budget.externalActions,config.activityExternalActions);
      let executeStarted=false;
      try{
       await this.call(c,'policy',await this.policy(c,c.cycleId,action.id),2000);
       executeStarted=true;feedback=await this.call(c,'execute',{work,action:{...action,activationTrigger:trigger.reason,autonomousMoment:true,outerDecision:{at:new Date().toISOString(),reason:'autonomous_moment',cycleId:c.cycleId}}},Math.max(1000,deadline-Date.now()+2000))}
      catch(error){
       const fault=classifyFault(error);
       faultContext={class:fault.faultClass,reason:fault.reason,phase:'activity_execute',configRevision:c.revision,epoch:c.epoch,generation:c.generation,at:Date.now()};
       if(!executeStarted)feedback=notStarted(action.objective);
       else{
       await this.call(c,'cancel',{},2000).catch(()=>{});
       const receipt=await this.call(c,'receipt',{id:action.id},2000).catch(()=>null);
       feedback=receipt?.result||{status:'unknown',summary:'自主活动结果未知；保留回执，不重复派发',effects:receipt?.effects||[],evidence:[],unfinished:[],usage:{tokens:work.budget.tokens,modelCalls:work.budget.modelCalls,toolCalls:work.budget.toolCalls,estimated:true,estimateReason:'execution_outcome_unknown'}};
       }
      }
      if(!feedback.usage||['tokens','modelCalls','toolCalls'].some(name=>!Number.isSafeInteger(feedback.usage[name])||feedback.usage[name]<0)){
       const usageReason=feedback.usage?'invalid_model_usage':'missing_model_usage';
       faultContext??={class:'contract',reason:usageReason,phase:'activity_execute',configRevision:c.revision,epoch:c.epoch,generation:c.generation,at:Date.now()};
       feedback={...feedback,usage:{tokens:work.budget.tokens,modelCalls:work.budget.modelCalls,toolCalls:work.budget.toolCalls,estimated:true,estimateReason:usageReason}};
      }
      feedback=await this.repo.withExternalEffects(c,action.id,feedback);
      this.charge(usage,action.id,'execute',feedback);usage.externalActions=await externalCount(this.db,c.cycleId);await this.repo.feedback(c,action.id,feedback);
      reason=faultContext?.reason||(feedback.status==='completed'?'activity_completed':feedback.status==='canceled'?'activity_canceled':'activity_'+feedback.status);
      sleep=Number.isSafeInteger(feedback.restSeconds)?Math.max(300,Math.min(config.maxSleepSeconds,feedback.restSeconds)):config.activityIntervalMinutes*60;
    }
    break;
   }
   // An unknown execution may reserve the full ceiling. That accounting is
   // not evidence that budget exhaustion caused the execution failure.
   if(!faultContext&&(usage.modelCalls>=config.cycleModelCalls||usage.tokens>=tokenBudget)){reason='budget_exhausted';sleep=15}
  }catch(error){
   // Unknown model work was charged at its IPC boundary. A rejected decision
   // with returned usage must retain the known cost, not invent a full bill.
   const fault=classifyFault(error);
   reason=fault.reason;
   // A failed request is an explicit error backoff, never a model rest choice.
   sleep=30;
   faultContext={class:fault.faultClass,configRevision:c.revision,epoch:c.epoch,generation:c.generation,at:Date.now()};
  }finally{
   // Unfinished due goals retain their task wake. Timed activity owns exactly
   // one separate wake, never a backlog or a fast retry/model polling loop.
   if(reason==='foreground_work_busy'||reason==='action_invalidated'||feedback?.continuationAccepted===false||feedback?.stopReason==='not_started')for(const wake of c.wakes.filter((w:any)=>['handoff-due','operator-wake'].includes(w.reason)))await this.repo.wake(c.tenant_id,c.bot_id,c.epoch,wake.reason,Math.max(15,sleep),wake.scope).catch(()=>{});
   if(c.wakes.some((w:any)=>w.reason==='activity'))await this.repo.scheduleActivity(c,Math.max(300,sleep||config.activityIntervalMinutes*60),true).catch(()=>{});
   await this.repo.finish(c,usage,reason,faultContext);
   // Withdraw the short effect permit before releasing the global slot. A
   // failed withdrawal still expires after at most fourteen seconds.
   const [latest]=await this.db.query('SELECT s.*,b.generation,b.revision FROM autonomy_settings s JOIN bots b ON b.id=s.bot_id WHERE s.bot_id=$1 AND s.tenant_id=$2',[c.bot_id,c.tenant_id]);
   if(latest)await this.call(latest,'policy',await this.policy(latest),2000).catch(()=>{});
  }
 }
}

export function runtimeClient(db:Sql):RuntimeCall{
 return async(bot,operation,body,timeoutMs=3000)=>{
  const [current]=await db.query('SELECT runtime_mode,group_id,generation,revision FROM bots WHERE id=$1 AND tenant_id=$2 AND deleted_at IS NULL',[bot.bot_id,bot.tenant_id]);
  if(!current||current.generation!==bot.generation||current.revision!==bot.revision)throw new Error('runtime_identity_changed');
  let port:number,route:string,token:string,groupGeneration:number|undefined;
  if(current.runtime_mode==='shared'){
   const [g]=await db.query('SELECT g.* FROM runtime_groups g JOIN group_members m ON m.group_id=g.id WHERE m.bot_id=$1',[bot.bot_id]);if(!g)throw new Error('runtime_group_missing');
   port=g.health_port;route=`/members/${bot.bot_id}/autonomous/${operation}`;token=g.token;groupGeneration=g.generation;
  }else{
   const [i]=await db.query('SELECT ai_port,token FROM instances WHERE bot_id=$1',[bot.bot_id]);if(!i)throw new Error('runtime_instance_missing');
   port=i.ai_port+1;route=`/autonomous/${operation}`;token=i.token;
  }
  const headers:Record<string,string>={Authorization:'Bearer '+token,'Content-Type':'application/json','X-Member-Generation':String(bot.generation),'X-Member-Revision':String(bot.revision)};
  if(groupGeneration!==undefined)headers['X-Group-Generation']=String(groupGeneration);
  const response=await runtimeIpc(port,route,{method:'POST',headers,body,timeoutMs});
  if(!response.ok)throw new Error('autonomous_ipc_'+response.status);return response.json();
 };
}
