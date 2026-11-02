import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,readFile} from 'node:fs/promises';
import path from 'node:path';
import {randomUUID} from 'node:crypto';
import {openDatabase} from '../packages/database/index.js';
import {AutonomyRepository} from '../packages/database/autonomous.js';
import {defaultAutonomyConfig,AutonomyConfigSchema,DecisionEnvelopeSchema,ActionIntentSchema} from '../packages/contracts/autonomous.js';

async function fixture(){
 const db=await openDatabase(await mkdtemp(path.resolve('.local/autonomous-state-')));
 for(const tenant of ['ta','tb'])await db.query('INSERT INTO tenants(id,name) VALUES($1,$1)',[tenant]);
 for(const[id,tenant]of[['a','ta'],['b','tb']])await db.query("INSERT INTO bots(id,tenant_id,name,protocol,desired_state,health) VALUES($1,$2,$1,'mock','running',$3)",[id,tenant,JSON.stringify({sampledAt:new Date().toISOString(),protocol:{online:true}})]);
 const repo=new AutonomyRepository(db);return{db,repo};
}

test('message intents require a complete scope instead of an ambiguous QQ number',()=>{
 for(const target of ['2324509593','','private:123','private:12345678/other','group:bad'])assert.equal(ActionIntentSchema.safeParse({kind:'message',target,objective:'authorized contact'}).success,false);
 for(const target of ['private:2324509593','group:1234'])assert.equal(ActionIntentSchema.parse({kind:'message',target,objective:'authorized contact'}).target,target);
 assert.equal(ActionIntentSchema.parse({kind:'research',objective:'read actual context'}).target,'');
});
test('autonomy defaults remain passive; invalid budgets and permissions are rejected',()=>{
 assert.equal(defaultAutonomyConfig.mode,'passive');assert.equal(defaultAutonomyConfig.rollout,'shadow');
 assert.equal(defaultAutonomyConfig.minSleepSeconds,300);
 assert.equal(defaultAutonomyConfig.cycleModelCalls,24);assert.equal(defaultAutonomyConfig.cycleToolCalls,32);assert.equal(defaultAutonomyConfig.cycleTokenBudget,500000);
 assert.equal(defaultAutonomyConfig.fatigueEnabled,true);assert.equal(defaultAutonomyConfig.fatigueWindowMinutes,60);
 assert.throws(()=>AutonomyConfigSchema.parse({fatigueWindowMinutes:0}));
 assert.equal(AutonomyConfigSchema.safeParse({groupAllowlist:['12345']}).success,false,'a second group whitelist must not return');
 assert.throws(()=>AutonomyConfigSchema.parse({cycleTokenBudget:20000,dailyTokenBudget:10000}));
 assert.equal(defaultAutonomyConfig.dailyTokenBudget,0);assert.equal(AutonomyConfigSchema.parse({dailyTokenBudget:0}).dailyTokenBudget,0);assert.throws(()=>AutonomyConfigSchema.parse({dailyTokenBudget:1}));
 assert.throws(()=>AutonomyConfigSchema.parse({minSleepSeconds:5000,maxSleepSeconds:900}));
 assert.throws(()=>AutonomyConfigSchema.parse({capabilities:['shell']}));
 assert.equal(AutonomyConfigSchema.safeParse({capabilities:[]}).success,false,'outer configuration must not contain tool permission switches');
});

