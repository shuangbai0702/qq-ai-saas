import test from 'node:test';import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,readFile,readdir,rm} from 'node:fs/promises';import path from 'node:path';
import {HeartbeatWriter} from '../apps/node-agent/src/agent.js';

async function fixture(){
 const root=await mkdtemp(path.resolve('.local/heartbeat-'));
 return{root,file:path.join(root,'agent-heartbeat.json')};
}
test('overlapping ticks serialize into one monotonic readable snapshot without leftover temps',async()=>{
 const {root,file}=await fixture();
 const writer=new HeartbeatWriter(file,{pid:4242,processStart:'12345',startedAt:'2026-11-07T00:00:00.000Z'});
 await Promise.all([writer.write(),writer.write(),writer.write()]);
 const data=JSON.parse(await readFile(file,'utf8'));
 assert.equal(data.sequence,3);assert.equal(data.pid,4242);assert.equal(data.processStart,'12345');
 assert.equal(typeof data.at,'number');
 assert.equal((await readdir(root)).filter(n=>n.endsWith('.tmp')).length,0,'each writer cleans only its own temp file');
 await writer.stop();
});
test('a late writer from a replaced process cannot roll the snapshot back',async()=>{
 const {file}=await fixture();
 const old=new HeartbeatWriter(file,{pid:111,startedAt:'2026-11-07T00:00:00.000Z'});
 await old.write();
 const replacement=new HeartbeatWriter(file,{pid:222,startedAt:'2026-11-07T00:05:00.000Z'});
 await replacement.write();
 await old.write();
 const data=JSON.parse(await readFile(file,'utf8'));
 assert.equal(data.pid,222);assert.equal(data.sequence,2,'the newer process keeps the file and the sequence stays monotonic across restart');
 await old.stop();await replacement.stop();
});
test('a heartbeat file without writer identity (legacy) is taken over, never blocks restart',async()=>{
 const {file}=await fixture();
 await writeFile(file,JSON.stringify({pid:9,at:Date.now()}));
 const writer=new HeartbeatWriter(file,{pid:333,startedAt:'2026-11-07T01:00:00.000Z'});
 await writer.write();
 const data=JSON.parse(await readFile(file,'utf8'));
 assert.equal(data.pid,333);assert.equal(data.sequence,1);
 await writer.stop();
});
test('rename failure cleans its own temp and a later write recovers monotonically',async()=>{
 const {root,file}=await fixture();
 let attempts=0;
 const realRename=(await import('node:fs/promises')).rename;
 const writer=new HeartbeatWriter(file,{pid:444,startedAt:'2026-11-07T02:00:00.000Z'},{rename:(async(from:string,to:string)=>{
  if(++attempts===1)throw new Error('rename_failed');
  return realRename(from,to)}) as any});
 await assert.rejects(writer.write());
 assert.equal((await readdir(root)).filter(n=>n.endsWith('.tmp')).length,0,'failed writes do not leave temp files');
 await writer.write();
 const data=JSON.parse(await readFile(file,'utf8'));
 assert.equal(data.pid,444);assert.equal(data.sequence,2,'the sequence stays monotonic after a failed write');
 await writer.stop();
});
