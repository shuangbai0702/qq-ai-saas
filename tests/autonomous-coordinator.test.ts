import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';
import path from 'node:path';
import {openDatabase} from '../packages/database/index.js';
import {AutonomyCoordinator,taskResultFacts} from '../apps/node-agent/src/autonomous.js';

async function fixture(count=1){
 const root=await mkdtemp(path.resolve('.local/autonomous-coordinator-'));
 const db=await openDatabase(root).catch(async error=>{await rm(root,{recursive:true,force:true});throw error});
 const close=db.close.bind(db);db.close=async()=>{try{await close()}finally{await rm(root,{recursive:true,force:true})}};
 await db.query("INSERT INTO tenants(id,name) VALUES('tenant','tenant')");
 for(let n=0;n<count;n++)await db.query("INSERT INTO bots(id,tenant_id,name,protocol,desired_state,health) VALUES($1,'tenant',$1,'mock','running',$2)",['bot-'+n,JSON.stringify({sampledAt:new Date().toISOString(),protocol:{online:true}})]);
 return db;
}
const usage={modelCalls:1,toolCalls:0,tokens:400};

test('shared experience survives coordinator replacement, labels decisions and failures, and isolates members',async()=>{
 const db=await fixture(2);let count=0;
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy'||op==='snapshot')return{};
  if(op==='execute')return{status:'blocked',summary:'actual public service limit',toolResults:[{tool:'bilibili_search',status:'unavailable',detail:'cooldown'}],effects:[],evidence:[],unfinished:['read actual source'],usage};
  count++;return{status:'completed',usage,decision:{expectedGoalRevision:body.goalRevision,goalChanges:[],action:count===1?{kind:'research',objective:'inspect a public topic'}:null,summary:'retained actual failure',reason:count===1?'own interest':'choose rest after actual failure',wait:{kind:count===1?'after_result':'time',seconds:count===1?0:600}}};
 });
 try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active',fatigueEnabled:false});await coordinator.tick();await coordinator.active?.task;
  const replacement=new AutonomyCoordinator(db,async()=>({}));
  const history=await replacement.repo.experience('tenant','bot-0');
  assert.equal(history.actions[0].status,'blocked');assert.equal(history.actions[0].toolResults[0].tool,'bilibili_search');assert.equal(history.actions[0].effects.length,0);
  assert.ok(history.actions[0].assignedAt&&history.actions[0].finishedAt);assert.ok(history.decisions[0].at);assert.equal(history.decisions[0].action,null);
  assert.deepEqual((await replacement.repo.experience('tenant','bot-1')).actions,[]);
  assert.deepEqual((await replacement.repo.experience('different-tenant','bot-0')).decisions,[]);
  const view=await coordinator.repo.view('tenant','bot-0');await coordinator.repo.configure('tenant','bot-0',view.settings.epoch,{mode:'passive'});
  assert.ok((await coordinator.repo.policies()).some(b=>b.bot_id==='bot-0'),'mode switch hid durable experience from runtime recovery');
  await replacement.stop();
 }finally{await coordinator.stop();await db.close()}
});

for(const changedWhileThinking of [false,true])test('fresh inner feedback is loaded before thinking and fences a later change: '+changedWhileThinking,async()=>{
 const db=await fixture();const at=Date.now()-1000;let snapshots=0,executions=0;
 const feedback=(version:number)=>({at:at+version,phase:'foreground_result',eventId:'reply-'+version,scope:'private:12345678',senderId:'12345678',directed:true,result:{status:'sent',response:'actual feedback '+version}});
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy')return{};
  if(op==='snapshot'){
   snapshots++;const version=snapshots===1?1:snapshots>=3&&changedWhileThinking?3:2;
   return{feedbackVersion:at+version,foregroundFeedback:feedback(version),event:{at:at+10,eventId:'ambient',scope:'group:12345678',senderId:'23456789',directed:false,text:'unrelated traffic'}};
  }
  if(op==='execute'){executions++;return{status:'completed',summary:'fixture send',effects:[{id:'one',status:'confirmed',receipt:'actual-fixture'}],evidence:[],unfinished:[],usage}}
  assert.equal(op,'think');assert.equal(body.recentForeground.result.response,'actual feedback 2');assert.equal(body.work.feedbackVersion,at+2);
  return{status:'completed',usage,decision:{expectedGoalRevision:body.goalRevision,goalChanges:[],action:changedWhileThinking?{kind:'message',target:'private:12345678',objective:'do not dispatch stale choice'}:null,summary:'latest input only',reason:'own choice',wait:{kind:'time',seconds:600}}};
 });
 try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active'});await coordinator.tick();await coordinator.active?.task;
  const v=await coordinator.repo.view('tenant','bot-0');assert.equal(executions,0);
  assert.equal(v.settings.observation.eventId,'ambient');assert.equal(v.settings.observation.latestFeedback.result.response,'actual feedback '+(changedWhileThinking?3:2));
  assert.equal(v.cycles[0].reason,changedWhileThinking?'foreground_feedback_changed':'sleep');assert.equal(v.actions.length,0);
 }finally{await coordinator.stop();await db.close()}
});

test('autonomous activity facts preserve idle time, isolation and the choice to continue',async()=>{
 const db=await fixture(2);let decisions=0,executions=0;
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy'||op==='snapshot')return{};
  if(op==='execute'){executions++;return{status:'completed',summary:'actual fixture read',evidence:[{source:'fixture',reference:'read'}],effects:[],unfinished:[],usage}}
  assert.equal(op,'think');decisions++;
  assert.equal(body.autonomousActivity.attemptedTasks,executions);
  assert.equal(body.autonomousActivity.idleSinceLastTaskSeconds===null,executions===0);
  assert.ok(body.autonomousActivity.currentCycleSeconds>=0);
  // Recording prior effort must not stop a new model-selected task.
  return{status:'completed',usage,decision:{expectedGoalRevision:body.goalRevision,goalChanges:[],action:decisions<3?{kind:'research',objective:'continue a chosen topic'}:null,summary:'self-directed',reason:'own choice',wait:{kind:decisions<3?'after_result':'time',seconds:decisions<3?0:600}}};
 });
 try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active'});await coordinator.tick();await coordinator.active?.task;
  assert.equal(executions,2);assert.equal(decisions,3);
  await db.query("UPDATE autonomy_actions SET created_at=now()-interval '121 minutes',finished_at=now()-interval '120 minutes' WHERE bot_id='bot-0'");
  const activity=await coordinator.repo.activity('tenant','bot-0',60);
  assert.equal(activity.attemptedTasks,0);assert.equal(activity.recentTaskElapsedSeconds,0);assert.ok(activity.idleSinceLastTaskSeconds!>=7199);
  assert.equal((await coordinator.repo.activity('tenant','bot-1',60)).lastTaskFinishedAt,null);
  assert.equal((await coordinator.repo.activity('another-tenant','bot-0',60)).lastTaskFinishedAt,null);
  // A planning-only cycle does not manufacture activity or erase idle time.
  const next=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
   if(op==='policy'||op==='snapshot')return{};
   assert.equal(op,'think');assert.ok(body.autonomousActivity.idleSinceLastTaskSeconds>=7199);
   return{status:'completed',usage,decision:{expectedGoalRevision:body.goalRevision,goalChanges:[],action:null,summary:'rest',reason:'no action chosen',wait:{kind:'time',seconds:600}}};
  });
  await coordinator.stop();await next.repo.wake('tenant','bot-0',2,'fixture-recheck',0);await next.tick();await next.active?.task;await next.stop();
  assert.ok((await next.repo.activity('tenant','bot-0',60)).idleSinceLastTaskSeconds!>=7199);
 }finally{await coordinator.stop();await db.close()}
});