test('shared-policy migration preserves goals and receipts, replaces old default sleep and fences old permits once',async()=>{
 const{db,repo}=await fixture();try{
  await repo.configure('ta','a',1,{mode:'active'});
  await repo.configure('tb','b',1,{minSleepSeconds:1800});
  await repo.applied('ta','a',2);
  const claim=await repo.claim('migration-fixture');assert.ok(claim);
  const value=DecisionEnvelopeSchema.parse({expectedGoalRevision:0,goalChanges:[{operation:'create',title:'retain this goal',reason:'existing entrusted work'}],action:{kind:'research',objective:'read existing state'},summary:'in progress',reason:'actual prior task',wait:{kind:'after_result',seconds:0}});
  const committed=await repo.commitDecision(claim,value,{});assert.ok(committed.action);
  await db.query("UPDATE autonomy_settings SET config=config||'{\"groupAllowlist\":[],\"minSleepSeconds\":900}'::jsonb WHERE bot_id='a'");
  await db.query("UPDATE autonomy_settings SET config=config||'{\"groupAllowlist\":[\"98765\"]}'::jsonb WHERE bot_id='b'");
  await repo.wake('ta','a',2,'old-sleep',3600);
  const oldUsage=await db.query('SELECT * FROM autonomy_daily_usage ORDER BY bot_id');
  const sql=await readFile(new URL('../packages/database/migrations/007_shared_autonomous_policy.sql',import.meta.url),'utf8');
  await db.transaction(async tx=>{await tx.query(sql)});
  const a=await repo.view('ta','a'),b=await repo.view('tb','b');
  assert.equal(a.settings.config.minSleepSeconds,600);assert.equal(b.settings.config.minSleepSeconds,1800);
  assert.equal('groupAllowlist' in a.settings.config,false);assert.equal('groupAllowlist' in b.settings.config,false);
  assert.equal(a.settings.epoch,3);assert.equal(b.settings.epoch,3);assert.equal(a.settings.state,'ready');assert.equal(b.settings.state,'disabled');
  assert.equal(a.goals[0].title,'retain this goal');assert.equal(a.goals[0].status,'active');
  assert.equal(a.actions[0].status,'canceled');assert.equal(a.actions[0].id,committed.action!.id);
  assert.deepEqual(await db.query('SELECT * FROM autonomy_daily_usage ORDER BY bot_id'),oldUsage,'migration altered token accounting');
  assert.equal(await repo.renew(claim),false,'old work retained its permit');
  const wakes=await db.query('SELECT * FROM autonomy_wakes');assert.equal(wakes.length,1);assert.equal(wakes[0].reason,'shared-policy-updated');assert.equal(wakes[0].epoch,3);
  await db.transaction(async tx=>{await tx.query(sql)});
  assert.equal((await repo.view('ta','a')).settings.epoch,3,'replaying the migration changed ownership again');
  assert.equal((await db.query('SELECT * FROM autonomy_wakes')).length,1);
 }finally{await db.close()}
});

test('a new decision consumes superseded sleep and its observed event, preserving other wake owners',async()=>{
 const{db,repo}=await fixture();try{
  await repo.configure('ta','a',1,{mode:'active'});await repo.applied('ta','a',2);
  const bot=(await repo.policies()).find(b=>b.bot_id==='a')!;
  await repo.wake('ta','a',2,'autonomous-time',60);
  await repo.event(bot,Date.now(),{scope:'private:23456789',eventId:'observed',senderId:'23456789',directed:true},900);
  await repo.wake('ta','a',2,'real-event',600,'group:12345');
  await repo.wake('ta','a',2,'handoff-due',120,'durable-goal');
  await repo.wake('ta','a',2,'operator-check',400,'private:23456789');
  const claim=await repo.claim('new-decision');assert.ok(claim);
  let wakes=await db.query("SELECT reason,scope FROM autonomy_wakes WHERE bot_id='a'");
  assert.equal(wakes.some(w=>w.reason==='autonomous-time'),false);
  assert.equal(wakes.some(w=>w.reason==='real-event'&&w.scope==='private:23456789'),false);
  assert.ok(wakes.some(w=>w.reason==='real-event'&&w.scope==='group:12345'));
  assert.ok(wakes.some(w=>w.reason==='handoff-due'));assert.ok(wakes.some(w=>w.reason==='operator-check'));
  // A later real event is not part of the claim's observation, even if it
  // belongs to the same person. Finishing must retain its newly queued wake.
  await repo.event(bot,Date.now()+10,{scope:'private:23456789',eventId:'newer',senderId:'23456789',directed:true},900);
  await repo.finish(claim,{tokens:0,modelCalls:0,toolCalls:0,externalActions:0},'sleep',1800);
  wakes=await db.query("SELECT reason,scope,due_at FROM autonomy_wakes WHERE bot_id='a'");
  assert.ok(wakes.some(w=>w.reason==='real-event'&&w.scope==='private:23456789'));
  assert.ok(new Date(wakes.find(w=>w.reason==='autonomous-time').due_at).getTime()>Date.now()+1700000);
  assert.ok(wakes.some(w=>w.reason==='handoff-due'));
 }finally{await db.close()}
});

