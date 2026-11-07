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
/** One accepted handoff goal: the only durable work item the coordinator
 * dispatches. goalId doubles as the handoff id, exactly as acceptHandoff writes. */
async function handoffGoal(db:any,goalId:string,bot='bot-0',opts:{scope?:string;owner?:boolean;notBeforeOffsetMs?:number;objective?:string}={}){
 const notBefore=Date.now()+(opts.notBeforeOffsetMs??-1000);
 await db.query("INSERT INTO autonomy_goals(id,bot_id,tenant_id,origin,status,title,reason,progress,success_condition,source) VALUES($1,$2,'tenant',$3,'active',$4,'real chat submitted through the authorized foreground tool','',$5,$6)",
  [goalId,bot,opts.owner?'owner_request':'contact_request',opts.objective||'deliver the accepted task result',opts.objective||'deliver the accepted task result',
   JSON.stringify({eventId:'handoff-event',scope:opts.scope||'private:12345678',senderId:'12345678',owner:opts.owner===true,handoffId:goalId,notBefore})]);
}

test('a due handoff goal is dispatched to the inner with its trusted source and settles the goal',async()=>{
 const db=await fixture();let executions=0,thinkCalls=0;
 const goalId='b1786cd0-91c4-40ec-8e42-75d80ae70a9d';
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy'||op==='snapshot')return{};
  if(op==='think'){thinkCalls++;return{status:'completed',usage}}
  if(op==='execute'){executions++;
   assert.equal(body.action.kind,'message');assert.equal(body.action.goalId,goalId);
   assert.equal(body.action.target,'private:12345678');
   assert.equal(body.action.requestSource.handoffId,goalId,'the accepted goal source is the only requestSource');
   assert.equal(body.action.requestSource.eventId,'handoff-event');
   assert.equal(body.action.sourceScope,'private:12345678');
   assert.equal(body.action.evidenceSources[0].ref,'event:handoff-event');
   assert.match(body.action.evidenceSources[0].digest,/^[0-9a-f]{64}$/);
   return{status:'completed',summary:'actual fixture reply delivered',effects:[{id:body.work.id+':0',status:'confirmed',receipt:'fixture-result-ack'}],evidence:[],unfinished:[],usage}}
  throw Error(op);
 });
 try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active',rollout:'live'});
  await handoffGoal(db,goalId);
  await coordinator.repo.wake('tenant','bot-0',2,'handoff-due',0,goalId);
  await coordinator.tick();await coordinator.active?.task;
  assert.equal(executions,1);assert.equal(thinkCalls,0,'no orientation or planning model call exists');
  const view=await coordinator.repo.view('tenant','bot-0');
  assert.equal(view.goals[0].status,'completed');assert.equal(view.actions[0].status,'completed');
  assert.equal(view.cycles[0].reason,'handoff_executed');assert.equal(view.actions.length,1,'one action per goal, no planning call');
 }finally{await coordinator.stop();await db.close()}
});

test('a slow member policy cannot block the active lease heartbeat',async()=>{
 const db=await fixture(2);let releaseSlow!:()=>void,enteredSlow!:()=>void,executions=0;
 const slowGate=new Promise<void>(resolve=>releaseSlow=resolve),slowEntered=new Promise<void>(resolve=>enteredSlow=resolve);
 let slow=false,activePolicies=0;
 const goalId='0e5d3a9e-6f0e-4a35-9f7b-1c2d3e4f5a6b';
 const coordinator=new AutonomyCoordinator(db,async(bot,op,body:any)=>{
  if(op==='policy'){
   if(bot.bot_id==='bot-0')activePolicies++;
   if(slow&&bot.bot_id==='bot-1'){enteredSlow();await slowGate}
   return{};
  }
  if(op==='snapshot'||op==='cancel')return{};
  if(op==='execute'){executions++;assert.equal(body.action.goalId,goalId);return{status:'completed',summary:'actual result',effects:[],evidence:[],unfinished:[],usage}}
  throw Error(op);
 });
 try{
  for(let n=0;n<2;n++)await coordinator.repo.configure('tenant','bot-'+n,1,{mode:'active'});
  await handoffGoal(db,goalId);await coordinator.repo.wake('tenant','bot-0',2,'handoff-due',0,goalId);
  await coordinator.tick();assert.ok(coordinator.active);slow=true;
  const blockedTick=coordinator.tick();await enteredSlow;
  const before=activePolicies;await coordinator.heartbeat();assert.ok(activePolicies>before,'renew active owner while peer IPC remains unresolved');
  releaseSlow();await blockedTick;await coordinator.active?.task;assert.equal(executions,1);
 }finally{releaseSlow();await coordinator.stop();await db.close()}
});

