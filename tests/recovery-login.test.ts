import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {createApp} from '../apps/control-api/src/app.js';
import {issueTestInvite} from './helpers/invitations.js';

test('native QR recovery publishes once per process, honors lifecycle ownership, and uses protected normal API',async t=>{
 const {app,db,agent}=await createApp({root:await mkdtemp(path.join(os.tmpdir(),'fish-recovery-login-')),agent:false});t.after(()=>app.close());
 const registration=await app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(db),email:'qr-recovery@example.test',name:'测试',password:'qr-recovery-123'}});
 const cookies={fish_session:registration.cookies[0].value};
 const created=await app.inject({method:'POST',url:'/v1/bots',cookies,payload:{name:'二维码恢复',protocol:'mock'}});assert.equal(created.statusCode,201,created.body);const id=created.json().id;
 await db.query("UPDATE bots SET protocol='napcat',desired_state='running',generation=7 WHERE id=$1",[id]);
 const [bot]=await db.query('SELECT * FROM bots WHERE id=$1',[id]);const ctx=await agent.context(bot);
 let record:any={pid:123,created:456};let snapshot:any={state:'waiting_scan',qr:'https://example.test/recovery-qr'};
 agent.host.record=async()=>record;agent.host.inspect=async()=>record?{pid:record.pid,valid:true}:null;
 agent.protocols.napcat.getLoginState=async()=>snapshot;
 const health={alive:true,ready:true,online:false,pid:123,loginState:'waiting_scan' as const};
 await agent.recoverLoginSession(ctx,{...health,online:true});assert.equal((await db.query('SELECT * FROM login_sessions')).length,0,'quick login needs no QR session');
 await Promise.all([agent.recoverLoginSession(ctx,health),agent.recoverLoginSession(ctx,health)]);
 let rows=await db.query('SELECT * FROM login_sessions');assert.equal(rows.length,1);const sid=rows[0].id;assert.equal(rows[0].qr,snapshot.qr);
 const latest=await app.inject({method:'GET',url:`/v1/bots/${id}/login-session`,cookies});assert.equal(latest.json().id,sid);assert.equal(latest.json().hasQr,true);assert.equal(latest.json().qr,undefined);
 const image=await app.inject({method:'GET',url:`/v1/login-sessions/${sid}/qr`,cookies});assert.equal(image.statusCode,200);assert.match(image.headers['content-type']||'',/image\/png/);assert.equal(image.headers['cache-control'],'no-store');assert.deepEqual(image.rawPayload.subarray(0,8),Buffer.from([137,80,78,71,13,10,26,10]));
 assert.equal((await app.inject({method:'GET',url:`/v1/login-sessions/${sid}/qr`})).statusCode,401);
 await db.query("UPDATE login_sessions SET state='expired',qr=NULL WHERE id=$1",[sid]);await agent.recoverLoginSession(ctx,health);assert.equal((await db.query('SELECT * FROM login_sessions')).length,1,'same process cannot renew expired login indefinitely');
 assert.equal((await app.inject({method:'GET',url:`/v1/login-sessions/${sid}/qr`,cookies})).statusCode,410);
 record={pid:124,created:457};const nextHealth={...health,pid:124};
 let release!:()=>void;let entered!:()=>void;const boundary=new Promise<void>(r=>entered=r),gate=new Promise<void>(r=>release=r);
 agent.protocols.napcat.getLoginState=async()=>{entered();await gate;return snapshot};const pending=agent.recoverLoginSession(ctx,nextHealth);await boundary;
 await db.query('UPDATE bots SET generation=8 WHERE id=$1',[id]);release();await pending;assert.equal((await db.query('SELECT * FROM login_sessions')).length,1,'manual refresh generation wins over late native QR');
 await db.query('UPDATE bots SET generation=7 WHERE id=$1',[id]);
 agent.protocols.napcat.getLoginState=async()=>{record={pid:125,created:458};return snapshot};await agent.recoverLoginSession(ctx,nextHealth);assert.equal((await db.query('SELECT * FROM login_sessions')).length,1,'process replacement rejects old QR');
 agent.protocols.napcat.getLoginState=async()=>snapshot;await agent.recoverLoginSession(ctx,{...health,pid:125});assert.equal((await db.query('SELECT * FROM login_sessions')).length,2,'new process gets a fresh observation window');
});

test('native recovery QR expiry preserves the established offline grace and cannot expire a newer generation',async t=>{
 const {app,db,agent}=await createApp({root:await mkdtemp(path.join(os.tmpdir(),'fish-recovery-expiry-')),agent:false});t.after(()=>app.close());
 const r=await app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(db),email:'qr-expiry@example.test',name:'测试',password:'qr-expiry-123'}});
 const cookies={fish_session:r.cookies[0].value};
 const id=(await app.inject({method:'POST',url:'/v1/bots',cookies,payload:{name:'恢复期限',protocol:'mock'}})).json().id;
 await db.query("UPDATE bots SET protocol='napcat',desired_state='running',generation=7 WHERE id=$1",[id]);
 const [bot]=await db.query('SELECT * FROM bots WHERE id=$1',[id]);const ctx=await agent.context(bot),record={pid:123,created:456};
 agent.host.record=async()=>record as any;agent.host.inspect=async()=>({pid:record.pid,valid:true}) as any;
 agent.protocols.napcat.getLoginState=async()=>({state:'waiting_scan',qr:'https://example.test/recovery-qr'});
 await agent.recoverLoginSession(ctx,{alive:true,ready:true,online:false,pid:123,loginState:'waiting_scan'});
 const [session]=await db.query('SELECT * FROM login_sessions WHERE bot_id=$1',[id]);
 await db.query("UPDATE login_sessions SET expires_at=now()-interval '1 second' WHERE id=$1",[session.id]);
 const [expired]=await db.query('SELECT * FROM login_sessions WHERE id=$1',[session.id]);
 await agent.pollLogin(expired);
 assert.equal((await db.query('SELECT state,qr FROM login_sessions WHERE id=$1',[session.id]))[0].state,'expired');
 assert.equal((await db.query("SELECT * FROM commands WHERE bot_id=$1 AND kind='cancel-login'",[id])).length,0,'QR expiry must not shorten the account offline window');
 assert.equal((await db.query('SELECT desired_state,generation FROM bots WHERE id=$1',[id]))[0].desired_state,'running');
 await agent.recoverLoginSession(ctx,{alive:true,ready:true,online:false,pid:123,loginState:'waiting_scan'});
 assert.equal((await db.query('SELECT * FROM login_sessions WHERE bot_id=$1',[id])).length,1,'expiry must not renew a QR forever');
 // A refresh/new generation owns the remaining lifecycle, even if the old
 // expiry worker had already read its Bot before awaiting the transaction.
 await db.query("UPDATE login_sessions SET state='waiting_scan' WHERE id=$1",[session.id]);
 const transaction=db.transaction.bind(db);let once=true;
 db.transaction=async fn=>{if(once){once=false;await db.query('UPDATE bots SET generation=8 WHERE id=$1',[id])}return transaction(fn)};
 await agent.pollLogin(expired);
 assert.equal((await db.query('SELECT state FROM login_sessions WHERE id=$1',[session.id]))[0].state,'waiting_scan');
});