test('remaining daily tokens form a bounded reservation without changing config or undercharging a crash',async()=>{
 const{db,repo}=await fixture();try{
  await repo.configure('ta','a',1,{mode:'active',cycleTokenBudget:6000,dailyTokenBudget:10000});await repo.applied('ta','a',2);
  await db.query("INSERT INTO autonomy_daily_usage(bot_id,tenant_id,day,used_tokens) VALUES('a','ta',(now() AT TIME ZONE 'Asia/Shanghai')::date,5000)");
  const claims=await Promise.all([repo.claim('one'),repo.claim('two')]);assert.equal(claims.filter(Boolean).length,1);
  const claim=claims.find(Boolean)!;assert.equal(claim.tokenLimit,5000);assert.equal(claim.config.cycleTokenBudget,6000);
  let view=await repo.view('ta','a');assert.equal(view.settings.config.cycleTokenBudget,6000);assert.equal(view.usage.reserved_tokens,5000);
  const[cycle]=await db.query('SELECT budget FROM autonomy_cycles WHERE id=$1',[claim.cycleId]);assert.equal(cycle.budget.cycleTokenBudget,5000);assert.equal(cycle.budget.configuredCycleTokenBudget,6000);
  await repo.finish(claim,{tokens:400,modelCalls:1,toolCalls:0,externalActions:0},'sleep',900);
  view=await repo.view('ta','a');assert.equal(view.usage.used_tokens,5400);assert.equal(view.usage.reserved_tokens,0);
  await repo.wake('ta','a',2,'next',0);const next=await repo.claim('three');assert.equal(next.tokenLimit,4600);
  await db.query("UPDATE autonomy_settings SET lease_until=now()-interval '1 second' WHERE bot_id='a'");
  assert.equal(await repo.claim('after-crash'),null);
  view=await repo.view('ta','a');assert.equal(view.usage.used_tokens,10000);assert.equal(view.usage.reserved_tokens,0);assert.equal(view.settings.state,'budget_wait');
  assert.ok(new Date(view.settings.next_wake_at).getTime()>Date.now());assert.equal((await db.query("SELECT count(*)::int AS n FROM autonomy_wakes WHERE bot_id='a' AND reason='budget-reset'"))[0].n,1,'crash with no pending wake lost next-day recovery');
 }finally{await db.close()}
});

