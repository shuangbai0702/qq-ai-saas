import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';
import path from 'node:path';
import {openDatabase} from '../packages/database/index.js';
import {AutonomyRepository} from '../packages/database/autonomous.js';
import {AutonomousGoalUpdateSchema} from '../packages/contracts/autonomous.js';
import {ToolEffects} from '../packages/database/tool-effects.js';
import {qqExternalBudget} from '../packages/database/external-action-budget.js';
import {AutonomyCoordinator} from '../apps/node-agent/src/autonomous.js';
const goalId='00000000-0000-4000-8000-000000000001',cycleId='00000000-0000-4000-8000-000000000003',actionId=cycleId,effectId='00000000-0000-4000-8000-000000000004';
async function fixture(t:any){
 const root=await mkdtemp(path.resolve('.local/goal-management-')),db=await openDatabase(root);
 t.after(async()=>{await db.close();await rm(root,{recursive:true,force:true})});
 await db.query("INSERT INTO tenants(id,name) VALUES('tenant','tenant')");
 await db.query("INSERT INTO bots(id,tenant_id,name,protocol,desired_state,health) VALUES('bot','tenant','bot','mock','running',$1)",[JSON.stringify({sampledAt:new Date().toISOString(),protocol:{online:true}})]);
 await db.query("INSERT INTO bot_config_revisions(bot_id,revision,config) VALUES('bot',1,$1)",[JSON.stringify({proactive:true,behavior:{ownerUin:'12345678'}})]);
 const repo=new AutonomyRepository(db);await repo.configure('tenant','bot',1,{mode:'active',rollout:'live'});
 const [bot]=await db.query("SELECT * FROM bots WHERE id='bot'");
 await db.query("INSERT INTO autonomy_goals(id,bot_id,tenant_id,origin,status,title,reason,success_condition,source) VALUES($1,'bot','tenant','owner_request','active','original intent','original motive','original constraints',$2)",[goalId,JSON.stringify({scope:'private:12345678',senderId:'12345678',owner:true,handoffId:goalId,notBefore:Date.now()+600000})]);
 return{db,repo,bot};
}
test('current Goal limit includes paused goals and preserves accepted replay',async t=>{
 const {db,repo,bot}=await fixture(t);
 for(let i=0;i<1;i++)await db.query("INSERT INTO autonomy_goals(id,bot_id,tenant_id,origin,status,title,reason,success_condition,source) VALUES($1,'bot','tenant','model',$2,'retained','why','done','{}')",[`10000000-0000-4000-8000-${String(i).padStart(12,'0')}`,i%2?'paused':'paused_user']);
 const request={id:'20000000-0000-4000-8000-000000000001',tenantId:'tenant',botId:'bot',generation:bot.generation,revision:bot.revision,epoch:2,eventId:'real-input',scope:'private:12345678',senderId:'12345678',at:Date.now(),objective:'third goal',delaySeconds:600};
 const trusted={...bot,bot_id:bot.id,epoch:2};
 const third=await repo.acceptHandoff(trusted,request);assert.equal(third.status,'accepted');
 const replay=await repo.acceptHandoff(trusted,request);assert.equal(replay.status,'accepted');
 const fourth=await repo.acceptHandoff(trusted,{...request,id:'20000000-0000-4000-8000-000000000002',objective:'fourth goal'});
 assert.equal(fourth.status,'rejected');assert.equal(fourth.reason,'active_goal_limit');
 const [{count}]=await db.query("SELECT count(*)::int AS count FROM autonomy_goals WHERE bot_id='bot' AND status NOT IN ('completed','abandoned','canceled')");assert.equal(count,3);
});