async function idleHistory(db:any){
 for(let n=0;n<2;n++)await db.query("INSERT INTO autonomy_cycles(id,bot_id,tenant_id,epoch,generation,config_revision,owner,state,budget,reason,decision,started_at,finished_at) VALUES($1,'bot-0','tenant',2,0,0,'old-owner','sleeping','{}','sleep',$2,now()-interval '12 hours'+($3*interval '1 minute'),now()-interval '20 minutes'+($3*interval '1 minute'))",['old-idle-'+n,JSON.stringify({action:null,summary:'nothing new; rest'}),n]);
}

test('intention and planning each repair their own contract once with actual diagnostic messages',async()=>{
 const db=await fixture();let orientations=0,plans=0;
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy'||op==='snapshot')return{};
  assert.equal(op,'think');
  if(body.phase==='orientation'){
   orientations++;assert.ok(Array.isArray(body.recentActivity));
   if(orientations===2){assert.equal(body.repair,true);assert.ok(body.validationErrors.some((e:any)=>e.path==='disposition'&&e.message));}
   return{status:'completed',usage,decision:{disposition:orientations===1?'invalid':'rest',motive:'consider current situation',desiredOutcome:'',reason:'own choice'}};
  }
  plans++;
  if(plans===2){assert.equal(body.repair,true);assert.ok(body.validationErrors.some((e:any)=>e.path==='action.goalId'&&e.message));}
  return{status:'completed',usage,decision:{expectedGoalRevision:body.goalRevision,goalChanges:[],action:plans===1?{kind:'research',goalId:'e4b9b7a1-8c32-4d56-a789-0123456789ab',objective:'invalid absent goal'}:null,summary:'considered',reason:'own choice',wait:{kind:'time',seconds:300}}};
 });
 try{await coordinator.repo.configure('tenant','bot-0',1,{mode:'active'});await idleHistory(db);await coordinator.tick();await coordinator.active?.task;const view=await coordinator.repo.view('tenant','bot-0');assert.equal(orientations,2);assert.equal(plans,2);assert.equal(view.cycles[0].reason,'sleep');assert.equal(view.cycles[0].usage.modelCalls,4);assert.equal(view.actions.length,0);}
 finally{await coordinator.stop();await db.close()}
});

test('a newer undispatched cancellation cannot hide the latest actual task feedback after recreation',async()=>{
 const db=await fixture();let decisions=0;
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy'||op==='snapshot')return{};
  assert.equal(op,'think');decisions++;
  assert.equal(body.feedback.task.actionId,'actual-finished');assert.equal(body.feedback.summary,'actual returned result');assert.equal(body.feedback.checkpoints[0].query,'actual public query');
  if(body.phase==='orientation'){assert.equal(body.recentActivity[0].status,'canceled');return{status:'completed',usage,decision:{disposition:'rest',motive:'consider real result',desiredOutcome:'',reason:'own choice'}};}
  return{status:'completed',usage,decision:{expectedGoalRevision:body.goalRevision,goalChanges:[],action:null,summary:'reviewed actual result',reason:'own choice',wait:{kind:'time',seconds:300}}};
 });
 try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active'});await idleHistory(db);
  for(const [id,status,result,age] of [['actual-finished','completed',{summary:'actual returned result',usage,effects:[],checkpoints:[{tool:'bilibili_search',query:'actual public query'}]},60],['canceled-undispatched','canceled',{summary:'not dispatched',effects:[]},10]] as const){
   await db.query("INSERT INTO autonomy_actions(id,cycle_id,bot_id,tenant_id,epoch,kind,target,objective,status,expires_at,result,created_at,finished_at) VALUES($1,'old-idle-0','bot-0','tenant',2,'research','','actual task',$2,now()+interval '1 hour',$3,now()-($4*interval '1 second'),now()-($4*interval '1 second'))",[id,status,JSON.stringify(result),age]);
  }
  await coordinator.tick();await coordinator.active?.task;const view=await coordinator.repo.view('tenant','bot-0');assert.equal(decisions,2);assert.equal(view.cycles[0].reason,'sleep');assert.equal(view.actions.length,2);
 }finally{await coordinator.stop();await db.close()}
});

test('real effort recovers over time while inactivity accumulates, independent of idle planning and restart',async()=>{
 const db=await fixture(2),coordinator=new AutonomyCoordinator(db,async()=>({}));
 try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active'});await idleHistory(db);
  await db.query("INSERT INTO autonomy_actions(id,cycle_id,bot_id,tenant_id,epoch,kind,target,objective,status,expires_at,result,created_at,finished_at) VALUES('real-effort','old-idle-0','bot-0','tenant',2,'research','','read real evidence','completed',now()+interval '1 hour',$1,now()-interval '120 seconds',now())",[JSON.stringify({usage,effects:[]})]);
  const warm=await coordinator.repo.activity('tenant','bot-0',60);assert.ok(warm.regulation.fatigue>0.09);assert.ok(warm.regulation.initiativePressure<0.01);
  await db.query("UPDATE autonomy_actions SET created_at=now()-interval '32 minutes',finished_at=now()-interval '30 minutes' WHERE id='real-effort'");
  const recovered=await coordinator.repo.activity('tenant','bot-0',60);assert.ok(recovered.regulation.fatigue<warm.regulation.fatigue*0.55);assert.ok(recovered.regulation.idleActivation>=0.49);
  await db.query("UPDATE autonomy_actions SET created_at=now()-interval '12 hours'-interval '120 seconds',finished_at=now()-interval '12 hours' WHERE id='real-effort'");
  const rested=await coordinator.repo.activity('tenant','bot-0',60);assert.ok(rested.regulation.fatigue<0.000001);assert.ok(rested.regulation.initiativePressure>0.99);assert.equal(rested.regulation.recentRestDecisions,2);
  const reopened=new AutonomyCoordinator(db,async()=>({}));assert.ok((await reopened.repo.activity('tenant','bot-0',60)).regulation.initiativePressure>0.99);await reopened.stop();
  assert.equal((await coordinator.repo.activity('tenant','bot-1',60)).regulation.initiativePressure,0);assert.equal((await coordinator.repo.activity('other-tenant','bot-0',60)).regulation.fatigue,0);
 }finally{await coordinator.stop();await db.close()}
});