test('shared experience survives coordinator replacement, labels handoff results and isolates members',async()=>{
 const db=await fixture(2);const goalId='1c9a1bd2-2a4b-4a35-9f7b-1c2d3e4f5a6c';
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy'||op==='snapshot')return{};
  if(op==='execute')return{status:'blocked',summary:'actual public service limit',toolResults:[{tool:'bilibili_search',status:'unavailable',detail:'cooldown'}],effects:[],evidence:[],unfinished:['deliver the accepted result'],usage};
  throw Error(op);
 });
 try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active'});await coordinator.repo.applied('tenant','bot-0',2);
  await handoffGoal(db,goalId);await coordinator.repo.wake('tenant','bot-0',2,'handoff-due',0,goalId);
  await coordinator.tick();await coordinator.active?.task;
  const replacement=new AutonomyCoordinator(db,async()=>({}));
  const history=await replacement.repo.experience('tenant','bot-0');
  assert.equal(history.actions[0].status,'blocked');assert.equal(history.actions[0].toolResults[0].tool,'bilibili_search');assert.equal(history.actions[0].effects.length,0);
  assert.ok(history.actions[0].assignedAt&&history.actions[0].finishedAt);
  assert.deepEqual((await replacement.repo.experience('tenant','bot-1')).actions,[]);
  assert.deepEqual((await replacement.repo.experience('different-tenant','bot-0')).decisions,[]);
  const view=await coordinator.repo.view('tenant','bot-0');await coordinator.repo.configure('tenant','bot-0',view.settings.epoch,{mode:'passive'});
  assert.ok((await coordinator.repo.policies()).some(b=>b.bot_id==='bot-0'),'mode switch hid durable experience from runtime recovery');
  await replacement.stop();
 }finally{await coordinator.stop();await db.close()}
});

test('four receipt-bearing bubbles consume one versioned logical chat action and settle the goal',async()=>{
 const db=await fixture();const goalId='2c9a1bd2-2a4b-4a35-9f7b-1c2d3e4f5a6c';
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy'||op==='snapshot')return{};
  if(op==='execute')return{status:'completed',actionUnitVersion:2,summary:'four bubbles actually confirmed by fixture protocol',effects:Array.from({length:4},(_,n)=>({id:body.work.id+':'+n,logicalActionId:body.work.id+':message:private:12345678',method:'send_private_msg',target:'private:12345678',status:'confirmed',receipt:'real-fixture-'+n})),evidence:[],unfinished:[],usage};
  throw Error(op);
 });
 try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active',cycleExternalActions:1});
  await handoffGoal(db,goalId);await coordinator.repo.wake('tenant','bot-0',2,'handoff-due',0,goalId);
  await coordinator.tick();await coordinator.active?.task;
  const view=await coordinator.repo.view('tenant','bot-0');
  assert.equal(view.usage.external_actions,1);assert.equal(view.actions.length,1);
  assert.equal(view.actions[0].result.effects.length,4);assert.equal(new Set(view.actions[0].result.effects.map((e:any)=>e.receipt)).size,4);
  assert.equal(view.goals[0].status,'completed');assert.equal(view.cycles[0].reason,'handoff_executed');
 }finally{await coordinator.stop();await db.close()}
});

test('a completed handoff yields the next local dispatch to another due member without losing its own result',async()=>{
 const db=await fixture(2),executions:string[]=[];const goals=['3c9a1bd2-2a4b-4a35-9f7b-1c2d3e4f5a6c','4c9a1bd2-2a4b-4a35-9f7b-1c2d3e4f5a6c','5fc9a1bd-2a4b-4a35-9f7b-1c2d3e4f5a6c'];
 const coordinator=new AutonomyCoordinator(db,async(bot,op,body:any)=>{
  if(op==='policy'||op==='snapshot')return{};
  if(op==='execute'){executions.push(bot.bot_id+':'+body.action.goalId);return{status:'completed',summary:'actual result',effects:[],evidence:[],unfinished:[],usage}}
  throw Error(op);
 });
 try{
  for(let n=0;n<2;n++)await coordinator.repo.configure('tenant','bot-'+n,1,{mode:'active'});
  await handoffGoal(db,goals[0],'bot-0');await coordinator.repo.wake('tenant','bot-0',2,'handoff-due',0,goals[0]);
  await handoffGoal(db,goals[1],'bot-0');await coordinator.repo.wake('tenant','bot-0',2,'handoff-due',0,goals[1]);
  await handoffGoal(db,goals[2],'bot-1');await coordinator.repo.wake('tenant','bot-1',2,'handoff-due',0,goals[2]);
  await coordinator.tick();await coordinator.active?.task;
  assert.equal(executions.length,1);assert.equal(executions[0],'bot-0:'+goals[0]);
  const view=await coordinator.repo.view('tenant','bot-0');
  assert.equal(view.goals.find((g:any)=>g.id===goals[0]).status,'completed','yielding never loses the settled result');
  assert.equal(view.cycles[0].reason,'fair_yield','a second due member shortens the local backlog wait');
  assert.ok(new Date(view.settings.next_wake_at).getTime()<=Date.now()+2000,'yielded local work keeps an immediate wake');
  await coordinator.tick();await coordinator.active?.task;
  assert.equal(executions.length,2);assert.equal(executions[1],'bot-1:'+goals[2],'the other due member goes first after the yield');
  await new Promise(resolve=>setTimeout(resolve,1100));
  await coordinator.tick();await coordinator.active?.task;
  assert.equal(executions.length,3);assert.equal(executions[2],'bot-0:'+goals[1]);
 }finally{await coordinator.stop();await db.close()}
});