test('goal exact-ID permissions, original intent preservation and stale revision CAS',async t=>{
 const {db,repo,bot}=await fixture(t),audience={scope:'private:12345678',senderId:'12345678'};
 assert.equal((await repo.readGoals(bot,{scope:'private:88888888',senderId:'88888888'})).goals.length,0);
 assert.equal((await repo.updateGoal(bot,{scope:'private:88888888',senderId:'88888888'},{goalId,expectedRevision:1,changes:{progress:'stolen'}})).status,'rejected');
 const updated=await repo.updateGoal(bot,audience,{goalId,expectedRevision:1,changes:{title:'clearer wording',successCondition:'current milestone',nextIntention:'continue analysis',progress:'evidence gathered'}});
 assert.equal(updated.status,'updated');assert.equal(updated.goal.revision,2);assert.equal(updated.goal.source.originalGoal.title,'original intent');assert.equal(updated.goal.source.originalGoal.successCondition,'original constraints');assert.equal(updated.goal.source.senderId,'12345678');
 assert.equal((await repo.updateGoal(bot,audience,{goalId,expectedRevision:1,changes:{progress:'stale'}})).status,'conflict');
 assert.equal(AutonomousGoalUpdateSchema.safeParse({goalId,expectedRevision:2,changes:{source:{owner:true}}}).success,false);
 for(const status of ['paused_user','paused_mode']){
  await db.query('UPDATE autonomy_goals SET status=$2 WHERE id=$1',[goalId,status]);
  for(const next of ['active','paused','completed','abandoned'])assert.equal((await repo.updateGoal(bot,audience,{goalId,expectedRevision:2,changes:{status:next}})).reason,'goal_control_pause_requires_existing_control','goal text changes cannot bypass an explicit control-plane pause');
 }
 await db.query("UPDATE autonomy_goals SET status='active' WHERE id=$1",[goalId]);
 await db.query("UPDATE autonomy_goals SET progress=$2 WHERE id=$1",[goalId,'full evidence '.repeat(1500)]);
 const snapshot=await repo.readGoals(bot,audience);assert.equal(snapshot.complete,true);assert.ok(snapshot.goals[0].progress.length>12000,'read never silently clips existing content');
 await db.query("INSERT INTO autonomy_cycles(id,bot_id,tenant_id,epoch,generation,config_revision,owner,state,budget) VALUES($1,'bot','tenant',2,$2,$3,'test','ended','{}')",[cycleId,bot.generation,bot.revision]);
 await db.query("UPDATE autonomy_cycles SET decision=jsonb_build_object('continuation',jsonb_build_object('goalId',$2::text),'receiptPending',true) WHERE id=$1",[cycleId,goalId]);
 await db.query("INSERT INTO tool_effects(id,tenant_id,bot_id,work_id,cycle_id,step_id,operation,args,args_digest,authority,mode_epoch,generation,config_revision,settings_revision,deadline_at,state,lease_owner) VALUES($1,'tenant','bot',$2,$2,'exact-step','analyze',$3,'exact-digest','autonomous',2,$4,$5,1,now()+interval '5 minutes','claimed','exact-worker')",[effectId,actionId,JSON.stringify({bvid:'BV1xx411c7mD',question:'specific observation',start:0,seconds:30,frameCount:4}),bot.generation,bot.revision]);
 const beforeEnd=await repo.readGoals(bot,audience);assert.deepEqual(Object.keys(beforeEnd).sort(),['complete','goals']);
 assert.equal(beforeEnd.goals[0].id,goalId);assert.equal(beforeEnd.goals[0].revision,2);assert.equal(beforeEnd.goals[0].success_condition,'current milestone');assert.equal(beforeEnd.goals[0].source.originalGoal.successCondition,'original constraints');
 const ended=await repo.updateGoal(bot,audience,{goalId,expectedRevision:2,changes:{status:'completed'}});assert.equal(ended.status,'updated');assert.equal(ended.retainedExecutionResponsibility,true);
 const retained=await repo.readGoals(bot,audience);assert.deepEqual(retained,{goals:[],complete:true});
 assert.equal((await db.query('SELECT state FROM tool_effects WHERE id=$1',[effectId]))[0].state,'claimed','ending a Goal does not retire its live original tool responsibility');
 assert.deepEqual(await repo.readGoals(bot,audience),{goals:[],complete:true});
 const [pending]=await db.query('SELECT * FROM tool_effects WHERE id=$1',[effectId]);
 assert.equal(pending.id,effectId);assert.equal(pending.work_id,actionId);assert.equal(pending.step_id,'exact-step');assert.equal(pending.args.frameCount,4);assert.equal(pending.args.question,'specific observation');assert.equal(pending.args_digest,'exact-digest');assert.equal(pending.lease_owner,'exact-worker');assert.equal(pending.mode_epoch,2);assert.equal(pending.state,'claimed');assert.equal(pending.cancel_requested,false);assert.ok(pending.deadline_at);
 await db.query("UPDATE tool_effects SET state='finished',completion_state='done' WHERE id=$1",[effectId]);
 const closed=await repo.readGoals(bot,audience);
 assert.deepEqual(closed,{goals:[],complete:true});
 const [closedGoal]=await db.query('SELECT * FROM autonomy_goals WHERE id=$1',[goalId]);assert.equal(closedGoal.status,'completed');assert.equal(closedGoal.revision,3);assert.ok(closedGoal.progress.length>12000,'reading goals never deletes authoritative history');
 assert.equal((await repo.readGoals(bot,{scope:'private:88888888',senderId:'88888888'})).goals.length,0,'current goals obey the original scope authority');

});
for(const {terminal,accepted} of [{terminal:'finished',accepted:true},{terminal:'unknown',accepted:true},{terminal:'canceled',accepted:true},{terminal:'finished',accepted:false},{terminal:'finished',accepted:undefined}])test('tool-completed '+terminal+' acceptance='+accepted+' keeps receipt ownership without replaying effects',async t=>{
 const {db,repo}=await fixture(t);
 await db.query("INSERT INTO autonomy_cycles(id,bot_id,tenant_id,epoch,generation,config_revision,owner,state,budget,decision,finished_at) VALUES($1,'bot','tenant',2,1,1,'old','completed','{}','{\"reason\":\"autonomous_moment\"}',now())",[cycleId]);
 await db.query("INSERT INTO tool_effects(id,tenant_id,bot_id,work_id,cycle_id,step_id,operation,args,args_digest,authority,mode_epoch,generation,config_revision,settings_revision,deadline_at,state,result,completion_state) VALUES($1,'tenant','bot',$2,$2,'analysis','analyze','{}','hash','autonomous',2,1,1,1,now(),$4,$3,'pending')",[effectId,actionId,JSON.stringify(terminal==='finished'?{execution:'completed',receipt:{status:'not_applicable'},data:{analysis:'real result'}}:{execution:terminal==='canceled'?'canceled':'failed',receipt:{status:terminal==='unknown'?'unknown':'rejected'}}),terminal]);
 await db.query("UPDATE autonomy_cycles SET generation=(SELECT generation FROM bots WHERE id='bot') WHERE id=$1",[cycleId]);
 await db.query("UPDATE tool_effects SET generation=(SELECT generation FROM bots WHERE id='bot') WHERE id=$1",[effectId]);
 await db.query("DELETE FROM autonomy_wakes WHERE bot_id='bot'");await repo.wake('tenant','bot',2,'tool-completed');
 let calls=0;
 const coordinator=new AutonomyCoordinator(db,async(_bot,operation,body:any)=>{
  if(operation==='policy'||operation==='snapshot')return{};
  if(operation==='receipt'&&accepted===undefined)return{exists:true,active:false,result:{status:'completed',summary:'durable staged receipt',effects:[],evidence:[],continuationAccepted:true,usage:{modelCalls:1,tokens:100,toolCalls:0}}};
  if(operation==='execute'){
   calls++;assert.notEqual(body.action.id,actionId);assert.equal(body.action.continuation.parentActionId,actionId);assert.equal(body.action.continuation.id,effectId);assert.equal(body.action.continuationOfMoment,true);assert.equal(body.action.continuation.result.execution,terminal==='finished'?'completed':terminal==='canceled'?'canceled':'failed');assert.equal('goalSnapshot' in body.action,false);
   return{status:accepted===false?'canceled':'completed',summary:'completion acceptance fixture',effects:[],unfinished:[],evidence:[],...(accepted===undefined?{}:{continuationAccepted:accepted}),usage:{modelCalls:accepted===false?0:1,tokens:accepted===false?0:100,toolCalls:0}};
  }throw Error(operation);
 });t.after(()=>coordinator.stop());
 await coordinator.tick();await coordinator.active?.task;assert.equal(calls,1,JSON.stringify(await repo.view('tenant','bot')));
 assert.equal((await db.query('SELECT completion_state FROM tool_effects WHERE id=$1',[effectId]))[0].completion_state,accepted===true?'done':accepted===false?'pending':'unknown');
 assert.equal((await db.query('SELECT state FROM autonomy_cycles WHERE id=$1',[cycleId]))[0].state,'completed','original cycle is not dispatched again');
 if(accepted===true)await repo.wake('tenant','bot',2,'tool-completed');
 if(accepted===false){const wakes=await db.query("SELECT scope,due_at>now() AS delayed FROM autonomy_wakes WHERE bot_id='bot' AND reason='tool-completed'");assert.deepEqual(wakes,[{scope:effectId,delayed:true}]);}
 // Advance past the existing receipt sampling interval without sleeping.
 const realNow=Date.now,clock=accepted===undefined?t.mock.method(Date,'now',()=>realNow()+31000):undefined;
 await coordinator.tick();await coordinator.active?.task;assert.equal(calls,1);
 clock?.mock.restore();
 if(accepted===undefined)assert.equal((await db.query('SELECT completion_state FROM tool_effects WHERE id=$1',[effectId]))[0].completion_state,'done','authoritative late acceptance settles the original result without executing again');
 assert.equal((await db.query('SELECT state FROM autonomy_cycles WHERE id=$1',[cycleId]))[0].state,'completed','original cycle is not dispatched again');
});

