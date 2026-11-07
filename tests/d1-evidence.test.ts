import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';
import path from 'node:path';
import {openDatabase} from '../packages/database/index.js';
import {AutonomyCoordinator} from '../apps/node-agent/src/autonomous.js';
import {ActionIntentSchema} from '../packages/contracts/autonomous.js';

async function fixture(){
 const root=await mkdtemp(path.resolve('.local/d1-evidence-'));
 const db=await openDatabase(root).catch(async error=>{await rm(root,{recursive:true,force:true});throw error});
 const close=db.close.bind(db);db.close=async()=>{try{await close()}finally{await rm(root,{recursive:true,force:true})}};
 await db.query("INSERT INTO tenants(id,name) VALUES('tenant','tenant')");
 await db.query("INSERT INTO bots(id,tenant_id,name,protocol,desired_state,health) VALUES('bot','tenant','bot','mock','running',$1)",[JSON.stringify({sampledAt:new Date().toISOString(),protocol:{online:true}})]);
 return db;
}
const usage={modelCalls:1,toolCalls:0,tokens:400};
const EVENT={scope:'group:963105233',senderId:'12345678',owner:true,eventId:'real-source',directed:true,phase:'foreground_result',text:'群里让我查一下',result:{status:'sent'}};

test('D1 schema accepts at most eight evidence refs and nothing else',()=>{
 const parsed=ActionIntentSchema.parse({kind:'research',objective:'read',evidenceRefs:['event:a','event:b']});
 assert.deepEqual(parsed.evidenceRefs,['event:a','event:b']);
 assert.equal(ActionIntentSchema.safeParse({kind:'research',objective:'read',evidenceRefs:Array.from({length:9},(_,i)=>'e'+i)}).success,false);
 assert.equal(ActionIntentSchema.safeParse({kind:'research',objective:'read',sourceScope:'group:1'}).success,false,'the model cannot write authoritative source fields');
});

test('the accepted goal source is dereferenced once into evidenceSources and dispatched same-scope',async()=>{
 const db=await fixture();let executions=0;
 const goalId='bf1086cd-91c4-40ec-8e42-75d80ae70a9d';
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy'||op==='snapshot')return{};
  if(op==='execute'){executions++;
   assert.equal(body.action.sourceScope,'group:963105233','requestSource same-scope rule unchanged');
   assert.equal(body.action.evidenceSources.length,1);
   const source=body.action.evidenceSources[0];
   assert.equal(source.ref,'event:real-source');assert.equal(source.scope,'group:963105233');
   assert.equal(source.eventId,'real-source');assert.equal(source.senderId,'12345678');
   assert.equal(source.kind,'goal_source');assert.match(source.digest,/^[0-9a-f]{64}$/);
   assert.equal(body.action.requestSource.handoffId,goalId);
   return{status:'completed',summary:'actual read',effects:[],evidence:[],unfinished:[],usage}}
  throw Error(op);
 });
 try{
  await coordinator.repo.configure('tenant','bot',1,{mode:'active',rollout:'live'});
  await coordinator.repo.event({tenant_id:'tenant',bot_id:'bot',epoch:2},Date.now(),EVENT,15);
  await db.query("INSERT INTO autonomy_goals(id,bot_id,tenant_id,origin,status,title,reason,progress,success_condition,source) VALUES($1,'bot','tenant','owner_request','active','follow the group request','accepted handoff','',$2,$3)",
   [goalId,'read for the source group',JSON.stringify({eventId:'real-source',scope:'group:963105233',senderId:'12345678',owner:true,handoffId:goalId,notBefore:Date.now()-1000})]);
  await coordinator.repo.wake('tenant','bot',2,'handoff-due',0,goalId);
  await coordinator.tick();await coordinator.active?.task;assert.equal(executions,1);
  const [cycle]=await db.query("SELECT decision FROM autonomy_cycles WHERE bot_id='bot' AND decision ? 'evidenceSources'");
  assert.equal(cycle.decision.evidenceSources[0].eventId,'real-source');
  const view=await coordinator.repo.view('tenant','bot');
  assert.equal(view.goals[0].status,'completed');
  assert.equal(view.actions[0].status,'completed');
 }finally{await coordinator.stop();await db.close()}
});

