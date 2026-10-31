import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,readFile} from 'node:fs/promises';
import os from 'node:os';import path from 'node:path';
import {createApp} from '../apps/control-api/src/app.js';
import {NodeAgent} from '../apps/node-agent/src/agent.js';
import {observeOffline,evictionDue} from '../apps/node-agent/src/offline-policy.js';
import {issueTestInvite} from './helpers/invitations.js';

test('offline windows reset on reconnect, changed evidence, observation gaps and clock rollback',()=>{
 const grace={offline:300000,fault:900000,login:600000};let previous:any;
 for(let now=1000000;now<=1300000;now+=10000)previous=observeOffline(previous,{alive:true,ready:true,online:false},now,grace);
 assert.equal(evictionDue(previous,1300000),true);
 assert.equal(observeOffline(previous,{alive:true,ready:true,online:true},1300000,grace),undefined);
 assert.equal(observeOffline(previous,{alive:false,ready:false},1300000,grace)?.since,1300000);
 assert.equal(observeOffline(previous,{alive:true,ready:true},1400000,grace)?.observations,1);
 assert.equal(observeOffline(previous,{alive:true,ready:true},900000,grace)?.observations,1);
 assert.equal(observeOffline(undefined,{alive:true,ready:true,loginState:'waiting_scan'},1000000,grace)?.graceMs,180000);
});

test('startup 120s, QQ waiting 180s, offline 300s caps survive retry phases and persisted observation gaps',()=>{
 const grace={startup:120000,login:180000,offline:300000,fault:300000};const ai={alive:true,ready:false};
 let p=observeOffline(undefined,{alive:true,ready:true,online:true},1000000,grace,{ai,requestedAt:900000})!;
 assert.equal(p.reason,'runtime-starting');assert.equal(p.since,900000);assert.equal(p.graceMs,120000);
 p=observeOffline(p,{alive:true,ready:true,loginState:'waiting_scan'},1001000,grace)!;assert.equal(p.graceMs,180000);
 p=observeOffline(p,{alive:true,ready:true,online:true},1020001,grace,{ai})!;assert.equal(p.since,900000,'return to startup cannot reset its deadline');
 p=observeOffline(p,{alive:true,ready:true,online:true},1020002,grace,{ai})!;p=observeOffline(p,{alive:true,ready:true,online:true},1020003,grace,{ai})!;assert.ok(evictionDue(p,1020003));
 let fault=observeOffline(undefined,{alive:false,ready:false},1000000,grace,{onlineSince:900000})!;assert.equal(fault.graceMs,300000);
 assert.equal(observeOffline(undefined,{alive:true,ready:true,online:true},1000000,grace,{ai,onlineSince:900000,requestedAt:1})!.graceMs,300000,'an old start timestamp cannot prematurely evict a previously healthy account');
 fault=observeOffline(fault,{alive:false,ready:false},1400000,grace,{onlineSince:900000})!;assert.equal(fault.since,1000000);assert.equal(fault.observations,1);
 assert.equal(observeOffline(undefined,{alive:true,ready:true,online:true},1400000,grace),undefined);
});

