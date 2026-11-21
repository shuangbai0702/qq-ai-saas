import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp} from 'node:fs/promises';import path from 'node:path';import os from 'node:os';
import {createApp} from '../apps/control-api/src/app.js';import {issueTestInvite} from './helpers/invitations.js';
test('online QR refresh is rejected without lifecycle side effects; stale health allows recovery',async t=>{
 const {app,db}=await createApp({root:await mkdtemp(path.join(os.tmpdir(),'fish-onboarding-')),agent:false});t.after(()=>app.close());
 const auth=await app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(db,2),email:'onboarding@example.test',name:'new',password:'onboarding-test-123'}});const cookies={fish_session:auth.cookies[0].value};const call=(method:any,url:string,payload?:any)=>app.inject({method,url,payload,cookies});
 const id=(await call('POST','/v1/bots',{name:'QQ',protocol:'napcat',apiKey:'keep-key'})).json().id;
 const health={sampledAt:new Date().toISOString(),protocol:{online:true},ai:{ready:true}};
 await db.query("UPDATE bots SET desired_state='running',observed_state='online',health=$2::jsonb WHERE id=$1",[id,JSON.stringify(health)]);
 const [before]=await db.query('SELECT generation FROM bots WHERE id=$1',[id]);
 for(const refresh of [false,true]){const r=await call('POST',`/v1/bots/${id}/login-sessions`,{refresh});assert.equal(r.statusCode,409);assert.match(r.json().error,/QQ已在线/)}
 assert.equal((await db.query('SELECT generation FROM bots WHERE id=$1',[id]))[0].generation,before.generation);assert.equal((await db.query('SELECT * FROM commands')).length,0);assert.equal((await db.query('SELECT * FROM login_sessions')).length,0);assert.equal((await db.query('SELECT * FROM secrets')).length,1);
 await db.query('UPDATE users SET is_admin=true WHERE id=$1',[auth.json().user.id]);assert.equal((await call('GET','/v1/admin/overview')).json().online,1);
 health.sampledAt=new Date(Date.now()-100000).toISOString();await db.query('UPDATE bots SET health=$2::jsonb WHERE id=$1',[id,JSON.stringify(health)]);assert.equal((await call('GET','/v1/admin/overview')).json().online,0);
 assert.equal((await call('POST',`/v1/bots/${id}/login-sessions`,{})).statusCode,200);assert.equal((await db.query('SELECT * FROM login_sessions')).length,1);
});

test('explicit login after stop resumes AI, while a later stop fences its result',async t=>{
 const {app,db,agent}=await createApp({root:await mkdtemp(path.join(os.tmpdir(),'fish-relogin-')),agent:false});t.after(()=>app.close());
 const auth=await app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(db,2),email:'relogin@example.test',name:'relogin',password:'relogin-test-123'}});
 const cookies={fish_session:auth.cookies[0].value};const call=(method:any,url:string,payload?:any)=>app.inject({method,url,payload,cookies});
 const id=(await call('POST','/v1/bots',{name:'relogin',protocol:'mock',runtimeMode:'dedicated'})).json().id;
 let aiReady=false;
 agent.host.beginInspection=async()=>{};agent.host.endInspection=()=>{};
 agent.ai.probe=async()=>({alive:aiReady,ready:aiReady});agent.ai.start=async()=>{aiReady=true};agent.ai.stop=async()=>{aiReady=false};
 agent.protocols.mock.getHealth=async()=>({alive:true,ready:true,online:true});agent.protocols.mock.queryIdentity=async()=> '90123456';agent.protocols.mock.getLoginState=async()=>({state:'online',uin:'90123456'});
 agent.protocols.mock.stop=async()=>{};agent.protocols.mock.beginLogin=async()=>{};
 for(const state of ['stopped','offline']){
  await db.query("UPDATE bots SET desired_state=$2,observed_state=$2,health='{}' WHERE id=$1",[id,state]);
  const response=await call('POST',`/v1/bots/${id}/login-sessions`,{});assert.equal(response.statusCode,200,response.body);
  const [bot]=await db.query('SELECT * FROM bots WHERE id=$1',[id]);assert.equal(bot.desired_state,'running');assert.equal(bot.observed_state,'starting');
  const sid=response.json().id;
  const duplicate=await call('POST',`/v1/bots/${id}/login-sessions`,{});assert.equal(duplicate.json().id,sid);assert.equal((await db.query('SELECT generation FROM bots WHERE id=$1',[id]))[0].generation,bot.generation);
  // A pre-fix in-flight login can still have the stopped intent. Reusing it
  // must repair intent without creating a second session or generation.
  await db.query("UPDATE bots SET desired_state='stopped' WHERE id=$1",[id]);
  assert.equal((await call('POST',`/v1/bots/${id}/login-sessions`,{})).json().id,sid);
  assert.equal((await db.query('SELECT desired_state FROM bots WHERE id=$1',[id]))[0].desired_state,'running');
  aiReady=false;
  const [command]=await db.query("SELECT * FROM commands WHERE bot_id=$1 AND generation=$2 AND kind='login'",[id,bot.generation]);await agent.execute(command);
  assert.equal((await db.query('SELECT status FROM commands WHERE id=$1',[command.id]))[0].status,'completed');
  const [session]=await db.query('SELECT * FROM login_sessions WHERE id=$1',[sid]);await agent.pollLogin(session);
  assert.equal((await db.query('SELECT state FROM login_sessions WHERE id=$1',[sid]))[0].state,'online');
  await agent.health();assert.equal(aiReady,true,'explicit login must start the AI');assert.equal((await db.query('SELECT observed_state FROM bots WHERE id=$1',[id]))[0].observed_state,'online');
  await call('POST',`/v1/bots/${id}/actions`,{action:'stop',idempotencyKey:'stop-'+state});
  await agent.pollLogin(session);assert.equal((await db.query('SELECT desired_state FROM bots WHERE id=$1',[id]))[0].desired_state,'stopped','late login must not undo an explicit stop');
 }
});