test('foreground handoff registers one persistent goal and due wake; forged identity and early execution are rejected',async()=>{
 const{db,repo}=await fixture();try{
  await db.query("INSERT INTO bot_config_revisions(bot_id,revision,config) SELECT id,revision,$1 FROM bots",[JSON.stringify({behavior:{ownerUin:'12345678'}})]);
  await repo.configure('ta','a',1,{mode:'active'});await repo.applied('ta','a',2);
  const bot=(await repo.policies()).find(b=>b.bot_id==='a')!;
  const request={id:randomUUID(),tenantId:'ta',botId:'a',epoch:2,generation:bot.generation,revision:bot.revision,eventId:'true-event',scope:'private:23456789',senderId:'23456789',at:Date.now(),objective:'read actual state later and report if permitted',delaySeconds:300};
  assert.equal((await repo.acceptHandoff(bot,{...request,tenantId:'tb'})).status,'rejected');
  assert.equal((await repo.acceptHandoff(bot,{...request,scope:'private:12345678'})).status,'rejected');
  const accepted=await repo.acceptHandoff(bot,request);assert.equal(accepted.status,'accepted');
  assert.deepEqual(await new AutonomyRepository(db).acceptHandoff(bot,request),accepted,'lost acknowledgement created another goal');
  assert.equal((await repo.acceptHandoff(bot,{...request,objective:'replace accepted request'})).status,'rejected');
  let view=await repo.view('ta','a');assert.equal(view.goals.length,1);assert.equal(view.goals[0].origin,'contact_request');assert.equal(view.goals[0].source.owner,false);assert.equal(view.goals[0].source.senderId,'23456789');
  assert.equal((await db.query("SELECT * FROM autonomy_wakes WHERE bot_id='a' AND reason='handoff-due'")).length,1);
  const claim=await repo.claim('handoff-worker');assert.ok(claim);
  const decision=DecisionEnvelopeSchema.parse({expectedGoalRevision:view.settings.goal_revision,goalChanges:[],action:{kind:'research',goalId:request.id,objective:request.objective},summary:'received',reason:'read later',wait:{kind:'time',seconds:900}});
  await assert.rejects(repo.commitDecision(claim,decision,{tokens:0,modelCalls:0,toolCalls:0,externalActions:0}),/最早执行时间/);
  await assert.rejects(repo.commitDecision(claim,{...decision,action:null,goalChanges:[{operation:'complete',id:request.id,reason:'not actually done'}]},{tokens:0,modelCalls:0,toolCalls:0,externalActions:0}),/提前标记完成/);
  await repo.finish(claim,{tokens:0,modelCalls:0,toolCalls:0,externalActions:0},'sleep',900);
  view=await repo.view('ta','a');assert.ok(new Date(view.settings.next_wake_at).getTime()<Date.now()+301000,'outer sleep lost earlier handoff due wake');
  await repo.configure('ta','a',2,{mode:'passive'});assert.equal((await repo.acceptHandoff(bot,{...request,id:randomUUID()})).status,'rejected');
  assert.equal((await repo.view('tb','b')).goals.length,0);
 }finally{await db.close()}
});
test('tenant ownership, epoch CAS, wake coalescing and durable pause retain goals',async()=>{
 const{db,repo}=await fixture();try{
  await assert.rejects(repo.view('tb','a'));
  const state=await repo.ensure('ta','a');assert.equal(state.epoch,1);
  await repo.configure('ta','a',1,{mode:'active'});await assert.rejects(repo.configure('ta','a',1,{mode:'passive'}));
  await repo.wake('ta','a',2,'incoming',60,'group:12345');await repo.wake('ta','a',2,'incoming',90,'group:12345');
  assert.equal((await db.query("SELECT * FROM autonomy_wakes WHERE bot_id='a' AND dedupe_key='incoming:group:12345'")).length,1);
  await db.query("INSERT INTO autonomy_goals(id,bot_id,tenant_id,origin,status,title,reason) VALUES('goal-a','a','ta','model','active','ongoing','evidence')");
  await repo.configure('ta','a',2,{mode:'passive'});
  assert.equal((await db.query("SELECT status FROM autonomy_goals WHERE bot_id='a'"))[0].status,'paused_mode');
  assert.equal((await db.query("SELECT * FROM autonomy_wakes WHERE bot_id='a'")).length,0);
  assert.equal((await repo.view('ta','a')).goals.length,1);
 }finally{await db.close()}
});
test('two claimers reserve one cycle; mode and offline fences revoke its lease',async()=>{
 const{db,repo}=await fixture();try{
  await repo.configure('ta','a',1,{mode:'active'});await repo.applied('ta','a',2);
  const claims=await Promise.all([repo.claim('node-1'),repo.claim('node-2')]);
  assert.equal(claims.filter(Boolean).length,1);const c=claims.find(Boolean)!;
  assert.equal((await repo.view('ta','a')).usage.reserved_tokens,c.config.cycleTokenBudget);
  assert.equal(await repo.renew(c),true);
  await db.query("UPDATE bots SET desired_state='offline' WHERE id='a'");await repo.suspendOffline();
  assert.equal(await repo.renew(c),false);assert.equal((await repo.view('ta','a')).settings.state,'suspended_offline');
  assert.equal(await repo.claim('node-2'),null);
  assert.equal((await db.query("SELECT desired_state FROM bots WHERE id='a'"))[0].desired_state,'offline');
 }finally{await db.close()}
});

