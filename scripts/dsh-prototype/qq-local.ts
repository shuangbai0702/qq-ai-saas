/** Local QQ acceptance host. No production roots, autonomous scheduler or new QQ transport. */
import path from 'node:path';
import {readFile,writeFile} from 'node:fs/promises';import {createHash} from 'node:crypto';
import {openDatabase} from '../../packages/database/index.js';
import {loadKey} from '../../packages/shared/security.js';
import {defaultConfig,type RuntimeContext} from '../../packages/contracts/index.js';
import {ProcessHost} from '../../apps/node-agent/src/process-host.js';
import {AstrBotAdapter} from '../../adapters/astrbot/index.js';
import {BilibiliTools} from '../../packages/database/bilibili-tools.js';
import {BilibiliExecutor} from '../../apps/node-agent/src/bilibili/executor.js';
import {DshHost} from '../../apps/node-agent/src/dsh-host.js';
import type {NodeAgent} from '../../apps/node-agent/src/agent.js';
import {verifyBridge} from '../../packages/shared/tool-bridge.js';
import Fastify from 'fastify';
import {registerBilibili} from '../../apps/control-api/src/bilibili.js';
import {SingleFlight} from '../../packages/shared/single-flight.js';

if(process.env.FISHBOT_DSH_QQ_LOCAL!=='1'||!process.env.FISHBOT_DSH_TEST_API_KEY)throw Error('Explicit local QQ experiment and credential required');
// This operator-selected model origin resolves to a local Clash fake-IP here.
// Keep the original host/port pinning; grant no other model origins an exception.
process.env.FISHBOT_MODEL_ALLOWED_PRIVATE_ORIGINS='https://nai.lunarbyte.pw';
const root=path.resolve('.local/dsh-qq-login'),identity=JSON.parse(await readFile(path.join(root,'identity.json'),'utf8'));
const status=JSON.parse(await readFile(path.join(root,'status.json'),'utf8'));
if(status.state!=='online'||!/^\d{5,20}$/.test(status.uin))throw Error('Local QQ must already be online');
const config=structuredClone(defaultConfig);config.baseUrl='https://nai.lunarbyte.pw/v1';config.model='deepseek-v4.1-flash';config.provider='custom';config.proactive=false;
config.behavior.groupParticipationGroups=[];config.behavior.groupParticipationEnabled=false;config.behavior.ownerContactEnabled=false;config.behavior.autonomousGroupContact=false;
config.behavior.selfDevelopment=false;
const db=await openDatabase(root,''),key=await loadKey(root,{createIfMissing:true});
await db.query('INSERT INTO tenants(id,name) VALUES($1,$2) ON CONFLICT DO NOTHING',[identity.tenantId,'本地DSH验收']);
await db.query("INSERT INTO bots(id,tenant_id,name,protocol,generation,revision,runtime_mode,expected_uin,desired_state) VALUES($1,$2,'本地小双白','napcat',1,1,'dedicated',$3,'running') ON CONFLICT DO NOTHING",[identity.botId,identity.tenantId,status.uin]);
await db.query('INSERT INTO bot_config_revisions(bot_id,revision,config) VALUES($1,1,$2) ON CONFLICT DO NOTHING',[identity.botId,JSON.stringify(config)]);
await db.query('INSERT INTO instances(bot_id,generation,ai_port,protocol_port,token) VALUES($1,1,24872,24874,$2) ON CONFLICT DO NOTHING',[identity.botId,identity.token]);
const [bot]=await db.query('SELECT * FROM bots WHERE id=$1',[identity.botId]);
const [storedConfig]=await db.query('SELECT config FROM bot_config_revisions WHERE bot_id=$1 AND revision=$2',[bot.id,bot.revision]);
Object.assign(config,storedConfig.config);
const owner=process.env.FISHBOT_DSH_OWNER_UIN;
if(owner){
 if(!/^\d{5,20}$/.test(owner))throw Error('Invalid local owner');
 if(config.behavior.ownerUin&&config.behavior.ownerUin!==owner)throw Error('Existing local owner differs');
 if(config.behavior.ownerUin!==owner){
  config.behavior.ownerUin=owner;
  await db.transaction(async tx=>{
   const updated=await tx.query('UPDATE bots SET revision=revision+1 WHERE id=$1 AND tenant_id=$2 AND revision=$3 RETURNING revision',[bot.id,bot.tenant_id,bot.revision]);
   if(updated.length!==1)throw Error('Local configuration CAS conflict');
   await tx.query('INSERT INTO bot_config_revisions(bot_id,revision,config) VALUES($1,$2,$3)',[bot.id,updated[0].revision,JSON.stringify(config)]);bot.revision=updated[0].revision;
  });
 }
}
const ctx:RuntimeContext={...identity,root:path.join(root,'bots',identity.botId),generation:bot.generation,revision:bot.revision,aiPort:24872,protocolPort:24874,config,apiKey:process.env.FISHBOT_DSH_TEST_API_KEY,expectedUin:status.uin,runtimeMode:'dedicated'};
const processHost=new ProcessHost(root,path.resolve('references/astrbot/.venv/Scripts/python.exe'));
const astrbot=new AstrBotAdapter(processHost,path.resolve('references/astrbot'),path.resolve('references/astrbot/.venv/Scripts/python.exe'));
const repo=new BilibiliTools(db),executor=new BilibiliExecutor(db,root,key);
const settings=await repo.settings(bot.tenant_id,bot.id);
if(!settings.config.enabled)await repo.configure(bot.tenant_id,bot.id,settings.revision,{enabled:true,allowAnonymous:true,scopes:['public.read']});
const allowedScopes=(process.env.FISHBOT_DSH_BILIBILI_SCOPES||'public.read').split(',');
if(process.env.FISHBOT_DSH_BILIBILI_SCOPES){
 if(!config.behavior.ownerUin)throw Error('Authenticated experiment requires configured owner');
 await executor.accounts.credential(bot.tenant_id,bot.id);
 const current=await repo.settings(bot.tenant_id,bot.id);
 if(JSON.stringify(current.config.scopes)!==JSON.stringify(allowedScopes))await repo.configure(bot.tenant_id,bot.id,current.revision,{...current.config,allowAnonymous:false,scopes:allowedScopes});
}
// The production host owns the native kernel, model route and protocol-v2
// callbacks. This harness supplies only the existing local runtime identity.
let closing=false;
const bridgeToken=createHash('sha256').update(identity.token+'local-dsh').digest('hex');
const localAgent={db,key,root,bilibili:executor,get closed(){return closing},
 async context(live:any){
  if(live.id!==bot.id||live.tenant_id!==bot.tenant_id||live.generation!==ctx.generation||live.revision!==ctx.revision)throw Error('Local runtime configuration changed; restart at an idle boundary');
  return ctx;
 }};