test('an absent stopped read-only task closes unknown without replaying an external effect',async()=>{
 const db=await fixture();let executed=0;
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy'||op==='snapshot')return{};
  if(op==='receipt')return{exists:false,active:false,effects:[]};
  if(op==='execute')executed++;
  throw Error(op);
 });
 try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active'});
  await db.query("INSERT INTO autonomy_cycles(id,bot_id,tenant_id,epoch,generation,config_revision,owner,state,budget,started_at,finished_at) VALUES('closed-cycle','bot-0','tenant',2,0,0,'old','ended','{}',now(),now())");
  await db.query("INSERT INTO autonomy_actions(id,cycle_id,bot_id,tenant_id,epoch,kind,target,objective,status,expires_at,result,created_at,finished_at) VALUES('stale-read','closed-cycle','bot-0','tenant',2,'research','','read public facts','unknown',now()+interval '1 hour',$1,now(),now())",[JSON.stringify({status:'unknown',summary:'read interrupted',effects:[],usage})]);
  await coordinator.repo.configure('tenant','bot-0',2,{mode:'passive'});(coordinator as any).sampled.clear();await coordinator.tick();
  const view=await coordinator.repo.view('tenant','bot-0');assert.equal(executed,0);assert.equal(view.actions[0].status,'blocked');assert.equal(view.actions[0].result.stopReason,'read_execution_interrupted');assert.equal(view.usage?.external_actions??0,0);
 }finally{await coordinator.stop();await db.close()}
});

test('foreground feedback advances the version fence; a canceled dispatch keeps its retry wake',async()=>{
 const db=await fixture();const goalId='5c9a1bd2-2a4b-4a35-9f7b-1c2d3e4f5a6c';const at=Date.now()-1000;
 let snapshots=0,executions=0,latestVersion=0;
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy')return{};
  if(op==='snapshot'){snapshots++;latestVersion=at+snapshots;return{feedbackVersion:latestVersion,foregroundFeedback:{at:latestVersion,phase:'foreground_result',eventId:'reply-'+snapshots,scope:'private:12345678',senderId:'12345678',directed:true,result:{status:'sent'}}}}
  if(op==='execute'){executions++;assert.equal(body.work.feedbackVersion,latestVersion,'work carries the version the cycle actually observed');return{status:'canceled',summary:'foreground invalidated work before side effects',effects:[],evidence:[],unfinished:[],usage:{tokens:0,modelCalls:0,toolCalls:0}}}
  throw Error(op);
 });
 try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active'});
  await handoffGoal(db,goalId);await coordinator.repo.wake('tenant','bot-0',2,'handoff-due',0,goalId);
  await coordinator.tick();await coordinator.active?.task;
  const view=await coordinator.repo.view('tenant','bot-0');
  assert.equal(executions,1);assert.equal(view.actions[0].status,'canceled');assert.equal(view.goals[0].status,'active','a canceled dispatch is not a terminal result');
  assert.equal(view.cycles[0].reason,'foreground_or_mode_changed');
  const delay=new Date(view.settings.next_wake_at).getTime()-Date.now();assert.ok(delay>0&&delay<=16000,'foreground interruption must not strand the accepted handoff');
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
  throw Error(op);
 });
 const persist=coordinator.repo.event.bind(coordinator.repo);
 coordinator.repo.event=async(...args)=>{attempts++;if(outage)throw new Error('controlled database outage');return persist(...args)};
 try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active'});
  await coordinator.tick();
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

test('accepted task result flows back, completes the goal and leaves no polling wake',async()=>{
 const db=await fixture();const goalId='6c9a1bd2-2a4b-4a35-9f7b-1c2d3e4f5a6c';
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy'||op==='snapshot')return{};
  if(op==='execute')return{status:'completed',summary:'verified fixture receipt',evidence:[{source:'fixture',reference:'checked'}],effects:[{id:body.work.id+':0',status:'confirmed',receipt:'actual-ack'}],unfinished:[],usage:{...usage,modelCalls:2,toolCalls:1,tokens:1200}};
  throw Error(op);
 });
 try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active'});
  await handoffGoal(db,goalId,'bot-0',{owner:true});await coordinator.repo.wake('tenant','bot-0',2,'handoff-due',0,goalId);
  await coordinator.tick();await coordinator.active?.task;
  const view=await coordinator.repo.view('tenant','bot-0');
  assert.equal(view.goals[0].status,'completed');assert.equal(view.settings.state,'sleeping');
  assert.equal(view.usage.used_tokens,1200);assert.equal(view.usage.reserved_tokens,0);assert.equal(coordinator.active,undefined);
  assert.equal(view.cycles[0].reason,'handoff_executed');
  assert.equal(await db.query('SELECT count(*)::int AS n FROM autonomy_wakes WHERE bot_id=$1',['bot-0']).then((r:any)=>r[0].n),0,'event-driven design arms no polling wake after terminal work');
  // A new supervisor reads durable state only; the completed goal is not re-run.
  const next=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
   if(op==='policy'||op==='snapshot')return{};
   if(op==='execute')throw Error('completed goal must not be re-executed');
   throw Error(op);
  });
  await next.repo.wake('tenant','bot-0',2,'actual-event',0);await next.tick();await next.active?.task;assert.equal(next.active,undefined);await next.stop();
 }finally{await coordinator.stop();await db.close()}
});

