import {FavoriteMaintenance} from './favorite-maintenance.js';
import {BotConfigSchema} from '../../../packages/contracts/index.js';
import {acquireLease} from '../../../packages/database/lease.js';
import {observeOffline,evictionDue,offlineGrace} from './offline-policy.js';
import path from 'node:path';import {mkdir,appendFile,readFile,writeFile,rm,stat,readdir,rename} from 'node:fs/promises';import {randomUUID} from 'node:crypto';
import type {Sql} from '../../../packages/database/index.js';import {decrypt,redact} from '../../../packages/shared/security.js';import {terminalLogin,type RuntimeContext,type ProtocolAdapter} from '../../../packages/contracts/index.js';

/** Single serialized heartbeat writer for one Agent process.
 * Overlapping tick writes queue on one chain; every snapshot carries a
 * monotonic sequence plus the pid/processStart identity the supervisor
 * verifies. A heartbeat left behind by a replaced process can never roll the
 * file back: an identity mismatch wins only with a strictly newer startedAt,
 * and each write uses a unique temp file cleaned up by its own writer. */
export class HeartbeatWriter{
 private sequence=0;private chain:Promise<void>=Promise.resolve();private stopped=false;
 constructor(private file:string,private identity:{pid:number;processStart?:string;startedAt:string},private deps:{rename:typeof rename}={rename}){}
 write(payload:Record<string,unknown>={}){
  const task=this.chain.catch(()=>{}).then(()=>this.writeOnce(payload));
  this.chain=task;return task;
 }
 private owns(current:any){return Boolean(current)&&Number(current.pid)===this.identity.pid&&String(current.startedAt||'')===this.identity.startedAt}
 private async writeOnce(payload:Record<string,unknown>){
  if(this.stopped)return;
  let current:any=null;try{current=JSON.parse(await readFile(this.file,'utf8'))}catch(e:any){if(e.code!=='ENOENT')throw e}
  if(current&&!this.owns(current)&&String(current.startedAt||'')>=this.identity.startedAt)return;
  this.sequence=Math.max(this.sequence,Number(current?.sequence||0))+1;
  const snapshot={...this.identity,...payload,sequence:this.sequence,at:Date.now()};
  const temp=`${this.file}.${this.identity.pid}.${randomUUID()}.tmp`;
  let renamed=false;
  try{await writeFile(temp,JSON.stringify(snapshot),{flag:'wx',mode:0o600});await this.deps.rename(temp,this.file);renamed=true}
  finally{if(!renamed)await rm(temp,{force:true})}
 }
 async stop(){this.stopped=true;await this.chain.catch(()=>{})}
}
import {SharedAstrBotAdapter} from '../../../adapters/astrbot/shared.js';
import {AutonomyCoordinator,runtimeClient} from './autonomous.js';
import {BilibiliExecutor} from './bilibili/executor.js';
import {ProcessHost} from './process-host.js';import {AstrBotAdapter} from '../../../adapters/astrbot/index.js';import {MockProtocolAdapter} from '../../../adapters/mock/index.js';import {NapCatAdapter} from '../../../adapters/protocol/napcat/index.js';import {EulerAdapter} from '../../../adapters/protocol/euler/index.js';
export class NodeAgent {
 host:ProcessHost;ai:AstrBotAdapter;shared:SharedAstrBotAdapter;protocols:Record<string,ProtocolAdapter>;busy=false;closed=false;timer?:NodeJS.Timeout;lastHealth=0;
 private releaseLease?:(()=>Promise<void>);
 private shutdownTask?:Promise<void>;
 private operations=new Map<string,Promise<void>>();
 private healthMembers=new Set<string>();
 private sharedOperation=false;
 private healthTask?:Promise<void>;
 controlIdle(){return !this.busy&&this.operations.size===0&&!this.healthTask}
 private launch(botId:string,shared:boolean,run:()=>Promise<void>){
  if(this.closed||this.operations.has(botId)||this.healthMembers.has(botId)||this.operations.size>=4||shared&&this.sharedOperation)return false;
  if(shared)this.sharedOperation=true;
  const task=Promise.resolve().then(run).catch(error=>console.error('agent operation:',redact(String(error)))).finally(()=>{
   this.operations.delete(botId);if(shared)this.sharedOperation=false;
  });
  this.operations.set(botId,task);return true;
 }
 autonomy:AutonomyCoordinator;
 bilibili:BilibiliExecutor;
 favorites:FavoriteMaintenance;
 recovery=new Map<string,{attempts:number;next:number;healthySince:number;unreadySince?:number;recent?:number[];decision?:string}>();
 offlineGrace:{offline:number;fault:number;login:number;startup?:number}={offline:offlineGrace(process.env.FISHBOT_OFFLINE_GRACE_SECONDS,300),fault:offlineGrace(process.env.FISHBOT_PROTOCOL_FAULT_GRACE_SECONDS,300),login:offlineGrace(process.env.FISHBOT_LOGIN_IDLE_GRACE_SECONDS,180),startup:offlineGrace(process.env.FISHBOT_STARTUP_GRACE_SECONDS,120)};
 constructor(public db:Sql,public root:string,public key:Buffer){
   const source=path.resolve(process.env.ASTRBOT_SOURCE||'references/astrbot');const python=path.resolve(process.env.ASTRBOT_PYTHON||`references/astrbot/.venv/${process.platform==='win32'?'Scripts/python.exe':'bin/python'}`);
   this.host=new ProcessHost(root,python);this.ai=new AstrBotAdapter(this.host,source,python);this.shared=new SharedAstrBotAdapter(db,this.host,this.ai,source,python,ctx=>this.sharedEligible(ctx));
   this.protocols={mock:new MockProtocolAdapter(this.host),napcat:new NapCatAdapter(this.host,path.join(root,'napcat')),euler:new EulerAdapter(this.host,path.resolve(process.env.EULER_SOURCE||'references/euler'),path.resolve(process.env.EULER_PYTHON||`references/euler/.venv/${process.platform==='win32'?'Scripts/python.exe':'bin/python'}`))};
   this.autonomy=new AutonomyCoordinator(db,runtimeClient(db));
   this.bilibili=new BilibiliExecutor(db,root,key);
   this.favorites=new FavoriteMaintenance(db,root,key);
 }
 async context(bot:any):Promise<RuntimeContext>{const [instance]=await this.db.query('SELECT * FROM instances WHERE bot_id=$1',[bot.id]);const [revision]=await this.db.query('SELECT config FROM bot_config_revisions WHERE bot_id=$1 AND revision=$2',[bot.id,bot.revision]);const [binding]=await this.db.query('SELECT qq_uin FROM qq_bindings WHERE bot_id=$1',[bot.id]);const [secret]=await this.db.query('SELECT ciphertext FROM secrets WHERE bot_id=$1 AND tenant_id=$2',[bot.id,bot.tenant_id]);const [permit]=await this.db.query("SELECT id::text AS id FROM audit_events WHERE bot_id=$1 AND event='config.saved' AND detail->>'revision'=$2 AND detail->>'source'='authenticated_web_config' AND detail->>'actorId' IS NOT NULL ORDER BY id DESC LIMIT 1",[bot.id,String(bot.revision)]);return{botId:bot.id,tenantId:bot.tenant_id,root:path.join(this.root,'bots',bot.id),generation:bot.generation,revision:bot.revision,systemChangePermitId:permit?.id,aiPort:instance.ai_port,protocolPort:instance.protocol_port,token:instance.token,config:BotConfigSchema.parse(revision.config),apiKey:secret?decrypt(this.key,`${bot.tenant_id}:${bot.id}`,secret.ciphertext):'',expectedUin:binding?.qq_uin||bot.expected_uin||'',runtimeMode:bot.runtime_mode,groupId:bot.group_id}}
 aiFor(bot:any){return bot.runtime_mode==='shared'?this.shared:this.ai}
 async sharedEligible(ctx:RuntimeContext){const [bot]=await this.db.query('SELECT * FROM bots WHERE id=$1',[ctx.botId]);if(!bot||bot.deleted_at||bot.runtime_mode!=='shared'||bot.desired_state!=='running'||bot.tenant_id!==ctx.tenantId||bot.generation!==ctx.generation||bot.revision!==ctx.revision||!ctx.expectedUin)return false;const protocol=this.protocols[bot.protocol];return !!(await protocol.getHealth(ctx)).online&&(await protocol.queryIdentity(ctx))===ctx.expectedUin}
 async reconcileSharedMembers(){
   // Old releases retained stopped memberships indefinitely. Do not erase
   // bookkeeping until the old worker acknowledges unload (or is proven gone).
   const inactive=await this.db.query("SELECT b.id,b.deleted_at FROM group_members m JOIN bots b ON b.id=m.bot_id WHERE b.deleted_at IS NOT NULL OR b.desired_state<>'running' OR m.desired_state<>'running'");
   for(const bot of inactive){
    if(this.operations.has(bot.id)||this.healthMembers.has(bot.id)||this.sharedOperation)continue;
    this.sharedOperation=true;this.healthMembers.add(bot.id);
    try{await this.shared.stop(bot.id,!!bot.deleted_at)}catch(error){await this.log(bot,'共享成员清理等待确认：'+(error as Error).message)}
    finally{this.sharedOperation=false;this.healthMembers.delete(bot.id)}
   }
 }
 async log(bot:any,text:string){const root=path.join(this.root,'bots',bot.id);await mkdir(root,{recursive:true});const file=path.join(root,'runtime.log');try{if((await stat(file)).size>2*1024*1024)await writeFile(file,'[platform] log rotated\n')}catch{};await appendFile(file,`[platform ${new Date().toISOString()}] ${redact(text)}\n`)}
 async begin():Promise<void>{await mkdir(this.root,{recursive:true});this.releaseLease=await acquireLease(this.root,'agent');try{await writeFile(path.join(this.root,'agent.lock'),String(process.pid));
   await this.db.query("UPDATE commands SET status='pending' WHERE status='processing'");
   await this.reconcileSharedMembers();
   await this.bilibili.recover();
   this.timer=setInterval(()=>void this.tick().catch(e=>console.error('agent tick:',redact(String(e)))),500);this.timer.unref();await this.tick();this.autonomy.start();}catch(error){if(this.timer)clearInterval(this.timer);await rm(path.join(this.root,'agent.lock'),{force:true});await this.releaseLease?.();this.releaseLease=undefined;throw error}
 }
 shutdown(){return this.shutdownTask??=this.shutdownOnce()}
 private async shutdownOnce(){this.closed=true;if(this.timer)clearInterval(this.timer);let failure:unknown;
  // All owners must get a chance to settle before children and the lease go.
  // A failed maintenance stop must not bypass the remaining shutdown path.
  for(const owner of [this.favorites,this.bilibili,this.autonomy])try{await owner?.stop()}catch(e){failure??=e}
  while(this.busy)await new Promise(r=>setTimeout(r,20));await Promise.allSettled([...this.operations.values(),...(this.healthTask?[this.healthTask]:[])]);
  try{for(const bot of await this.db.query('SELECT * FROM bots WHERE deleted_at IS NULL')){await this.aiFor(bot).stop(bot.id).catch(e=>{failure??=e});await this.protocols[bot.protocol].stop(bot.id).catch(e=>{failure??=e})}}catch(e){failure??=e}
  finally{try{
   // Database failure must not leave a shared group or QQ child alive. These
   // on-disk records are verified by ProcessHost against PID creation and cwd.
   const dirs=await readdir(path.join(this.root,'bots')).catch(e=>{if(e.code==='ENOENT')return[];throw e});for(const id of dirs)for(const kind of ['ai','protocol'])try{await this.host.stop(id,kind)}catch(e){failure??=e};
  }catch(e){failure??=e}finally{if(!failure){await rm(path.join(this.root,'agent.lock'),{force:true});await this.releaseLease?.();this.releaseLease=undefined}}}
  if(failure)throw failure;
 }
 async tick(){if(this.busy||this.closed)return;this.busy=true;try{
   this.bilibili.tick();
   this.favorites?.tick();
   const commands=await this.db.query("SELECT c.*,b.runtime_mode FROM commands c JOIN bots b ON b.id=c.bot_id WHERE c.status='pending' ORDER BY c.created_at LIMIT 32");
   if(this.closed)return;
   for(const command of commands)this.launch(command.bot_id,command.runtime_mode==='shared',()=>this.execute(command));
   const sessions=await this.db.query("SELECT s.*,b.protocol,b.expected_uin,b.tenant_id FROM login_sessions s JOIN bots b ON b.id=s.bot_id WHERE s.state NOT IN ('online','expired','canceled','failed') AND b.deleted_at IS NULL");
   if(this.closed)return;
   for(const session of sessions)this.launch(session.bot_id,true,()=>this.pollLogin(session));
   if(!this.healthTask&&Date.now()-this.lastHealth>4000){this.lastHealth=Date.now();this.healthTask=this.health().catch(error=>console.error('agent health:',redact(String(error)))).finally(()=>{this.healthTask=undefined})}
 }finally{this.busy=false}}
 async execute(command:any){await this.db.query("UPDATE commands SET status='processing' WHERE id=$1",[command.id]);let ctx:RuntimeContext|undefined;
 try{const [bot]=await this.db.query('SELECT * FROM bots WHERE id=$1',[command.bot_id]);if(!bot||bot.deleted_at||bot.generation!==command.generation){await this.db.query("UPDATE commands SET status='superseded',finished_at=now() WHERE id=$1",[command.id]);return}
   ctx=await this.context(bot);const protocol=this.protocols[bot.protocol],ai=this.aiFor(bot);await mkdir(ctx.root,{recursive:true});
   await this.log(bot,`lifecycle command=${command.id} kind=${command.kind} key=${command.idempotency_key} generation=${command.generation} mode=${bot.runtime_mode} group=${bot.group_id||'dedicated'}`);
   if(command.idempotency_key.startsWith('recover-')&&(await ai.probe(ctx)).ready&&(await protocol.getHealth(ctx)).ready){await this.db.query("UPDATE commands SET status='superseded',finished_at=now() WHERE id=$1",[command.id]);return;}
   if(!command.idempotency_key.startsWith('recover-'))this.recovery.delete(bot.id);
   if(command.kind==='evict-offline'){
     // The bot row lock serializes process destruction against user start/login.
     // Only runtime handles are released; never call the delete path.
     let reconnected=false;
     await this.db.transaction(async tx=>{
       const [owner]=await tx.query('SELECT * FROM bots WHERE id=$1 FOR UPDATE',[bot.id]);
       if(!owner||owner.deleted_at||owner.generation!==command.generation||owner.desired_state!=='offline')throw Object.assign(new Error('离线清退已被新操作替代'),{superseded:true});
       // A protocol may reconnect after the durable claim without a user
       // command. Recheck immediately before destruction and preserve it.
       const finalHealth=await protocol.getHealth(ctx!);
       if(finalHealth.online){
         const health={...owner.health,protocol:finalHealth,offline:undefined};
         await tx.query("UPDATE bots SET desired_state='running',observed_state='starting',health=$2 WHERE id=$1",[bot.id,JSON.stringify(health)]);
         await tx.query("INSERT INTO commands(id,bot_id,idempotency_key,generation,kind) VALUES($1,$2,$3,$4,'start') ON CONFLICT DO NOTHING",[randomUUID(),bot.id,`reconnected-${command.generation}`,command.generation]);
         await tx.query("UPDATE commands SET status='superseded',finished_at=now() WHERE id=$1",[command.id]);
         reconnected=true;return;
       }
       await protocol.stop(bot.id);if(bot.runtime_mode==='shared')await this.shared.stop(bot.id,false,tx);else await ai.stop(bot.id);
       const health={...owner.health,ai:{alive:false,ready:false},protocol:{alive:false,ready:false,online:false},offline:{...owner.health.offline,evictedAt:Date.now()}};
       await tx.query("UPDATE bots SET observed_state='offline',health=$2,last_error=NULL WHERE id=$1",[bot.id,JSON.stringify(health)]);
       await tx.query('UPDATE instances SET health=$2,generation=$3,heartbeat_at=now() WHERE bot_id=$1',[bot.id,JSON.stringify(health),command.generation]);
     });this.recovery.delete(bot.id);
     if(reconnected)return;
   }
   else if(command.kind==='stop'||command.kind==='delete'){await (bot.runtime_mode==='shared'?this.shared.stop(bot.id,command.kind==='delete'):ai.stop(bot.id));await protocol.stop(bot.id);await this.db.query("UPDATE bots SET observed_state='stopped',health='{}' WHERE id=$1 AND generation=$2",[bot.id,command.generation]);if(command.kind==='delete')await this.db.transaction(async tx=>{await tx.query('DELETE FROM secrets WHERE bot_id=$1',[bot.id]);await tx.query('DELETE FROM qq_bindings WHERE bot_id=$1',[bot.id]);await tx.query('UPDATE bots SET deleted_at=now() WHERE id=$1',[bot.id])});}
   else if(command.kind==='login'){const sessionId=command.idempotency_key.replace(/^login-/, '');const [s]=await this.db.query("UPDATE login_sessions SET state='starting',sequence=sequence+1 WHERE bot_id=$1 AND id=$2 AND generation=$3 AND state='created' RETURNING id",[bot.id,sessionId,command.generation]);if(s){if(bot.runtime_mode==='shared')await this.shared.stop(bot.id);await protocol.stop(bot.id);
     if(bot.runtime_mode!=='shared'&&bot.desired_state==='running'&&!(await ai.probe(ctx!)).ready){
       await ai.stop(bot.id);
       await ai.start(ctx!);
     }
     await this.db.transaction(async tx=>{await tx.query('SELECT id FROM bots WHERE id=$1 FOR UPDATE',[bot.id]);const [current]=await tx.query("SELECT id FROM login_sessions WHERE id=$1 AND bot_id=$2 AND generation=$3 AND state='starting'",[s.id,bot.id,command.generation]);if(current){const loginContext:RuntimeContext={...ctx!,botId:bot.id,tenantId:bot.tenant_id,root:ctx!.root,generation:command.generation,revision:bot.revision,aiPort:ctx!.aiPort,protocolPort:ctx!.protocolPort,token:ctx!.token,config:ctx!.config,apiKey:ctx!.apiKey,expectedUin:ctx!.expectedUin,runtimeMode:bot.runtime_mode,groupId:bot.group_id,loginIntent:'qr'};await protocol.beginLogin(loginContext)}})}}
   else if(command.kind==='cancel-login'){await this.db.transaction(async tx=>{
     const [owner]=await tx.query('SELECT * FROM bots WHERE id=$1 FOR UPDATE',[bot.id]);
     const [latest]=await tx.query('SELECT id,state FROM login_sessions WHERE bot_id=$1 ORDER BY created_at DESC LIMIT 1',[bot.id]);
     if(!owner||owner.generation!==command.generation||!['canceled','expired'].includes(latest?.state)||command.idempotency_key!==`cancel-${latest.id}`)return;
     await protocol.cancelLogin(ctx!);
     const generation=owner.generation+1;
     await tx.query("UPDATE bots SET desired_state='offline',observed_state='stopping',generation=$2 WHERE id=$1",[bot.id,generation]);
     await tx.query("INSERT INTO commands(id,bot_id,idempotency_key,generation,kind) VALUES($1,$2,$3,$4,'evict-offline') ON CONFLICT DO NOTHING",[randomUUID(),bot.id,`evict-offline-${generation}`,generation]);
   })}
   else {
     const current=async()=>{const [fresh]=await this.db.query('SELECT generation,revision,desired_state,deleted_at FROM bots WHERE id=$1',[bot.id]);return fresh&&!fresh.deleted_at&&fresh.desired_state==='running'&&fresh.generation===ctx!.generation&&fresh.revision===ctx!.revision};
     const superseded=()=>Object.assign(new Error('命令已被新的生命周期或配置替代'),{superseded:true});
     if(!await current())throw superseded();
     const recovering=command.kind==='restart'&&command.idempotency_key.startsWith('recover-');
     // Automatic recovery repairs the missing component. A failed shared AI
     // must not stop an online QQ, and a failed protocol must not detach a
     // healthy member or restart an independent AI. Manual restart is unchanged.
     if(!recovering&&(bot.runtime_mode!=='shared'||command.kind==='restart'))await ai.stop(bot.id);
     if(command.kind==='restart'&&!recovering)await protocol.stop(bot.id);
     if(!await current())throw superseded();
     // Shared SDK slots belong only to online QQ accounts. The protocol can
     // restore/login without an SDK platform; its reverse WS reconnects when
     // the existing health path later admits the authenticated member.
     let sharedOnline=true;
     if(bot.runtime_mode==='shared'){
       let h=await protocol.getHealth(ctx);
       if(command.kind!=='apply'&&!h.alive){await protocol.beginLogin(ctx);if(!await current()){await protocol.stop(bot.id);throw superseded()}h=await protocol.getHealth(ctx)}
       sharedOnline=!!h.online&&!!ctx.expectedUin;
       if(sharedOnline){const uin=await protocol.queryIdentity(ctx);if(!uin||!/^\d{5,20}$/.test(uin)||uin!==ctx.expectedUin)throw new Error('恢复会话的QQ身份不匹配')}
     }
     const recoveryHealth=recovering&&sharedOnline?await ai.probe(ctx):undefined;
     // A dedicated process stuck before readiness cannot be repaired by start:
     // ProcessHost.start correctly adopts a live PID. Replace only that AI;
     // shared recovery reattaches its member without killing peers or QQ.
     if(recovering&&command.idempotency_key.includes('-readiness-')&&recoveryHealth?.alive&&!recoveryHealth.ready&&bot.runtime_mode!=='shared')await ai.stop(bot.id);
     if(!sharedOnline)await this.shared.stop(bot.id);
     else if(recovering&&command.idempotency_key.includes('-readiness-')&&bot.runtime_mode==='shared'&&!recoveryHealth?.ready)await this.shared.recoverReadiness(ctx);
     else if(!recovering||!recoveryHealth?.ready)await ai.start(ctx);
     if(!await current()){await ai.stop(bot.id);throw superseded()}
     if(command.kind!=='apply'&&!(await protocol.getHealth(ctx)).alive){if(!await current()){await ai.stop(bot.id);throw superseded()}await protocol.beginLogin(ctx);if(!await current()){await ai.stop(bot.id);await protocol.stop(bot.id);throw superseded()}}
     await this.db.query("UPDATE bots SET observed_state='starting',last_error=NULL WHERE id=$1 AND generation=$2",[bot.id,command.generation])}
   await this.log(bot,`${command.kind} 命令执行完成；等待真实健康回执`);await this.db.query("UPDATE commands SET status='completed',finished_at=now() WHERE id=$1",[command.id]);
 }catch(e){if((e as any).superseded){await this.db.query("UPDATE commands SET status='superseded',finished_at=now() WHERE id=$1",[command.id]);return}const message=redact((e as Error).message,[ctx?.apiKey||'',ctx?.token||'',process.env.EULER_SIGNER_TOKEN||'']);await this.db.query("UPDATE commands SET status='failed',error=$2,finished_at=now() WHERE id=$1",[command.id,message]);await this.db.query("UPDATE bots SET observed_state='failed',last_error=$2 WHERE id=$1 AND generation=$3",[command.bot_id,message,command.generation]);await this.db.query("UPDATE login_sessions SET state='failed',message=$2,qr=NULL,sequence=sequence+1 WHERE bot_id=$1 AND generation=$3 AND state NOT IN ('online','expired','canceled','failed')",[command.bot_id,message,command.generation]);await this.log({id:command.bot_id},message)}
 }
 async pollLogin(session:any){const [bot]=await this.db.query('SELECT * FROM bots WHERE id=$1',[session.bot_id]);if(bot.generation!==session.generation)return;
   if(new Date(session.expires_at).getTime()<Date.now()){await this.db.transaction(async tx=>{const updated=await tx.query("UPDATE login_sessions SET state='expired',qr=NULL,message='二维码已过期',sequence=sequence+1 WHERE id=$1 AND state NOT IN ('online','expired','canceled','failed') RETURNING id",[session.id]);if(updated.length)await tx.query("INSERT INTO commands(id,bot_id,idempotency_key,generation,kind) VALUES($1,$2,$3,$4,'cancel-login') ON CONFLICT DO NOTHING",[randomUUID(),bot.id,`cancel-${session.id}`,session.generation]);});return}
   try{const ctx=await this.context(bot),s=await this.protocols[bot.protocol].getLoginState(ctx);if(terminalLogin.has(s.state)&&s.state==='online'){
      if(!s.uin||!/^\d{5,20}$/.test(s.uin))throw new Error('协议未返回可信QQ身份');if(ctx.expectedUin&&ctx.expectedUin!==s.uin)throw new Error('扫码QQ（'+s.uin+'）与预填QQ号（'+ctx.expectedUin+'）不一致，请使用原QQ重新登录；换号需删除机器人后重新创建');
      const h=await this.protocols[bot.protocol].getHealth(ctx);if(!h.online)return;
      await this.db.transaction(async tx=>{
        await tx.query('SELECT id FROM bots WHERE id=$1 FOR UPDATE',[bot.id]);
        const [current]=await tx.query("SELECT s.state,b.generation,b.expected_uin FROM login_sessions s JOIN bots b ON b.id=s.bot_id WHERE s.id=$1 FOR UPDATE",[session.id]);
        if(!current||current.generation!==session.generation)return;
        if(terminalLogin.has(current.state)){
          const [latest]=await tx.query('SELECT id FROM login_sessions WHERE bot_id=$1 ORDER BY created_at DESC LIMIT 1',[bot.id]);
          if(current.state!=='online'&&latest?.id===session.id)await this.protocols[bot.protocol].stop(bot.id);
          return;
        }
        const [binding]=await tx.query('SELECT qq_uin FROM qq_bindings WHERE bot_id=$1',[bot.id]);
        const pinned=binding?.qq_uin||current.expected_uin;
        if(pinned&&pinned!==s.uin)throw new Error('登录QQ与此机器人绑定账号不一致；换号需删除机器人后重新创建');
        await tx.query('INSERT INTO qq_bindings(bot_id,qq_uin,binding_kind) VALUES($1,$2,$3) ON CONFLICT(bot_id) DO UPDATE SET qq_uin=$2,binding_kind=$3,bound_at=now()',[bot.id,s.uin,bot.protocol==='mock'?'mock':'real']);
        await tx.query("UPDATE login_sessions SET state='online',qq_uin=$2,qr=NULL,sequence=sequence+1 WHERE id=$1",[session.id,s.uin]);
      });
   }else{await this.db.query("UPDATE login_sessions SET state=$2,qr=$3,message=$4,sequence=sequence+1 WHERE id=$1 AND generation=$5 AND state NOT IN ('online','expired','canceled','failed') AND (state IS DISTINCT FROM $2 OR qr IS DISTINCT FROM $3)",[session.id,s.state,s.qr||null,s.message||null,session.generation])}}
   catch(e:any){if(e.code==='23505'||e.message.includes('不一致')||e.message.includes('可信')){await this.db.query("UPDATE login_sessions SET state='failed',message=$2,qr=NULL,sequence=sequence+1 WHERE id=$1 AND state NOT IN ('online','expired','canceled','failed')",[session.id,e.code==='23505'?'此QQ已绑定其他机器人':e.message]);await this.protocols[bot.protocol].stop(bot.id)}/* transient request failures retain state until timeout */}
 }
 async health(){return this.host.withInspection(()=>this.healthSnapshot())}
 async healthSnapshot(){await this.host.beginInspection();try{await this.reconcileSharedMembers();const selected=await this.db.query("SELECT b.* FROM bots b WHERE b.deleted_at IS NULL AND (b.desired_state='running' OR (b.desired_state='offline' AND b.observed_state!='offline') OR b.health->'ai'->>'alive'='true' OR b.health->'protocol'->>'alive'='true' OR EXISTS (SELECT 1 FROM login_sessions s WHERE s.bot_id=b.id AND s.state NOT IN ('online','expired','canceled','failed'))) ORDER BY b.created_at,b.id");const running=new Set(selected.filter(b=>b.desired_state==='running').map(b=>b.id));for(const id of this.recovery.keys())if(!running.has(id))this.recovery.delete(id);for(const bot of selected){if(this.operations.has(bot.id))continue;this.healthMembers.add(bot.id);try{
       if(bot.desired_state==='offline'){await this.db.query("UPDATE commands SET status='pending',error=NULL WHERE bot_id=$1 AND generation=$2 AND kind='evict-offline' AND status='failed' AND finished_at<now()-interval '30 seconds'",[bot.id,bot.generation]);continue}
       const ctx=await this.context(bot);const protocol=await this.protocols[bot.protocol].getHealth(ctx);
       if(protocol.online){try{const uin=await this.protocols[bot.protocol].queryIdentity(ctx);const [identityOwner]=await this.db.query('SELECT generation,deleted_at FROM bots WHERE id=$1',[bot.id]);if(!identityOwner||identityOwner.deleted_at||identityOwner.generation!==bot.generation)continue;if(!uin||!/^\d{5,20}$/.test(uin)||(ctx.expectedUin&&ctx.expectedUin!==uin))throw Object.assign(new Error('恢复会话的QQ身份不匹配'),{identityConflict:true});await this.db.transaction(async tx=>{const [owner]=await tx.query('SELECT generation,deleted_at,desired_state,expected_uin FROM bots WHERE id=$1 FOR UPDATE',[bot.id]);if(!owner||owner.deleted_at||owner.desired_state==='deleting'||owner.generation!==bot.generation)return;const [binding]=await tx.query('SELECT qq_uin FROM qq_bindings WHERE bot_id=$1',[bot.id]);const pinned=binding?.qq_uin||owner.expected_uin;if(pinned&&pinned!==uin)throw Object.assign(new Error('QQ身份不一致，换号需删除机器人后重新创建'),{identityConflict:true});await tx.query("INSERT INTO qq_bindings(bot_id,qq_uin,binding_kind) VALUES($1,$2,$3) ON CONFLICT(bot_id) DO UPDATE SET qq_uin=$2,binding_kind=$3",[bot.id,uin,bot.protocol==='mock'?'mock':'real'])})}catch(e){if(!(e as any).identityConflict&&(e as any).code!=='23505')throw e;const [identityOwner]=await this.db.query('SELECT generation,deleted_at FROM bots WHERE id=$1',[bot.id]);if(!identityOwner||identityOwner.deleted_at||identityOwner.generation!==bot.generation)continue;await this.protocols[bot.protocol].stop(bot.id);protocol.online=false;protocol.alive=false;protocol.ready=false;protocol.detail='QQ身份冲突，已停止协议进程';await this.log(bot,protocol.detail)}}
       const aiNeeded=bot.runtime_mode!=='shared'||!!protocol.online;
       if(bot.runtime_mode==='shared'&&(bot.desired_state!=='running'||!aiNeeded)){if(this.sharedOperation)continue;this.sharedOperation=true;try{await this.shared.stop(bot.id)}finally{this.sharedOperation=false}}
       const ai=aiNeeded?await this.aiFor(bot).probe(ctx):{alive:false,ready:false,detail:'QQ离线，未占用共享SDK成员位'};
       const [login]=await this.db.query("SELECT id FROM login_sessions WHERE bot_id=$1 AND state NOT IN ('online','expired','canceled','failed')",[bot.id]);
       const [pending]=await this.db.query("SELECT id FROM commands WHERE bot_id=$1 AND status IN ('pending','processing')",[bot.id]);
       const onlineSince=bot.health?.onlineSince||(protocol.online?Date.now():undefined);
       const offline=(bot.desired_state==='running'||ai.alive||protocol.alive)?observeOffline(bot.health?.offline,protocol,Date.now(),this.offlineGrace,{ai:bot.desired_state==='running'&&aiNeeded?ai:undefined,onlineSince,requestedAt:bot.health?.runtimeRequestedAt}):undefined;
       const health={ai,protocol,offline,onlineSince,runtimeRequestedAt:bot.health?.runtimeRequestedAt,mode:bot.protocol==='mock'?'mock':'real',sampledAt:new Date().toISOString()};const observed=bot.desired_state==='stopped'?'stopped':!aiNeeded?(protocol.alive?'degraded':'starting'):ai.ready?(protocol.online?'online':'degraded'):ai.alive?'starting':'failed';await this.db.query('UPDATE bots SET health=$2,observed_state=$3,applied_revision=CASE WHEN $4 THEN $5 ELSE applied_revision END WHERE id=$1 AND generation=$6 AND revision=$5 AND desired_state=$7',[bot.id,JSON.stringify(health),observed,ai.ready,ctx.revision,ctx.generation,bot.desired_state]);await this.db.query('UPDATE instances SET heartbeat_at=now(),health=$2,generation=$3 WHERE bot_id=$1 AND EXISTS(SELECT 1 FROM bots WHERE id=$1 AND generation=$3 AND revision=$4)',[bot.id,JSON.stringify(health),ctx.generation,ctx.revision]);
       if(evictionDue(offline,Date.now())){
         await this.db.transaction(async tx=>{
           const [owner]=await tx.query('SELECT * FROM bots WHERE id=$1 FOR UPDATE',[bot.id]);
           if(!owner||owner.deleted_at||owner.generation!==bot.generation||owner.revision!==bot.revision||owner.desired_state!==bot.desired_state)return;
           await tx.query("UPDATE login_sessions SET state='expired',qr=NULL,message='运行资源等待超时，已释放实例',sequence=sequence+1 WHERE bot_id=$1 AND generation=$2 AND state NOT IN ('online','expired','canceled','failed')",[bot.id,bot.generation]);
           const generation=owner.generation+1;
           await tx.query("UPDATE bots SET desired_state='offline',observed_state='stopping',generation=$2 WHERE id=$1",[bot.id,generation]);
           await tx.query("INSERT INTO commands(id,bot_id,idempotency_key,generation,kind) VALUES($1,$2,$3,$4,'evict-offline')",[randomUUID(),bot.id,`evict-offline-${generation}`,generation]);
         });this.recovery.delete(bot.id);continue;
       }
       const retry=this.recovery.get(bot.id)||{attempts:0,next:0,healthySince:0};
       if(bot.desired_state==='running'){
        this.recovery.set(bot.id,retry);
        const now=Date.now();retry.recent=(retry.recent||[]).filter(at=>at>now-3600000);
        if(ai.ready)retry.unreadySince=undefined;else retry.unreadySince??=now;
        const readinessExpired=ai.alive&&!ai.ready&&now-(retry.unreadySince??now)>=120000;
        const decision=async(value:string)=>{if(retry.decision!==value){retry.decision=value;await this.log(bot,`recovery decision=${value} attempts=${retry.attempts} next=${retry.next} aiAlive=${ai.alive} protocolAlive=${protocol.alive}`)}};
        if((!aiNeeded||ai.ready)&&protocol.ready){if(!retry.healthySince)retry.healthySince=now;if(now-retry.healthySince>60000){retry.attempts=0;retry.next=0}retry.decision=undefined}
        else if(aiNeeded&&!ai.alive||!protocol.alive||aiNeeded&&readinessExpired){
         retry.healthySince=0;
         if(login||pending)await decision(login?'login-pending':'command-pending');
         else if(now<retry.next)await decision(retry.attempts>=3?'slow-backoff':'backoff');
         else if(retry.recent.length>=9){retry.next=retry.recent[0]+3600000;await decision('hourly-limit')}
         else{
          try{
           const id=randomUUID();const inserted=await this.db.query("INSERT INTO commands(id,bot_id,kind,generation,idempotency_key) SELECT $4,id,'restart',generation,$2 FROM bots WHERE id=$1 AND generation=$3 AND desired_state='running' AND deleted_at IS NULL AND NOT EXISTS(SELECT 1 FROM commands WHERE bot_id=$1 AND status IN ('pending','processing')) RETURNING id",[bot.id,`recover-${ctx.generation}-${readinessExpired?'readiness-':''}${id}`,ctx.generation,id]);
           if(inserted.length){retry.attempts++;retry.unreadySince=now;retry.recent.push(now);retry.next=now+(retry.attempts>=3?600000:5000*2**retry.attempts);retry.decision=undefined;await this.log(bot,`recovery queued command=${id} attempt=${retry.attempts} next=${retry.next}`)}
           else await decision('superseded-or-pending');
          }catch(error){retry.next=now+10000;await decision('insert-failed');throw error}
         }
        }else{retry.healthySince=0;await decision('awaiting-readiness')}
       }else this.recovery.delete(bot.id);
   }catch(e){await this.log(bot,(e as Error).message)}finally{this.healthMembers.delete(bot.id)}}}finally{this.host.endInspection()}}
}