for(const rest of [false,true])test('recovered idle robot forms a fresh model intention before planning; freedom to rest: '+rest,async()=>{
 const db=await fixture();let orientations=0,plans=0,executions=0;
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy'||op==='snapshot')return{};
  if(op==='execute'){executions++;return{status:'completed',summary:'actual isolated tool result',evidence:[{source:'fixture',reference:'read'}],effects:[],unfinished:[],usage}}
  assert.equal(op,'think');
  if(body.phase==='orientation'){
   orientations++;assert.ok(body.autonomousActivity.regulation.initiativePressure>0.99);assert.equal(body.summary,undefined);assert.equal(body.recentActions,undefined);
   return{status:'completed',usage,decision:{disposition:rest?'rest':'engage',motive:'new model-selected interest',desiredOutcome:rest?'':'learn a new fact',reason:'considered energy and current interests'}};
  }
  plans++;assert.equal(body.orientation.motive,'new model-selected interest');assert.equal(body.summary,'');
  if(plans===2)assert.equal(body.feedback.summary,'actual isolated tool result');
  return{status:'completed',usage,decision:{expectedGoalRevision:body.goalRevision,goalChanges:[],action:!rest&&plans===1?{kind:'research',objective:'model chooses its specific investigation'}:null,summary:'new state',reason:'own decision',wait:{kind:!rest&&plans===1?'after_result':'time',seconds:!rest&&plans===1?0:600}}};
 });
 try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active'});await idleHistory(db);await coordinator.tick();await coordinator.active?.task;
  const view=await coordinator.repo.view('tenant','bot-0');assert.equal(orientations,1);assert.equal(executions,rest?0:1);assert.equal(view.cycles[0].orientation.motive,'new model-selected interest');assert.equal(view.cycles[0].usage.modelCalls,rest?2:4);assert.equal(view.cycles[0].reason,'sleep');assert.equal(view.actions.length,rest?0:1);
 }finally{await coordinator.stop();await db.close()}
});

test('recent failed work cannot postpone intention review behind an activation threshold',async()=>{
 const db=await fixture();let orientations=0,plans=0;
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy'||op==='snapshot')return{};
  assert.equal(op,'think');
  if(body.phase==='orientation'){
   orientations++;assert.ok(body.autonomousActivity.regulation.initiativePressure>0.9,'failed work must retain accumulated inactivity');assert.ok(body.autonomousActivity.regulation.availableEnergy>0.9);
   return{status:'completed',usage,decision:{disposition:'rest',motive:'model reviews other opportunities after tool failure',desiredOutcome:'',reason:'chooses rest freely'}};
  }
  plans++;assert.ok(body.orientation);assert.equal(body.summary,'');
  return{status:'completed',usage,decision:{expectedGoalRevision:body.goalRevision,goalChanges:[],action:null,summary:'reviewed current options',reason:'model still chooses rest',wait:{kind:'time',seconds:600}}};
 });
 try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active'});await idleHistory(db);
  await db.query("INSERT INTO autonomy_actions(id,cycle_id,bot_id,tenant_id,epoch,kind,target,objective,status,expires_at,result,created_at,finished_at) VALUES('recent-failure','old-idle-0','bot-0','tenant',2,'research','','unavailable tool','blocked',now()+interval '1 hour',$1,now()-interval '5 seconds',now())",[JSON.stringify({usage,effects:[]})]);
  await coordinator.tick();await coordinator.active?.task;
  const view=await coordinator.repo.view('tenant','bot-0');assert.equal(orientations,1);assert.equal(plans,1);assert.equal(view.cycles[0].reason,'sleep');assert.equal(view.cycles[0].orientation.disposition,'rest');assert.equal(view.actions.length,1);
 }finally{await coordinator.stop();await db.close()}
});

for(const innerStatus of ['completed','budget_exhausted'])test('inner model allowance leaves the outer a real result assessment: '+innerStatus,async()=>{
 const db=await fixture();let plans=0,executions=0;
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy'||op==='snapshot')return{};
  if(op==='execute'){
   executions++;assert.equal(body.work.budget.modelCalls,4);
   return{status:innerStatus,summary:'actual tool result after consuming the inner allowance',effects:[],evidence:[],unfinished:[],usage:{tokens:1600,modelCalls:body.work.budget.modelCalls,toolCalls:2}};
  }
  assert.equal(op,'think');assert.equal(body.phase,undefined);plans++;
  if(plans===2){assert.equal(body.work.budget.modelCalls,1);assert.equal(body.feedback.status,innerStatus);assert.equal(body.feedback.summary,'actual tool result after consuming the inner allowance')}
  return{status:'completed',usage,decision:{expectedGoalRevision:body.goalRevision,goalChanges:[],action:plans===1?{kind:'research',objective:'bounded inner task'}:null,summary:'outer assessed actual result',reason:'real reassessment',wait:{kind:plans===1?'after_result':'time',seconds:plans===1?0:300}}};
 });
 try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active',cycleModelCalls:6});await coordinator.tick();await coordinator.active?.task;
  const view=await coordinator.repo.view('tenant','bot-0');assert.equal(plans,2);assert.equal(executions,1);assert.equal(view.settings.summary,'outer assessed actual result');assert.equal(view.cycles[0].usage.modelCalls,6);assert.equal(view.cycles[0].reason,'sleep');
 }finally{await coordinator.stop();await db.close()}
});

test('a blocked task reopens intention before the next plan without waiting for two rests',async()=>{
 const db=await fixture();let plans=0,orientations=0;
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy'||op==='snapshot')return{};
  if(op==='execute')return{status:'blocked',summary:'one tool temporarily unavailable',effects:[],evidence:[],unfinished:['first task'],usage};
  assert.equal(op,'think');
  if(body.phase==='orientation'){
   orientations++;assert.equal(body.feedback.status,'blocked');assert.equal(body.feedback.summary,'one tool temporarily unavailable');assert.equal(body.summary,undefined);
   return{status:'completed',usage,decision:{disposition:'rest',motive:'assess own options beyond the blocked task',desiredOutcome:'',reason:'free decision'}};
  }
  plans++;if(plans===2){assert.equal(body.orientation.motive,'assess own options beyond the blocked task');assert.equal(body.feedback.status,'blocked');assert.equal(body.summary,'')}
  return{status:'completed',usage,decision:{expectedGoalRevision:body.goalRevision,goalChanges:[],action:plans===1?{kind:'research',objective:'first task'}:null,summary:'considered alternatives',reason:'model choice',wait:{kind:plans===1?'after_result':'time',seconds:plans===1?0:300}}};
 });
 try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active'});await coordinator.tick();await coordinator.active?.task;
  const view=await coordinator.repo.view('tenant','bot-0');assert.equal(orientations,1);assert.equal(plans,2);assert.equal(view.cycles[0].orientation.disposition,'rest');assert.equal(view.cycles[0].reason,'sleep');
 }finally{await coordinator.stop();await db.close()}
});

