import {qqExternalBudget} from '../packages/database/external-action-budget.js';
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

async function savedWork(db:any,id:string,pending:boolean){
 await db.query("INSERT INTO autonomy_cycles(id,bot_id,tenant_id,epoch,generation,config_revision,owner,state,budget,decision,reason,work_expires_at,finished_at) VALUES($1,'bot-0','tenant',2,1,1,'old','completed','{}',$2,$3,now()+interval '1 hour',now())",[id,JSON.stringify({receiptPending:pending}),pending?'activity_unknown':'activity_partial']);
}

/** Accepted Goal metadata. A due wake activates the model, never dispatches this Goal. */
async function handoffGoal(db:any,goalId:string,bot='bot-0',opts:{scope?:string;owner?:boolean;notBeforeOffsetMs?:number;objective?:string}={}){
 // Real accepted work always has its Bot's current configuration revision.
 // Goal reads now verify that authoritative identity before execute IPC.
 await db.query("INSERT INTO bot_config_revisions(bot_id,revision,config) SELECT id,revision,'{}' FROM bots WHERE id=$1 ON CONFLICT(bot_id,revision) DO NOTHING",[bot]);
 const notBefore=Date.now()+(opts.notBeforeOffsetMs??-1000);
 await db.query("INSERT INTO autonomy_goals(id,bot_id,tenant_id,origin,status,title,reason,progress,success_condition,source) VALUES($1,$2,'tenant',$3,'active',$4,'real chat submitted through the authorized foreground tool','',$5,$6)",
  [goalId,bot,opts.owner?'owner_request':'contact_request',opts.objective||'deliver the accepted task result',opts.objective||'deliver the accepted task result',
   JSON.stringify({eventId:'handoff-event',scope:opts.scope||'private:12345678',senderId:'12345678',owner:opts.owner===true,handoffId:goalId,notBefore})]);
}

test('a due handoff wakes the main AI without prescribing a Goal or send target',async()=>{
 const db=await fixture();let executions=0,thinkCalls=0;
 const goalId='b1786cd0-91c4-40ec-8e42-75d80ae70a9d';
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy'||op==='snapshot')return{};
  if(op==='think'){thinkCalls++;return{status:'completed',usage}}
  if(op==='execute'){executions++;
   assert.equal(body.action.kind,'research');assert.equal(body.action.goalId,undefined);
   assert.equal(body.action.target,'');assert.equal(body.action.autonomousMoment,true);
   assert.equal(body.action.activationTrigger,'handoff-due');
   assert.equal(body.action.requestSource,undefined);assert.equal(body.action.goalSnapshot,undefined);
   return{status:'completed',summary:'actual fixture reply delivered',effects:[{id:body.work.id+':0',status:'confirmed',receipt:'fixture-result-ack'}],evidence:[],unfinished:[],usage}}
  throw Error(op);
 });
 try{
  await db.query("INSERT INTO bot_config_revisions(bot_id,revision,config) SELECT id,revision,$1 FROM bots",[JSON.stringify({proactive:false})]);
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active',rollout:'live'});
  await handoffGoal(db,goalId);
  const [originalGoal]=await db.query('SELECT * FROM autonomy_goals WHERE id=$1',[goalId]);
  await coordinator.repo.wake('tenant','bot-0',2,'handoff-due',0,goalId);
  await coordinator.tick();await coordinator.active?.task;
  assert.equal(executions,1,'an accepted user reminder still runs with proactive=false');assert.equal(thinkCalls,0,'no orientation or planning model call exists');
  const view=await coordinator.repo.view('tenant','bot-0');
  assert.deepEqual((await db.query('SELECT * FROM autonomy_goals WHERE id=$1',[goalId]))[0],originalGoal,'delivery cannot modify Goal status, progress, revision or source');
  assert.equal(view.goals[0].status,'active');assert.ok(view.cycles[0].finished_at);
  assert.equal(view.cycles[0].reason,'activity_completed');assert.equal(view.cycles.filter(c=>c.work_expires_at).length,1,'one main activation, no Goal dispatcher');
 }finally{await coordinator.stop();await db.close()}
});