const dsh=new DshHost(localAgent as unknown as NodeAgent);
// Reuse original account, tool-completion and execution-memory routes. Each
// internal endpoint still validates its original runtime bridge credential.
const accountApi=Fastify({logger:false});
const accountRoutes=new Set(['GET /v1/bots/:id/bilibili','POST /v1/bots/:id/bilibili/login','GET /v1/bots/:id/bilibili/login/:login/qr','POST /shutdown']);
accountApi.addHook('onRequest',async(req,reply)=>{
 if(closing||req.headers.origin)return reply.code(403).send({error:'local_account_access_denied'});
 const route=req.routeOptions.url||'';
 if(route.startsWith('/internal/v1/bots/:id/')){
  const params=req.params as {id?:string};
  const [live]=await db.query('SELECT * FROM bots WHERE id=$1 AND deleted_at IS NULL',[bot.id]);
  if(params.id!==bot.id||!live||!verifyBridge(key,live,String(req.headers.authorization||'').replace(/^Bearer /,'')))return reply.code(403).send({error:'local_runtime_access_denied'});
  return;
 }
 if(req.headers.authorization!=='Bearer '+bridgeToken||!accountRoutes.has(req.method+' '+route))return reply.code(403).send({error:'local_account_access_denied'});
});
registerBilibili(accountApi,db,key,async(_req,id)=>{
 if(id!==bot.id)throw Error('Foreign Bot denied');
 const [live]=await db.query('SELECT * FROM bots WHERE id=$1 AND tenant_id=$2',[bot.id,bot.tenant_id]);return live;
},root);
async function assertIdle(){
 const response=await fetch(`http://127.0.0.1:${ctx.aiPort+1}/health`,{headers:{Authorization:'Bearer '+ctx.token},signal:AbortSignal.timeout(1500)});
 const health:any=await response.json();
 if(!response.ok||health.generation!==ctx.generation||health.revision!==ctx.revision||health.activity?.idle!==true||executor.busy||executor.mediaBusy)throw Error('Local AI is not confirmed idle; leave QQ and AI running');
}
accountApi.post('/shutdown',async(_req,reply)=>{
 try{await assertIdle()}catch{return reply.code(409).send({error:'local_ai_not_idle'})}
 reply.send({ok:true});setImmediate(()=>void close().catch(error=>console.error('Local shutdown:',error.message)));
});
// The original Bilibili worker publishes this URL in its canonical bridge.
process.env.FISHBOT_TOOL_API_URL='http://127.0.0.1:24879';
const healthFlight=new SingleFlight();let healthTimer:NodeJS.Timeout|undefined,workerTimer:NodeJS.Timeout|undefined;
async function health(){
 const ai=await astrbot.probe(ctx);
 await db.query('UPDATE bots SET health=$2,observed_state=$3 WHERE id=$1',[bot.id,JSON.stringify({sampledAt:new Date().toISOString(),protocol:{online:true},ai}),ai.ready?'running':'starting']);
}
// Refuse to adopt an old AI process under new source/config. The operator can
// stop only that AI at its confirmed idle boundary; QQ login is untouched.
if(await processHost.inspect(bot.id,'ai')){await db.close();throw Error('Existing local AI must be stopped at a confirmed idle boundary before starting this harness')}
try{
 await executor.recover();
 await accountApi.listen({host:'127.0.0.1',port:24879});
 await dsh.start();
 await astrbot.start(ctx);
 await healthFlight.run(health);
 await executor.publishBridges();executor.tick();
 workerTimer=setInterval(()=>executor.tick(),1000);
 healthTimer=setInterval(()=>void healthFlight.run(health).catch(error=>console.error('Local health:',error.message)),10000);
 await writeFile(path.join(root,'runtime.pid'),String(process.pid));
 console.log('Local AstrBot and production DSH host started; QQ login and transport unchanged. Account API/shutdown: 127.0.0.1:24879.');
}catch(error){closing=true;await dsh.stop();await executor.stop();await accountApi.close();await db.close();throw error}
let stopping:Promise<void>|undefined;
function close(){return stopping??=closeOnce()}
async function closeOnce(){
 // A refused shutdown leaves both the harness and the original QQ untouched.
 try{await assertIdle()}catch(error){stopping=undefined;throw error}
 closing=true;clearInterval(healthTimer);clearInterval(workerTimer);
 await dsh.stop();await executor.stop();await healthFlight.pending;
 await astrbot.stop(bot.id);await accountApi.close();await db.close();process.exit();
}
for(const signal of ['SIGINT','SIGTERM'] as const)process.on(signal,()=>void close().catch(error=>console.error('Local shutdown:',error.message)));
