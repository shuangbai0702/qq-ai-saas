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
  const result=await installAutonomousRuntime(agent,'a'.repeat(64));
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
  const first=await installBilibiliRuntime(agent,'b'.repeat(64));assert.equal(first.pid,process.pid);assert.equal(agent.autonomy,coordinator);assert.equal(agent.host.children.get('media-fixture:protocol'),child);assert.equal(child.pid,originalPid);assert.equal(child.exitCode,null);
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
  const current=agent.bilibili;let activeTicks=0;current.tick=()=>{activeTicks++};
  // Current native tick already calls Bilibili; the existing maintenance hook
  // also covers older cached ticks. The executor mutex gates that fixed pair.
  await agent.tick();assert.equal(activeTicks,2,'releases must not grow the native-plus-maintenance tick chain');
  await new Promise(r=>setTimeout(r,150));await agent.host.logWrites.get('media-fixture');assert.match(await readFile(path.join(childRoot,'runtime.log'),'utf8'),/bili-pipe-alive/);
 }finally{await agent.bilibili.stop();await agent.autonomy.stop();await agent.host.stop('media-fixture','protocol');await db.close()}
});
