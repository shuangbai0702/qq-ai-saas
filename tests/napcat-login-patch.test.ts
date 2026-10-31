import test from 'node:test';import assert from 'node:assert/strict';import vm from 'node:vm';
import {mkdtemp,copyFile,readFile} from 'node:fs/promises';import path from 'node:path';import os from 'node:os';import {execFileSync} from 'node:child_process';

test('pinned native login recovers a missed connected callback once and respects disconnected state',async()=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'fish-napcat-patch-')),file=path.join(root,'napcat.mjs');
 await copyFile('references/napcat-windows-node/napcat/napcat.mjs',file);
 const python=path.resolve(`references/astrbot/.venv/${process.platform==='win32'?'Scripts/python.exe':'bin/python'}`);
 execFileSync(python,['scripts/napcat_login_patch.py',file],{windowsHide:true});const source=await readFile(file,'utf8');
 execFileSync(python,['scripts/napcat_login_patch.py',file],{windowsHide:true});assert.equal(await readFile(file,'utf8'),source,'installation is idempotent');
 const start=source.indexOf('async function e2e('),end=source.indexOf('\nasync function t2e(',start);assert.ok(start>=0&&end>start);
 for(const status of [1,2,3])for(const callback of [false,true]){
  let listener:any,calls=0;const timers:Array<()=>void>=[];
  const context=vm.createContext({use:class{},cu:(x:any)=>x,i2e:async()=>{},t2e:async()=>{calls++;await listener.onQRCodeLoginSucceed({uin:'90000001',uid:'fixture'})},pe:new Proxy({},{get:()=>()=>undefined}),setTimeout:(fn:()=>void)=>{timers.push(fn);return 1}});
  const fn=vm.runInContext('('+source.slice(start,end)+')',context);
  const service={addKernelLoginListener:(x:any)=>{listener=x},connect:()=>{if(callback&&status===1)listener.onLoginConnected();return true},getMsfStatus:()=>status};
  const promise=fn(service,{}, {},undefined,[]);await new Promise(r=>setImmediate(r));assert.equal(timers.length,1);timers[0]();await new Promise(r=>setImmediate(r));
  if(status===1){assert.equal((await promise).uin,'90000001');assert.equal(calls,1,'native callback and fallback must share the flow fence')}
  else assert.equal(calls,0,'restarting/disconnected service must not initiate login');
 }
 let listener:any,release!:()=>void,calls=0;const gate=new Promise<void>(r=>release=r),timers:Array<()=>void>=[];
 const context=vm.createContext({use:class{},cu:(x:any)=>x,i2e:()=>gate,t2e:async()=>{calls++;await listener.onQRCodeLoginSucceed({uin:'90000001',uid:'fixture'})},pe:new Proxy({},{get:()=>()=>undefined}),setTimeout:(fn:()=>void)=>{timers.push(fn);return 1}});
 const fn=vm.runInContext('('+source.slice(start,end)+')',context);
 const promise=fn({addKernelLoginListener:(x:any)=>listener=x,connect:()=>{listener.onLoginConnected();return true},getMsfStatus:()=>1},{},{},undefined,[]);
 timers[0]();release();assert.equal((await promise).uin,'90000001');assert.equal(calls,1,'fallback must not compete with an already received native callback waiting on the network');
});