test('a blocked refusal abandons the accepted handoff; receipts still settle through the tick loop',async()=>{
 const db=await fixture();const goalId='7c9a1bd2-2a4b-4a35-9f7b-1c2d3e4f5a6c';let actionId='';
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy'||op==='snapshot')return{};
  if(op==='execute'){actionId=body.work.id;return{status:'blocked',summary:'原请求回复许可无效或已使用；不能降级为主动发送重试',effects:[],evidence:[],unfinished:[],usage:{tokens:0,modelCalls:0,toolCalls:0}}}
  if(op==='receipt')return{result:{status:'completed',summary:'late authoritative receipt',effects:[{id:actionId+':0',status:'confirmed',receipt:'late-real-id'}],evidence:[],unfinished:[]}};
  throw Error(op);
 });
 try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active'});
  await handoffGoal(db,goalId);await coordinator.repo.wake('tenant','bot-0',2,'handoff-due',0,goalId);
  await coordinator.tick();await coordinator.active?.task;
  let view=await coordinator.repo.view('tenant','bot-0');
  assert.equal(view.goals[0].status,'abandoned','a final refusal frees the accepted-goal slot');assert.equal(view.cycles[0].reason,'handoff_blocked');
  // The receipt-settlement path remains for any action left uncertain.
  await db.query("INSERT INTO autonomy_cycles(id,bot_id,tenant_id,epoch,generation,config_revision,owner,state,budget,started_at,finished_at) VALUES('closed-cycle','bot-0','tenant',2,0,0,'old','ended','{}',now(),now())");
  await db.query("INSERT INTO autonomy_actions(id,cycle_id,bot_id,tenant_id,epoch,kind,target,objective,status,expires_at,result,created_at,finished_at) VALUES('later-unknown','closed-cycle','bot-0','tenant',2,'message','private:12345678','contact','unknown',now()+interval '1 hour',$1,now(),now())",[JSON.stringify({status:'unknown',summary:'protocol response lost',effects:[{id:'later-unknown:0',status:'unknown'}],usage})]);
  (coordinator as any).sampled.clear();await coordinator.tick();
  view=await coordinator.repo.view('tenant','bot-0');
  assert.equal(view.actions.find((a:any)=>a.id==='later-unknown').status,'completed');assert.equal(view.actions.find((a:any)=>a.id==='later-unknown').result.effects[0].receipt,'late-real-id');
 }finally{await coordinator.stop();await db.close()}
});

test('an uncertain effect is recorded once and never re-dispatched by a later wake',async()=>{
 const db=await fixture();const goalId='8c9a1bd2-2a4b-4a35-9f7b-1c2d3e4f5a6c';let executions=0;
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy'||op==='snapshot')return{};
  if(op==='execute'){executions++;return{status:'unknown',summary:'unconfirmed actual effect',effects:[{id:body.work.id+':0',status:'unknown'}],evidence:[],unfinished:['verify'],usage}}
  throw Error(op);
 });
 try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active',rollout:'live'});
  await handoffGoal(db,goalId);await coordinator.repo.wake('tenant','bot-0',2,'handoff-due',0,goalId);
  await coordinator.tick();await coordinator.active?.task;
  await coordinator.repo.wake('tenant','bot-0',2,'later',0);await coordinator.tick();await coordinator.active?.task;
  const view=await coordinator.repo.view('tenant','bot-0');
  assert.equal(executions,1,'the reply permit is single-use; the goal waits for its receipt');assert.equal(view.actions.length,1);
  assert.equal(view.actions[0].status,'unknown');assert.equal(view.cycles[0].reason,'waiting_receipt');
  assert.equal(view.usage.external_actions,1);
  const delay=(new Date(view.settings.next_wake_at).getTime()-Date.now())/1000;assert.ok(delay>250&&delay<=310,'bounded retry wake re-arms the unsettled receipt');
 }finally{await coordinator.stop();await db.close()}
});

test('a partial message handoff with every effect confirmed settles the goal honestly without a second dispatch',async()=>{
 const db=await fixture();const goalId='aec9a1bd-2a4b-4a35-9f7b-1c2d3e4f5a6c';let executions=0;
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy'||op==='snapshot')return{};
  if(op==='execute'){executions++;throw Error('a fully confirmed delivery must never be re-dispatched')}
  throw Error(op);
 });
 try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active',rollout:'live'});
  await handoffGoal(db,goalId);
  await db.query("INSERT INTO autonomy_cycles(id,bot_id,tenant_id,epoch,generation,config_revision,owner,state,budget,started_at,finished_at) VALUES('partial-cycle','bot-0','tenant',2,0,0,'old','ended','{}',now(),now())");
  await db.query("INSERT INTO autonomy_actions(id,cycle_id,bot_id,tenant_id,epoch,kind,target,objective,goal_id,status,expires_at,result,created_at,finished_at) VALUES('confirmed-partial','partial-cycle','bot-0','tenant',2,'message','private:12345678','deliver the accepted task result',$1,'partial',now()+interval '1 hour',$2,now(),now())",[goalId,JSON.stringify({status:'partial',summary:'回复已按协议送达',effects:[{id:'confirmed-partial:0',status:'confirmed',receipt:'real-fixture-ack'}],evidence:[],unfinished:[]})]);
  await coordinator.repo.wake('tenant','bot-0',2,'handoff-due',0,goalId);
  await coordinator.tick();await coordinator.active?.task;
  const view=await coordinator.repo.view('tenant','bot-0');
  assert.equal(executions,0,'planHandoff would replay the already-confirmed outward send');
  assert.equal(view.goals[0].status,'completed');assert.equal(view.goals[0].progress,'回复已按协议送达');
  assert.equal(view.cycles[0].reason,'handoff_executed');
  assert.equal(view.actions.length,1);assert.equal(view.actions[0].status,'partial','the settled action ledger is not rewritten');
 }finally{await coordinator.stop();await db.close()}
});

