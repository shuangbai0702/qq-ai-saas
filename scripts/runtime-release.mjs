/** Trusted host CLI for staged AI releases. No application HTTP execution API.
 * Reuses worker-update's ownership, stop fence and recovery. SSH transport and
 * credentials stay with the operator; receipts survive a disconnected caller.
 * npm run deploy:runtime -- plan|apply|status <private-profile.json> <stage>
 * Stage owns release.json, expected-before.json and staged/ exact file hashes.
 * Run apply as a durable host task; after a lost response query status first.
 */
import assert from 'node:assert/strict';
import path from 'node:path';
import fs from 'node:fs/promises';
import {createHash,randomUUID} from 'node:crypto';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
import {updateSharedWorkers} from './worker-update.mjs';
import {verifyProduction} from './verify-production.mjs';

const hash=raw=>createHash('sha256').update(raw).digest('hex');
const json=async file=>JSON.parse((await fs.readFile(file,'utf8')).replace(/^\uFEFF/,''));
const fingerprint=async file=>{try{return hash(await fs.readFile(file))}catch(e){if(e.code==='ENOENT')return null;throw e}};
async function verifyFiles(root,files){
 for(const [name,digest]of Object.entries(files))assert.equal(await fingerprint(target(root,name)),digest,'Release verification mismatch: '+name);
}
function target(root,name){
 assert.ok(typeof name==='string'&&name&&!name.includes('\\')&&!name.includes('\0')&&!name.split('/').includes('..')&&!['.local','node_modules'].includes(name.split('/')[0]),'Invalid release path: '+name);
 const file=path.resolve(root,name);assert.ok(file.startsWith(path.resolve(root)+path.sep),'Release path escapes its root');return file;
}
async function atomic(file,raw){
 await fs.mkdir(path.dirname(file),{recursive:true});const temp=file+'.release-'+randomUUID()+'.tmp';
 try{
  await fs.writeFile(temp,raw);const deadline=Date.now()+2000;
  for(;;){
   try{await fs.rename(temp,file);break}
   catch(error){if(process.platform!=='win32'||!['EPERM','EACCES','EBUSY'].includes(error.code)||Date.now()>=deadline)throw error;await new Promise(resolve=>setTimeout(resolve,25))}
  }
 }finally{await fs.rm(temp,{force:true})}
}
const receipt=(dir,name,value)=>atomic(path.join(dir,name+'.json'),JSON.stringify(value,null,2));

// The deployment account owns code writes. The live Agent only borrows this
// exact staged operation while it owns the Worker fence; it needs no source ACL.
export function releaseFiles({root,spec,expected},dir){
 const backup=path.join(dir,'before-code'),installed=[];
 return{
  async install(){
   for(const [name,digest]of Object.entries(expected))assert.equal(await fingerprint(target(root,name)),digest,'Activation CAS changed: '+name);
   await fs.mkdir(backup);
   for(const [name,digest]of Object.entries(expected))if(digest!==null)await atomic(target(backup,name),await fs.readFile(target(root,name)));
   for(const [name,digest]of Object.entries(expected))assert.equal(await fingerprint(target(root,name)),digest,'Activation CAS changed: '+name);
   for(const name of [...Object.keys(spec.files).filter(n=>n!=='dist-server/build.json'),'dist-server/build.json']){
    installed.push(name);await atomic(target(root,name),await fs.readFile(target(path.join(dir,'staged'),name)));
   }
   for(const name of spec.retired||[]){installed.push(name);await fs.unlink(target(root,name))}
   await verifyProduction(root);await verifyFiles(root,spec.verifyFiles);
  },
  async rollback(){
   for(const name of [...installed].reverse()){
    const file=target(root,name),current=await fingerprint(file);
    if(current===expected[name])continue;
    assert.ok(current===spec.files[name]||current===null,'Concurrent file change during rollback: '+name);
    if(expected[name]===null)await fs.rm(file,{force:true});
    else await atomic(file,await fs.readFile(target(backup,name)));
   }
   await verifyProduction(root);
  }
 };
}

