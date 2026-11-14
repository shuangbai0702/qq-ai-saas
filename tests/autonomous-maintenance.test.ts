import {SharedAstrBotAdapter} from '../adapters/astrbot/shared.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import {mkdtemp,mkdir,writeFile,readFile} from 'node:fs/promises';
import {openDatabase} from '../packages/database/index.js';
import {NodeAgent} from '../apps/node-agent/src/agent.js';
import {installAutonomousRuntime,installBilibiliRuntime} from '../apps/node-agent/src/maintenance.js';

test('maintenance retains real child objects, PID and log pipes; reinstall is idempotent',{timeout:30000},async()=>{
 const root=await mkdtemp(path.resolve('.local/autonomous-maintenance-')),db=await openDatabase(root),agent=new NodeAgent(db,root,Buffer.alloc(32));
 const childRoot=path.join(root,'bots','maintenance-fixture');await mkdir(childRoot,{recursive:true});
 const script=path.join(childRoot,'fixture.cjs');await writeFile(script,"setInterval(()=>console.log('owned-pipe-alive'),50)");
 try{
  const cachedPrototype=Object.create(Object.getPrototypeOf(agent));cachedPrototype.health=async()=>{throw Error('stale cached health')};Object.setPrototypeOf(agent,cachedPrototype);
  await agent.host.start('maintenance-fixture','protocol',process.execPath,[script],childRoot,process.env);
  const child=agent.host.children.get('maintenance-fixture:protocol')!;assert.ok(child.pid);const record=await agent.host.record('maintenance-fixture','protocol');assert.ok(record.created);
  agent.shared.request=async()=>{throw Error('stale cached IPC')};
  const canonicalShutdown=agent.shutdown;const result=await installAutonomousRuntime(agent,'a'.repeat(64));
  assert.equal(agent.shared.request,SharedAstrBotAdapter.prototype.request,'maintenance retained stale IPC');
  assert.equal(agent.shutdown,canonicalShutdown,'maintenance must retain canonical shutdown ownership');
  assert.equal(cachedPrototype.health,NodeAgent.prototype.health,'maintenance retained the old cached offline-policy closure');
  assert.equal(result.pid,process.pid);assert.equal(agent.host.children.get('maintenance-fixture:protocol'),child);assert.equal(child.exitCode,null);
  const coordinator=agent.autonomy;assert.equal((await installAutonomousRuntime(agent,'a'.repeat(64))).unchanged,true);assert.equal(agent.autonomy,coordinator);
  async function logBeyond(length:number){
   const deadline=Date.now()+5000;
   while(Date.now()<deadline){
    assert.equal(child.exitCode,null,'owned child exited while awaiting its stdout');
    await agent.host.logWrites.get('maintenance-fixture');
    const text=await readFile(path.join(childRoot,'runtime.log'),'utf8').catch(error=>{if(error.code==='ENOENT')return '';throw error});
    if(text.length>length)return text.length;
    await new Promise(resolve=>setTimeout(resolve,20));
   }
   assert.fail('existing stdout pipe stopped draining');
  }
  const initial=await logBeyond(0);await logBeyond(initial);
  await coordinator.stop();assert.equal(coordinator.closed,true);const resumed=await installAutonomousRuntime(agent,'a'.repeat(64));assert.notEqual(resumed.unchanged,true);assert.notEqual(agent.autonomy,coordinator);assert.equal(agent.autonomy.closed,false);
  assert.equal((await agent.host.inspect('maintenance-fixture','protocol')).pid,child.pid);
 }finally{await agent.autonomy.stop();await agent.host.stop('maintenance-fixture','protocol');await db.close()}
});