test('a tenant backlog yields the next turn to another due tenant without delaying older local work',async()=>{
 const{db,repo}=await fixture();try{
  await db.query("INSERT INTO bots(id,tenant_id,name,protocol,desired_state,health) SELECT 'a2',tenant_id,'a2',protocol,desired_state,health FROM bots WHERE id='a'");
  for(const [tenant,id] of [['ta','a'],['ta','a2'],['tb','b']]){
   await repo.configure(tenant,id,1,{mode:'active'});await repo.applied(tenant,id,2);
  }
  // A is the oldest unserved tenant and has two older due wakes than B.
  await db.query("UPDATE autonomy_settings SET updated_at=now()-CASE WHEN tenant_id='ta' THEN interval '2 hours' ELSE interval '1 hour' END");
  await db.query("UPDATE autonomy_wakes SET due_at=now()-CASE WHEN bot_id='a' THEN interval '3 hours' WHEN bot_id='a2' THEN interval '2 hours' ELSE interval '1 hour' END");
  await db.query('UPDATE autonomy_settings s SET next_wake_at=(SELECT min(due_at) FROM autonomy_wakes w WHERE w.bot_id=s.bot_id)');
  const first=await repo.claim('one-worker');assert.equal(first.bot_id,'a');
  const zero={tokens:0,modelCalls:0,toolCalls:0,externalActions:0};await repo.finish(first,zero,'sleep',900);
  await repo.wake('tb','b',2,'new-message',0); // Activity must not count as a scheduling turn.
  const second=await repo.claim('one-worker');assert.equal(second.bot_id,'b','one tenant backlog monopolized the global worker');
  await repo.finish(second,zero,'sleep',900);
  const third=await repo.claim('one-worker');assert.equal(third.bot_id,'a2');await repo.finish(third,zero,'sleep',900);
  assert.equal(await repo.claim('one-worker'),null);
 }finally{await db.close()}
});
test('goal CAS and immutable action ownership reject stale or foreign updates',async()=>{
 const{db,repo}=await fixture();try{
  await repo.configure('ta','a',1,{mode:'active'});await repo.applied('ta','a',2);const c=await repo.claim('node-1');
  const decision=DecisionEnvelopeSchema.parse({expectedGoalRevision:0,goalChanges:[{operation:'create',title:'source-driven goal',reason:'actual source'}],action:{kind:'research',objective:'inspect actual source'},summary:'continue',reason:'interest',wait:{kind:'after_result',seconds:0}});
  const committed=await repo.commitDecision(c,decision,{tokens:10});assert.equal(committed.goalRevision,1);
  await assert.rejects(repo.commitDecision(c,decision,{}));
  assert.equal(await repo.markExecuting({...c,tenant_id:'tb'},committed.action!.id),false);
  assert.equal(await repo.markExecuting(c,committed.action!.id),true);assert.equal(await repo.markExecuting(c,committed.action!.id),false);
  await repo.feedback(c,committed.action!.id,{status:'unknown',summary:'connection lost',effects:[{id:committed.action!.id,status:'unknown'}]});
  assert.equal(await repo.markExecuting(c,committed.action!.id),false,'unknown is never blindly resent');
  await repo.finish(c,{tokens:200,modelCalls:2,toolCalls:1,externalActions:0},'sleep',900);
  await repo.finish(c,{tokens:200,modelCalls:2,toolCalls:1,externalActions:0},'sleep',900);
  const v=await repo.view('ta','a');assert.equal(v.usage.reserved_tokens,0);assert.equal(v.usage.used_tokens,200);assert.equal(v.actions[0].status,'unknown');
 }finally{await db.close()}
});
test('expired lease recovers pending effects as unknown and conservatively settles tokens',async()=>{
 const{db,repo}=await fixture();try{
  await repo.configure('ta','a',1,{mode:'active'});await repo.applied('ta','a',2);const c=await repo.claim('old-node');
  const d=DecisionEnvelopeSchema.parse({expectedGoalRevision:0,goalChanges:[],action:{kind:'message',target:'private:12345678',objective:'greeting'},summary:'waiting receipt',reason:'actual interest',wait:{kind:'after_result',seconds:0}});
  const out=await repo.commitDecision(c,d,{});await repo.markExecuting(c,out.action!.id);
  await db.query("UPDATE autonomy_settings SET lease_until=now()-interval '1 second' WHERE bot_id='a'");
  await repo.wake('ta','a',2,'recovery',0);const restored=await repo.claim('new-node');assert.ok(restored);
  assert.equal((await db.query('SELECT status FROM autonomy_actions WHERE id=$1',[out.action!.id]))[0].status,'unknown');
  assert.equal((await repo.view('ta','a')).usage.used_tokens,c.config.cycleTokenBudget);
  assert.equal(await repo.renew(c),false);
 }finally{await db.close()}
});

