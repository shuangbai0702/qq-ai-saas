import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';
import path from 'node:path';
import {openDatabase} from '../packages/database/index.js';
import {AutonomyRepository} from '../packages/database/autonomous.js';
import {cycleModelBudget as requestModelBudget,type ModelRequestBudget} from '../packages/database/model-request-budget.js';
import {openToolWork} from './helpers/tool-work.js';
import {qqExternalBudget,externalCount} from '../packages/database/external-action-budget.js';

const cycleId='00000000-0000-4000-8000-000000000011',actionId=cycleId,effectId='299786bd-5764-5ef8-9201-8e90440a52e6';
const NATIVE=Symbol('isolated-original-tool-work');
const original=(db:any)=>db[NATIVE] as Awaited<ReturnType<typeof openToolWork>>;
const cycleModelBudget=(db:any,bot:any,v:ModelRequestBudget)=>requestModelBudget(db,bot,v,original(db).transport);
async function fixture(t:any){
 const root=await mkdtemp(path.resolve('.local/model-request-'));let db=await openDatabase(root);
 await db.query("INSERT INTO tenants(id,name) VALUES('tenant','tenant')");
 await db.query("INSERT INTO bots(id,tenant_id,name,protocol,desired_state,health) VALUES('bot','tenant','bot','mock','running',$1)",[JSON.stringify({sampledAt:new Date().toISOString(),protocol:{online:true},ai:{ready:true}})]);
 await db.query("INSERT INTO bot_config_revisions(bot_id,revision,config) VALUES('bot',1,'{}')");
 const repo=new AutonomyRepository(db);await repo.configure('tenant','bot',1,{mode:'active'});
 const [bot]=await db.query("SELECT * FROM bots WHERE id='bot'");
 await db.query("INSERT INTO autonomy_daily_usage(bot_id,tenant_id,day,reserved_tokens) VALUES('bot','tenant','fixture',1000)");
 await db.query("INSERT INTO autonomy_cycles(id,bot_id,tenant_id,epoch,generation,config_revision,owner,state,work_expires_at,budget) VALUES($1,'bot','tenant',2,$2,$3,'test','acting',now()+interval '5 minutes','{\"day\":\"fixture\",\"cycleTokenBudget\":1000,\"cycleModelCalls\":1}')",[cycleId,bot.generation,bot.revision]);
 const native=await openToolWork(root,db,bot,actionId,2);Object.assign(db,{[NATIVE]:native});
 await native.call('admit',{record:{bot_id:bot.id,tenant_id:bot.tenant_id,generation:bot.generation,config_revision:bot.revision,work_id:actionId,step_id:'analysis',args_digest:'hash',operation:'analyze',args:{},write:false,authority:'autonomous',mode_epoch:2,settings_revision:1,deadlineAt:Date.now()+300000,continuation:{}}});
 assert.equal((await native.call('claim',{owner:'worker'})).id,effectId);
 t.after(async()=>{await native.close();await db.close();await rm(root,{recursive:true,force:true})});
 return{db,repo,bot,root,native,reopen:async()=>{await db.close();db=await openDatabase(root);native.repo.db=db;Object.assign(db,{[NATIVE]:native});return db}};
}
const main={workId:actionId,epoch:2,requestId:'main:1',phase:'reserve' as const,source:'main' as const,tokens:200};
const media={...main,requestId:'media:1',source:'media' as const,effectId,owner:'worker'};

test('media receipt is fetched outside the budget transaction and ownership is rechecked after IPC',async t=>{
 const {db,bot,native}=await fixture(t);let inTransaction=false,calls=0;
 const observed=Object.create(db);
 observed.query=db.query.bind(db);
 observed.transaction=(body:any)=>db.transaction(async tx=>{inTransaction=true;try{return await body(tx)}finally{inTransaction=false}});
 const transport:typeof native.transport=async(...args)=>{
  assert.equal(inTransaction,false,'worker IPC must not hold the budget transaction');calls++;
  return native.transport(...args);
 };
 await requestModelBudget(observed,bot,media,transport);
 assert.equal(calls,1);
 await requestModelBudget(observed,bot,{...media,phase:'released'},transport);
 assert.equal(calls,1,'settlement does not fetch a second worker receipt');
 await assert.rejects(()=>requestModelBudget(observed,bot,{...media,requestId:'media:stale'},async(...args)=>{
  const receipt=await transport(...args);
  await db.query("UPDATE bots SET revision=revision+1 WHERE id='bot'");
  return receipt;
 }),/model_budget_owner_expired/);
 const [cycle]=await db.query('SELECT budget FROM autonomy_cycles WHERE id=$1',[cycleId]);
 assert.equal(cycle.budget.modelRequests['media:stale'],undefined);
});

