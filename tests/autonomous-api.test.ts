import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {createApp} from '../apps/control-api/src/app.js';
import {issueTestInvite} from './helpers/invitations.js';
import {AutonomyRepository} from '../packages/database/autonomous.js';

test('autonomous public control enforces tenancy, epochs and preserves runtime/config data',async t=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'fishbot-autonomous-api-'));const {app,db}=await createApp({root,agent:false});t.after(async()=>{await app.close();await rm(root,{recursive:true,force:true})});
 const register=async(email:string)=>{const r=await app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(db),email,password:'test-password-123',name:'tester'}});assert.equal(r.statusCode,200,r.body);return r.cookies[0].value};
 const a=await register('auto-a@example.com'),b=await register('auto-b@example.com');
 const call=(cookie:string,method:any,url:string,payload?:any)=>app.inject({method,url,payload,cookies:{fish_session:cookie}});
 const r=await call(a,'POST','/v1/bots',{name:'autonomous robot',protocol:'mock'});assert.equal(r.statusCode,201,r.body);const id=r.json().id,url='/v1/bots/'+id+'/autonomous';
 const [before]=await db.query('SELECT * FROM bots WHERE id=$1',[id]);
 const initial=await call(a,'GET',url);assert.equal(initial.statusCode,200);assert.equal(initial.json().settings.config.mode,'passive');
 assert.equal((await call(b,'GET',url)).statusCode,404);assert.equal((await call(b,'PUT',url,{expectedEpoch:1,config:{mode:'active'}})).statusCode,404);
 assert.equal((await call(a,'PUT',url,{expectedEpoch:1,config:{mode:'active',capabilities:['shell']}})).statusCode,400);
 const enabled=await call(a,'PUT',url,{expectedEpoch:1,config:{mode:'active'}});assert.equal(enabled.statusCode,200,enabled.body);assert.equal(enabled.json().pendingRuntime,true);
 assert.equal((await call(a,'PUT',url,{expectedEpoch:1,config:{mode:'passive'}})).statusCode,409);
 const wake=await call(a,'POST',url+'/wake',{expectedEpoch:2});assert.equal(wake.statusCode,200);assert.equal(wake.json().startsQQ,false);
 const stop=await call(a,'PUT',url,{expectedEpoch:2,config:{mode:'passive'}});assert.equal(stop.statusCode,200);
 const [after]=await db.query('SELECT * FROM bots WHERE id=$1',[id]);assert.equal(after.generation,before.generation);assert.equal(after.revision,before.revision);assert.equal(after.desired_state,before.desired_state);
 assert.equal((await db.query('SELECT * FROM commands WHERE bot_id=$1',[id])).length,0);assert.equal((await db.query('SELECT * FROM autonomy_wakes WHERE bot_id=$1',[id])).length,0);
});

