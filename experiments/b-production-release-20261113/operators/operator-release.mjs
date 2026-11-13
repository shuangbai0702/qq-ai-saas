import fs from 'node:fs/promises';import{createRequire}from'node:module';import{pathToFileURL}from'node:url';import{once}from'node:events';
const base='C:/FishBotPredeploy20261112',dir=base+'/control/b-release-20261113';
const cfg=JSON.parse((await fs.readFile(base+'/mobile-private-config.json','utf8')).replace(/^\uFEFF/,'')),rec=JSON.parse(await fs.readFile(cfg.stateRoot+'/services/agent.process.json','utf8'));
const WebSocket=createRequire(cfg.sourceRoot+'/package.json')('ws');let ws,seq=0;const pending=new Map();
async function cdp(method,params){const id=++seq;const p=new Promise((resolve,reject)=>{const t=setTimeout(()=>{pending.delete(id);reject(Error('CDP timeout'))},240000);pending.set(id,{resolve:v=>{clearTimeout(t);resolve(v)},reject:e=>{clearTimeout(t);reject(e)}})});ws.send(JSON.stringify({id,method,params}));return p}
const bound=e=>`process.getBuiltinModule('vm').runInThisContext(${JSON.stringify(e)},{importModuleDynamically:process.getBuiltinModule('vm').constants.USE_MAIN_CONTEXT_DEFAULT_LOADER})`;
async function evaluate(e,byValue=true){const r=await cdp('Runtime.evaluate',{expression:bound(e),awaitPromise:true,returnByValue:byValue});if(r.exceptionDetails)throw Error(r.exceptionDetails.exception?.description||r.exceptionDetails.text);return byValue?r.result.value:r.result}
let result;
try{
 process._debugProcess(rec.pid);let url;
 for(let i=0;i<50;i++){try{url=(await fetch('http://127.0.0.1:9229/json/list').then(r=>r.json()))[0]?.webSocketDebuggerUrl;if(url)break}catch{}await new Promise(r=>setTimeout(r,100))}
 if(!url?.startsWith('ws://127.0.0.1:9229/'))throw Error('Non-loopback or unavailable inspector');
 ws=new WebSocket(url);await once(ws,'open');ws.on('message',data=>{const r=JSON.parse(String(data));if(r.id){const p=pending.get(r.id);pending.delete(r.id);if(p)r.error?p.reject(Error(r.error.message)):p.resolve(r.result)}});
 if((await evaluate('process.pid'))!==rec.pid)throw Error('Agent PID changed');
 const proto=await evaluate(`import(${JSON.stringify(pathToFileURL(cfg.sourceRoot+'/dist-server/apps/node-agent/src/agent.js').href)}).then(m=>m.NodeAgent.prototype)`,false);
 const objects=await cdp('Runtime.queryObjects',{prototypeObjectId:proto.objectId});
 const capture=await cdp('Runtime.callFunctionOn',{objectId:objects.objects.objectId,functionDeclaration:"function(){const a=this.filter(x=>x.db&&x.host&&x.closed===false);if(a.length!==1)throw Error('Ambiguous Agent');globalThis[Symbol.for('fishbot.reply.capture')]=a[0];return a.length}",returnByValue:true});if(capture.exceptionDetails)throw Error('Capture failed');
 const op=process.argv[2]||'inspect';
 result=await evaluate(`(async()=>{try{const a=globalThis[Symbol.for('fishbot.reply.capture')];const ops=await import(${JSON.stringify(pathToFileURL(dir+'/manage-release.mjs').href+'?revision=b1')});return await ops.manage(a,${JSON.stringify(op)})}finally{delete globalThis[Symbol.for('fishbot.reply.capture')]}})()`);
 await fs.writeFile(dir+'/'+op+'-result.json',JSON.stringify({ok:true,...result}));
}catch(e){await fs.writeFile(dir+'/'+(process.argv[2]||'inspect')+'-result.json',JSON.stringify({ok:false,error:String(e)}));process.exitCode=1}
finally{if(ws){try{await evaluate("setTimeout(()=>process.getBuiltinModule('inspector').close(),200).unref();true")}catch{}ws.close()}}
