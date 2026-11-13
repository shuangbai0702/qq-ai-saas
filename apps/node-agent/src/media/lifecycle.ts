/** Bounded media primitives with common Guard gates and owned OS process trees. */
import {spawn} from 'node:child_process';
import os from 'node:os';
import {createWriteStream} from 'node:fs';
import {mkdtemp,mkdir,rm,stat,readFile,access,rmdir} from 'node:fs/promises';
import {Transform,Readable} from 'node:stream';
import {pipeline} from 'node:stream/promises';
import {request} from 'node:https';
import {lookup} from 'node:dns/promises';
import {isIP} from 'node:net';
import path from 'node:path';
import {BiliError} from '../bilibili/web-adapter.js';

export function publicAddress(ip:string){
 if(isIP(ip)===6)return /^[23][0-9a-f]{3}:/i.test(ip)&&!/^2001:db8:/i.test(ip);
 if(isIP(ip)!==4)return false;
 const[a,b]=ip.split('.').map(Number);return a!==0&&a!==10&&a!==127&&a<224&&!(a===169&&b===254)&&!(a===172&&b>=16&&b<=31)&&!(a===192&&[0,168].includes(b))&&!(a===100&&b>=64&&b<=127)&&!(a===198&&[18,19,51].includes(b))&&!(a===203&&b===0);
}
export function mediaUrl(value:string){const u=new URL(value);if(u.protocol!=='https:'||u.port&&u.port!=='443'||u.username||u.password||!/(^|\.)(bilivideo\.com|bilivideo\.cn|hdslb\.com)$/.test(u.hostname))throw new BiliError('invalid_media_source','媒体来源不允许');return u}
export async function downloadMedia(url:string,file:string,budget:{remaining:number;received:number},signal:AbortSignal,redirects=0):Promise<void>{
 if(signal.aborted)throw signal.reason;const u=mediaUrl(url),addresses=await lookup(u.hostname,{all:true});if(!addresses.length||addresses.some(a=>!publicAddress(a.address)))throw new BiliError('private_media_address','媒体域名解析到非公网地址');
 // Pin the validated address while preserving hostname/TLS SNI. No cookies or
 // account headers ever go to CDN; every redirect is separately validated.
 const address=addresses[0];
 const response=await new Promise<import('node:http').IncomingMessage>((resolve,reject)=>{
  const req=request(u,{signal,headers:{Referer:'https://www.bilibili.com/','User-Agent':'Mozilla/5.0'},lookup:(_hostname,options:any,cb:any)=>options.all?cb(null,addresses):cb(null,address.address,address.family)},resolve);req.on('error',reject);req.end();
 });
 if([301,302,303,307,308].includes(response.statusCode||0)){response.destroy();if(redirects>=3||!response.headers.location)throw new BiliError('media_redirect','媒体跳转超过限制');return downloadMedia(new URL(response.headers.location,u).href,file,budget,signal,redirects+1)}
 if(response.statusCode!==200){response.destroy();throw new BiliError('media_download_failed','媒体下载被拒绝')}
 const length=Number(response.headers['content-length']);if(Number.isFinite(length)&&length>budget.remaining){response.destroy();throw new BiliError('media_too_large','媒体超过任务下载额度')}
 try{await pipeline(response,new Transform({transform(chunk,_enc,done){budget.remaining-=chunk.length;budget.received+=chunk.length;if(budget.remaining<0)done(new BiliError('media_too_large','媒体超过累计下载额度'));else done(null,chunk)}}),createWriteStream(file,{flags:'wx',mode:0o600}),{signal})}
 catch(e){await rm(file,{force:true});throw e}
}
export async function streamToFile(stream:Readable,file:string,max:number,signal:AbortSignal){
 let bytes=0;try{await pipeline(stream,new Transform({transform(chunk,_e,cb){bytes+=chunk.length;if(bytes>max)cb(new BiliError('media_too_large','下载超过额度'));else cb(null,chunk)}}),createWriteStream(file,{flags:'wx',mode:0o600}),{signal});return bytes}catch(e){await rm(file,{force:true});throw e}
}
export async function mediaAdmission(guardRoot:string,cgroupRoot:string){
 if(!['linux','win32'].includes(process.platform))throw new BiliError('media_platform_unsupported','媒体任务不支持当前平台');
 let heartbeat;try{heartbeat=await stat(path.join(guardRoot,'guard-heartbeat'));await access(path.join(guardRoot,'armed'))}catch{throw new BiliError('guard_unavailable','内存Guard尚未就绪')}
 if(!heartbeat.isFile()||Date.now()-heartbeat.mtimeMs>10000||heartbeat.mtimeMs>Date.now()+5000)throw new BiliError('guard_unavailable','内存Guard心跳过期');
 try{await access(path.join(guardRoot,'disabled'));throw new BiliError('guard_disabled','内存Guard已禁用')}catch(e){if((e as NodeJS.ErrnoException).code!=='ENOENT')throw e}
 const available=process.platform==='win32'?os.freemem():Number(/^MemAvailable:\s+(\d+)/m.exec(await readFile('/proc/meminfo','utf8'))?.[1]||0)*1024;
 if(available<576*1024*1024)throw new BiliError('resource_wait','可用内存不足，媒体任务暂缓');
 if(process.platform!=='linux')return;
 if(!path.isAbsolute(cgroupRoot)||cgroupRoot==='/sys/fs/cgroup')throw new BiliError('media_cgroup_required','必须配置专属媒体cgroup委派目录');
 await access(path.join(cgroupRoot,'cgroup.procs'),2);
}
export async function managedProcess(executable:string,args:string[],options:{cwd:string;signal:AbortSignal;timeoutMs:number;cgroupRoot?:string;jobId?:string;maxOutput?:number}){
 if(options.signal.aborted)throw options.signal.reason;
 if(process.platform==='linux'&&(!options.cgroupRoot||!options.jobId))throw new BiliError('media_cgroup_required','媒体任务缺少专属进程归属，拒绝启动');
 let ownedGroup:string|undefined;
 if(process.platform==='linux'){
  if(!path.isAbsolute(options.cgroupRoot!)||options.cgroupRoot==='/sys/fs/cgroup'||!/^[-a-zA-Z0-9]{1,80}$/.test(options.jobId!))throw new BiliError('media_cgroup_required','媒体进程归属无效');
  const group=path.join(options.cgroupRoot!,options.jobId!);
  // Exclusive mkdir establishes ownership. If another task already owns this
  // ID, fail before spawn and never enter its cleanup/kill path.
  await mkdir(group);ownedGroup=group;
 }
 if(!['linux','win32'].includes(process.platform))throw new BiliError('media_platform_unsupported','媒体任务不支持当前平台');
 const command=process.platform==='linux'?process.execPath:path.resolve(process.env.ASTRBOT_PYTHON||'references/astrbot/.venv/Scripts/python.exe');
 const argv=process.platform==='linux'?[process.env.FISHBOT_MEDIA_LAUNCHER||path.resolve('scripts/media-launcher.mjs'),options.cgroupRoot!,options.jobId!,executable,...args]:['-B',path.resolve('scripts/media-windows-job.py'),path.resolve(executable),...args];
 const signal=AbortSignal.any([options.signal,AbortSignal.timeout(options.timeoutMs)]),child=spawn(command,argv,{cwd:options.cwd,detached:process.platform==='linux',windowsHide:true,stdio:['pipe','pipe','pipe'],env:{PATH:process.env.PATH,SystemRoot:process.env.SystemRoot,PYTHONIOENCODING:'utf-8'}});
 child.stdin.on('error',()=>{});
 let output='',errors='',closed=false,overflow=false,killer:NodeJS.Timeout|undefined;
 const kill=()=>{if(closed||!child.pid)return;if(process.platform==='win32')child.stdin.end();else{try{process.kill(-child.pid,'SIGTERM')}catch{}}killer??=setTimeout(()=>{if(!closed&&child.pid)try{if(process.platform==='win32')child.kill('SIGKILL');else process.kill(-child.pid,'SIGKILL')}catch{}},2000);killer.unref()};
 const max=options.maxOutput??65536;
 child.stdout.on('data',chunk=>{if(Buffer.byteLength(output)+chunk.length>max){overflow=true;kill();return}output+=chunk.toString('utf8')});
 child.stderr.on('data',chunk=>{errors=(errors+chunk.toString('utf8')).slice(-16384)});
 signal.addEventListener('abort',kill,{once:true});if(signal.aborted)kill();
 try{
  const code=await new Promise<number|null>((resolve,reject)=>{child.once('error',reject);child.once('close',resolve)});closed=true;
  if(signal.aborted)throw signal.reason;if(overflow||code!==0)throw new BiliError('media_process_failed','媒体子进程失败或输出过大；未生成已确认内容');return{stdout:output,stderr:errors,pid:child.pid};
 }finally{
  closed=true;signal.removeEventListener('abort',kill);if(killer)clearTimeout(killer);
  if(ownedGroup){
   const group=ownedGroup;
   // Only our unique job cgroup is eligible. Never kill a fleet/tenant parent.
   const procs=await readFile(path.join(group,'cgroup.procs'),'utf8').catch(()=>null);
   if(procs?.trim()){
    const {writeFile}=await import('node:fs/promises');await writeFile(path.join(group,'cgroup.kill'),'1');
    for(let i=0;i<50;i++){if(!(await readFile(path.join(group,'cgroup.procs'),'utf8')).trim())break;await new Promise(r=>setTimeout(r,20))}
   }
   if(procs!==null)await rmdir(group);
  }
 }
}
export async function mediaWorkspace<T>(root:string,run:(dir:string)=>Promise<T>){
 const base=path.resolve(root),dir=await mkdtemp(path.join(base,'job-'));try{return await run(dir)}finally{if(path.dirname(dir)!==base)throw new Error('Invalid media workspace ownership');await rm(dir,{recursive:true,force:true})}
}
