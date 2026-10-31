import test from'node:test';import assert from'node:assert/strict';import{mkdtemp,mkdir,writeFile,readFile}from'node:fs/promises';import path from'node:path';import os from'node:os';import{pathToFileURL}from'node:url';
import{NapCatAdapter}from'../adapters/protocol/napcat/index.js';import{defaultConfig,type RuntimeContext}from'../packages/contracts/index.js';
const runner=await import(pathToFileURL(path.resolve('adapters/protocol/napcat/runner.mjs')).href);
test('NapCat runner isolates QQ native profile, writable config and OneBot destinations',async()=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'fish-napcat-runner-'));const runtime=path.join(root,'binaries');await mkdir(path.join(runtime,'napcat'),{recursive:true});for(const file of ['wrapper.node','config.json','package.json','napcat/napcat.mjs','node_addon.node'])await writeFile(path.join(runtime,file),'fixture');
 const prepared:any[]=[];
 for(const id of ['a','b']){const bot=path.join(root,id);await mkdir(bot);const manifest=path.join(bot,'napcat-platform.json');await writeFile(manifest,JSON.stringify({dataDir:path.join(bot,'qq'),runtimeDir:runtime,management:{host:'127.0.0.1',port:id==='a'?24002:24006,token:`private-${id}`},reverseWs:{url:`ws://127.0.0.1:${id==='a'?24000:24004}/ws`,token:id.repeat(64)}}));prepared.push(runner.prepareRuntime(manifest))}
 assert.notEqual(prepared[0].qqProfile,prepared[1].qqProfile);assert.notEqual(prepared[0].workdir,prepared[1].workdir);assert.notEqual(prepared[0].env.USERPROFILE,prepared[1].env.USERPROFILE);
 for(let i=0;i<2;i++){const id=i?'b':'a';const c=JSON.parse(await readFile(path.join(prepared[i].workdir,'config/onebot11.json'),'utf8'));assert.equal(c.network.websocketClients[0].token,id.repeat(64));const web=JSON.parse(await readFile(path.join(prepared[i].workdir,'config/webui.json'),'utf8'));assert.equal(web.token,`private-${id}`);assert.equal(web.host,'127.0.0.1');assert.equal(prepared[i].env.NAPCAT_DISABLE_MULTI_PROCESS,'1')}
 const invalid=path.join(root,'invalid.json');await writeFile(invalid,JSON.stringify({...prepared[0].manifest,dataDir:path.join(root,'a/qq')}));assert.throws(()=>runner.prepareRuntime(invalid),/dataDir/);
 const manifestPath=path.join(root,'a/napcat-platform.json');
 for(const reverseWs of [null,{}, {url:42,token:'a'.repeat(64)}, ...['ws://example.com:24000/ws','ws://127.0.0.1:0/ws','ws://127.0.0.1:65536/ws','wss://127.0.0.1:24000/ws','ws://localhost:24000/ws','ws://127.0.0.1:24000/ws?token=x','ws://127.0.0.1:24000/ws#x','ws://user@127.0.0.1:24000/ws','ws://127.0.0.1:24000/other'].map(url=>({url,token:'a'.repeat(64)})), ...['','short','g'.repeat(64),123].map(token=>({url:'ws://127.0.0.1:24000/ws',token}))]){
  await writeFile(manifestPath,JSON.stringify({...prepared[0].manifest,reverseWs}));assert.throws(()=>runner.prepareRuntime(manifestPath),/reverseWs/);
 }
 assert.equal(JSON.parse(await readFile(path.join(prepared[0].workdir,'config/onebot11.json'),'utf8')).network.websocketClients[0].token,'a'.repeat(64),'rejected manifests leave config untouched');
});
test('NapCat startup remains alive without API readiness; no premature QR or online',async()=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'fish-napcat-state-'));const adapter=new NapCatAdapter({inspect:async()=>({pid:123,rssBytes:100})}as any,root);const ctx:RuntimeContext={botId:'a',tenantId:'a',root,generation:1,revision:1,aiPort:24000,protocolPort:24002,token:'a',config:defaultConfig,apiKey:'',expectedUin:''};
 (adapter as any).request=async()=>{throw new Error('ECONNREFUSED')};const startup=await adapter.getHealth(ctx);assert.equal(startup.alive,true);assert.equal(startup.ready,false);assert.equal(startup.online,false);
 (adapter as any).request=async()=>({isLogin:false,coreReady:false,qrcodeurl:''});assert.equal((await adapter.getLoginState(ctx)).state,'starting');
 for(const phase of ['generating_qrcode','waiting_qrcode','reconnecting']){
  (adapter as any).request=async()=>({isLogin:false,isOffline:true,coreReady:false,loginPhase:phase,qrcodeurl:''});assert.equal((await adapter.getLoginState(ctx)).state,'starting','offline core during '+phase+' is not terminal');
 }
 (adapter as any).request=async()=>({isOffline:true,loginPhase:'waiting_qrcode',qrcodeurl:'real-qr-content'});assert.equal((await adapter.getLoginState(ctx)).state,'waiting_scan');
 (adapter as any).request=async()=>({isOffline:true,loginPhase:'qrcode_scanned'});assert.equal((await adapter.getLoginState(ctx)).state,'waiting_confirm');
 (adapter as any).request=async()=>({isOffline:true,loginPhase:'initializing'});assert.equal((await adapter.getLoginState(ctx)).state,'authenticating');
 (adapter as any).request=async()=>({isOffline:true,loginPhase:'offline'});assert.equal((await adapter.getLoginState(ctx)).state,'failed');
 (adapter as any).request=async()=>({loginError:'二维码已过期，请刷新'});assert.equal((await adapter.getLoginState(ctx)).state,'expired');
 (adapter as any).request=async()=>({isLogin:true,coreReady:false,qrcodeurl:'qr'});assert.notEqual((await adapter.getLoginState(ctx)).state,'online');
 (adapter as any).request=async()=>({loginError:'账号需要验证'});const verification=await adapter.getHealth(ctx);assert.equal(verification.online,false);assert.equal(verification.loginState,'verification_required');assert.equal(verification.detail,'账号需要验证');assert.ok(!('qr' in verification),'QR is not part of health');
});
test('native profile facade handles non-configurable getter without touching desktop path',()=>{
 const original=process.dlopen;
 const util=function(){};Object.defineProperty(util,'getNTUserDataInfoConfig',{value:()=>'/desktop/profile',writable:false,configurable:false});Object.defineProperty(util,'getOwner',{value:function(){return this},writable:false,configurable:false});
 try{process.dlopen=((module:any)=>{module.exports={NodeQQNTWrapperUtil:util}})as any;const runtime={env:{NAPCAT_WRAPPER_PATH:path.resolve('fixture-wrapper.node')},qqProfile:'/private/bot/qq/profile'};runner.isolateNativeProfile(runtime);const module:any={exports:{}};process.dlopen(module,path.resolve('fixture-wrapper.node'));assert.equal(module.exports.NodeQQNTWrapperUtil.getNTUserDataInfoConfig(),runtime.qqProfile);assert.equal(module.exports.NodeQQNTWrapperUtil.getOwner(),util);assert.equal((util as any).getNTUserDataInfoConfig(),'/desktop/profile')}
 finally{process.dlopen=original}
});
