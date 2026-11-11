import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm,mkdir,writeFile,stat} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {NodeAgent} from '../apps/node-agent/src/agent.js';
import {ProcessHost} from '../apps/node-agent/src/process-host.js';
import {installFavoriteRuntime} from '../apps/node-agent/src/maintenance.js';

test('slow lifecycle leaves tick, other bot and health runnable; same bot stays serialized',async()=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'fishbot-operation-'));
 let release!:()=>void;
 const gate=new Promise<void>(resolve=>release=resolve);
 const started:string[]=[];let health=0;
 const commands=[{id:'slow',bot_id:'a',runtime_mode:'shared'},{id:'same',bot_id:'a',runtime_mode:'shared'},{id:'other',bot_id:'b',runtime_mode:'dedicated'}];
 const db:any={query:async(sql:string)=>sql.includes('FROM commands c')?commands:[]};
 const agent=new NodeAgent(db,root,Buffer.alloc(32));
 agent.bilibili.tick=()=>{};agent.favorites.tick=()=>{};
 agent.execute=async(command:any)=>{started.push(command.id);if(command.id==='slow')await gate};
 agent.health=async()=>{health++};
 try{
  await agent.tick();await Promise.resolve();
  assert.deepEqual(started,['slow','other']);assert.equal(health,1);
  await agent.tick();assert.equal(started.filter(id=>id==='slow').length,1);
  release();await Promise.allSettled([...(agent as any).operations.values()]);
  assert.equal((agent as any).operations.size,0);
 }finally{release();await rm(root,{recursive:true,force:true})}
});

test('admission is bounded and a health-owned bot cannot start a lifecycle operation',async()=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'fishbot-operation-'));
 const agent:any=new NodeAgent({} as any,root,Buffer.alloc(32));
 let release!:()=>void;const gate=new Promise<void>(resolve=>release=resolve);
 try{
  agent.healthMembers.add('health');assert.equal(agent.launch('health',false,()=>gate),false);
  for(let i=0;i<4;i++)assert.equal(agent.launch(String(i),false,()=>gate),true);
  assert.equal(agent.launch('overflow',false,()=>gate),false);
  release();await Promise.allSettled([...agent.operations.values()]);
 }finally{release();await rm(root,{recursive:true,force:true})}
});

test('shared cleanup owns its lifecycle lane until acknowledged',async()=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'fishbot-operation-'));
 let release!:()=>void,entered!:()=>void;
 const gate=new Promise<void>(resolve=>release=resolve),start=new Promise<void>(resolve=>entered=resolve);
 const agent:any=new NodeAgent({query:async()=>[{id:'cleanup'}]} as any,root,Buffer.alloc(32));
 agent.shared.stop=async()=>{entered();await gate};
 try{
  const cleanup=agent.reconcileSharedMembers();await start;
  assert.equal(agent.launch('cleanup',false,async()=>{}),false);
  assert.equal(agent.launch('other-shared',true,async()=>{}),false);
  assert.equal(agent.launch('dedicated',false,async()=>{}),true);
  release();await cleanup;await Promise.allSettled([...agent.operations.values()]);
  assert.equal(agent.sharedOperation,false);assert.equal(agent.healthMembers.size,0);
 }finally{release();await rm(root,{recursive:true,force:true})}
});

test('process inspection snapshots belong only to the health async context',async()=>{
 const host:any=new ProcessHost('unused','unused');
 let entered!:()=>void,release!:()=>void;
 const started=new Promise<void>(resolve=>entered=resolve),gate=new Promise<void>(resolve=>release=resolve);
 host.record=async()=>({pid:1,created:2});host.helper=async()=>JSON.stringify({valid:true,source:'fresh'});
 const sweep=host.withInspection(async()=>{
  host.inspection=new Map([['bot:ai',{source:'snapshot'}]]);entered();await gate;
  assert.equal((await host.inspect('bot','ai')).source,'snapshot');
 });
 await started;
 assert.equal((await host.inspect('bot','ai')).source,'fresh');
 host.endInspection();release();await sweep;
});


test('shutdown settles every owner but retains its lease after an unconfirmed maintenance stop',async()=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'fishbot-shutdown-'));
 const calls:string[]=[];let release!:()=>void,entered!:()=>void;
 const gate=new Promise<void>(resolve=>release=resolve),started=new Promise<void>(resolve=>entered=resolve);
 const failure=new Error('favorite stop failed');
 const agent:any=new NodeAgent({query:async()=>[{id:'bot',protocol:'mock',runtime_mode:'dedicated'}]} as any,root,Buffer.alloc(32));
 await mkdir(path.join(root,'bots','bot'),{recursive:true});await writeFile(path.join(root,'agent.lock'),'fixture');
 agent.favorites.stop=async()=>{calls.push('favorites');entered();await gate;throw failure};
 agent.bilibili.stop=async()=>{calls.push('bilibili')};agent.autonomy.stop=async()=>{calls.push('autonomy')};
 agent.ai.stop=async()=>{calls.push('ai')};agent.protocols.mock.stop=async()=>{calls.push('protocol')};
 agent.host.stop=async(id:string,kind:string)=>{calls.push('fallback:'+kind)};
 agent.releaseLease=async()=>{calls.push('lease')};
 try{
  const first=agent.shutdown(),result=assert.rejects(first,error=>error===failure);await started;
  assert.equal(agent.closed,true);assert.equal(agent.shutdown(),first,'all callers await the same shutdown');
  assert.deepEqual(calls,['favorites']);release();await result;
  assert.deepEqual(calls,['favorites','bilibili','autonomy','ai','protocol','fallback:ai','fallback:protocol']);
  assert.equal((await stat(path.join(root,'agent.lock'))).isFile(),true);assert.equal(typeof agent.releaseLease,'function');
 }finally{release();await rm(root,{recursive:true,force:true})}
});

