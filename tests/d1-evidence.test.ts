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

test('a valid evidenceRef is dereferenced, stored with evidenceSources and dispatched',async()=>{
 const db=await fixture();let executions=0;
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy'||op==='snapshot')return{};
  if(op==='think')return{status:'completed',usage,decision:{expectedGoalRevision:body.goalRevision,
   goalChanges:[{operation:'create',title:'follow the group request',reason:'delegation',sourceEventId:'real-source'}],
   action:{kind:'research',objective:'read for the source group',evidenceRefs:['event:real-source']},summary:'progress',reason:'cited request',wait:{kind:'after_result',seconds:0}}};
  if(op==='execute'){executions++;
   assert.equal(body.action.sourceScope,'group:963105233','requestSource same-scope rule unchanged');
   assert.equal(body.action.evidenceSources.length,1);
   const source=body.action.evidenceSources[0];
   assert.equal(source.ref,'event:real-source');assert.equal(source.scope,'group:963105233');
   assert.equal(source.eventId,'real-source');assert.equal(source.senderId,'12345678');
   assert.equal(source.kind,'foreground_event');assert.match(source.digest,/^[0-9a-f]{64}$/);
   return{status:'completed',summary:'actual read',effects:[],evidence:[],unfinished:[],usage}}
  throw Error(op);
 });
 try{
  await coordinator.repo.configure('tenant','bot',1,{mode:'active'});
  await coordinator.repo.event({tenant_id:'tenant',bot_id:'bot',epoch:2},Date.now(),EVENT,15);
  await coordinator.tick();await coordinator.active?.task;assert.equal(executions,1);
  const [cycle]=await db.query("SELECT decision FROM autonomy_cycles WHERE bot_id='bot' AND decision ? 'evidenceSources'");
  assert.equal(cycle.decision.evidenceSources[0].eventId,'real-source');
  const view=await coordinator.repo.view('tenant','bot');
  assert.equal(view.goals[0].source.evidenceSources[0].ref,'event:real-source');
 }finally{await coordinator.stop();await db.close()}
});

test('a forged or expired evidenceRef rejects the whole decision without executing',async()=>{
 const db=await fixture();let executions=0;
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy'||op==='snapshot')return{};
  if(op==='think')return{status:'completed',usage,decision:{expectedGoalRevision:body.goalRevision,goalChanges:[],
   action:{kind:'research',objective:'invent evidence',evidenceRefs:['event:forged-by-model']},summary:'x',reason:'r',wait:{kind:'time',seconds:60}}};
  if(op==='execute')executions++;
  throw Error(op);
 });
 try{
  await coordinator.repo.configure('tenant','bot',1,{mode:'active'});
  await coordinator.repo.event({tenant_id:'tenant',bot_id:'bot',epoch:2},Date.now(),EVENT,15);
  await coordinator.tick();await coordinator.active?.task;
  assert.equal(executions,0,'a forged reference never dispatches');
  const actions=await db.query("SELECT id FROM autonomy_actions WHERE bot_id='bot'");
  assert.deepEqual(actions,[],'no action row survives the rejected decision');
  const [cycle]=await db.query("SELECT reason FROM autonomy_cycles WHERE bot_id='bot' ORDER BY started_at DESC LIMIT 1");
  assert.equal(cycle.reason,'decision_conflict','the commit rolled back as an autonomy conflict');
 }finally{await coordinator.stop();await db.close()}
});

test('an expired provided event is rejected even when it was once real',async()=>{
 const db=await fixture();let executions=0;
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy'||op==='snapshot')return{};
  if(op==='think')return{status:'completed',usage,decision:{expectedGoalRevision:body.goalRevision,goalChanges:[],
   action:{kind:'research',objective:'cite stale event',evidenceRefs:['event:old-event']},summary:'x',reason:'r',wait:{kind:'time',seconds:60}}};
  if(op==='execute')executions++;
  throw Error(op);
 });
 try{
  await coordinator.repo.configure('tenant','bot',1,{mode:'active'});
  await coordinator.repo.event({tenant_id:'tenant',bot_id:'bot',epoch:2},Date.now()-9*86400000,{...EVENT,eventId:'old-event'},15);
  await coordinator.tick();await coordinator.active?.task;
  assert.equal(executions,0,'an event older than seven days is expired evidence');
  const actions=await db.query("SELECT id FROM autonomy_actions WHERE bot_id='bot'");
  assert.deepEqual(actions,[],'no action row survives the expired reference');
  const [cycle]=await db.query("SELECT reason FROM autonomy_cycles WHERE bot_id='bot' ORDER BY started_at DESC LIMIT 1");
  assert.equal(cycle.reason,'decision_conflict');
 }finally{await coordinator.stop();await db.close()}
});
