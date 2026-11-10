import test from 'node:test';
import assert from 'node:assert/strict';
import {prepareAiStop} from '../scripts/prepare-ai-stop.mjs';

function fixture(){
 const expected={pid:123,created:42},group={id:'group',generation:7};
 const calls=[];let owner={...expected},fenced=false;
 const agent={host:{record:async()=>owner},shared:{request:async(_g,path)=>{
  calls.push(path);
  if(path==='/health')return{pid:123,generation:7,stopFence:true,activity:{idle:true}};
  if(path==='/prepare-stop'){fenced=true;return{pid:123,quiescing:true}}
  if(path==='/cancel-stop'){fenced=false;return{quiescing:false}}
 }}};
 return{agent,group,expected,calls,get fenced(){return fenced},replace:()=>owner={pid:124,created:43}};
}
test('atomic prepare and cancel use the same process identity',async()=>{
 const f=fixture(),lease=await prepareAiStop(f.agent,f.group,f.expected);
 assert.equal(lease.legacy,false);assert.equal(f.fenced,true);
 await lease.cancel();assert.equal(f.fenced,false);
 assert.deepEqual(f.calls,['/health','/prepare-stop','/cancel-stop']);
});
test('lost prepare response reopens admission before returning failure',async()=>{
 const f=fixture(),original=f.agent.shared.request;
 f.agent.shared.request=async(g,p)=>{const r=await original(g,p);if(p==='/prepare-stop')throw Error('connection lost');return r};
 await assert.rejects(prepareAiStop(f.agent,f.group,f.expected),/connection lost/);
 assert.equal(f.fenced,false);
});
test('replacement process never receives cancellation for an old owner',async()=>{
 const f=fixture(),lease=await prepareAiStop(f.agent,f.group,f.expected);f.replace();
 await lease.cancel();assert.equal(f.calls.includes('/cancel-stop'),false);
});
test('legacy replacement requires explicit transition and idle evidence',async()=>{
 const f=fixture();f.agent.shared.request=async()=>({pid:123,generation:7,activity:{idle:true}});
 await assert.rejects(prepareAiStop(f.agent,f.group,f.expected),/no atomic stop fence/);
 assert.equal((await prepareAiStop(f.agent,f.group,f.expected,{allowLegacy:true})).legacy,true);
 f.agent.shared.request=async()=>({pid:123,generation:7,activity:{idle:false}});
 await assert.rejects(prepareAiStop(f.agent,f.group,f.expected,{allowLegacy:true}),/busy/);
});
