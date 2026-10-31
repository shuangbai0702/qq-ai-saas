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