test('a partial message without a summary completes with an honest delivery description',async()=>{
 const db=await fixture();const goalId='afc9a1bd-2a4b-4a35-9f7b-1c2d3e4f5a6c';let executions=0;
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy'||op==='snapshot')return{};
  if(op==='execute'){executions++;throw Error('a fully confirmed delivery must never be re-dispatched')}
  throw Error(op);
 });
 try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active',rollout:'live'});
  await handoffGoal(db,goalId);
  await db.query("INSERT INTO autonomy_cycles(id,bot_id,tenant_id,epoch,generation,config_revision,owner,state,budget,started_at,finished_at) VALUES('partial-blank-cycle','bot-0','tenant',2,0,0,'old','ended','{}',now(),now())");
  await db.query("INSERT INTO autonomy_actions(id,cycle_id,bot_id,tenant_id,epoch,kind,target,objective,goal_id,status,expires_at,result,created_at,finished_at) VALUES('confirmed-blank','partial-blank-cycle','bot-0','tenant',2,'message','private:12345678','deliver the accepted task result',$1,'partial',now()+interval '1 hour',$2,now(),now())",[goalId,JSON.stringify({status:'partial',effects:[{id:'confirmed-blank:0',status:'confirmed',receipt:'real-fixture-ack'}],evidence:[],unfinished:[]})]);
  await coordinator.repo.wake('tenant','bot-0',2,'handoff-due',0,goalId);
  await coordinator.tick();await coordinator.active?.task;
  const view=await coordinator.repo.view('tenant','bot-0');
  assert.equal(executions,0);
  assert.equal(view.goals[0].status,'completed');assert.equal(view.goals[0].progress,'此前不明的投递回执已全部确认为已送达');
  assert.equal(view.cycles[0].reason,'handoff_executed');
 }finally{await coordinator.stop();await db.close()}
});

test('a partial research handoff with unfinished items stays active and keeps a visible wake without re-dispatch',async()=>{
 const db=await fixture();const goalId='bec9a1bd-2a4b-4a35-9f7b-1c2d3e4f5a6c';let executions=0;
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy'||op==='snapshot')return{};
  if(op==='execute'){executions++;throw Error('a partial research goal must not be re-dispatched')}
  throw Error(op);
 });
 try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active',rollout:'live'});
  await handoffGoal(db,goalId);
  await db.query("INSERT INTO autonomy_cycles(id,bot_id,tenant_id,epoch,generation,config_revision,owner,state,budget,started_at,finished_at) VALUES('partial-research-cycle','bot-0','tenant',2,0,0,'old','ended','{}',now(),now())");
  await db.query("INSERT INTO autonomy_actions(id,cycle_id,bot_id,tenant_id,epoch,kind,target,objective,goal_id,status,expires_at,result,created_at,finished_at) VALUES('open-research','partial-research-cycle','bot-0','tenant',2,'research','','read the requested public facts',$1,'partial',now()+interval '1 hour',$2,now(),now())",[goalId,JSON.stringify({status:'partial',summary:'已读取部分公开来源',effects:[{id:'open-research:0',status:'confirmed'}],evidence:[{source:'fixture',reference:'checked'}],unfinished:['未能访问的其余来源']})]);
  await coordinator.repo.wake('tenant','bot-0',2,'handoff-due',0,goalId);
  await coordinator.tick();await coordinator.active?.task;
  const view=await coordinator.repo.view('tenant','bot-0');
  assert.equal(executions,0,'re-dispatching would replay confirmed sends; abandoning would drop them');
  assert.equal(view.goals[0].status,'active','confirmed effects and remaining items stay visible');
  assert.equal(view.cycles[0].reason,'handoff_partial_open');assert.equal(view.actions.length,1);
  assert.equal(view.settings.state,'backoff');assert.equal(view.settings.last_error,'handoff_partial_open');
  const delay=(new Date(view.settings.next_wake_at).getTime()-Date.now())/1000;assert.ok(delay>880&&delay<=900,'the open handoff keeps its own wake for manual handling');
 }finally{await coordinator.stop();await db.close()}
});

test('a partial message with an unknown effect still waits for its authoritative receipt',async()=>{
 const db=await fixture();const goalId='cec9a1bd-2a4b-4a35-9f7b-1c2d3e4f5a6c';let executions=0;
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy'||op==='snapshot')return{};
  if(op==='execute'){executions++;throw Error('an unsettled delivery must not be re-dispatched')}
  throw Error(op);
 });
 try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active',rollout:'live'});
  await handoffGoal(db,goalId);
  await db.query("INSERT INTO autonomy_cycles(id,bot_id,tenant_id,epoch,generation,config_revision,owner,state,budget,started_at,finished_at) VALUES('partial-unknown-cycle','bot-0','tenant',2,0,0,'old','ended','{}',now(),now())");
  await db.query("INSERT INTO autonomy_actions(id,cycle_id,bot_id,tenant_id,epoch,kind,target,objective,goal_id,status,expires_at,result,created_at,finished_at) VALUES('mixed-partial','partial-unknown-cycle','bot-0','tenant',2,'message','private:12345678','deliver the accepted task result',$1,'partial',now()+interval '1 hour',$2,now(),now())",[goalId,JSON.stringify({status:'partial',summary:'一条气泡已确认，另一条回执未明',effects:[{id:'mixed-partial:0',status:'confirmed',receipt:'real-fixture-ack'},{id:'mixed-partial:1',status:'unknown'}],evidence:[],unfinished:[]})]);
  await coordinator.repo.wake('tenant','bot-0',2,'handoff-due',0,goalId);
  await coordinator.tick();await coordinator.active?.task;
  const view=await coordinator.repo.view('tenant','bot-0');
  assert.equal(executions,0);assert.equal(view.actions.length,1);
  assert.equal(view.goals[0].status,'active','an unknown receipt is not a completion');
  assert.equal(view.cycles[0].reason,'waiting_receipt');
  const delay=(new Date(view.settings.next_wake_at).getTime()-Date.now())/1000;assert.ok(delay>250&&delay<=310,'the unsettled receipt keeps its bounded retry wake');
 }finally{await coordinator.stop();await db.close()}
});

