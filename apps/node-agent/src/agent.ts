import {BotConfigSchema} from '../../../packages/contracts/index.js';
import {acquireLease} from '../../../packages/database/lease.js';
import {observeOffline,evictionDue,offlineGrace} from './offline-policy.js';
import path from 'node:path';import {mkdir,appendFile,readFile,writeFile,rm,open,stat,readdir} from 'node:fs/promises';import {randomBytes,randomUUID} from 'node:crypto';
import type {Sql} from '../../../packages/database/index.js';import {decrypt,redact} from '../../../packages/shared/security.js';import {terminalLogin,type RuntimeContext,type ProtocolAdapter,type LoginSnapshot} from '../../../packages/contracts/index.js';
import {SharedAstrBotAdapter} from '../../../adapters/astrbot/shared.js';
import {AutonomyCoordinator,runtimeClient} from './autonomous.js';
import {BilibiliExecutor} from './bilibili/executor.js';
import {ProcessHost} from './process-host.js';import {AstrBotAdapter} from '../../../adapters/astrbot/index.js';import {MockProtocolAdapter} from '../../../adapters/mock/index.js';import {NapCatAdapter} from '../../../adapters/protocol/napcat/index.js';import {EulerAdapter} from '../../../adapters/protocol/euler/index.js';
export class NodeAgent {
 host:ProcessHost;ai:AstrBotAdapter;shared:SharedAstrBotAdapter;protocols:Record<string,ProtocolAdapter>;busy=false;closed=false;timer?:NodeJS.Timeout;lastHealth=0;
 private releaseLease?:(()=>Promise<void>);
 autonomy:AutonomyCoordinator;
 bilibili:BilibiliExecutor;
 recovery=new Map<string,{attempts:number;next:number;healthySince:number}>();
 offlineGrace:{offline:number;fault:number;login:number;startup?:number}={offline:offlineGrace(process.env.FISHBOT_OFFLINE_GRACE_SECONDS,300),fault:offlineGrace(process.env.FISHBOT_PROTOCOL_FAULT_GRACE_SECONDS,300),login:offlineGrace(process.env.FISHBOT_LOGIN_IDLE_GRACE_SECONDS,180),startup:offlineGrace(process.env.FISHBOT_STARTUP_GRACE_SECONDS,120)};
 constructor(public db:Sql,public root:string,public key:Buffer){
   const source=path.resolve(process.env.ASTRBOT_SOURCE||'references/astrbot');const python=path.resolve(process.env.ASTRBOT_PYTHON||`references/astrbot/.venv/${process.platform==='win32'?'Scripts/python.exe':'bin/python'}`);
   this.host=new ProcessHost(root,python);this.ai=new AstrBotAdapter(this.host,source,python);this.shared=new SharedAstrBotAdapter(db,this.host,this.ai,source,python,bot=>this.context(bot));
   this.protocols={mock:new MockProtocolAdapter(this.host),napcat:new NapCatAdapter(this.host,path.join(root,'napcat')),euler:new EulerAdapter(this.host,path.resolve(process.env.EULER_SOURCE||'references/euler'),path.resolve(process.env.EULER_PYTHON||`references/euler/.venv/${process.platform==='win32'?'Scripts/python.exe':'bin/python'}`))};
   this.autonomy=new AutonomyCoordinator(db,runtimeClient(db));
   this.bilibili=new BilibiliExecutor(db,root,key);
 }
 async context(bot:any):Promise<RuntimeContext>{const [instance]=await this.db.query('SELECT * FROM instances WHERE bot_id=$1',[bot.id]);const [revision]=await this.db.query('SELECT config FROM bot_config_revisions WHERE bot_id=$1 AND revision=$2',[bot.id,bot.revision]);const [binding]=await this.db.query('SELECT qq_uin FROM qq_bindings WHERE bot_id=$1',[bot.id]);const [secret]=await this.db.query('SELECT ciphertext FROM secrets WHERE bot_id=$1 AND tenant_id=$2',[bot.id,bot.tenant_id]);return{botId:bot.id,tenantId:bot.tenant_id,root:path.join(this.root,'bots',bot.id),generation:bot.generation,revision:bot.revision,aiPort:instance.ai_port,protocolPort:instance.protocol_port,token:instance.token,config:BotConfigSchema.parse(revision.config),apiKey:secret?decrypt(this.key,`${bot.tenant_id}:${bot.id}`,secret.ciphertext):'',expectedUin:binding?.qq_uin||bot.expected_uin||'',runtimeMode:bot.runtime_mode,groupId:bot.group_id}}
 aiFor(bot:any){return bot.runtime_mode==='shared'?this.shared:this.ai}
 async log(bot:any,text:string){const root=path.join(this.root,'bots',bot.id);await mkdir(root,{recursive:true});const file=path.join(root,'runtime.log');try{if((await stat(file)).size>2*1024*1024)await writeFile(file,'[platform] log rotated\n')}catch{};await appendFile(file,`[platform ${new Date().toISOString()}] ${redact(text)}\n`)}
 async begin():Promise<void>{await mkdir(this.root,{recursive:true});this.releaseLease=await acquireLease(this.root,'agent');try{await writeFile(path.join(this.root,'agent.lock'),String(process.pid));
   await this.db.query("UPDATE commands SET status='pending' WHERE status='processing'");
   await this.bilibili.recover();
   this.timer=setInterval(()=>void this.tick().catch(e=>console.error('agent tick:',redact(String(e)))),500);this.timer.unref();await this.tick();this.autonomy.start();}catch(error){if(this.timer)clearInterval(this.timer);await rm(path.join(this.root,'agent.lock'),{force:true});await this.releaseLease?.();this.releaseLease=undefined;throw error}
 }
 async shutdown(){if(this.closed)return;this.closed=true;if(this.timer)clearInterval(this.timer);await this.bilibili.stop();await this.autonomy.stop();while(this.busy)await new Promise(r=>setTimeout(r,20));let failure:unknown;
  try{for(const bot of await this.db.query('SELECT * FROM bots WHERE deleted_at IS NULL')){await this.aiFor(bot).stop(bot.id).catch(e=>{failure??=e});await this.protocols[bot.protocol].stop(bot.id).catch(e=>{failure??=e})}}catch(e){failure??=e}
  finally{try{
   // Database failure must not leave a shared group or QQ child alive. These
   // on-disk records are verified by ProcessHost against PID creation and cwd.
   const dirs=await readdir(path.join(this.root,'bots')).catch(e=>{if(e.code==='ENOENT')return[];throw e});for(const id of dirs)for(const kind of ['ai','protocol'])await this.host.stop(id,kind).catch(e=>{failure??=e});
  }finally{try{await rm(path.join(this.root,'agent.lock'),{force:true})}finally{const release=this.releaseLease;this.releaseLease=undefined;await release?.()}}}
  if(failure)throw failure;
 }
 async tick(){if(this.busy||this.closed)return;this.busy=true;try{
   this.bilibili.tick();
   const [command]=await this.db.query("SELECT * FROM commands WHERE status='pending' ORDER BY created_at LIMIT 1");
   if(command)await this.execute(command);
   const sessions=await this.db.query("SELECT s.*,b.protocol,b.expected_uin,b.tenant_id FROM login_sessions s JOIN bots b ON b.id=s.bot_id WHERE s.state NOT IN ('online','expired','canceled','failed') AND b.deleted_at IS NULL");
   for(const session of sessions)await this.pollLogin(session);
   if(Date.now()-this.lastHealth>4000){this.lastHealth=Date.now();await this.health()}
 }finally{this.busy=false}}
 async execute(command:any){await this.db.query("UPDATE commands SET status='processing' WHERE id=$1",[command.id]);let ctx:RuntimeContext|undefined;
 try{const [bot]=await this.db.query('SELECT * FROM bots WHERE id=$1',[command.bot_id]);if(!bot||bot.deleted_at||bot.generation!==command.generation){await this.db.query("UPDATE commands SET status='superseded',finished_at=now() WHERE id=$1",[command.id]);return}
   ctx=await this.context(bot);const protocol=this.protocols[bot.protocol],ai=this.aiFor(bot);await mkdir(ctx.root,{recursive:true});
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
       if(finalHealth.online&&(await ai.probe({...ctx!,generation:command.generation-1})).ready){
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
   else if(command.kind==='login'){const sessionId=command.idempotency_key.replace(/^login-/, '');const [s]=await this.db.query("UPDATE login_sessions SET state='starting',sequence=sequence+1 WHERE bot_id=$1 AND id=$2 AND generation=$3 AND state='created' RETURNING id",[bot.id,sessionId,command.generation]);if(s){await protocol.stop(bot.id);
     if(bot.desired_state==='running'&&!(await ai.probe(ctx!)).ready){
       if(bot.runtime_mode!=='shared')await ai.stop(bot.id);
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
     if(!recovering||!(await ai.probe(ctx)).ready)await ai.start(ctx);
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
 async health(){await this.host.beginInspection();try{const selected=await this.db.query("SELECT b.* FROM bots b WHERE b.deleted_at IS NULL AND (b.desired_state='running' OR (b.desired_state='offline' AND b.observed_state!='offline') OR b.health->'ai'->>'alive'='true' OR b.health->'protocol'->>'alive'='true' OR EXISTS (SELECT 1 FROM login_sessions s WHERE s.bot_id=b.id AND s.state NOT IN ('online','expired','canceled','failed')))");const running=new Set(selected.filter(b=>b.desired_state==='running').map(b=>b.id));for(const id of this.recovery.keys())if(!running.has(id))this.recovery.delete(id);for(const bot of selected){try{
       if(bot.desired_state==='offline'){await this.db.query("UPDATE commands SET status='pending',error=NULL WHERE bot_id=$1 AND generation=$2 AND kind='evict-offline' AND status='failed' AND finished_at<now()-interval '30 seconds'",[bot.id,bot.generation]);continue}
       const ctx=await this.context(bot);const ai=await this.aiFor(bot).probe(ctx);const protocol=await this.protocols[bot.protocol].getHealth(ctx);
       if(protocol.online){try{const uin=await this.protocols[bot.protocol].queryIdentity(ctx);const [identityOwner]=await this.db.query('SELECT generation,deleted_at FROM bots WHERE id=$1',[bot.id]);if(!identityOwner||identityOwner.deleted_at||identityOwner.generation!==bot.generation)continue;if(!uin||!/^\d{5,20}$/.test(uin)||(ctx.expectedUin&&ctx.expectedUin!==uin))throw new Error('恢复会话的QQ身份不匹配');await this.db.transaction(async tx=>{const [owner]=await tx.query('SELECT generation,deleted_at,desired_state,expected_uin FROM bots WHERE id=$1 FOR UPDATE',[bot.id]);if(!owner||owner.deleted_at||owner.desired_state==='deleting'||owner.generation!==bot.generation)return;const [binding]=await tx.query('SELECT qq_uin FROM qq_bindings WHERE bot_id=$1',[bot.id]);const pinned=binding?.qq_uin||owner.expected_uin;if(pinned&&pinned!==uin)throw new Error('QQ身份不一致，换号需删除机器人后重新创建');await tx.query("INSERT INTO qq_bindings(bot_id,qq_uin,binding_kind) VALUES($1,$2,$3) ON CONFLICT(bot_id) DO UPDATE SET qq_uin=$2,binding_kind=$3",[bot.id,uin,bot.protocol==='mock'?'mock':'real'])})}catch(e){const [identityOwner]=await this.db.query('SELECT generation,deleted_at FROM bots WHERE id=$1',[bot.id]);if(!identityOwner||identityOwner.deleted_at||identityOwner.generation!==bot.generation)continue;await this.protocols[bot.protocol].stop(bot.id);protocol.online=false;protocol.alive=false;protocol.ready=false;protocol.detail='QQ身份冲突，已停止协议进程';await this.log(bot,protocol.detail)}}
       const [login]=await this.db.query("SELECT id FROM login_sessions WHERE bot_id=$1 AND state NOT IN ('online','expired','canceled','failed')",[bot.id]);
       const [pending]=await this.db.query("SELECT id FROM commands WHERE bot_id=$1 AND status IN ('pending','processing')",[bot.id]);
       const onlineSince=bot.health?.onlineSince||(protocol.online&&(bot.desired_state!=='running'||ai.ready)?Date.now():undefined);
       const offline=(bot.desired_state==='running'||ai.alive||protocol.alive)?observeOffline(bot.health?.offline,protocol,Date.now(),this.offlineGrace,{ai:bot.desired_state==='running'?ai:undefined,onlineSince,requestedAt:bot.health?.runtimeRequestedAt}):undefined;
       const health={ai,protocol,offline,onlineSince,runtimeRequestedAt:bot.health?.runtimeRequestedAt,mode:bot.protocol==='mock'?'mock':'real',sampledAt:new Date().toISOString()};const observed=bot.desired_state==='stopped'?'stopped':ai.ready?(protocol.online?'online':'degraded'):ai.alive?'starting':'failed';await this.db.query('UPDATE bots SET health=$2,observed_state=$3,applied_revision=CASE WHEN $4 THEN $5 ELSE applied_revision END WHERE id=$1 AND generation=$6 AND revision=$5 AND desired_state=$7',[bot.id,JSON.stringify(health),observed,ai.ready,ctx.revision,ctx.generation,bot.desired_state]);await this.db.query('UPDATE instances SET heartbeat_at=now(),health=$2,generation=$3 WHERE bot_id=$1 AND EXISTS(SELECT 1 FROM bots WHERE id=$1 AND generation=$3 AND revision=$4)',[bot.id,JSON.stringify(health),ctx.generation,ctx.revision]);
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
       if(bot.desired_state==='running'&&!login&&!pending){
        if(ai.alive&&protocol.alive){if(!retry.healthySince)retry.healthySince=Date.now();if(Date.now()-retry.healthySince>60000)retry.attempts=0}
        else{retry.healthySince=0;if(retry.attempts<3&&Date.now()>=retry.next){const [restartPending]=await this.db.query("SELECT id FROM commands WHERE bot_id=$1 AND status IN ('pending','processing')",[bot.id]);if(!restartPending){retry.attempts++;retry.next=Date.now()+5000*2**retry.attempts;await this.db.query("INSERT INTO commands(id,bot_id,kind,generation,idempotency_key) SELECT $4,id,'restart',generation,$2 FROM bots WHERE id=$1 AND generation=$3 AND desired_state='running'",[bot.id,`recover-${ctx.generation}-${Date.now()}`,ctx.generation,randomUUID()]);await this.log(bot,`检测到受控进程退出，恢复尝试 ${retry.attempts}/3；失败后退避，禁止无限重启`)}}}
        this.recovery.set(bot.id,retry);
       }else if(bot.desired_state!=='running')this.recovery.delete(bot.id);
   }catch(e){await this.log(bot,(e as Error).message)}}}finally{this.host.endInspection()}}
}
