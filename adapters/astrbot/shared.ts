import path from 'node:path';import {mkdir,readFile,writeFile} from 'node:fs/promises';import {randomUUID,randomBytes} from 'node:crypto';
import net from 'node:net';
import type {RuntimeContext,Health} from '../../packages/contracts/index.js';import type {Sql} from '../../packages/database/index.js';import {ProcessHost} from '../../apps/node-agent/src/process-host.js';import {AstrBotAdapter} from './index.js';
async function bindHealthPort(port:number):Promise<number|undefined>{
 return new Promise((resolve,reject)=>{const server=net.createServer();server.once('error',(error:NodeJS.ErrnoException)=>{if(error.code==='EADDRINUSE'||error.code==='EACCES')resolve(undefined);else reject(error)});server.listen({host:'127.0.0.1',port,exclusive:true},()=>{const address=server.address() as net.AddressInfo;server.close(error=>error?reject(error):resolve(address.port))})});
}
async function availableHealthPort(db:Sql,preferred?:number,groupId=''){
 // Instance blocks include the AI health endpoint and NapCat maintenance API,
 // even while the corresponding process is offline and its socket is free.
 const reserved=new Set((await db.query('SELECT health_port AS port FROM runtime_groups WHERE id<>$1 UNION SELECT ai_port AS port FROM instances UNION SELECT ai_port+1 AS port FROM instances UNION SELECT protocol_port AS port FROM instances UNION SELECT protocol_port+1 AS port FROM instances',[groupId])).map(row=>Number(row.port)));
 if(preferred&&!reserved.has(preferred)&&await bindHealthPort(preferred))return preferred;
 // Let each OS avoid its own excluded/reserved ranges before any fallback scan.
 const chosen=await bindHealthPort(0);if(chosen&&!reserved.has(chosen))return chosen;
 // An OS-selected ephemeral port can still belong to a stopped DB instance.
 // Scan every remaining nonprivileged candidate once; no random retry limit.
 for(let slot=0;slot<64512;slot++){const port=1024+((38976+slot)%64512);if(port===preferred||port===chosen||reserved.has(port))continue;if(await bindHealthPort(port))return port;}
 throw new Error('共享运行组没有可绑定的本机健康端口');
}
export class SharedAstrBotAdapter {
 constructor(private db:Sql,private host:ProcessHost,private dedicated:AstrBotAdapter,private source:string,private python:string,_context:(bot:any)=>Promise<RuntimeContext>,private canAttach?:(ctx:RuntimeContext)=>Promise<boolean>){}
 async assign(id:string){return this.db.transaction(async tx=>{await tx.query('SELECT id FROM bots WHERE id=$1 FOR UPDATE',[id]);const [bot]=await tx.query('SELECT group_id FROM bots WHERE id=$1',[id]);if(bot.group_id)return(await tx.query('SELECT * FROM runtime_groups WHERE id=$1',[bot.group_id]))[0];
  // Single-node Agent serializes assignment. Lock the chosen group for PG too.
  let [group]=await tx.query('SELECT g.* FROM runtime_groups g WHERE (SELECT count(*) FROM group_members m WHERE m.group_id=g.id)<g.capacity ORDER BY g.created_at LIMIT 1 FOR UPDATE');
  if(!group){const gid='group-'+randomUUID();const capacity=Number(process.env.SHARED_GROUP_CAPACITY||8);if(![1,2,4,8].includes(capacity))throw new Error('SHARED_GROUP_CAPACITY must be 1,2,4,8');const port=await availableHealthPort(tx);[group]=await tx.query('INSERT INTO runtime_groups(id,capacity,health_port,token) VALUES($1,$2,$3,$4) RETURNING *',[gid,capacity,port,randomBytes(32).toString('hex')]);}
  await tx.query('UPDATE bots SET group_id=$2 WHERE id=$1',[id,group.id]);await tx.query('INSERT INTO group_members(bot_id,group_id) VALUES($1,$2)',[id,group.id]);return group;});}
 async group(id:string,db:Sql=this.db){return(await db.query('SELECT g.* FROM runtime_groups g JOIN group_members m ON m.group_id=g.id WHERE m.bot_id=$1',[id]))[0]}
 async request(group:any,route:string,method='GET',body?:unknown,memberGeneration?:number,timeoutMs=20000){const r=await fetch(`http://127.0.0.1:${group.health_port}${route}`,{method,headers:{Authorization:'Bearer '+group.token,'X-Group-Generation':String(group.generation),'X-Member-Generation':String(memberGeneration??0),'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined,signal:AbortSignal.timeout(timeoutMs)});if(!r.ok)throw new Error(`共享运行组请求失败 HTTP ${r.status}`);return r.json() as Promise<any>}
 async attach(group:any,ctx:RuntimeContext){await this.dedicated.provision(ctx);const config=JSON.parse(await readFile(path.join(ctx.root,'ai/data/cmd_config.json'),'utf8'));if(this.canAttach&&!await this.canAttach(ctx))throw Object.assign(new Error('共享成员已离线或生命周期变化'),{superseded:true});await this.request(group,'/members/'+ctx.botId,'PUT',{generation:ctx.generation,revision:ctx.revision,systemChangePermitId:ctx.systemChangePermitId,prompt:ctx.config.systemPrompt,key:ctx.apiKey,config,groups:ctx.config.groups,uin:ctx.expectedUin,platform:config.platform[0],runtimeRoot:ctx.root,behaviorConfig:ctx.config,botId:ctx.botId,tenantId:ctx.tenantId});await this.db.query("UPDATE group_members SET generation=$2,applied_revision=$3,desired_state='running',observed_state='ready' WHERE bot_id=$1",[ctx.botId,ctx.generation,ctx.revision]);}
 async start(ctx:RuntimeContext){let group=await this.assign(ctx.botId);await this.db.query("UPDATE group_members SET desired_state='running',generation=$2 WHERE bot_id=$1",[ctx.botId,ctx.generation]);
  this.host.endInspection();
  if(!await this.host.inspect(group.id,'ai')){
   let launch=false;
   group=await this.db.transaction(async tx=>{
    const [current]=await tx.query('SELECT * FROM runtime_groups WHERE id=$1 FOR UPDATE',[group.id]);
    this.host.endInspection();if(await this.host.inspect(current.id,'ai'))return current;
    const port=await availableHealthPort(tx,current.health_port,current.id);
    // Binding is asynchronous; a newly owned worker wins over port repair.
    this.host.endInspection();if(await this.host.inspect(current.id,'ai'))return current;
    const [starting]=await tx.query("UPDATE runtime_groups SET health_port=$2,generation=generation+1,desired_state='running',observed_state='starting' WHERE id=$1 RETURNING *",[current.id,port]);launch=true;return starting;
   });
   if(launch){const root=path.join(this.host.root,'bots',group.id,'ai');await mkdir(path.join(root,'data'),{recursive:true});
   await writeFile(path.join(root,'data/cmd_config.json'),JSON.stringify({config_version:5,dashboard:{enable:false},platform:[],provider:[],provider_sources:[],plugin_set:[],disable_builtin_commands:true}),{mode:0o600});await writeFile(path.join(root,'group.json'),JSON.stringify({token:group.token,healthPort:group.health_port,generation:group.generation,capacity:group.capacity}),{mode:0o600});
   await this.host.start(group.id,'ai',this.python,[path.resolve('adapters/astrbot/shared-worker.py'),this.source,root],root,{...process.env,PYTHONIOENCODING:'utf-8',PYTHONUNBUFFERED:'1'},[group.token]);
   const deadline=Date.now()+110000;let ready=false;while(Date.now()<deadline){try{const h:any=await this.request(group,'/health','GET',undefined,undefined,Math.min(1500,Math.max(1,deadline-Date.now())));if(h.ready&&h.generation===group.generation){ready=true;break}}catch{}await new Promise(r=>setTimeout(r,100));}if(!ready)throw new Error('共享AstrBot启动超时');
   }
  }
  // Each member is admitted by the Agent only after its own fresh protocol
  // and identity check. Persisted desired_state is not online evidence: after
  // a worker restart peers rejoin through that same path, never bulk preload.
  try{await this.attach(group,ctx)}catch(error){if((error as any).superseded)await this.stop(ctx.botId);throw error}
  await this.db.query("UPDATE runtime_groups SET observed_state='ready',desired_state='running' WHERE id=$1",[group.id]);
 }
 async recoverReadiness(ctx:RuntimeContext){
  const group=await this.group(ctx.botId);if(!group)return this.start(ctx);
  this.host.endInspection();const owner=await this.host.inspect(group.id,'ai');if(!owner)return this.start(ctx);
  let health:any;try{health=await this.request(group,'/health','GET',undefined,undefined,1500)}catch{/* A timed-out control plane is rechecked against current PID ownership below. */}
  if(health?.ready){
   if(health.generation!==group.generation)throw Object.assign(new Error('共享组代次已变化，拒绝替换健康进程'),{superseded:true});
   const member=health.members?.[ctx.botId];if(member?.generation===ctx.generation&&member?.revision===ctx.revision)return;
   // Healthy group: only repair this member; peers keep their live work.
   return this.start(ctx);
  }
  const currentGroup=await this.group(ctx.botId);const [bot]=await this.db.query('SELECT generation,revision,desired_state,deleted_at FROM bots WHERE id=$1',[ctx.botId]);
  if(!currentGroup||currentGroup.id!==group.id||currentGroup.generation!==group.generation||!bot||bot.deleted_at||bot.desired_state!=='running'||bot.generation!==ctx.generation||bot.revision!==ctx.revision)throw Object.assign(new Error('共享组恢复已被新生命周期替代'),{superseded:true});
  this.host.endInspection();const current=await this.host.inspect(group.id,'ai');
  if(!current||current.pid!==owner.pid||current.created!==owner.created)throw Object.assign(new Error('共享组进程所有权已变化'),{superseded:true});
  await this.host.stop(group.id,'ai','shared-readiness-timeout',{pid:owner.pid,created:owner.created});
  // Existing start owns generation advancement and reattaches this member.
  // Peers recover independently after their own live protocol/identity check.
  return this.start(ctx);
 }
 async stop(id:string,permanent=false,db:Sql=this.db){const group=await this.group(id,db);if(!group)return;const [bot]=await db.query('SELECT generation FROM bots WHERE id=$1',[id]);if(await this.host.inspect(group.id,'ai'))await this.request(group,'/members/'+id+(permanent?'?permanent=true':''),'DELETE',undefined,bot?.generation);
  // Release a runtime slot only after the worker has drained the old owner.
  // Per-bot root/Work/Journal survive ordinary offline unload and group moves.
  await db.query('DELETE FROM group_members WHERE bot_id=$1',[id]);await db.query('UPDATE bots SET group_id=NULL WHERE id=$1',[id]);
  const [active]=await db.query("SELECT count(*)::int AS count FROM group_members WHERE group_id=$1 AND desired_state='running'",[group.id]);if(!active.count){await this.host.stop(group.id,'ai');await db.query("UPDATE runtime_groups SET desired_state='stopped',observed_state='stopped' WHERE id=$1",[group.id]);}}
 async probe(ctx:RuntimeContext):Promise<Health>{const group=await this.group(ctx.botId);if(!group)return{alive:false,ready:false};const p=await this.host.inspect(group.id,'ai');if(!p){await this.db.query("UPDATE runtime_groups SET observed_state='failed',health=$2 WHERE id=$1",[group.id,JSON.stringify({alive:false})]);await this.db.query("UPDATE group_members SET observed_state='failed' WHERE group_id=$1 AND desired_state='running'",[group.id]);return{alive:false,ready:false,detail:'共享组进程退出'}}try{const h=await this.request(group,'/health','GET',undefined,undefined,1500);const m=h.members[ctx.botId];const ready=h.ready&&h.generation===group.generation&&m?.generation===ctx.generation&&m?.revision===ctx.revision;await this.db.query('UPDATE runtime_groups SET health=$2 WHERE id=$1',[group.id,JSON.stringify({pid:p.pid,rssBytes:p.rssBytes,members:Object.keys(h.members).length,generation:h.generation})]);return{alive:true,ready:!!ready,pid:p.pid,memoryPolicy:p.memoryPolicy,metrics:h.metrics?.[ctx.botId],detail:`共享组 ${group.id}；${m?.modelConfigured?'模型已配置，需对话验收':'尚未配置模型Key'}；组RSS ${Math.round(p.rssBytes/1048576)} MiB（不按成员重复计费）`};}catch{return{alive:true,ready:false,pid:p.pid,memoryPolicy:p.memoryPolicy,detail:'共享组IPC未就绪'}}}
}
