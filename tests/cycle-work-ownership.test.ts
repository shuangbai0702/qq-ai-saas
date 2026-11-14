import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp} from 'node:fs/promises';
import path from 'node:path';
import {randomUUID} from 'node:crypto';
import {openDatabase} from '../packages/database/index.js';
import {AutonomyRepository} from '../packages/database/autonomous.js';
import {cycleModelBudget} from '../packages/database/model-request-budget.js';
import {qqExternalBudget,externalCount} from '../packages/database/external-action-budget.js';
import {ToolEffects} from '../packages/database/tool-effects.js';

async function fixture(t:any){
 const root=await mkdtemp(path.resolve('.local/cycle-work-'));let db=await openDatabase(root);
 t.after(()=>db.close());
 await db.query("INSERT INTO tenants(id,name) VALUES('tenant','fixture')");
 await db.query("INSERT INTO bots(id,tenant_id,name,protocol,desired_state,health) VALUES('bot','tenant','fixture','mock','running',$1)",[JSON.stringify({sampledAt:new Date().toISOString(),protocol:{online:true}})]);
 const [bot]=await db.query("SELECT * FROM bots WHERE id='bot'");
 await db.query("INSERT INTO bot_config_revisions(bot_id,revision,config) VALUES('bot',$1,$2)",[bot.revision,JSON.stringify({behavior:{ownerUin:'12345678'}})]);
 const repo=new AutonomyRepository(db);
 await repo.configure('tenant','bot',1,{mode:'active',activityEnabled:true,cycleModelCalls:4});await repo.applied('tenant','bot',2);
 await repo.wake('tenant','bot',2,'operator-wake',0);
 const claim=await repo.claim('fixture-owner');assert.ok(claim);
 return{db,repo,bot,claim,root,reopen:async()=>{await db.close();db=await openDatabase(root);return db}};
}

test('cycle owns one service permit; Goal CAS and late budgets do not need an Action history',async t=>{
 const f=await fixture(t),{db,repo,claim,bot}=f;
 assert.equal((await db.query("SELECT 1 FROM information_schema.tables WHERE table_name='autonomy_actions'")).length,0);
 const activation=await repo.planActivity(claim,{});assert.equal(activation.id,claim.cycleId);
 await assert.rejects(()=>repo.planActivity(claim,{}));
 assert.equal(await repo.markExecuting({...claim,tenant_id:'other'},activation.id),false);
 assert.equal(await repo.markExecuting(claim,activation.id),true);assert.equal(await repo.markExecuting(claim,activation.id),false);
 const goalId=randomUUID();await db.query("INSERT INTO autonomy_goals(id,bot_id,tenant_id,origin,status,title,reason,success_condition,source) VALUES($1,'bot','tenant','model','active','current','real intent','real result','{\"scope\":\"self\"}')",[goalId]);
 const audience={scope:'self',senderId:'0',work:{id:claim.cycleId,epoch:claim.epoch}};
 const list=await repo.readGoals(bot,audience);assert.equal(list.goals[0].id,goalId);
 const change={goalId,expectedRevision:1,changes:{progress:'真实进度'}};
 const results=await Promise.all([repo.updateGoal(bot,audience,change),repo.updateGoal(bot,audience,change)]);
 assert.deepEqual(results.map(r=>r.status).sort(),['conflict','updated']);
 await assert.rejects(()=>repo.readGoals(bot,{...audience,work:{id:randomUUID(),epoch:2}}));
 const reserve={workId:claim.cycleId,epoch:claim.epoch,logicalActionId:'same-send'};
 await qqExternalBudget(db,bot,{...reserve,phase:'reserve'});
 await cycleModelBudget(db,bot,{workId:claim.cycleId,epoch:claim.epoch,requestId:'main',phase:'reserve',source:'main',tokens:100});
 await repo.feedback(claim,activation.id,{status:'completed',summary:'must stay in the original Work',effects:[],evidence:['original receipt'],unfinished:[]});
 const [cycle]=await db.query('SELECT * FROM autonomy_cycles WHERE id=$1',[claim.cycleId]);
 assert.deepEqual(cycle.decision,{reason:'autonomous_moment',receiptPending:false});assert.equal(cycle.state,'acting');
 assert.equal((await repo.view('tenant','bot') as any).actions,undefined);
 await repo.finish(claim,{tokens:100,modelCalls:1,toolCalls:0,externalActions:1},'activity_completed');
 await assert.rejects(()=>qqExternalBudget(db,bot,{...reserve,logicalActionId:'another',phase:'reserve'}));
 await qqExternalBudget(db,bot,{...reserve,phase:'unknown'});
 await qqExternalBudget(db,bot,{...reserve,phase:'released'});
 assert.equal(await externalCount(db,claim.cycleId),1);
 await qqExternalBudget(db,bot,{...reserve,phase:'confirmed'});
 await cycleModelBudget(db,bot,{workId:claim.cycleId,epoch:claim.epoch,requestId:'main',phase:'settled',source:'main',tokens:70});
 const reopened=await f.reopen();const [saved]=await reopened.query('SELECT * FROM autonomy_cycles WHERE id=$1',[claim.cycleId]);
 assert.equal(saved.budget.qqExternalRequests[claim.cycleId]['same-send'].status,'confirmed');
 assert.equal(saved.budget.modelRequests.main.tokens,70);assert.equal(saved.budget.modelRequests.main.status,'settled');
 assert.equal((await reopened.query("SELECT progress FROM autonomy_goals WHERE id=$1",[goalId]))[0].progress,'真实进度');
});

