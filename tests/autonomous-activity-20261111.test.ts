import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';
import path from 'node:path';
import {openDatabase} from '../packages/database/index.js';
import {AutonomyCoordinator} from '../apps/node-agent/src/autonomous.js';
import {AutonomyConfigSchema,TaskHandoffSchema} from '../packages/contracts/autonomous.js';

async function fixture(){
 const root=await mkdtemp(path.resolve('.local/activity-test-')),db=await openDatabase(root);
 await db.query("INSERT INTO tenants(id,name) VALUES('t','t')");
 await db.query("INSERT INTO bots(id,tenant_id,name,protocol,desired_state,health) VALUES('b','t','b','mock','running',$1)",[JSON.stringify({sampledAt:new Date().toISOString(),protocol:{online:true}})]);
 await db.query("INSERT INTO bot_config_revisions(bot_id,revision,config) SELECT id,revision,'{}' FROM bots");
 return {db,close:async()=>{await db.close();await rm(root,{recursive:true,force:true})}};
}
test('activity uses one durable wake, native budget and logical cycle without a fake goal; rest survives restart',async()=>{
 const f=await fixture();let n=0;const c=new AutonomyCoordinator(f.db,async(_b,op,body:any)=>{
  if(op==='policy'||op==='snapshot')return{};
  assert.equal(op,'execute');n++;assert.equal(body.action.autonomousMoment,true);assert.equal(body.action.goalId,undefined);
  assert.deepEqual(body.work.budget,{modelCalls:2,toolCalls:3,tokens:9000,seconds:90,externalActions:0});
  return{status:'completed',summary:'休息',effects:[],evidence:[],unfinished:[],restSeconds:601,usage:{tokens:300,modelCalls:2,toolCalls:1}};
 });
 try{
  await c.repo.configure('t','b',1,{mode:'active',activityModelCalls:2,activityToolCalls:3,activityTokenBudget:9000,activityExternalActions:0});
  await c.tick();await c.active?.task;assert.equal(n,0,'ordinary config wake cannot impersonate timed activity');
  for(let i=0;i<3;i++)await c.repo.scheduleActivity({tenant_id:'t',bot_id:'b',epoch:2,config:AutonomyConfigSchema.parse({mode:'active'})});
  assert.equal((await f.db.query("SELECT count(*)::int n FROM autonomy_wakes WHERE reason='activity'"))[0].n,1);
  await f.db.query("UPDATE autonomy_wakes SET due_at=now()-interval '1 second' WHERE reason='activity'");
  await c.tick();await c.active?.task;assert.equal(n,1);
  const view=await c.repo.view('t','b');assert.equal(view.goals.length,0);assert.equal(view.actions.length,1);assert.equal(view.cycles[0].usage.modelCalls,2);assert.equal(view.cycles[0].state,'completed');
  const [wake]=await f.db.query("SELECT extract(epoch FROM due_at-now()) AS wait FROM autonomy_wakes WHERE reason='activity'");assert.ok(Number(wake.wait)>590&&Number(wake.wait)<=601);
  const restarted=new AutonomyCoordinator(f.db,async()=>{throw Error('must not wake early')});await restarted.tick();assert.equal(restarted.active,undefined);await restarted.stop();
  const parent=view.actions[0].id;
  const source={id:'c871187b-951f-44d0-a119-06351a5fbd03',tenantId:'t',botId:'b',generation:1,revision:1,epoch:2,eventId:parent,scope:'self',senderId:'0',at:Date.now(),objective:'后续研究',delaySeconds:300,kind:'research',origin:'model',parentActionId:parent};
  const bot=(await c.repo.policies())[0];source.generation=bot.generation;source.revision=bot.revision;
  const accepted=await c.repo.acceptHandoff(bot,source);assert.equal(accepted.status,'accepted',JSON.stringify(accepted));
  assert.equal((await c.repo.acceptHandoff(bot,source)).status,'accepted','idempotent retry');
  assert.equal((await c.repo.acceptHandoff(bot,{...source,id:'d871187b-951f-44d0-a119-06351a5fbd03',parentActionId:'e871187b-951f-44d0-a119-06351a5fbd03',eventId:'e871187b-951f-44d0-a119-06351a5fbd03'})).status,'rejected','forged parent');
 }finally{await c.stop();await f.close()}
});
test('busy activity skips and rearms once without a model request or 15-second poll',async()=>{
 const f=await fixture();const c=new AutonomyCoordinator(f.db,async(_b,op)=>{if(op==='snapshot')return{foregroundBusy:true};if(op==='policy')return{};throw Error('no execution')});
 try{await c.repo.configure('t','b',1,{mode:'active'});await c.tick();await c.active?.task;
 await f.db.query("UPDATE autonomy_wakes SET due_at=now()-interval '1 second' WHERE reason='activity'");await c.tick();await c.active?.task;
 assert.equal((await f.db.query('SELECT count(*)::int n FROM autonomy_actions'))[0].n,0);
 const rows=await f.db.query("SELECT reason,extract(epoch FROM due_at-now()) wait FROM autonomy_wakes");assert.equal(rows.length,1);assert.equal(rows[0].reason,'activity');assert.ok(Number(rows[0].wait)>295);
 }finally{await c.stop();await f.close()}
});
test('interval cannot be less than five minutes and internal provenance is strict',()=>{
 assert.equal(AutonomyConfigSchema.safeParse({activityIntervalMinutes:4}).success,false);
 assert.equal(TaskHandoffSchema.safeParse({scope:'self',senderId:'0'}).success,false);
});

