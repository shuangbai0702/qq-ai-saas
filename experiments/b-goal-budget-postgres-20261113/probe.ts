import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import pg from 'pg';
import {openDatabase,configuredDatabaseUrl,type Sql} from '../../packages/database/index.js';
import {AutonomyRepository} from '../../packages/database/autonomous.js';
import {cycleModelBudget,type ModelRequestBudget} from '../../packages/database/model-request-budget.js';

// Run only on explicit test authorization. Every application query has an
// exclusive random search_path; no production schema is a fallback.
const base=await configuredDatabaseUrl();
if(!base)throw new Error('Authorized database environment must be supplied by the runner');
const schema='fishbot_budget_probe_'+randomUUID().replaceAll('-','');
assert.match(schema,/^fishbot_budget_probe_[a-f0-9]{32}$/);
const admin=new pg.Pool({connectionString:base,max:1,application_name:schema+'_admin'});
const url=new URL(base);url.searchParams.set('options','-csearch_path='+schema);url.searchParams.set('application_name',schema);
let db:Sql|undefined,created=false;
const reports:any[]=[];
const deferred=()=>{let resolve!:(value?:any)=>void;const promise=new Promise<any>(r=>resolve=r);return{promise,resolve}};
const timeout=async<T>(promise:Promise<T>,label:string):Promise<T>=>{
 let timer:any;try{return await Promise.race([promise,new Promise<never>((_,reject)=>{timer=setTimeout(()=>reject(new Error('probe_timeout:'+label)),8000)})])}finally{clearTimeout(timer)}
};
const outcome=async(fn:()=>Promise<any>)=>{try{return{ok:true,value:await fn()}}catch(error){return{ok:false,error}}};
function instrument(source:Sql,match:(sql:string)=>boolean,hold:boolean){
 const acquired=deferred(),release=deferred(),started=deferred();let once=false;
 const wrapped:Sql={...source,transaction:fn=>source.transaction(async tx=>{
  const [{pid}]=await tx.query('SELECT pg_backend_pid() AS pid');started.resolve(pid);
  return fn({...tx,query:async(sql,params)=>{const rows=await tx.query(sql,params);if(!once&&match(sql)){once=true;acquired.resolve(pid);if(hold)await timeout(release.promise,'release-lock')}return rows}});
 })};
 return{db:wrapped,acquired,release,started};
}
async function blocked(pid:number){
 const until=Date.now()+5000;
 while(Date.now()<until){
  const {rows}=await admin.query('SELECT wait_event_type FROM pg_catalog.pg_stat_activity WHERE pid=$1 AND application_name=$2',[pid,schema]);
  if(rows[0]?.wait_event_type==='Lock')return;
  await new Promise(r=>setTimeout(r,20));
 }
 throw new Error('second_connection_did_not_wait_for_database_lock');
}
async function race(name:string,a:(db:Sql)=>Promise<any>,b:(db:Sql)=>Promise<any>,match=(sql:string)=>sql.includes('autonomy_cycles')&&sql.includes('FOR UPDATE')){
 const first=instrument(db!,match,true),second=instrument(db!,()=>false,false);
 const pa=outcome(()=>a(first.db));let pb:Promise<any>|undefined;
 try{
  const pidA=await timeout(first.acquired.promise,'first-lock');pb=outcome(()=>b(second.db));
  const pidB=await timeout(second.started.promise,'second-connection');assert.notEqual(pidA,pidB);
  await blocked(pidB);first.release.resolve();const results=await Promise.all([pa,pb]);
  reports.push({name,distinctConnections:true,secondWaitedForDatabaseLock:true});return results;
 }finally{first.release.resolve();await pa;if(pb)await pb}
}
async function fixture(calls=1){
 const botId='fixture_'+randomUUID(),cycleId=randomUUID(),workId=randomUUID(),effectId=randomUUID();
 await db!.query('INSERT INTO tenants(id,name) VALUES($1,$1)',[botId]);
 await db!.query("INSERT INTO bots(id,tenant_id,name,protocol,desired_state,health) VALUES($1,$1,'isolated probe','mock','running',$2)",[botId,JSON.stringify({sampledAt:new Date().toISOString(),protocol:{online:true}})]);
 await db!.query("INSERT INTO bot_config_revisions(bot_id,revision,config) VALUES($1,1,'{\"behavior\":{\"ownerUin\":\"12345678\"}}')",[botId]);
 const repo=new AutonomyRepository(db!);await repo.configure(botId,botId,1,{mode:'active'});
 const [bot]=await db!.query('SELECT * FROM bots WHERE id=$1',[botId]);
 await db!.query("INSERT INTO autonomy_daily_usage(bot_id,tenant_id,day,reserved_tokens) VALUES($1,$1,'fixture',1000)",[botId]);
 const budget={day:'fixture',cycleTokenBudget:1000,cycleModelCalls:calls};
 await db!.query("INSERT INTO autonomy_cycles(id,bot_id,tenant_id,epoch,generation,config_revision,owner,state,budget) VALUES($1,$2,$2,2,$3,$4,'probe','acting',$5)",[cycleId,botId,bot.generation,bot.revision,JSON.stringify(budget)]);
 await db!.query("INSERT INTO autonomy_actions(id,cycle_id,bot_id,tenant_id,epoch,kind,target,objective,status,expires_at) VALUES($1,$2,$3,$3,2,'research','','isolated probe','executing',now()+interval '5 minutes')",[workId,cycleId,botId]);
 await db!.query("INSERT INTO tool_effects(id,tenant_id,bot_id,work_id,step_id,operation,args,args_digest,authority,mode_epoch,generation,config_revision,settings_revision,deadline_at,state,lease_owner,lease_until) VALUES($1,$2,$2,$3,'analysis','analyze','{}','fixture','autonomous',2,$4,$5,1,now()+interval '5 minutes','claimed','probe-worker',now()+interval '5 minutes')",[effectId,botId,workId,bot.generation,bot.revision]);
 const main:ModelRequestBudget={workId,epoch:2,requestId:'main',phase:'reserve',source:'main',tokens:200};
 const media:ModelRequestBudget={...main,requestId:'media',source:'media',effectId,owner:'probe-worker'};
 const claim={cycleId,bot_id:botId,tenant_id:botId,epoch:2,owner:'probe',tokenLimit:1000,config:{cycleTokenBudget:1000},day:'fixture'};
 const day=async()=>(await db!.query("SELECT * FROM autonomy_daily_usage WHERE bot_id=$1 AND day='fixture'",[botId]))[0];
 return{bot,botId,cycleId,workId,effectId,budget,main,media,claim,day,repo};
}
const zero={tokens:0,modelCalls:0,toolCalls:0,externalActions:0};
function oneRejected(results:any[],pattern:RegExp){assert.equal(results.filter(r=>r.ok).length,1);assert.match(String(results.find(r=>!r.ok)?.error),pattern)}
try{
 await admin.query('CREATE SCHEMA "'+schema+'"');created=true;
 process.env.PG_POOL_MAX='4';process.env.DB_AUTO_MIGRATE='true';
 process.env.PG_STATEMENT_TIMEOUT_MS='15000';process.env.PG_LOCK_TIMEOUT_MS='12000';process.env.PG_IDLE_TRANSACTION_TIMEOUT_MS='30000';
 db=await openDatabase('.',url.toString());
 const [identity]=await db.query('SELECT current_schema() AS schema,current_setting(\'search_path\') AS path');assert.equal(identity.schema,schema);assert.equal(identity.path,schema);
 for(const leader of ['main','media'] as const){
  const f=await fixture(),other=leader==='main'?'media':'main';
  const result=await race('same-cycle-call-limit:'+leader,d=>cycleModelBudget(d,f.bot,f[leader]),d=>cycleModelBudget(d,f.bot,f[other]));oneRejected(result,/model_budget_exhausted/);
 }
 {
  const f=await fixture(2);const result=await race('same-cycle-token-limit',d=>cycleModelBudget(d,f.bot,{...f.main,tokens:900}),d=>cycleModelBudget(d,f.bot,f.media));oneRejected(result,/model_token_budget_exhausted/);
 }
 for(const finishFirst of [true,false]){
  const f=await fixture(),finish=(d:Sql)=>new AutonomyRepository(d).finish(f.claim,zero,'activity_completed'),reserve=(d:Sql)=>cycleModelBudget(d,f.bot,f.media);
  const result=await race('cycle-finish-media:'+finishFirst,finishFirst?finish:reserve,finishFirst?reserve:finish);assert.ok(result.every(r=>r.ok));
  const day=await f.day();assert.equal(Number(day.used_tokens),200);assert.equal(Number(day.reserved_tokens),0);assert.equal(day.model_calls,1);
  await cycleModelBudget(db,f.bot,{...f.media,phase:'settled',tokens:75});assert.equal(Number((await f.day()).used_tokens),75);
 }
 {
  const f=await fixture();await f.repo.finish(f.claim,zero,'activity_completed');
  await db.query("UPDATE autonomy_settings SET config=config||'{\"dailyTokenBudget\":1000}' WHERE bot_id=$1",[f.botId]);
  await db.query("UPDATE autonomy_daily_usage SET reserved_tokens=700 WHERE bot_id=$1 AND day='fixture'",[f.botId]);
  const cycle2=randomUUID(),work2=randomUUID(),effect2=randomUUID();
  await db.query("INSERT INTO autonomy_cycles(id,bot_id,tenant_id,epoch,generation,config_revision,owner,state,budget,usage,reason,decision,started_at,finished_at) SELECT $2,bot_id,tenant_id,epoch,generation,config_revision,owner,state,budget,usage,reason,decision,started_at,finished_at FROM autonomy_cycles WHERE id=$1",[f.cycleId,cycle2]);
  await db.query("INSERT INTO autonomy_actions(id,cycle_id,bot_id,tenant_id,epoch,kind,target,objective,goal_id,status,expires_at,receipt,result,created_at,finished_at) SELECT $2,$3,bot_id,tenant_id,epoch,kind,target,objective,goal_id,status,expires_at,receipt,result,created_at,finished_at FROM autonomy_actions WHERE id=$1",[f.workId,work2,cycle2]);
  await db.query("INSERT INTO tool_effects(id,tenant_id,bot_id,work_id,step_id,operation,args,args_digest,authority,mode_epoch,generation,config_revision,settings_revision,deadline_at,state,lease_owner,lease_until) VALUES($1,$2,$2,$3,'analysis','analyze','{}','fixture','autonomous',2,$4,$5,1,now()+interval '5 minutes','claimed','probe-worker',now()+interval '5 minutes')",[effect2,f.botId,work2,f.bot.generation,f.bot.revision]);
  const second={...f.media,workId:work2,effectId:effect2};
  const result=await race('different-cycles-daily-used-plus-reserved',d=>cycleModelBudget(d,f.bot,f.media),d=>cycleModelBudget(d,f.bot,second),sql=>sql.startsWith('UPDATE autonomy_daily_usage SET used_tokens'));
  oneRejected(result,/model_daily_budget_exhausted/);const day=await f.day();assert.equal(Number(day.used_tokens),200);assert.equal(Number(day.reserved_tokens),700);assert.equal(day.model_calls,1);
  assert.equal((await db.query('SELECT budget FROM autonomy_cycles WHERE id=$1',[cycle2]))[0].budget.modelRequests,undefined);
 }
 {
  const f=await fixture(),goal=randomUUID();
  await db.query("INSERT INTO autonomy_goals(id,bot_id,tenant_id,origin,status,title,reason,source) VALUES($1,$2,$2,'owner_request','active','fixture','fixture','{\"scope\":\"group:11111111\",\"senderId\":\"12345678\"}')",[goal,f.botId]);
  const audience={scope:'group:11111111',senderId:'12345678'};
  const result=await race('same-goal-revision-cas',d=>new AutonomyRepository(d).updateGoal(f.bot,audience,{goalId:goal,expectedRevision:1,changes:{progress:'first'}}),d=>new AutonomyRepository(d).updateGoal(f.bot,audience,{goalId:goal,expectedRevision:1,changes:{progress:'second'}}),sql=>sql.includes('SELECT bot_id,epoch FROM autonomy_settings')&&sql.includes('FOR UPDATE'));
  assert.ok(result.every(r=>r.ok));assert.deepEqual(result.map(r=>r.value.status).sort(),['conflict','updated']);
  const wrong=await f.repo.updateGoal(f.bot,{scope:'group:22222222',senderId:'12345678'},{goalId:goal,expectedRevision:2,changes:{progress:'forbidden'}});assert.equal(wrong.status,'rejected');
  const [current]=await db.query('SELECT progress,revision FROM autonomy_goals WHERE id=$1',[goal]);assert.equal(current.revision,2);assert.equal(current.progress,'first');
 }
 console.log(JSON.stringify({kind:'isolated-postgres-budget-probe',schema,passed:reports.length,reports,externalWrites:0,paidModelCalls:0}));
}catch(error:any){
 console.error(JSON.stringify({kind:'isolated-postgres-budget-probe-failed',name:error?.name,code:error?.code,message:error?.name==='AssertionError'?error.message:'probe execution failed'}));process.exitCode=1;
}finally{
 try{await db?.close()}finally{
 try{if(created){
  const {rows}=await admin.query('SELECT n.nspname,r.rolname=current_user AS owned FROM pg_catalog.pg_namespace n JOIN pg_catalog.pg_roles r ON r.oid=n.nspowner WHERE n.nspname=$1',[schema]);
  assert.equal(rows.length,1);assert.equal(rows[0].owned,true);assert.equal(rows[0].nspname,schema);
  await admin.query('DROP SCHEMA "'+schema+'" CASCADE');
  const {rows:remaining}=await admin.query('SELECT 1 FROM pg_catalog.pg_namespace WHERE nspname=$1',[schema]);assert.equal(remaining.length,0);
  console.log(JSON.stringify({kind:'isolated-postgres-budget-probe-cleanup',schema,removed:true}));
 }}finally{await admin.end()}
 }
}
