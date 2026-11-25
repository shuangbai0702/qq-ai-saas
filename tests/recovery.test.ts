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
 for(let i=0;i<2;i++){await db.query("UPDATE commands SET status='failed'");const state=agent.recovery.get(id)!;state.next=0;await agent.health()}
 assert.equal((await db.query('SELECT * FROM commands')).length,3);
 await db.query("UPDATE commands SET status='failed'");await agent.health();assert.equal((await db.query('SELECT * FROM commands')).length,3,'three fast attempts enter a ten-minute cooldown');
 const state=agent.recovery.get(id)!;assert.ok(state.next>Date.now()+590000);state.next=0;await agent.health();assert.equal((await db.query('SELECT * FROM commands')).length,4,'cooldown expiry recovers without Agent restart');
 await db.query("UPDATE commands SET status='failed'");state.next=0;state.recent=Array(9).fill(Date.now());await agent.health();assert.equal((await db.query('SELECT * FROM commands')).length,4,'rolling hourly ceiling limits a flapping bot');
 state.recent=[];state.next=0;const query=db.query.bind(db);db.query=async(sql,params)=>{if(sql.startsWith('INSERT INTO commands(id,bot_id,kind'))throw new Error('fixture insert failure');return query(sql,params)};
 const attempts=state.attempts;await agent.health();db.query=query;assert.equal(state.attempts,attempts,'failed ledger insert never consumes a process recovery attempt');state.next=0;await agent.health();assert.equal((await db.query('SELECT * FROM commands')).length,5,'insert failure can retry');
 await db.query("UPDATE bots SET desired_state='stopped' WHERE id=$1",[id]);await agent.health();assert.equal(agent.recovery.has(id),false);
 let stopped=0;agent.protocols.mock.cancelLogin=async()=>{stopped++};
 const first=(await call('POST',`/v1/bots/${id}/login-sessions`,{})).json().id;await call('DELETE',`/v1/login-sessions/${first}`);const second=(await call('POST',`/v1/bots/${id}/login-sessions`,{refresh:true})).json().id;assert.notEqual(first,second);
 const [late]=await db.query("SELECT * FROM commands WHERE kind='cancel-login' ORDER BY created_at DESC LIMIT 1");await agent.execute(late);assert.equal(stopped,0,'stale cancel cannot stop refreshed login');
 const [current]=await db.query('SELECT generation FROM bots WHERE id=$1',[id]);await db.query("UPDATE bots SET generation=generation+1 WHERE id=$1",[id]);await agent.execute({...late,generation:current.generation});const[c]=await db.query('SELECT status FROM commands WHERE id=$1',[late.id]);assert.equal(c.status,'superseded');
});

test('live AI readiness is bounded and recovery replaces dedicated AI without stopping online QQ',async t=>{
 const {app,db,agent}=await createApp({root:await mkdtemp(path.join(os.tmpdir(),'fish-readiness-')),agent:false});t.after(()=>app.close());
 const auth=await app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(db),email:'readiness@example.test',name:'readiness',password:'readiness-test-123'}});
 const created=await app.inject({method:'POST',url:'/v1/bots',cookies:{fish_session:auth.cookies[0].value},payload:{name:'readiness',protocol:'mock',runtimeMode:'dedicated'}});const id=created.json().id;
 await db.query("UPDATE bots SET desired_state='running',generation=7 WHERE id=$1",[id]);agent.host.beginInspection=async()=>{};agent.host.endInspection=()=>{};
 let ready=false;const calls:string[]=[];agent.ai.probe=async()=>({alive:true,ready});agent.ai.stop=async()=>{calls.push('ai-stop')};agent.ai.start=async()=>{calls.push('ai-start');ready=true};
 agent.protocols.mock.getHealth=async()=>({alive:true,ready:true,online:true});agent.protocols.mock.queryIdentity=async()=> '90123456';agent.protocols.mock.stop=async()=>{calls.push('protocol-stop')};agent.protocols.mock.beginLogin=async()=>{calls.push('protocol-start')};
 await agent.health();assert.equal((await db.query('SELECT * FROM commands')).length,0,'initial startup grace is preserved');
 const retry=agent.recovery.get(id)!;retry.unreadySince=Date.now()-120001;await agent.health();const commands=await db.query('SELECT * FROM commands');assert.equal(commands.length,1,'live unready AI eventually enters bounded recovery');
 await agent.execute(commands[0]);assert.deepEqual(calls,['ai-stop','ai-start']);assert.equal((await db.query('SELECT desired_state FROM bots WHERE id=$1',[id]))[0].desired_state,'running');
 ready=false;await db.query("UPDATE commands SET status='completed'");retry.next=0;await agent.health();assert.equal((await db.query('SELECT * FROM commands')).length,1,'fresh AI receives another readiness grace');
});