test('one hundred sleeping robots have no active work or per-bot timers; offline wake never starts QQ',async()=>{
 const db=await fixture(100);let modelCalls=0;
 const coordinator=new AutonomyCoordinator(db,async(_bot,op)=>{if(op==='execute')modelCalls++;return {}});
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
 const db=await fixture();let receiptCalls=0,executions=0,settled=false;
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy'||op==='snapshot')return{};
  if(op==='receipt'){receiptCalls++;return settled?{result:{status:'completed',summary:'late authoritative receipt',effects:[{id:'kept-unknown:0',status:'confirmed',receipt:'late-real-id'}],evidence:[],unfinished:[]}}:{result:{status:'unknown',effects:[{id:'kept-unknown:0',status:'unknown'}]}}}
  if(op==='execute')executions++;
  throw Error(op);
 });
 try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active',rollout:'live'});
  await db.query("INSERT INTO autonomy_cycles(id,bot_id,tenant_id,epoch,generation,config_revision,owner,state,budget,started_at,finished_at) VALUES('closed-cycle','bot-0','tenant',2,0,0,'old','ended','{}',now(),now())");
  await db.query("INSERT INTO autonomy_actions(id,cycle_id,bot_id,tenant_id,epoch,kind,target,objective,status,expires_at,result,created_at,finished_at) VALUES('kept-unknown','closed-cycle','bot-0','tenant',2,'message','private:12345678','one greeting','unknown',now()+interval '1 hour',$1,now(),now())",[JSON.stringify({status:'unknown',summary:'protocol response lost',effects:[{id:'kept-unknown:0',status:'unknown'}],usage})]);
  await coordinator.repo.configure('tenant','bot-0',2,{mode:'passive'});
  (coordinator as any).sampled.clear();await coordinator.tick();assert.equal(receiptCalls,1);assert.equal(coordinator.active,undefined);assert.equal(executions,0);
  assert.equal((await coordinator.repo.policies()).length,1,'pending old receipt disappeared once passive policy was applied');
  settled=true;await db.query("UPDATE autonomy_actions SET result=jsonb_set(result,'{receiptCheck,nextAt}','0') WHERE id='kept-unknown'");(coordinator as any).sampled.clear();await coordinator.tick();
  const view=await coordinator.repo.view('tenant','bot-0');assert.equal(view.actions[0].status,'completed');assert.equal(view.actions[0].result.effects[0].receipt,'late-real-id');assert.equal(view.settings.config.mode,'passive');assert.equal(view.settings.runtime_epoch,3);assert.equal(executions,0);assert.equal(receiptCalls,2);
  const policies=await coordinator.repo.policies();assert.equal(policies.length,0,'fully settled passive work needs no policy install');assert.equal((await db.query('SELECT * FROM autonomy_wakes')).length,0);assert.equal((await db.query('SELECT * FROM commands')).length,0);
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

test('expired health never claims a cycle; recovery re-arms and dispatches the due handoff',async()=>{
 const db=await fixture();const goalId='9c9a1bd2-2a4b-4a35-9f7b-1c2d3e4f5a6c';let executions=0;
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy'||op==='snapshot')return{};
  if(op==='execute'){executions++;assert.equal(body.action.goalId,goalId);return{status:'completed',summary:'delivered after recovery',effects:[],evidence:[],unfinished:[],usage}}
  throw Error(op);
 });
 try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active',rollout:'live'});
  await handoffGoal(db,goalId);await coordinator.repo.wake('tenant','bot-0',2,'handoff-due',0,goalId);
  await db.query("UPDATE bots SET health=$1 WHERE id='bot-0'",[JSON.stringify({sampledAt:new Date(Date.now()-120000).toISOString(),protocol:{online:true}})]);
  await coordinator.tick();assert.equal(executions,0);assert.equal(coordinator.active,undefined,'stale health grants no claim and no external work');
  assert.equal((await coordinator.repo.view('tenant','bot-0')).settings.state,'suspended_offline');assert.equal((await db.query('SELECT * FROM commands')).length,0);
  await db.query("UPDATE bots SET health=$1 WHERE id='bot-0'",[JSON.stringify({sampledAt:new Date().toISOString(),protocol:{online:true}})]);
  await coordinator.repo.suspendOffline();
  await coordinator.tick();
  const resumed=coordinator.active as {task:Promise<void>}|undefined;await resumed?.task;
  assert.equal(executions,1,'the online-resumed wake rediscovered the due handoff');
  const view=await coordinator.repo.view('tenant','bot-0');assert.equal(view.goals[0].status,'completed');
 }finally{await coordinator.stop();await db.close()}
});

