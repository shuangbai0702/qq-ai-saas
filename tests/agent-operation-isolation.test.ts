import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {NodeAgent} from '../apps/node-agent/src/agent.js';
import {ProcessHost} from '../apps/node-agent/src/process-host.js';

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
