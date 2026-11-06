import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {openDatabase} from '../packages/database/index.js';
import {NodeAgent} from '../apps/node-agent/src/agent.js';
import {installForegroundContextRuntime} from '../apps/node-agent/src/maintenance.js';

test('foreground transport maintenance preserves real child, coordinator and all other runtime owners',{timeout:30000},async()=>{
 await mkdir('.local',{recursive:true});
 const root=await mkdtemp(path.resolve('.local/foreground-maintenance-'));
 const db=await openDatabase(root),agent=new NodeAgent(db,root,Buffer.alloc(32));
 const dir=path.join(root,'bots','fixture');await mkdir(dir,{recursive:true});
 const script=path.join(dir,'fixture.cjs');await writeFile(script,'setInterval(()=>{},50)');
 try{
  await agent.host.start('fixture','protocol',process.execPath,[script],dir,process.env);
  const child=agent.host.children.get('fixture:protocol')!,pid=child.pid;
  const owners={autonomy:agent.autonomy,favorites:agent.favorites,bilibili:agent.bilibili,tick:agent.tick,shutdown:agent.shutdown,start:agent.host.start};
  const result=await installForegroundContextRuntime(agent,'a'.repeat(64));
  assert.equal(result.pid,process.pid);assert.equal(child.exitCode,null);
  assert.equal(agent.host.children.get('fixture:protocol'),child);assert.equal(child.pid,pid);
  for(const [key,value] of Object.entries(owners))assert.equal(key==='start'?agent.host.start:(agent as any)[key],value,key);
  assert.equal((await installForegroundContextRuntime(agent,'a'.repeat(64))).unchanged,true);
  await assert.rejects(()=>installForegroundContextRuntime(agent,'invalid'),/Invalid foreground release/);
  assert.equal((await agent.host.inspect('fixture','protocol'))?.pid,pid);
 }finally{await agent.host.stop('fixture','protocol');await db.close()}
});