test('proactive off and an idle active-mode wake never start a planning model',async()=>{
 const db=await fixture();const operations:string[]=[];
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  operations.push(op);assert.ok(['policy','snapshot'].includes(op),'idle wake must not execute or think');
  if(op==='snapshot')assert.deepEqual(body,{});return{};
 });
 try{
  await db.query("INSERT INTO bot_config_revisions(bot_id,revision,config) SELECT id,revision,$1 FROM bots",[JSON.stringify({proactive:false})]);
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active'});
  await coordinator.tick();await coordinator.active?.task;
  const view=await coordinator.repo.view('tenant','bot-0');
  assert.equal(view.cycles.filter(c=>c.work_expires_at).length,0);assert.ok(operations.includes('snapshot'));
  assert.equal(view.cycles[0].usage.modelCalls,0);assert.equal(view.settings.config.mode,'active');
 }finally{await coordinator.stop();await db.close()}
});

test('config re-enable preserves unknown Work while fresh accepted wakes carry no chosen Goal',async()=>{
 const db=await fixture();let executes=0,receipts=0;
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy'||op==='snapshot')return{};
  if(op==='receipt'){receipts++;assert.equal(body.id,'unknown-work');return{active:true,effects:[{status:'unknown'}]}}
  if(op==='execute'){executes++;assert.notEqual(body.work.id,'unknown-work');assert.equal(body.action.goalId,undefined);return{status:'completed',effects:[],usage}}
  throw Error(op);
 });try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active'});
  await handoffGoal(db,'pending-goal');await handoffGoal(db,'unknown-goal');
  await savedWork(db,'unknown-work',true);
  await coordinator.tick();await coordinator.active?.task;
  await db.query("INSERT INTO bot_config_revisions(bot_id,revision,config) VALUES('bot-0',2,'{\"proactive\":true}')");
  await db.query("UPDATE bots SET revision=2 WHERE id='bot-0'");
  await coordinator.tick();await coordinator.active?.task;
  assert.ok(receipts>=1);assert.equal(executes,2,'each accepted due wake is a fresh main activation; none redispatches the unknown Work');
  assert.equal((await db.query("SELECT decision->>'receiptPending' AS pending FROM autonomy_cycles WHERE id='unknown-work'"))[0].pending,'true');
  assert.equal((await coordinator.repo.view('tenant','bot-0')).goals.every(g=>g.status==='active'),true);
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
  if(op==='execute'){executions++;assert.equal(body.action.goalId,undefined);return{status:'completed',summary:'actual result',effects:[],evidence:[],unfinished:[],usage}}
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

test('coordinator replacement retains cycle diagnostics without copying execution history',async()=>{
 const db=await fixture(2);
 const coordinator=new AutonomyCoordinator(db,async(_bot,op)=>{
  if(op==='policy'||op==='snapshot')return{};
  if(op==='execute')return{status:'blocked',summary:'PRIVATE-EXECUTION-RESULT',toolResults:[{tool:'bilibili_search',status:'unavailable'}],effects:[],usage};
  throw Error(op);
 });try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active'});
  await coordinator.repo.wake('tenant','bot-0',2,'operator-wake',0);
  await coordinator.tick();await coordinator.active?.task;
  const replacement=new AutonomyCoordinator(db,async()=>({}));
  assert.equal('experience' in replacement.repo,false);
  const view=await replacement.repo.view('tenant','bot-0');
  assert.equal(view.cycles[0].reason,'activity_blocked');assert.ok(view.cycles[0].finished_at);
  assert.equal(JSON.stringify(await db.query('SELECT decision FROM autonomy_cycles')).includes('PRIVATE-EXECUTION-RESULT'),false);
  assert.deepEqual((await replacement.repo.view('tenant','bot-1')).cycles,[]);
  await assert.rejects(replacement.repo.view('different-tenant','bot-0'));await replacement.stop();
 }finally{await coordinator.stop();await db.close()}
});


