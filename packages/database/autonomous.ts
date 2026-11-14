import {externalCount,biliWorkEffects} from './external-action-budget.js';
import {modelRequestTotals} from './model-request-budget.js';
import {createHash,randomUUID} from 'node:crypto';
import type {Sql} from './index.js';
import {runtimeOnlineSql} from '../shared/runtime-status.js';
import {MAX_CURRENT_GOALS,AutonomyConfigSchema,defaultAutonomyConfig,TaskHandoffSchema,AutonomousGoalAudienceSchema,AutonomousGoalUpdateSchema,type AutonomousGoalAudience} from '../contracts/autonomous.js';

const daySql="to_char(now() AT TIME ZONE 'Asia/Shanghai','YYYY-MM-DD')";
// Match the existing 90-second health lifetime at every durable work boundary.
// A missing timestamp or a far-future report is not evidence of an online QQ.
const onlineSql=runtimeOnlineSql();
export class AutonomyConflict extends Error {}
export class AutonomyRepository {
 constructor(public db:Sql){}
 /** Complete current responsibility, with original intent and result evidence intact. */
 async goalAudience(bot:any,value:unknown,tx=this.db){
  const audience=AutonomousGoalAudienceSchema.parse(value);
  const [current]=await tx.query('SELECT b.generation,b.revision,c.config FROM bots b JOIN bot_config_revisions c ON c.bot_id=b.id AND c.revision=b.revision WHERE b.id=$1 AND b.tenant_id=$2 AND b.deleted_at IS NULL',[bot.id,bot.tenant_id]);
  if(!current||current.generation!==bot.generation||current.revision!==bot.revision)throw new AutonomyConflict('goal_identity_changed');
  const owner=!!current.config?.behavior?.ownerUin&&String(current.config.behavior.ownerUin)===audience.senderId;
  let action:any;
  if(audience.work){
   [action]=await tx.query("SELECT a.*,c.decision AS cycle_decision FROM autonomy_actions a JOIN autonomy_settings s ON s.bot_id=a.bot_id AND s.tenant_id=a.tenant_id AND s.epoch=a.epoch JOIN autonomy_cycles c ON c.id=a.cycle_id AND c.bot_id=a.bot_id AND c.tenant_id=a.tenant_id WHERE a.id=$1 AND a.bot_id=$2 AND a.tenant_id=$3 AND a.epoch=$4 AND a.status='executing' AND a.expires_at>now() AND s.config->>'mode'='active' AND c.generation=$5 AND c.config_revision=$6",[audience.work.id,bot.id,bot.tenant_id,audience.work.epoch,bot.generation,bot.revision]);
   if(!action)throw new AutonomyConflict('goal_work_expired');
  }
  if(audience.scope==='self'&&(!action||audience.senderId!=='0'))throw new AutonomyConflict('goal_internal_identity_required');
  if(!action&&audience.scope.startsWith('private:')&&audience.scope!=='private:'+audience.senderId)throw new AutonomyConflict('goal_scope_mismatch');
  return{...audience,owner,action};
 }
 private goalVisible(goal:any,audience:AutonomousGoalAudience&{owner:boolean;action?:any}){
  return audience.scope==='self'||goal.source?.scope===audience.scope&&(!!audience.action||audience.owner||goal.source?.senderId===audience.senderId)||!!audience.action&&goal.origin==='model'&&(!goal.source?.scope||goal.source.scope==='self');
 }
 async readGoals(bot:any,value:unknown,tx=this.db){
  const audience=await this.goalAudience(bot,value,tx);
  // Goal cognition has one owner. Execution recovery and effect receipts keep
  // their existing lifecycle; reading goals must not replay those histories.
  const current=await tx.query("SELECT g.* FROM autonomy_goals g WHERE g.bot_id=$1 AND g.tenant_id=$2 AND g.status NOT IN ('completed','abandoned','canceled') ORDER BY g.created_at,g.id",[bot.id,bot.tenant_id]);
  return{goals:current.filter(g=>this.goalVisible(g,audience)),complete:true};
 }
 async updateGoal(bot:any,value:unknown,change:unknown){
  const update=AutonomousGoalUpdateSchema.parse(change);
  return this.db.transaction(async tx=>{
   // Same lock order as handoff/configure; all permission and CAS checks use fresh rows.
   const [settings]=await tx.query('SELECT bot_id,epoch FROM autonomy_settings WHERE bot_id=$1 AND tenant_id=$2 FOR UPDATE',[bot.id,bot.tenant_id]);
   const audience=await this.goalAudience(bot,value,tx);
   const [goal]=await tx.query('SELECT * FROM autonomy_goals WHERE id=$1 AND bot_id=$2 AND tenant_id=$3 FOR UPDATE',[update.goalId,bot.id,bot.tenant_id]);
   if(!goal||!this.goalVisible(goal,audience))return{status:'rejected',reason:'goal_not_authorized'};
   if(audience.action&&audience.action.cycle_decision?.reason!=='autonomous_moment'&&goal.origin!=='model'&&audience.action?.goal_id!==goal.id)return{status:'rejected',reason:'goal_source_authority_required',goal};
   if(goal.revision!==update.expectedRevision)return{status:'conflict',reason:'goal_revision_changed',goal};
   if(['completed','abandoned','canceled'].includes(goal.status))return{status:'rejected',reason:'goal_terminal',goal};
   if(update.changes.status&&['paused_user','paused_mode'].includes(goal.status))return{status:'rejected',reason:'goal_control_pause_requires_existing_control',goal};
   let retainedExecutionResponsibility=false;
   if(update.changes.status&&['completed','abandoned'].includes(update.changes.status)){
    const [pending]=await tx.query("SELECT 1 FROM autonomy_actions a WHERE a.goal_id=$1 AND a.bot_id=$2 AND a.tenant_id=$3 AND (a.status IN ('planned','unknown') OR a.status='executing' AND a.id<>$4 OR a.status='partial' AND (a.result->'effects' @> '[{\"status\":\"unknown\"}]'::jsonb OR jsonb_array_length(COALESCE(a.result->'unfinished','[]'::jsonb))>0) OR EXISTS(SELECT 1 FROM tool_effects e WHERE e.work_id=a.id AND e.bot_id=a.bot_id AND e.tenant_id=a.tenant_id AND (e.state IN ('queued','claimed','dispatching','unknown') OR e.completion_state IN ('pending','claimed')))) LIMIT 1",[goal.id,bot.id,bot.tenant_id,audience.action?.id||'']);
    retainedExecutionResponsibility=!!pending;
   }
   const originalGoal=goal.source?.originalGoal||{title:goal.title,reason:goal.reason,successCondition:goal.success_condition,source:goal.source};
   const source={...goal.source,originalGoal,...(update.changes.nextIntention!==undefined?{nextIntention:update.changes.nextIntention}:{})};
   const [updated]=await tx.query('UPDATE autonomy_goals SET title=COALESCE($7,title),reason=COALESCE($8,reason),success_condition=COALESCE($9,success_condition),source=$10,status=COALESCE($5,status),progress=COALESCE($6,progress),revision=revision+1,updated_at=now() WHERE id=$1 AND bot_id=$2 AND tenant_id=$3 AND revision=$4 RETURNING *',[goal.id,bot.id,bot.tenant_id,update.expectedRevision,update.changes.status??null,update.changes.progress??null,update.changes.title??null,update.changes.reason??null,update.changes.successCondition??null,JSON.stringify(source)]);
   if(!updated)return{status:'conflict',reason:'goal_revision_changed'};
   await tx.query('UPDATE autonomy_settings SET goal_revision=goal_revision+1 WHERE bot_id=$1 AND tenant_id=$2',[bot.id,bot.tenant_id]);
   await tx.query("INSERT INTO audit_events(tenant_id,event,bot_id,detail) VALUES($1,'autonomous.goal.updated',$2,$3)",[bot.tenant_id,bot.id,JSON.stringify({goalId:goal.id,previousRevision:goal.revision,revision:updated.revision,fields:Object.keys(update.changes),scope:audience.scope,senderId:audience.senderId,workId:audience.action?.id})]);
   if(settings&&updated.status==='active'&&updated.source?.handoffId)await this.wake(bot.tenant_id,bot.id,settings.epoch,'handoff-due',Math.max(0,(Number(updated.source.notBefore||0)-Date.now())/1000),updated.id,tx);
   return{status:'updated',goal:updated,retainedExecutionResponsibility,meaning:'目标状态只表示认知目标结算；原Action/effects/unknown和未消费结果独立保留，不代表外部操作成功。'};
  });
 }
 async ensure(tenantId:string,botId:string,tx=this.db){
  await tx.query("INSERT INTO autonomy_settings(bot_id,tenant_id,config) SELECT id,tenant_id,$3 FROM bots WHERE id=$1 AND tenant_id=$2 AND deleted_at IS NULL ON CONFLICT(bot_id) DO NOTHING",[botId,tenantId,JSON.stringify(defaultAutonomyConfig)]);
  const [s]=await tx.query('SELECT * FROM autonomy_settings WHERE bot_id=$1 AND tenant_id=$2',[botId,tenantId]);
  if(!s)throw new AutonomyConflict('机器人不可用');return s;
 }
 async view(tenantId:string,botId:string){
  const settings=await this.ensure(tenantId,botId);
  const goals=await this.db.query("SELECT * FROM autonomy_goals WHERE bot_id=$1 AND tenant_id=$2 ORDER BY CASE WHEN status IN ('active','paused','paused_user','paused_mode') THEN 0 ELSE 1 END,updated_at DESC LIMIT 40",[botId,tenantId]);
  const cycles=await this.db.query("SELECT id,epoch,state,usage,reason,decision->>'reason' AS decision_reason,decision->'fault' AS fault,started_at,finished_at FROM autonomy_cycles WHERE bot_id=$1 AND tenant_id=$2 ORDER BY started_at DESC LIMIT 15",[botId,tenantId]);
  const actions=await this.db.query('SELECT id,cycle_id,epoch,goal_id,kind,target,objective,status,result,created_at,finished_at FROM autonomy_actions WHERE bot_id=$1 AND tenant_id=$2 ORDER BY created_at DESC LIMIT 25',[botId,tenantId]);
  const usage=await this.db.query(`SELECT * FROM autonomy_daily_usage WHERE bot_id=$1 AND tenant_id=$2 AND day=${daySql}`,[botId,tenantId]);
  return{settings,goals,cycles,actions,usage:usage[0]||null};
 }
 async configure(tenantId:string,botId:string,expectedEpoch:number,value:unknown,audit?:{actorId:string;reason:string}){
  const config=AutonomyConfigSchema.parse(value);
  return this.db.transaction(async tx=>{
   await this.ensure(tenantId,botId,tx);
   const [s]=await tx.query('SELECT * FROM autonomy_settings WHERE bot_id=$1 AND tenant_id=$2 FOR UPDATE',[botId,tenantId]);
   if(s.epoch!==expectedEpoch)throw new AutonomyConflict('自主配置已更新，请刷新');
   const epoch=s.epoch+1;
   await tx.query("UPDATE autonomy_settings SET epoch=$3,config=$4,state=$5,wait='{}',lease_owner=NULL,lease_until=NULL,next_wake_at=NULL,last_error=NULL,updated_at=now() WHERE bot_id=$1 AND tenant_id=$2",[botId,tenantId,epoch,JSON.stringify(config),config.mode==='active'?'ready':'disabled']);
   await tx.query('DELETE FROM autonomy_wakes WHERE bot_id=$1 AND tenant_id=$2',[botId,tenantId]);
   await tx.query("UPDATE autonomy_actions SET status='canceled',finished_at=now() WHERE bot_id=$1 AND tenant_id=$2 AND status='planned'",[botId,tenantId]);
   await tx.query("UPDATE autonomy_goals SET status=$3,revision=revision+1,updated_at=now() WHERE bot_id=$1 AND tenant_id=$2 AND status=$4",[botId,tenantId,config.mode==='active'?'active':'paused_mode',config.mode==='active'?'paused_mode':'active']);
   if(config.mode==='active'){
    await this.rearmHandoffs(tenantId,botId,epoch,tx);
    await this.wake(tenantId,botId,epoch,'mode-enabled',0,'',tx);
   }
   if(audit)await tx.query("INSERT INTO audit_events(tenant_id,event,bot_id,detail) VALUES($1,'admin.autonomous.paused',$2,$3)",[tenantId,botId,JSON.stringify({actorId:audit.actorId,reason:audit.reason,previousEpoch:s.epoch,epoch})]);
   return{epoch,config,state:config.mode==='active'?'ready':'disabled',pendingRuntime:true};
  });
 }
 async wake(tenantId:string,botId:string,epoch:number,reason:string,seconds=0,scope='',tx=this.db){
  // One row per source/scope merges events instead of retaining message text.
  const dedupe=reason+':'+scope;
  await tx.query("INSERT INTO autonomy_wakes(bot_id,tenant_id,dedupe_key,epoch,due_at,reason,scope) SELECT bot_id,tenant_id,$4,epoch,now()+($5*interval '1 second'),$6,$7 FROM autonomy_settings WHERE bot_id=$1 AND tenant_id=$2 AND epoch=$3 AND config->>'mode'='active' ON CONFLICT(bot_id,dedupe_key) DO UPDATE SET epoch=excluded.epoch,due_at=LEAST(autonomy_wakes.due_at,excluded.due_at),reason=excluded.reason",[botId,tenantId,epoch,dedupe,Math.max(0,seconds),reason,scope]);
  await tx.query('UPDATE autonomy_settings SET next_wake_at=(SELECT min(due_at) FROM autonomy_wakes WHERE bot_id=$1),updated_at=now() WHERE bot_id=$1 AND tenant_id=$2 AND epoch=$3',[botId,tenantId,epoch]);
 }
 async rearmHandoffs(tenantId:string,botId:string,epoch:number,tx=this.db){
  const goals=await tx.query("SELECT id,source FROM autonomy_goals g WHERE bot_id=$1 AND tenant_id=$2 AND status='active' AND source->>'handoffId'=id::text AND NOT EXISTS(SELECT 1 FROM autonomy_actions a WHERE a.goal_id=g.id AND a.bot_id=g.bot_id AND a.tenant_id=g.tenant_id AND a.status IN ('planned','executing','unknown','partial'))",[botId,tenantId]);
  for(const goal of goals)await this.wake(tenantId,botId,epoch,'handoff-due',Math.max(0,(Number(goal.source?.notBefore||0)-Date.now())/1000),goal.id,tx);
 }
 async policies(){return this.db.query("SELECT s.*,b.generation,b.revision,b.desired_state,b.health,b.runtime_mode,b.group_id FROM autonomy_settings s JOIN bots b ON b.id=s.bot_id AND b.tenant_id=s.tenant_id WHERE b.deleted_at IS NULL AND (s.config->>'mode'='active' OR s.runtime_epoch<>s.epoch OR EXISTS(SELECT 1 FROM autonomy_cycles c WHERE c.bot_id=s.bot_id AND c.tenant_id=s.tenant_id AND c.decision ? 'expectedGoalRevision') OR EXISTS(SELECT 1 FROM autonomy_actions a WHERE a.bot_id=s.bot_id AND a.tenant_id=s.tenant_id AND (a.status='unknown' OR a.status='executing' AND EXISTS(SELECT 1 FROM autonomy_cycles c WHERE c.id=a.cycle_id AND c.bot_id=a.bot_id AND c.tenant_id=a.tenant_id AND c.finished_at IS NOT NULL) OR a.status='partial' AND a.result->'effects' @> '[{\"status\":\"unknown\"}]'::jsonb))) ORDER BY s.bot_id")}
 async acceptHandoff(bot:any,value:unknown){
  const parsed=TaskHandoffSchema.safeParse(value);
  if(!parsed.success)return{status:'rejected',reason:'invalid_handoff'};
  const r=parsed.data;
  if(r.origin==='model'&&r.kind!=='research'&&!r.parentActionId)return{status:'rejected',reason:'model_handoff_read_only'};
  const fingerprint=createHash('sha256').update(JSON.stringify(r)).digest('hex');
  if(r.botId!==bot.bot_id||r.tenantId!==bot.tenant_id||r.epoch!==bot.epoch||r.generation!==bot.generation||r.revision!==bot.revision)return{status:'rejected',reason:'handoff_identity_changed'};
  if(!r.parentActionId&&r.scope.startsWith('private:')&&r.scope!=='private:'+r.senderId)return{status:'rejected',reason:'handoff_scope_mismatch'};
  if(r.at>Date.now()+60000||r.at<Date.now()-604800000)return{status:'rejected',reason:'handoff_expired'};
  return this.db.transaction(async tx=>{
   const [s]=await tx.query("SELECT s.*,b.generation,b.revision,b.desired_state,b.health,c.config AS bot_config FROM autonomy_settings s JOIN bots b ON b.id=s.bot_id AND b.tenant_id=s.tenant_id JOIN bot_config_revisions c ON c.bot_id=b.id AND c.revision=b.revision WHERE s.bot_id=$1 AND s.tenant_id=$2 AND b.deleted_at IS NULL FOR UPDATE OF s",[r.botId,r.tenantId]);
   if(!s||s.epoch!==r.epoch||s.generation!==r.generation||s.revision!==r.revision||s.config.mode!=='active')return{status:'rejected',reason:'handoff_identity_changed'};
   const [existing]=await tx.query('SELECT source FROM autonomy_goals WHERE bot_id=$1 AND tenant_id=$2 AND id=$3',[r.botId,r.tenantId,r.id]);
   if(existing)return existing.source?.handoffId===r.id&&existing.source?.handoffFingerprint===fingerprint?{status:'accepted',goalId:r.id,notBefore:existing.source.notBefore}:{status:'rejected',reason:'handoff_identity_conflict'};
   // Mode authorizes the durable task executor; proactive only authorizes
   // model-created work. User-accepted reminders keep their separate source.
   let depth=0;
   if(r.parentActionId){
    const [parent]=await tx.query("SELECT a.id,a.goal_id,g.source AS goal_source FROM autonomy_actions a JOIN autonomy_cycles c ON c.id=a.cycle_id AND c.bot_id=a.bot_id AND c.tenant_id=a.tenant_id LEFT JOIN autonomy_goals g ON g.id=a.goal_id AND g.bot_id=a.bot_id AND g.tenant_id=a.tenant_id WHERE a.id=$1 AND a.bot_id=$2 AND a.tenant_id=$3 AND a.epoch=$4 AND c.generation=$5 AND c.config_revision=$6 AND a.status IN ('executing','completed')",[r.parentActionId,r.botId,r.tenantId,r.epoch,r.generation,r.revision]);
    if(!parent)return{status:'rejected',reason:'parent_action_not_owned_or_settled'};
    if((await tx.query("SELECT id FROM autonomy_goals WHERE bot_id=$1 AND tenant_id=$2 AND source->>'parentActionId'=$3 LIMIT 1",[r.botId,r.tenantId,r.parentActionId])).length)return{status:'rejected',reason:'parent_continuation_already_registered'};
    depth=Number(parent.goal_source?.continuationDepth||0)+1;
    if(depth>8)return{status:'rejected',reason:'continuation_depth_limit'};
    if(r.delaySeconds<300)return{status:'rejected',reason:'continuation_interval_minimum'};
   }else if(r.origin==='model'&&s.bot_config.proactive!==true)return{status:'rejected',reason:'proactive_disabled'};
   const [{count}]=await tx.query("SELECT count(*)::int AS count FROM autonomy_goals WHERE bot_id=$1 AND tenant_id=$2 AND status NOT IN ('completed','abandoned','canceled')",[r.botId,r.tenantId]);
   if(count>=MAX_CURRENT_GOALS)return{status:'rejected',reason:'active_goal_limit'};
   const owner=Boolean(s.bot_config.behavior?.ownerUin&&String(s.bot_config.behavior.ownerUin)===r.senderId);
   const notBefore=r.at+r.delaySeconds*1000;
   const source={eventId:r.eventId,scope:r.scope,senderId:r.senderId,owner:r.origin==='model'?false:owner,handoffId:r.id,handoffFingerprint:fingerprint,notBefore,kind:r.kind||'message',...(r.origin?{origin:r.origin}:{}),...(r.parentActionId?{parentActionId:r.parentActionId,continuationDepth:depth}:{}),...(r.agreement?{agreement:r.agreement}:{})};
   await tx.query("INSERT INTO autonomy_goals(id,bot_id,tenant_id,origin,status,title,reason,progress,success_condition,source) VALUES($1,$2,$3,$4,'active',$5,$6,'',$7,$8)",[r.id,r.botId,r.tenantId,r.origin==='model'?'model':owner?'owner_request':'contact_request',r.objective.slice(0,160),'内层通过授权工具登记，由现有任务账本到期执行',r.objective,JSON.stringify(source)]);
   await tx.query('UPDATE autonomy_settings SET goal_revision=goal_revision+1,updated_at=now() WHERE bot_id=$1 AND tenant_id=$2',[r.botId,r.tenantId]);
   await this.wake(r.tenantId,r.botId,r.epoch,'handoff-received',0,'',tx);
   await this.wake(r.tenantId,r.botId,r.epoch,'handoff-due',Math.max(0,(notBefore-Date.now())/1000),r.id,tx);
   return{status:'accepted',goalId:r.id,notBefore};
  });
 }
 async event(bot:any,at:number,event:any,seconds:number){
  if(!Number.isFinite(at)||at<=0||at>Date.now()+60000)return;
  await this.db.transaction(async tx=>{
   // This one bounded observation is lifecycle progress, not a second history
   // store. Source identity comes from the authenticated member, never an LLM.
   const observation={at,eventId:String(event.eventId||'').slice(0,100),scope:String(event.scope||'').slice(0,100),senderId:String(event.senderId||'').slice(0,20),owner:event.owner===true,directed:event.directed===true,text:String(event.text||'').slice(0,1000),phase:event.phase||'incoming',result:event.result||null};
   const rows=await tx.query("UPDATE autonomy_settings SET last_event_at=$4,observation=$5::jsonb||jsonb_build_object('latestFeedback',observation->'latestFeedback') WHERE bot_id=$1 AND tenant_id=$2 AND epoch=$3 AND (last_event_at IS NULL OR last_event_at<$4) RETURNING epoch",[bot.bot_id,bot.tenant_id,bot.epoch,new Date(at),JSON.stringify(observation)]);
   const [current]=await tx.query('SELECT wait FROM autonomy_settings WHERE bot_id=$1 AND tenant_id=$2',[bot.bot_id,bot.tenant_id]);
   const wanted=current?.wait?.scope;
   if(rows.length&&(!wanted||wanted===event.scope||event.directed))await this.wake(bot.tenant_id,bot.bot_id,bot.epoch,'real-event',event.directed?15:Math.max(60,seconds),String(event.scope||'').slice(0,100),tx);
  });
 }
 async reconcileCancellations(bot:any,requests:any[]){
  for(const r of requests.slice(0,128)){
   if(typeof r?.id!=='string'||r.botId!==bot.bot_id||r.tenantId!==bot.tenant_id||typeof r.scope!=='string'||typeof r.senderId!=='string'||typeof r.eventId!=='string')continue;
   // Authenticated worker projection, bound to the original request source.
   // Do not alter action results or erase unknown external-effect evidence.
   await this.db.query("UPDATE autonomy_goals SET status='abandoned',revision=revision+1,updated_at=now() WHERE id=$1 AND bot_id=$2 AND tenant_id=$3 AND status IN ('active','paused_mode') AND source->>'handoffId'=$1 AND source->>'scope'=$4 AND source->>'senderId'=$5 AND source->>'eventId'=$6",[r.id,bot.bot_id,bot.tenant_id,r.scope,r.senderId,r.eventId]);
  }
 }
 async reconcileAgreements(bot:any,states:any[]){
  for(const state of states.slice(0,128)){
   if(typeof state?.handoffId!=='string'||typeof state.id!=='string'||!Number.isSafeInteger(state.revision))continue;
   // Invalidation only. Never replay an action, erase its receipts, or mark
   // business completion from a state projection alone.
   if(state.status==='accepted'&&state.revision===state.taskRevision)continue;
   if(state.status==='fulfilled')continue;
   await this.db.query("UPDATE autonomy_goals SET status='abandoned',revision=revision+1,updated_at=now() WHERE id=$1 AND bot_id=$2 AND tenant_id=$3 AND status IN ('active','paused_mode') AND source->'agreement'->>'id'=$4 AND source->>'handoffId'=$1",[state.handoffId,bot.bot_id,bot.tenant_id,state.id]);
  }
 }
 async latestFeedback(bot:any,value:any){
  const at=Number(value?.at||0);
  if(!Number.isFinite(at)||at<=0||at>Date.now()+60000||value?.phase!=='foreground_result'||!value.result||typeof value.result!=='object')return;
  // One bounded result slot, independent of ambient-event coalescing. It is
  // execution state, not another chat history or model-generated memory.
  const feedback={at,eventId:String(value.eventId||'').slice(0,100),scope:String(value.scope||'').slice(0,100),senderId:String(value.senderId||'').slice(0,20),owner:value.owner===true,directed:value.directed===true,text:String(value.text||'').slice(0,1000),phase:'foreground_result',result:value.result};
  await this.db.query("UPDATE autonomy_settings SET observation=jsonb_set(observation,'{latestFeedback}',$4::jsonb,true) WHERE bot_id=$1 AND tenant_id=$2 AND epoch=$3 AND COALESCE((observation->'latestFeedback'->>'at')::double precision,0)<$5",[bot.bot_id,bot.tenant_id,bot.epoch,JSON.stringify(feedback),at]);
 }
 async applied(tenantId:string,botId:string,epoch:number){await this.db.query('UPDATE autonomy_settings SET runtime_epoch=$3 WHERE bot_id=$1 AND tenant_id=$2 AND epoch=$3',[botId,tenantId,epoch])}
 async suspendOffline(){
  await this.db.transaction(async tx=>{
   await tx.query(`UPDATE autonomy_settings s SET state='suspended_offline',lease_owner=NULL,lease_until=NULL WHERE s.config->>'mode'='active' AND s.state<>'suspended_offline' AND EXISTS(SELECT 1 FROM bots b WHERE b.id=s.bot_id AND b.tenant_id=s.tenant_id AND NOT COALESCE((${onlineSql}),false))`);
   const resumed=await tx.query(`UPDATE autonomy_settings s SET state='ready' WHERE s.state='suspended_offline' AND s.config->>'mode'='active' AND EXISTS(SELECT 1 FROM bots b WHERE b.id=s.bot_id AND b.tenant_id=s.tenant_id AND ${onlineSql}) RETURNING s.bot_id,s.tenant_id,s.epoch`);
   // The interrupted cycle may already have ended without an owned lease,
   // leaving no wake at all. Commit readiness and one merged wake together.
   // This resumes cognition only; it never creates a QQ runtime.
   for(const s of resumed)await this.wake(s.tenant_id,s.bot_id,s.epoch,'online-resumed',0,'',tx);
  });
 }
 async claim(owner:string){
  return this.db.transaction(async tx=>{
   const [s]=await tx.query(`SELECT s.*,b.generation,b.revision,b.runtime_mode FROM autonomy_settings s JOIN bots b ON b.id=s.bot_id AND b.tenant_id=s.tenant_id JOIN (SELECT member.tenant_id,max((SELECT c.started_at FROM autonomy_cycles c WHERE c.bot_id=member.bot_id AND c.tenant_id=member.tenant_id ORDER BY c.started_at DESC LIMIT 1)) AS last_turn FROM autonomy_settings member WHERE member.config->>'mode'='active' GROUP BY member.tenant_id) fair ON fair.tenant_id=s.tenant_id WHERE s.config->>'mode'='active' AND s.runtime_epoch=s.epoch AND ${onlineSql} AND (s.lease_until IS NULL OR s.lease_until<now()) AND (EXISTS(SELECT 1 FROM autonomy_wakes w WHERE w.bot_id=s.bot_id AND w.tenant_id=s.tenant_id AND w.epoch=s.epoch AND w.due_at<=now()) OR EXISTS(SELECT 1 FROM autonomy_cycles c WHERE c.bot_id=s.bot_id AND c.tenant_id=s.tenant_id AND c.finished_at IS NULL)) ORDER BY fair.last_turn NULLS FIRST,s.next_wake_at NULLS FIRST,s.bot_id LIMIT 1 FOR UPDATE OF s SKIP LOCKED`);
   if(!s)return null;
   const config=AutonomyConfigSchema.parse(s.config);
   await this.recover(s,tx);
   await tx.query(`INSERT INTO autonomy_daily_usage(bot_id,tenant_id,day) VALUES($1,$2,${daySql}) ON CONFLICT DO NOTHING`,[s.bot_id,s.tenant_id]);
   const [balance]=await tx.query(`SELECT used_tokens,reserved_tokens FROM autonomy_daily_usage WHERE bot_id=$1 AND tenant_id=$2 AND day=${daySql} FOR UPDATE`,[s.bot_id,s.tenant_id]);
   // A smaller remaining day balance is usable with a smaller Work budget.
   // Keep the configured ceiling unchanged, and reserve the entire effective
   // ceiling before any model request. Below the minimum cycle size, wait.
   const tokenLimit=config.dailyTokenBudget===0?config.cycleTokenBudget:Math.min(config.cycleTokenBudget,config.dailyTokenBudget-balance.used_tokens-balance.reserved_tokens);
   const [usage]=tokenLimit>=4000?await tx.query(`UPDATE autonomy_daily_usage SET reserved_tokens=reserved_tokens+$3 WHERE bot_id=$1 AND tenant_id=$2 AND day=${daySql} AND ($4=0 OR used_tokens+reserved_tokens+$3<=$4) RETURNING day`,[s.bot_id,s.tenant_id,tokenLimit,config.dailyTokenBudget]):[];
   if(!usage){
    await this.waitForDailyBudget(tx,s,'今日自主模型预算已预留或用完');return null;
   }
   const id=randomUUID();
   const wakes=await tx.query('DELETE FROM autonomy_wakes WHERE bot_id=$1 AND tenant_id=$2 AND epoch=$3 AND due_at<=now() RETURNING reason,scope',[s.bot_id,s.tenant_id,s.epoch]);
   // Consuming a wake is atomic with cycle ownership. Activity is re-armed
   // separately at a minimum five-minute interval, without a queued backlog.
   await tx.query("INSERT INTO autonomy_cycles(id,bot_id,tenant_id,epoch,generation,config_revision,owner,state,budget) VALUES($1,$2,$3,$4,$5,$6,$7,'thinking',$8)",[id,s.bot_id,s.tenant_id,s.epoch,s.generation,s.revision,owner,JSON.stringify({...config,cycleTokenBudget:tokenLimit,configuredCycleTokenBudget:config.cycleTokenBudget,day:usage.day})]);
   await tx.query("UPDATE autonomy_settings SET lease_owner=$3,lease_until=now()+interval '30 seconds',state='thinking',next_wake_at=(SELECT min(due_at) FROM autonomy_wakes WHERE bot_id=$1),updated_at=now() WHERE bot_id=$1 AND tenant_id=$2",[s.bot_id,s.tenant_id,owner]);
   return{...s,config,tokenLimit,cycleId:id,owner,day:usage.day,wakes};
  });
 }
 private async recover(s:any,tx:Sql){
  const lost=await tx.query("SELECT * FROM autonomy_cycles WHERE bot_id=$1 AND tenant_id=$2 AND finished_at IS NULL FOR UPDATE",[s.bot_id,s.tenant_id]);
  for(const cycle of lost){
   const reserved=cycle.budget.cycleTokenBudget,externalActions=await externalCount(tx,cycle.id),requests=modelRequestTotals(cycle.budget);
   // No returned usage after a crash is charged conservatively, not refunded.
   await tx.query('UPDATE autonomy_daily_usage SET reserved_tokens=GREATEST(0,reserved_tokens-$3),used_tokens=used_tokens+$3,external_actions=external_actions+$5,model_calls=model_calls+$6 WHERE bot_id=$1 AND tenant_id=$2 AND day=$4',[s.bot_id,s.tenant_id,reserved,cycle.budget.day,externalActions,requests.modelCalls]);
   await tx.query("UPDATE autonomy_cycles SET state='interrupted',reason='lease_expired',finished_at=now(),usage=usage||$2::jsonb WHERE id=$1",[cycle.id,JSON.stringify({externalActions,tokens:reserved,modelCalls:requests.modelCalls})]);
   await tx.query("UPDATE autonomy_actions SET status=CASE WHEN status='executing' THEN 'unknown' ELSE 'canceled' END,finished_at=now() WHERE cycle_id=$1 AND status IN ('planned','executing')",[cycle.id]);
  }
 }
 async renew(claim:any){
  const rows=await this.db.query(`UPDATE autonomy_settings s SET lease_until=now()+interval '30 seconds' WHERE bot_id=$1 AND tenant_id=$2 AND epoch=$3 AND lease_owner=$4 AND lease_until>now() AND config->>'mode'='active' AND EXISTS(SELECT 1 FROM bots b WHERE b.id=s.bot_id AND b.tenant_id=s.tenant_id AND b.generation=$5 AND b.revision=$6 AND ${onlineSql}) RETURNING epoch`,[claim.bot_id,claim.tenant_id,claim.epoch,claim.owner,claim.generation,claim.revision]);
  return rows.length===1;
 }
 async scheduleActivity(bot:any,seconds?:number,replace=false){
  const config=AutonomyConfigSchema.parse(bot.config);
  if(config.mode!=='active'||!config.activityEnabled)return;
  const delay=Math.max(300,Math.min(config.maxSleepSeconds,seconds??config.activityIntervalMinutes*60));
  await this.db.transaction(async tx=>{
   await tx.query("INSERT INTO autonomy_wakes(bot_id,tenant_id,dedupe_key,epoch,due_at,reason,scope) SELECT $1,$2,'activity:',$3,now()+($4*interval '1 second'),'activity','' WHERE EXISTS(SELECT 1 FROM autonomy_settings WHERE bot_id=$1 AND tenant_id=$2 AND epoch=$3 AND config->>'mode'='active') ON CONFLICT(bot_id,dedupe_key) DO UPDATE SET epoch=excluded.epoch,due_at=CASE WHEN $5 OR autonomy_wakes.epoch<>excluded.epoch THEN excluded.due_at ELSE autonomy_wakes.due_at END",[bot.bot_id,bot.tenant_id,bot.epoch,delay,replace]);
   await tx.query('UPDATE autonomy_settings SET next_wake_at=(SELECT min(due_at) FROM autonomy_wakes WHERE bot_id=$1 AND tenant_id=$2 AND epoch=$3) WHERE bot_id=$1 AND tenant_id=$2 AND epoch=$3',[bot.bot_id,bot.tenant_id,bot.epoch]);
  });
 }
 async planActivity(claim:any,usage:any){
  return this.db.transaction(async tx=>{
   const [s]=await tx.query("SELECT * FROM autonomy_settings WHERE bot_id=$1 AND tenant_id=$2 AND epoch=$3 AND lease_owner=$4 AND lease_until>now() FOR UPDATE",[claim.bot_id,claim.tenant_id,claim.epoch,claim.owner]);
   if(!s||!AutonomyConfigSchema.parse(s.config).activityEnabled&&!claim.wakes?.some((w:any)=>['handoff-due','operator-wake'].includes(w.reason)))throw new AutonomyConflict();
   const [prior]=await tx.query('SELECT id FROM autonomy_actions WHERE cycle_id=$1 AND bot_id=$2 AND tenant_id=$3 AND goal_id IS NULL',[claim.cycleId,claim.bot_id,claim.tenant_id]);
   if(prior)throw new AutonomyConflict();
   const action={id:randomUUID(),kind:'research',target:'',objective:'根据当前真实状态自主选择行动或休息'};
   await tx.query("UPDATE autonomy_cycles SET decision=COALESCE(decision,'{}'::jsonb)||$2::jsonb,usage=$3,state='acting' WHERE id=$1 AND bot_id=$4 AND tenant_id=$5",[claim.cycleId,JSON.stringify({reason:'autonomous_moment',action}),JSON.stringify(usage),claim.bot_id,claim.tenant_id]);
   await tx.query("INSERT INTO autonomy_actions(id,cycle_id,bot_id,tenant_id,epoch,kind,target,objective,status,expires_at) VALUES($1,$2,$3,$4,$5,'research','',$6,'planned',now()+($7*interval '1 second'))",[action.id,claim.cycleId,claim.bot_id,claim.tenant_id,claim.epoch,action.objective,claim.config.cycleSeconds]);
   return action;
  });
 }
 async planToolContinuation(claim:any,usage:any){
  return this.db.transaction(async tx=>{
   const [settings]=await tx.query("SELECT * FROM autonomy_settings WHERE bot_id=$1 AND tenant_id=$2 AND epoch=$3 AND lease_owner=$4 AND lease_until>now() FOR UPDATE",[claim.bot_id,claim.tenant_id,claim.epoch,claim.owner]);
   if(!settings)return null;
   await tx.query("UPDATE tool_effects e SET completion_state='unknown',completion_until=NULL WHERE e.bot_id=$1 AND e.tenant_id=$2 AND e.authority='autonomous' AND e.completion_state='claimed' AND e.completion_until<now() AND EXISTS(SELECT 1 FROM autonomy_actions a WHERE a.id=e.continuation->>'coordinatorActionId' AND a.bot_id=e.bot_id AND a.tenant_id=e.tenant_id AND a.status<>'planned')",[claim.bot_id,claim.tenant_id]);
   const [job]=await tx.query(`SELECT e.*,a.goal_id,a.objective AS parent_objective,g.source AS goal_source,c.decision->>'reason' AS parent_reason
    FROM tool_effects e JOIN autonomy_actions a ON a.id=e.work_id AND a.bot_id=e.bot_id AND a.tenant_id=e.tenant_id
    JOIN autonomy_cycles c ON c.id=a.cycle_id AND c.bot_id=a.bot_id AND c.tenant_id=a.tenant_id
    LEFT JOIN autonomy_goals g ON g.id=a.goal_id AND g.bot_id=a.bot_id AND g.tenant_id=a.tenant_id
    WHERE e.bot_id=$1 AND e.tenant_id=$2 AND e.authority='autonomous' AND e.operation='analyze'
    AND e.generation=$3 AND e.config_revision=$4 AND e.mode_epoch=$5
    AND e.state IN ('finished','unknown','canceled') AND (e.completion_state='pending' OR e.completion_state='claimed' AND e.completion_until<now())
    ORDER BY e.created_at,e.id LIMIT 1 FOR UPDATE OF e SKIP LOCKED`,[claim.bot_id,claim.tenant_id,claim.generation,claim.revision,claim.epoch]);
   if(!job)return null;
   const id=randomUUID(),owner=randomUUID();
   // The original effect is only consumed as evidence; no replay of analyze or writes.
   const continuation={id:job.id,owner,parentActionId:job.work_id,goalId:job.goal_id,result:job.result,scope:job.goal_source?.scope||'self',originEvent:job.goal_source?.eventId||job.origin_event,objective:job.parent_objective};
   const action={id,kind:'research',target:'',goalId:job.goal_id||undefined,objective:job.parent_objective,continuation,continuationOfMoment:job.parent_reason==='autonomous_moment',requestSource:job.goal_source||{scope:'self',senderId:'0',origin:'model'}};
   await tx.query("UPDATE tool_effects SET completion_state='claimed',completion_owner=$2,completion_until=now()+interval '120 seconds',continuation=continuation||jsonb_build_object('coordinatorActionId',$3::text) WHERE id=$1",[job.id,owner,id]);
   await tx.query("INSERT INTO autonomy_actions(id,cycle_id,bot_id,tenant_id,epoch,kind,target,objective,goal_id,status,expires_at,result) VALUES($1,$2,$3,$4,$5,'research','',$6,$7,'planned',now()+($8*interval '1 second'),$9)",[id,claim.cycleId,claim.bot_id,claim.tenant_id,claim.epoch,job.parent_objective,job.goal_id,claim.config.cycleSeconds,JSON.stringify({continuation})]);
   await tx.query("UPDATE autonomy_cycles SET decision=COALESCE(decision,'{}'::jsonb)||$2::jsonb,state='acting',usage=$3 WHERE id=$1",[claim.cycleId,JSON.stringify({reason:job.parent_reason==='autonomous_moment'?'autonomous_moment':'tool_continuation',action,continuation}),JSON.stringify(usage)]);
   return action;
  });
 }
 async settleToolContinuation(claim:any,action:any,status:'done'|'unknown'|'pending'){
  return this.db.query("UPDATE tool_effects SET completion_state=$5,completion_until=NULL WHERE id=$1 AND bot_id=$2 AND tenant_id=$3 AND completion_state='claimed' AND completion_owner=$4 RETURNING id",[action.continuation.id,claim.bot_id,claim.tenant_id,action.continuation.owner,status]);
 }
 async markExecuting(claim:any,id:string){
  return(await this.db.query("UPDATE autonomy_actions a SET status='executing' WHERE id=$1 AND bot_id=$2 AND tenant_id=$3 AND cycle_id=$4 AND status='planned' AND expires_at>now() AND (EXISTS(SELECT 1 FROM autonomy_goals g WHERE g.id=a.goal_id AND g.bot_id=a.bot_id AND g.tenant_id=a.tenant_id AND g.status='active' AND g.source->>'handoffId'=g.id::text) OR EXISTS(SELECT 1 FROM tool_effects e WHERE e.bot_id=a.bot_id AND e.tenant_id=a.tenant_id AND e.authority='autonomous' AND e.completion_state='claimed' AND e.completion_until>now() AND e.continuation->>'coordinatorActionId'=a.id) OR a.goal_id IS NULL AND a.kind='research' AND EXISTS(SELECT 1 FROM autonomy_cycles activity WHERE activity.id=a.cycle_id AND activity.bot_id=a.bot_id AND activity.tenant_id=a.tenant_id AND activity.decision->>'reason'='autonomous_moment')) AND EXISTS(SELECT 1 FROM autonomy_settings s WHERE s.bot_id=a.bot_id AND s.tenant_id=a.tenant_id AND s.epoch=a.epoch AND s.lease_owner=$5 AND s.lease_until>now()) AND NOT EXISTS(SELECT 1 FROM autonomy_actions old WHERE old.bot_id=a.bot_id AND old.tenant_id=a.tenant_id AND old.id<>a.id AND old.kind=a.kind AND old.target=a.target AND old.kind<>'research' AND (old.status='unknown' OR old.status='partial' AND old.result->'effects' @> '[{\"status\":\"unknown\"}]'::jsonb)) RETURNING id",[id,claim.bot_id,claim.tenant_id,claim.cycleId,claim.owner])).length===1;
 }
 async cancelPlanned(claim:any,id:string,reason:string){await this.db.query("UPDATE autonomy_actions SET status='canceled',result=$5,finished_at=now() WHERE id=$1 AND bot_id=$2 AND tenant_id=$3 AND cycle_id=$4 AND status='planned'",[id,claim.bot_id,claim.tenant_id,claim.cycleId,JSON.stringify({status:'canceled',summary:reason,effects:[],evidence:[],unfinished:[]})])}
 async feedback(claim:any,id:string,result:any){
  result=await this.withExternalEffects(claim,id,result);
  await this.db.query("UPDATE autonomy_actions SET status=$5,result=COALESCE(result,'{}'::jsonb)||$6::jsonb,receipt=$7,finished_at=now() WHERE id=$1 AND bot_id=$2 AND tenant_id=$3 AND cycle_id=$4 AND status IN ('executing','unknown','partial')",[id,claim.bot_id,claim.tenant_id,claim.cycleId,result.status,JSON.stringify(result),JSON.stringify(result.effects||[])]);
  if(result.continuationAccepted===true)await this.db.query("UPDATE tool_effects SET completion_state='done',completion_until=NULL WHERE bot_id=$1 AND tenant_id=$2 AND continuation->>'coordinatorActionId'=$3 AND completion_state='unknown'",[claim.bot_id,claim.tenant_id,id]);
 }
 async withExternalEffects(claim:any,id:string,result:any){
  const bili=await biliWorkEffects(this.db,claim.bot_id,claim.tenant_id,id);
  const effects=[...(result.effects||[]).filter((e:any)=>e.source!=='bilibili'),...bili];
  const uncertain=effects.some((e:any)=>e.status==='unknown');
  const pending=await this.db.query("SELECT id FROM tool_effects WHERE work_id=$1 AND bot_id=$2 AND tenant_id=$3 AND authority='autonomous' AND operation='analyze' AND (state IN ('queued','claimed','dispatching') OR completion_state IN ('pending','claimed'))",[id,claim.bot_id,claim.tenant_id]);
  if(pending.length&&result.status==='completed')result={...result,status:'partial',unfinished:[...(result.unfinished||[]),'后台分析结果尚未交回原任务理解：'+pending.map(p=>p.id).join(',')]};
  return{...result,effects,...(uncertain?{status:effects.some((e:any)=>e.status==='confirmed')?'partial':'unknown'}:{})};
 }
 async unconfirmed(tenantId:string,botId:string){
  // A finalized cycle can still have an executing row after a lost feedback
  // write. Recover only its receipt ownership, never dispatch it again. The
  // predicate excludes live cycles and binds all three ledger identities.
  await this.db.query("UPDATE autonomy_actions a SET status='unknown' WHERE a.bot_id=$1 AND a.tenant_id=$2 AND a.status='executing' AND EXISTS(SELECT 1 FROM autonomy_cycles c WHERE c.id=a.cycle_id AND c.bot_id=a.bot_id AND c.tenant_id=a.tenant_id AND c.finished_at IS NOT NULL)",[botId,tenantId]);
  return this.db.query("SELECT id,cycle_id,kind,objective,result FROM autonomy_actions WHERE bot_id=$1 AND tenant_id=$2 AND coalesce((result->'receiptCheck'->>'nextAt')::double precision,0)<=extract(epoch FROM now()) AND (status='unknown' OR status='partial' AND result->'effects' @> '[{\"status\":\"unknown\"}]'::jsonb) ORDER BY created_at LIMIT 2",[botId,tenantId])}
 async receiptChecked(bot:any,pending:any){
  const attempts=Math.min(10,Number(pending.result?.receiptCheck?.attempts||0)+1);
  await this.db.query("UPDATE autonomy_actions SET result=jsonb_set(coalesce(result,'{}'),'{receiptCheck}',$4::jsonb,true) WHERE id=$1 AND bot_id=$2 AND tenant_id=$3 AND status IN ('unknown','partial')",[pending.id,bot.bot_id,bot.tenant_id,JSON.stringify({attempts,nextAt:Date.now()/1000+Math.min(300,30*2**Math.min(4,attempts-1))})]);
 }
 async otherDue(claim:any){
  const rows=await this.db.query(`SELECT 1 FROM autonomy_settings s JOIN bots b ON b.id=s.bot_id AND b.tenant_id=s.tenant_id WHERE s.bot_id<>$1 AND s.config->>'mode'='active' AND s.runtime_epoch=s.epoch AND ${onlineSql} AND (s.lease_until IS NULL OR s.lease_until<now()) AND EXISTS(SELECT 1 FROM autonomy_wakes w WHERE w.bot_id=s.bot_id AND w.tenant_id=s.tenant_id AND w.epoch=s.epoch AND w.due_at<=now()) LIMIT 1`,[claim.bot_id]);return rows.length>0;
 }
 private async waitForDailyBudget(tx:Sql,bot:any,message:string){
  await tx.query("UPDATE autonomy_wakes SET due_at=(date_trunc('day',now() AT TIME ZONE 'Asia/Shanghai')+interval '1 day') AT TIME ZONE 'Asia/Shanghai' WHERE bot_id=$1 AND tenant_id=$2 AND epoch=$3 AND due_at<=now()",[bot.bot_id,bot.tenant_id,bot.epoch]);
  // A crash or a request rejected before dispatch may have consumed its wake.
  await tx.query("INSERT INTO autonomy_wakes(bot_id,tenant_id,dedupe_key,epoch,due_at,reason,scope) VALUES($1,$2,'budget-reset:',$3,(date_trunc('day',now() AT TIME ZONE 'Asia/Shanghai')+interval '1 day') AT TIME ZONE 'Asia/Shanghai','budget-reset','') ON CONFLICT(bot_id,dedupe_key) DO UPDATE SET epoch=excluded.epoch,due_at=excluded.due_at",[bot.bot_id,bot.tenant_id,bot.epoch]);
  await tx.query("UPDATE autonomy_settings SET state='budget_wait',last_error=$4,next_wake_at=(SELECT min(due_at) FROM autonomy_wakes WHERE bot_id=$1 AND tenant_id=$2 AND epoch=$3) WHERE bot_id=$1 AND tenant_id=$2 AND epoch=$3",[bot.bot_id,bot.tenant_id,bot.epoch,message]);
 }
 async finish(claim:any,usage:{tokens:number;modelCalls:number;toolCalls:number;externalActions:number;charges?:unknown[]},reason:string,fault?:Record<string,unknown>){
  await this.db.transaction(async tx=>{
   // The usage JSON keeps its historical totals and now also carries the
   // charge ledger (actual/estimated/unknown per request); the fault context
   // is merged into the cycle decision JSON without changing any schema.
   const [lockedCycle]=await tx.query('SELECT * FROM autonomy_cycles WHERE id=$1 AND bot_id=$2 AND tenant_id=$3 FOR UPDATE',[claim.cycleId,claim.bot_id,claim.tenant_id]);
   if(!lockedCycle||lockedCycle.finished_at)return;
   const mainRequests=modelRequestTotals(lockedCycle.budget,'main'),mediaRequests=modelRequestTotals(lockedCycle.budget,'media');
   const journalEffects=[...new Set(Object.values(lockedCycle.budget.modelRequests||{}).filter((r:any)=>r.source==='media').map((r:any)=>r.effectId))];
   usage={...usage,modelCalls:Math.max(usage.modelCalls,mainRequests.modelCalls),tokens:Math.max(usage.tokens,mainRequests.tokens)};
   const [media]=await tx.query("SELECT COALESCE(sum(COALESCE((e.usage_checkpoint->>'settledModelRequests')::int,0)),0)::int AS calls,COALESCE(sum(COALESCE((e.usage_checkpoint->>'settledTokens')::bigint,0)),0)::bigint AS tokens,count(*) FILTER (WHERE COALESCE((e.usage_checkpoint->>'settledModelRequests')::int,0)>0 AND COALESCE(e.usage_checkpoint->>'tokenStatus','unknown')='unknown')::int AS unknown FROM tool_effects e JOIN autonomy_actions a ON a.id=e.work_id AND a.bot_id=e.bot_id AND a.tenant_id=e.tenant_id WHERE a.cycle_id=$1 AND e.authority='autonomous' AND NOT (e.id::text=ANY($2::text[]))",[claim.cycleId,journalEffects]);
   usage={...usage,modelCalls:usage.modelCalls+mediaRequests.modelCalls+Number(media.calls),tokens:usage.tokens+mediaRequests.tokens+Number(media.tokens),externalActions:await externalCount(tx,claim.cycleId),...{mediaModelCalls:mediaRequests.modelCalls+Number(media.calls),mediaTokens:mediaRequests.tokens+Number(media.tokens),mediaTokenStatus:Number(media.unknown)>0||Object.values(lockedCycle.budget.modelRequests||{}).some((r:any)=>r.source==='media'&&!['settled','released'].includes(r.status))?'unknown':'actual'}};
   const faultJson=JSON.stringify(fault?{fault:{...fault}}:{});
   const settled=['no_due_work','handoff_executed','activity_completed','goal_awaits_model_decision'].includes(reason);
   const done=await tx.query("UPDATE autonomy_cycles SET state=$2,usage=$3,reason=$4,finished_at=now(),decision=COALESCE(decision,'{}'::jsonb)||$5::jsonb WHERE id=$1 AND bot_id=$6 AND tenant_id=$7 AND finished_at IS NULL RETURNING id",[claim.cycleId,settled?'completed':'ended',JSON.stringify(usage),reason,faultJson,claim.bot_id,claim.tenant_id]);
   if(!done.length)return;
   await tx.query('UPDATE autonomy_daily_usage SET reserved_tokens=GREATEST(0,reserved_tokens-$3),used_tokens=used_tokens+$4,model_calls=model_calls+$5,tool_calls=tool_calls+$6,external_actions=external_actions+$7 WHERE bot_id=$1 AND tenant_id=$2 AND day=$8',[claim.bot_id,claim.tenant_id,claim.tokenLimit??claim.config.cycleTokenBudget,Math.max(0,usage.tokens),usage.modelCalls,usage.toolCalls,usage.externalActions,claim.day]);
   // No polling wake: the event-driven cycle re-arms 'handoff-due' itself
   // before finishing whenever due work remains. next_wake_at keeps pointing
   // at the nearest future handoff deadline, or nothing until the next
   // accepted handoff / event / config change writes a wake.
   const sleepState=reason==='waiting_receipt'||reason==='handoff_retry_wait'?'waiting_receipt':settled?'sleeping':'backoff';
   await tx.query("UPDATE autonomy_settings SET lease_owner=NULL,lease_until=NULL,state=$5,last_error=$6,updated_at=now() WHERE bot_id=$1 AND tenant_id=$2 AND epoch=$3 AND lease_owner=$4 RETURNING epoch",[claim.bot_id,claim.tenant_id,claim.epoch,claim.owner,sleepState,settled?null:reason]);
  });
 }
}