async function requestFiles(dir,release,operation){
 const id=randomUUID(),deadline=Date.now()+60000;
 await receipt(dir,'file-request',{id,release,operation,ownerPid:process.pid,deadline});
 for(;;){
  const result=await json(path.join(dir,'file-result.json')).catch(error=>{if(error.code==='ENOENT')return null;throw error});
  if(result?.id===id){if(!result.ok)throw Error('Release '+operation+' failed: '+result.error);return}
  assert.ok(Date.now()<deadline,'Release '+operation+' receipt timed out; inspect host task before retrying');
  await new Promise(resolve=>setTimeout(resolve,50));
 }
}

export async function checkRuntimeRelease(profile,dir){
 const root=path.resolve(profile.sourceRoot),spec=await json(path.join(dir,'release.json'));
 assert.match(spec.commit,/^[a-f0-9]{40}$/);assert.ok(spec.files&&spec.verifyFiles);
 for(const name of Object.keys(spec.verifyFiles))target(root,name);
 const expected=await json(path.join(dir,'expected-before.json'));
 const current=await json(path.join(root,'dist-server/build.json'));
 if(current.gitCommit===spec.commit){await verifyProduction(root);return{root,spec,expected,alreadyApplied:true}}
 assert.equal(await fingerprint(path.join(root,'dist-server/build.json')),spec.oldManifestSha256,'Concurrent release manifest change');
 await verifyProduction(root);
 for(const [name,digest]of Object.entries(expected))assert.equal(await fingerprint(target(root,name)),digest,'Concurrent source change: '+name);
 for(const [name,digest]of Object.entries(spec.files)){
  assert.ok(Object.hasOwn(expected,name),'Missing activation CAS: '+name);
  assert.equal(spec.verifyFiles[name],digest,'Incomplete release inventory: '+name);
  assert.match(digest,/^[a-f0-9]{64}$/);assert.equal(await fingerprint(target(path.join(dir,'staged'),name)),digest,'Staged hash mismatch: '+name);
 }
 for(const name of spec.retired||[])assert.ok(Object.hasOwn(expected,name)&&expected[name]!==null,'Missing retirement CAS: '+name);
 const next=await json(path.join(dir,'staged/dist-server/build.json'));
 assert.equal(next.gitCommit,spec.commit);assert.equal(next.gitDirty,false);
 const allowed=new Set(['dist-server/apps/node-agent/src/bilibili/executor.js','dist-server/apps/node-agent/src/bilibili/executor.js.map']);
 for(const field of ['artifactHashes','contracts','migrations']){
  for(const name of new Set([...Object.keys(current[field]||{}),...Object.keys(next[field]||{})]))
   assert.ok(current[field]?.[name]===next[field]?.[name]||allowed.has(name),'Requires a different component release: '+name);
 }
 // Reusing dependencies is justified by semantic lock equality, not package name.
 if(spec.files['package-lock.json'])assert.deepEqual(await json(path.join(root,'package-lock.json')),await json(path.join(dir,'staged/package-lock.json')),'Dependency installation required');
 if(spec.files['package.json']){
  const old=await json(path.join(root,'package.json')),nextPackage=await json(path.join(dir,'staged/package.json'));
  for(const key of ['dependencies','devDependencies','optionalDependencies','peerDependencies','engines','type'])assert.deepEqual(old[key],nextPackage[key],'Dependency/runtime change requires installation: '+key);
 }
 return{root,spec,expected,alreadyApplied:false};
}

async function journals(profile,ids){
 const code=`import hashlib,json,sqlite3,sys
from pathlib import Path
result={}
for id in json.loads(sys.argv[2]):
 file=Path(sys.argv[1])/'bots'/id/'behavior.sqlite'
 assert file.is_file(),'Original Journal database missing'
 with sqlite3.connect(file.as_uri()+'?mode=ro',uri=True,timeout=5) as db:
  db.execute('pragma query_only=ON');db.execute('begin')
  sha=lambda v:hashlib.sha256(v.encode() if isinstance(v,str) else bytes(v)).hexdigest()
  rows=[(s,v,state,h,sha(raw) if raw is not None else None) for s,v,state,raw,h in db.execute('select scope,version,state,payload,sha from fishbot_readonly_journals order by scope')]
  blobs=[(s,h,sha(raw)) for s,h,raw in db.execute('select scope,sha,data from fishbot_readonly_journal_blobs order by scope,sha')]
  result[id]={'digest':sha(json.dumps([rows,blobs],ensure_ascii=False,separators=(',',':'))),'scopes':len(rows),'blobs':len(blobs)}
print(json.dumps(result))`;
 const result=await promisify(execFile)(profile.python,['-X','utf8','-c',code,path.resolve(profile.dataRoot),JSON.stringify(ids)],{timeout:20000,windowsHide:true,maxBuffer:1024*1024});
 return JSON.parse(result.stdout);
}