test('media completion binds the original cycle and resumes one Work without storing a second execution report',async t=>{
 const {db,repo,claim,bot}=await fixture(t);
 const activation=await repo.planActivity(claim,{});await repo.markExecuting(claim,activation.id);
 const effects=new ToolEffects(db),settings=await effects.settings('tenant','bot');
 await effects.configure('tenant','bot',settings.revision,{enabled:true,allowAutonomous:true,allowAnonymous:true,scopes:['public.read']});
 const call:any={workId:claim.cycleId,stepId:'search',operation:'search',args:{query:'公开只读样本'},authority:'autonomous',epoch:claim.epoch,deadlineAt:Date.now()+60000,origin:{scope:'self',eventId:claim.cycleId,objective:'真实查询'}};
 const admitted=await effects.admit(bot,call);assert.equal(admitted.cycle_id,claim.cycleId);
 assert.equal((await effects.admit(bot,call)).id,admitted.id);
 await db.query("UPDATE tool_effects SET state='finished' WHERE id=$1",[admitted.id]);
 const id=randomUUID();await db.query("INSERT INTO tool_effects(id,tenant_id,bot_id,work_id,cycle_id,step_id,operation,args,args_digest,authority,mode_epoch,generation,config_revision,settings_revision,deadline_at,state,result,origin_scope,origin_event,continuation,completion_state) VALUES($1,'tenant','bot',$2,$2,'analysis','analyze','{}','fixture','autonomous',2,$3,$4,2,now()+interval '2 minutes','finished',$5,'self',$2,'{\"objective\":\"理解已完成分析\"}','pending')",[id,claim.cycleId,bot.generation,bot.revision,JSON.stringify({execution:'completed',data:{analysis:'真实工具结果'},receipt:{status:'confirmed'}})]);
 await repo.finish(claim,{tokens:0,modelCalls:0,toolCalls:1,externalActions:0},'activity_completed');
 await repo.wake('tenant','bot',claim.epoch,'tool-completed',0,id);
 const next=await repo.claim('next-owner');assert.ok(next);
 const continuation=await repo.planToolContinuation(next,{});assert.ok(continuation);
 assert.equal(continuation.id,next.cycleId);assert.equal(continuation.continuation.parentActionId,claim.cycleId);assert.equal(continuation.continuation.result.data.analysis,'真实工具结果');
 assert.equal(await repo.markExecuting(next,continuation.id),true);
 const [saved]=await db.query('SELECT decision FROM autonomy_cycles WHERE id=$1',[next.cycleId]);
 assert.equal(JSON.stringify(saved).includes('真实工具结果'),false);assert.equal(saved.decision.continuation.id,id);
 await repo.settleToolContinuation(next,continuation,'pending');assert.equal((await db.query('SELECT completion_state FROM tool_effects WHERE id=$1',[id]))[0].completion_state,'pending');
 await repo.finish(next,{tokens:0,modelCalls:0,toolCalls:0,externalActions:0},'not_started');
});

test('lost execution receipts retain original cycle recovery across a classified provider fault',async t=>{
 const {db,repo,claim,bot}=await fixture(t),work=await repo.planActivity(claim,{});
 await repo.markExecuting(claim,work.id);
 await repo.feedback(claim,work.id,{status:'partial',summary:'private execution detail',effects:[{status:'confirmed',receipt:'private receipt'},{status:'unknown'}]});
 await repo.finish(claim,{tokens:20,modelCalls:1,toolCalls:0,externalActions:0},'runtime_network_failed',{class:'network'});
 const pending=await repo.unconfirmed('tenant','bot');assert.equal(pending.length,1);assert.equal(pending[0].id,work.id);
 const [cycle]=await db.query('SELECT * FROM autonomy_cycles WHERE id=$1',[work.id]);
 assert.equal(cycle.decision.receiptPending,true);assert.equal(JSON.stringify(cycle).includes('private execution detail'),false);assert.equal(JSON.stringify(cycle).includes('private receipt'),false);
 await repo.receiptChecked({...bot,bot_id:bot.id},pending[0]);assert.equal((await repo.unconfirmed('tenant','bot')).length,0,'receipt recovery must use its original bounded backoff');
 await repo.feedback(claim,work.id,{status:'completed',effects:[{status:'confirmed',receipt:'late original receipt'}]});
 assert.equal((await repo.unconfirmed('tenant','bot')).length,0);
 assert.equal((await db.query('SELECT decision FROM autonomy_cycles WHERE id=$1',[work.id]))[0].decision.receiptPending,false);
 assert.equal(await repo.markExecuting(claim,work.id),false,'settling a late receipt never reopens dispatch');
});