test('four receipt-bearing bubbles consume one versioned logical chat action without completing the goal',async()=>{
 const receipts:string[]=[];const db=await fixture();const goalId='2c9a1bd2-2a4b-4a35-9f7b-1c2d3e4f5a6c';
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy'||op==='snapshot')return{};
  if(op==='execute'){receipts.push(...Array.from({length:4},(_,n)=>'real-fixture-'+n));await qqExternalBudget(db,{id:body.work.botId,tenant_id:body.work.tenantId},{workId:body.work.id,epoch:body.work.epoch,logicalActionId:body.work.id+':message:private:12345678',phase:'reserve'});return{status:'completed',actionUnitVersion:2,summary:'four bubbles actually confirmed by fixture protocol',effects:Array.from({length:4},(_,n)=>({id:body.work.id+':'+n,logicalActionId:body.work.id+':message:private:12345678',method:'send_private_msg',target:'private:12345678',status:'confirmed',receipt:'real-fixture-'+n})),evidence:[],unfinished:[],usage};}
  throw Error(op);
 });
 try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active',cycleExternalActions:1});
  await handoffGoal(db,goalId);await coordinator.repo.wake('tenant','bot-0',2,'handoff-due',0,goalId);
  await coordinator.tick();await coordinator.active?.task;
  const view=await coordinator.repo.view('tenant','bot-0');
  assert.equal(view.usage.external_actions,1);assert.equal(view.cycles.filter(c=>c.work_expires_at).length,1);
  assert.equal(receipts.length,4);assert.equal(new Set(receipts).size,4);assert.equal(JSON.stringify(view).includes('real-fixture-'),false,'protocol receipts are not copied into cycle diagnostics');
  assert.equal(view.goals[0].status,'active');assert.equal(view.cycles[0].reason,'activity_completed');
 }finally{await coordinator.stop();await db.close()}
});

test('coalesced handoffs yield the next activation to another due member',async()=>{
 const db=await fixture(2),executed:string[]=[];
 const coordinator=new AutonomyCoordinator(db,async(bot,op,body:any)=>{
  if(op==='policy'||op==='snapshot')return{};
  if(op==='execute'){executed.push(bot.bot_id);assert.equal(body.action.goalId,undefined);return{status:'completed',effects:[],usage}}
  throw Error(op);
 });try{
  for(let n=0;n<2;n++)await coordinator.repo.configure('tenant','bot-'+n,1,{mode:'active'});
  for(const [id,bot] of [['first','bot-0'],['second','bot-0'],['peer','bot-1']]){
   await handoffGoal(db,id,bot);await coordinator.repo.wake('tenant',bot,2,'handoff-due',0,id);
  }
  await coordinator.tick();await coordinator.active?.task;await coordinator.tick();await coordinator.active?.task;
  assert.deepEqual(executed,['bot-0','bot-1']);
  await coordinator.tick();await coordinator.active?.task;assert.equal(executed.length,2,'no per-Goal automatic dispatch');
  assert.equal((await coordinator.repo.view('tenant','bot-0')).goals.every(g=>g.status==='active'),true);
 }finally{await coordinator.stop();await db.close()}
});


test('an absent stopped Work closes receipt waiting only when original runtime reports no effects',async()=>{
 const db=await fixture();let executed=0;
 const coordinator=new AutonomyCoordinator(db,async(_bot,op)=>{
  if(op==='policy')return{};
  if(op==='receipt')return{exists:false,active:false,effects:[]};
  if(op==='execute')executed++;throw Error(op);
 });try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active'});await savedWork(db,'stopped',true);
  await coordinator.repo.configure('tenant','bot-0',2,{mode:'passive'});await coordinator.tick();
  assert.equal(executed,0);assert.deepEqual(await coordinator.repo.unconfirmed('tenant','bot-0'),[]);
  assert.equal((await db.query("SELECT reason FROM autonomy_cycles WHERE id='stopped'"))[0].reason,'receipt_resolved');
  assert.equal((await coordinator.repo.view('tenant','bot-0')).usage?.external_actions??0,0);
 }finally{await coordinator.stop();await db.close()}
});