test('a blocked task can yield to a different model-selected task and feed both results back',async()=>{
 const db=await fixture();let plans=0,executions=0,orientations=0;
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy'||op==='snapshot')return{};
  if(op==='execute'){
   executions++;assert.equal(body.action.objective,executions===1?'temporarily blocked direction':'different available direction');
   assert.ok(body.work.budget.modelCalls>=1);
   return{status:executions===1?'blocked':'completed',summary:executions===1?'one dependency unavailable':'different task returned actual facts',effects:[],evidence:[],unfinished:executions===1?['first task']:[],usage};
  }
  assert.equal(op,'think');
  if(body.phase==='orientation'){
   orientations++;assert.equal(body.feedback.status,'blocked');
   return{status:'completed',usage,decision:{disposition:'engage',motive:'model chooses another worthwhile direction',desiredOutcome:'learn something independently',reason:'first dependency is only locally unavailable'}};
  }
  plans++;if(plans===2)assert.equal(body.feedback.status,'blocked');if(plans===3)assert.equal(body.feedback.summary,'different task returned actual facts');
  return{status:'completed',usage,decision:{expectedGoalRevision:body.goalRevision,goalChanges:[],action:plans<3?{kind:'research',objective:plans===1?'temporarily blocked direction':'different available direction'}:null,summary:'new task assessed',reason:'own choice after actual result',wait:{kind:plans<3?'after_result':'time',seconds:plans<3?0:300}}};
 });
 try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active'});await coordinator.tick();await coordinator.active?.task;
  const view=await coordinator.repo.view('tenant','bot-0');assert.equal(orientations,1);assert.equal(plans,3);assert.equal(executions,2);assert.equal(view.actions.length,2);assert.deepEqual(view.actions.map(a=>a.status).sort(),['blocked','completed']);assert.equal(view.cycles[0].usage.modelCalls,6);assert.equal(view.cycles[0].reason,'sleep');
 }finally{await coordinator.stop();await db.close()}
});

test('an explicit two-request limit still executes work and resumes from its durable result',async()=>{
 const db=await fixture();let plans=0,executions=0;
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy'||op==='snapshot')return{};
  if(op==='execute'){executions++;assert.equal(body.work.budget.modelCalls,1);return{status:'completed',summary:'minimal-budget actual result',effects:[],evidence:[{source:'fixture_read',reference:'durable-result'}],toolResults:[{tool:'actual_read',status:'returned'}],checkpoints:[{tool:'actual_read',source:'fixture_read',value:'actual checkpoint'}],unfinished:[],usage}}
  assert.equal(op,'think');plans++;
  if(plans===2){assert.equal(body.feedback.summary,'minimal-budget actual result');assert.equal(body.feedback.status,'completed');assert.deepEqual(body.feedback.evidence,[{source:'fixture_read',reference:'durable-result'}]);assert.equal(body.feedback.toolResults[0].tool,'actual_read');assert.equal(body.feedback.task.kind,'research');assert.equal(body.feedback.task.objective,'minimal-budget task');assert.ok(body.feedback.task.actionId);assert.ok(body.feedback.task.finishedAt);assert.equal(body.feedback.checkpoints[0].value,'actual checkpoint');assert.deepEqual(body.recentActions,[])}
  return{status:'completed',usage,decision:{expectedGoalRevision:body.goalRevision,goalChanges:[],action:plans===1?{kind:'research',objective:'minimal-budget task'}:null,summary:'actual result reviewed',reason:'own next choice',wait:{kind:plans===1?'after_result':'time',seconds:plans===1?0:300}}};
 });
 try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active',cycleModelCalls:2});await coordinator.tick();await coordinator.active?.task;
  let view=await coordinator.repo.view('tenant','bot-0');assert.equal(executions,1);assert.equal(view.cycles[0].reason,'budget_exhausted');assert.equal(view.actions[0].status,'completed');assert.ok(new Date(view.settings.next_wake_at).getTime()-Date.now()<=16000,'budget exhaustion yields promptly instead of ordinary sleep');
  await coordinator.repo.wake('tenant','bot-0',view.settings.epoch,'fixture-next-cycle',0);await coordinator.tick();await coordinator.active?.task;
  view=await coordinator.repo.view('tenant','bot-0');assert.equal(plans,2);assert.equal(executions,1);assert.equal(view.cycles[0].reason,'sleep');
 }finally{await coordinator.stop();await db.close()}
});

test('switching to passive during intention formation prevents subsequent planning or action',async()=>{
 const db=await fixture();let planning=0;
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy'||op==='snapshot')return{};
  assert.equal(op,'think');assert.equal(body.phase,'orientation');planning++;
  await coordinator.repo.configure('tenant','bot-0',2,{mode:'passive'});
  return{status:'completed',usage,decision:{disposition:'engage',motive:'late intention',desiredOutcome:'must not execute',reason:'late result'}};
 });
 try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active'});await idleHistory(db);await coordinator.tick();await coordinator.active?.task;
  const view=await coordinator.repo.view('tenant','bot-0');assert.equal(planning,1);assert.equal(view.actions.length,0);assert.equal(view.settings.config.mode,'passive');assert.equal(view.cycles[0].orientation,null);assert.equal(view.cycles[0].usage.modelCalls,1);
 }finally{await coordinator.stop();await db.close()}
});

test('a partial day reservation bounds internal work while policy keeps its configured ceiling',async()=>{
 const db=await fixture();let calls=0;
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy'){assert.equal(body.config.cycleTokenBudget,6000);return{}};
  if(op==='snapshot')return{};
  assert.equal(op,'think');calls++;assert.equal(body.work.budget.tokens,5000);
  return{status:'completed',usage,decision:{expectedGoalRevision:body.goalRevision,goalChanges:[],action:null,summary:'resting',reason:'no task',wait:{kind:'time',seconds:1800}}};
 });
 try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active',cycleTokenBudget:6000,dailyTokenBudget:10000});
  await db.query("INSERT INTO autonomy_daily_usage(bot_id,tenant_id,day,used_tokens) VALUES('bot-0','tenant',(now() AT TIME ZONE 'Asia/Shanghai')::date,5000)");
  await coordinator.tick();await coordinator.active?.task;
  const view=await coordinator.repo.view('tenant','bot-0');assert.equal(calls,1);assert.equal(view.usage.used_tokens,5400);assert.equal(view.usage.reserved_tokens,0);assert.equal(view.settings.config.cycleTokenBudget,6000);
 }finally{await coordinator.stop();await db.close()}
});