async function fixture(t:any){
 const root=await mkdtemp(path.join(os.tmpdir(),'fish-offline-'));const current=await createApp({root,agent:false});t.after(()=>current.app.close());
 const {app,db,agent}=current;const registration=await app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(db,3),email:'offline@example.test',name:'离线测试',password:'offline-test-123'}});
 const cookies={fish_session:registration.cookies[0].value};
 const call=(method:any,url:string,payload?:any)=>app.inject({method,url,payload,cookies});
 const created=await call('POST','/v1/bots',{name:'离线保留',protocol:'mock',runtimeMode:'dedicated',apiKey:'preserved-model-key'});assert.equal(created.statusCode,201,created.body);const id=created.json().id;
 await db.query("UPDATE bots SET desired_state='running',generation=7 WHERE id=$1",[id]);
 agent.host.beginInspection=async()=>{};agent.host.endInspection=()=>{};
 agent.offlineGrace={offline:30000,fault:30000,login:30000};
 const state={online:false,alive:true,ai:true,stops:[] as string[]};
 agent.ai.probe=async()=>({alive:state.ai,ready:state.ai});
 agent.ai.stop=async botId=>{state.ai=false;state.stops.push('ai:'+botId)};
 agent.ai.start=async()=>{state.ai=true};
 agent.protocols.mock.getHealth=async()=>({alive:state.alive,ready:state.alive,online:state.online,loginState:state.online?'online':'failed'});
 agent.protocols.mock.queryIdentity=async()=> '90123456';
 agent.protocols.mock.stop=async botId=>{state.alive=false;state.stops.push('protocol:'+botId)};
 const age=async()=>{await agent.health();await db.query("UPDATE bots SET health=jsonb_set(health,'{offline}', $2::jsonb) WHERE id=$1",[id,JSON.stringify({since:Date.now()-31000,lastObservedAt:Date.now(),observations:2,reason:'account-offline',graceMs:30000})]);};
 const claim=async()=>{await age();await agent.health();return(await db.query("SELECT * FROM commands WHERE bot_id=$1 AND kind='evict-offline'",[id]))[0]};
 return {...current,root,id,call,state,age,claim};
}

test('durable eviction releases handles only; no restart or data/key/binding deletion',async t=>{
 const f=await fixture(t);const {db,agent,id,state}=f;
 const directory=path.join(f.root,'bots',id);await mkdir(path.join(directory,'qq'),{recursive:true});
 for(const name of ['qq/identity.json','qq/native-session.bin','behavior.sqlite'])await writeFile(path.join(directory,name),'preserve:'+name);
 await db.query("INSERT INTO qq_bindings(bot_id,qq_uin,binding_kind) VALUES($1,'90123456','mock')",[id]);
 const before=await db.query('SELECT ciphertext FROM secrets WHERE bot_id=$1',[id]);const revisions=await db.query('SELECT * FROM bot_config_revisions WHERE bot_id=$1',[id]);
 const command=await f.claim();assert.ok(command);assert.equal(state.stops.length,0,'claim must be durable before stopping');
 await agent.execute(command);let [bot]=await db.query('SELECT * FROM bots WHERE id=$1',[id]);assert.equal(bot.desired_state,'offline');assert.equal(bot.observed_state,'offline');assert.equal(bot.health.protocol.alive,false);assert.ok(bot.health.offline.evictedAt);
 assert.deepEqual(await db.query('SELECT ciphertext FROM secrets WHERE bot_id=$1',[id]),before);assert.deepEqual(await db.query('SELECT * FROM bot_config_revisions WHERE bot_id=$1',[id]),revisions);assert.equal((await db.query('SELECT * FROM qq_bindings WHERE bot_id=$1',[id])).length,1);
 for(const name of ['qq/identity.json','qq/native-session.bin','behavior.sqlite'])assert.equal(await readFile(path.join(directory,name),'utf8'),'preserve:'+name);
 const restarted=new NodeAgent(db,f.root,agent.key);restarted.host.beginInspection=async()=>{};restarted.host.endInspection=()=>{};await restarted.health();assert.equal((await db.query("SELECT * FROM commands WHERE kind='restart'")).length,0);
 await agent.health();assert.equal(state.stops.length,2);
});

test('short disconnect, live login and reconnect prevent eviction',async t=>{
 const f=await fixture(t);await f.agent.health();assert.equal((await f.db.query('SELECT * FROM commands')).length,0);
 await f.age();f.state.online=true;await f.agent.health();assert.equal((await f.db.query('SELECT health FROM bots WHERE id=$1',[f.id]))[0].health.offline,undefined);
 f.state.online=false;await f.age();await f.call('POST',`/v1/bots/${f.id}/login-sessions`,{});await f.agent.health();assert.equal((await f.db.query("SELECT * FROM commands WHERE kind='evict-offline'")).length,0);
});