test('a known partial delivery does not permanently block a new action; uncertain effects still do',async()=>{
 const{db,repo}=await fixture();try{
  await repo.configure('ta','a',1,{mode:'active',rollout:'live'});await repo.applied('ta','a',2);
  const c=await repo.claim('delivery-owner');assert.ok(c);
  const intent=(objective:string)=>DecisionEnvelopeSchema.parse({expectedGoalRevision:0,goalChanges:[],action:{kind:'message',target:'private:12345678',objective},summary:'actual result known',reason:'new authorized work',wait:{kind:'after_result',seconds:0}});
  const first=(await repo.commitDecision(c,intent('first conversation'),{})).action!;assert.equal(await repo.markExecuting(c,first.id),true);
  await repo.feedback(c,first.id,{status:'partial',summary:'one confirmed bubble; remaining output canceled before dispatch',effects:[{id:first.id+':0',status:'confirmed',receipt:'actual-first-receipt'}]});
  const next=(await repo.commitDecision(c,intent('a distinct later conversation'),{})).action!;
  assert.equal(await repo.markExecuting(c,next.id),true,'a settled partial was treated as a permanently unknown send');
  await repo.feedback(c,next.id,{status:'partial',summary:'one confirmed and one uncertain effect',effects:[{id:next.id+':0',status:'confirmed',receipt:'actual-next-receipt'},{id:next.id+':1',status:'unknown'}]});
  const blocked=(await repo.commitDecision(c,intent('must await the original uncertain receipt'),{})).action!;
  assert.equal(await repo.markExecuting(c,blocked.id),false);
  assert.equal(await repo.markExecuting(c,first.id),false,'settled actions must never be replayed');
  assert.deepEqual((await repo.unconfirmed('ta','a')).map(a=>a.id),[next.id]);
 }finally{await db.close()}
});

test('expired, missing or far-future health cannot renew work or resume an offline autonomous state',async()=>{
 const{db,repo}=await fixture();try{
  await repo.configure('ta','a',1,{mode:'active'});await repo.applied('ta','a',2);const claim=await repo.claim('fresh-health');assert.ok(claim);
  for(const health of [{sampledAt:new Date(Date.now()-120000).toISOString(),protocol:{online:true}},{sampledAt:new Date(Date.now()+600000).toISOString(),protocol:{online:true}},{protocol:{online:true}}]){
   await db.query("UPDATE bots SET health=$1 WHERE id='a'",[JSON.stringify(health)]);
   assert.equal(await repo.renew(claim),false,'stale online flags renewed the effect permit');
   await repo.suspendOffline();assert.equal((await repo.view('ta','a')).settings.state,'suspended_offline');assert.equal(await repo.claim('stale-claim'),null);
  }
  await db.query("UPDATE bots SET health=$1 WHERE id='a'",[JSON.stringify({sampledAt:new Date().toISOString(),protocol:{online:true}})]);await repo.suspendOffline();
  assert.equal((await repo.view('ta','a')).settings.state,'ready');assert.equal((await db.query('SELECT * FROM commands')).length,0);
 }finally{await db.close()}
});