for(const order of ['main','media'])test('shared cycle admits one concurrent '+order+' and other-source request before dispatch',async t=>{
 const {db,bot}=await fixture(t);
 let enter!:()=>void;const barrier=new Promise<void>(resolve=>enter=resolve);
 const requests=order==='main'?[main,media]:[media,main];
 let dispatched=0;
 const calls=requests.map(async request=>{await barrier;await cycleModelBudget(db,bot,request);dispatched++});
 enter();const outcomes=await Promise.allSettled(calls);
 assert.equal(outcomes.filter(x=>x.status==='fulfilled').length,1);
 assert.equal(dispatched,1,'the rejected in-flight request cannot reach the provider');
 assert.match(String((outcomes.find(x=>x.status==='rejected') as PromiseRejectedResult).reason),/model_budget_exhausted/);
});

test('main, media and QQ reservations preserve each other in the same cycle budget',async t=>{
 const {db,bot}=await fixture(t);
 await db.query("UPDATE autonomy_cycles SET budget=budget||'{\"cycleModelCalls\":2,\"cycleExternalActions\":1}'::jsonb WHERE id=$1",[cycleId]);
 await Promise.all([cycleModelBudget(db,bot,main),cycleModelBudget(db,bot,media),qqExternalBudget(db,bot,{workId:actionId,epoch:2,logicalActionId:'qq-output',phase:'reserve'})]);
 const [cycle]=await db.query('SELECT budget FROM autonomy_cycles WHERE id=$1',[cycleId]);
 assert.deepEqual(Object.keys(cycle.budget.modelRequests).sort(),['main:1','media:1']);
 assert.equal(cycle.budget.qqExternalRequests[actionId]['qq-output'].status,'reserved');
 assert.equal(await externalCount(db,cycleId),1);
 await Promise.all([cycleModelBudget(db,bot,{...main,phase:'settled',tokens:40}),cycleModelBudget(db,bot,{...media,phase:'released'}),qqExternalBudget(db,bot,{workId:actionId,epoch:2,logicalActionId:'qq-output',phase:'unknown'})]);
 const [settled]=await db.query('SELECT budget FROM autonomy_cycles WHERE id=$1',[cycleId]);
 assert.equal(settled.budget.modelRequests['main:1'].tokens,40);
 assert.equal(settled.budget.modelRequests['media:1'].status,'released');
 assert.equal(settled.budget.qqExternalRequests[actionId]['qq-output'].status,'unknown');
});

test('failure release is monotonic, unknown survives restart and late receipt settles original day once',async t=>{
 const f=await fixture(t);let db=f.db;
 await cycleModelBudget(db,f.bot,main);
 await assert.rejects(()=>cycleModelBudget(db,f.bot,main),/already_reserved/);
 await cycleModelBudget(db,f.bot,{...main,phase:'released'});
 await cycleModelBudget(db,f.bot,{...main,phase:'settled',tokens:1});
 const request={...main,requestId:'main:2'};
 await cycleModelBudget(db,f.bot,request);
 await cycleModelBudget(db,f.bot,{...request,phase:'unknown'});
 await cycleModelBudget(db,f.bot,{...request,phase:'released'});
 db=await f.reopen();
 await assert.rejects(()=>cycleModelBudget(db,f.bot,{...main,requestId:'main:3'}),/budget_exhausted/);
 const repo=new AutonomyRepository(db);
 const claim={cycleId,bot_id:'bot',tenant_id:'tenant',epoch:2,owner:'test',tokenLimit:1000,config:{cycleTokenBudget:1000},day:'fixture'};
 await repo.finish(claim,{tokens:0,modelCalls:0,toolCalls:0,externalActions:0},'activity_completed');
 await cycleModelBudget(db,f.bot,{...request,phase:'settled',tokens:75});
 await cycleModelBudget(db,f.bot,{...request,phase:'settled',tokens:75});
 await cycleModelBudget(db,f.bot,{...request,phase:'unknown',tokens:999});
 const [cycle]=await db.query('SELECT * FROM autonomy_cycles WHERE id=$1',[cycleId]),[day]=await db.query("SELECT * FROM autonomy_daily_usage WHERE bot_id='bot' AND day='fixture'");
 assert.equal(cycle.budget.modelRequests['main:2'].status,'settled');assert.equal(cycle.usage.modelCalls,1);assert.equal(Number(cycle.usage.tokens),75);assert.equal(day.model_calls,1);assert.equal(Number(day.used_tokens),75);
});

