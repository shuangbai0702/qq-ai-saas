import test from 'node:test';import assert from 'node:assert/strict';
import {SharedAstrBotAdapter} from '../adapters/astrbot/shared.js';
import {ProcessHost} from '../apps/node-agent/src/process-host.js';
import {defaultConfig,type RuntimeContext} from '../packages/contracts/index.js';

const ctx:RuntimeContext={botId:'member',tenantId:'tenant',root:'unused',generation:7,revision:2,aiPort:24000,protocolPort:24002,token:'fixture',apiKey:'fixture',config:defaultConfig,expectedUin:''};
function fixture(){
 const group={id:'group-fixture',generation:3};let process:any={pid:123,created:456};const calls:any[]=[];
 const db:any={query:async(sql:string)=>sql.startsWith('SELECT generation,revision')?[{generation:7,revision:2,desired_state:'running'}]:[],transaction:async(fn:any)=>fn(db)};
 const host:any={endInspection:()=>{},inspect:async()=>process,stop:async(...args:any[])=>{calls.push(['stop',...args]);process=null}};
 const adapter=new SharedAstrBotAdapter(db,host,{} as any,'','',async()=>ctx);adapter.group=async()=>({...group});
 adapter.start=async received=>{assert.equal(received,ctx);calls.push(['start'])};
 return{adapter,host,db,group,calls,setProcess:(value:any)=>{process=value}};
}

test('shared probe reports live PID truthfully and bounds failed IPC to 1500ms',async()=>{
 const f=fixture();f.adapter.request=async(...args)=>{assert.equal(args[5],1500);throw new Error('fixture IPC unavailable')};
 const health=await f.adapter.probe(ctx);assert.equal(health.alive,true);assert.equal(health.ready,false);assert.equal(health.pid,123);assert.deepEqual(f.calls,[]);
 f.adapter.request=async()=>({ready:true,generation:3,members:{},metrics:{}});const missing=await f.adapter.probe(ctx);assert.equal(missing.alive,true);assert.equal(missing.ready,false,'missing member does not mean missing worker');
});

test('readiness recovery preserves healthy shared group, including absent member',async()=>{
 const f=fixture();f.adapter.request=async(...args)=>{assert.equal(args[5],1500);return{ready:true,generation:3,members:{peer:{generation:9,revision:4}}}};
 await f.adapter.recoverReadiness(ctx);assert.deepEqual(f.calls,[['start']],'existing start reattaches only target member on a live group');
 f.calls.length=0;f.adapter.request=async()=>({ready:true,generation:3,members:{member:{generation:7,revision:2}}});await f.adapter.recoverReadiness(ctx);assert.deepEqual(f.calls,[],'a member already recovered is not reloaded');
});

test('unresponsive shared control plane replaces only verified group then delegates the checked member restore to existing start',async()=>{
 const f=fixture();f.adapter.request=async()=>{throw new Error('fixture timeout')};await f.adapter.recoverReadiness(ctx);
 assert.deepEqual(f.calls,[['stop','group-fixture','ai','shared-readiness-timeout',{pid:123,created:456}],['start']]);
});

test('changed group generation, changed PID birth or changed member lifecycle prevents destructive recovery',async()=>{
 for(const change of ['group','birth','bot']){
  const f=fixture();f.adapter.request=async()=>{if(change==='group')f.group.generation++;if(change==='birth')f.setProcess({pid:123,created:999});if(change==='bot')f.db.query=async()=>[{generation:8,revision:2,desired_state:'running'}];throw new Error('fixture IPC failed')};
  await assert.rejects(f.adapter.recoverReadiness(ctx),error=>(error as any).superseded===true);assert.deepEqual(f.calls,[],change);
 }
 const f=fixture();f.adapter.request=async()=>({ready:true,generation:99,members:{}});await assert.rejects(f.adapter.recoverReadiness(ctx),error=>(error as any).superseded===true);assert.deepEqual(f.calls,[],'healthy different-generation worker is never killed');
});

test('ProcessHost rejects a changed process record before issuing any stop helper',async()=>{
 const host=new ProcessHost('unused','unused');host.record=async()=>({pid:123,created:999});
 await assert.rejects(host.stop('group-fixture','ai','shared-readiness-timeout',{pid:123,created:456}),error=>(error as any).superseded===true);
});
