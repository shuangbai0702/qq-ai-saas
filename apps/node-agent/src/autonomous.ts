import {randomUUID} from 'node:crypto';
import type {Sql} from '../../../packages/database/index.js';
import {AutonomyRepository,AutonomyConflict} from '../../../packages/database/autonomous.js';
import {AutonomyConfigSchema,DecisionEnvelopeSchema,decisionJsonSchema,OrientationSchema,orientationJsonSchema,type InternalWork} from '../../../packages/contracts/autonomous.js';
import {runtimeStatus} from '../../../packages/shared/runtime-status.js';

type Usage={tokens:number;modelCalls:number;toolCalls:number;externalActions:number;conservative?:boolean};
type RuntimeCall=(bot:any,operation:string,body:unknown,timeoutMs?:number)=>Promise<any>;
const emptyUsage=():Usage=>({tokens:0,modelCalls:0,toolCalls:0,externalActions:0});
function online(bot:any){return bot.desired_state==='running'&&runtimeStatus(bot).online}
function cycleTokens(claim:any):number{return claim.tokenLimit??claim.config.cycleTokenBudget}

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

/** One shared scheduler. PostgreSQL owns clocks and work; Python owns execution. */
export class AutonomyCoordinator {
 readonly repo:AutonomyRepository;readonly owner='autonomy-'+randomUUID();
 timer?:NodeJS.Timeout;heartbeatTimer?:NodeJS.Timeout;heartbeatTask?:Promise<void>;closed=false;busy=false;active?:{claim:any;actionId?:string;feedbackVersion?:number;task:Promise<void>};
 private policyCursor=0;
 private policySerial=0;
 private events=new Map<string,number>();private sampled=new Map<string,number>();
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
  const snapshot=await this.call(bot,'snapshot',{},3000);
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
     if(!installed?.pendingHandoffs&&Date.now()-(this.sampled.get(bot.bot_id)||0)<30000)return;
     this.sampled.set(bot.bot_id,Date.now());
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
    await this.repo.finish(claim,{tokens:cycleTokens(claim),modelCalls:claim.config.cycleModelCalls,toolCalls:0,externalActions:0},'runtime_unavailable',30).catch(()=>{});
   }).finally(()=>{if(this.active===active)this.active=undefined});
  }finally{this.busy=false}
 }
 private deadline(claim:any,usage:Usage){
  // A finite identity horizon covers all remaining bounded steps. Runtime
  // inactivity is checked between actual model/tool results, not cycle age.
  const steps=Math.max(1,claim.config.cycleModelCalls-usage.modelCalls+claim.config.cycleToolCalls-usage.toolCalls+1);
  return Date.now()+claim.config.cycleSeconds*1000*steps;
 }
 private work(active:{claim:any;feedbackVersion?:number},usage:Usage,id:string,deadline:number,reserveModelCalls=0):InternalWork{
  const c=active.claim;return{id,tenantId:c.tenant_id,botId:c.bot_id,epoch:c.epoch,generation:c.generation,revision:c.revision,cycleId:c.cycleId,principal:'autonomous-service',feedbackVersion:active.feedbackVersion||0,expiresAt:deadline,
   budget:{modelCalls:Math.max(0,c.config.cycleModelCalls-usage.modelCalls-reserveModelCalls),toolCalls:Math.max(0,c.config.cycleToolCalls-usage.toolCalls),tokens:Math.max(0,cycleTokens(c)-usage.tokens),seconds:c.config.cycleSeconds,externalActions:Math.max(0,c.config.cycleExternalActions-usage.externalActions)}};
 }
 private charge(usage:Usage,result:any){
  if(!result.usage)throw new Error('missing_model_usage');
  for(const name of ['tokens','modelCalls','toolCalls'] as const){const value=result.usage[name];if(!Number.isSafeInteger(value)||value<0)throw new Error('invalid_model_usage');usage[name]+=value}
  const effects=(result.effects||[]).filter((e:any)=>e.status==='confirmed'||e.status==='unknown');
  // Versioned results preserve historical physical accounting. A new chat
  // action owns its OutputPlan; every bubble still has a separate receipt.
  usage.externalActions+=result.actionUnitVersion===2?new Set(effects.map((e:any)=>e.logicalActionId||e.id)).size:effects.length;
  if(result.usage.estimated)usage.conservative=true;
 }
 private async think(claim:any,body:unknown,timeout:number,usage:Usage){
  try{const result=await this.call(claim,'think',body,timeout);this.charge(usage,result);return result}
  catch(error){usage.tokens=Math.max(usage.tokens,cycleTokens(claim));usage.conservative=true;throw error}
 }
 private async cycle(active:{claim:any;actionId?:string;feedbackVersion?:number}){
  const c=active.claim,config=c.config,tokenBudget=cycleTokens(c),usage=emptyUsage();let deadline=this.deadline(c,usage);
  let feedback:any=null,feedbackActionId:string|undefined,orientation:any=null,reason='cycle_interrupted',sleep=15,orientationRepairs=0,decisionRepairs=0,settled=false;
  // With an explicitly minimal two-call budget, perform one task and let the
  // next durable cycle reassess its recorded result rather than starve work.
  const reserveModelCalls=config.cycleModelCalls>=3?1:0;
  try{
   while(!this.closed&&usage.modelCalls<config.cycleModelCalls&&usage.tokens<tokenBudget){
    if(!await this.repo.renew(c)){reason='ownership_changed';break}
    deadline=this.deadline(c,usage);
    if(active.actionId)feedbackActionId=active.actionId;
    active.actionId=undefined;await this.call(c,'policy',await this.policy(c,c.cycleId),2000);
    const fresh=await this.freshSnapshot(c);
    if(fresh.foregroundBusy){reason='foreground_work_busy';sleep=15;break}
    active.feedbackVersion=Number(fresh.feedbackVersion||0);
    const view=await this.repo.view(c.tenant_id,c.bot_id);
    c.observation=view.settings.observation;
    const latestResult=view.actions.filter(a=>a.result&&(a.status!=='canceled'||a.result.usage||(a.result.effects||[]).length)).sort((a,b)=>new Date(b.finished_at||b.created_at).getTime()-new Date(a.finished_at||a.created_at).getTime())[0];
    if(latestResult){feedback=latestResult.result;feedbackActionId=latestResult.id;}
    const relevant=[...view.goals.filter(g=>['active','paused','paused_user','paused_mode'].includes(g.status)),...view.goals.filter(g=>['completed','abandoned'].includes(g.status)).slice(0,3)];
    const goals=relevant.map(g=>({id:g.id,status:g.status,title:g.title,origin:g.origin,commitment:['owner_request','contact_request'].includes(g.origin)?'delegated':'self_directed',source:g.source,progress:g.progress.slice(0,400),reason:g.reason.slice(0,160),successCondition:g.success_condition.slice(0,200)}));
    // The current result is already in feedback. Repeating it in recentActions
    // inflated every reassessment and could exhaust a default cycle before
    // the outer model could choose sleep after a successful task.
    const recentActions=view.actions.filter(a=>a.id!==feedbackActionId).slice(0,4).map(a=>({id:a.id,epoch:a.epoch,goalId:a.goal_id,sourceEventId:view.goals.find(g=>g.id===a.goal_id)?.source?.eventId,at:a.finished_at||a.created_at,kind:a.kind,target:a.target,objective:a.objective.slice(0,250),status:a.status,result:taskResultFacts(a.kind,a.result)}));
    const feedbackAction=view.actions.find(a=>a.id===feedbackActionId),feedbackKind=feedbackAction?.kind||'research';
    const resultSnapshot=feedback?{status:feedback.status,task:feedbackAction?{actionId:feedbackAction.id,goalId:feedbackAction.goal_id,kind:feedbackAction.kind,target:feedbackAction.target,objective:feedbackAction.objective.slice(0,500),finishedAt:feedbackAction.finished_at,epoch:feedbackAction.epoch}:undefined,...taskResultFacts(feedbackKind,feedback,900),evidence:(feedback.evidence||[]).slice(0,6),evidenceLimits:(feedback.evidenceLimits||[]).slice(0,6),unfinished:(feedback.unfinished||[]).slice(0,6),toolResults:(feedback.toolResults||[]).slice(-16),checkpoints:(feedback.checkpoints||[]).slice(-4),shadow:feedback.shadow}:null;
    const cycleStarted=view.cycles.find(cycle=>cycle.id===c.cycleId)?.started_at;
    const activity=config.fatigueEnabled?{...await this.repo.activity(c.tenant_id,c.bot_id,config.fatigueWindowMinutes,config),currentCycleSeconds:cycleStarted?Math.max(0,Math.floor((Date.now()-new Date(cycleStarted).getTime())/1000)):null}:undefined;
    const input={decisionSchema:decisionJsonSchema,goalRevision:view.settings.goal_revision,goals,summary:view.settings.summary,feedback:resultSnapshot,recentActions,wakes:c.wakes,recentForeground:view.settings.observation.latestFeedback||view.settings.observation,bilibiliCompletion:view.settings.observation.bilibiliCompletion,...(activity?{autonomousActivity:activity}:{})};
    // Repeated rest reopens intention formation, not the QQ send gate.
    // Energy and idle activation inform the model; neither gates this review.
    if(!orientation&&activity&&usage.modelCalls+3<=config.cycleModelCalls&&(activity.regulation.recentRestDecisions===2||feedback&&['blocked','partial'].includes(feedback.status))){
     const body={phase:'orientation',decisionSchema:orientationJsonSchema,goals:goals.filter(g=>g.status==='active'),recentForeground:input.recentForeground,feedback:resultSnapshot,recentActivity:recentActions,autonomousActivity:activity};
     let result=await this.think(c,{...body,work:this.work(active,usage,randomUUID(),deadline)},Math.max(1000,deadline-Date.now()+2000),usage);
     let checked=OrientationSchema.safeParse(result.decision);
     if((result.reason==='invalid_decision_json'||result.status==='completed'&&!checked.success)&&orientationRepairs<1&&usage.modelCalls<config.cycleModelCalls&&Date.now()<deadline){
      orientationRepairs++;result=await this.think(c,{...body,validationErrors:checked.success?[]:checked.error.issues.map(issue=>({path:issue.path.join('.'),code:issue.code,message:issue.message.slice(0,400)})).slice(0,8),repair:true,work:this.work(active,usage,randomUUID(),deadline)},Math.max(1000,deadline-Date.now()+2000),usage);checked=OrientationSchema.safeParse(result.decision);
     }
     if(result.status!=='completed'){reason=result.reason||result.status;break}
     if(!checked.success)throw checked.error;
     const current=await this.freshSnapshot(c);
     if(current.foregroundBusy||Number(current.feedbackVersion||0)!==active.feedbackVersion){reason='foreground_feedback_changed';sleep=15;break}
     orientation={...checked.data,at:Date.now(),regulation:activity.regulation};
     await this.repo.recordOrientation(c,orientation,usage);
    }
    if(usage.modelCalls>=config.cycleModelCalls||usage.tokens>=tokenBudget){reason='budget_exhausted';break}
    if(orientation)Object.assign(input,{orientation,summary:''});
    const currentSchema=DecisionEnvelopeSchema.superRefine((value,ctx)=>{
     if(value.expectedGoalRevision!==view.settings.goal_revision)ctx.addIssue({code:'custom',path:['expectedGoalRevision'],message:'必须等于本轮真实目标版本'});
     if(!value.action?.goalId)return;
     const goal=goals.find(g=>g.id===value.action!.goalId),change=value.goalChanges.find(g=>g.id===value.action!.goalId);
     const status=change?({resume:'active',update:goal?.status,pause:'paused',complete:'completed',abandon:'abandoned'} as Record<string,string|undefined>)[change.operation]:goal?.status;
     if(status!=='active')ctx.addIssue({code:'custom',path:['action','goalId'],message:'只能使用已有且本轮仍活跃的真实目标ID；创建时省略ID'});
    });
    let decision=await this.think(c,{...input,work:this.work(active,usage,randomUUID(),deadline)},Math.max(1000,deadline-Date.now()+2000),usage);
    let checked=currentSchema.safeParse(decision.decision);
    if((decision.reason==='invalid_decision_json'||decision.status==='completed'&&!checked.success)&&decisionRepairs<1&&usage.modelCalls<config.cycleModelCalls&&Date.now()<deadline){
     decisionRepairs++;
     const validationErrors=checked.success?[]:checked.error.issues.map(issue=>({path:issue.path.join('.'),code:issue.code,message:issue.message.slice(0,400)})).slice(0,8);
     decision=await this.think(c,{...input,validationErrors,repair:true,work:this.work(active,usage,randomUUID(),deadline)},Math.max(1000,deadline-Date.now()+2000),usage);
     checked=currentSchema.safeParse(decision.decision);
    }
    if(decision.status!=='completed'){reason=decision.reason||decision.status;break}
    if(!checked.success)throw checked.error;
    const latest=await this.freshSnapshot(c);
    if(latest.foregroundBusy||Number(latest.feedbackVersion||0)!==active.feedbackVersion){reason='foreground_feedback_changed';sleep=15;break}
    const parsed=checked.data;
    const committed=await this.repo.commitDecision(c,parsed,usage);
    if(!committed.action){
     settled=true;reason='sleep';
     sleep=Math.max(config.minSleepSeconds,Math.min(config.maxSleepSeconds,parsed.wait.seconds||config.minSleepSeconds));
     break;
    }
    const action=committed.action;
    if(usage.modelCalls+reserveModelCalls>=config.cycleModelCalls||usage.tokens>=tokenBudget){await this.repo.cancelPlanned(c,action.id,'周期预算不足，保留目标到下次重新评估');reason='budget_exhausted';break}
    if(action.kind!=='research'&&usage.externalActions>=config.cycleExternalActions){await this.repo.cancelPlanned(c,action.id,'本周期对外动作预算已用完');reason='external_action_budget';break}
    if(!await this.repo.markExecuting(c,action.id)){await this.repo.cancelPlanned(c,action.id,'身份已变化，或该对象有未确认的历史动作');reason='action_invalidated';break}
    active.actionId=action.id;await this.call(c,'policy',await this.policy(c,c.cycleId,action.id),2000);
    // Source comes from the durable accepted goal, never the model action.
    const source=committed.source;
    const task={...action,sourceScope:source?.scope,outerDecision:{at:committed.decidedAt,reason:parsed.reason.slice(0,600),cycleId:c.cycleId},...(action.kind==='message'&&source&&source.handoffId===action.goalId&&source.scope===action.target?{requestSource:source}:{})};
    // Keep one request for the outer to assess the actual result. An inner
    // tool loop must not consume the entire shared decision allowance.
    deadline=this.deadline(c,usage);
    try{feedback=await this.call(c,'execute',{work:this.work(active,usage,action.id,deadline,reserveModelCalls),action:task,feedback},Math.max(1000,deadline-Date.now()+2000))}
    catch{
     await this.call(c,'cancel',{},2000).catch(()=>{});
     const receipt=await this.call(c,'receipt',{id:action.id},2000).catch(()=>null);
     feedback=receipt?.result||{status:'unknown',summary:'执行连接中断，未得到完整结果；不得重复投递',effects:receipt?.effects||[],evidence:[],unfinished:[action.objective]};
     if(!feedback.usage)feedback.usage={tokens:Math.max(0,tokenBudget-usage.tokens),modelCalls:Math.max(0,config.cycleModelCalls-usage.modelCalls),toolCalls:0,estimated:true};
    }
    this.charge(usage,feedback);await this.repo.feedback(c,action.id,feedback);
    // Every meaningful task returns here. The next model decision sees actual
    // feedback, not an eagerly queued list of tasks. Uncertain effects stop
    // this cycle and remain blocked until they have authoritative resolution.
    if(feedback.status==='unknown'||feedback.status==='partial'&&(feedback.effects||[]).some((effect:any)=>effect.status!=='confirmed')){reason='waiting_receipt';break}
    if(feedback.status==='canceled'){reason='foreground_or_mode_changed';sleep=15;break}
    if(['blocked','partial','budget_exhausted'].includes(feedback.status))orientation=null;
    if(await this.repo.otherDue(c)){reason='fair_yield';sleep=1;break}
   }
   if(!settled&&(usage.modelCalls>=config.cycleModelCalls||usage.tokens>=tokenBudget))reason='budget_exhausted';
   // A bounded computation slice yields; only a committed model choice sleeps.
   if(!settled&&['budget_exhausted','external_action_budget'].includes(reason))sleep=15;
  }catch(error){
   // Unknown model work was charged at its IPC boundary. A rejected decision
   // with returned usage must retain the known cost, not invent a full bill.
   const issues=(error as any)?.issues;
   reason=Array.isArray(issues)?'decision_contract:'+issues.slice(0,4).map((issue:any)=>issue.path.join('.')+':'+issue.code).join(','):
    error instanceof AutonomyConflict?'decision_conflict':String((error as any)?.message||'runtime_or_decision_failed').match(/^autonomous_ipc_\d{3}$|^missing_model_usage$|^invalid_model_usage$|^runtime_identity_changed$/)?.[0]||'runtime_or_decision_failed';
   // A failed request is an explicit error backoff, never a model rest choice.
   sleep=30;
  }finally{
   await this.repo.finish(c,usage,reason,sleep);
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
  let url:string,token:string,groupGeneration:number|undefined;
  if(current.runtime_mode==='shared'){
   const [g]=await db.query('SELECT g.* FROM runtime_groups g JOIN group_members m ON m.group_id=g.id WHERE m.bot_id=$1',[bot.bot_id]);if(!g)throw new Error('runtime_group_missing');
   url=`http://127.0.0.1:${g.health_port}/members/${bot.bot_id}/autonomous/${operation}`;token=g.token;groupGeneration=g.generation;
  }else{
   const [i]=await db.query('SELECT ai_port,token FROM instances WHERE bot_id=$1',[bot.bot_id]);if(!i)throw new Error('runtime_instance_missing');
   url=`http://127.0.0.1:${i.ai_port+1}/autonomous/${operation}`;token=i.token;
  }
  const headers:Record<string,string>={Authorization:'Bearer '+token,'Content-Type':'application/json','X-Member-Generation':String(bot.generation),'X-Member-Revision':String(bot.revision)};
  if(groupGeneration!==undefined)headers['X-Group-Generation']=String(groupGeneration);
  const response=await fetch(url,{method:'POST',headers,body:JSON.stringify(body),signal:AbortSignal.timeout(timeoutMs)});
  if(!response.ok)throw new Error('autonomous_ipc_'+response.status);return response.json();
 };
}
