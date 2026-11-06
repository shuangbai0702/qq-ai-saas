import {createHash,randomUUID} from 'node:crypto';
import type {Sql} from './index.js';
import {ExecutionMemory} from './execution-memory.js';
import {runtimeOnlineSql} from '../shared/runtime-status.js';
import {AutonomyConfigSchema,defaultAutonomyConfig,TaskHandoffSchema,type AutonomyConfig,type DecisionEnvelope} from '../contracts/autonomous.js';

const daySql="to_char(now() AT TIME ZONE 'Asia/Shanghai','YYYY-MM-DD')";
// Match the existing 90-second health lifetime at every durable work boundary.
// A missing timestamp or a far-future report is not evidence of an online QQ.
const onlineSql=runtimeOnlineSql();
export class AutonomyConflict extends Error {}
export class AutonomyRepository {
 constructor(public db:Sql){}
 async experience(tenantId:string,botId:string){
  // Read the canonical lifecycle ledger; this is not a second memory store.
  const decisions=await this.db.query(`SELECT c.id AS "cycleId",c.decision->>'decidedAt' AS at,c.started_at AS "cycleStartedAt",c.decision->>'reason' AS reason,c.decision->'action' AS action,c.decision->'wait' AS wait,g.source->>'scope' AS "sourceScope" FROM autonomy_cycles c LEFT JOIN autonomy_goals g ON g.id::text=c.decision->'action'->>'goalId' AND g.bot_id=c.bot_id AND g.tenant_id=c.tenant_id WHERE c.tenant_id=$1 AND c.bot_id=$2 AND c.decision ? 'expectedGoalRevision' ORDER BY c.started_at DESC LIMIT 4`,[tenantId,botId]);
  const actions=await this.db.query(`SELECT a.id,a.kind,a.target,a.objective,a.status,a.created_at AS "assignedAt",a.finished_at AS "finishedAt",g.source->>'scope' AS "sourceScope",a.result FROM autonomy_actions a LEFT JOIN autonomy_goals g ON g.id=a.goal_id AND g.bot_id=a.bot_id AND g.tenant_id=a.tenant_id WHERE a.tenant_id=$1 AND a.bot_id=$2 ORDER BY a.created_at DESC LIMIT 6`,[tenantId,botId]);
  const execution=await new ExecutionMemory(this.db).recall(tenantId,botId,{scope:'self',senderId:'0',ownerId:'',limit:8});
  return{sampledAt:Date.now(),execution,decisions,actions:actions.map(a=>({id:a.id,kind:a.kind,target:a.target,objective:a.objective.slice(0,400),status:a.status,assignedAt:a.assignedAt,finishedAt:a.finishedAt,sourceScope:a.sourceScope,outerDecision:a.result?.task?.outerDecision,summary:String(a.result?.summary||'').slice(0,600),toolResults:(a.result?.toolResults||[]).slice(-6).map((t:any)=>({tool:t.tool,status:t.status,detail:String(t.detail||'').slice(0,200)})),effects:(a.result?.effects||[]).slice(-4).map((e:any)=>({method:e.method,target:e.target,status:e.status,receipt:e.receipt})),evidence:(a.result?.evidence||[]).slice(0,3),unfinished:(a.result?.unfinished||[]).slice(0,3)}))};
 }
 async activity(tenantId:string,botId:string,windowMinutes:number,config:Pick<AutonomyConfig,'fatigueRecoveryMinutes'|'fatigueCapacityMinutes'|'initiativeAfterIdleMinutes'>=defaultAutonomyConfig){
  // Existing durable receipts, scoped to this robot. A planning-only decision
  // or a canceled preflight is not an executed task and cannot reset idle time.
  const [row]=await this.db.query(`SELECT
   count(*) FILTER (WHERE finished_at>=now()-($3*interval '1 minute')) AS attempted_tasks,
   count(*) FILTER (WHERE finished_at>=now()-($3*interval '1 minute') AND status='completed') AS completed_tasks,
   count(*) FILTER (WHERE finished_at>=now()-($3*interval '1 minute') AND kind='research') AS research_tasks,
   count(*) FILTER (WHERE finished_at>=now()-($3*interval '1 minute') AND kind='message') AS message_tasks,
   count(*) FILTER (WHERE finished_at>=now()-($3*interval '1 minute') AND status IN ('unknown','partial','blocked','budget_exhausted')) AS failed_or_uncertain_tasks,
   COALESCE(sum(GREATEST(0,extract(epoch FROM finished_at-GREATEST(created_at,now()-($3*interval '1 minute'))))) FILTER (WHERE finished_at>=now()-($3*interval '1 minute')),0) AS task_elapsed_seconds,
   max(finished_at) AS last_task_finished_at,
   max(finished_at) FILTER (WHERE status='completed' OR status='partial' AND result->'effects' @> '[{"status":"confirmed"}]'::jsonb) AS last_progress_at,
   COALESCE(sum(LEAST(180,GREATEST(0,extract(epoch FROM finished_at-created_at)))*power(0.5,GREATEST(0,extract(epoch FROM now()-finished_at))/($4*60))),0) AS recovered_load_seconds
   FROM autonomy_actions WHERE tenant_id=$1 AND bot_id=$2 AND finished_at IS NOT NULL
   AND (COALESCE((result->'usage'->>'modelCalls')::int,0)>0 OR COALESCE((result->'usage'->>'toolCalls')::int,0)>0 OR jsonb_array_length(COALESCE(result->'effects','[]'::jsonb))>0)`,[tenantId,botId,windowMinutes,config.fatigueRecoveryMinutes]);
  const at=Date.now(),last=row.last_task_finished_at?new Date(row.last_task_finished_at).getTime():null;
  const [history]=await this.db.query(`SELECT
   (SELECT min(c.started_at) FROM autonomy_cycles c JOIN autonomy_settings s ON s.bot_id=c.bot_id AND s.tenant_id=c.tenant_id AND s.epoch=c.epoch WHERE c.tenant_id=$1 AND c.bot_id=$2) AS active_since,
   (SELECT count(*) FROM (SELECT reason FROM autonomy_cycles WHERE tenant_id=$1 AND bot_id=$2 AND finished_at IS NOT NULL ORDER BY started_at DESC LIMIT 2) recent WHERE reason='sleep') AS recent_rest_decisions`,[tenantId,botId]);
  const activeSince=history.active_since?new Date(history.active_since).getTime():null;
  const lastProgress=row.last_progress_at?new Date(row.last_progress_at).getTime():null;
  const anchor=lastProgress??activeSince,idle=anchor===null?0:Math.max(0,(at-anchor)/1000);
  const fatigue=1-Math.exp(-Number(row.recovered_load_seconds)/(config.fatigueCapacityMinutes*60));
  const regulation={fatigue,availableEnergy:1-fatigue,idleActivation:1-Math.pow(0.5,idle/(config.initiativeAfterIdleMinutes*60)),initiativePressure:(1-fatigue)*(1-Math.pow(0.5,idle/(config.initiativeAfterIdleMinutes*60))),activeSince,recentRestDecisions:Number(history.recent_rest_decisions),recoveryHalfLifeMinutes:config.fatigueRecoveryMinutes,capacityMinutes:config.fatigueCapacityMinutes,activationHalfLifeMinutes:config.initiativeAfterIdleMinutes};
  return{windowMinutes,at,lastTaskFinishedAt:last,lastProgressAt:lastProgress,idleSinceProgressSeconds:anchor===null?null:Math.max(0,Math.floor((at-anchor)/1000)),idleSinceLastTaskSeconds:last===null?null:Math.max(0,Math.floor((at-last)/1000)),recentTaskElapsedSeconds:Math.round(Number(row.task_elapsed_seconds)),attemptedTasks:Number(row.attempted_tasks),completedTasks:Number(row.completed_tasks),researchTasks:Number(row.research_tasks),messageTasks:Number(row.message_tasks),failedOrUncertainTasks:Number(row.failed_or_uncertain_tasks),regulation};
 }
 async recordOrientation(claim:any,orientation:any,usage:any){
  const rows=await this.db.query(`UPDATE autonomy_cycles c SET decision=COALESCE(decision,'{}'::jsonb)||jsonb_build_object('orientation',$4::jsonb),usage=$5 WHERE c.id=$1 AND c.bot_id=$2 AND c.tenant_id=$3 AND c.finished_at IS NULL AND EXISTS(SELECT 1 FROM autonomy_settings s WHERE s.bot_id=c.bot_id AND s.tenant_id=c.tenant_id AND s.epoch=$6 AND s.lease_owner=$7 AND s.lease_until>now()) RETURNING c.id`,[claim.cycleId,claim.bot_id,claim.tenant_id,JSON.stringify(orientation),JSON.stringify(usage),claim.epoch,claim.owner]);
  if(!rows.length)throw new AutonomyConflict('自主意图所有权已失效');
 }
 async ensure(tenantId:string,botId:string,tx=this.db){
  await tx.query("INSERT INTO autonomy_settings(bot_id,tenant_id,config) SELECT id,tenant_id,$3 FROM bots WHERE id=$1 AND tenant_id=$2 AND deleted_at IS NULL ON CONFLICT(bot_id) DO NOTHING",[botId,tenantId,JSON.stringify(defaultAutonomyConfig)]);
  const [s]=await tx.query('SELECT * FROM autonomy_settings WHERE bot_id=$1 AND tenant_id=$2',[botId,tenantId]);
  if(!s)throw new AutonomyConflict('机器人不可用');return s;
 }
 async view(tenantId:string,botId:string){
  const settings=await this.ensure(tenantId,botId);
  const goals=await this.db.query("SELECT * FROM autonomy_goals WHERE bot_id=$1 AND tenant_id=$2 ORDER BY CASE WHEN status IN ('active','paused','paused_user','paused_mode') THEN 0 ELSE 1 END,updated_at DESC LIMIT 40",[botId,tenantId]);
  const cycles=await this.db.query("SELECT id,epoch,state,usage,reason,decision->>'reason' AS decision_reason,decision->'orientation' AS orientation,started_at,finished_at FROM autonomy_cycles WHERE bot_id=$1 AND tenant_id=$2 ORDER BY started_at DESC LIMIT 15",[botId,tenantId]);
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
   await tx.query("UPDATE autonomy_goals SET status=$3,updated_at=now() WHERE bot_id=$1 AND tenant_id=$2 AND status=$4",[botId,tenantId,config.mode==='active'?'active':'paused_mode',config.mode==='active'?'paused_mode':'active']);
   if(config.mode==='active')await this.wake(tenantId,botId,epoch,'mode-enabled',0,'',tx);
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
 async policies(){return this.db.query("SELECT s.*,b.generation,b.revision,b.desired_state,b.health,b.runtime_mode,b.group_id FROM autonomy_settings s JOIN bots b ON b.id=s.bot_id AND b.tenant_id=s.tenant_id WHERE b.deleted_at IS NULL AND (s.config->>'mode'='active' OR s.runtime_epoch<>s.epoch OR EXISTS(SELECT 1 FROM autonomy_cycles c WHERE c.bot_id=s.bot_id AND c.tenant_id=s.tenant_id AND c.decision ? 'expectedGoalRevision') OR EXISTS(SELECT 1 FROM autonomy_actions a WHERE a.bot_id=s.bot_id AND a.tenant_id=s.tenant_id AND (a.status='unknown' OR a.status='partial' AND a.result->'effects' @> '[{\"status\":\"unknown\"}]'::jsonb))) ORDER BY s.bot_id")}
 async acceptHandoff(bot:any,value:unknown){
  const parsed=TaskHandoffSchema.safeParse(value);
  if(!parsed.success)return{status:'rejected',reason:'invalid_handoff'};
  const r=parsed.data;
  const fingerprint=createHash('sha256').update(JSON.stringify(r)).digest('hex');
  if(r.botId!==bot.bot_id||r.tenantId!==bot.tenant_id||r.epoch!==bot.epoch||r.generation!==bot.generation||r.revision!==bot.revision)return{status:'rejected',reason:'handoff_identity_changed'};
  if(r.scope.startsWith('private:')&&r.scope!=='private:'+r.senderId)return{status:'rejected',reason:'handoff_scope_mismatch'};
  if(r.at>Date.now()+60000||r.at<Date.now()-604800000)return{status:'rejected',reason:'handoff_expired'};
  return this.db.transaction(async tx=>{
   const [s]=await tx.query("SELECT s.*,b.generation,b.revision,b.desired_state,b.health,c.config AS bot_config FROM autonomy_settings s JOIN bots b ON b.id=s.bot_id AND b.tenant_id=s.tenant_id JOIN bot_config_revisions c ON c.bot_id=b.id AND c.revision=b.revision WHERE s.bot_id=$1 AND s.tenant_id=$2 AND b.deleted_at IS NULL FOR UPDATE OF s",[r.botId,r.tenantId]);
   if(!s||s.epoch!==r.epoch||s.generation!==r.generation||s.revision!==r.revision||s.config.mode!=='active')return{status:'rejected',reason:'handoff_identity_changed'};
   const [existing]=await tx.query('SELECT source FROM autonomy_goals WHERE bot_id=$1 AND tenant_id=$2 AND id=$3',[r.botId,r.tenantId,r.id]);
   if(existing)return existing.source?.handoffId===r.id&&existing.source?.handoffFingerprint===fingerprint?{status:'accepted',goalId:r.id,notBefore:existing.source.notBefore}:{status:'rejected',reason:'handoff_identity_conflict'};
   const [{count}]=await tx.query("SELECT count(*)::int AS count FROM autonomy_goals WHERE bot_id=$1 AND tenant_id=$2 AND status IN ('active','paused_mode')",[r.botId,r.tenantId]);
   if(count>=12)return{status:'rejected',reason:'active_goal_limit'};
   const owner=Boolean(s.bot_config.behavior?.ownerUin&&String(s.bot_config.behavior.ownerUin)===r.senderId);
   const notBefore=r.at+r.delaySeconds*1000;
   const source={eventId:r.eventId,scope:r.scope,senderId:r.senderId,owner,handoffId:r.id,handoffFingerprint:fingerprint,notBefore};
   await tx.query("INSERT INTO autonomy_goals(id,bot_id,tenant_id,origin,status,title,reason,progress,success_condition,source) VALUES($1,$2,$3,$4,'active',$5,$6,'',$7,$8)",[r.id,r.botId,r.tenantId,owner?'owner_request':'contact_request',r.objective.slice(0,160),'真实聊天通过授权工具提交，等待外层判断可行性和下一步行动',r.objective,JSON.stringify(source)]);
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
   const [s]=await tx.query(`SELECT s.*,b.generation,b.revision FROM autonomy_settings s JOIN bots b ON b.id=s.bot_id AND b.tenant_id=s.tenant_id JOIN (SELECT member.tenant_id,max((SELECT c.started_at FROM autonomy_cycles c WHERE c.bot_id=member.bot_id AND c.tenant_id=member.tenant_id ORDER BY c.started_at DESC LIMIT 1)) AS last_turn FROM autonomy_settings member WHERE member.config->>'mode'='active' GROUP BY member.tenant_id) fair ON fair.tenant_id=s.tenant_id WHERE s.config->>'mode'='active' AND s.runtime_epoch=s.epoch AND ${onlineSql} AND (s.lease_until IS NULL OR s.lease_until<now()) AND (EXISTS(SELECT 1 FROM autonomy_wakes w WHERE w.bot_id=s.bot_id AND w.tenant_id=s.tenant_id AND w.epoch=s.epoch AND w.due_at<=now()) OR EXISTS(SELECT 1 FROM autonomy_cycles c WHERE c.bot_id=s.bot_id AND c.tenant_id=s.tenant_id AND c.finished_at IS NULL)) ORDER BY fair.last_turn NULLS FIRST,s.next_wake_at NULLS FIRST,s.bot_id LIMIT 1 FOR UPDATE OF s SKIP LOCKED`);
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
   // This new decision supersedes the previous decision's sleep timer and
   // has already observed the current directed event. Future task deadlines,
   // ambient group checks and unrelated/manual wakes retain their ownership.
   const observedScope=s.observation?.directed===true?String(s.observation.scope||''):'';
   await tx.query("DELETE FROM autonomy_wakes WHERE bot_id=$1 AND tenant_id=$2 AND epoch=$3 AND (reason='autonomous-time' OR (reason='real-event' AND scope=$4 AND $4<>''))",[s.bot_id,s.tenant_id,s.epoch,observedScope]);
   await tx.query("INSERT INTO autonomy_cycles(id,bot_id,tenant_id,epoch,generation,config_revision,owner,state,budget) VALUES($1,$2,$3,$4,$5,$6,$7,'thinking',$8)",[id,s.bot_id,s.tenant_id,s.epoch,s.generation,s.revision,owner,JSON.stringify({...config,cycleTokenBudget:tokenLimit,configuredCycleTokenBudget:config.cycleTokenBudget,day:usage.day})]);
   await tx.query("UPDATE autonomy_settings SET lease_owner=$3,lease_until=now()+interval '30 seconds',state='thinking',next_wake_at=(SELECT min(due_at) FROM autonomy_wakes WHERE bot_id=$1),updated_at=now() WHERE bot_id=$1 AND tenant_id=$2",[s.bot_id,s.tenant_id,owner]);
   return{...s,config,tokenLimit,cycleId:id,owner,day:usage.day,wakes};
  });
 }
 private async recover(s:any,tx:Sql){
  const lost=await tx.query("SELECT * FROM autonomy_cycles WHERE bot_id=$1 AND tenant_id=$2 AND finished_at IS NULL FOR UPDATE",[s.bot_id,s.tenant_id]);
  for(const cycle of lost){
   const reserved=cycle.budget.cycleTokenBudget;
   // No returned usage after a crash is charged conservatively, not refunded.
   await tx.query('UPDATE autonomy_daily_usage SET reserved_tokens=GREATEST(0,reserved_tokens-$3),used_tokens=used_tokens+$3 WHERE bot_id=$1 AND tenant_id=$2 AND day=$4',[s.bot_id,s.tenant_id,reserved,cycle.budget.day]);
   await tx.query("UPDATE autonomy_cycles SET state='interrupted',reason='lease_expired',finished_at=now() WHERE id=$1",[cycle.id]);
   await tx.query("UPDATE autonomy_actions SET status=CASE WHEN status='executing' THEN 'unknown' ELSE 'canceled' END,finished_at=now() WHERE cycle_id=$1 AND status IN ('planned','executing')",[cycle.id]);
  }
 }
 async renew(claim:any){
  const rows=await this.db.query(`UPDATE autonomy_settings s SET lease_until=now()+interval '30 seconds' WHERE bot_id=$1 AND tenant_id=$2 AND epoch=$3 AND lease_owner=$4 AND lease_until>now() AND config->>'mode'='active' AND EXISTS(SELECT 1 FROM bots b WHERE b.id=s.bot_id AND b.tenant_id=s.tenant_id AND b.generation=$5 AND b.revision=$6 AND ${onlineSql}) RETURNING epoch`,[claim.bot_id,claim.tenant_id,claim.epoch,claim.owner,claim.generation,claim.revision]);
  return rows.length===1;
 }
 async commitDecision(claim:any,decision:DecisionEnvelope,usage:any){
  return this.db.transaction(async tx=>{
   const [s]=await tx.query('SELECT *,lease_until>now() AS lease_valid FROM autonomy_settings WHERE bot_id=$1 AND tenant_id=$2 FOR UPDATE',[claim.bot_id,claim.tenant_id]);
   if(!s||s.epoch!==claim.epoch||s.lease_owner!==claim.owner||!s.lease_valid||s.goal_revision!==decision.expectedGoalRevision)throw new AutonomyConflict('自主工作已失效');
   const [bot]=await tx.query(`SELECT b.generation,b.revision,(${onlineSql}) AS online FROM bots b WHERE b.id=$1 AND b.tenant_id=$2 FOR UPDATE`,[claim.bot_id,claim.tenant_id]);
   if(!bot||bot.generation!==claim.generation||bot.revision!==claim.revision||!bot.online)throw new AutonomyConflict('机器人配置或运行状态已变化');
   const goals=await tx.query('SELECT * FROM autonomy_goals WHERE bot_id=$1 AND tenant_id=$2',[claim.bot_id,claim.tenant_id]);const createdGoals:string[]=[];
   for(const change of decision.goalChanges){
    if(change.operation==='create'){
     if(change.id||!change.title)throw new AutonomyConflict('新目标身份或标题无效');
     if(goals.filter(g=>g.status==='active').length>=12)throw new AutonomyConflict('活跃目标过多');
     const observation=s.observation||{},delegated=Boolean(change.sourceEventId);
     if(delegated&&(change.sourceEventId!==observation.eventId||!observation.scope||!observation.senderId))throw new AutonomyConflict('目标的对话来源未核实');
     const source=delegated?{eventId:observation.eventId,scope:observation.scope,senderId:observation.senderId,owner:observation.owner===true}:{};
     const id=randomUUID();await tx.query("INSERT INTO autonomy_goals(id,bot_id,tenant_id,origin,status,title,reason,progress,success_condition,source) VALUES($1,$2,$3,$4,'active',$5,$6,$7,$8,$9)",[id,s.bot_id,s.tenant_id,delegated?(observation.owner?'owner_request':'contact_request'):'model',change.title,change.reason,change.progress??'',change.successCondition??'',JSON.stringify(source)]);goals.push({id,status:'active',source});createdGoals.push(id);
    }else{
     if(!change.id||!goals.some(g=>g.id===change.id))throw new AutonomyConflict('目标不属于当前机器人');
     const goal=goals.find(g=>g.id===change.id)!;
     if(change.operation==='complete'&&Number(goal.source?.notBefore||0)>Date.now())throw new AutonomyConflict('尚未到该任务最早执行时间，不能提前标记完成');
     if(change.sourceEventId&&change.sourceEventId!==goal.source?.eventId)throw new AutonomyConflict('已有目标的对话来源不能改写');
     if(goal.status==='paused_user')throw new AutonomyConflict('用户暂停的目标只能由用户恢复');
     if(change.operation==='resume'&&(goal.status!=='paused'||goals.filter(g=>g.status==='active').length>=12))throw new AutonomyConflict('目标当前不可恢复');
     const status={update:goal.status,pause:'paused',resume:'active',complete:'completed',abandon:'abandoned'}[change.operation];
     await tx.query('UPDATE autonomy_goals SET status=$4,title=COALESCE($5,title),reason=$6,progress=COALESCE($7,progress),success_condition=COALESCE($8,success_condition),revision=revision+1,updated_at=now() WHERE bot_id=$1 AND tenant_id=$2 AND id=$3',[s.bot_id,s.tenant_id,change.id,status,change.title||null,change.reason,change.progress??null,change.successCondition??null]);
     goal.status=status;
    }
   }
   const revision=s.goal_revision+(decision.goalChanges.length?1:0);
   await tx.query('UPDATE autonomy_settings SET goal_revision=$3,summary=$4,state=$5,wait=$6,updated_at=now() WHERE bot_id=$1 AND tenant_id=$2',[s.bot_id,s.tenant_id,revision,decision.summary,decision.action?'acting':'sleeping',JSON.stringify(decision.wait)]);
   const [committed]=await tx.query("UPDATE autonomy_cycles SET decision=COALESCE(decision,'{}'::jsonb)||$2::jsonb||jsonb_build_object('decidedAt',now()),usage=$3,state=$4 WHERE id=$1 AND bot_id=$5 AND tenant_id=$6 RETURNING decision->>'decidedAt' AS at",[claim.cycleId,JSON.stringify(decision),JSON.stringify(usage),decision.action?'acting':'observing',s.bot_id,s.tenant_id]);
   if(!decision.action)return{goalRevision:revision,action:null,source:undefined,decidedAt:committed.at};
   if(decision.action.goalId&&!goals.some(g=>g.id===decision.action!.goalId&&g.status==='active'))throw new AutonomyConflict('动作目标不属于当前机器人或已暂停/结束');
   if(decision.action.goalId&&Number(goals.find(g=>g.id===decision.action!.goalId)?.source?.notBefore||0)>Date.now())throw new AutonomyConflict('尚未到该任务最早执行时间');
   const action={id:randomUUID(),...decision.action,goalId:decision.action.goalId||(createdGoals.length===1?createdGoals[0]:undefined)};
   await tx.query("INSERT INTO autonomy_actions(id,cycle_id,bot_id,tenant_id,epoch,kind,target,objective,goal_id,status,expires_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,'planned',now()+($10*interval '1 second'))",[action.id,claim.cycleId,s.bot_id,s.tenant_id,s.epoch,action.kind,action.target,action.objective,action.goalId||null,claim.config.cycleSeconds]);
   return{goalRevision:revision,action,source:goals.find(g=>g.id===action.goalId)?.source,decidedAt:committed.at};
  });
 }
 async markExecuting(claim:any,id:string){
  return(await this.db.query("UPDATE autonomy_actions a SET status='executing' WHERE id=$1 AND bot_id=$2 AND tenant_id=$3 AND cycle_id=$4 AND status='planned' AND expires_at>now() AND EXISTS(SELECT 1 FROM autonomy_settings s WHERE s.bot_id=a.bot_id AND s.tenant_id=a.tenant_id AND s.epoch=a.epoch AND s.lease_owner=$5 AND s.lease_until>now()) AND NOT EXISTS(SELECT 1 FROM autonomy_actions old WHERE old.bot_id=a.bot_id AND old.tenant_id=a.tenant_id AND old.id<>a.id AND old.kind=a.kind AND old.target=a.target AND old.kind<>'research' AND (old.status='unknown' OR old.status='partial' AND old.result->'effects' @> '[{\"status\":\"unknown\"}]'::jsonb)) RETURNING id",[id,claim.bot_id,claim.tenant_id,claim.cycleId,claim.owner])).length===1;
 }
 async cancelPlanned(claim:any,id:string,reason:string){await this.db.query("UPDATE autonomy_actions SET status='canceled',result=$5,finished_at=now() WHERE id=$1 AND bot_id=$2 AND tenant_id=$3 AND cycle_id=$4 AND status='planned'",[id,claim.bot_id,claim.tenant_id,claim.cycleId,JSON.stringify({status:'canceled',summary:reason,effects:[],evidence:[],unfinished:[]})])}
 async feedback(claim:any,id:string,result:any){
  await this.db.query("UPDATE autonomy_actions SET status=$5,result=$6,receipt=$7,finished_at=now() WHERE id=$1 AND bot_id=$2 AND tenant_id=$3 AND cycle_id=$4 AND status IN ('executing','unknown','partial')",[id,claim.bot_id,claim.tenant_id,claim.cycleId,result.status,JSON.stringify(result),JSON.stringify(result.effects||[])]);
 }
 async unconfirmed(tenantId:string,botId:string){return this.db.query("SELECT id,cycle_id,kind,objective,result FROM autonomy_actions WHERE bot_id=$1 AND tenant_id=$2 AND coalesce((result->'receiptCheck'->>'nextAt')::double precision,0)<=extract(epoch FROM now()) AND (status='unknown' OR status='partial' AND result->'effects' @> '[{\"status\":\"unknown\"}]'::jsonb) ORDER BY created_at LIMIT 2",[botId,tenantId])}
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
 async finish(claim:any,usage:{tokens:number;modelCalls:number;toolCalls:number;externalActions:number},reason:string,sleepSeconds:number){
  await this.db.transaction(async tx=>{
   const done=await tx.query('UPDATE autonomy_cycles SET state=$2,usage=$3,reason=$4,finished_at=now() WHERE id=$1 AND bot_id=$5 AND tenant_id=$6 AND finished_at IS NULL RETURNING id',[claim.cycleId,reason==='sleep'?'completed':'ended',JSON.stringify(usage),reason,claim.bot_id,claim.tenant_id]);
   if(!done.length)return;
   await tx.query('UPDATE autonomy_daily_usage SET reserved_tokens=GREATEST(0,reserved_tokens-$3),used_tokens=used_tokens+$4,model_calls=model_calls+$5,tool_calls=tool_calls+$6,external_actions=external_actions+$7 WHERE bot_id=$1 AND tenant_id=$2 AND day=$8',[claim.bot_id,claim.tenant_id,claim.tokenLimit??claim.config.cycleTokenBudget,Math.max(0,usage.tokens),usage.modelCalls,usage.toolCalls,usage.externalActions,claim.day]);
   const rows=await tx.query("UPDATE autonomy_settings SET lease_owner=NULL,lease_until=NULL,state=$5,last_error=$6,updated_at=now() WHERE bot_id=$1 AND tenant_id=$2 AND epoch=$3 AND lease_owner=$4 RETURNING epoch",[claim.bot_id,claim.tenant_id,claim.epoch,claim.owner,reason==='sleep'?'sleeping':reason==='waiting_receipt'?'waiting_receipt':'backoff',reason==='sleep'?null:reason]);
   if(rows.length){
    if(reason==='daily_request_token_budget')await this.waitForDailyBudget(tx,claim,'剩余今日自主预算不足以容纳当前请求；等待预算恢复或新事件/配置');
    else await this.wake(claim.tenant_id,claim.bot_id,claim.epoch,'autonomous-time',sleepSeconds,'',tx);
   }
  });
 }
}