test('native reconnect after durable claim supersedes eviction without stopping either handle',async t=>{
 const f=await fixture(t);const command=await f.claim();f.state.online=true;await f.agent.execute(command);
 assert.deepEqual(f.state.stops,[]);const [bot]=await f.db.query('SELECT * FROM bots WHERE id=$1',[f.id]);assert.equal(bot.desired_state,'running');assert.equal(bot.health.offline,undefined);
 assert.equal((await f.db.query('SELECT status FROM commands WHERE id=$1',[command.id]))[0].status,'superseded');
 const [resume]=await f.db.query("SELECT * FROM commands WHERE kind='start'");assert.equal(resume.generation,command.generation);
});

test('new login supersedes claimed eviction before destruction and recreates AI',async t=>{
 const f=await fixture(t);const command=await f.claim();const response=await f.call('POST',`/v1/bots/${f.id}/login-sessions`,{});assert.equal(response.statusCode,200);
 await f.agent.execute(command);assert.equal(f.state.stops.length,0);assert.equal((await f.db.query('SELECT status FROM commands WHERE id=$1',[command.id]))[0].status,'superseded');
 const [login]=await f.db.query("SELECT * FROM commands WHERE kind='login'");f.state.ai=false;let starts=0;f.agent.protocols.mock.beginLogin=async()=>{starts++};await f.agent.execute(login);assert.equal(f.state.ai,true);assert.equal(starts,1);
});

test('login during destruction waits for owner lock and starts after cleanup',async t=>{
 const f=await fixture(t);const command=await f.claim();let entered!:()=>void,release!:()=>void;const barrier=new Promise<void>(resolve=>entered=resolve);const finish=new Promise<void>(resolve=>release=resolve);
 f.agent.protocols.mock.stop=async()=>{entered();await finish};
 const evicting=f.agent.execute(command);await barrier;
 const loggingIn=f.call('POST',`/v1/bots/${f.id}/login-sessions`,{});release();await evicting;const response=await loggingIn;assert.equal(response.statusCode,200);
 const [bot]=await f.db.query('SELECT * FROM bots WHERE id=$1',[f.id]);assert.equal(bot.desired_state,'running');assert.ok(bot.generation>command.generation);
});

test('shared eviction detaches only its member using the owning transaction',async t=>{
 const f=await fixture(t);await f.db.query("UPDATE bots SET runtime_mode='shared' WHERE id=$1",[f.id]);f.agent.shared.probe=async()=>({alive:true,ready:true});
 let detached='';f.agent.shared.stop=async(id,permanent,tx)=>{assert.equal(permanent,false);assert.ok(tx);detached=id};
 const command=await f.claim();await f.agent.execute(command);assert.equal(detached,f.id);
});

test('expired or canceled QR releases AI as well and never enters automatic recovery',async t=>{
 const f=await fixture(t);const r=await f.call('POST',`/v1/bots/${f.id}/login-sessions`,{});const sid=r.json().id;
 const [bot]=await f.db.query('SELECT generation FROM bots WHERE id=$1',[f.id]);
 await f.db.query("UPDATE login_sessions SET state='expired' WHERE id=$1",[sid]);
 await f.db.query("INSERT INTO commands(id,bot_id,idempotency_key,generation,kind) VALUES('cancel-expired',$1,$2,$3,'cancel-login')",[f.id,'cancel-'+sid,bot.generation]);
 f.agent.protocols.mock.cancelLogin=async ctx=>f.agent.protocols.mock.stop(ctx.botId);
 const [cancel]=await f.db.query("SELECT * FROM commands WHERE id='cancel-expired'");await f.agent.execute(cancel);
 const [evict]=await f.db.query("SELECT * FROM commands WHERE kind='evict-offline'");await f.agent.execute(evict);await f.agent.health();
 assert.equal(f.state.ai,false);assert.equal(f.state.alive,false);assert.equal((await f.db.query('SELECT observed_state FROM bots WHERE id=$1',[f.id]))[0].observed_state,'offline');
 assert.equal((await f.db.query("SELECT * FROM commands WHERE kind='restart'")).length,0);
});
