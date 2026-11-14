import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {PGlite} from '@electric-sql/pglite';

test('Action retirement is atomic, refuses unsettled owners and preserves original Goal, budget and effect origins',async t=>{
 const db=new PGlite();t.after(()=>db.close());
 const files=['001_initial','002_runtime_groups','003_invitations','004_bot_quotas','005_qq_binding_kinds','006_autonomous_lifecycle','007_shared_autonomous_policy','008_inner_owned_capabilities','009_bilibili_tools','010_bilibili_media_jobs','011_outer_context_cycles','012_external_action_budget','013_cycle_external_reservations'];
 for(const file of files)await db.exec(await readFile(new URL('../packages/database/migrations/'+file+'.sql',import.meta.url),'utf8'));
 await db.exec("CREATE TABLE schema_migrations(version TEXT PRIMARY KEY);INSERT INTO schema_migrations VALUES('013'),('014')");
 await db.exec("INSERT INTO tenants(id,name) VALUES('tenant','fixture');INSERT INTO bots(id,tenant_id,name,protocol) VALUES('bot','tenant','fixture','mock')");
 await db.query("INSERT INTO autonomy_goals(id,bot_id,tenant_id,origin,status,title,reason,source) VALUES('goal','bot','tenant','model','active','current','original intent','{\"scope\":\"group:12345\",\"eventId\":\"real-event\"}')");
 const budget={modelRequests:{main:{status:'settled',tokens:25}},qqExternalRequests:{'old-a':{send:{status:'confirmed'}},'old-b':{send:{status:'released'}}}};
 await db.query("INSERT INTO autonomy_cycles(id,bot_id,tenant_id,epoch,generation,config_revision,owner,state,budget,decision,finished_at) VALUES('cycle','bot','tenant',1,1,1,'old','completed',$1,'{\"action\":{\"private\":\"retire\"},\"goalId\":\"goal\",\"reason\":\"completed\"}',now())",[JSON.stringify(budget)]);
 for(const id of ['old-a','old-b'])await db.query("INSERT INTO autonomy_actions(id,cycle_id,bot_id,tenant_id,epoch,kind,target,objective,goal_id,status,expires_at,result,finished_at) VALUES($1,'cycle','bot','tenant',1,'research','','old narrative','goal','completed',now(),'{}',now())",[id]);
 await db.query("INSERT INTO tool_effects(id,bot_id,tenant_id,work_id,step_id,operation,args,args_digest,authority,mode_epoch,generation,config_revision,settings_revision,deadline_at,state,completion_state) VALUES('effect','bot','tenant','old-b','step','analyze','{}','hash','autonomous',1,1,1,1,now(),'finished','done')");
 const sql=await readFile(new URL('../packages/database/migrations/014_retire_autonomous_actions.sql',import.meta.url),'utf8');
 const migrate=()=>db.transaction(async tx=>{await tx.exec(sql)});
 for(const status of ['planned','executing','unknown','partial']){
  await db.query("UPDATE autonomy_actions SET status=$1,result=$2 WHERE id='old-a'",[status,JSON.stringify({effects:status==='partial'?[{status:'unknown'}]:[]})]);
  await assert.rejects(migrate,/division by zero/);
  assert.equal((await db.query<{n:number}>("SELECT count(*)::int n FROM autonomy_actions")).rows[0].n,2);
 }
 await db.query("UPDATE autonomy_actions SET status='completed',result='{}'");
 await db.query("UPDATE autonomy_cycles SET finished_at=NULL");await assert.rejects(migrate,/division by zero/);await db.query("UPDATE autonomy_cycles SET finished_at=now()");
 for(const [state,completion] of [['queued','done'],['claimed','done'],['dispatching','done'],['unknown','done'],['finished','pending'],['finished','claimed'],['finished','unknown']]){
  await db.query("UPDATE tool_effects SET state=$1,completion_state=$2",[state,completion]);await assert.rejects(migrate,/division by zero/);
  assert.equal((await db.query("SELECT * FROM information_schema.columns WHERE table_name='autonomy_cycles' AND column_name='work_expires_at'")).rows.length,0,'failed migration leaves no partial DDL');
 }
 await db.query("UPDATE tool_effects SET state='finished',completion_state='done'");
 await migrate();
 assert.equal((await db.query("SELECT * FROM information_schema.tables WHERE table_name='autonomy_actions'")).rows.length,0);
 const cycle=(await db.query<{budget:typeof budget;decision:{reason:string}}>('SELECT budget,decision FROM autonomy_cycles')).rows[0];
 assert.deepEqual(cycle.budget,budget);assert.deepEqual(cycle.decision,{reason:'completed'});
 const effect=(await db.query('SELECT cycle_id,work_id,origin_scope,origin_event,state FROM tool_effects')).rows[0];
 assert.deepEqual(effect,{cycle_id:'cycle',work_id:'old-b',origin_scope:'group:12345',origin_event:'real-event',state:'finished'});
 assert.equal((await db.query<{reason:string}>('SELECT reason FROM autonomy_goals')).rows[0].reason,'original intent');
 // A downgrade after live traffic must keep newly committed Goal/budget state,
 // not restore the pre-release database over it or resurrect retired narratives.
 await db.query("UPDATE autonomy_goals SET progress='new live progress',revision=2");
 await db.query("INSERT INTO autonomy_cycles(id,bot_id,tenant_id,epoch,generation,config_revision,owner,state,budget,decision,reason,work_expires_at,finished_at) VALUES('new-work','bot','tenant',1,1,1,'new','ended','{\"qqExternalRequests\":{\"new-work\":{\"send\":{\"status\":\"unknown\"}}}}','{\"receiptPending\":true}','activity_unknown',now(),now())");
 await db.transaction(async tx=>{await tx.exec(await readFile(new URL('../scripts/rollback-autonomous-actions.sql',import.meta.url),'utf8'))});
 const owners=(await db.query<{id:string;status:string}>('SELECT id,status FROM autonomy_actions ORDER BY id')).rows;
 assert.deepEqual(owners,[{id:'new-work',status:'unknown'},{id:'old-a',status:'completed'},{id:'old-b',status:'completed'}]);
 assert.equal((await db.query<{progress:string;revision:number}>('SELECT progress,revision FROM autonomy_goals')).rows[0].progress,'new live progress');
 assert.deepEqual((await db.query<{budget:typeof budget}>("SELECT budget FROM autonomy_cycles WHERE id='cycle'")).rows[0].budget,budget);
 await db.transaction(async tx=>{await tx.exec(await readFile(new URL('../scripts/rollback-cycle-external-reservations.sql',import.meta.url),'utf8'))});
 assert.deepEqual((await db.query<{external_effects:object}>("SELECT external_effects FROM autonomy_actions WHERE id='new-work'")).rows[0].external_effects,{send:{status:'unknown'}});
 assert.deepEqual((await db.query<{budget:object}>("SELECT budget FROM autonomy_cycles WHERE id='cycle'")).rows[0].budget,{modelRequests:budget.modelRequests});
});