test('foreground feedback advances the version fence; a canceled dispatch keeps its retry wake',async()=>{
 const db=await fixture();const goalId='5c9a1bd2-2a4b-4a35-9f7b-1c2d3e4f5a6c';const at=Date.now()-1000;
 let snapshots=0,executions=0,latestVersion=0;
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy')return{};
  if(op==='snapshot'){snapshots++;latestVersion=at+snapshots;return{feedbackVersion:latestVersion,foregroundFeedback:{at:latestVersion,phase:'foreground_result',eventId:'reply-'+snapshots,scope:'private:12345678',senderId:'12345678',directed:true,result:{status:'sent'}}}}
  if(op==='execute'){executions++;assert.equal(body.work.feedbackVersion,latestVersion,'work carries the version the cycle actually observed');return{status:'canceled',continuationAccepted:false,stopReason:'not_started',summary:'foreground invalidated work before side effects',effects:[],evidence:[],unfinished:[],usage:{tokens:0,modelCalls:0,toolCalls:0}}}
  throw Error(op);
 });
 try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active'});
  await handoffGoal(db,goalId);await coordinator.repo.wake('tenant','bot-0',2,'handoff-due',0,goalId);
  await coordinator.tick();await coordinator.active?.task;
  const view=await coordinator.repo.view('tenant','bot-0');
  assert.equal(executions,1);assert.equal(view.cycles[0].reason,'activity_canceled');assert.equal(view.goals[0].status,'active','a canceled dispatch is not a terminal result');
  assert.equal(view.cycles[0].reason,'activity_canceled');
  const delay=new Date(view.settings.next_wake_at).getTime()-Date.now();assert.ok(delay>25000&&delay<=31000,'foreground interruption must not strand the accepted handoff');
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

test('accepted task result flows back, preserves the goal and leaves no polling wake',async()=>{
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
  assert.equal(view.goals[0].status,'active');assert.equal(view.settings.state,'sleeping');
  assert.equal(view.usage.used_tokens,1200);assert.equal(view.usage.reserved_tokens,0);assert.equal(coordinator.active,undefined);
  assert.equal(view.cycles[0].reason,'activity_completed');
  assert.equal(await db.query("SELECT count(*)::int AS n FROM autonomy_wakes WHERE bot_id=$1 AND reason<>'activity'",['bot-0']).then((r:any)=>r[0].n),0,'terminal handoff leaves no task retry wake; periodic activity has its own wake');
  // A new supervisor reads durable state only; the completed goal is not re-run.
  const next=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
   if(op==='policy'||op==='snapshot')return{};
   if(op==='execute')throw Error('completed goal must not be re-executed');
   throw Error(op);
  });
  await next.repo.wake('tenant','bot-0',2,'actual-event',0);await next.tick();await next.active?.task;assert.equal(next.active,undefined);await next.stop();
 }finally{await coordinator.stop();await db.close()}
});

test('a blocked activation preserves the Goal while late original receipts settle through the tick loop',async()=>{
 const db=await fixture();let executions=0,receipts=0;
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy'||op==='snapshot')return{};
  if(op==='execute'){executions++;return{status:'blocked',effects:[],usage}}
  if(op==='receipt'){receipts++;assert.equal(body.id,'late-work');return{result:{status:'completed',effects:[{status:'confirmed',receipt:'real-late-id'}]}}}
  throw Error(op);
 });try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active'});await handoffGoal(db,'blocked-goal');
  await coordinator.repo.wake('tenant','bot-0',2,'handoff-due',0,'blocked-goal');await coordinator.tick();await coordinator.active?.task;
  assert.equal((await coordinator.repo.view('tenant','bot-0')).cycles[0].reason,'activity_blocked');
  await savedWork(db,'late-work',true);(coordinator as any).sampled.clear();await coordinator.tick();
  assert.equal(receipts,1);assert.equal(executions,1);assert.deepEqual(await coordinator.repo.unconfirmed('tenant','bot-0'),[]);
  assert.equal((await coordinator.repo.view('tenant','bot-0')).goals[0].status,'active');
 }finally{await coordinator.stop();await db.close()}
});


test('an uncertain effect remains owned by the original Work and idle wakes never re-dispatch it',async()=>{
 const db=await fixture();let executions=0,workId='';
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy'||op==='snapshot')return{};
  if(op==='receipt')return{active:true,result:{status:'unknown',effects:[{status:'unknown'}]}};
  if(op==='execute'){executions++;workId=body.work.id;await qqExternalBudget(db,{id:body.work.botId,tenant_id:body.work.tenantId},{workId,epoch:body.work.epoch,logicalActionId:workId+':0',phase:'reserve'});return{status:'unknown',effects:[{status:'unknown'}],usage}}
  throw Error(op);
 });try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active'});
  await coordinator.repo.wake('tenant','bot-0',2,'operator-wake',0);await coordinator.tick();await coordinator.active?.task;
  (coordinator as any).sampled.clear();await coordinator.repo.wake('tenant','bot-0',2,'later',0);await coordinator.tick();await coordinator.active?.task;
  const view=await coordinator.repo.view('tenant','bot-0');assert.equal(executions,1);assert.equal(view.usage.external_actions,1);
  const [original]=await db.query('SELECT decision,reason FROM autonomy_cycles WHERE id=$1',[workId]);
  assert.equal(original.reason,'activity_unknown');assert.equal(original.decision.receiptPending,true);
  assert.equal(original.decision.receiptCheck.attempts,1);assert.ok(original.decision.receiptCheck.nextAt>Date.now()/1000+25);
  assert.deepEqual(await coordinator.repo.unconfirmed('tenant','bot-0'),[],'the next original receipt check observes backoff');
 }finally{await coordinator.stop();await db.close()}
});