test('shadow blocks actual Bili and QQ write permits after admission while reads remain usable',async t=>{
 const {db,bot}=await fixture(t),effects=new ToolEffects(db);
 await effects.configure('tenant','bot',1,{enabled:true,allowAutonomous:true,scopes:['public.read','like.write']});
 await db.query("INSERT INTO bilibili_accounts(bot_id,tenant_id,uid,label,secret) VALUES('bot','tenant','123','test','unused')");
 await db.query("INSERT INTO autonomy_cycles(id,bot_id,tenant_id,epoch,generation,config_revision,owner,state,work_expires_at,budget) VALUES($1,'bot','tenant',2,$2,$3,'test','acting',now()+interval '5 minutes','{\"cycleExternalActions\":2}')",[cycleId,bot.generation,bot.revision]);
 await db.query("UPDATE autonomy_settings SET lease_owner='test',lease_until=now()+interval '5 minutes' WHERE bot_id='bot'");
 const call={workId:actionId,stepId:'like',operation:'like' as const,args:{bvid:'BV1xx411c7mD',value:true},authority:'autonomous' as const,epoch:2,deadlineAt:Date.now()+90000};
 const admitted=await effects.admit(bot,call),owned=await effects.claim('writer');assert.equal(owned.id,admitted.id);
 await db.query("UPDATE autonomy_settings SET config=jsonb_set(config,'{rollout}','\"shadow\"') WHERE bot_id='bot'");
 await assert.rejects(()=>effects.beginDispatch(owned,new AbortController().signal),/shadow/);
 await assert.rejects(()=>qqExternalBudget(db,bot,{workId:actionId,epoch:2,logicalActionId:'qq-write',phase:'reserve'}),/shadow/);
 await assert.rejects(()=>effects.admit(bot,{...call,stepId:'another-write'}),/shadow/);
 const read=await effects.admit(bot,{...call,stepId:'public-read',operation:'popular',args:{}});assert.equal(read.state,'queued');
 assert.equal((await effects.get('tenant','bot',admitted.id)).state,'claimed','rejected write gets no dispatch transition');
});

