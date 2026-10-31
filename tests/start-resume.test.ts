import test from 'node:test';import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,readFile} from 'node:fs/promises';import path from 'node:path';import os from 'node:os';
import {createApp} from '../apps/control-api/src/app.js';import {issueTestInvite} from './helpers/invitations.js';
import type {LoginSnapshot,RuntimeContext} from '../packages/contracts/index.js';

test('start observes saved NapCat resume, reuses pending start and exposes QR fallback without relaunch',async t=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'fish-resume-'));const {app,db,agent}=await createApp({root,agent:false});t.after(()=>app.close());
 const registration=await app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(db,3),email:'resume@example.test',name:'resume',password:'resume-test-123'}});
 const cookies={fish_session:registration.cookies[0].value};const call=(method:any,url:string,payload?:any)=>app.inject({method,url,payload,cookies});
 const id=(await call('POST','/v1/bots',{name:'saved',protocol:'napcat',runtimeMode:'dedicated',expectedUin:'90123456',apiKey:'keep-key'})).json().id;
 const qq=path.join(root,'bots',id,'qq');await mkdir(qq,{recursive:true});await writeFile(path.join(qq,'identity.json'),JSON.stringify({uin:'90123456'}));await writeFile(path.join(qq,'session.bin'),'preserved-session');
 await db.query("UPDATE bots SET desired_state='offline',observed_state='offline' WHERE id=$1",[id]);
 let launches=0,stops=0,alive=false,login:LoginSnapshot={state:'waiting_scan',qr:'fallback-qr'};let launched:RuntimeContext|undefined;
 agent.ai.start=async()=>{};agent.ai.stop=async()=>{};
 agent.protocols.napcat.beginLogin=async ctx=>{launches++;alive=true;launched=ctx};agent.protocols.napcat.stop=async()=>{stops++;alive=false};
 agent.protocols.napcat.getHealth=async()=>({alive,ready:alive,online:alive&&login.state==='online'});agent.protocols.napcat.getLoginState=async()=>login;
 const first=(await call('POST',`/v1/bots/${id}/actions`,{action:'start',idempotencyKey:'start-1'})).json();assert.ok(first.loginSessionId);
 const duplicate=(await call('POST',`/v1/bots/${id}/actions`,{action:'start',idempotencyKey:'start-2'})).json();assert.equal(duplicate.id,first.id);assert.equal(duplicate.loginSessionId,first.loginSessionId);
 assert.equal((await db.query('SELECT * FROM commands WHERE bot_id=$1',[id])).length,1);
 await agent.execute((await db.query('SELECT * FROM commands WHERE id=$1',[first.id]))[0]);assert.equal(launches,1);assert.equal(stops,0);assert.notEqual(launched?.loginIntent,'qr','normal start must allow native session resume');
 const session=async()=> (await db.query('SELECT * FROM login_sessions WHERE id=$1',[first.loginSessionId]))[0];
 await agent.pollLogin(await session());const fallback=await call('GET',`/v1/login-sessions/${first.loginSessionId}`);assert.equal(fallback.json().state,'waiting_scan');assert.equal(fallback.json().hasQr,true);assert.equal(fallback.json().qr,undefined);
 assert.equal((await call('GET',`/v1/login-sessions/${first.loginSessionId}/qr`)).statusCode,200);assert.equal(launches,1,'QR is from the same process');
 login={state:'online',uin:'90123456'};await agent.pollLogin(await session());assert.equal((await session()).state,'online');assert.equal((await db.query('SELECT qq_uin FROM qq_bindings WHERE bot_id=$1',[id]))[0].qq_uin,'90123456');
 await db.query("UPDATE bots SET health=$2::jsonb WHERE id=$1",[id,JSON.stringify({sampledAt:new Date().toISOString(),protocol:{online:true}})]);
 const online=(await call('POST',`/v1/bots/${id}/actions`,{action:'start',idempotencyKey:'start-online'})).json();assert.equal(online.reused,true);assert.equal(launches,1);assert.equal(stops,0);
 assert.equal(await readFile(path.join(qq,'session.bin'),'utf8'),'preserved-session');assert.equal((await db.query('SELECT * FROM secrets WHERE bot_id=$1',[id])).length,1);
 const stranger=await app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(db,1),email:'stranger-resume@example.test',name:'other',password:'resume-test-123'}});
 assert.equal((await app.inject({method:'GET',url:`/v1/login-sessions/${first.loginSessionId}/qr`,cookies:{fish_session:stranger.cookies[0].value}})).statusCode,404);
 // A delayed online result cannot bind or stop a replacement generation.
 await db.query("UPDATE login_sessions SET state='starting' WHERE id=$1",[first.loginSessionId]);
 let release!:(s:LoginSnapshot)=>void;agent.protocols.napcat.getLoginState=()=>new Promise(resolve=>{release=resolve});
 const late=agent.pollLogin(await session());while(!release)await new Promise(resolve=>setImmediate(resolve));
 await call('POST',`/v1/bots/${id}/actions`,{action:'stop',idempotencyKey:'stop-new'});const before=stops;release({state:'online',uin:'90123456'});await late;assert.equal((await session()).state,'canceled');assert.equal(stops,before);
});