test('partial Work recovery: confirmed_message never decides Goal completion or repeats execution',async()=>{
 const db=await fixture();let executions=0,checks=0;
 const uncertain=false;
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy')return{};
  if(op==='receipt'){checks++;assert.equal(body.id,'partial-work');return{active:true,result:{status:'partial',effects:[{status:'unknown'}]}}}
  if(op==='execute')executions++;throw Error(op);
 });try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active'});await handoffGoal(db,'partial-goal');
  await savedWork(db,'partial-work',uncertain);
  await coordinator.repo.configure('tenant','bot-0',2,{mode:'passive'});await coordinator.tick();
  assert.equal(executions,0);assert.equal(checks,uncertain?1:0);
  const view=await coordinator.repo.view('tenant','bot-0');assert.equal(view.goals[0].status,'paused_mode','explicit mode pause is preserved; receipt recovery does not decide completion');
  assert.equal(view.goals[0].progress,'');assert.equal(view.cycles.length,1);
  assert.equal((await db.query('SELECT * FROM autonomy_wakes')).length,0);
  assert.equal((await coordinator.repo.unconfirmed('tenant','bot-0')).length,0,'uncertain checks are bounded by backoff, settled results are never polled');
 }finally{await coordinator.stop();await db.close()}
});


test('partial Work recovery: no_completion_evidence never decides Goal completion or repeats execution',async()=>{
 const db=await fixture();let executions=0,checks=0;
 const uncertain=false;
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy')return{};
  if(op==='receipt'){checks++;assert.equal(body.id,'partial-work');return{active:true,result:{status:'partial',effects:[{status:'unknown'}]}}}
  if(op==='execute')executions++;throw Error(op);
 });try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active'});await handoffGoal(db,'partial-goal');
  await savedWork(db,'partial-work',uncertain);
  await coordinator.repo.configure('tenant','bot-0',2,{mode:'passive'});await coordinator.tick();
  assert.equal(executions,0);assert.equal(checks,uncertain?1:0);
  const view=await coordinator.repo.view('tenant','bot-0');assert.equal(view.goals[0].status,'paused_mode','explicit mode pause is preserved; receipt recovery does not decide completion');
  assert.equal(view.goals[0].progress,'');assert.equal(view.cycles.length,1);
  assert.equal((await db.query('SELECT * FROM autonomy_wakes')).length,0);
  assert.equal((await coordinator.repo.unconfirmed('tenant','bot-0')).length,0,'uncertain checks are bounded by backoff, settled results are never polled');
 }finally{await coordinator.stop();await db.close()}
});


test('partial Work recovery: unfinished_read never decides Goal completion or repeats execution',async()=>{
 const db=await fixture();let executions=0,checks=0;
 const uncertain=false;
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy')return{};
  if(op==='receipt'){checks++;assert.equal(body.id,'partial-work');return{active:true,result:{status:'partial',effects:[{status:'unknown'}]}}}
  if(op==='execute')executions++;throw Error(op);
 });try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active'});await handoffGoal(db,'partial-goal');
  await savedWork(db,'partial-work',uncertain);
  await coordinator.repo.configure('tenant','bot-0',2,{mode:'passive'});await coordinator.tick();
  assert.equal(executions,0);assert.equal(checks,uncertain?1:0);
  const view=await coordinator.repo.view('tenant','bot-0');assert.equal(view.goals[0].status,'paused_mode','explicit mode pause is preserved; receipt recovery does not decide completion');
  assert.equal(view.goals[0].progress,'');assert.equal(view.cycles.length,1);
  assert.equal((await db.query('SELECT * FROM autonomy_wakes')).length,0);
  assert.equal((await coordinator.repo.unconfirmed('tenant','bot-0')).length,0,'uncertain checks are bounded by backoff, settled results are never polled');
 }finally{await coordinator.stop();await db.close()}
});