test('a durable task notification bypasses only the bounded observation sampling delay',async()=>{
 const db=await fixture();let pending=false,samples=0;
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy')return{pendingHandoffs:pending};
  if(op==='snapshot'){samples++;return{}}
  throw Error(op);
 });
 try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active'});
  await coordinator.tick();await coordinator.active?.task;assert.equal(samples,2,'the accepted-wake cycle performs one bounded sample');
  await coordinator.tick();assert.equal(samples,2,'ordinary polling lost its sampling bound');
  pending=true;await coordinator.tick();assert.equal(samples,3,'accepted task waited for the ambient observation timer');
 }finally{await coordinator.stop();await db.close()}
});

test('unlimited daily policy keeps actual accounting and bounded cycles after prior daily use',async()=>{
 const db=await fixture();const goalId='adc9a1bd-2a4b-4a35-9f7b-1c2d3e4f5a6c';let requests=0;
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy'||op==='snapshot')return{};
  if(op==='execute'){requests++;assert.equal(body.work.budget.tokens,500000);return{status:'completed',summary:'actual result',effects:[],evidence:[],unfinished:[],usage}}
  throw Error(op);
 });
 try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active',dailyTokenBudget:0});
  await db.query("INSERT INTO autonomy_daily_usage(bot_id,tenant_id,day,used_tokens) VALUES('bot-0','tenant',(now() AT TIME ZONE 'Asia/Shanghai')::date,250000)");
  await handoffGoal(db,goalId);await coordinator.repo.wake('tenant','bot-0',2,'handoff-due',0,goalId);
  await coordinator.tick();await coordinator.active?.task;
  const v=await coordinator.repo.view('tenant','bot-0');assert.equal(requests,1);assert.equal(v.settings.state,'sleeping');assert.equal(v.usage.used_tokens,250400);assert.equal(v.usage.reserved_tokens,0);assert.equal(v.settings.config.cycleTokenBudget,500000);
 }finally{await coordinator.stop();await db.close()}
});

test('a partial day reservation bounds the dispatched work while policy keeps its configured ceiling',async()=>{
 const db=await fixture();const goalId='bdc9a1bd-2a4b-4a35-9f7b-1c2d3e4f5a6c';let calls=0;
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy'){assert.equal(body.config.cycleTokenBudget,6000);return{}};
  if(op==='snapshot')return{};
  if(op==='execute'){calls++;assert.equal(body.work.budget.tokens,5000);return{status:'completed',summary:'actual result',effects:[],evidence:[],unfinished:[],usage}}
  throw Error(op);
 });
 try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active',cycleTokenBudget:6000,dailyTokenBudget:10000});
  await db.query("INSERT INTO autonomy_daily_usage(bot_id,tenant_id,day,used_tokens) VALUES('bot-0','tenant',(now() AT TIME ZONE 'Asia/Shanghai')::date,5000)");
  await handoffGoal(db,goalId);await coordinator.repo.wake('tenant','bot-0',2,'handoff-due',0,goalId);
  await coordinator.tick();await coordinator.active?.task;
  const view=await coordinator.repo.view('tenant','bot-0');assert.equal(calls,1);assert.equal(view.usage.used_tokens,5400);assert.equal(view.usage.reserved_tokens,0);assert.equal(view.settings.config.cycleTokenBudget,6000);
 }finally{await coordinator.stop();await db.close()}
});

test('a failed request uses an error backoff rather than a silent idle interval, and keeps its wake',async()=>{
 const db=await fixture();const goalId='cdc9a1bd-2a4b-4a35-9f7b-1c2d3e4f5a6c';let cycles=0;
 const coordinator=new AutonomyCoordinator(db,async(_bot,op)=>{
  if(op==='policy')return{};
  if(op==='snapshot'){cycles++;if(cycles>1)throw new Error('test provider request failed');return{}}
  throw Error(op);
 });try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active',minSleepSeconds:7200});
  await handoffGoal(db,goalId);await coordinator.repo.wake('tenant','bot-0',2,'handoff-due',0,goalId);
  await coordinator.tick();await coordinator.active?.task;
  const view=await coordinator.repo.view('tenant','bot-0');assert.equal(view.cycles[0].reason,'runtime_or_decision_failed');assert.equal(view.settings.state,'backoff');
  const delay=new Date(view.settings.next_wake_at).getTime()-Date.now();assert.ok(delay>16000&&delay<=31000,'due handoff work is retried after the error backoff');
 }finally{await coordinator.stop();await db.close()}
});

test('bounded work identity covers its remaining steps and the goal settles after late completion',async()=>{
 const db=await fixture();const goalId='ddc9a1bd-2a4b-4a35-9f7b-1c2d3e4f5a6c';let elapsed=0;const realNow=Date.now;
 const coordinator:AutonomyCoordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy'||op==='snapshot')return{};
  assert.equal(body.work.budget.seconds,30);assert.ok(body.work.expiresAt>Date.now()+30000,'work identity must cover its remaining bounded steps');
  elapsed=20000;
  await db.query("UPDATE bots SET health=$1 WHERE id='bot-0'",[JSON.stringify({sampledAt:new Date(Date.now()).toISOString(),protocol:{online:true}})]);
  assert.equal(await coordinator.repo.renew(coordinator.active!.claim),true);
  elapsed=31000;
  await db.query("UPDATE bots SET health=$1 WHERE id='bot-0'",[JSON.stringify({sampledAt:new Date(Date.now()).toISOString(),protocol:{online:true}})]);
  return{status:'completed',summary:'actual task completed after ongoing steps',effects:[],evidence:[],unfinished:[],usage};
 });try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active',cycleSeconds:30});Date.now=()=>realNow()+elapsed;
  await handoffGoal(db,goalId);await coordinator.repo.wake('tenant','bot-0',2,'handoff-due',0,goalId);
  await coordinator.tick();await coordinator.active?.task;
  const view=await coordinator.repo.view('tenant','bot-0');assert.equal(view.actions[0].status,'completed');assert.equal(view.goals[0].status,'completed');assert.equal(view.cycles[0].reason,'handoff_executed');
 }finally{Date.now=realNow;await coordinator.stop();await db.close()}
});

