import test from 'node:test';import assert from 'node:assert/strict';
import {SharedAstrBotAdapter} from '../adapters/astrbot/shared.js';
import {defaultConfig,type RuntimeContext} from '../packages/contracts/index.js';
const ctx:RuntimeContext={botId:'member',tenantId:'tenant',root:'unused',generation:7,revision:2,aiPort:1,protocolPort:2,token:'',apiKey:'',config:defaultConfig,expectedUin:''};
function fixture(){
 const calls:string[]=[];const owner:any={pid:123,created:456,loopActivity:{at:Date.now()/1000,pid:124,created:457,active:true}};
 const db:any={query:async(sql:string)=>sql.startsWith('SELECT generation,revision')?[{generation:7,revision:2,desired_state:'running'}]:[]};
 const host:any={endInspection:()=>{},inspect:async()=>owner,stop:async()=>{calls.push('stop')}};
 const adapter=new SharedAstrBotAdapter(db,host,{} as any,'','');adapter.group=async()=>({id:'group',generation:3});adapter.start=async()=>{calls.push('start')};adapter.request=async(...args)=>{assert.equal(args[5],1500);throw Error('timeout')};
 return{adapter,owner,calls};
}
test('matching recent busy loop keeps ready false and delays destructive recovery only',async()=>{
 const f=fixture();const health=await f.adapter.probe(ctx);assert.equal(health.ready,false);assert.match(health.detail!,/IPC卡顿/);
 await f.adapter.recoverReadiness(ctx);assert.deepEqual(f.calls,[]);
 f.owner.loopActivity.at=Date.now()/1000-121;await f.adapter.recoverReadiness(ctx);assert.deepEqual(f.calls,['stop','start']);
});
test('continuous busy loop refresh cannot defer the same IPC failure beyond five minutes',async()=>{
 const f=fixture();await f.adapter.probe(ctx);(f.adapter as any).ipcStalls.get('group').since=Date.now()-300000;
 f.owner.loopActivity.at=Date.now()/1000;await f.adapter.recoverReadiness(ctx);assert.deepEqual(f.calls,['stop','start']);
});
test('successful health and changed process lifetime each reset the failure episode',async()=>{
 const f=fixture();await f.adapter.probe(ctx);const first=(f.adapter as any).ipcStalls.get('group');first.since=Date.now()-300000;
 f.owner.created++;await f.adapter.recoverReadiness(ctx);assert.deepEqual(f.calls,[]);assert.notEqual((f.adapter as any).ipcStalls.get('group'),first);
 f.adapter.request=async()=>({ready:true,generation:3,members:{member:{generation:7,revision:2}},metrics:{}});await f.adapter.probe(ctx);assert.equal((f.adapter as any).ipcStalls.size,0);
});
test('fresh activity is checked again immediately before destructive recovery',async()=>{
 const f=fixture();await f.adapter.probe(ctx);let checks=0;const live={...f.owner,loopActivity:null};(f.adapter as any).host.inspect=async()=>++checks===1?f.owner:live;
 await f.adapter.recoverReadiness(ctx);assert.deepEqual(f.calls,['stop','start']);
});
test('prototype refresh lazily owns its stall map and explicit unhealthy replies are not exempted',async()=>{
 const f=fixture();delete (f.adapter as any).ipcStalls;await f.adapter.probe(ctx);assert.equal((f.adapter as any).ipcStalls.size,1);
 f.adapter.request=async()=>({ready:false,generation:3});await f.adapter.recoverReadiness(ctx);assert.deepEqual(f.calls,['stop','start']);assert.equal((f.adapter as any).ipcStalls.size,0);
});