test('no planning model exists, so the dispatch carries only the durable goal source and no invented refs',async()=>{
 const db=await fixture();let executions=0,thinkCalls=0;
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy'||op==='snapshot')return{};
  if(op==='think'){thinkCalls++;return{status:'completed',usage}}
  if(op==='execute'){executions++;
   assert.equal(body.action.evidenceRefs,undefined,'no model-supplied refs exist in the event-driven dispatch');
   assert.equal(body.action.evidenceSources.length,1,'evidence comes only from the accepted goal source');
   return{status:'completed',summary:'actual read',effects:[],evidence:[],unfinished:[],usage}}
  throw Error(op);
 });
 try{
  await coordinator.repo.configure('tenant','bot',1,{mode:'active'});
  await coordinator.repo.event({tenant_id:'tenant',bot_id:'bot',epoch:2},Date.now(),EVENT,15);
  await db.query("INSERT INTO autonomy_goals(id,bot_id,tenant_id,origin,status,title,reason,progress,success_condition,source) VALUES('bf2086cd-91c4-40ec-8e42-75d80ae70a9d','bot','tenant','owner_request','active','follow the group request','accepted handoff','','read for the source group',$1)",
   [JSON.stringify({eventId:'real-source',scope:'group:963105233',senderId:'12345678',owner:true,handoffId:'bf2086cd-91c4-40ec-8e42-75d80ae70a9d',notBefore:Date.now()-1000})]);
  await coordinator.repo.wake('tenant','bot',2,'handoff-due',0,'bf2086cd-91c4-40ec-8e42-75d80ae70a9d');
  await coordinator.tick();await coordinator.active?.task;
  assert.equal(executions,1);assert.equal(thinkCalls,0);
  const actions=await db.query("SELECT id FROM autonomy_actions WHERE bot_id='bot'");
  assert.equal(actions.length,1,'exactly the platform-dispatched action exists');
 }finally{await coordinator.stop();await db.close()}
});

test('an accepted handoff past its seven-day reply window is refused and frees the goal',async()=>{
 const db=await fixture();let executions=0;
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy'||op==='snapshot')return{};
  if(op==='execute'){executions++;return{status:'blocked',summary:'原请求回复许可无效或已使用；不能降级为主动发送重试',effects:[],evidence:[],unfinished:[],usage:{tokens:0,modelCalls:0,toolCalls:0}}}
  throw Error(op);
 });
 try{
  await coordinator.repo.configure('tenant','bot',1,{mode:'active',rollout:'live'});
  await coordinator.repo.event({tenant_id:'tenant',bot_id:'bot',epoch:2},Date.now()-9*86400000,{...EVENT,eventId:'old-event'},15);
  await db.query("INSERT INTO autonomy_goals(id,bot_id,tenant_id,origin,status,title,reason,progress,success_condition,source) VALUES('bf3086cd-91c4-40ec-8e42-75d80ae70a9d','bot','tenant','owner_request','active','cite stale event','accepted handoff','','read',$1)",
   [JSON.stringify({eventId:'old-event',scope:'group:963105233',senderId:'12345678',owner:true,handoffId:'bf3086cd-91c4-40ec-8e42-75d80ae70a9d',notBefore:Date.now()-9*86400000})]);
  await coordinator.repo.wake('tenant','bot',2,'handoff-due',0,'bf3086cd-91c4-40ec-8e42-75d80ae70a9d');
  await coordinator.tick();await coordinator.active?.task;
  assert.equal(executions,1,'the inner runtime is the authority that refuses the expired window');
  const view=await coordinator.repo.view('tenant','bot');
  assert.equal(view.goals[0].status,'abandoned','the refused handoff frees its slot without a fake success');
  assert.equal(view.actions[0].status,'blocked');assert.equal(view.cycles[0].reason,'handoff_blocked');
 }finally{await coordinator.stop();await db.close()}
});