test('historical spoken content cannot replace a confirmed protocol receipt',()=>{
 const facts=taskResultFacts('message',{status:'completed',summary:'I could not send anything',effects:[{id:'action-a:0',status:'confirmed',receipt:'real-id'}],contextScope:'private:12345678'})!;
 assert.match(facts.summary,/确认1，不明0/);assert.equal(facts.response,'I could not send anything');assert.equal(facts.effects[0].receipt,'real-id');
 const rejected=taskResultFacts('message',{status:'blocked',summary:'different action rejected',effects:[]})!;
 assert.match(rejected.summary,/状态blocked/);assert.equal(rejected.response,'');
 assert.equal(taskResultFacts('research',{status:'partial',summary:'actual read result',effects:[]})!.summary,'actual read result');
});

test('a failed observation commit remains eligible for the next bounded sample',async()=>{
 const db=await fixture(),at=Date.now();let attempts=0,outage=true;const acknowledgements:number[]=[];
 const event={at,eventId:'real-event',scope:'private:12345678',senderId:'12345678',directed:true,text:'actual incoming'};
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy'){acknowledgements.push(body.observedThrough);return{}};
  if(op==='snapshot')return{event};
  assert.equal(op,'think');
  return{status:'completed',usage,decision:{expectedGoalRevision:body.goalRevision,goalChanges:[],action:null,summary:'resting',reason:'no task',wait:{kind:'time',seconds:1800}}};
 });
 const persist=coordinator.repo.event.bind(coordinator.repo);
 coordinator.repo.event=async(...args)=>{attempts++;if(outage)throw new Error('controlled database outage');return persist(...args)};
 try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active'});
  await coordinator.tick();await coordinator.active?.task;
  assert.equal((await coordinator.repo.view('tenant','bot-0')).settings.last_event_at,null);
  assert.ok(acknowledgements.every(at=>at===0),'failed PG commit was acknowledged');
  const failedAttempts=attempts;outage=false;
  (coordinator as any).sampled.clear();
  await coordinator.tick();await coordinator.active?.task;
  const saved=(await coordinator.repo.view('tenant','bot-0')).settings;
  assert.equal(attempts,failedAttempts+1);assert.equal(saved.observation.eventId,event.eventId);
  assert.equal(new Date(saved.last_event_at).getTime(),at);
  (coordinator as any).sampled.clear();await coordinator.tick();await coordinator.active?.task;
  assert.equal(attempts,failedAttempts+1,'committed observations must not be repeatedly persisted');
  assert.equal(acknowledgements[0],0);assert.equal(acknowledgements[1],0,'failed PG commit was acknowledged');assert.equal(acknowledgements.at(-1),at);
 }finally{await coordinator.stop();await db.close()}
});

test('outer consumes actual inner feedback, changes its goal and durably sleeps',async()=>{
 const db=await fixture();let decisions=0,executions=0;
 const coordinator=new AutonomyCoordinator(db,async(bot,op,body:any)=>{
  if(op==='policy')return {epoch:bot.epoch};if(op==='snapshot')return {};
  if(op==='think'){
   decisions++;
   assert.ok(body.decisionSchema.required.includes('action'));
   assert.ok(body.decisionSchema.properties.wait.required.includes('seconds'));
   if(decisions===2){assert.deepEqual(body.feedback.evidenceLimits,['read receipt only, not verification of external facts']);assert.equal(body.feedback.summary,'verified fixture receipt');assert.equal(body.work.budget.modelCalls,21);assert.equal(body.goals[0].status,'active');assert.deepEqual(body.recentActions,[],'current feedback was repeated in action history')}
   return {status:'completed',usage,decision:{expectedGoalRevision:body.goalRevision,goalChanges:decisions===1?[{operation:'create',title:'independently chosen topic',reason:'interest'}]:[{operation:'complete',id:body.goals[0].id,reason:'actual inner evidence',progress:'checked receipt'}],action:decisions===1?{kind:'research',objective:'inspect actual context'}:null,summary:decisions===1?'exploring':'completed and resting',reason:'feedback-driven',wait:{kind:decisions===1?'after_result':'time',seconds:decisions===1?0:1800}}};
  }
  if(op==='execute'){executions++;return{status:'completed',summary:'verified fixture receipt',evidence:[{source:'fixture',reference:'checked'}],evidenceLimits:['read receipt only, not verification of external facts'],effects:[],unfinished:[],usage:{...usage,modelCalls:2,toolCalls:1,tokens:1200}}}
  throw new Error(op);
 });
 try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active'});await coordinator.tick();await coordinator.active?.task;
  const view=await coordinator.repo.view('tenant','bot-0');
  assert.equal(decisions,2);assert.equal(executions,1);assert.equal(view.goals[0].status,'completed');assert.equal(view.settings.state,'sleeping');assert.equal(view.usage.used_tokens,2000);assert.equal(view.usage.reserved_tokens,0);assert.equal(coordinator.active,undefined);
  assert.ok(new Date(view.settings.next_wake_at).getTime()>Date.now()+1700000);
  // A new supervisor and event wake load the durable state, no in-memory goal
  // cache or per-bot timer survives from the previous cycle.
  const next=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
   if(op==='policy'||op==='snapshot')return{};
   assert.equal(op,'think');assert.equal(body.goals[0].status,'completed');assert.equal(body.summary,'completed and resting');return{status:'completed',usage,decision:{expectedGoalRevision:body.goalRevision,goalChanges:[],action:null,summary:'restored',reason:'already done',wait:{kind:'event',seconds:3600}}};
  });
  await next.repo.wake('tenant','bot-0',2,'actual-event',0);await next.tick();await next.active?.task;await next.stop();
 }finally{await coordinator.stop();await db.close()}
});

test('new goal IDs are assigned by PostgreSQL; one format repair links the action and never invents a full token bill',async()=>{
 const db=await fixture();let decisions=0,executions=0;
 const fake='e4b9b7a1-8c32-4d56-a789-0123456789ab';
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy'||op==='snapshot')return{};
  if(op==='think'){
   decisions++;
   if(decisions===2){assert.equal(body.repair,true);assert.ok(body.validationErrors.some((issue:any)=>issue.path==='goalChanges.0.id'));}
   return{status:'completed',usage,decision:{expectedGoalRevision:body.goalRevision,goalChanges:decisions<3?[{operation:'create',...(decisions===1?{id:fake}:{}),title:'model chosen',reason:'interest'}]:[],action:decisions<3?{kind:'research',...(decisions===1?{goalId:fake}:{}),objective:'actual read'}:null,summary:'progress',reason:'chosen',wait:{kind:decisions<3?'after_result':'time',seconds:decisions<3?0:1800}}};
  }
  if(op==='execute'){executions++;assert.notEqual(body.action.goalId,fake);assert.ok(body.action.goalId);return{status:'completed',summary:'actual read',evidence:[{source:'fixture',reference:'read'}],effects:[],unfinished:[],usage};}
  throw new Error(op);
 });
 try{await coordinator.repo.configure('tenant','bot-0',1,{mode:'active'});await coordinator.tick();await coordinator.active?.task;const view=await coordinator.repo.view('tenant','bot-0');assert.equal(decisions,3);assert.equal(executions,1);assert.equal(view.goals.length,1);const[stored]=await db.query('SELECT goal_id FROM autonomy_actions WHERE id=$1',[view.actions[0].id]);assert.equal(stored.goal_id,view.goals[0].id);assert.equal(view.usage.used_tokens,1600);}
 finally{await coordinator.stop();await db.close()}
});

