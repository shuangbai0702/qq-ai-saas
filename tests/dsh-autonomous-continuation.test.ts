import test from 'node:test';import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';import os from 'node:os';import path from 'node:path';
import {openDatabase} from '../packages/database/index.js';import {AutonomyRepository} from '../packages/database/autonomous.js';
import {defaultAutonomyConfig} from '../packages/contracts/autonomous.js';

test('original autonomous media continuation carries only trusted DSH routing into a newly admitted cycle',async t=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'dsh-original-cycle-')),db=await openDatabase(root);
 t.after(async()=>{await db.close();await rm(root,{recursive:true,force:true})});
 await db.query("INSERT INTO tenants(id,name) VALUES('tenant','fixture')");
 await db.query("INSERT INTO bots(id,tenant_id,name,protocol) VALUES('bot','tenant','fixture','mock')");
 await db.query("INSERT INTO autonomy_settings(bot_id,tenant_id,epoch,config,lease_owner,lease_until) VALUES('bot','tenant',2,$1,'owner',now()+interval '1 minute')",[JSON.stringify({...defaultAutonomyConfig,mode:'active'})]);
 for(const [id,state] of [['old-cycle','completed'],['new-cycle','thinking']])await db.query("INSERT INTO autonomy_cycles(id,bot_id,tenant_id,epoch,generation,config_revision,owner,state,budget,decision) VALUES($1,'bot','tenant',2,1,1,'owner',$2,'{}',$3)",[id,state,JSON.stringify({reason:'autonomous_moment'})]);
 const repo=new AutonomyRepository(db),claim={bot_id:'bot',tenant_id:'tenant',epoch:2,owner:'owner',generation:1,revision:1,cycleId:'new-cycle',config:defaultAutonomyConfig};
 const dsh={sessionId:'fishbot-'+'a'.repeat(64),delegationId:'original-call',ignored:'must not become context'};
 const job={id:'original-media',bot_id:'bot',tenant_id:'tenant',authority:'autonomous',mode_epoch:2,generation:1,config_revision:1,completion_state:'claimed',completion_owner:'completion-owner',owner:'completion-owner',work_id:'old-cycle',sender_id:'service:bot',origin_scope:'self',origin_event:'old-event',result:{execution:'completed',data:{analysis:'evidence'}},continuation:{objective:'original task',dsh}};
 const action=await repo.planToolContinuation(claim,{modelCalls:0,tokens:0},job);assert(action);
 assert.equal(action.id,'new-cycle');assert.deepEqual(action.continuation.dsh,{sessionId:dsh.sessionId,delegationId:dsh.delegationId});
 assert.equal(action.continuation.parentActionId,'old-cycle');assert.equal(action.continuation.result,job.result);
 const [cycle]=await db.query('SELECT state,decision FROM autonomy_cycles WHERE id=$1',['new-cycle']);assert.equal(cycle.state,'planned');
 assert.equal(cycle.decision.continuation.parentWorkId,'old-cycle');assert(!JSON.stringify(cycle.decision).includes('sessionId'),'routing does not create another execution history');
 await db.query("UPDATE autonomy_cycles SET state='thinking' WHERE id='new-cycle'");
 await assert.rejects(repo.planToolContinuation(claim,{}, {...job,continuation:{dsh:{...dsh,sessionId:'forged'}}}),/tool_dsh_session_identity_invalid/);
 await assert.rejects(repo.planToolContinuation(claim,{}, {...job,bot_id:'foreign'}),/tool_completion_identity_changed/);
 const [unchanged]=await db.query("SELECT state FROM autonomy_cycles WHERE id='new-cycle'");assert.equal(unchanged.state,'thinking');
});