for(const late of [false,true])test('media provider usage settles '+(late?'after':'before')+' original cycle finish exactly once',async t=>{
 const {db,bot,repo}=await fixture(t),effects=new ToolEffects(db);
 await db.query("INSERT INTO autonomy_daily_usage(bot_id,tenant_id,day,used_tokens,model_calls) VALUES('bot','tenant','fixture',$1,$2)",[late?100:0,late?2:0]);
 await db.query("INSERT INTO autonomy_cycles(id,bot_id,tenant_id,epoch,generation,config_revision,owner,state,work_expires_at,budget,usage,finished_at) VALUES($1,'bot','tenant',2,$2,$3,'test','acting',now()+interval '5 minutes','{\"day\":\"fixture\",\"cycleTokenBudget\":1000}','{\"modelCalls\":2,\"tokens\":100}',CASE WHEN $4 THEN now() ELSE NULL END)",[cycleId,bot.generation,bot.revision,late]);
 await db.query("UPDATE autonomy_settings SET lease_owner='test',lease_until=now()+interval '5 minutes' WHERE bot_id='bot'");
 await db.query("INSERT INTO tool_effects(id,tenant_id,bot_id,work_id,cycle_id,step_id,operation,args,args_digest,authority,mode_epoch,generation,config_revision,settings_revision,deadline_at,state,lease_owner,usage_checkpoint) VALUES($1,'tenant','bot',$2,$2,'analysis','analyze','{}','hash','autonomous',2,$3,$4,1,now(),'claimed','worker','{\"modelRequests\":1}')",[effectId,actionId,bot.generation,bot.revision]);
 const [job]=await db.query('SELECT * FROM tool_effects WHERE id=$1',[effectId]);
 const result={schemaVersion:'fishbot.tool-result.v1' as const,actionId:effectId,execution:'completed' as const,receipt:{status:'not_applicable' as const},usage:{modelRequests:1,costStatus:'actual' as const,providerUsage:{total_tokens:75}},versions:{adapter:'fixture'}};
 await effects.finish(job,result);await effects.finish(job,result);
 if(!late)await repo.finish({cycleId,bot_id:'bot',tenant_id:'tenant',epoch:2,owner:'test',tokenLimit:1000,config:{cycleTokenBudget:1000},day:'fixture'},{tokens:100,modelCalls:2,toolCalls:0,externalActions:0},'activity_completed');
 const [cycle]=await db.query('SELECT usage FROM autonomy_cycles WHERE id=$1',[cycleId]),[day]=await db.query("SELECT * FROM autonomy_daily_usage WHERE bot_id='bot' AND day='fixture'");
 assert.equal(cycle.usage.modelCalls,3);assert.equal(Number(cycle.usage.tokens),175);assert.equal(day.model_calls,3);assert.equal(Number(day.used_tokens),175);
});