test('partial read-only research returns actual evidence to the outer instead of waiting for a nonexistent send receipt',async()=>{
 const db=await fixture();let decisions=0,orientations=0;
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy'||op==='snapshot')return{};
  if(op==='think'){
   if(body.phase==='orientation'){orientations++;assert.equal(body.feedback.status,'partial');assert.deepEqual(body.feedback.evidence,[{source:'fixture_read',reference:'actual'}]);return{status:'completed',usage,decision:{disposition:'rest',motive:'review alternatives',desiredOutcome:'',reason:'partial facts considered'}}}
   decisions++;
   if(decisions===2)assert.equal(body.feedback.status,'partial');
   return{status:'completed',usage,decision:{expectedGoalRevision:body.goalRevision,goalChanges:[],action:decisions===1?{kind:'research',objective:'read partial context'}:null,summary:'known partial result',reason:'choose again from actual feedback',wait:{kind:decisions===1?'after_result':'time',seconds:decisions===1?0:1800}}};
  }
  if(op==='execute')return{status:'partial',summary:'only part is available',evidence:[{source:'fixture_read',reference:'actual'}],effects:[],unfinished:['remote source unavailable'],usage};
  throw new Error(op);
 });
 try{await coordinator.repo.configure('tenant','bot-0',1,{mode:'active'});await coordinator.tick();await coordinator.active?.task;const view=await coordinator.repo.view('tenant','bot-0');assert.equal(decisions,2);assert.equal(orientations,1);assert.equal(view.cycles[0].reason,'sleep');assert.equal(view.actions[0].status,'partial');}
 finally{await coordinator.stop();await db.close()}
});

test('unknown effect is recorded once and cannot be reissued by a later decision',async()=>{
 const db=await fixture();let executions=0;
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy'||op==='snapshot')return{};
  if(op==='think')return{status:'completed',usage,decision:{expectedGoalRevision:body.goalRevision,goalChanges:[],action:{kind:'message',target:'private:12345678',objective:'chosen greeting'},summary:'contact',reason:'actual interest',wait:{kind:'after_result',seconds:0}}};
  if(op==='execute'){executions++;return{status:'unknown',summary:'unconfirmed actual effect',effects:[{id:body.work.id+':0',status:'unknown'}],evidence:[],unfinished:['verify'],usage}};
  throw new Error(op);
 });
 try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active',rollout:'live'});
  await coordinator.tick();await coordinator.active?.task;
  await coordinator.repo.wake('tenant','bot-0',2,'later',0);await coordinator.tick();await coordinator.active?.task;
  const view=await coordinator.repo.view('tenant','bot-0');assert.equal(executions,1);assert.ok(view.actions.some(a=>a.status==='unknown'));assert.ok(view.actions.some(a=>a.status==='canceled'));assert.equal(view.usage.external_actions,1);
 }finally{await coordinator.stop();await db.close()}
});

test('one hundred sleeping robots have no active work or per-bot timers; offline wake never starts QQ',async()=>{
 const db=await fixture(100);let modelCalls=0;
 const coordinator=new AutonomyCoordinator(db,async(_bot,op)=>{if(op==='think'||op==='execute')modelCalls++;return {}});
 try{
  for(let n=0;n<100;n++){
   await coordinator.repo.configure('tenant','bot-'+n,1,{mode:'active'});
   await db.query("UPDATE autonomy_wakes SET due_at=now()+interval '1 day' WHERE bot_id=$1",['bot-'+n]);
  }
  await db.query("UPDATE bots SET desired_state='offline' WHERE id='bot-0'");await coordinator.repo.wake('tenant','bot-0',2,'offline-event',0);
  await coordinator.tick();assert.equal(modelCalls,0);assert.equal(coordinator.active,undefined);assert.equal(coordinator.timer,undefined);
  assert.equal((await coordinator.repo.policies()).length,100);assert.equal((await db.query("SELECT desired_state FROM bots WHERE id='bot-0'"))[0].desired_state,'offline');
 }finally{await coordinator.stop();await db.close()}
});

test('passive mode settles old receipts without a model call, new wake or repeat effect',async()=>{
 const db=await fixture();let actionId='',receiptCalls=0,executions=0,settled=false;
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy'||op==='snapshot')return{};
  if(op==='think')return{status:'completed',usage,decision:{expectedGoalRevision:body.goalRevision,goalChanges:[],action:{kind:'message',target:'private:12345678',objective:'one greeting'},summary:'contact',reason:'selected',wait:{kind:'after_result',seconds:0}}};
  if(op==='execute'){executions++;actionId=body.work.id;return{status:'unknown',summary:'protocol response lost',effects:[{id:actionId+':0',status:'unknown'}],evidence:[],unfinished:['verify'],usage}};
  if(op==='receipt'){receiptCalls++;assert.equal(body.id,actionId);return settled?{result:{status:'completed',summary:'late authoritative receipt',effects:[{id:actionId+':0',status:'confirmed',receipt:'late-real-id'}],evidence:[],unfinished:[],usage}}:{result:{status:'unknown',effects:[{id:actionId+':0',status:'unknown'}]}}}
  throw new Error(op);
 });
 try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active',rollout:'live'});
  await coordinator.tick();await coordinator.active?.task;assert.equal(executions,1);
  await coordinator.repo.configure('tenant','bot-0',2,{mode:'passive'});
  (coordinator as any).sampled.clear();await coordinator.tick();assert.equal(receiptCalls,1);assert.equal(coordinator.active,undefined);
  assert.equal((await coordinator.repo.policies()).length,1,'pending old receipt disappeared once passive policy was applied');
  settled=true;(coordinator as any).sampled.clear();await coordinator.tick();
  const view=await coordinator.repo.view('tenant','bot-0');assert.equal(view.actions[0].status,'completed');assert.equal(view.actions[0].result.effects[0].receipt,'late-real-id');assert.equal(view.settings.config.mode,'passive');assert.equal(view.settings.runtime_epoch,3);assert.equal(executions,1);assert.equal(receiptCalls,2);
  const policies=await coordinator.repo.policies();assert.equal(policies.length,1,'durable history remains readable in passive mode');assert.equal(policies[0].config.mode,'passive');assert.equal((await db.query('SELECT * FROM autonomy_wakes')).length,0);assert.equal((await db.query('SELECT * FROM commands')).length,0);
 }finally{await coordinator.stop();await db.close()}
});

