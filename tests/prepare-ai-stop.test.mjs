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

test('failed prepare and failed cancellation both retain their real cause',async()=>{
 const f=fixture(),request=f.agent.shared.request;
 f.agent.shared.request=async(g,p)=>{if(p==='/prepare-stop')throw Error('prepare receipt lost');if(p==='/cancel-stop')throw Error('cancel unavailable');return request(g,p)};
 await assert.rejects(prepareAiStop(f.agent,f.group,f.expected),error=>{
  assert.ok(error instanceof AggregateError);assert.deepEqual(error.errors.map(x=>x.message),['prepare receipt lost','cancel unavailable']);return true;
 });
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

test('Windows launcher descendant is fenced and cancellation rejects PID reuse',async()=>{
 const f=fixture(),original=f.agent.shared.request;
 f.agent.host.inspect=async()=>({pid:123,created:42,processInstances:[{pid:456,created:50}]});
 f.agent.shared.request=async(g,p)=>{const r=await original(g,p);return p==='/health'||p==='/prepare-stop'?{...r,pid:456}:r};
 const lease=await prepareAiStop(f.agent,f.group,f.expected);assert.equal(f.fenced,true);
 f.agent.host.inspect=async()=>({pid:123,created:42,processInstances:[{pid:456,created:51}]});
 await lease.cancel();assert.equal(f.calls.includes('/cancel-stop'),false);
});
test('unrelated worker PID cannot acquire a stop fence',async()=>{
 const f=fixture(),original=f.agent.shared.request;
 f.agent.host.inspect=async()=>({pid:123,created:42,processInstances:[{pid:789,created:50}]});
 f.agent.shared.request=async(g,p)=>({...await original(g,p),pid:456});
 await assert.rejects(prepareAiStop(f.agent,f.group,f.expected),/owner changed/);
 assert.equal(f.calls.includes('/prepare-stop'),false);
});