test('request identity, bot ownership, expired effects and configuration generation block reserve',async t=>{
 const {db,bot}=await fixture(t);
 await assert.rejects(()=>cycleModelBudget(db,{...bot,id:'other-bot'},main),/owner_missing/);
 await assert.rejects(()=>cycleModelBudget(db,bot,{...media,owner:'other-worker'}),/effect_expired/);
 await cycleModelBudget(db,bot,main);
 await assert.rejects(()=>cycleModelBudget(db,bot,{...main,phase:'settled',source:'media'}),/identity_conflict/);
 await cycleModelBudget(db,bot,{...main,phase:'released'});
 await db.query("UPDATE bots SET revision=revision+1 WHERE id='bot'");
 await assert.rejects(()=>cycleModelBudget(db,bot,{...main,requestId:'stale'}),/owner_expired/);
});

test('lost dispatched analyze publishes unknown completion and duplicate receipt cannot reopen consumed completion',async t=>{
 const {db}=await fixture(t),effects=original(db).repo;
 await original(db).sql("UPDATE pipeline_events SET status='sending',execution_started=1,lease_until=0 WHERE source='tool' AND event=?",[effectId]);
 assert.equal(await effects.claim('replacement'),null);
 const job=await resultJob(db);
 assert.equal(job.state,'unknown');assert.equal(job.completion_state,'pending');
 await original(db).sql("UPDATE pipeline_events SET request=json_set(request,'$.completion.state','done') WHERE event=?",[effectId]);
 const unknown=job.result;await effects.finish(job,unknown);
 assert.equal((await resultJob(db)).completion_state,'done');
 const receipt={...unknown,execution:'completed',receipt:{status:'not_applicable'},data:{analysis:'late authoritative result'}};
 await effects.finish(job,receipt);
 assert.equal((await resultJob(db)).completion_state,'pending','a genuinely new authoritative result needs cognition');
 await original(db).sql("UPDATE pipeline_events SET request=json_set(request,'$.completion.state','done') WHERE event=?",[effectId]);
 await effects.finish(job,receipt);
 assert.equal((await resultJob(db)).completion_state,'done');
});

test('media crash after model reservation keeps unknown responsibility and cannot replay claimed work',async t=>{
 const {db,bot}=await fixture(t),effects=original(db).repo;
 await cycleModelBudget(db,bot,media);
 await original(db).call('advance',{id:effectId,owner:'worker',phase:'checkpoint',usage:{modelRequests:1}});
 await original(db).sql("UPDATE pipeline_events SET lease_until=0 WHERE source='tool' AND event=?",[effectId]);
 assert.equal(await effects.claim('new-process'),null);
 const job=await resultJob(db);
 assert.equal(job.state,'unknown');assert.equal(job.completion_state,'pending');
 assert.equal(job.lease_owner,'worker','the crashed original owner retains the only authoritative late receipt path');
 await assert.rejects(()=>cycleModelBudget(db,bot,{...main,requestId:'after-crash'}),/budget_exhausted/);
});

const claim={cycleId,bot_id:'bot',tenant_id:'tenant',epoch:2,owner:'test',tokenLimit:1000,config:{cycleTokenBudget:1000},day:'fixture'};
const zeroUsage={tokens:0,modelCalls:0,toolCalls:0,externalActions:0};
async function balance(db:any){return (await db.query("SELECT * FROM autonomy_daily_usage WHERE bot_id='bot' AND day='fixture'"))[0]}
async function resultJob(db:any){return original(db).repo.get('tenant','bot',effectId)}
const mediaReceipt:any={schemaVersion:'fishbot.tool-result.v1',actionId:effectId,execution:'completed',receipt:{status:'not_applicable'},usage:{modelRequests:1,providerUsage:{total_tokens:75}},versions:{adapter:'bilibili-web-v1'}};