test('shared live-unready recovery uses the marked ledger path and preserves protocol',async t=>{
 const {app,db,agent}=await createApp({root:await mkdtemp(path.join(os.tmpdir(),'fish-shared-readiness-')),agent:false});t.after(()=>app.close());
 const auth=await app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(db),email:'shared-readiness@example.test',name:'shared readiness',password:'readiness-test-123'}});
 const created=await app.inject({method:'POST',url:'/v1/bots',cookies:{fish_session:auth.cookies[0].value},payload:{name:'shared readiness',protocol:'mock',runtimeMode:'shared'}});const id=created.json().id;
 await db.query("UPDATE bots SET desired_state='running',generation=7 WHERE id=$1",[id]);agent.host.beginInspection=async()=>{};agent.host.endInspection=()=>{};
 const calls:string[]=[];agent.shared.probe=async()=>({alive:true,ready:false});agent.shared.recoverReadiness=async()=>{calls.push('shared-recover')};agent.shared.start=async()=>{calls.push('shared-start')};agent.shared.stop=async()=>{calls.push('shared-stop')};
 agent.protocols.mock.getHealth=async()=>({alive:true,ready:true,online:true});agent.protocols.mock.queryIdentity=async()=> '90123456';agent.protocols.mock.stop=async()=>{calls.push('protocol-stop')};agent.protocols.mock.beginLogin=async()=>{calls.push('protocol-start')};
 await agent.health();assert.equal((await db.query('SELECT * FROM commands')).length,0);agent.recovery.get(id)!.unreadySince=Date.now()-120001;await agent.health();const [command]=await db.query('SELECT * FROM commands');assert.match(command.idempotency_key,/-readiness-/);
 await agent.execute(command);assert.deepEqual(calls,['shared-recover']);assert.equal((await db.query('SELECT status FROM commands'))[0].status,'completed');
});