test('known partial read results cannot starve uncertain external receipts',async()=>{
 const db=await fixture(),coordinator=new AutonomyCoordinator(db,async()=>({}));
 try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active'});await coordinator.repo.applied('tenant','bot-0',2);
  const claim=await coordinator.repo.claim('fixture-owner');assert.ok(claim);
  for(let n=0;n<20;n++){
   await db.query("INSERT INTO autonomy_actions(id,cycle_id,bot_id,tenant_id,epoch,kind,target,objective,status,expires_at,result) VALUES($1,$2,'bot-0','tenant',2,'research','','read','partial',now()+interval '1 day',$3)",['read-'+n,claim.cycleId,JSON.stringify({status:'partial',effects:[],unfinished:['unavailable source']})]);
  }
  await db.query("INSERT INTO autonomy_actions(id,cycle_id,bot_id,tenant_id,epoch,kind,target,objective,status,expires_at,result) VALUES('uncertain',$1,'bot-0','tenant',2,'message','private:12345678','contact','partial',now()+interval '1 day',$2)",[claim.cycleId,JSON.stringify({status:'partial',effects:[{id:'uncertain:0',status:'confirmed'},{id:'uncertain:1',status:'unknown'}]})]);
  assert.deepEqual((await coordinator.repo.unconfirmed('tenant','bot-0')).map(a=>a.id),['uncertain']);assert.equal((await coordinator.repo.unconfirmed('other-tenant','bot-0')).length,0);
  await coordinator.repo.configure('tenant','bot-0',2,{mode:'passive'});await coordinator.repo.applied('tenant','bot-0',3);
  assert.equal((await coordinator.repo.policies()).length,1);
 }finally{await coordinator.stop();await db.close()}
});

test('health expiring during a model await rejects the late decision and grants no external work',async()=>{
 const db=await fixture();let entered!:()=>void,release!:()=>void,calls=0;
 const atModel=new Promise<void>(r=>entered=r),resume=new Promise<void>(r=>release=r);
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy'||op==='snapshot')return{};
  assert.equal(op,'think');calls++;entered();await resume;
  return{status:'completed',usage,decision:{expectedGoalRevision:body.goalRevision,goalChanges:[],action:{kind:'message',target:'private:12345678',objective:'late intent must not dispatch'},summary:'late model result',reason:'test boundary',wait:{kind:'after_result',seconds:0}}};
 });
 try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active',rollout:'live'});await coordinator.tick();await atModel;
  await db.query("UPDATE bots SET health=$1 WHERE id='bot-0'",[JSON.stringify({sampledAt:new Date(Date.now()-120000).toISOString(),protocol:{online:true}})]);release();await coordinator.active?.task;
  const view=await coordinator.repo.view('tenant','bot-0');assert.equal(view.actions.length,0);assert.equal(view.cycles[0].reason,'decision_conflict');assert.equal(view.usage.used_tokens,400);
  await coordinator.tick();assert.equal(calls,1);assert.equal((await coordinator.repo.view('tenant','bot-0')).settings.state,'suspended_offline');assert.equal((await db.query('SELECT * FROM commands')).length,0);
 }finally{release();await coordinator.stop();await db.close()}
});


test('a durable task notification bypasses only the bounded observation sampling delay',async()=>{
 const db=await fixture();let pending=false,samples=0;
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy')return{pendingHandoffs:pending};
  if(op==='snapshot'){samples++;return{}}
  assert.equal(op,'think');return{status:'completed',usage,decision:{expectedGoalRevision:body.goalRevision,goalChanges:[],action:null,summary:'resting',reason:'no task',wait:{kind:'time',seconds:1800}}};
 });
 try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active'});
  await coordinator.tick();await coordinator.active?.task;assert.equal(samples,3,'cycle requires a bounded sample plus fresh pre/post decision feedback');
  await coordinator.tick();assert.equal(samples,3,'ordinary polling lost its sampling bound');
  pending=true;await coordinator.tick();assert.equal(samples,4,'accepted task waited for the ambient observation timer');
 }finally{await coordinator.stop();await db.close()}
});

test('outer owns accepted task, inner executes then returns a source-bound result for reassessment',async()=>{
 const db=await fixture();let decisions=0;const executed:any[]=[];
 const id='b1786cd0-91c4-40ec-8e42-75d80ae70a9d',source={handoffId:id,eventId:'real-request',scope:'private:12345678',senderId:'12345678',owner:true};
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy'||op==='snapshot')return{};
  if(op==='execute'){executed.push(body.action);return{status:'completed',usage,summary:'actual fixture result',effects:body.action.kind==='message'?[{id:body.work.id+':0',status:'confirmed',receipt:'fixture-result-ack'}]:[],evidence:[{source:'fixture',reference:'actual-read'}],contextScope:body.action.kind==='research'?'self':source.scope}}
  assert.equal(op,'think');decisions++;
  if(decisions===2)assert.equal(body.feedback.summary,'actual fixture result');
  if(decisions===3)assert.equal(body.feedback.effects[0].receipt,'fixture-result-ack');
  return{status:'completed',usage,decision:{expectedGoalRevision:body.goalRevision,goalChanges:decisions===3?[{operation:'complete',id,reason:'actual result delivered'}]:[],action:decisions<3?{kind:decisions===1?'research':'message',goalId:id,target:decisions===1?'':source.scope,objective:decisions===1?'perform the accepted task':'return its real result'}:null,summary:'feedback-driven',reason:'task then reassess',wait:{kind:decisions<3?'after_result':'time',seconds:decisions<3?0:1800}}};
 });
 try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active',rollout:'live'});
  await db.query("INSERT INTO autonomy_goals(id,bot_id,tenant_id,origin,status,title,reason,progress,success_condition,source) VALUES($1,'bot-0','tenant','owner_request','active','real task','accepted fixture','','verified result delivered',$2)",[id,JSON.stringify(source)]);
  await coordinator.tick();await coordinator.active?.task;
  assert.equal(decisions,3);assert.equal(executed.length,2);assert.equal(executed[0].requestSource,undefined);
  assert.deepEqual(executed[1].requestSource,source);assert.equal(executed[1].goalId,id);
  assert.equal((await coordinator.repo.view('tenant','bot-0')).goals[0].status,'completed');
 }finally{await coordinator.stop();await db.close()}
});


