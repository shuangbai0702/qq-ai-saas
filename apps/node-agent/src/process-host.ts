import {spawn,execFile, type ChildProcess} from 'node:child_process';import {mkdir,readFile,writeFile,appendFile,rm,stat,readdir} from 'node:fs/promises';import path from 'node:path';
import {redact} from '../../../packages/shared/security.js';
import {StringDecoder} from 'node:string_decoder';
export class ProcessHost {
  children=new Map<string,ChildProcess>(); secrets=new Map<string,string[]>(); logWrites=new Map<string,Promise<void>>();
  constructor(public root:string,public python:string){}
  private inspection?:Map<string,any>;
  private lastMemorySweep=0;
  private memorySweep?:Promise<void>;
  memoryStatus=new Map<string,unknown>();
  async maintainMemory(records:any[]){
    if(this.memorySweep)return this.memorySweep;
    if(!records.length){this.memoryStatus.clear();return}
    if(Date.now()-this.lastMemorySweep<30000)return;
    this.lastMemorySweep=Date.now();
    this.memorySweep=new Promise<void>(resolve=>{
      const child=execFile(this.python,[path.resolve('scripts/runtime_memory.py'),...(process.env.FISHBOT_MEMORY_POLICY==='off'?['--disabled']:[])],{windowsHide:true,timeout:12000,maxBuffer:1024*1024},(error,stdout)=>{
        try{if(error)throw error;const rows=JSON.parse(stdout);this.memoryStatus=new Map(rows.map((v:any)=>[`${v.id}:${v.kind}`,v]))}catch{this.memoryStatus=new Map(records.map(v=>[`${v.id}:${v.kind}`,{strategy:'system-managed',decision:'skipped',reason:'maintenance-unavailable'}]))}
        resolve();
      });child.stdin!.end(JSON.stringify(records));
    }).finally(()=>{this.memorySweep=undefined});
    return this.memorySweep;
  }
  private helper(args:string[],input?:string){return new Promise<string>((resolve,reject)=>{const child=execFile(this.python,[path.resolve('scripts/process_control.py'),...args],{windowsHide:true,timeout:15000,maxBuffer:1024*1024},(error,stdout)=>error?reject(error):resolve(stdout));if(input)child.stdin!.end(input)})}
  // One short-lived interpreter for an entire health sweep, not one per probe.
  // The cache never spans a command, start/stop, or another health sweep.
  async beginInspection(){const records=[];for(const id of await readdir(path.join(this.root,'bots')).catch(()=>[])){for(const kind of ['ai','protocol']){const rec=await this.record(id,kind);if(rec)records.push({id,kind,pid:rec.pid,created:rec.created,root:path.join(this.root,'bots',id)})}}const values=JSON.parse(await this.helper(['batch'],JSON.stringify(records)));await this.maintainMemory(records);this.inspection=new Map(values.map((v:any)=>[`${v.id}:${v.kind}`,v.valid?{...v,memoryPolicy:this.memoryStatus.get(`${v.id}:${v.kind}`)}:null]))}
  endInspection(){this.inspection=undefined}
  file(id:string,kind:string){return path.join(this.root,'bots',id,`${kind}.process.json`)}
  async record(id:string,kind:string){try{return JSON.parse(await readFile(this.file(id,kind),'utf8'))}catch{return null}}
  async inspect(id:string,kind:string){if(this.inspection)return this.inspection.get(`${id}:${kind}`)||null;const rec=await this.record(id,kind);if(!rec)return null;const v=JSON.parse(await this.helper(['inspect',String(rec.pid),path.join(this.root,'bots',id),String(rec.created??'unknown')]));return v.valid?{...v,memoryPolicy:this.memoryStatus.get(`${id}:${kind}`)}:null}
  async start(id:string,kind:string,executable:string,args:string[],cwd:string,env:NodeJS.ProcessEnv,keys:string[]=[]){
    this.endInspection();
    if(await this.inspect(id,kind))return;
    await mkdir(path.join(this.root,'bots',id),{recursive:true});
    // Each child owns only its current credentials. Never retain old rotated
    // keys in a host-wide map after the child is gone.
    const childKeys=[...keys];
    const safeEnv:NodeJS.ProcessEnv={};
    for(const [k,v] of Object.entries(env))if(/^(PATH|PATHEXT|SYSTEMROOT|WINDIR|COMSPEC|HOME|USERPROFILE|LOCALAPPDATA|APPDATA|PROGRAMDATA|TEMP|TMP|TMPDIR|LANG|LC_.*|SSL_CERT_FILE|SSL_CERT_DIR|LD_LIBRARY_PATH|PYTHON.*|ASTRBOT_ROOT|FISHBOT_MODEL_KEY|FISHBOT_ASTRBOT_SLIM|FISHBOT_MODEL_ALLOWED_PRIVATE_ORIGINS|NAPCAT_PLATFORM_MANIFEST|NODE_EXTRA_CA_CERTS)$/i.test(k))safeEnv[k]=v;
    const child=spawn(executable,args,{cwd,env:safeEnv,windowsHide:true,stdio:['ignore','pipe','pipe']});
    await new Promise<void>((resolve,reject)=>{child.once('spawn',resolve);child.once('error',reject)});
    if(process.platform==='linux'&&process.env.FISHBOT_CGROUP_ROOT){
      try{if(!/^[a-zA-Z0-9-]+$/.test(id))throw new Error('Invalid cgroup ID');const group=path.join(path.resolve(process.env.FISHBOT_CGROUP_ROOT),id);await mkdir(group,{recursive:true});await writeFile(path.join(group,'memory.max'),String(Number(process.env.BOT_MEMORY_MAX_BYTES||1073741824)));await writeFile(path.join(group,'pids.max'),'128');await writeFile(path.join(group,'cpu.max'),process.env.BOT_CPU_MAX||'100000 100000');await writeFile(path.join(group,'cgroup.procs'),String(child.pid))}catch(e){child.kill('SIGKILL');throw new Error('无法应用已要求的cgroup配额：'+(e as Error).message)}
    }
    this.children.set(`${id}:${kind}`,child);
    let queued=0;
    const enqueue=(line:string)=>{if(queued>=32)return;queued++;const text=redact(line,childKeys);const file=path.join(this.root,'bots',id,'runtime.log');const pending=(this.logWrites.get(id)||Promise.resolve()).then(async()=>{try{if((await stat(file)).size>2*1024*1024)await writeFile(file,'[platform] log rotated\n')}catch{};await appendFile(file,`[${kind}] ${text}`)}).catch(()=>{}).finally(()=>{queued--;if(this.logWrites.get(id)===pending)this.logWrites.delete(id)});this.logWrites.set(id,pending)};
    // Redact complete lines, including secrets split over pipe chunks. Discard
    // oversized lines entirely rather than exposing a truncated secret suffix.
    const streamLogger=()=>{const decoder=new StringDecoder('utf8');let line='',discard=false;return{data:(buf:Buffer)=>{for(const part of decoder.write(buf).split(/(?<=\n)/)){if(!discard)line+=part;if(line.length>65536){line='';discard=true}if(part.endsWith('\n')){if(!discard)enqueue(line);line='';discard=false}}},end:()=>{if(!discard&&line)enqueue(line+decoder.end()+'\n')}}};
    const stdout=streamLogger(),stderr=streamLogger();child.stdout?.on('data',stdout.data);child.stderr?.on('data',stderr.data);
    child.once('close',code=>{stdout.end();stderr.end();if(this.children.get(`${id}:${kind}`)===child)this.children.delete(`${id}:${kind}`);enqueue(`process exited (${code})\n`)});
    await writeFile(this.file(id,kind),JSON.stringify({pid:child.pid,kind,startedAt:new Date().toISOString()}),{mode:0o600});
    const identity=await this.inspect(id,kind);if(identity)await writeFile(this.file(id,kind),JSON.stringify({pid:child.pid,kind,created:identity.created,startedAt:new Date().toISOString()}),{mode:0o600});
  }
  async stop(id:string,kind:string){
    this.endInspection();
    const rec=await this.record(id,kind);if(!rec)return;
    await this.helper(['stop',String(rec.pid),path.join(this.root,'bots',id),String(rec.created??'unknown')]);
    await rm(this.file(id,kind),{force:true});this.children.delete(`${id}:${kind}`);
  }
}