test('partial Work recovery: unknown_message never decides Goal completion or repeats execution',async()=>{
 const db=await fixture();let executions=0,checks=0;
 const uncertain=true;
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy')return{};
  if(op==='receipt'){checks++;assert.equal(body.id,'partial-work');return{active:true,result:{status:'partial',effects:[{status:'unknown'}]}}}
  if(op==='execute')executions++;throw Error(op);
 });try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active'});await handoffGoal(db,'partial-goal');
  await savedWork(db,'partial-work',uncertain);
  await coordinator.repo.configure('tenant','bot-0',2,{mode:'passive'});await coordinator.tick();
  assert.equal(executions,0);assert.equal(checks,uncertain?1:0);
  const view=await coordinator.repo.view('tenant','bot-0');assert.equal(view.goals[0].status,'paused_mode','explicit mode pause is preserved; receipt recovery does not decide completion');
  assert.equal(view.goals[0].progress,'');assert.equal(view.cycles.length,1);
  assert.equal((await db.query('SELECT * FROM autonomy_wakes')).length,0);
  assert.equal((await coordinator.repo.unconfirmed('tenant','bot-0')).length,0,'uncertain checks are bounded by backoff, settled results are never polled');
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

test('passive mode settles original Work receipts without a model call, new wake or repeat effect',async()=>{
 const db=await fixture();let receiptCalls=0,executions=0,settled=false;
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy')return{};
  if(op==='receipt'){receiptCalls++;assert.equal(body.id,'kept-unknown');return{result:{status:settled?'completed':'unknown',effects:[{status:settled?'confirmed':'unknown',receipt:settled?'real-late-id':undefined}]}}}
  if(op==='execute')executions++;throw Error(op);
 });try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active'});await savedWork(db,'kept-unknown',true);
  await coordinator.repo.configure('tenant','bot-0',2,{mode:'passive'});await coordinator.tick();
  assert.equal(receiptCalls,1);assert.equal(executions,0);assert.equal((await coordinator.repo.policies()).length,1);
  settled=true;await db.query("UPDATE autonomy_cycles SET decision=decision-'receiptCheck' WHERE id='kept-unknown'");
  (coordinator as any).sampled.clear();await coordinator.tick();
  const view=await coordinator.repo.view('tenant','bot-0');assert.equal(view.settings.runtime_epoch,3);assert.equal(receiptCalls,2);
  assert.equal((await coordinator.repo.policies()).length,0);assert.deepEqual(await coordinator.repo.unconfirmed('tenant','bot-0'),[]);
  assert.equal((await db.query('SELECT * FROM autonomy_wakes')).length,0);assert.equal((await db.query('SELECT * FROM commands')).length,0);
 }finally{await coordinator.stop();await db.close()}
});


test('settled cycle diagnostics cannot starve uncertain original Work receipts',async()=>{
 const db=await fixture(),coordinator=new AutonomyCoordinator(db,async()=>({}));
 try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active'});await coordinator.repo.applied('tenant','bot-0',2);
  for(let n=0;n<20;n++)await savedWork(db,'settled-'+n,false);
  await savedWork(db,'uncertain',true);
  assert.deepEqual((await coordinator.repo.unconfirmed('tenant','bot-0')).map(w=>w.id),['uncertain']);
  assert.deepEqual(await coordinator.repo.unconfirmed('other-tenant','bot-0'),[]);
  await coordinator.repo.configure('tenant','bot-0',2,{mode:'passive'});await coordinator.repo.applied('tenant','bot-0',3);
  assert.equal((await coordinator.repo.policies()).length,1);
 }finally{await coordinator.stop();await db.close()}
});


