import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,readFile,writeFile} from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {initialEgress,nextEgress,observeEgress,proxyPort} from '../adapters/protocol/egress.js';
import {withNapcatSettings} from '../adapters/protocol/napcat/egress.js';
import {NapCatAdapter} from '../adapters/protocol/napcat/index.js';
import {type ProtocolContext} from '../packages/contracts/index.js';

const identity={pid:123,created:456,generation:7};
test('egress preserves random choice and switches after 1,1,2,2,4,4 distinct offline episodes',()=>{
 assert.equal(initialEgress(()=>0).route,'direct');assert.equal(initialEgress(()=>1).route,'huawei');
 let state=initialEgress(()=>0);
 for(const threshold of [1,1,2,2,4,4]){
  state.process={...identity,online:false,counted:false};
  for(let n=0;n<threshold;n++){
   state=observeEgress(state,identity,true,false);
   state=observeEgress(state,identity,false,true);
   const duplicate=observeEgress(state,identity,false,true);assert.deepEqual(duplicate,state);
   assert.equal(state.pending,n===threshold-1);
  }
  const previous=state.route;state=nextEgress(state);assert.notEqual(state.route,previous);assert.equal(state.failures,0);
 }
 assert.equal(state.switches,6);
 const stable={...state,process:{...identity,online:true,counted:false}};
 for(const stale of [{...identity,pid:999},{...identity,created:999},{...identity,generation:8}])assert.deepEqual(observeEgress(stable,stale,false,true),stable);
 assert.deepEqual(observeEgress(stable,identity,false,false),stable,'verification and startup are not a kick');
});

test('settings serialize concurrent receipts and retain exact data after parse failure',async()=>{
 const dir=await mkdtemp(path.join(os.tmpdir(),'fish-egress-')),file=path.join(dir,'bot.json');
 await withNapcatSettings(file,async()=>({settings:{url:'http://127.0.0.1:1234/api',webuiToken:'private',egress:initialEgress(()=>0)},result:undefined}));
 await Promise.all(Array.from({length:6},()=>withNapcatSettings(file,async settings=>{assert.ok(settings?.egress);settings.egress.failures++;return{settings,result:undefined}})));
 assert.equal(JSON.parse(await readFile(file,'utf8')).egress.failures,6);
 await writeFile(file,'{broken');await assert.rejects(withNapcatSettings(file,async settings=>({settings:settings!,result:undefined})));assert.equal(await readFile(file,'utf8'),'{broken');
 assert.equal(proxyPort({NAPCAT_EGRESS_PROXY_PORT:'11973'}),11973);assert.equal(proxyPort({}),undefined);assert.throws(()=>proxyPort({NAPCAT_EGRESS_PROXY_PORT:'65536'}));
});

test('native proxy admission and real offline receipts reuse existing recovery ownership',async()=>{
 const dir=await mkdtemp(path.join(os.tmpdir(),'fish-egress-adapter-')),settingsFile=path.join(dir,'bot.json');
 const original=process.env.NAPCAT_EGRESS_PROXY_PORT;process.env.NAPCAT_EGRESS_PROXY_PORT='11973';
 const ctx:ProtocolContext={botId:'bot',tenantId:'tenant',root:dir,generation:7,aiPort:24000,protocolPort:24002,token:'a'.repeat(64),expectedUin:''};
 let processRecord:any=null,starts=0,online=true,failedStart=false,nativeRestart=false;
 const host:any={inspect:async()=>processRecord?{...processRecord,valid:true}:null,record:async()=>processRecord,start:async()=>{starts++;const manifest=JSON.parse(await readFile(path.join(dir,'napcat-platform.json'),'utf8'));assert.equal(manifest.network.mode,failedStart?'direct':'socks5');if(!failedStart)assert.equal(manifest.network.port,11973);assert.equal(JSON.parse(await readFile(settingsFile,'utf8')).egress.route,failedStart?'direct':'huawei','route is durable before process starts');if(failedStart)throw new Error('spawn failed');processRecord={...identity};}};
 const adapter=new NapCatAdapter(host,dir);
 await writeFile(settingsFile,JSON.stringify({url:'http://127.0.0.1:24002/api',webuiToken:'token',launcher:'fixture-node',runtimeDir:dir,egress:{...initialEgress(()=>0),pending:true}}));
 (adapter as any).getLoginState=async()=>online?{state:'online'}:nativeRestart?{state:'starting',restartRequested:true}:{state:'failed',message:'QQ已掉线'};
 try{
  await adapter.beginLogin(ctx);assert.equal(starts,1);
  const first=await adapter.getHealth(ctx);assert.equal(first.online,true);
  online=false;const offline=await adapter.getHealth(ctx);assert.equal(offline.metrics?.restartRequested,true);
  await adapter.getHealth(ctx);assert.equal(JSON.parse(await readFile(settingsFile,'utf8')).egress.failures,1);
  await adapter.beginLogin(ctx);assert.equal(starts,1,'adoption never restarts independent QQ');
  nativeRestart=true;assert.equal((await adapter.getHealth(ctx)).metrics?.restartRequested,true);assert.equal(JSON.parse(await readFile(settingsFile,'utf8')).egress.failures,1,'native restart does not count the same loss again');
  processRecord=null;failedStart=true;await assert.rejects(adapter.beginLogin(ctx),/spawn failed/);
  assert.equal(JSON.parse(await readFile(settingsFile,'utf8')).egress.route,'direct','failed start retains the next chosen route');
 }finally{if(original===undefined)delete process.env.NAPCAT_EGRESS_PROXY_PORT;else process.env.NAPCAT_EGRESS_PROXY_PORT=original}
});
