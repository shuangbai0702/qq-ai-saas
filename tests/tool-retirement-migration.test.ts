import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile,readdir} from 'node:fs/promises';
import {PGlite} from '@electric-sql/pglite';

test('ToolEffects retirement requires drained owners and verified minimal receipts; preserves one original budget',async t=>{
 const db=new PGlite();t.after(()=>db.close());
 const directory=new URL('../packages/database/migrations/',import.meta.url);
 for(const file of (await readdir(directory)).filter(f=>/^0(0[1-9]|1[0-4])_/.test(f)).sort())await db.exec(await readFile(new URL(file,directory),'utf8'));
 await db.exec("INSERT INTO tenants(id,name) VALUES('tenant','fixture');INSERT INTO bots(id,tenant_id,name,protocol) VALUES('bot','tenant','fixture','mock')");
 await db.exec("INSERT INTO autonomy_cycles(id,bot_id,tenant_id,epoch,generation,config_revision,owner,state,budget,usage,finished_at) VALUES('work','bot','tenant',1,1,1,'owner','completed','{\"qqExternalRequests\":{\"work\":{\"qq-send\":{\"status\":\"confirmed\"}}},\"modelRequests\":{\"main\":{\"status\":\"settled\",\"tokens\":25}}}','{\"tokens\":25}',now())");
 await db.exec("INSERT INTO tool_effects(id,bot_id,tenant_id,work_id,cycle_id,step_id,operation,args,args_digest,authority,mode_epoch,generation,config_revision,settings_revision,deadline_at,state,completion_state,result,budget_reserved,account_uid) VALUES('receipt','bot','tenant','work','work','comment','comment','{\"aid\":\"123\",\"text\":\"retired private text\"}','hash','autonomous',1,1,1,1,now(),'finished','none','{\"receipt\":{\"status\":\"confirmed\",\"platformObjectId\":\"456\"}}',true,'account')");
 const sql=await readFile(new URL('015_retire_tool_effects.sql',directory),'utf8');
 const run=(proof?:string)=>db.transaction(async tx=>{if(proof)await tx.query("SELECT set_config('fishbot.receipt_transfer_verified',$1,true)",[proof]);await tx.exec(sql)});
 for(const [state,completion] of [['queued','none'],['claimed','none'],['dispatching','none'],['unknown','none'],['finished','pending'],['finished','claimed'],['finished','unknown']]){
  await db.query('UPDATE tool_effects SET state=$1,completion_state=$2',[state,completion]);await assert.rejects(()=>run(),/division by zero/);
 }
 await db.exec("UPDATE tool_effects SET state='finished',completion_state='none'");
 await assert.rejects(()=>run(),/division by zero/);await assert.rejects(()=>run('wrong-receipt-proof'),/division by zero/);
 assert.equal((await db.query('SELECT * FROM tool_effects')).rows.length,1);
 const proof=(await db.query<{proof:string}>("SELECT md5(string_agg(id||':'||jsonb_build_object('account',account_uid,'aid',args->>'aid','status','confirmed','platformObjectId',result->'receipt'->>'platformObjectId')::text,'|' ORDER BY id)) AS proof FROM tool_effects WHERE operation IN ('comment','dynamic_post') AND result->'receipt'->>'status'='confirmed'")).rows[0].proof;
 await run(proof);
 assert.equal((await db.query("SELECT 1 FROM information_schema.tables WHERE table_name IN ('tool_effects','tool_cancellations','autonomy_actions')")).rows.length,0);
 const cycle=(await db.query<any>('SELECT budget,usage FROM autonomy_cycles')).rows[0];
 assert.deepEqual(cycle.budget.qqExternalRequests.work,{'qq-send':{status:'confirmed'},'bilibili:receipt':{status:'confirmed'}});
 assert.equal(cycle.budget.modelRequests.main.tokens,25);assert.equal(cycle.usage.tokens,25);
 assert.equal(JSON.stringify(cycle).includes('retired private text'),false);
});