test('Bilibili maintenance keeps current coordinator and child owners, and rolls back a failed replacement',{timeout:30000},async()=>{
 const root=await mkdtemp(path.resolve('.local/bilibili-maintenance-')),db=await openDatabase(root),agent=new NodeAgent(db,root,Buffer.alloc(32));
 const childRoot=path.join(root,'bots','media-fixture');await mkdir(childRoot,{recursive:true});const script=path.join(childRoot,'fixture.cjs');await writeFile(script,"setInterval(()=>console.log('bili-pipe-alive'),50)");
 try{
  await agent.host.start('media-fixture','protocol',process.execPath,[script],childRoot,process.env);
  const child=agent.host.children.get('media-fixture:protocol')!,coordinator=agent.autonomy,originalPid=child.pid;
  const canonicalShutdown=agent.shutdown,canonicalTick=agent.tick;const first=await installBilibiliRuntime(agent,'b'.repeat(64));assert.equal(agent.shutdown,canonicalShutdown);assert.equal(agent.tick,canonicalTick);assert.equal(first.pid,process.pid);assert.equal(agent.autonomy,coordinator);assert.equal(agent.host.children.get('media-fixture:protocol'),child);assert.equal(child.pid,originalPid);assert.equal(child.exitCode,null);
  const installed=agent.bilibili;assert.equal((await installBilibiliRuntime(agent,'b'.repeat(64))).unchanged,true);assert.equal(agent.bilibili,installed);
  const firstTick=agent.tick,firstShutdown=agent.shutdown;
  const query=db.query.bind(db);db.query=async(sql:any,args?:any)=>{if(String(sql).includes('tool_service_cooldowns'))throw Error('fixture database interruption');return query(sql,args)};
  await assert.rejects(()=>installBilibiliRuntime(agent,'c'.repeat(64)),/fixture database interruption/);db.query=query;
  assert.equal(agent.bilibili,installed);assert.equal(installed.stopped,false);assert.equal(agent.autonomy,coordinator);assert.equal(child.exitCode,null);
  for(const revision of ['d','e','f']){
   const previous=agent.bilibili;await installBilibiliRuntime(agent,revision.repeat(64));
   assert.equal(previous.stopped,true);assert.notEqual(agent.bilibili,previous);
   assert.equal(agent.tick,firstTick,'reinstall stacked another tick wrapper');
   assert.equal(agent.shutdown,firstShutdown,'reinstall stacked another shutdown wrapper');
  }
  const heldPrevious=agent.bilibili;await heldPrevious.stop();
  await installBilibiliRuntime(agent,'1'.repeat(64),{start:false});
  const held=agent.bilibili;assert.equal(heldPrevious.stopped,true);assert.equal(held.stopped,true);
  assert.equal((await installBilibiliRuntime(agent,'1'.repeat(64),{start:false})).unchanged,true);
  await held.tick();assert.equal(held.busy,undefined,'held replacement must not claim a job');
  db.query=async(sql:any,args?:any)=>{if(String(sql).includes('tool_service_cooldowns'))throw Error('held database interruption');return query(sql,args)};
  await assert.rejects(()=>installBilibiliRuntime(agent,'2'.repeat(64),{start:false}),/held database interruption/);db.query=query;
  assert.equal(agent.bilibili,held);assert.equal(held.stopped,true,'rollback must preserve the maintenance fence');
  assert.equal(agent.autonomy,coordinator);assert.equal(agent.host.children.get('media-fixture:protocol'),child);
  held.stopped=false;
  const current=agent.bilibili;let activeTicks=0;current.tick=()=>{activeTicks++};
  await agent.tick();assert.equal(activeTicks,1,'canonical tick owns exactly one current Bilibili admission');
  await new Promise(r=>setTimeout(r,150));await agent.host.logWrites.get('media-fixture');assert.match(await readFile(path.join(childRoot,'runtime.log'),'utf8'),/bili-pipe-alive/);
 }finally{await agent.bilibili.stop();await agent.autonomy.stop();await agent.host.stop('media-fixture','protocol');await db.close()}
});


test('failed autonomous maintenance restores the running coordinator without replacing child owners',async()=>{
 const root=await mkdtemp(path.resolve('.local/autonomous-rollback-')),db=await openDatabase(root),agent=new NodeAgent(db,root,Buffer.alloc(32));
 const child={pid:123,exitCode:null} as any;agent.host.children.set('owner:protocol',child);
 const prior=agent.autonomy;prior.start();
 const stop=prior.stop.bind(prior),get=agent.host.children.get.bind(agent.host.children);
 prior.stop=async()=>{await stop();agent.host.children.get=()=>undefined};
 try{
  await assert.rejects(()=>installAutonomousRuntime(agent,'e'.repeat(64)),/child owner changed/);
  assert.equal(agent.autonomy,prior);assert.equal(prior.closed,false);assert.ok(prior.timer&&prior.heartbeatTimer);
  agent.host.children.get=get;assert.equal(agent.host.children.get('owner:protocol'),child);
 }finally{agent.host.children.get=get;prior.stop=stop;await prior.stop();agent.host.children.clear();await db.close()}
});


test('held autonomous installation preserves real child ownership and never starts a coordinator request before resume',async()=>{
 const root=await mkdtemp(path.resolve('.local/autonomous-held-')),db=await openDatabase(root),agent=new NodeAgent(db,root,Buffer.alloc(32));
 const childRoot=path.join(root,'bots','held-fixture');await mkdir(childRoot,{recursive:true});const script=path.join(childRoot,'fixture.cjs');await writeFile(script,"setInterval(()=>console.log('held-owner-alive'),30)");
 try{
  await agent.host.start('held-fixture','protocol',process.execPath,[script],childRoot,process.env);
  const child=agent.host.children.get('held-fixture:protocol')!,previous=agent.autonomy,query=db.query.bind(db);let autonomyQueries=0;
  db.query=async(sql:any,args?:any)=>{if(String(sql).includes('autonomy_settings'))autonomyQueries++;return query(sql,args)};
  const oldTick=agent.tick,oldShutdown=agent.shutdown;
  await installAutonomousRuntime(agent,'f'.repeat(64),{start:false});
  assert.notEqual(agent.autonomy,previous);assert.equal(previous.closed,true);assert.equal(agent.autonomy.closed,true);assert.equal(agent.autonomy.timer,undefined);assert.equal(agent.autonomy.heartbeatTimer,undefined);
  assert.equal(autonomyQueries,0,'held installation must not run the immediate autonomous planning tick');
  assert.equal(agent.tick,oldTick);assert.equal(agent.shutdown,oldShutdown);assert.equal(agent.host.children.get('held-fixture:protocol'),child);assert.equal(child.exitCode,null);
  agent.autonomy.closed=false;agent.autonomy.start();
  const until=Date.now()+3000;while(!autonomyQueries&&Date.now()<until)await new Promise(r=>setTimeout(r,10));assert.ok(autonomyQueries>0,'explicit resume starts the current coordinator');
  assert.equal(agent.host.children.get('held-fixture:protocol'),child);assert.equal((await agent.host.inspect('held-fixture','protocol')).pid,child.pid);
 }finally{await agent.autonomy.stop();await agent.host.stop('held-fixture','protocol');await db.close()}
});