test('login recovery atomically restores one wake after an offline cycle ends, without restarting QQ or passive work',async()=>{
 const{db,repo}=await fixture();try{
  await repo.configure('ta','a',1,{mode:'active'});await repo.applied('ta','a',2);const claim=await repo.claim('before-offline');assert.ok(claim);
  await repo.commitDecision(claim,DecisionEnvelopeSchema.parse({expectedGoalRevision:0,goalChanges:[{operation:'create',title:'preserved goal',reason:'model interest'}],action:null,summary:'progress retained',reason:'continue later',wait:{kind:'time',seconds:900}}),{});
  await db.query("UPDATE bots SET desired_state='offline' WHERE id='a'");await repo.suspendOffline();await repo.finish(claim,{tokens:200,modelCalls:1,toolCalls:0,externalActions:0},'ownership_changed',900);
  assert.equal((await db.query("SELECT * FROM autonomy_wakes WHERE bot_id='a'")).length,0);
  await db.query("UPDATE bots SET desired_state='running',health=$1 WHERE id='a'",[JSON.stringify({protocol:{online:true},sampledAt:new Date().toISOString()})]);
  const wake=repo.wake.bind(repo);repo.wake=async()=>{throw new Error('controlled wake persistence failure')};
  await assert.rejects(repo.suspendOffline(),/controlled wake persistence failure/);assert.equal((await repo.view('ta','a')).settings.state,'suspended_offline','ready committed before its wake');repo.wake=wake;
  await Promise.all([repo.suspendOffline(),new AutonomyRepository(db).suspendOffline()]);
  const view=await repo.view('ta','a'),wakes=await db.query("SELECT reason,epoch FROM autonomy_wakes WHERE bot_id='a'");
  assert.equal(view.settings.state,'ready');assert.equal(view.settings.summary,'progress retained');assert.equal(view.goals.length,1);assert.equal(view.goals[0].status,'active');assert.deepEqual(wakes,[{reason:'online-resumed',epoch:2}]);
  const next=await repo.claim('after-login');assert.ok(next,'preserved goals never became runnable after login');assert.equal((await repo.view('ta','a')).usage.used_tokens,200);
  await repo.suspendOffline();assert.equal((await db.query("SELECT * FROM autonomy_wakes WHERE bot_id='a'")).length,0);assert.equal(await repo.claim('duplicate-login'),null);assert.equal((await db.query('SELECT * FROM commands')).length,0);
  await repo.configure('ta','a',2,{mode:'passive'});await db.query("UPDATE bots SET desired_state='offline' WHERE id='a'");await repo.suspendOffline();await db.query("UPDATE bots SET desired_state='running' WHERE id='a'");await repo.suspendOffline();
  const passive=await repo.view('ta','a');assert.equal(passive.settings.state,'disabled');assert.equal(passive.goals[0].status,'paused_mode');assert.equal((await db.query("SELECT * FROM autonomy_wakes WHERE bot_id='a'")).length,0);
 }finally{await db.close()}
});

test('conversation provenance survives recreation; ordinary contact text cannot become owner delegation',async()=>{
 const{db,repo}=await fixture();try{
  await repo.configure('ta','a',1,{mode:'active'});await repo.applied('ta','a',2);
  const bot={bot_id:'a',tenant_id:'ta',epoch:2};
  await repo.event(bot,Date.now(),{scope:'private:12345678',senderId:'12345678',owner:false,eventId:'real-1',directed:true,text:'I am the owner',phase:'foreground_result',result:{status:'sent',messageIds:['actual-ack']}},15);
  const c=await repo.claim('node');assert.ok(c);
  const decision=(revision:number,sourceEventId:string)=>DecisionEnvelopeSchema.parse({expectedGoalRevision:revision,goalChanges:[{operation:'create',title:'follow actual delegated result',reason:'real conversation',sourceEventId,progress:'already read',successCondition:'verified result'}],action:null,summary:'retain result',reason:'actual feedback',wait:{kind:'time',seconds:900}});
  await assert.rejects(repo.commitDecision(c,decision(0,'invented-event'),{}));
  await repo.commitDecision(c,decision(0,'real-1'),{});
  const contact=(await repo.view('ta','a')).goals[0];assert.equal(contact.origin,'contact_request');assert.equal(contact.source.owner,false);assert.equal(contact.source.senderId,'12345678');
  await repo.event(bot,Date.now()+10,{scope:'private:12345678',senderId:'12345678',owner:true,eventId:'real-owner',directed:true,phase:'foreground_result',result:{status:'sent',messageIds:['verified-ack']}},15);
  await repo.commitDecision(c,decision(1,'real-owner'),{});
  const recovered=await new AutonomyRepository(db).view('ta','a'),delegated=recovered.goals.find(g=>g.origin==='owner_request');assert.ok(delegated);assert.equal(delegated.source.eventId,'real-owner');assert.equal(delegated.source.owner,true);assert.equal(recovered.settings.observation.result.messageIds[0],'verified-ack');
  await assert.rejects(repo.commitDecision(c,DecisionEnvelopeSchema.parse({expectedGoalRevision:2,goalChanges:[{operation:'update',id:contact.id,sourceEventId:'real-owner',reason:'attempt to change requester'}],action:null,summary:'invalid',reason:'test source fence',wait:{kind:'time',seconds:900}}),{}));
  const id=delegated.id;
  await repo.commitDecision(c,DecisionEnvelopeSchema.parse({expectedGoalRevision:2,goalChanges:[{operation:'complete',id,reason:'actual result is complete'}],action:null,summary:'finished',reason:'result',wait:{kind:'time',seconds:900}}),{});
  const goal=(await repo.view('ta','a')).goals.find(g=>g.id===id)!;assert.equal(goal.progress,'already read');assert.equal(goal.success_condition,'verified result');
  await db.query("UPDATE autonomy_goals SET status='paused_user' WHERE id=$1",[id]);
  await assert.rejects(repo.commitDecision(c,DecisionEnvelopeSchema.parse({expectedGoalRevision:3,goalChanges:[{operation:'resume',id,reason:'model wants to resume'}],action:null,summary:'invalid',reason:'test fence',wait:{kind:'time',seconds:900}}),{}));
 }finally{await db.close()}
});