test('finished cycle admits its live media owner, reacquires original-day capacity and settles once across effect receipt',async t=>{
 const {db,repo,bot}=await fixture(t);
 await repo.finish(claim,zeroUsage,'activity_completed');
 assert.equal(Number((await balance(db)).reserved_tokens),0);
 await assert.rejects(()=>cycleModelBudget(db,bot,main),/owner_expired/);
 await cycleModelBudget(db,bot,media);
 assert.equal(Number((await balance(db)).used_tokens),200);
 await cycleModelBudget(db,bot,{...media,phase:'settled',tokens:75});
 const effects=original(db).repo,job=await resultJob(db);
 await effects.finish(job,mediaReceipt);await effects.finish(job,mediaReceipt);
 const day=await balance(db),[cycle]=await db.query('SELECT * FROM autonomy_cycles WHERE id=$1',[cycleId]);
 assert.equal(Number(day.used_tokens),75);assert.equal(day.model_calls,1);
 assert.equal(Number(cycle.usage.tokens),75);assert.equal(cycle.usage.modelCalls,1);
 assert.equal(Number(cycle.usage.mediaTokens),75);assert.equal(cycle.usage.mediaModelCalls,1);assert.equal(cycle.usage.mediaTokenStatus,'actual');
 assert.equal(cycle.budget.modelRequests['media:1'].tokens,75,'actual cost has one authority in the original cycle budget');
});

for(const finishFirst of [false,true])test('cycle finish and media reserve preserve daily charge in either serialized order '+finishFirst,async t=>{
 const {db,repo,bot}=await fixture(t);
 // Start both operations in the chosen order without awaiting the first. The
 // database transaction serializes their shared cycle lock, never a JS mock.
 const finish=()=>repo.finish(claim,zeroUsage,'activity_completed'),reserve=()=>cycleModelBudget(db,bot,media);
 await Promise.all(finishFirst?[finish(),reserve()]:[reserve(),finish()]);
 const day=await balance(db);assert.equal(Number(day.used_tokens),200);assert.equal(Number(day.reserved_tokens),0);assert.equal(day.model_calls,1);
 await cycleModelBudget(db,bot,{...media,phase:'settled',tokens:75});
 await original(db).repo.finish(await resultJob(db),mediaReceipt);
 assert.equal(Number((await balance(db)).used_tokens),75);assert.equal((await balance(db)).model_calls,1);
});

test('late media checks other-cycle daily reservations; failed admission rolls back journal and can retry same request',async t=>{
 const {db,repo,bot}=await fixture(t);
 await repo.finish(claim,zeroUsage,'activity_completed');
 await db.query("UPDATE autonomy_settings SET config=config||'{\"dailyTokenBudget\":1000}' WHERE bot_id='bot'");
 await db.query("UPDATE autonomy_daily_usage SET reserved_tokens=900 WHERE bot_id='bot' AND day='fixture'");
 await assert.rejects(()=>cycleModelBudget(db,bot,media),/daily_budget_exhausted/);
 assert.equal(Number((await balance(db)).used_tokens),0);
 assert.equal((await db.query('SELECT budget FROM autonomy_cycles WHERE id=$1',[cycleId]))[0].budget.modelRequests,undefined);
 await db.query("UPDATE autonomy_daily_usage SET reserved_tokens=800 WHERE bot_id='bot' AND day='fixture'");
 await cycleModelBudget(db,bot,media);
 assert.equal(Number((await balance(db)).used_tokens),200);
 await cycleModelBudget(db,bot,{...media,phase:'released'});
 assert.equal(Number((await balance(db)).used_tokens),0);assert.equal((await balance(db)).model_calls,0);assert.equal(Number((await balance(db)).reserved_tokens),800);
});

test('unlimited daily budget still enforces shared cycle tokens for main and late media',async t=>{
 const {db,repo,bot}=await fixture(t);
 await db.query("UPDATE autonomy_cycles SET budget=budget||'{\"cycleModelCalls\":5}' WHERE id=$1",[cycleId]);
 await db.query("UPDATE autonomy_settings SET config=config||'{\"dailyTokenBudget\":0}' WHERE bot_id='bot'");
 await cycleModelBudget(db,bot,{...main,tokens:900});
 await assert.rejects(()=>cycleModelBudget(db,bot,media),/token_budget_exhausted/);
 await cycleModelBudget(db,bot,{...main,phase:'settled',tokens:800});
 await repo.finish(claim,zeroUsage,'activity_completed');
 await db.query("UPDATE autonomy_daily_usage SET reserved_tokens=9999999 WHERE bot_id='bot' AND day='fixture'");
 await cycleModelBudget(db,bot,media);
 assert.equal(Number((await balance(db)).used_tokens),1000);
});