test('unlimited daily policy keeps actual accounting and bounded cycles after prior daily use',async()=>{
 const db=await fixture();let requests=0;
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy'||op==='snapshot')return{};
  assert.equal(op,'think');requests++;assert.equal(body.work.budget.tokens,500000);assert.equal(_bot.config.minSleepSeconds,300);
  return{status:'completed',usage,decision:{expectedGoalRevision:body.goalRevision,goalChanges:[],action:null,summary:'resting',reason:'nothing useful now',wait:{kind:'time',seconds:600}}};
 });
 try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active',dailyTokenBudget:0});
  await db.query("INSERT INTO autonomy_daily_usage(bot_id,tenant_id,day,used_tokens) VALUES('bot-0','tenant',(now() AT TIME ZONE 'Asia/Shanghai')::date,250000)");
  await coordinator.tick();await coordinator.active?.task;
  const v=await coordinator.repo.view('tenant','bot-0');assert.equal(requests,1);assert.equal(v.settings.state,'sleeping');assert.equal(v.usage.used_tokens,250400);assert.equal(v.usage.reserved_tokens,0);assert.equal(v.settings.config.cycleTokenBudget,500000);
 }finally{await coordinator.stop();await db.close()}
});


test('a failed attempt consumes effort but does not reset time without actual progress',async()=>{
 const db=await fixture(),coordinator=new AutonomyCoordinator(db,async()=>({}));try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active'});await idleHistory(db);
  await db.query("INSERT INTO autonomy_actions(id,cycle_id,bot_id,tenant_id,epoch,kind,target,objective,status,expires_at,result,created_at,finished_at) VALUES('old-progress','old-idle-0','bot-0','tenant',2,'research','','actual result','completed',now(),$1,now()-interval '42 minutes',now()-interval '40 minutes'),('failed-retry','old-idle-0','bot-0','tenant',2,'research','','unavailable service','blocked',now(),$1,now()-interval '60 seconds',now())",[JSON.stringify({usage,effects:[]})]);
  const info=await coordinator.repo.activity('tenant','bot-0',60);
  assert.ok(info.lastTaskFinishedAt);assert.ok(info.lastProgressAt);assert.ok(info.idleSinceProgressSeconds!>=2399);assert.ok(info.idleSinceLastTaskSeconds!<5);assert.ok(info.regulation.idleActivation>0.59);assert.ok(info.regulation.fatigue>0);
 }finally{await coordinator.stop();await db.close()}
});


test('a canceled inner task yields briefly and resumes evaluation instead of a full idle sleep',async()=>{
 const db=await fixture();let executions=0;const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy'||op==='snapshot')return{};
  if(op==='execute'){executions++;return{status:'canceled',summary:'foreground invalidated work before side effects',effects:[],evidence:[],unfinished:['continue later'],usage:{modelCalls:0,toolCalls:0,tokens:0}}}
  return{status:'completed',usage,decision:{expectedGoalRevision:body.goalRevision,goalChanges:[],action:{kind:'research',objective:'real task'},summary:'continue',reason:'work selected',wait:{kind:'after_result',seconds:0}}};
 });try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active'});await coordinator.tick();await coordinator.active?.task;
  const view=await coordinator.repo.view('tenant','bot-0');assert.equal(executions,1);assert.equal(view.cycles[0].reason,'foreground_or_mode_changed');assert.equal(view.actions[0].status,'canceled');
  const delay=new Date(view.settings.next_wake_at).getTime()-Date.now();assert.ok(delay>0&&delay<=16000,'foreground interruption must not turn into a 5-minute idle sleep');
 }finally{await coordinator.stop();await db.close()}
});

for(const reason of ['request_token_budget','model_call_budget','tool_call_budget','time_budget'])test('outer preflight yield does not inherit the ordinary idle interval: '+reason,async()=>{
 const db=await fixture();const coordinator=new AutonomyCoordinator(db,async(_bot,op)=>{
  if(op==='policy'||op==='snapshot')return{};
  assert.equal(op,'think');return{status:'budget_exhausted',reason,usage:{modelCalls:0,toolCalls:0,tokens:0}};
 });try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active',minSleepSeconds:7200});await coordinator.tick();await coordinator.active?.task;
  const view=await coordinator.repo.view('tenant','bot-0');assert.equal(view.cycles[0].reason,reason);assert.equal(view.settings.state,'backoff');assert.equal(view.actions.length,0);
  const delay=new Date(view.settings.next_wake_at).getTime()-Date.now();assert.ok(delay>0&&delay<=16000,'uncommitted decisions must yield rather than sleep for two hours');
 }finally{await coordinator.stop();await db.close()}
});

test('failed outer request uses an error backoff rather than a model rest interval',async()=>{
 const db=await fixture();const coordinator=new AutonomyCoordinator(db,async(_bot,op)=>{
  if(op==='policy'||op==='snapshot')return{};
  throw new Error('test provider request failed');
 });try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active',minSleepSeconds:7200});await coordinator.tick();await coordinator.active?.task;
  const view=await coordinator.repo.view('tenant','bot-0');assert.equal(view.cycles[0].reason,'runtime_or_decision_failed');assert.equal(view.settings.state,'backoff');
  const delay=new Date(view.settings.next_wake_at).getTime()-Date.now();assert.ok(delay>16000&&delay<=31000);
 }finally{await coordinator.stop();await db.close()}
});

test('a completed task can exceed the configured inactivity interval and still return to the outer',async()=>{
 const db=await fixture();let elapsed=0,decisions=0;const realNow=Date.now;
 const coordinator:AutonomyCoordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy'||op==='snapshot')return{};
  assert.equal(body.work.budget.seconds,30);assert.ok(body.work.expiresAt>Date.now()+30000,'work identity must cover its remaining bounded steps');
  if(op==='execute'){
   // The independent Agent health sampler continues while work advances.
   elapsed=20000;
   await db.query("UPDATE bots SET health=$1 WHERE id='bot-0'",[JSON.stringify({sampledAt:new Date(Date.now()).toISOString(),protocol:{online:true}})]);
   assert.equal(await coordinator.repo.renew(coordinator.active!.claim),true);
   elapsed=31000;
   await db.query("UPDATE bots SET health=$1 WHERE id='bot-0'",[JSON.stringify({sampledAt:new Date(Date.now()).toISOString(),protocol:{online:true}})]);
   return{status:'completed',summary:'actual task completed after ongoing steps',effects:[],evidence:[],unfinished:[],usage};
  }
  decisions++;if(decisions===2)assert.equal(body.feedback.summary,'actual task completed after ongoing steps');
  return{status:'completed',usage,decision:{expectedGoalRevision:body.goalRevision,goalChanges:[],action:decisions===1?{kind:'research',objective:'ongoing useful work'}:null,summary:'observed result',reason:'own choice',wait:{kind:decisions===1?'after_result':'time',seconds:decisions===1?0:600}}};
 });try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active',cycleSeconds:30});Date.now=()=>realNow()+elapsed;
  await coordinator.tick();await coordinator.active?.task;
  const view=await coordinator.repo.view('tenant','bot-0');assert.equal(decisions,2,JSON.stringify({cycle:view.cycles[0],action:view.actions[0]}));assert.equal(view.cycles[0].reason,'sleep');assert.equal(view.actions[0].status,'completed');
 }finally{Date.now=realNow;await coordinator.stop();await db.close()}
});