test('activity preserves IPC failure and conservative ceiling instead of reporting budget exhaustion',async()=>{
 const f=await fixture();let calls=0;
 const c=new AutonomyCoordinator(f.db,async(_b,op)=>{
  if(op==='execute'){calls++;throw Error('autonomous_ipc_409')}
  if(op==='receipt')return null;
  return{};
 });
 try{
  await c.repo.configure('t','b',1,{mode:'active',cycleTokenBudget:9000,activityTokenBudget:9000,cycleModelCalls:2,activityModelCalls:2});
  await c.tick();await c.active?.task;
  await f.db.query("UPDATE autonomy_wakes SET due_at=now()-interval '1 second' WHERE reason='activity'");
  await c.tick();await c.active?.task;
  const view=await c.repo.view('t','b'),cycle=view.cycles.find((x:any)=>x.reason==='autonomous_ipc_409');
  assert.equal(calls,1);assert.ok(cycle,JSON.stringify(view.cycles));
  assert.equal(cycle.fault.class,'protocol');assert.equal(cycle.fault.phase,'activity_execute');
  assert.equal(cycle.usage.tokens,9000);assert.equal(cycle.usage.modelCalls,2);assert.equal(cycle.usage.conservative,true);
  assert.equal(cycle.usage.charges[0].estimateReason,'execution_outcome_unknown');
  assert.equal(view.actions[0].status,'unknown');
 }finally{await c.stop();await f.close()}
});


test('due goals coalesce into one main activation without selecting a goal or replaying its fields',async()=>{
 const f=await fixture();let n=0;
 const c=new AutonomyCoordinator(f.db,async(_b,op,body:any)=>{
  if(op==='policy'||op==='snapshot')return{};
  if(op==='execute'){
   n++;assert.equal(body.action.autonomousMoment,true);assert.equal(body.action.activationTrigger,'handoff-due');
   assert.equal(body.action.goalId,undefined);assert.equal(body.action.target,'');assert.equal(body.action.goalSnapshot,undefined);
   assert.ok(!JSON.stringify(body).includes('PRIVATE_GOAL_PAYLOAD'));
   return{status:'completed',effects:[],evidence:[],unfinished:[],usage:{modelCalls:1,toolCalls:0,tokens:200}};
  }throw Error(op);
 });
 try{
  await c.repo.configure('t','b',1,{mode:'active',activityEnabled:false});
  const bot=(await c.repo.policies())[0];
  for(const id of ['00000000-0000-4000-8000-000000000001','00000000-0000-4000-8000-000000000002'])
   await f.db.query("INSERT INTO autonomy_goals(id,bot_id,tenant_id,origin,status,title,reason,success_condition,source) VALUES($1,'b','t','owner_request','active','PRIVATE_GOAL_PAYLOAD','why','condition',$2)",[id,JSON.stringify({handoffId:id,scope:'private:12345678',senderId:'12345678',owner:true,notBefore:Date.now()-1000})]);
  const before=await f.db.query('SELECT * FROM autonomy_goals ORDER BY id');
  for(const goal of before)await c.repo.wake('t','b',bot.epoch,'handoff-due',0,goal.id);
  c.repo.readGoals=async()=>{throw Error('No automatic Goal snapshot')};
  c.repo.dueHandoffs=async()=>{throw Error('No Goal selection')};
  c.repo.planHandoff=async()=>{throw Error('No Goal dispatch')};
  await c.tick();await c.active?.task;assert.equal(n,1);
  assert.deepEqual(await f.db.query('SELECT * FROM autonomy_goals ORDER BY id'),before);
  const rows=await f.db.query('SELECT goal_id,kind,target FROM autonomy_actions');assert.deepEqual(rows,[{goal_id:null,kind:'research',target:''}]);
  await c.tick();await c.active?.task;assert.equal(n,1,'consumed due wakes do not loop over retained goals');
 }finally{await c.stop();await f.close()}
});

test('busy due wake keeps its original durable identity without starting a model or dispatch',async()=>{
 const f=await fixture();const c=new AutonomyCoordinator(f.db,async(_b,op)=>{if(op==='snapshot')return{foregroundBusy:true};if(op==='policy')return{};throw Error('no execution')});
 try{
  await c.repo.configure('t','b',1,{mode:'active',activityEnabled:false});
  const bot=(await c.repo.policies())[0];await c.repo.wake('t','b',bot.epoch,'handoff-due',0,'exact-goal');
  await c.tick();await c.active?.task;
  assert.equal((await f.db.query('SELECT count(*)::int n FROM autonomy_actions'))[0].n,0);
  const wakes=await f.db.query("SELECT reason,scope,extract(epoch FROM due_at-now()) AS wait FROM autonomy_wakes WHERE reason='handoff-due'");
  assert.equal(wakes.length,1);assert.equal(wakes[0].scope,'exact-goal');assert.ok(Number(wakes[0].wait)>0);
 }finally{await c.stop();await f.close()}
});
