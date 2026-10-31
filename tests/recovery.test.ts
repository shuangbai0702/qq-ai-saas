import {issueTestInvite} from './helpers/invitations.js';
import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp}from'node:fs/promises';import os from'node:os';import path from'node:path';import{createApp}from'../apps/control-api/src/app.js';
import {randomUUID} from 'node:crypto';
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

test('automatic recovery preserves healthy QQ and AI components while manual restart still replaces both',async t=>{
 const {app,db,agent}=await createApp({root:await mkdtemp(path.join(os.tmpdir(),'fish-component-recovery-')),agent:false});t.after(()=>app.close());
 const auth=await app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(db,2),email:'component-recovery@example.test',name:'组件恢复',password:'component-recovery-123'}});
 const cookies={fish_session:auth.cookies[0].value};
 for(const mode of ['dedicated','shared']){
  const created=await app.inject({method:'POST',url:'/v1/bots',cookies,payload:{name:'组件恢复-'+mode,protocol:'mock',runtimeMode:mode}});assert.equal(created.statusCode,201,created.body);const botId=created.json().id;
  await db.query("UPDATE bots SET desired_state='running',generation=7 WHERE id=$1",[botId]);
  let aiReady=false,protocolAlive=false,failAI=false,failProtocol=false;const calls:{action:string;botId:string}[]=[];const ai=mode==='shared'?agent.shared:agent.ai;
  ai.probe=async()=>({alive:aiReady,ready:aiReady});
  ai.start=async ctx=>{calls.push({action:'ai-start',botId:ctx.botId});if(failAI)throw new Error('fixture AI unavailable');aiReady=true};
  ai.stop=async(id:string)=>{calls.push({action:'ai-stop',botId:id});aiReady=false};
  agent.protocols.mock.getHealth=async()=>({alive:protocolAlive,ready:protocolAlive,online:protocolAlive});
  agent.protocols.mock.beginLogin=async ctx=>{calls.push({action:'protocol-start',botId:ctx.botId});if(failProtocol)throw new Error('fixture protocol unavailable');protocolAlive=true};
  agent.protocols.mock.stop=async id=>{calls.push({action:'protocol-stop',botId:id});protocolAlive=false};
  const cases=[
   {ai:false,protocol:true,automatic:true,actions:['ai-start'],status:'completed'},
   {ai:true,protocol:false,automatic:true,actions:['protocol-start'],status:'completed'},
   {ai:false,protocol:false,automatic:true,actions:['ai-start','protocol-start'],status:'completed'},
   {ai:true,protocol:true,automatic:false,actions:['ai-stop','protocol-stop','ai-start','protocol-start'],status:'completed'},
   {ai:true,protocol:true,automatic:true,actions:[],status:'superseded'},
   {ai:false,protocol:true,automatic:true,actions:['ai-start'],status:'failed',failAI:true},
   {ai:true,protocol:false,automatic:true,actions:['protocol-start'],status:'failed',failProtocol:true},
   {ai:false,protocol:false,automatic:true,actions:[],status:'superseded',generation:6}
  ];
  for(const row of cases){
   aiReady=row.ai;protocolAlive=row.protocol;failAI=!!row.failAI;failProtocol=!!row.failProtocol;calls.length=0;const id=randomUUID();
   const [command]=await db.query("INSERT INTO commands(id,bot_id,idempotency_key,generation,kind) VALUES($1,$2,$3,$4,'restart') RETURNING *",[id,botId,(row.automatic?'recover-':'manual-')+id,row.generation??7]);
   await agent.execute(command);
   assert.deepEqual(calls.map(c=>c.action),row.actions,`${mode}: ${JSON.stringify(row)}`);assert.ok(calls.every(c=>c.botId===botId),'recovery only touches its actual Bot');
   assert.equal((await db.query('SELECT status FROM commands WHERE id=$1',[id]))[0].status,row.status);
  }
 }
});