for(const entry of ['activity','handoff','continuation'])test(entry+' permit preparation failure is not started, costs zero and keeps safe retry',async t=>{
 const {db,repo,bot}=await fixture(t);await db.query("DELETE FROM autonomy_wakes WHERE bot_id='bot'");
 if(entry==='handoff')await db.query("UPDATE autonomy_goals SET source=source||jsonb_build_object('kind','research','notBefore',0) WHERE id=$1",[goalId]);
 if(entry==='continuation'){
  await db.query("INSERT INTO autonomy_cycles(id,bot_id,tenant_id,epoch,generation,config_revision,owner,state,budget,decision,finished_at) VALUES($1,'bot','tenant',2,$2,$3,'old','completed','{}','{\"reason\":\"autonomous_moment\"}',now())",[cycleId,bot.generation,bot.revision]);
  await db.query("INSERT INTO tool_effects(id,tenant_id,bot_id,work_id,cycle_id,step_id,operation,args,args_digest,authority,mode_epoch,generation,config_revision,settings_revision,deadline_at,state,result,completion_state) VALUES($1,'tenant','bot',$2,$2,'analysis','analyze','{}','hash','autonomous',2,$3,$4,1,now(),'finished','{\"execution\":\"completed\"}','pending')",[effectId,actionId,bot.generation,bot.revision]);
 }
 await repo.wake('tenant','bot',2,entry==='activity'?'activity':entry==='handoff'?'handoff-due':'tool-completed');
 let calls=0,fail=true;
 const coordinator=new AutonomyCoordinator(db,async(_b,op,body:any)=>{if(op==='policy'){if(body.actionId&&fail)throw Error('injected permit transport failure');return{}}if(op==='snapshot')return{};assert.equal(op,'execute','preparation failure must not cancel or query a runtime never dispatched');calls++;return{status:'completed',continuationAccepted:true,summary:'real model result',effects:[],unfinished:[],evidence:[],usage:{tokens:10,modelCalls:1,toolCalls:0}}});t.after(()=>coordinator.stop());
 for(let i=0;i<(entry==='handoff'?5:1);i++){
  await db.query("UPDATE autonomy_wakes SET due_at=now()-interval '1 second' WHERE bot_id='bot'");await coordinator.tick();await coordinator.active?.task;
  assert.equal(calls,0);const view=await repo.view('tenant','bot');assert.equal(view.cycles[0].reason,entry==='continuation'?'tool_continuation_canceled':'runtime_or_decision_failed');assert.equal(view.cycles[0].usage.modelCalls,0);assert.equal(view.cycles[0].usage.tokens,0);assert.equal(view.goals[0].status,'active');
  if(entry==='continuation')assert.equal((await db.query('SELECT completion_state FROM tool_effects WHERE id=$1',[effectId]))[0].completion_state,'pending');
 }
 fail=false;await db.query("UPDATE autonomy_wakes SET due_at=now()-interval '1 second' WHERE bot_id='bot'");await coordinator.tick();await coordinator.active?.task;assert.equal(calls,1);
 if(entry==='continuation')assert.equal((await db.query('SELECT completion_state FROM tool_effects WHERE id=$1',[effectId]))[0].completion_state,'done');
});
test('blocked activation leaves Goal decisions to the model; only a committed Goal tool revises or ends it',async t=>{
 const {db,repo,bot}=await fixture(t);let executions=0,block=true;
 const coordinator=new AutonomyCoordinator(db,async(_b,op,body:any)=>{
  if(op==='policy'||op==='snapshot')return{};assert.equal(op,'execute');executions++;
  assert.equal(body.action.goalId,undefined);assert.equal(body.action.target,'');
  return{status:block?'blocked':'completed',summary:'actual decision',effects:[],unfinished:[],evidence:[],usage:{tokens:10,modelCalls:1,toolCalls:0}};
 });t.after(()=>coordinator.stop());
 await repo.wake('tenant','bot',2,'operator-wake');await coordinator.tick();await coordinator.active?.task;
 assert.equal(executions,1);assert.equal((await repo.view('tenant','bot')).goals[0].status,'active');
 const changed=await repo.updateGoal(bot,{scope:'private:12345678',senderId:'12345678'},{goalId,expectedRevision:1,changes:{progress:'new user instruction/evidence'}});assert.equal(changed.status,'updated');
 block=false;await repo.wake('tenant','bot',2,'operator-wake');await coordinator.tick();await coordinator.active?.task;assert.equal(executions,2);
 const reviewed=await repo.view('tenant','bot');assert.equal(reviewed.goals[0].status,'active');assert.equal(reviewed.goals[0].revision,changed.goal.revision);assert.equal(reviewed.cycles[0].reason,'activity_completed');
 await coordinator.tick();await coordinator.active?.task;assert.equal(executions,2,'no automatic Goal execution or result replay');
 const done=await repo.updateGoal(bot,{scope:'private:12345678',senderId:'12345678'},{goalId,expectedRevision:changed.goal.revision,changes:{status:'completed'}});assert.equal(done.status,'updated');
});