test('late unknown media survives reopen, rejects failure refund and accepts authoritative negative settlement delta',async t=>{
 const f=await fixture(t);let db=f.db;
 await f.repo.finish(claim,zeroUsage,'activity_completed');
 await cycleModelBudget(db,f.bot,media);await cycleModelBudget(db,f.bot,{...media,phase:'unknown'});
 db=await f.reopen();
 await cycleModelBudget(db,f.bot,{...media,phase:'released'});
 assert.equal(Number((await balance(db)).used_tokens),200);
 assert.equal((await db.query('SELECT usage FROM autonomy_cycles WHERE id=$1',[cycleId]))[0].usage.mediaTokenStatus,'unknown');
 await cycleModelBudget(db,f.bot,{...media,phase:'settled',tokens:25});
 await cycleModelBudget(db,f.bot,{...media,phase:'settled',tokens:1});
 assert.equal(Number((await balance(db)).used_tokens),25);assert.equal((await balance(db)).model_calls,1);
});

test('recovery full-ceiling charge covers prior media receipt and blocks additional requests against consumed cycle',async t=>{
 const {db,repo,bot}=await fixture(t);
 await db.query("UPDATE autonomy_cycles SET budget=budget||'{\"cycleModelCalls\":5}' WHERE id=$1",[cycleId]);
 await cycleModelBudget(db,bot,media);
 await db.transaction(tx=>(repo as any).recover({bot_id:'bot',tenant_id:'tenant'},tx));
 assert.equal(Number((await balance(db)).used_tokens),1000);assert.equal((await balance(db)).model_calls,1);
 await assert.rejects(()=>cycleModelBudget(db,bot,{...media,requestId:'late-after-recovery'}),/token_budget_exhausted/);
 await cycleModelBudget(db,bot,{...media,phase:'settled',tokens:75});
 await original(db).repo.finish(await resultJob(db),mediaReceipt);
 assert.equal(Number((await balance(db)).used_tokens),1000);assert.equal((await balance(db)).model_calls,1);
});

test('transaction fault after late daily charge rolls back both daily capacity and request identity',async t=>{
 const {db,repo,bot}=await fixture(t);await repo.finish(claim,zeroUsage,'activity_completed');
 const faultDb:any={...db,transaction:(fn:any)=>db.transaction(tx=>fn({...tx,query:async(sql:string,params:any[])=>{const rows=await tx.query(sql,params);if(sql.includes('UPDATE autonomy_daily_usage'))throw new Error('injected post-charge crash');return rows}}))};
 await assert.rejects(()=>cycleModelBudget(faultDb,bot,media),/post-charge crash/);
 assert.equal(Number((await balance(db)).used_tokens),0);assert.equal((await balance(db)).model_calls,0);
 assert.equal((await db.query('SELECT budget FROM autonomy_cycles WHERE id=$1',[cycleId]))[0].budget.modelRequests,undefined);
 await cycleModelBudget(db,bot,media);assert.equal(Number((await balance(db)).used_tokens),200);
});


test('concurrent main and media reserve share tokens even when both call slots fit',async t=>{
 const {db,bot}=await fixture(t);
 await db.query("UPDATE autonomy_cycles SET budget=budget||'{\"cycleModelCalls\":2}' WHERE id=$1",[cycleId]);
 let enter!:()=>void;const barrier=new Promise<void>(r=>enter=r);
 const pending=[{...main,tokens:900},media].map(async request=>{await barrier;return cycleModelBudget(db,bot,request)});
 enter();const outcomes=await Promise.allSettled(pending);
 assert.equal(outcomes.filter(r=>r.status==='fulfilled').length,1);
 assert.match(String((outcomes.find(r=>r.status==='rejected') as PromiseRejectedResult).reason),/token_budget_exhausted/);
});

test('media process loss after finished-cycle reservation retains charge and late successful receipt corrects it once',async t=>{
 const f=await fixture(t);let db=f.db;
 await f.repo.finish(claim,zeroUsage,'activity_completed');await cycleModelBudget(db,f.bot,media);
 await original(db).call('advance',{id:effectId,owner:'worker',phase:'checkpoint',usage:{modelRequests:1}});
 await original(db).sql("UPDATE pipeline_events SET lease_until=0 WHERE source='tool' AND event=?",[effectId]);
 db=await f.reopen();const effects=original(db).repo;assert.equal(await effects.claim('replacement'),null);
 const job=await resultJob(db);assert.equal(job.state,'unknown');assert.equal(Number((await balance(db)).used_tokens),200);
 await cycleModelBudget(db,f.bot,{...media,phase:'settled',tokens:75});
 await effects.finish(job,mediaReceipt);await effects.finish(job,mediaReceipt);
 assert.equal(Number((await balance(db)).used_tokens),75);assert.equal((await balance(db)).model_calls,1);assert.equal((await resultJob(db)).state,'finished');
});
