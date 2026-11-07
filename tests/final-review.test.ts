import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
const root=path.resolve(process.env.FISHBOT_REVIEW_BASE||'.');
const {openDatabase}=await import(pathToFileURL(path.join(root,'packages/database/index.ts')).href);
const {AutonomyRepository}=await import(pathToFileURL(path.join(root,'packages/database/autonomous.ts')).href);
const {AutonomyCoordinator}=await import(pathToFileURL(path.join(root,'apps/node-agent/src/autonomous.ts')).href);
const goalId='8a7ff118-3cca-4d53-8774-6fc61794713f';

async function fixture(){
 const dir=await mkdtemp(path.resolve('.local/final-review-db-'));const db=await openDatabase(dir);
 await db.query("INSERT INTO tenants(id,name) VALUES('review','review')");
 await db.query("INSERT INTO bots(id,tenant_id,name,protocol,desired_state,health) VALUES('review-bot','review','review','mock','running',$1)",[JSON.stringify({sampledAt:new Date().toISOString(),protocol:{online:true}})]);
 const repo=new AutonomyRepository(db);await repo.configure('review','review-bot',1,{mode:'active'});await repo.applied('review','review-bot',2);
 const source={handoffId:goalId,scope:'private:12345678',senderId:'12345678',eventId:'review-event',notBefore:Date.now()-1000};
 const objective='核验任务细节。'.repeat(35)+'最后必须返回原始证据链接';
 await db.query("INSERT INTO autonomy_goals(id,bot_id,tenant_id,origin,status,title,reason,progress,source) VALUES($1,'review-bot','review','contact_request','active',$2,'review',$3,$4)",[goalId,objective.slice(0,160),objective,JSON.stringify(source)]);
 const close=async()=>{await db.close();await rm(dir,{recursive:true,force:true})};
 return{db,repo,source,objective,close};
}

test('configuration epoch change re-arms future handoffs from their ledger',async()=>{
 const f=await fixture();try{
  const due=Date.now()+3600000;
  await f.db.query("UPDATE autonomy_goals SET source=jsonb_set(source,'{notBefore}',$2::jsonb) WHERE id=$1",[goalId,JSON.stringify(due)]);
  await f.repo.configure('review','review-bot',2,{mode:'passive'});
  await f.repo.configure('review','review-bot',3,{mode:'active'});
  const wakes=await f.db.query("SELECT scope,epoch,due_at FROM autonomy_wakes WHERE reason='handoff-due'");
  assert.equal(wakes.length,1);assert.equal(wakes[0].scope,goalId);assert.equal(wakes[0].epoch,4);
  assert.ok(Math.abs(new Date(wakes[0].due_at).getTime()-due)<1000);
 }finally{await f.close()}
});

test('plan re-reads a completed goal and refuses its stale active snapshot',async()=>{
 const f=await fixture();try{
  const claim=await f.repo.claim('review-worker');assert.ok(claim);
  const [stale]=await f.repo.dueHandoffs(claim);assert.ok(stale);
  assert.equal(await f.repo.completeGoal(claim,goalId,'already handled'),true);
  await assert.rejects(f.repo.planHandoff(claim,stale,{}),/取消|暂停/);
  assert.equal((await f.db.query('SELECT count(*)::int AS n FROM autonomy_actions'))[0].n,0);
 }finally{await f.close()}
});

test('canonical source and full handoff objective survive planning; canceled goal cannot execute',async()=>{
 const f=await fixture();try{
  const claim=await f.repo.claim('review-worker');assert.ok(claim);
  const [goal]=await f.repo.dueHandoffs(claim);
  const prepared=await f.repo.planHandoff(claim,{...goal,source:{...goal.source,scope:'group:99999999'}},{});
  assert.equal(prepared.action.target,f.source.scope);assert.equal(prepared.source.scope,f.source.scope);
  assert.equal(prepared.action.objective,f.objective);
  assert.equal(await f.repo.abandonGoal(claim,goalId),true);
  assert.equal(await f.repo.markExecuting(claim,prepared.action.id),false);
 }finally{await f.close()}
});

for(const returnedUsage of [undefined,{}, {tokens:-1,modelCalls:1,toolCalls:0}])test('a returned result with incomplete usage keeps its reserved cost: '+JSON.stringify(returnedUsage),async()=>{
 const f=await fixture();const coordinator=new AutonomyCoordinator(f.db,async(_bot:any,op:string,body:any)=>{
  if(op==='policy'||op==='snapshot')return{};
  if(op==='execute')return{status:'completed',summary:'actual fixture result',effects:[{id:body.work.id+':0',status:'confirmed',receipt:'fixture'}],evidence:[],unfinished:[],usage:returnedUsage};
  throw new Error(op);
 });
 try{
  await coordinator.tick();await coordinator.active?.task;
  const view=await coordinator.repo.view('review','review-bot');
  assert.ok(view.usage.used_tokens>0,'missing usage refunded executed work');
  assert.equal(view.cycles[0].usage.charges[0].source,'estimated');
 }finally{await coordinator.stop();await f.close()}
});