test('legitimate main activity may update visible user goals while isolated research keeps source authority',async t=>{
 const {db,repo,bot}=await fixture(t);
 await db.query("INSERT INTO autonomy_cycles(id,bot_id,tenant_id,epoch,generation,config_revision,owner,state,work_expires_at,budget,decision) VALUES($1,'bot','tenant',2,$2,$3,'test','acting',now()+interval '5 minutes','{}','{\"reason\":\"handoff_due\"}')",[cycleId,bot.generation,bot.revision]);
 await db.query("UPDATE autonomy_settings SET lease_owner='test',lease_until=now()+interval '5 minutes' WHERE bot_id='bot'");
 const audience={scope:'self',senderId:'0',work:{id:actionId,epoch:2}},update={goalId,expectedRevision:1,changes:{progress:'current reasoning'}};
 assert.equal((await repo.updateGoal(bot,audience,update)).status,'rejected');
 await db.query("UPDATE autonomy_cycles SET decision='{\"reason\":\"autonomous_moment\"}' WHERE id=$1",[cycleId]);
 const changed=await repo.updateGoal(bot,audience,update);assert.equal(changed.status,'updated');assert.equal(changed.goal.source.owner,true);assert.equal(changed.goal.source.originalGoal.successCondition,'original constraints');
});