for(const boundary of ['commands','sessions'])test(`shutdown during ${boundary} query does not admit late health or operations`,async()=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'fishbot-shutdown-query-'));
 let release!:()=>void,entered!:()=>void;
 const gate=new Promise<void>(resolve=>release=resolve),started=new Promise<void>(resolve=>entered=resolve);
 const db:any={query:async(sql:string)=>{
  if(sql.includes(boundary==='commands'?'FROM commands c':'FROM login_sessions s')){entered();await gate}
  if(boundary==='commands'&&sql.includes('FROM commands c'))return [{id:'late-command',bot_id:'late-bot',runtime_mode:'dedicated'}];
  if(boundary==='sessions'&&sql.includes('FROM login_sessions s'))return [{id:'late-session',bot_id:'late-bot'}];
  return [];
 }};
 const agent:any=new NodeAgent(db,root,Buffer.alloc(32));let health=0,operations=0,released=0;
 agent.releaseLease=async()=>{released++};await writeFile(path.join(root,'agent.lock'),'fixture');
 agent.bilibili.tick=()=>{};agent.favorites.tick=()=>{};
 agent.favorites.stop=async()=>{};agent.bilibili.stop=async()=>{};agent.autonomy.stop=async()=>{};
 agent.health=async()=>{health++};agent.execute=async()=>{operations++};agent.pollLogin=async()=>{operations++};
 try{
  const tick=agent.tick();await started;const shutdown=agent.shutdown();release();await tick;await shutdown;
  assert.equal(health,0);assert.equal(operations,0);assert.equal(agent.healthTask,undefined);assert.equal(released,1);
  await assert.rejects(stat(path.join(root,'agent.lock')),{code:'ENOENT'});
 }finally{release();await rm(root,{recursive:true,force:true})}
});


test('maintenance preserves canonical shutdown and closes admission before waiting for a slow owner',async()=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'fishbot-maintenance-shutdown-'));
 const agent:any=new NodeAgent({query:async()=>[]} as any,root,Buffer.alloc(32));
 let bilibiliStopped=0,autonomyStopped=0,released=0,commands=0,queries=0;
 const canonical=agent.shutdown;let release!:()=>void,entered!:()=>void;
 const gate=new Promise<void>(resolve=>release=resolve),started=new Promise<void>(resolve=>entered=resolve);
 agent.favorites.stop=async()=>{};agent.bilibili.stop=async()=>{bilibiliStopped++};agent.autonomy.stop=async()=>{autonomyStopped++};
 agent.releaseLease=async()=>{released++};
 try{
  await installFavoriteRuntime(agent,'a'.repeat(64));
  assert.equal(agent.shutdown,canonical);
  agent.db.query=async()=>{queries++;return []};agent.execute=async()=>{commands++};
  agent.favorites.stop=async()=>{entered();await gate;throw new Error('maintenance fixture stop failed')};
  const shutdown=agent.shutdown(),settled=assert.rejects(shutdown,/maintenance fixture stop failed/);await started;
  assert.equal(agent.closed,true);await agent.tick();assert.equal(queries,0);assert.equal(commands,0);
  assert.equal(agent.launch('late',false,async()=>{commands++}),false);
  release();await settled;
  assert.equal(agent.closed,true);assert.equal(bilibiliStopped,1);assert.equal(autonomyStopped,1);assert.equal(released,0);
 }finally{release();await rm(root,{recursive:true,force:true})}
});


test('shutdown keeps its lease when the fallback process inventory cannot be inspected',async()=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'fishbot-shutdown-inventory-'));
 const agent:any=new NodeAgent({query:async()=>[]} as any,root,Buffer.alloc(32));let released=0;
 agent.favorites.stop=async()=>{};agent.bilibili.stop=async()=>{};agent.autonomy.stop=async()=>{};
 agent.releaseLease=async()=>{released++};await writeFile(path.join(root,'agent.lock'),'fixture');await writeFile(path.join(root,'bots'),'unreadable inventory');
 try{
  await assert.rejects(agent.shutdown(),{code:'ENOTDIR'});
  assert.equal(released,0);assert.equal((await stat(path.join(root,'agent.lock'))).isFile(),true);
 }finally{await rm(root,{recursive:true,force:true})}
});


test('a synchronous fallback stop failure does not skip other process owners or release the lease',async()=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'fishbot-shutdown-fallback-'));
 const agent:any=new NodeAgent({query:async()=>[]} as any,root,Buffer.alloc(32));let released=0;const stopped:string[]=[];
 agent.favorites.stop=async()=>{};agent.bilibili.stop=async()=>{};agent.autonomy.stop=async()=>{};
 agent.releaseLease=async()=>{released++};await mkdir(path.join(root,'bots','fixture'),{recursive:true});await writeFile(path.join(root,'agent.lock'),'fixture');
 agent.host.stop=(id:string,kind:string)=>{stopped.push(kind);if(kind==='ai')throw Error('synchronous stop failure');return Promise.resolve()};
 try{
  await assert.rejects(agent.shutdown(),/synchronous stop failure/);
  assert.deepEqual(stopped,['ai','protocol']);assert.equal(released,0);assert.equal((await stat(path.join(root,'agent.lock'))).isFile(),true);
 }finally{await rm(root,{recursive:true,force:true})}
});
