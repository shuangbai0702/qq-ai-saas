// Real control database/lifecycle and adapter provisioning; controlled IPC/QQ
// handles let membership and drain failures be asserted without paid services.
import test from 'node:test';import assert from 'node:assert/strict';
import {mkdtemp} from 'node:fs/promises';import path from 'node:path';import {execFile} from 'node:child_process';
import {createApp} from '../apps/control-api/src/app.js';import {issueTestInvite} from './helpers/invitations.js';

test('only online shared accounts fill eight-member groups; unload/restart preserve one per-bot owner and data',{timeout:60000},async t=>{
 const root=await mkdtemp(path.resolve('.local/shared-online-'));const {app,db,agent}=await createApp({root,agent:false});t.after(()=>app.close());
 const states=new Map<string,{alive:boolean;online:boolean}>(),workers=new Map<string,{pid:number;members:Map<string,any>}>();let nextPid=100,failDrain='';const attached:any[]=[];
 agent.host.beginInspection=async()=>{};agent.host.endInspection=()=>{};
 agent.host.inspect=async(id:string)=>workers.has(id)?{pid:workers.get(id)!.pid,created:1,rssBytes:1024} as any:null;
 agent.host.start=async(id:string)=>{assert.ok(!workers.has(id),'one worker instance per group');workers.set(id,{pid:++nextPid,members:new Map()});return {} as any};
 agent.host.stop=async(id:string)=>{workers.delete(id)};
 agent.shared.request=async(group:any,route:string,method='GET',body?:any)=>{
  const w=workers.get(group.id);assert.ok(w,'IPC targets the owned worker');
  if(route==='/health')return{ready:true,generation:group.generation,members:Object.fromEntries(w.members),metrics:{}};
  const id=route.split('/')[2].split('?')[0];
  if(method==='DELETE'){if(id===failDrain)throw new Error('drain not confirmed');w.members.delete(id);return{removed:id}}
  assert.equal(method,'PUT');assert.ok(w.members.has(id)||w.members.size<8);w.members.set(id,body);attached.push(body);return{};
 };
 agent.protocols.mock.getHealth=async ctx=>{const s=states.get(ctx.botId)||{alive:false,online:false};return{alive:s.alive,ready:s.alive,online:s.online,loginState:s.online?'online':'waiting_scan'}};
 agent.protocols.mock.queryIdentity=async ctx=>states.get(ctx.botId)?.online?ctx.expectedUin:null;
 agent.protocols.mock.beginLogin=async ctx=>{states.set(ctx.botId,{alive:true,online:false})};
 agent.protocols.mock.stop=async id=>{states.set(id,{alive:false,online:false})};
 const ids:string[]=[],cookies:any[]=[];
 for(let tenant=0;tenant<2;tenant++){
  const registered=await app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(db,5),email:`shared-online-${tenant}@test.invalid`,name:'tenant'+tenant,password:'shared-online-123'}});cookies.push({fish_session:registered.cookies[0].value});
  for(let n=0;n<5;n++){
   const i=ids.length,r=await app.inject({method:'POST',url:'/v1/bots',cookies:cookies[tenant],payload:{name:'member'+i,protocol:'mock',expectedUin:String(91000100+i),apiKey:'key-'+i}});assert.equal(r.statusCode,201,r.body);ids.push(r.json().id);
   const started=await app.inject({method:'POST',url:`/v1/bots/${ids[i]}/actions`,cookies:cookies[tenant],payload:{action:'start',idempotencyKey:'start'}});assert.equal(started.statusCode,200,started.body);
  }
 }
 const executePending=async()=>{for(const c of await db.query("SELECT * FROM commands WHERE status='pending' ORDER BY created_at,id"))await agent.execute(c)};
 await executePending();await agent.health();assert.equal((await db.query('SELECT * FROM group_members')).length,0);assert.equal(workers.size,0,'waiting-scan accounts allocate no SDK worker');
 // Upgrade fixture: old versions pinned stopped bots to groups, including a
 // live SDK worker, while health={} kept those bots out of normal selection.
 for(const id of ids.slice(1))await agent.shared.assign(id);
 await db.query("UPDATE group_members SET desired_state='stopped'");
 for(const id of ids.slice(1))await db.query("UPDATE bots SET desired_state='stopped',health='{}' WHERE id=$1",[id]);
 for(const row of await db.query('SELECT * FROM group_members')){if(!workers.has(row.group_id))workers.set(row.group_id,{pid:++nextPid,members:new Map()});workers.get(row.group_id)!.members.set(row.bot_id,{generation:row.generation})}
 await agent.reconcileSharedMembers();assert.equal((await db.query('SELECT * FROM group_members')).length,0);assert.equal(workers.size,0);assert.ok(ids.every(id=>states.get(id)!.alive),'reconciliation preserves protocol handles');
 for(const id of ids.slice(1))await db.query("UPDATE bots SET desired_state='running' WHERE id=$1",[id]);
 for(const id of ids.slice(1))states.set(id,{alive:true,online:true});
 await agent.health();await executePending();await agent.health();
 const sizes=async()=>(await db.query('SELECT group_id,count(*)::int AS n FROM group_members GROUP BY group_id')).map(r=>r.n).sort((a,b)=>a-b);
 assert.deepEqual(await sizes(),[1,8]);assert.equal(workers.size,2);assert.equal(attached.length,9);
 const first=(await db.query('SELECT group_id FROM group_members GROUP BY group_id ORDER BY count(*) DESC LIMIT 1'))[0].group_id;
 const victim=(await db.query('SELECT bot_id FROM group_members WHERE group_id=$1',[first]))[0].bot_id;
 const file=path.join(root,'bots',victim,'behavior.sqlite');
 const durable=(mode:string)=>new Promise<string>((resolve,reject)=>execFile(path.resolve(`references/astrbot/.venv/${process.platform==='win32'?'Scripts/python.exe':'bin/python'}`),['-B','tests/helpers/shared_member_persistence.py',file,victim,mode],{windowsHide:true},(error,stdout,stderr)=>error?reject(new Error(stderr)):resolve(stdout.trim())));
 const evidence=await durable('seed');
 // A failed unload cannot release its slot or create a second owner elsewhere.
 states.set(victim,{alive:true,online:false});failDrain=victim;await agent.health();
 assert.equal((await db.query('SELECT group_id FROM bots WHERE id=$1',[victim]))[0].group_id,first);assert.ok(workers.get(first)!.members.has(victim));
 failDrain='';await agent.health();assert.equal((await db.query('SELECT group_id FROM bots WHERE id=$1',[victim]))[0].group_id,null);assert.ok(!workers.get(first)!.members.has(victim));assert.deepEqual(await sizes(),[1,7]);
 states.set(ids[0],{alive:true,online:true});await agent.health();await executePending();await agent.health();assert.deepEqual(await sizes(),[1,8]);assert.equal((await db.query('SELECT group_id FROM bots WHERE id=$1',[ids[0]]))[0].group_id,first,'next online account reuses the released earliest slot');
 states.set(victim,{alive:true,online:true});agent.recovery.delete(victim);await agent.health();await executePending();await agent.health();assert.deepEqual(await sizes(),[2,8]);
 assert.equal(await durable('inspect'),evidence,'unknown send stays unknown, redelivery remains duplicate and old media Journal restores exactly');
 for(const body of attached){const [b]=await db.query('SELECT tenant_id FROM bots WHERE id=$1',[body.botId]);assert.equal(body.tenantId,b.tenant_id);assert.equal(body.runtimeRoot,path.join(root,'bots',body.botId));assert.equal(body.key,'key-'+ids.indexOf(body.botId));}
 assert.equal((await app.inject({method:'GET',url:`/v1/bots/${ids[0]}`,cookies:cookies[1]})).statusCode,404);
 // A crashed group reloads only the freshly checked command's member. Its
 // other desired-running rows are not evidence to bulk load any SDK profile.
 workers.delete(first);agent.recovery.clear();await agent.health();
 const pending=await db.query("SELECT * FROM commands WHERE status='pending' ORDER BY created_at,id");assert.equal(pending.length,8);await agent.execute(pending[0]);assert.equal(workers.get(first)!.members.size,1);
 for(const c of pending.slice(1))await agent.execute(c);await agent.health();assert.equal(workers.get(first)!.members.size,8);assert.deepEqual(await sizes(),[2,8]);
 for(const id of ids)assert.equal([...workers.values()].filter(w=>w.members.has(id)).length,1,'exactly one SDK owner per online bot');
 assert.equal(await durable('inspect'),evidence);
 // Going offline while group startup/provision awaits must fail the final
 // eligibility fence, release the staging slot and leave no SDK owner.
 await agent.shared.stop(victim);const ctx=await agent.context((await db.query('SELECT * FROM bots WHERE id=$1',[victim]))[0]);
 const provision=agent.ai.provision.bind(agent.ai);agent.ai.provision=async c=>{await provision(c);states.set(c.botId,{alive:true,online:false})};
 await assert.rejects(agent.shared.start(ctx),error=>(error as any).superseded===true);
 assert.equal((await db.query('SELECT group_id FROM bots WHERE id=$1',[victim]))[0].group_id,null);assert.ok([...workers.values()].every(w=>!w.members.has(victim)));
 assert.equal(await durable('inspect'),evidence);
});