test('finishing a goal frees its slot within the same decision without erasing other goals',async()=>{
 const{db,repo}=await fixture();try{
  await repo.configure('ta','a',1,{mode:'active'});await repo.applied('ta','a',2);const c=await repo.claim('node');
  const ids=Array.from({length:12},()=>randomUUID());for(const id of ids)await db.query("INSERT INTO autonomy_goals(id,bot_id,tenant_id,origin,status,title,reason) VALUES($1,'a','ta','model','active','existing','actual')",[id]);
  await repo.commitDecision(c,DecisionEnvelopeSchema.parse({expectedGoalRevision:0,goalChanges:[{operation:'complete',id:ids[0],reason:'finished'},{operation:'create',title:'new useful goal',reason:'new facts'}],action:null,summary:'switch goal',reason:'actual result',wait:{kind:'time',seconds:900}}),{});
  const view=await repo.view('ta','a');assert.equal(view.goals.filter(g=>g.status==='active').length,12);assert.equal(view.goals.length,13);
 }finally{await db.close()}
});


test('inner-owned capability migration removes outer grants once and preserves modes, goals and receipts',async()=>{
 const{db,repo}=await fixture();try{
  await repo.configure('ta','a',1,{mode:'active',rollout:'live'});
  await repo.configure('tb','b',1,{mode:'passive'});
  const before=await db.query('SELECT * FROM autonomy_settings ORDER BY bot_id');
  await db.query("UPDATE autonomy_settings SET config=config||'{\"capabilities\":[]}'::jsonb");
  const goals=await db.query('SELECT * FROM autonomy_goals'),usage=await db.query('SELECT * FROM autonomy_daily_usage');
  const sql=await readFile(new URL('../packages/database/migrations/008_inner_owned_capabilities.sql',import.meta.url),'utf8');
  await db.transaction(async tx=>{await tx.query(sql)});
  const after=await db.query('SELECT * FROM autonomy_settings ORDER BY bot_id');
  for(let i=0;i<after.length;i++){assert.equal('capabilities' in after[i].config,false);assert.equal(after[i].epoch,before[i].epoch+1);assert.deepEqual(after[i].config,before[i].config)}
  assert.deepEqual(await db.query('SELECT * FROM autonomy_goals'),goals);assert.deepEqual(await db.query('SELECT * FROM autonomy_daily_usage'),usage);
  assert.equal((await db.query("SELECT * FROM autonomy_wakes WHERE bot_id='a'")).length,1);
  assert.equal((await db.query("SELECT * FROM autonomy_wakes WHERE bot_id='b'")).length,0);
  await db.transaction(async tx=>{await tx.query(sql)});assert.deepEqual(await db.query('SELECT * FROM autonomy_settings ORDER BY bot_id'),after,'rerun must not invalidate the next epoch');
 }finally{await db.close()}
});
