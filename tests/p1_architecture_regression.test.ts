import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {openDatabase,hasEncryptedSecrets,platformCreditAvailableSql} from '../packages/database/index.js';
import {AutonomyRepository} from '../packages/database/autonomous.js';
import {safeSubprocessEnv} from '../packages/shared/security.js';
import {bilibiliModelConfigured} from '../packages/shared/bilibili-model.js';
import {createApp} from '../apps/control-api/src/app.js';
import {issueTestInvite} from './helpers/invitations.js';
import {reserveCredits,dispatchCredits,resolveStaleReservation,creditBalance} from '../apps/control-api/src/platform-credits.js';
import {DshHost} from '../apps/node-agent/src/dsh-host.js';

test('P1: hasEncryptedSecrets includes platform_model_providers and PGlite nested savepoints roll back cleanly',async t=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'fish-p1-db-'));
 const db=await openDatabase(root,'');
 t.after(async()=>{await db.close();await rm(root,{recursive:true,force:true})});
 assert.equal(await hasEncryptedSecrets(db),false);
 await db.query("INSERT INTO platform_model_providers(id,name,base_url,model,secret,priority,enabled) VALUES('p1','Provider','https://example.test/v1','m1','cipher',1,true)");
 assert.equal(await hasEncryptedSecrets(db),true);

 // Verify nested transaction SAVEPOINT rollback and tx.close() guard in PGlite mode.
 await db.transaction(async tx=>{
  await assert.rejects(()=>tx.close(),/does not own/);
  await tx.query("INSERT INTO platform_model_providers(id,name,base_url,model,secret,priority,enabled) VALUES('p2','Outer','https://example.test/v1','m2','cipher',2,true)");
  await assert.rejects(()=>tx.transaction(async inner=>{
   await inner.query("INSERT INTO platform_model_providers(id,name,base_url,model,secret,priority,enabled) VALUES('p3','Inner','https://example.test/v1','m3','cipher',3,true)");
   throw new Error('rollback_inner');
  }),/rollback_inner/);
 });
 const ids=(await db.query('SELECT id FROM platform_model_providers ORDER BY id')).map((r:any)=>r.id);
 assert.deepEqual(ids,['p1','p2']);
});

test('P1: safeSubprocessEnv strips sensitive secrets and keeps allowlisted vars',()=>{
 const prev={MASTER_KEY:process.env.MASTER_KEY,DATABASE_URL:process.env.DATABASE_URL,PGPASSWORD:process.env.PGPASSWORD,FISHBOT_DSH_KEY_ABC:process.env.FISHBOT_DSH_KEY_ABC};
 process.env.MASTER_KEY='0'.repeat(64);
 process.env.DATABASE_URL='postgres://secret';
 process.env.PGPASSWORD='secret';
 process.env.FISHBOT_DSH_KEY_ABC='dsh-secret';
 try{
  const env=safeSubprocessEnv({PYTHONIOENCODING:'utf-8'});
  assert.equal(env.MASTER_KEY,undefined);
  assert.equal(env.DATABASE_URL,undefined);
  assert.equal(env.PGPASSWORD,undefined);
  assert.equal(env.FISHBOT_DSH_KEY_ABC,undefined);
  assert.equal(env.PYTHONIOENCODING,'utf-8');
 }finally{
  for(const [k,v] of Object.entries(prev))if(v===undefined)delete process.env[k];else process.env[k]=v;
 }
});

test('P1: bilibiliModelConfigured supports platformModel bots and PLATFORM_CREDIT_AVAILABLE_SQL excludes expired campaigns',async t=>{
 assert.equal(bilibiliModelConfigured({config:{model:{provider:'bot'}}},{platformModel:true,baseUrl:'',model:''},true),true);
 assert.equal(bilibiliModelConfigured({config:{model:{provider:'bot'}}},{platformModel:true,baseUrl:'',model:''},false),false);
 assert.equal(bilibiliModelConfigured({config:{model:{provider:'bot'}}},{platformModel:false,baseUrl:'https://x/v1',model:''},true),false);

 const root=await mkdtemp(path.join(os.tmpdir(),'fish-p1-credits-'));
 const db=await openDatabase(root,'');
 t.after(async()=>{await db.close();await rm(root,{recursive:true,force:true})});
 const tenant='00000000-0000-4000-8000-000000000001';
 await db.query("INSERT INTO tenants(id,name) VALUES($1,'t')",[tenant]);
 await db.query("INSERT INTO platform_model_credits(tenant_id,granted,campaign_granted,campaign_expires_at) VALUES($1,5000,5000,now()-interval '1 hour')",[tenant]);
 const [expired]=await db.query(`SELECT (${platformCreditAvailableSql('$1')}) AS ok`,[tenant]);
 assert.equal(expired.ok,false,'expired campaign-only grant must not report available');
 await db.query("UPDATE platform_model_credits SET campaign_expires_at=now()+interval '1 hour' WHERE tenant_id=$1",[tenant]);
 const [active]=await db.query(`SELECT (${platformCreditAvailableSql('$1')}) AS ok`,[tenant]);
 assert.equal(active.ok,true);
});