test('automatic recovery preserves healthy QQ and AI components while manual restart still replaces both',async t=>{
 const {app,db,agent}=await createApp({root:await mkdtemp(path.join(os.tmpdir(),'fish-component-recovery-')),agent:false});t.after(()=>app.close());
 const auth=await app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(db,2),email:'component-recovery@example.test',name:'组件恢复',password:'component-recovery-123'}});
 const cookies={fish_session:auth.cookies[0].value};
 for(const mode of ['dedicated','shared']){
  const created=await app.inject({method:'POST',url:'/v1/bots',cookies,payload:{name:'组件恢复-'+mode,protocol:'mock',runtimeMode:mode}});assert.equal(created.statusCode,201,created.body);const botId=created.json().id;
  await db.query("UPDATE bots SET desired_state='running',generation=7,expected_uin='90123456' WHERE id=$1",[botId]);
  let aiReady=false,protocolAlive=false,failAI=false,failProtocol=false;const calls:{action:string;botId:string}[]=[];const ai=mode==='shared'?agent.shared:agent.ai;
  ai.probe=async()=>({alive:aiReady,ready:aiReady});
  ai.start=async ctx=>{calls.push({action:'ai-start',botId:ctx.botId});if(failAI)throw new Error('fixture AI unavailable');aiReady=true};
  ai.stop=async(id:string)=>{calls.push({action:'ai-stop',botId:id});aiReady=false};
  agent.protocols.mock.queryIdentity=async()=> '90123456';
  agent.protocols.mock.getHealth=async()=>({alive:protocolAlive,ready:protocolAlive,online:protocolAlive});
  agent.protocols.mock.beginLogin=async ctx=>{calls.push({action:'protocol-start',botId:ctx.botId});if(failProtocol)throw new Error('fixture protocol unavailable');protocolAlive=true};
  agent.protocols.mock.stop=async id=>{calls.push({action:'protocol-stop',botId:id});protocolAlive=false};
  const cases=[
   {ai:false,protocol:true,automatic:true,actions:['ai-start'],status:'completed'},
   {ai:true,protocol:false,automatic:true,actions:['protocol-start'],status:'completed'},
   {ai:false,protocol:false,automatic:true,actions:mode==='shared'?['protocol-start','ai-start']:['ai-start','protocol-start'],status:'completed'},
   {ai:true,protocol:true,automatic:false,actions:mode==='shared'?['ai-stop','protocol-stop','protocol-start','ai-start']:['ai-stop','protocol-stop','ai-start','protocol-start'],status:'completed'},
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


test('native recovery is durable per PID, replaces protocol only, and rejects stale/online targets',async t=>{
 const {app,db,agent}=await createApp({root:await mkdtemp(path.join(os.tmpdir(),'fish-native-recovery-')),agent:false});t.after(()=>app.close());
 const auth=await app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(db),email:'native-recovery@example.test',name:'恢复',password:'native-recovery-123'}});
 const created=await app.inject({method:'POST',url:'/v1/bots',cookies:{fish_session:auth.cookies[0].value},payload:{name:'恢复',protocol:'mock',runtimeMode:'dedicated'}});assert.equal(created.statusCode,201,created.body);const id=created.json().id;
 await db.query("UPDATE bots SET desired_state='running',generation=7,expected_uin='90123456' WHERE id=$1",[id]);
 let record:any={pid:123,created:456},online=false,requested=true;const calls:string[]=[];
 agent.host.record=async()=>record;agent.host.stop=async(_id,kind,_intent,expected)=>{assert.equal(kind,'protocol');assert.deepEqual(expected,{pid:123,created:456});calls.push('stop');record=null};
 agent.ai.probe=async()=>({alive:true,ready:true});agent.ai.stop=async()=>{throw new Error('must preserve AI')};agent.ai.start=async()=>{throw new Error('must preserve AI')};
 agent.protocols.mock.getHealth=async()=>({alive:true,ready:!requested,online,pid:record?.pid,metrics:requested?{restartRequested:true}:{}});
 agent.protocols.mock.beginLogin=async()=>{calls.push('start');record={pid:124,created:457};requested=false};
 await agent.health();const [command]=await db.query("SELECT * FROM commands WHERE bot_id=$1",[id]);assert.equal(command.idempotency_key,'recover-protocol-7-123-456');
 await agent.health();assert.equal((await db.query('SELECT * FROM commands')).length,1);
 // A restarted Agent still sees the same durable claim; no duplicate request.
 await db.query("UPDATE commands SET status='failed' WHERE id=$1",[command.id]);agent.recovery.clear();await agent.health();assert.equal((await db.query('SELECT * FROM commands')).length,1);
 await db.query("UPDATE commands SET finished_at=now()-interval '20 seconds' WHERE id=$1",[command.id]);agent.recovery.clear();await agent.health();assert.equal((await db.query('SELECT status FROM commands WHERE id=$1',[command.id]))[0].status,'pending','transient failure retries the same durable claim after backoff');
 await agent.execute(command);assert.deepEqual(calls,['stop','start']);
 await agent.execute(command);assert.deepEqual(calls,['stop','start'],'a replay cannot stop the replacement PID');
 record={pid:123,created:456};requested=true;online=true;await agent.execute(command);assert.deepEqual(calls,['stop','start'],'reconnected protocol survives a queued request');
 online=false;await db.query("UPDATE bots SET generation=8 WHERE id=$1",[id]);await agent.execute(command);assert.deepEqual(calls,['stop','start'],'user lifecycle wins');
});


