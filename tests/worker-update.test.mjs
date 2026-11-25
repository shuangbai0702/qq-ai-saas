import test from 'node:test';
import assert from 'node:assert/strict';
import {updateSharedWorkers} from '../scripts/worker-update.mjs';

function fixture(){
 const events=[],protocol={pid:10,created:1},group={id:'g',generation:2,desired_state:'running'};
 const bot={id:'b',group_id:'g',runtime_mode:'shared',generation:3,revision:4};
 let worker={pid:20,created:2},fenced=false,online=true,next=21;
 const autonomy={busy:false,active:undefined,heartbeatTask:undefined,tick:async()=>{},stop:()=>assert.fail('must not cancel Work')};
 const agent={closed:false,tick:async()=>{},autonomy,bilibili:{stopped:false,mediaBusy:new Promise(()=>{}),stop:()=>assert.fail('must not abort media')},favorites:{closed:false,stop:()=>assert.fail('must not abort favorites')},controlIdle:()=>true,
  db:{query:async(sql)=>sql.includes('runtime_groups')?[group]:[bot]},
  context:async b=>b,
  host:{record:async(id,kind)=>kind==='protocol'?protocol:online?worker:null,inspect:async(id,kind)=>kind==='protocol'?protocol:online?worker:null,
   stop:async(id,kind)=>{assert.equal(kind,'ai');assert.equal(id,'g');events.push('stop:'+worker.pid);online=false}},
  shared:{request:async(g,path)=>{
   if(path==='/health')return{pid:worker.pid,generation:group.generation,stopFence:true,activity:{idle:true},ready:online,quiescing:fenced,members:{b:{generation:bot.generation,revision:bot.revision}},memberErrors:{}};
   if(path==='/prepare-stop'){fenced=true;events.push('fence');return{pid:worker.pid,quiescing:true}}
   if(path==='/cancel-stop'){fenced=false;events.push('reopen');return{quiescing:false}}
   assert.fail(path);
  },start:async()=>{if(!online)group.generation++;worker={pid:next++,created:next};online=true;fenced=false;events.push('start:'+worker.pid)}}};
 const prior={tick:agent.tick,autonomyTick:autonomy.tick,bili:agent.bilibili,favorites:agent.favorites};
 const options={release:'a'.repeat(40),timeoutMs:100,heartbeat:async()=>{},apply:async()=>events.push('apply'),rollback:async()=>events.push('rollback'),verify:async()=>{events.push('verify');return{release:'a'.repeat(40),runtimeApplied:true,journalDigestsUnchanged:true}}};
 return{agent,options,events,group,bot,protocol,prior,get fenced(){return fenced},restored(){assert.equal(agent.tick,prior.tick);assert.equal(autonomy.tick,prior.autonomyTick);assert.equal(agent.bilibili,prior.bili);assert.equal(agent.favorites,prior.favorites);assert.equal(agent.bilibili.stopped,false);assert.equal(agent.favorites.closed,false);assert.equal(agent[Symbol.for('fishbot.worker-update')],undefined)}};
}

test('release verifies real member revision and preserves QQ, scheduler and external tasks',async()=>{
 const f=fixture(),result=await updateSharedWorkers(f.agent,f.options);
 assert.equal(result.qqPreserved,1);assert.equal(result.proof.runtimeApplied,true);assert.equal(result.proof.journalDigestsUnchanged,true);
 assert.equal(f.group.generation,3,'Real shared start advances the stopped group generation');
 assert.deepEqual(f.events,['fence','apply','stop:20','start:21','verify']);f.restored();
});

test('busy boundary times out, restores admission and cannot install later',async()=>{
 const f=fixture();f.agent.controlIdle=()=>false;
 await assert.rejects(updateSharedWorkers(f.agent,{...f.options,timeoutMs:5}),/timed out/);f.restored();
 f.agent.controlIdle=()=>true;await Promise.resolve();assert.deepEqual(f.events,[]);
 await updateSharedWorkers(f.agent,f.options);f.restored();
});

test('an idle sampling race retries only a 409 and retains one fence',async()=>{
 const f=fixture(),request=f.agent.shared.request;let attempts=0;
 f.agent.shared.request=async(g,p)=>{if(p==='/prepare-stop'&&attempts++===0)throw Error('HTTP 409');return request(g,p)};
 await updateSharedWorkers(f.agent,f.options);assert.equal(attempts,2);assert.equal(f.events.filter(x=>x==='apply').length,1);f.restored();
});

test('concurrent caller is rejected before pausing another operation',async()=>{
 const f=fixture();let enter,finish;const entered=new Promise(r=>enter=r),gate=new Promise(r=>finish=r);
 const first=updateSharedWorkers(f.agent,{...f.options,apply:async()=>{enter();await gate}});
 await entered;await assert.rejects(updateSharedWorkers(f.agent,f.options),/Another Worker update/);
 finish();await first;f.restored();
});