test('P1: controlGoal rejects terminal goal resurrection, writes audit, and resolveStaleReservation frees aged post-019 holds',async t=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'fish-p1-api-'));
 const {app,db}=await createApp({root,databaseUrl:'',agent:false});
 t.after(async()=>{await app.close();await rm(root,{recursive:true,force:true})});
 const auth=await app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(db),name:'u',email:'p1@test.invalid',password:'password-123'}});
 const cookies={fish_session:auth.cookies[0].value};
 const actor=auth.json().user.id;
 const made=await app.inject({method:'POST',url:'/v1/bots',cookies,payload:{name:'bot',protocol:'mock'}});
 assert.equal(made.statusCode,201,made.body);
 const botId=made.json().id;
 const [{tenant_id:tenant}]=await db.query('SELECT tenant_id FROM bots WHERE id=$1',[botId]);

 // Terminal goal must not be resurrected by /v1/bots/:id/autonomous/goals
 const repo=new AutonomyRepository(db);
 const settings=await repo.ensure(tenant,botId);
 const goalId='10000000-0000-4000-8000-000000000099';
 await db.query("INSERT INTO autonomy_goals(id,bot_id,tenant_id,origin,status,title,reason,source,revision) VALUES($1,$2,$3,'owner_request','completed','done','done','{}',1)",[goalId,botId,tenant]);
 const res=await app.inject({method:'POST',url:`/v1/bots/${botId}/autonomous/goals`,cookies,payload:{id:goalId,expectedEpoch:settings.epoch,expectedRevision:1,operation:'resume'}});
 assert.equal(res.statusCode,409);

 // Post-019 stale reservation resolution with min-age fence + audit
 await db.query("INSERT INTO platform_model_credits(tenant_id,granted) VALUES($1,10000)",[tenant]);
 const rid=await db.transaction(tx=>reserveCredits(tx,tenant,botId,500));
 await dispatchCredits(db,rid,'provider-1');
 await db.query("UPDATE platform_model_reservations SET dispatch_state='unknown' WHERE id=$1",[rid]);
 const [row]=await db.query('SELECT * FROM platform_model_reservations WHERE id=$1',[rid]);
 const expected={state:'unknown' as const,tokens:500,createdAt:new Date(row.created_at).toISOString(),providerId:'provider-1'};
 // Fresh reservation rejected by min-age fence
 await assert.rejects(()=>resolveStaleReservation(db,rid,expected,actor,'上游故障导致无回执人工核销'),/尚未达到/);
 // Aged reservation succeeds and releases reserved balance
 const resolved=await resolveStaleReservation(db,rid,expected,actor,'上游故障导致无回执人工核销',0);
 assert.equal(resolved.released,true);
 assert.equal((await creditBalance(db,tenant)).reserved,0);
 const audits=await db.query("SELECT detail FROM audit_events WHERE event='admin.platform_reservation.resolved'");
 assert.equal(audits.length,1);
});

test('P1: DshHost degrades gracefully when Exa MCP is unreachable',async t=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'fish-p1-dsh-'));
 const db=await openDatabase(root,'');
 t.after(async()=>{await db.close();await rm(root,{recursive:true,force:true})});
 const fakeDeps={db,root,key:Buffer.alloc(32,1),closed:false,context:async()=>({} as any),bilibili:{repo:{} as any,catalog:()=>[]}};
 const host=new DshHost(fakeDeps,(async()=>{throw new Error('exa_unreachable')}) as any);
 (host as any).port=0;
 try{
  await host.start();
  assert.ok((host as any).server,'DshHost HTTP server must still start when Exa MCP fails');
 }finally{
  await host.stop();
 }
});
