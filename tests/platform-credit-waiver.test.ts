import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp} from 'node:fs/promises';
import os from 'node:os';import path from 'node:path';
import {createApp} from '../apps/control-api/src/app.js';
import {issueTestInvite} from './helpers/invitations.js';
import {reserveCredits,dispatchCredits,receiptCredits,waiveLegacyReservation,creditBalance} from '../apps/control-api/src/platform-credits.js';

test('explicit legacy write-off is authorized, atomic, idempotent and never claims zero consumption',async t=>{
 const {app,db}=await createApp({root:await mkdtemp(path.join(os.tmpdir(),'fish-credit-waiver-')),databaseUrl:'',agent:false});t.after(()=>app.close());
 const auth=await app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(db),name:'fixture',email:'waiver@test.invalid',password:'fixture-password'}});
 assert.equal(auth.statusCode,200,auth.body);const cookies={fish_session:auth.cookies[0].value};
 const made=await app.inject({method:'POST',url:'/v1/bots',cookies,payload:{name:'waiver fixture',protocol:'mock'}});assert.equal(made.statusCode,201,made.body);
 const bot=made.json().id,[row]=await db.query('SELECT tenant_id FROM bots WHERE id=$1',[bot]);const tenant=row.tenant_id,actor=auth.json().user.id,reason='明确由平台承担无法核实的历史成本';
 await db.query("INSERT INTO platform_model_credits(tenant_id,granted,campaign_granted,campaign_expires_at) VALUES($1,10000,5000,now()+interval '7 days')",[tenant]);
 const hold=()=>db.transaction(tx=>reserveCredits(tx,tenant,bot,100));
 const legacy=async()=>{const id=await hold();await db.query("UPDATE platform_model_reservations SET dispatch_state='unknown' WHERE id=$1",[id]);const [r]=await db.query('SELECT * FROM platform_model_reservations WHERE id=$1',[id]);return{id,expected:{tokens:100,createdAt:new Date(r.created_at).toISOString()}}};
 const old=await legacy(),url='/v1/admin/platform-model-reservations/'+old.id+'/waive',body={expectedTokens:100,expectedCreatedAt:old.expected.createdAt,reason};
 assert.equal((await app.inject({method:'POST',url,cookies,payload:body})).statusCode,403);
 await db.query('UPDATE users SET is_admin=true WHERE id=$1',[actor]);
 assert.equal((await app.inject({method:'POST',url,cookies,payload:{...body,actorId:'forged'}})).statusCode,400);
 assert.equal((await app.inject({method:'POST',url,cookies,payload:{...body,expectedTokens:101}})).statusCode,409);
 const before=await creditBalance(db,tenant);
 const results=await Promise.all([1,2].map(()=>app.inject({method:'POST',url,cookies,payload:body})));
 for(const r of results)assert.equal(r.statusCode,200,r.body);
 assert.equal(results.filter(x=>x.json().released).length,1);
 const after=await creditBalance(db,tenant);assert.equal(after.reserved,before.reserved-100);assert.equal(after.spent,before.spent);assert.equal(after.campaign.reserved,before.campaign.reserved-100);
 const audits=await db.query("SELECT detail FROM audit_events WHERE event='admin.platform_reservation.waived'");assert.equal(audits.length,1);assert.equal(audits[0].detail.actorId,actor);assert.equal(audits[0].detail.consumption,'unknown');assert.equal(audits[0].detail.original.dispatch_state,'unknown');assert.equal(audits[0].detail.original.usage_tokens,null);assert.equal((await db.query('SELECT * FROM platform_model_reservations WHERE id=$1',[old.id])).length,0);
 for(const state of ['reserved','dispatched','receipt','transport_unknown']){
  const id=await hold(),[r]=await db.query('SELECT created_at FROM platform_model_reservations WHERE id=$1',[id]);
  if(state!=='reserved')await dispatchCredits(db,id,'real-provider');
  if(state==='receipt')await receiptCredits(db,id,31);
  if(state==='transport_unknown')await db.query("UPDATE platform_model_reservations SET dispatch_state='unknown' WHERE id=$1",[id]);
  await assert.rejects(waiveLegacyReservation(db,id,{tokens:100,createdAt:new Date(r.created_at).toISOString()},actor,reason),/状态已变化/);
  assert.equal((await db.query('SELECT id FROM platform_model_reservations WHERE id=$1',[id])).length,1);
 }
 const rollback=await legacy(),snapshot=await creditBalance(db,tenant);
 const failing={...db,transaction:<T>(fn:any)=>db.transaction<T>(tx=>fn({...tx,query:async(sql:string,params?:any[])=>{if(sql.startsWith('INSERT INTO audit_events'))throw new Error('audit unavailable');return tx.query(sql,params)}}))};
 await assert.rejects(waiveLegacyReservation(failing,rollback.id,rollback.expected,actor,reason),/audit unavailable/);
 assert.deepEqual(await creditBalance(db,tenant),snapshot);assert.equal((await db.query('SELECT id FROM platform_model_reservations WHERE id=$1',[rollback.id])).length,1);
 const expired=await legacy();await db.query("UPDATE platform_model_credits SET campaign_expires_at=now()-interval '1 day' WHERE tenant_id=$1",[tenant]);const expBefore=await creditBalance(db,tenant);await waiveLegacyReservation(db,expired.id,expired.expected,actor,reason);const expAfter=await creditBalance(db,tenant);assert.equal(expAfter.available,expBefore.available);assert.equal(expAfter.campaign.available,0);assert.equal(expAfter.spent,expBefore.spent);
});