export async function applyRuntimeRelease(agent,profile,dir){
 dir=path.resolve(dir);const checked=await checkRuntimeRelease(profile,dir),{root,spec,expected}=checked;
 if(checked.alreadyApplied){const old=await json(path.join(dir,'result.json'));assert.equal(old.ok,true,'Installed files require runtime verification');assert.equal(old.commit,spec.commit);return old}
 const start=Date.now();
 const progress=value=>receipt(dir,'progress',{commit:spec.commit,ownerPid:process.pid,startedAt:start,updatedAt:Date.now(),...value});
 let bots,ids;
 const oldPrototype=Object.getPrototypeOf(agent.bilibili);let patchedPrototype,before,throughStop={};
 const {HeartbeatWriter}=await import(pathToFileURL(path.join(root,'dist-server/apps/node-agent/src/agent.js')).href);
 const prior=await json(path.join(agent.root,'agent-heartbeat.json'));
 const heartbeat=new HeartbeatWriter(path.join(agent.root,'agent-heartbeat.json'),{pid:process.pid,processStart:prior.processStart,startedAt:prior.startedAt});
 try{
  const result=await updateSharedWorkers(agent,{
   release:spec.commit,timeoutMs:120000,onProgress:progress,
   heartbeat:()=>heartbeat.write({maintenance:spec.commit}),
   apply:async()=>{
    await checkRuntimeRelease(profile,dir);
    bots=await agent.db.query("SELECT id,group_id FROM bots WHERE desired_state='running' AND deleted_at IS NULL ORDER BY id");
    ids=bots.map(b=>b.id);assert.ok(ids.length,'No running members to update');
    before=await journals(profile,ids);await receipt(dir,'journals-before',before);
    await requestFiles(dir,spec.commit,'install');
    await verifyProduction(root);await verifyFiles(root,spec.verifyFiles);assert.deepEqual(await journals(profile,ids),before,'Disk installation changed Journal');
    if(spec.files['dist-server/apps/node-agent/src/bilibili/executor.js']){
     // Reviewed method-only change: retain the original queues, credentials,
     // controllers and in-flight promises. Do not create a second executor.
     const module=await import(pathToFileURL(path.join(root,'dist-server/apps/node-agent/src/bilibili/executor.js')).href+'?release='+spec.commit);
     patchedPrototype=module.BilibiliExecutor.prototype;Object.setPrototypeOf(agent.bilibili,patchedPrototype);
    }
   },
   afterStop:async({group})=>{
    const members=bots.filter(b=>b.group_id===group.id).map(b=>b.id),stopped=await journals(profile,members);
    for(const id of members)assert.deepEqual(stopped[id],before[id],'AI stop changed committed Journal');
    Object.assign(throughStop,stopped);await receipt(dir,'journals-through-stop',throughStop);
   },
   rollback:async()=>{
    Object.setPrototypeOf(agent.bilibili,oldPrototype);
    await requestFiles(dir,spec.commit,'rollback');
    await verifyProduction(root);
   },
   verify:async({groups})=>{
    await verifyProduction(root);await verifyFiles(root,spec.verifyFiles);assert.deepEqual(throughStop,before,'Missing per-member stop proof');
    if(patchedPrototype)assert.equal(Object.getPrototypeOf(agent.bilibili),patchedPrototype);
    const loaded=[];
    for(const {group,health}of groups){
     assert.ok(health.ready&&!health.quiescing);
     const log=await fs.open(path.join(agent.root,'bots',group.id,'runtime.log'));
     let text;try{const stat=await log.stat(),buffer=Buffer.alloc(Math.min(stat.size,256*1024));await log.read(buffer,0,buffer.length,stat.size-buffer.length);text=buffer.toString('utf8')}finally{await log.close()}
     const line=text.split(/\r?\n/).filter(l=>l.includes('FISHBOT_RUNTIME_BUILD ')).at(-1);assert.ok(line,'Loaded runtime attestation absent');
     const build=JSON.parse(line.slice(line.indexOf('FISHBOT_RUNTIME_BUILD ')+22));
     for(const name of ['behavior','autonomous_runtime','readonly_journal','memory_reflection'])assert.ok(build[name],'Required loaded attestation absent: '+name);
     for(const [name,proof]of Object.entries(build)){
      assert.equal(proof.sourceMatchesLoaded,true,'Loaded source mismatch: '+name);
      const file='adapters/astrbot/'+name.split('.')[0]+'.py';
      assert.equal(proof.sourceSha,spec.verifyFiles[file],'Runtime release mismatch: '+name);
     }
     loaded.push({id:group.id,pid:health.pid,members:Object.keys(health.members||{}).length,attestedFunctions:Object.keys(build).length});
    }
    return{release:spec.commit,runtimeApplied:true,journalDigestsUnchanged:true,journalProofBoundary:'through-original-worker-stop',groups:loaded,bilibiliMethodsUpdated:!!patchedPrototype,verifiedFiles:Object.keys(spec.verifyFiles).length};
   }
  });
  const receiptValue={ok:true,commit:spec.commit,finishedAt:Date.now(),...result};await receipt(dir,'result',receiptValue);return receiptValue;
 }catch(error){
  await receipt(dir,'result',{ok:false,commit:spec.commit,finishedAt:Date.now(),errorType:error.name,error:String(error.message),causes:error.errors?.map(e=>({type:e.name,error:String(e.message)}))});throw error;
 }finally{await heartbeat.stop()}
}