test('a handoff accepted with a delay stays dormant until its due wake, then executes after restart',async()=>{
 const db=await fixture();const goalId='edc9a1bd-2a4b-4a35-9f7b-1c2d3e4f5a6c';let executions=0;
 const first=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy'||op==='snapshot')return{};
  if(op==='execute'){executions++;return{status:'completed',summary:'delivered after restart',effects:[],evidence:[],unfinished:[],usage}}
  throw Error(op);
 });
 try{
  await first.repo.configure('tenant','bot-0',1,{mode:'active'});
  await handoffGoal(db,goalId,'bot-0',{notBeforeOffsetMs:3600000});
  await first.repo.wake('tenant','bot-0',2,'handoff-due',3600,goalId);
  await first.tick();await first.active?.task;assert.equal(first.active,undefined,'not-yet-due handoff never keeps a cycle');assert.equal(executions,0);
  await first.stop();
  // Restart: durable state only. Pull the deadline due and let a fresh
  // coordinator process it; no in-memory timer survived.
  await db.query("UPDATE autonomy_wakes SET due_at=now() WHERE bot_id='bot-0' AND reason='handoff-due'");
  await db.query("UPDATE autonomy_goals SET source=jsonb_set(source,'{notBefore}',to_jsonb((extract(epoch from now())*1000)::bigint)) WHERE id=$1",[goalId]);
  const second=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
   if(op==='policy'||op==='snapshot')return{};
   if(op==='execute'){executions++;assert.equal(body.action.goalId,goalId);assert.ok(body.action.requestSource.handoffId===goalId);return{status:'completed',summary:'delivered after restart',effects:[],evidence:[],unfinished:[],usage}}
   throw Error(op);
  });
  await second.tick();await second.active?.task;
  assert.equal(executions,1);
  const view=await second.repo.view('tenant','bot-0');assert.equal(view.goals[0].status,'completed');assert.equal(view.cycles[0].reason,'handoff_executed');
  await second.stop();
 }finally{await db.close()}
});

test('a repeated wake never repeats the executed reply',async()=>{
 const db=await fixture();const goalId='fdc9a1bd-2a4b-4a35-9f7b-1c2d3e4f5a6c';let executions=0;
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy'||op==='snapshot')return{};
  if(op==='execute'){executions++;return{status:'completed',summary:'single delivery',effects:[{id:body.work.id+':0',status:'confirmed',receipt:'once'}],evidence:[],unfinished:[],usage}}
  throw Error(op);
 });
 try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active'});
  await handoffGoal(db,goalId);await coordinator.repo.wake('tenant','bot-0',2,'handoff-due',0,goalId);
  await coordinator.tick();await coordinator.active?.task;
  // A duplicate of the original wake (lost-ack redelivery) must be a no-op.
  await coordinator.repo.wake('tenant','bot-0',2,'handoff-received',0,'');
  await coordinator.repo.wake('tenant','bot-0',2,'handoff-due',0,goalId);
  await coordinator.tick();
  const recheck=coordinator.active as {task:Promise<void>}|undefined;await recheck?.task;assert.equal(coordinator.active,undefined,'completed goal produces no second cycle');
  await coordinator.tick();const again=coordinator.active as {task:Promise<void>}|undefined;await again?.task;assert.equal(executions,1);
  const view=await coordinator.repo.view('tenant','bot-0');assert.equal(view.actions.length,1);assert.equal(view.goals[0].status,'completed');
 }finally{await coordinator.stop();await db.close()}
});

test('two due handoffs execute in one event chain without re-executing the first',async()=>{
 const db=await fixture();const goals=['0ec9a1bd-2a4b-4a35-9f7b-1c2d3e4f5a6c','1ec9a1bd-2a4b-4a35-9f7b-1c2d3e4f5a6c'];const executed:string[]=[];
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy'||op==='snapshot')return{};
  if(op==='execute'){executed.push(body.action.goalId);return{status:'completed',summary:'delivered',effects:[],evidence:[],unfinished:[],usage}}
  throw Error(op);
 });
 try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active'});
  for(const id of goals){await handoffGoal(db,id);await coordinator.repo.wake('tenant','bot-0',2,'handoff-due',0,id)}
  await coordinator.tick();await coordinator.active?.task;
  assert.deepEqual(executed.sort(),goals,'both due handoffs ran in bounded succession');
  const view=await coordinator.repo.view('tenant','bot-0');
  assert.equal(view.actions.length,2);assert.equal(view.goals.every((g:any)=>g.status==='completed'),true);
  assert.equal((await db.query("SELECT count(*)::int AS n FROM autonomy_wakes WHERE bot_id='bot-0'"))[0].n,0,'no polling wake survives settled work');
 }finally{await coordinator.stop();await db.close()}
});
