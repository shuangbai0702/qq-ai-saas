import {issueTestInvite} from './helpers/invitations.js';
import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp}from'node:fs/promises';import os from'node:os';import path from'node:path';import{createApp}from'../apps/control-api/src/app.js';
test('reconcile persisted desired state, bound recovery and late login cancellation',async t=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'fish-recovery-'));const{app,db,agent}=await createApp({root,agent:false});t.after(()=>app.close());
 const r=await app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(db),email:'recovery@example.test',name:'恢复',password:'recovery-test-123'}});const cookies={fish_session:r.cookies[0].value};const call=(method:any,url:string,payload?:any)=>app.inject({method,url,payload,cookies});
 const id=(await call('POST','/v1/bots',{name:'恢复测试',protocol:'mock',runtimeMode:'dedicated'})).json().id;await db.query("UPDATE bots SET desired_state='running',generation=7 WHERE id=$1",[id]);
 agent.ai.probe=async()=>({alive:false,ready:false});agent.protocols.mock.getHealth=async()=>({alive:false,ready:false});
 await agent.health();let cmds=await db.query("SELECT * FROM commands WHERE bot_id=$1",[id]);assert.equal(cmds.length,1);assert.equal(cmds[0].generation,7);assert.match(cmds[0].idempotency_key,/^recover-/);
 await agent.health();assert.equal((await db.query('SELECT * FROM commands')).length,1,'no duplicate recovery while pending');
 for(let i=0;i<3;i++){await db.query("UPDATE commands SET status='failed'");const state=agent.recovery.get(id)!;state.next=0;await agent.health()}
 assert.equal((await db.query('SELECT * FROM commands')).length,3,'at most three attempts per agent lifetime');
 await db.query("UPDATE bots SET desired_state='stopped' WHERE id=$1",[id]);await agent.health();assert.equal(agent.recovery.has(id),false);
 let stopped=0;agent.protocols.mock.cancelLogin=async()=>{stopped++};
 const first=(await call('POST',`/v1/bots/${id}/login-sessions`,{})).json().id;await call('DELETE',`/v1/login-sessions/${first}`);const second=(await call('POST',`/v1/bots/${id}/login-sessions`,{refresh:true})).json().id;assert.notEqual(first,second);
 const [late]=await db.query("SELECT * FROM commands WHERE kind='cancel-login' ORDER BY created_at DESC LIMIT 1");await agent.execute(late);assert.equal(stopped,0,'stale cancel cannot stop refreshed login');
 const [current]=await db.query('SELECT generation FROM bots WHERE id=$1',[id]);await db.query("UPDATE bots SET generation=generation+1 WHERE id=$1",[id]);await agent.execute({...late,generation:current.generation});const[c]=await db.query('SELECT status FROM commands WHERE id=$1',[late.id]);assert.equal(c.status,'superseded');
});