test('expired health never claims a cycle; recovery re-arms and dispatches the due handoff',async()=>{
 const db=await fixture();const goalId='9c9a1bd2-2a4b-4a35-9f7b-1c2d3e4f5a6c';let executions=0;
 const coordinator=new AutonomyCoordinator(db,async(_bot,op,body:any)=>{
  if(op==='policy'||op==='snapshot')return{};
  if(op==='execute'){executions++;assert.equal(body.action.goalId,undefined);return{status:'completed',summary:'delivered after recovery',effects:[],evidence:[],unfinished:[],usage}}
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
  const view=await coordinator.repo.view('tenant','bot-0');assert.equal(view.goals[0].status,'active');
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

test('bounded work identity covers its remaining steps and the Goal stays unchanged after late execution completion',async()=>{
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
  const view=await coordinator.repo.view('tenant','bot-0');assert.ok(view.cycles[0].finished_at);assert.equal(view.goals[0].status,'active');assert.equal(view.cycles[0].reason,'activity_completed');
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
   if(op==='execute'){executions++;assert.equal(body.action.goalId,undefined);assert.equal(body.action.requestSource,undefined);return{status:'completed',summary:'delivered after restart',effects:[],evidence:[],unfinished:[],usage}}
   throw Error(op);
  });
  await second.tick();await second.active?.task;
  assert.equal(executions,1);
  const view=await second.repo.view('tenant','bot-0');assert.equal(view.goals[0].status,'active');assert.equal(view.cycles[0].reason,'activity_completed');
  await second.stop();
 }finally{await db.close()}
});

test('settled Work and unchanged Goals do not generate a second execution on idle ticks',async()=>{
 const db=await fixture();let executions=0;
 const coordinator=new AutonomyCoordinator(db,async(_bot,op)=>{
  if(op==='policy'||op==='snapshot')return{};
  if(op==='execute'){executions++;return{status:'completed',effects:[{status:'confirmed',receipt:'once'}],usage}}
  throw Error(op);
 });try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active'});await handoffGoal(db,'unchanged-goal');
  await coordinator.repo.wake('tenant','bot-0',2,'handoff-due',0,'unchanged-goal');await coordinator.tick();await coordinator.active?.task;
  for(let n=0;n<3;n++){await coordinator.tick();await coordinator.active?.task}
  assert.equal(executions,1);const view=await coordinator.repo.view('tenant','bot-0');
  assert.equal(view.cycles.filter(c=>c.work_expires_at).length,1);assert.equal(view.goals[0].status,'active');
 }finally{await coordinator.stop();await db.close()}
});


test('two due handoffs coalesce into one activation and leave both Goals to the main model',async()=>{
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
  assert.deepEqual(executed,[undefined],'one wake does not automatically iterate or select Goals');
  const view=await coordinator.repo.view('tenant','bot-0');
  assert.equal(view.cycles.filter(c=>c.work_expires_at).length,1);assert.equal(view.goals.every((g:any)=>g.status==='active'),true);
  assert.equal((await db.query("SELECT count(*)::int AS n FROM autonomy_wakes WHERE bot_id='bot-0' AND reason<>'activity'"))[0].n,0,'no polling wake survives settled work');
 }finally{await coordinator.stop();await db.close()}
});


test('agreement cancellation fences its own goal without touching another tenant or replaying effects',async()=>{
 const db=await fixture();const coordinator=new AutonomyCoordinator(db,async()=>({}));
 const goalId='cfc9a1bd-2a4b-4a35-9f7b-1c2d3e4f5a6c',agreementId='0123456789abcdef01234567';
 try{
  await coordinator.repo.configure('tenant','bot-0',1,{mode:'active'});await handoffGoal(db,goalId);
  await db.query("UPDATE autonomy_goals SET source=source||$2::jsonb WHERE id=$1",[goalId,JSON.stringify({agreement:{id:agreementId,revision:2},kind:'research',origin:'model'})]);
  await coordinator.repo.reconcileAgreements({bot_id:'foreign',tenant_id:'tenant'},[{id:agreementId,handoffId:goalId,revision:3,taskRevision:2,status:'canceled'}]);
  assert.equal((await db.query('SELECT status FROM autonomy_goals WHERE id=$1',[goalId]))[0].status,'active');
  await coordinator.repo.reconcileAgreements({bot_id:'bot-0',tenant_id:'tenant'},[{id:agreementId,handoffId:goalId,revision:3,taskRevision:2,status:'canceled'}]);
  assert.equal((await db.query('SELECT status FROM autonomy_goals WHERE id=$1',[goalId]))[0].status,'abandoned');
  assert.equal((await db.query("SELECT count(*)::int AS n FROM information_schema.tables WHERE table_name='autonomy_actions'"))[0].n,0);
 }finally{await coordinator.stop();await db.close()}
});
