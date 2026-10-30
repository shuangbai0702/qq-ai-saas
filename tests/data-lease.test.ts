import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp,writeFile,readFile,access,mkdir} from 'node:fs/promises';import path from 'node:path';import {acquireLease} from '../packages/database/lease.js';
import {openDatabase} from '../packages/database/index.js';import {createApp} from '../apps/control-api/src/app.js';import {NodeAgent} from '../apps/node-agent/src/agent.js';
test('PGlite lease is acquired before opening files, rejects duplicate owners and releases on close',async()=>{
 const root=await mkdtemp(path.resolve('.local/database-lease-'));const db=await openDatabase(root);
 try{await db.query('CREATE TABLE lease_probe(value TEXT)');await db.query("INSERT INTO lease_probe VALUES('durable')");await assert.rejects(()=>openDatabase(root),e=>(e as any).code==='ELOCKED')}finally{await db.close();await db.close()}
 const reopened=await openDatabase(root);try{assert.equal((await reopened.query('SELECT value FROM lease_probe'))[0].value,'durable')}finally{await reopened.close()}
});
test('shutdown releases the agent lease and cleans owned process records even if its database fails',async()=>{
 const root=await mkdtemp(path.resolve('.local/failed-shutdown-'));const current=await createApp({root,agent:false});const calls:string[]=[];
 try{await current.agent.begin();await mkdir(path.join(root,'bots','group-owned'),{recursive:true});current.agent.host.stop=async(id,kind)=>{calls.push(`${id}:${kind}`)};current.agent.db={...current.db,query:async()=>{throw new Error('injected database failure')}};
  await assert.rejects(()=>current.agent.shutdown(),/injected database failure/);assert.deepEqual(calls,['group-owned:ai','group-owned:protocol']);await assert.rejects(()=>access(path.join(root,'agent.lock')));const release=await acquireLease(root,'agent');await release();await current.agent.shutdown();
 }finally{await current.app.close()}
});
test('agent namespace lock rejects duplicates while a stale recycled PID marker cannot block recovery',async()=>{
 const root=await mkdtemp(path.resolve('.local/agent-lease-'));const current=await createApp({root,agent:false});let active=false;
 try{
  await writeFile(path.join(root,'agent.lock'),String(process.pid));await current.agent.begin();active=true;assert.equal(await readFile(path.join(root,'agent.lock'),'utf8'),String(process.pid));
  const other=new NodeAgent(current.db,root,Buffer.alloc(32));await assert.rejects(()=>other.begin(),e=>(e as any).code==='ELOCKED');assert.equal(other.timer,undefined);await current.agent.shutdown();active=false;await current.agent.shutdown();await assert.rejects(()=>access(path.join(root,'agent.lock')));
 }finally{if(active)await current.agent.shutdown();await current.app.close()}
});