test('established NapCat/LLBot disconnects recover without eviction, preserve peers/AI, and honor user login/stop',async t=>{
 const {app,db,agent}=await createApp({root:await mkdtemp(path.join(os.tmpdir(),'fish-qq-resume-')),agent:false});t.after(()=>app.close());
 const auth=await app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(db,4),email:'qq-resume@example.test',name:'恢复',password:'qq-resume-test-123'}});
 const cookies={fish_session:auth.cookies[0].value};agent.host.beginInspection=async()=>{};agent.host.endInspection=()=>{};
 const states=new Map<string,any>(),calls:string[]=[];
 agent.host.record=async id=>states.get(id)?.record;
 agent.host.inspect=async id=>states.get(id)?.record;
 agent.host.stop=async(id,kind,_intent,expected)=>{const state=states.get(id);assert.equal(kind,'protocol');assert.deepEqual(expected,state.record);calls.push('stop:'+id);state.record=null};
 agent.ai.probe=agent.shared.probe=async()=>({alive:true,ready:true});
 agent.ai.stop=async()=>{throw Error('recovery must preserve dedicated AI')};agent.ai.start=async()=>{throw Error('recovery must preserve dedicated AI')};
 agent.shared.stop=async()=>{}; // Existing health policy releases an offline member's slot.
 for(const kind of ['napcat','llbot']){
  agent.protocols[kind]={...agent.protocols[kind],getHealth:async (ctx:any)=>{const state=states.get(ctx.botId);return{alive:!!state.record,ready:true,online:state.online,pid:state.record?.pid,loginState:state.login,metrics:state.requested?{restartRequested:true}:{}}},getLoginState:async (ctx:any)=>({state:states.get(ctx.botId).login}),queryIdentity:async()=> '90123456',beginLogin:async (ctx:any)=>{assert.equal(ctx.loginIntent,'resume');calls.push('start:'+ctx.botId);states.get(ctx.botId).record={pid:900+calls.length,created:Date.now()/1000}},stop:async (id:string)=>{calls.push('user-stop:'+id);states.get(id).record=null}} as any;
  for(const mode of ['dedicated','shared']){
   const response=await app.inject({method:'POST',url:'/v1/bots',cookies,payload:{name:kind+' '+mode,protocol:'mock',runtimeMode:mode}});assert.equal(response.statusCode,201,response.body);const id=response.json().id;
   const now=Date.now();await db.query("UPDATE bots SET protocol=$2,desired_state='running',generation=7,expected_uin='90123456',health=$3 WHERE id=$1",[id,kind,JSON.stringify({onlineSince:now-60000})]);
   const state:any={record:{pid:100+states.size,created:200+states.size},online:false,login:'failed',requested:false};states.set(id,state);
   // A real online history is retained; a short disconnect uses native reconnect first.
   await agent.health();assert.equal((await db.query('SELECT * FROM commands WHERE bot_id=$1',[id])).length,0);
   const age=async()=>{await db.query("UPDATE bots SET health=jsonb_set(health,'{offline}',$2::jsonb) WHERE id=$1",[id,JSON.stringify({since:Date.now()-360000,lastObservedAt:Date.now(),observations:3,reason:'account-offline',graceMs:300000})]);const retry=agent.recovery.get(id);if(retry)retry.next=0};
   await age();await agent.health();let [command]=await db.query("SELECT * FROM commands WHERE bot_id=$1 AND kind='restart'",[id]);assert.ok(command);assert.match(command.idempotency_key,/^recover-protocol-/);
   assert.equal((await db.query("SELECT * FROM commands WHERE bot_id=$1 AND kind='evict-offline'",[id])).length,0,'transient outage must survive the old five-minute deadline');
   await agent.health();assert.equal((await db.query('SELECT * FROM commands WHERE bot_id=$1',[id])).length,1,'pending claim is unique');
   // Online evidence after enqueue wins, but cannot poison a later disconnect of the same PID.
   state.online=true;state.login='online';const firstCalls=calls.length;await agent.execute(command);assert.equal(calls.length,firstCalls);assert.equal((await db.query('SELECT status FROM commands WHERE id=$1',[command.id]))[0].status,'superseded');
   state.online=false;state.login='failed';await db.query("UPDATE commands SET finished_at=now()-interval '20 seconds' WHERE id=$1",[command.id]);await age();await agent.health();assert.equal((await db.query('SELECT status FROM commands WHERE id=$1',[command.id]))[0].status,'pending');
   await agent.execute(command);assert.deepEqual(calls.slice(firstCalls),['stop:'+id,'start:'+id]);
   const after=calls.length;await agent.execute(command);assert.equal(calls.length,after,'late replay cannot replace a new protocol PID');
   // A replacement gets startup time despite the retained old outage deadline.
   await age();await agent.health();assert.equal((await db.query("SELECT * FROM commands WHERE bot_id=$1 AND status='pending'",[id])).length,0,'a young protocol cannot be restarted while its saved login is starting');
   state.record.created=Date.now()/1000-121;
   // A newly published login requirement cancels an already queued recovery.
   await age();await agent.health();[command]=await db.query("SELECT * FROM commands WHERE bot_id=$1 AND status='pending'",[id]);assert.ok(command);
   state.login='verification_required';await agent.execute(command);assert.equal(calls.length,after,'credential/verification evidence wins before restart');
   await agent.health();assert.equal((await db.query('SELECT health FROM bots WHERE id=$1',[id]))[0].health.protocol.loginRequired,true);
   state.login='starting';state.record=null;state.requested=true;await age();await agent.health();assert.equal((await db.query("SELECT * FROM commands WHERE bot_id=$1 AND kind='restart' AND status='pending'",[id])).length,0,'later unavailability cannot erase the recorded credential requirement');
   // User stop is authoritative even if an old native recovery asks to resume.
   const stop=await app.inject({method:'POST',url:`/v1/bots/${id}/actions`,cookies,payload:{action:'stop',idempotencyKey:randomUUID()}});assert.equal(stop.statusCode,200,stop.body);
   state.record={pid:100+states.size,created:200+states.size};state.login='failed';state.requested=false;await agent.execute(command);assert.equal(calls.length,after);await agent.health();assert.equal(agent.recovery.has(id),false);
   assert.equal((await db.query('SELECT desired_state FROM bots WHERE id=$1',[id]))[0].desired_state,'stopped');
  }
 }
});