test('admin autonomous operations expose bounded metadata and audit a fenced single-bot pause',async t=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'fishbot-autonomous-admin-'));const {app,db}=await createApp({root,agent:false});t.after(async()=>{await app.close();await rm(root,{recursive:true,force:true})});
 const register=async(email:string)=>{const r=await app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(db,2),email,password:'test-password-123',name:'tester'}});assert.equal(r.statusCode,200,r.body);return{cookie:r.cookies[0].value,id:r.json().user.id}};
 const a=await register('autonomous-admin@example.com'),b=await register('autonomous-member@example.com');await db.query('UPDATE users SET is_admin=true WHERE id=$1',[a.id]);
 const call=(cookie:string,method:any,url:string,payload?:any)=>app.inject({method,url,payload,cookies:{fish_session:cookie}});
 const id=(await call(b.cookie,'POST','/v1/bots',{name:'member bot',protocol:'mock'})).json().id,other=(await call(b.cookie,'POST','/v1/bots',{name:'other bot',protocol:'mock'})).json().id;
 const [before]=await db.query('SELECT * FROM bots WHERE id=$1',[id]),repo=new AutonomyRepository(db);
 await repo.configure(before.tenant_id,id,1,{mode:'active'});await repo.applied(before.tenant_id,id,2);
 await db.query("UPDATE bots SET desired_state='running',health=$2 WHERE id=$1",[id,JSON.stringify({sampledAt:new Date(Date.now()-120000).toISOString(),protocol:{online:true,token:'PRIVATE-HEALTH-SECRET'}})]);
 await repo.configure(before.tenant_id,other,1,{mode:'active'});
 await db.query("INSERT INTO autonomy_cycles(id,bot_id,tenant_id,epoch,generation,config_revision,owner,state,budget) VALUES('old-cycle',$1,$2,2,1,1,'old','ended','{}')",[id,before.tenant_id]);
 await db.query("INSERT INTO autonomy_actions(id,cycle_id,bot_id,tenant_id,epoch,kind,target,objective,status,expires_at,result) VALUES('old-action','old-cycle',$1,$2,2,'message','private:12345678','PRIVATE-CHAT-CONTENT','unknown',now(),$3)",[id,before.tenant_id,JSON.stringify({status:'unknown',summary:'PRIVATE-RESULT-CONTENT',effects:[{id:'old-action:0',status:'unknown'}]})]);
 for(const url of ['/v1/admin/autonomous',`/v1/admin/autonomous/${id}/actions`]){assert.equal((await app.inject({method:'GET',url})).statusCode,401);assert.equal((await call(b.cookie,'GET',url)).statusCode,403)}
 assert.equal((await call(b.cookie,'POST',`/v1/admin/autonomous/${id}/pause`,{expectedEpoch:2,reason:'investigate'})).statusCode,403);
 const overview=await call(a.cookie,'GET','/v1/admin/autonomous');assert.equal(overview.statusCode,200,overview.body);const item=overview.json().items.find((v:any)=>v.bot_id===id);assert.ok(item.unconfirmed);assert.equal(item.online,false);assert.equal(item.fresh,false);assert.equal(overview.json().pageSize,20);assert.ok(!overview.body.includes('PRIVATE'));
 const actions=await call(a.cookie,'GET',`/v1/admin/autonomous/${id}/actions`);assert.equal(actions.statusCode,200,actions.body);assert.equal(actions.json().canResend,false);assert.equal(actions.json().items[0].id,'old-action');assert.equal(actions.json().items[0].effects[0].status,'unknown');assert.ok(!actions.body.includes('PRIVATE'));
 const url=`/v1/admin/autonomous/${id}/pause`,pauses=await Promise.all([1,2].map(()=>call(a.cookie,'POST',url,{expectedEpoch:2,reason:'核对不明投递，暂停后台'})));assert.deepEqual(pauses.map(v=>v.statusCode).sort(),[200,409]);
 const current=await repo.view(before.tenant_id,id);assert.equal(current.settings.config.mode,'passive');assert.equal(current.settings.epoch,3);assert.equal(current.actions[0].status,'unknown');assert.equal((await repo.view(before.tenant_id,other)).settings.config.mode,'active');
 const audit=await db.query("SELECT * FROM audit_events WHERE event='admin.autonomous.paused'");assert.equal(audit.length,1);assert.equal(audit[0].detail.actorId,a.id);assert.equal(audit[0].bot_id,id);
 const [after]=await db.query('SELECT * FROM bots WHERE id=$1',[id]);assert.equal(after.revision,before.revision);assert.equal(after.generation,before.generation);assert.equal(after.desired_state,'running');assert.equal((await db.query('SELECT * FROM commands')).length,0);
 assert.equal((await call(a.cookie,'GET','/v1/bots/'+id+'/autonomous')).statusCode,404,'admin operations must not broaden tenant APIs');
 assert.equal((await call(a.cookie,'GET','/v1/admin/autonomous?page=0')).statusCode,400);assert.equal((await call(a.cookie,'GET','/v1/admin/autonomous?filter=bogus')).statusCode,400);assert.equal((await call(a.cookie,'POST',url,{expectedEpoch:3,reason:'test',config:{mode:'active'}})).statusCode,400);
});

test('tenant autonomous status distinguishes expired health from a current QQ connection',async t=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'fishbot-autonomous-fresh-'));const{app,db}=await createApp({root,agent:false});t.after(async()=>{await app.close();await rm(root,{recursive:true,force:true})});
 const registered=await app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(db),email:'auto-fresh@example.com',password:'test-password-123',name:'tester'}});assert.equal(registered.statusCode,200,registered.body);
 const cookies={fish_session:registered.cookies[0].value},created=await app.inject({method:'POST',url:'/v1/bots',cookies,payload:{name:'freshness robot',protocol:'mock'}});assert.equal(created.statusCode,201,created.body);const id=created.json().id;
 const statuses:any[]=[];
 for(const[fresh,sampledAt]of [[true,new Date().toISOString()],[false,new Date(Date.now()-120000).toISOString()],[false,new Date(Date.now()+600000).toISOString()],[false,undefined]] as const){
  await db.query("UPDATE bots SET desired_state='running',health=$2 WHERE id=$1",[id,JSON.stringify({sampledAt,protocol:{online:true}})]);
  const r=await app.inject({method:'GET',url:`/v1/bots/${id}/autonomous`,cookies});assert.equal(r.statusCode,200,r.body);assert.equal(r.json().online,fresh,'saved online flag outlived its health sample');statuses.push([r.json(),fresh]);
 }
 for(const[status,fresh]of statuses)assert.equal(status.fresh,fresh);
 assert.equal((await db.query('SELECT * FROM commands')).length,0);
});