test('abort while draining reopens the original worker without any install',async()=>{
 const f=fixture(),controller=new AbortController(),request=f.agent.shared.request;
 f.agent.shared.request=async(g,p)=>{const r=await request(g,p);if(p==='/health'){controller.abort(Error('operator disconnected'));r.activity.idle=false}return r};
 await assert.rejects(updateSharedWorkers(f.agent,{...f.options,signal:controller.signal}),/operator disconnected/);
 assert.equal(f.events.includes('apply'),false);assert.equal(f.events.includes('rollback'),false);f.restored();
});

test('partial installation failure rolls files back and releases its acquired fence',async()=>{
 const f=fixture();await assert.rejects(updateSharedWorkers(f.agent,{...f.options,apply:async()=>{f.events.push('apply');throw Error('disk failure')}}),/disk failure/);
 assert.deepEqual(f.events,['fence','apply','rollback','reopen']);assert.equal(f.fenced,false);f.restored();
});

test('failed final verification fences the rejected replacement before rollback recovery',async()=>{
 const f=fixture();await assert.rejects(updateSharedWorkers(f.agent,{...f.options,verify:async()=>{throw Error('loaded source mismatch')}}),/loaded source mismatch/);
 assert.deepEqual(f.events,['fence','apply','stop:20','start:21','rollback','fence','stop:21','start:22']);f.restored();
});

test('generation change is rejected and never restarts a different owner',async()=>{
 const f=fixture();await assert.rejects(updateSharedWorkers(f.agent,{...f.options,apply:async()=>{f.group.generation++;}}),/generation changed/);
 assert.ok(f.events.includes('rollback'));assert.equal(f.events.some(x=>x.startsWith('stop:')),false);f.restored();
});

test('fence cancellation failure is visible and still restores Agent scheduling',async()=>{
 const f=fixture(),request=f.agent.shared.request;
 f.agent.shared.request=async(g,p)=>{if(p==='/cancel-stop')throw Error('cancel unavailable');return request(g,p)};
 await assert.rejects(updateSharedWorkers(f.agent,{...f.options,apply:async()=>{throw Error('disk failure')}}),/admission could not be reopened/);f.restored();
});

test('lost stop response still recovers the affected Worker before releasing scheduling',async()=>{
 const f=fixture(),stop=f.agent.host.stop;let first=true;
 f.agent.host.stop=async(...args)=>{await stop(...args);if(first){first=false;throw Error('lost stop response')}};
 await assert.rejects(updateSharedWorkers(f.agent,f.options),/lost stop response/);
 assert.deepEqual(f.events,['fence','apply','stop:20','rollback','start:21']);f.restored();
});

test('rollback failure preserves its real cause and never starts against unknown disk contents',async()=>{
 const f=fixture();
 await assert.rejects(updateSharedWorkers(f.agent,{...f.options,apply:async()=>{throw Error('partial install')},rollback:async()=>{throw Error('rollback disk error')}}),error=>{
  assert.ok(error instanceof AggregateError);assert.deepEqual(error.errors.map(e=>e.message),['partial install','rollback disk error']);return true;
 });
 assert.equal(f.events.some(x=>x.startsWith('start:')),false);f.restored();
});

test('transient readiness transport failure retries without reinstalling or stopping QQ',async()=>{
 const f=fixture(),request=f.agent.shared.request;let refusals=0;
 f.agent.shared.request=async(g,p)=>{
  if(p==='/health'&&f.events.some(x=>x.startsWith('start:'))&&refusals++===0)throw new TypeError('fetch failed',{cause:Object.assign(new Error(),{code:'ECONNREFUSED'})});
  return request(g,p);
 };
 await updateSharedWorkers(f.agent,{...f.options,timeoutMs:1000});
 assert.ok(refusals>1);assert.equal(f.events.filter(x=>x==='apply').length,1);assert.equal(f.events.includes('rollback'),false);f.restored();
});

test('readiness authentication failure is visible instead of retried as a startup delay',async()=>{
 const f=fixture(),request=f.agent.shared.request;
 f.agent.shared.request=async(g,p)=>{if(p==='/health'&&f.events.includes('start:21'))throw Error('HTTP 401');return request(g,p)};
 await assert.rejects(updateSharedWorkers(f.agent,f.options),error=>{
  assert.ok(error instanceof AggregateError);assert.equal(error.errors[0].message,'HTTP 401');return true;
 });assert.ok(f.events.includes('rollback'));assert.equal(f.events.includes('stop:21'),false);f.restored();
});

test('stop boundary proof runs before the new Worker and phase progress is observable',async()=>{
 const f=fixture(),progress=[];
 await updateSharedWorkers(f.agent,{...f.options,onProgress:p=>progress.push(p.phase),afterStop:async()=>{
  assert.ok(f.events.includes('stop:20'));assert.equal(f.events.includes('start:21'),false);f.events.push('journal-checked');
 }});
 assert.ok(f.events.indexOf('journal-checked')<f.events.indexOf('start:21'));
 assert.deepEqual(progress,['draining','installing','reloading','verifying','applied']);f.restored();
});
