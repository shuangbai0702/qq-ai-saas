import test from 'node:test';import assert from 'node:assert/strict';
import os from 'node:os';import path from 'node:path';import {randomBytes} from 'node:crypto';
import {mkdtemp,mkdir,writeFile,rm,readdir,utimes,readFile} from 'node:fs/promises';
import {mediaAdmission,managedProcess} from '../apps/node-agent/src/media/lifecycle.js';
import {VideoEvidence,mediaAvailable} from '../apps/node-agent/src/bilibili/video-evidence.js';
import {encrypt} from '../packages/shared/security.js';import {BiliWeb} from '../apps/node-agent/src/bilibili/web-adapter.js';import {BilibiliConfigSchema} from '../packages/contracts/bilibili.js';

async function guardFixture(t:any){const root=await mkdtemp(path.join(os.tmpdir(),'media-guard-'));t.after(()=>rm(root,{recursive:true,force:true}));const guard=path.join(root,'guard'),cgroup=path.join(root,'delegated');await mkdir(guard);await mkdir(cgroup);await writeFile(path.join(cgroup,'cgroup.procs'),'');return{root,guard,cgroup}}
async function arm(guard:string){await writeFile(path.join(guard,'guard-heartbeat'),'');await writeFile(path.join(guard,'armed'),'')}
test('both platforms require a fresh armed Guard and reject disabled or expired ownership',async t=>{
 const{guard,cgroup}=await guardFixture(t);if(process.platform==='win32')t.mock.method(os,'freemem',()=>1024*1048576);
 await assert.rejects(()=>mediaAdmission(guard,cgroup),(e:any)=>e.code==='guard_unavailable');await arm(guard);await mediaAdmission(guard,cgroup);
 await rm(path.join(guard,'armed'));await assert.rejects(()=>mediaAdmission(guard,cgroup),(e:any)=>e.code==='guard_unavailable');await arm(guard);
 const stale=new Date(Date.now()-11000);await utimes(path.join(guard,'guard-heartbeat'),stale,stale);await assert.rejects(()=>mediaAdmission(guard,cgroup),(e:any)=>e.code==='guard_unavailable');await arm(guard);
 await writeFile(path.join(guard,'disabled'),'revoked');await assert.rejects(()=>mediaAdmission(guard,cgroup),(e:any)=>e.code==='guard_disabled');
});
test('Windows admission enforces the 576 MiB reserve including the exact threshold',{skip:process.platform!=='win32'},async t=>{
 const{guard,cgroup}=await guardFixture(t);await arm(guard);let available=575*1048576;t.mock.method(os,'freemem',()=>available);
 await assert.rejects(()=>mediaAdmission(guard,cgroup),(e:any)=>e.code==='resource_wait');available=576*1048576;await mediaAdmission(guard,cgroup);
});
test('media availability fails closed on Windows when binaries exist but Guard is missing',{skip:process.platform!=='win32'},async t=>{
 const{root,guard}=await guardFixture(t);await writeFile(path.join(root,'media-resources.json'),JSON.stringify({guardRoot:guard,ffmpeg:process.execPath,ffprobe:process.execPath}));assert.equal(await mediaAvailable(root),false);
});
for(const revoke of ['armed','disabled','pressure'])test(`Windows running download cancels and cleans its workspace after ${revoke} changes`,{skip:process.platform!=='win32',timeout:10000},async t=>{
 const{root,guard}=await guardFixture(t);await arm(guard);let available=1024*1048576;t.mock.method(os,'freemem',()=>available);await writeFile(path.join(root,'media-resources.json'),JSON.stringify({guardRoot:guard}));
 const key=randomBytes(32),db:any={query:async(sql:string)=>sql.includes('bot_config_revisions')?[{config:{baseUrl:'https://provider.invalid',model:'fixture'}}]:[{ciphertext:encrypt(key,'tenant:bot','fixture-key')}]};
 const web=new BiliWeb();web.video=async()=>({pages:[{page:1,cid:1,duration:4}]});web.get=async()=>({dash:{video:[{baseUrl:'https://cdn.bilivideo.com/fixture'}]}});
 let entered!:()=>void;const started=new Promise<void>(resolve=>entered=resolve);
 const service=new VideoEvidence(db,root,key,web,async()=>{throw new Error('model must not be invoked')},{download:async(_url,file,_budget,signal)=>{await writeFile(file,'partial');entered();await new Promise<void>((_resolve,reject)=>{if(signal.aborted)reject(signal.reason);else signal.addEventListener('abort',()=>reject(signal.reason),{once:true})})}});
 const pending=service.analyze({id:'guard-test',bot_id:'bot',tenant_id:'tenant',config_revision:1,args:{bvid:'BV1xx411c7mD',part:1,start:0,seconds:4,question:'fixture'}},{config:BilibiliConfigSchema.parse({enabled:true,scopes:['media.analyze'],model:{provider:'bot'}})},undefined,new AbortController().signal);
 const rejected=assert.rejects(()=>pending,(e:any)=>e.code==='resource_wait');await started;
 if(revoke==='armed')await rm(path.join(guard,'armed'));else if(revoke==='disabled')await writeFile(path.join(guard,'disabled'),'revoked');else available=575*1048576;
 await rejected;assert.deepEqual(await readdir(path.join(root,'media-tasks')),[]);
});
test('Windows media Job drains a descendant after its native root exits successfully',{skip:process.platform!=='win32',timeout:20000},async t=>{
 const{root}=await guardFixture(t),marker=path.join(root,'descendant');
 const script=`const{spawn}=require('child_process');const child=spawn(process.execPath,['-e',\"require('fs').writeFileSync(process.argv[1],String(process.pid));setInterval(()=>{},1000)\",process.argv[1]],{stdio:'ignore'});child.unref();const timer=setInterval(()=>{if(require('fs').existsSync(process.argv[1])){clearInterval(timer);process.exit(0)}},10)`;
 await managedProcess(process.execPath,['-e',script,marker],{cwd:root,signal:new AbortController().signal,timeoutMs:15000});
 const pid=Number(await readFile(marker,'utf8'));assert.ok(pid);assert.throws(()=>process.kill(pid,0));
});
test('Windows media cancellation joins the native root and its descendant',{skip:process.platform!=='win32',timeout:20000},async t=>{
 const{root}=await guardFixture(t),marker=path.join(root,'tree'),controller=new AbortController();
 const script=`const{spawn}=require('child_process');const child=spawn(process.execPath,['-e',\"require('fs').writeFileSync(process.argv[1],String(process.pid));setInterval(()=>{},1000)\",process.argv[1]+'.child'],{stdio:'ignore'});require('fs').writeFileSync(process.argv[1],String(process.pid));setInterval(()=>{},1000)`;
 const pending=managedProcess(process.execPath,['-e',script,marker],{cwd:root,signal:controller.signal,timeoutMs:15000}),rejected=assert.rejects(()=>pending);let pids:number[]=[];
 try{const end=Date.now()+10000;while(Date.now()<end){try{pids=[Number(await readFile(marker,'utf8')),Number(await readFile(marker+'.child','utf8'))];break}catch{await new Promise(r=>setTimeout(r,20))}}assert.equal(pids.length,2)}finally{controller.abort();await rejected}
 for(const pid of pids){assert.ok(pid);assert.throws(()=>process.kill(pid,0))}
});
