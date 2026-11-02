import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import {mkdtemp,mkdir,writeFile,readFile} from 'node:fs/promises';
import {openDatabase} from '../packages/database/index.js';
import {NodeAgent} from '../apps/node-agent/src/agent.js';
import {installAutonomousRuntime} from '../apps/node-agent/src/maintenance.js';

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
  assert.equal((await agent.host.inspect('maintenance-fixture','protocol')).pid,child.pid);
 }finally{await agent.autonomy.stop();await agent.host.stop('maintenance-fixture','protocol');await db.close()}
});