async function operator(profile,dir){
 const record=await json(path.join(profile.stateRoot,'services/agent.process.json'));
 const checked=await checkRuntimeRelease(profile,dir),files=releaseFiles(checked,dir),handled=new Set();
 let fileTask=Promise.resolve(),fileBusy=false;
 const serveFiles=async()=>{
  if(fileBusy)return;fileBusy=true;
  try{
   const request=await json(path.join(dir,'file-request.json')).catch(error=>{if(error.code==='ENOENT')return null;throw error});
   if(!request||handled.has(request.id))return;
   handled.add(request.id);let error;
   try{
    const progress=await json(path.join(dir,'progress.json'));
    assert.equal(request.release,checked.spec.commit);assert.equal(request.ownerPid,record.pid);
    assert.equal(progress.ownerPid,record.pid);assert.equal(progress.commit,checked.spec.commit);
    assert.ok(request.operation==='install'&&progress.phase==='installing'||request.operation==='rollback'&&['installing','recovering'].includes(progress.phase));
    assert.ok(Date.now()<request.deadline,'Installer request expired');
    await files[request.operation]();
   }catch(failure){error=failure}
   await receipt(dir,'file-result',{id:request.id,ok:!error,error:error?String(error.message):undefined,errorType:error?.name});
  }finally{fileBusy=false}
 };
 const installer=setInterval(()=>{if(!fileBusy)fileTask=serveFiles().catch(error=>receipt(dir,'installer-error',{error:String(error.message)}))},50);
 const WebSocket=createRequire(path.join(profile.sourceRoot,'package.json'))('ws');
 let socket,sequence=0;const pending=new Map();
 const call=(method,params,timeoutMs=10000)=>new Promise((resolve,reject)=>{
  const id=++sequence,timer=setTimeout(()=>{pending.delete(id);reject(Error('Operator transport timed out; query the durable release receipt before retrying'))},timeoutMs);
  pending.set(id,{resolve:r=>{clearTimeout(timer);resolve(r)},reject:e=>{clearTimeout(timer);reject(e)}});socket.send(JSON.stringify({id,method,params}));
 });
 const evaluate=async(code,returnByValue=true,timeoutMs=10000)=>{
  const expression=`process.getBuiltinModule('vm').runInThisContext(${JSON.stringify(code)},{importModuleDynamically:process.getBuiltinModule('vm').constants.USE_MAIN_CONTEXT_DEFAULT_LOADER})`;
  const value=await call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue},timeoutMs);
  if(value.exceptionDetails)throw Error(value.exceptionDetails.exception?.description||value.exceptionDetails.text);
  return returnByValue?value.result.value:value.result;
 };
 try{
  process._debugProcess(record.pid);let url;
  for(let i=0;i<50;i++){
   try{const r=await fetch('http://127.0.0.1:9229/json/list',{signal:AbortSignal.timeout(500)});url=(await r.json())[0]?.webSocketDebuggerUrl;if(url)break}catch{}
   await new Promise(r=>setTimeout(r,100));
  }
  assert.ok(url?.startsWith('ws://127.0.0.1:9229/'),'Loopback inspector unavailable');
  socket=new WebSocket(url,{handshakeTimeout:5000});
  await new Promise((resolve,reject)=>{socket.once('open',resolve);socket.once('error',reject)});
  socket.on('message',data=>{const r=JSON.parse(String(data)),p=pending.get(r.id);if(p){pending.delete(r.id);r.error?p.reject(Error(r.error.message)):p.resolve(r.result)}});
  socket.on('close',()=>{for(const p of pending.values())p.reject(Error('Operator disconnected; query the durable release receipt'));pending.clear()});
  assert.equal(await evaluate('process.pid'),record.pid,'Agent PID changed');
  const proto=await evaluate(`import(${JSON.stringify(pathToFileURL(path.join(profile.sourceRoot,'dist-server/apps/node-agent/src/agent.js')).href)}).then(m=>m.NodeAgent.prototype)`,false);
  const objects=await call('Runtime.queryObjects',{prototypeObjectId:proto.objectId});
  const capture=await call('Runtime.callFunctionOn',{objectId:objects.objects.objectId,functionDeclaration:"function(){const a=this.filter(x=>x.db&&x.host&&x.closed===false);if(a.length!==1)throw Error('Ambiguous Agent');globalThis[Symbol.for('fishbot.runtime-release.capture')]=a[0];return true}",returnByValue:true});
  assert.ok(!capture.exceptionDetails,'Agent capture failed');
  const module=pathToFileURL(path.join(dir,'staged/scripts/runtime-release.mjs')).href;
  return await evaluate(`(async()=>{try{const a=globalThis[Symbol.for('fishbot.runtime-release.capture')];return await (await import(${JSON.stringify(module)})).applyRuntimeRelease(a,${JSON.stringify({sourceRoot:profile.sourceRoot,stateRoot:profile.stateRoot,dataRoot:profile.dataRoot,python:profile.python})},${JSON.stringify(dir)})}finally{delete globalThis[Symbol.for('fishbot.runtime-release.capture')]}})()`,true,600000);
 }finally{
  clearInterval(installer);await fileTask;
  if(socket?.readyState===WebSocket.OPEN){try{await evaluate("setTimeout(()=>process.getBuiltinModule('inspector').close(),200).unref();true",true,2000)}catch{}}
  socket?.terminate();
  for(const p of pending.values())p.reject(Error('Operator closed'));pending.clear();
 }
}

if(process.argv[1]&&path.resolve(process.argv[1])===path.resolve(import.meta.filename)){
 const [mode,profileFile,stage]=process.argv.slice(2);
 assert.ok(['plan','apply','status'].includes(mode)&&profileFile&&stage,'Usage: runtime-release.mjs plan|apply|status <private-profile.json> <staged-release-dir>');
 const dir=path.resolve(stage);
 if(mode==='status')console.log(JSON.stringify({progress:await json(path.join(dir,'progress.json')).catch(()=>null),result:await json(path.join(dir,'result.json')).catch(()=>null)}));
 else{
  const profile=await json(profileFile),checked=await checkRuntimeRelease(profile,dir);
  if(mode==='plan')console.log(JSON.stringify({commit:checked.spec.commit,alreadyApplied:checked.alreadyApplied,files:Object.keys(checked.spec.files).length}));
  else{const result=checked.alreadyApplied?await json(path.join(dir,'result.json')):await operator(profile,dir);assert.equal(result.ok,true);assert.equal(result.commit,checked.spec.commit);console.log(JSON.stringify(result))}
 }
}
