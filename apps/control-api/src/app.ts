import {registerCharacterCardLibrary} from './character-card-library.js';
import {CharacterCardsSchema} from '../../../packages/contracts/character-cards.js';
import {registerActivities} from './activities.js';
import {isDeepStrictEqual} from 'node:util';
import {edgeIngressHost,proxyClientIp} from './proxy-client.js';
import {lockOwnedBot,revokeLoginSessions} from './bot-access.js';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {registerAdminOperations} from './admin-operations.js';
import {registerConsoleStatistics} from './console-statistics.js';
import {runtimeStatus} from '../../../packages/shared/runtime-status.js';
import {createRequestBudget} from './request-budget.js';
import {createMemoryMonitor,collectMemory,collectProcesses,readHostSnapshot} from './memory-monitor.js';
import {buildMemoryView} from './memory-view.js';
import {registerBehaviorData} from './behavior-data.js';
import {registerAutonomous} from './autonomous.js';
import {AutonomyRepository} from '../../../packages/database/autonomous.js';
import {registerBilibili} from './bilibili.js';
import {getBotQuota} from './bot-quota.js';
import {probeModel} from '../../../packages/shared/model-probe.js';
import {modelSelectionChanged,validateModelSelection} from '../../../packages/shared/model-catalog.js';
import {registerSocialMedia} from './social-media.js';
import {registerProtocolSettings} from './protocol-settings.js';
import {registerQQFavorites} from './qq-favorites.js';
import {createFavoriteCaptioner} from '../../../packages/shared/qq-favorite-semantics.js';
import {registerInvitations} from './invitations.js';
import {registerModelCatalog} from './model-catalog.js';
import {creditBalance,grantReferral,referralInput,referralInvitationId,registerReferrals} from './referrals.js';
import {loadPlatformModelSecret,registerPlatformModel} from './platform-model.js';
import {createPlatformModels} from './platform-models-admin.js';
import {modelCredentialDestination} from '../../../packages/shared/model-http.js';
import {platformModelPath} from '../../../packages/shared/platform-model.js';
import Fastify from 'fastify';import cookie from '@fastify/cookie';import rateLimit from '@fastify/rate-limit';import serveStatic from '@fastify/static';import {z} from 'zod';
import {randomBytes,randomUUID} from 'node:crypto';import path from 'node:path';import os from 'node:os';import {mkdir,access,open,readFile,rm} from 'node:fs/promises';import QRCode from 'qrcode';
import {openDatabase,configuredDatabaseUrl,hasEncryptedSecrets,platformCreditAvailableSql,type Sql} from '../../../packages/database/index.js';import {hashPassword,verifyPassword,digest,loadKey,encrypt,redact,safeSubprocessEnv} from '../../../packages/shared/security.js';import {defaultConfig,BotConfigSchema,ProtocolSchema,personas,terminalLogin,hasManagedQQLogin} from '../../../packages/contracts/index.js';import type {NodeAgent} from '../../node-agent/src/agent.js';
import {createEmbeddedRuntimeControl,createExternalRuntimeControl} from './bot-runtime-reader.js';
import {httpError,mockProtocolAllowed,requireAdmin} from './http-guards.js';
declare module 'fastify'{interface FastifyRequest{user?:{id:string;email:string;name:string;is_admin:boolean;tenant_id:string}}}
const modelKeyInput=z.string().trim().max(4096).regex(/^[^\s\x00-\x1f\x7f]*$/,'Key 不能包含空格或控制字符').optional();
const provisionJournal=promisify(execFile);
export async function createApp(options:{root?:string;databaseUrl?:string;agent?:boolean;externalAgent?:boolean;logger?:import('fastify').FastifyServerOptions['logger'];platformModelTransport?:Parameters<typeof registerPlatformModel>[4]}={}){
 const root=path.resolve(options.root||process.env.DATA_ROOT||'.local');await mkdir(root,{recursive:true});
 const databaseUrl=options.databaseUrl??await configuredDatabaseUrl();const db=await openDatabase(root,databaseUrl);let key:Buffer;try{key=await loadKey(root,{createIfMissing:!(await hasEncryptedSecrets(db))})}catch(error){await db.close();throw error}
 const externalAgent=options.externalAgent??false;
 const agent=externalAgent?(null as unknown as NodeAgent):new (await import('../../node-agent/src/agent.js')).NodeAgent(db,root,key);
 const runtime=externalAgent?createExternalRuntimeControl(db,root,key):createEmbeddedRuntimeControl(agent);
 const streams=new Set<import('node:http').ServerResponse>();const streamOwners=new Map<string,number>();
 const proxySecret=process.env.FISHBOT_PROXY_SECRET||await readFile(path.join(root,'proxy-client.key'),'utf8').then(s=>s.trim()).catch(error=>{if(error.code==='ENOENT')return undefined;throw error});
 const app=Fastify({logger:options.logger??false,bodyLimit:65536});await app.register(cookie);
 app.addHook('onRequest',async(req,reply)=>{
  if(req.headers.host?.toLowerCase()!==edgeIngressHost)return;
  if(req.routeOptions.url?.startsWith('/internal/')||req.url.startsWith('/internal/'))return reply.code(404).send({error:'接口不存在'});
  if(!proxyClientIp(req.headers,proxySecret))return reply.code(403).send({error:'请求入口无效'});
 });
 await app.register(rateLimit,{max:180,timeWindow:'1 minute',allowList:req=>req.url.startsWith('/internal/')&&req.headers.host?.toLowerCase()!==edgeIngressHost,keyGenerator:req=>{
  const client=proxyClientIp(req.headers,proxySecret);
  return (req.routeOptions.url==='/v1/health'?'health:':'request:')+(client||req.ip);
 }});
 app.addHook('onSend',async(req,reply,payload)=>{reply.header('Strict-Transport-Security','max-age=31536000').header('X-Content-Type-Options','nosniff').header('Referrer-Policy','same-origin').header('X-Frame-Options','DENY').header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'");if(req.url.startsWith('/v1/')&&(!reply.hasHeader('Cache-Control')||reply.statusCode>=400))reply.header('Cache-Control','no-store');return payload});
 app.addHook('preClose',async()=>{for(const stream of streams)stream.end();streams.clear()});
 app.setErrorHandler((error:any,request,reply)=>{const status=error instanceof z.ZodError?400:error.statusCode;const code=Number.isInteger(status)&&status>=400&&status<600?status:500;reply.code(code).send({error:code>=500?'服务器处理失败，请查看管理员日志':redact(error instanceof z.ZodError?error.issues.map(x=>x.message).join('；'):error.message)});if(code>=500)app.log.error({message:redact(error.message),code:error.code},'request failed')});
 // Route onRequest rate limits run before authentication, including rejected
 // sessions. Authentication still completes before any request body is parsed.
 const authenticate=async(req:import('fastify').FastifyRequest,reply:import('fastify').FastifyReply)=>{
   const route=req.routeOptions.url;
   if(!route?.startsWith('/v1/')&&!req.url.startsWith('/v1/'))return;
   const origin=req.headers.origin;const allowed=new Set([process.env.APP_ORIGIN||'http://127.0.0.1:5173',`http://127.0.0.1:${process.env.PORT||8787}`]);
   if(!['GET','HEAD','OPTIONS'].includes(req.method)){if((Number(req.headers['content-length'])>0||req.headers['transfer-encoding'])&&!/^application\/json(?:\s*;|$)/i.test(req.headers['content-type']||''))throw Object.assign(new Error('仅支持 JSON 请求体'),{statusCode:415});let source=origin;const referer=req.headers.referer;if(!source&&referer)try{source=new URL(referer).origin}catch{source='invalid'}if((source&&!allowed.has(source))||req.headers['sec-fetch-site']==='cross-site')throw Object.assign(new Error('请求来源无效'),{statusCode:403})}
   if(['/v1/auth/register','/v1/auth/login','/v1/health',platformModelPath+'/chat/completions'].includes(route??''))return;
   const token=req.cookies.fish_session;if(!token)throw Object.assign(new Error('请先登录'),{statusCode:401});
   const [user]=await db.query('SELECT u.id,u.email,u.name,u.is_admin,m.tenant_id FROM sessions s JOIN users u ON u.id=s.user_id JOIN memberships m ON m.user_id=u.id WHERE s.id=$1 AND s.expires_at>now()',[digest(token)]);if(!user)throw Object.assign(new Error('登录已失效'),{statusCode:401});req.user=user;
 };
 const missingRouteLimit=app.rateLimit();
 app.addHook('preParsing',async(req,reply,payload)=>{
  // Not-found handlers have no onRoute limiter; share the same global bucket.
  if(req.is404)await missingRouteLimit.call(app,req,reply);
  if(!reply.sent)await authenticate(req,reply);
  return payload;
 });
 const fail=(message:string,statusCode=400)=>{throw httpError(message,statusCode)};
 async function owned(req:any,id:string,tx:Sql=db){if(!z.uuid().safeParse(id).success)fail('资源不存在',404);const [bot]=await tx.query('SELECT b.*,q.qq_uin FROM bots b LEFT JOIN qq_bindings q ON q.bot_id=b.id WHERE b.id=$1 AND b.tenant_id=$2 AND b.deleted_at IS NULL',[id,req.user.tenant_id]);if(!bot)fail('机器人不存在',404);return bot}
 async function audit(req:any,event:string,botId?:string){await db.query('INSERT INTO audit_events(tenant_id,event,bot_id) VALUES($1,$2,$3)',[req.user.tenant_id,event,botId||null])}
 const budget=createRequestBudget(),requestReleases=new WeakMap<object,()=>void>();
 app.addHook('onRoute',route=>{const handler=route.handler;route.handler=async function(req,reply){try{return await handler.call(this,req,reply)}finally{requestReleases.get(req)?.();requestReleases.delete(req)}}});
 app.addHook('onError',async req=>{requestReleases.get(req)?.();requestReleases.delete(req)});
 app.addHook('preHandler',async(req,reply)=>{if(!req.user)return;const route=req.routeOptions.url||'';let operation='',max=20,concurrency=2;
  if(route==='/v1/bots'&&req.method==='POST'){operation='create';max=10}
  else if(route.includes('/model-probe')||route.includes('/model-catalog')){operation='model';max=12;concurrency=1}
  else if(route.endsWith('/login-sessions')&&req.method==='POST'){operation='login';max=8;concurrency=1}
  else if(route.startsWith('/v1/admin/')&&!['GET','HEAD'].includes(req.method)){operation='admin';max=30}
  else if(!['GET','HEAD','OPTIONS'].includes(req.method)&&(route.startsWith('/v1/bots/')||route.startsWith('/v1/login-sessions/'))){operation='write';max=30}
  if(!operation)return;const keys=[`${operation}:user:${req.user.id}`,`${operation}:tenant:${req.user.tenant_id}`];let id=route==='/v1/model-catalog'?(req.body as any)?.botId:(req.params as any)?.id;if(route.startsWith('/v1/login-sessions/:id')){if(!z.uuid().safeParse(id).success)fail('登录会话不存在',404);const [login]=await db.query('SELECT bot_id FROM login_sessions WHERE id=$1',[id]);if(!login)fail('登录会话不存在',404);id=login.bot_id}if((route.startsWith('/v1/bots/:id')||route.startsWith('/v1/login-sessions/:id')||route==='/v1/model-catalog')&&id){await owned(req,id);keys.push(`${operation}:bot:${id}`)}const release=budget.acquire(keys,max,concurrency);requestReleases.set(req,release);
 });
 async function session(reply:any,userId:string){const token=randomBytes(32).toString('base64url');await db.query('INSERT INTO sessions(id,user_id,expires_at) VALUES($1,$2,$3)',[digest(token),userId,new Date(Date.now()+7*86400000)]);reply.setCookie('fish_session',token,{path:'/',httpOnly:true,sameSite:'lax',secure:process.env.COOKIE_SECURE==='true',maxAge:7*86400});}
 let platformSecret:Awaited<ReturnType<typeof loadPlatformModelSecret>>='';
 try{platformSecret=await loadPlatformModelSecret(root,key)}catch{
  app.log.error('platform-model credential unreadable; platform model and referral rewards disabled');
 }
 const platformModels=await createPlatformModels(db,key,platformSecret,options.platformModelTransport);
 platformModels.register(app);
 registerPlatformModel(app,db,key,platformModels,options.platformModelTransport);
 registerReferrals(app,db,platformModels.available,key);
 registerActivities(app,db,platformModels.available);
 async function requirePlatformCredit(config:z.infer<typeof BotConfigSchema>,tenantId:string,tx:Sql,alreadyPlatform=false){
  if(config.platformModel&&(!await platformModels.available(tx)||(!alreadyPlatform&&!(await creditBalance(tx,tenantId)).available)))fail('当前工作区暂无平台模型额度，请前往活动专区领取或查看邀请奖励',403);
 }
 // Frequent successful probes must not evict useful operational history.
 app.get('/v1/health',{logLevel:'warn',onResponse:async(req,reply)=>{
  if(reply.statusCode>=400||reply.elapsedTime>=2000)req.log.warn({route:'/v1/health',statusCode:reply.statusCode,responseTime:reply.elapsedTime},'Health probe degraded');
 }},async()=>{await db.query('SELECT 1');return{ok:true,publicOrigin:process.env.APP_ORIGIN||''}});
 app.post('/v1/auth/register',{config:{rateLimit:{max:10,timeWindow:'1 minute'}}},async(req,reply)=>{
   const value=z.object({email:z.email().max(254),password:z.string().min(8).max(128),name:z.string().min(1).max(60),inviteCode:z.string({error:'请输入激活码'}).trim().min(1,'请输入激活码').max(100),referralCode:referralInput}).parse(req.body);const id=randomUUID(),tenant=randomUUID();const hash=await hashPassword(value.password);if(value.referralCode&&!await platformModels.available())fail('邀请活动暂不可用',503);
   const lookup=value.referralCode?'id':'code_hash';
   const activation=value.referralCode?referralInvitationId(key,value.referralCode,value.inviteCode):digest(value.inviteCode);
   try{
    await db.transaction(async tx=>{
     const [invite]=await tx.query(`UPDATE invitations SET used_count=used_count+1
      WHERE ${lookup}=$1 AND revoked_at IS NULL AND expires_at>now() AND used_count<max_uses RETURNING id,bot_limit`,[activation]);
     if(!invite)fail('激活码无效、已过期或已用完');
     await tx.query('INSERT INTO users(id,email,password_hash,name,is_admin) VALUES($1,$2,$3,$4,$5)',[id,value.email.toLowerCase(),hash,value.name,false]);
     await tx.query('INSERT INTO tenants(id,name,bot_limit) VALUES($1,$2,$3)',[tenant,`${value.name}的工作区`,invite.bot_limit]);
     await tx.query('INSERT INTO memberships(user_id,tenant_id) VALUES($1,$2)',[id,tenant]);
     await tx.query('INSERT INTO invitation_redemptions(invitation_id,user_id) VALUES($1,$2)',[invite.id,id]);
     if(value.referralCode)await grantReferral(tx,value.referralCode,id,tenant);
    });
   }catch(e:any){if(e.code==='23505')fail('此邮箱已注册',409);throw e}
   await session(reply,id);return{user:{id,name:value.name,email:value.email}};
 });
 app.post('/v1/auth/login',{config:{rateLimit:{max:15,timeWindow:'1 minute'}}},async(req,reply)=>{const v=z.object({email:z.email(),password:z.string().max(128)}).parse(req.body);const [user]=await db.query('SELECT * FROM users WHERE email=$1',[v.email.toLowerCase()]);if(!user||!await verifyPassword(v.password,user.password_hash))fail('邮箱或密码不正确',401);await session(reply,user.id);return{user:{id:user.id,name:user.name,email:user.email}}});
 app.post('/v1/auth/logout',async(req,reply)=>{await db.query('DELETE FROM sessions WHERE id=$1',[digest(req.cookies.fish_session!)]);reply.clearCookie('fish_session',{path:'/'});return{ok:true}});
 app.get('/v1/auth/me',req=>({user:req.user}));app.get('/v1/personas',()=>({items:personas}));
 registerInvitations(app,db);
 registerCharacterCardLibrary(app,db);
 registerModelCatalog(app,runtime,owned);
 app.get('/v1/bots',async req=>({items:await db.query(`SELECT b.*,q.qq_uin,r.config->>'personaId' AS persona_id,CASE WHEN r.config->>'platformModel'='true' THEN 'platform' ELSE r.config->>'model' END AS model_id,(CASE WHEN r.config->>'platformModel'='true' THEN ((${platformCreditAvailableSql('b.tenant_id')}) OR (EXISTS(SELECT 1 FROM secrets s WHERE s.bot_id=b.id AND s.tenant_id=b.tenant_id) AND coalesce(r.config->>'baseUrl','')<>'' AND coalesce(r.config->>'model','')<>'' AND r.config->>'model'<>'platform')) ELSE EXISTS(SELECT 1 FROM secrets s WHERE s.bot_id=b.id AND s.tenant_id=b.tenant_id) END) IS TRUE AS has_key FROM bots b LEFT JOIN qq_bindings q ON q.bot_id=b.id LEFT JOIN bot_config_revisions r ON r.bot_id=b.id AND r.revision=b.revision WHERE b.tenant_id=$1 AND b.deleted_at IS NULL ORDER BY b.created_at DESC`,[req.user!.tenant_id]),quota:await getBotQuota(db,req.user!.tenant_id)}));
 app.post('/v1/bots',{config:{rateLimit:{max:10,timeWindow:'1 minute'}}},async(req,reply)=>{const v=z.object({name:z.string().trim().min(1).max(80),protocol:ProtocolSchema.default('napcat'),runtimeMode:z.enum(['dedicated','shared']).default('shared'),expectedUin:z.string().regex(/^\d{5,20}$|^$/).default(''),config:BotConfigSchema.default(defaultConfig),apiKey:modelKeyInput}).parse(req.body);if(v.protocol==='mock'&&!mockProtocolAllowed())fail('生产环境已禁用模拟协议',400);const id=randomUUID();
   v.config.behavior.groupParticipationGroups=v.config.behavior.groupParticipationGroups.filter(group=>v.config.groups.includes(group));
   if(!v.config.groups.length)v.config.behavior.groupParticipationEnabled=false;
   v.config.baseUrl=await validateModelSelection(v.config,v.apiKey||'',(req.body as any)?.config?.baseUrl??v.config.baseUrl);
   await db.transaction(async tx=>{await requirePlatformCredit(v.config,req.user!.tenant_id,tx);const quota=await getBotQuota(tx,req.user!.tenant_id,true);if(!quota.canCreate)fail('机器人名额已用完（'+quota.used+'/'+quota.limit+'）。请先删除现有机器人，删除完成后再创建；停止运行不会释放名额。',409);
   await tx.query('INSERT INTO bots(id,tenant_id,name,protocol,expected_uin,runtime_mode) VALUES($1,$2,$3,$4,$5,$6)',[id,req.user!.tenant_id,v.name,v.protocol,v.expectedUin,v.runtimeMode]);await tx.query('INSERT INTO bot_config_revisions(bot_id,revision,config) VALUES($1,1,$2)',[id,JSON.stringify(v.config)]);
   if(v.config.proactive)await new AutonomyRepository(db).enableProactive(req.user!.tenant_id,id,tx);
   await tx.query('LOCK TABLE instances IN SHARE ROW EXCLUSIVE MODE');
   const used=new Set((await tx.query('SELECT ai_port FROM instances')).map(row=>row.ai_port));const offset=Math.floor(Math.random()*4000);let port=0;for(let slot=0;slot<4000;slot++){const candidate=20000+((slot+offset)%4000)*4;if(!used.has(candidate)){port=candidate;break}}if(!port)fail('节点端口池已满，请联系管理员',503);
   await tx.query('INSERT INTO instances(bot_id,generation,ai_port,protocol_port,token) VALUES($1,0,$2,$3,$4)',[id,port,port+2,randomBytes(32).toString('hex')]);
   if(v.apiKey)await tx.query('INSERT INTO secrets(id,tenant_id,bot_id,ciphertext) VALUES($1,$2,$3,$4)',[randomUUID(),req.user!.tenant_id,id,encrypt(key,`${req.user!.tenant_id}:${id}`,v.apiKey)]);
   });
   const python=path.resolve(process.env.ASTRBOT_PYTHON||`references/astrbot/.venv/${process.platform==='win32'?'Scripts/python.exe':'bin/python'}`);
   const groupLimit='2';
   try{await provisionJournal(python,['-B',path.resolve('scripts/initialize-shared-journal.py'),path.join(root,'bots',id),groupLimit],{timeout:10000,windowsHide:true,env:safeSubprocessEnv({PYTHONIOENCODING:'utf-8'})})}
   catch(error){await db.transaction(async tx=>{await tx.query('DELETE FROM autonomy_settings WHERE bot_id=$1',[id]);await tx.query('DELETE FROM secrets WHERE bot_id=$1',[id]);await tx.query('DELETE FROM instances WHERE bot_id=$1',[id]);await tx.query('DELETE FROM bot_config_revisions WHERE bot_id=$1',[id]);await tx.query('DELETE FROM bots WHERE id=$1',[id])}).catch(()=>{});await rm(path.join(root,'bots',id),{recursive:true,force:true}).catch(()=>{});throw error}
   await audit(req,'bot.created',id);reply.code(201);return{id};
 });
 app.get<{Params:{id:string}}>('/v1/bots/:id',async req=>{const bot=await owned(req,req.params.id);const [revision]=await db.query('SELECT config FROM bot_config_revisions WHERE bot_id=$1 AND revision=$2',[bot.id,bot.revision]);const secret=(await db.query('SELECT id FROM secrets WHERE bot_id=$1 AND tenant_id=$2',[bot.id,req.user!.tenant_id])).length>0;const personalReady=secret&&Boolean(revision.config?.baseUrl?.trim()&&revision.config?.model?.trim()&&revision.config?.model!=='platform');const hasKey=revision.config.platformModel?((await platformModels.available()&&(await creditBalance(db,req.user!.tenant_id)).available>0)||personalReady):secret;return{bot,config:revision.config,hasKey,hasPersonalKey:secret,capabilities:runtime.protocolCapabilities(bot.protocol),commands:await db.query('SELECT id,kind,status,error,created_at,idempotency_key FROM commands WHERE bot_id=$1 ORDER BY created_at DESC LIMIT 10',[bot.id])}});
 async function saveConfig(req:any,cardsOnly=false){
  let body=req.body;
  if(cardsOnly){const value=z.object({expectedRevision:z.number().int(),characterCards:CharacterCardsSchema}).strict().parse(body);const bot=await owned(req,req.params.id);const [row]=await db.query('SELECT config FROM bot_config_revisions WHERE bot_id=$1 AND revision=$2',[bot.id,bot.revision]);body={expectedRevision:value.expectedRevision,name:bot.name,expectedUin:bot.expected_uin||'',config:{...row.config,characterCards:value.characterCards}};}
  const v=z.object({expectedRevision:z.number().int(),config:BotConfigSchema,name:z.string().trim().min(1).max(80),expectedUin:z.string().regex(/^\d{5,20}$|^$/).default(''),apiKey:modelKeyInput,clearKey:z.boolean().optional()}).parse(body);let revision=0,personaOnly=false,toolsOnly=false,cardsChanged=false,cardsOnlyChange=false,unchanged=false;
   const existingBot=await owned(req,req.params.id);
   if(!cardsOnly){const allowedGroups=new Set(v.config.groups);
   v.config.behavior.groupParticipationGroups=v.config.behavior.groupParticipationGroups.filter(group=>allowedGroups.has(group));
   if(!allowedGroups.size)v.config.behavior.groupParticipationEnabled=false;}
   if(!cardsOnly){
    if(existingBot.revision!==v.expectedRevision)fail('配置已被其他操作修改，请刷新后重试',409);
    const [saved]=await db.query('SELECT config FROM bot_config_revisions WHERE bot_id=$1 AND revision=$2',[existingBot.id,existingBot.revision]);
    if(!v.config.platformModel&&(v.apiKey||modelSelectionChanged(saved.config,v.config))){
     let credential=v.clearKey?'':v.apiKey||'';
     if(!credential&&!v.clearKey){const connection=await runtime.personalModelConnection(existingBot);if(connection.apiKey&&modelCredentialDestination(connection.baseUrl)!==modelCredentialDestination(v.config.baseUrl))fail('模型服务地址已更改，请输入对应的 API Key');credential=connection.apiKey;}
     v.config.baseUrl=await validateModelSelection(v.config,credential,(req.body as any).config.baseUrl);
    }
   }
   await db.transaction(async tx=>{const bot=await owned(req,req.params.id,tx);await tx.query('SELECT id FROM bots WHERE id=$1 FOR UPDATE',[bot.id]);const [current]=await tx.query('SELECT revision,desired_state,deleted_at,expected_uin FROM bots WHERE id=$1',[bot.id]);if(current.deleted_at||current.desired_state==='deleting')fail('机器人正在删除',409);if(current.revision!==v.expectedRevision)fail('配置已被其他操作修改，请刷新后重试',409);if(v.expectedUin!==(current.expected_uin||''))fail('不能通过配置修改QQ账号。已绑定账号不可更换；如需换号，请删除机器人后重新创建。',409);revision=current.revision+1;
     if(!v.apiKey&&!v.clearKey){
      const [saved]=await tx.query('SELECT r.config FROM bot_config_revisions r JOIN secrets s ON s.bot_id=r.bot_id AND s.tenant_id=$3 WHERE r.bot_id=$1 AND r.revision=$2',[bot.id,current.revision,bot.tenant_id]);
      const destination=(config:z.infer<typeof BotConfigSchema>)=>[modelCredentialDestination(config.baseUrl),modelCredentialDestination(config.behavior?.embeddingBaseUrl||'')];
      const next=destination(v.config);
      if(saved&&destination(saved.config).some((value,index)=>value!==next[index]))fail('模型服务地址已更改，请输入对应的 API Key，或清除已保存的 Key');
     }
     const [prior]=await tx.query('SELECT config FROM bot_config_revisions WHERE bot_id=$1 AND revision=$2',[bot.id,current.revision]);
     if(prior)prior.config=BotConfigSchema.parse(prior.config);
     if(cardsOnly&&isDeepStrictEqual(prior.config.characterCards,v.config.characterCards)){revision=current.revision;unchanged=true;return;}
     cardsChanged=!isDeepStrictEqual(prior.config.characterCards,v.config.characterCards);
     const withoutCards=(config:any)=>Object.fromEntries(Object.entries(config).filter(([key])=>key!=='characterCards'));
     cardsOnlyChange=cardsChanged&&!v.apiKey&&!v.clearKey&&isDeepStrictEqual(withoutCards(prior.config),withoutCards(v.config));
     if(!cardsOnlyChange)await requirePlatformCredit(v.config,req.user!.tenant_id,tx,Boolean(prior?.config?.platformModel));
     const withoutPersona=(config:any)=>Object.fromEntries(Object.entries(config).filter(([key])=>key!=='systemPrompt'));
     personaOnly=prior?.config?.systemPrompt!==v.config.systemPrompt&&!v.apiKey&&!v.clearKey&&isDeepStrictEqual(withoutPersona(prior.config),withoutPersona(v.config));
     const withoutTools=(config:any)=>Object.fromEntries(Object.entries(config).filter(([key])=>!['toolPrompts','delegatedCapabilities'].includes(key)));
     toolsOnly=!!prior&&!v.apiKey&&!v.clearKey&&!isDeepStrictEqual([prior.config.toolPrompts,prior.config.delegatedCapabilities],[v.config.toolPrompts,v.config.delegatedCapabilities])&&isDeepStrictEqual(withoutTools(prior.config),withoutTools(v.config));
     await tx.query('INSERT INTO bot_config_revisions(bot_id,revision,config) VALUES($1,$2,$3)',[bot.id,revision,JSON.stringify(v.config)]);await tx.query('UPDATE bots SET name=$2,revision=$3,expected_uin=$4 WHERE id=$1',[bot.id,v.name,revision,v.expectedUin]);
     if(v.config.proactive&&!prior?.config?.proactive)await new AutonomyRepository(db).enableProactive(bot.tenant_id,bot.id,tx);
     if(v.clearKey)await tx.query('DELETE FROM secrets WHERE bot_id=$1 AND tenant_id=$2',[bot.id,req.user!.tenant_id]);else if(v.apiKey)await tx.query('INSERT INTO secrets(id,tenant_id,bot_id,ciphertext) VALUES($1,$2,$3,$4) ON CONFLICT(bot_id) DO UPDATE SET ciphertext=$4,updated_at=now()',[randomUUID(),req.user!.tenant_id,bot.id,encrypt(key,`${req.user!.tenant_id}:${bot.id}`,v.apiKey)]);
     if(bot.desired_state==='running')await tx.query('INSERT INTO commands(id,bot_id,idempotency_key,generation,kind) VALUES($1,$2,$3,$4,$5)',[randomUUID(),bot.id,`config-${revision}`,bot.generation,'apply']);
     // This audit row is the backend-issued, one-use witness for a System
     // contract transition. No request-body field can mint this permission.
     await tx.query("INSERT INTO audit_events(tenant_id,event,bot_id,detail) VALUES($1,'config.saved',$2,$3)",[req.user!.tenant_id,bot.id,JSON.stringify({actorId:req.user!.id,revision,credentialChanged:!!v.apiKey||!!v.clearKey,changedRegion:cardsOnlyChange?'character_cards':undefined,source:'authenticated_web_config'})]);});let applied=false,applicationError:string|undefined;if(cardsOnlyChange){try{applied=await runtime.applyUserCards(req.params.id,req.user!.tenant_id,revision)}catch{applicationError='角色卡应用失败，已保留重试任务';req.log.warn({botId:req.params.id},'Character cards live apply pending')}}if(personaOnly){try{applied=await runtime.applyUserPersona(req.params.id,req.user!.tenant_id)}catch{req.log.warn({botId:req.params.id},'Persona live apply pending; durable apply command retained')}}if(toolsOnly){try{applied=await runtime.applyUserTools(req.params.id,req.user!.tenant_id)}catch{req.log.warn({botId:req.params.id},'Tool configuration live apply pending')}}return{revision,applied,applicationError,unchanged};
 }
 app.put<{Params:{id:string}}>('/v1/bots/:id/config',{bodyLimit:262144},req=>saveConfig(req));
 app.put<{Params:{id:string}}>('/v1/bots/:id/character-cards',{bodyLimit:131072},req=>saveConfig(req,true));
 app.put<{Params:{id:string}}>('/v1/bots/:id/qq-account',async req=>{
  const v=z.object({expectedRevision:z.number().int(),expectedUin:z.string().regex(/^\d{5,20}$|^$/)}).parse(req.body);
  const result=await db.transaction(async tx=>{const bot=await lockOwnedBot(tx,owned,req,req.params.id);
   if(bot.desired_state==='deleting')fail('机器人正在删除',409);if(bot.revision!==v.expectedRevision)fail('配置已变化，请刷新后重试',409);
   if((bot.expected_uin||'')===v.expectedUin)return{revision:bot.revision,changed:false};
   if(bot.qq_uin)fail('此机器人已绑定QQ '+bot.qq_uin+'，不允许更换或清空。请删除机器人后重新创建。',409);
   const revision=bot.revision+1,generation=bot.generation+1;
   await tx.query('INSERT INTO bot_config_revisions(bot_id,revision,config) SELECT bot_id,$2,config FROM bot_config_revisions WHERE bot_id=$1 AND revision=$3',[bot.id,revision,bot.revision]);
   await tx.query("UPDATE bots SET expected_uin=$2,revision=$3,generation=$4,desired_state='stopped',observed_state='stopping',last_error=NULL,health=jsonb_build_object('runtimeRequestedAt',(extract(epoch from now())*1000)::bigint) WHERE id=$1",[bot.id,v.expectedUin,revision,generation]);
   await tx.query('DELETE FROM qq_bindings WHERE bot_id=$1',[bot.id]);
   await revokeLoginSessions(tx,{botId:bot.id},{includeOnline:true,message:'QQ号已修改，请重新登录'});
   const command=randomUUID();await tx.query("INSERT INTO commands(id,bot_id,idempotency_key,generation,kind) VALUES($1,$2,$1,$3,'stop')",[command,bot.id,generation]);return{revision,changed:true,command};
  });await audit(req,'qq.account.changed',req.params.id);return result;
 });
 app.put<{Params:{id:string}}>('/v1/bots/:id/runtime',async req=>{const v=z.object({mode:z.enum(['dedicated','shared']),expectedRevision:z.number().int()}).parse(req.body);await db.transaction(async tx=>{const bot=await lockOwnedBot(tx,owned,req,req.params.id);if(bot.desired_state!=='stopped'||bot.health?.ai?.alive)fail('请先停止机器人，再切换运行模式',409);if(bot.revision!==v.expectedRevision)fail('配置版本冲突',409);if(bot.runtime_mode!==v.mode){await tx.query('DELETE FROM group_members WHERE bot_id=$1',[bot.id]);await tx.query('UPDATE bots SET group_id=NULL WHERE id=$1',[bot.id]);await tx.query('UPDATE bots SET runtime_mode=$2,generation=generation+1 WHERE id=$1',[bot.id,v.mode]);}});return{mode:v.mode}});
 app.post<{Params:{id:string}}>('/v1/bots/:id/model-probe',{config:{rateLimit:{max:2,timeWindow:'1 minute'}}},async req=>{const value=z.object({extended:z.boolean().default(false)}).parse(req.body??{});const bot=await owned(req,req.params.id);const ctx=await runtime.modelProbeContext(bot);if(!ctx.apiKey)fail('请先保存模型Key');if(ctx.config.platformModel)return{platform:true,configured:await platformModels.available(),...await creditBalance(db,bot.tenant_id)};return probeModel(ctx.config.baseUrl,ctx.config.model,ctx.apiKey,value.extended,{root,botId:bot.id,tenantId:bot.tenant_id,generation:bot.generation,revision:bot.revision})});
 app.post<{Params:{id:string}}>('/v1/bots/:id/actions',async req=>{const v=z.object({action:z.enum(['start','stop','restart']),idempotencyKey:z.string().min(1).max(150)}).parse(req.body);return db.transaction(async tx=>{const bot=await owned(req,req.params.id,tx);await tx.query('SELECT id FROM bots WHERE id=$1 FOR UPDATE',[bot.id]);const [old]=await tx.query('SELECT id,status FROM commands WHERE bot_id=$1 AND idempotency_key=$2',[bot.id,v.idempotencyKey]);if(old)return old;
    const [fresh]=await tx.query('SELECT * FROM bots WHERE id=$1',[bot.id]);if(fresh.deleted_at||fresh.desired_state==='deleting')fail('机器人正在删除',409);
    if((await tx.query("SELECT id FROM commands WHERE bot_id=$1 AND kind='switch-protocol' AND status IN ('pending','processing')",[bot.id])).length)fail('QQ协议切换尚未完成，请等待完成后再操作',409);
    if(v.action==='start'&&fresh.desired_state==='running'){
      const [pending]=await tx.query("SELECT id,status FROM commands WHERE bot_id=$1 AND generation=$2 AND status IN ('pending','processing') ORDER BY created_at DESC LIMIT 1",[bot.id,fresh.generation]);
      const [login]=await tx.query("SELECT id FROM login_sessions WHERE bot_id=$1 AND generation=$2 AND state NOT IN ('online','expired','canceled','failed')",[bot.id,fresh.generation]);
      if(pending||login||runtimeStatus(fresh).online)return{...(pending||{id:null,status:'completed'}),loginSessionId:login?.id,reused:true};
    }
    const generation=fresh.generation+1;const id=randomUUID();await tx.query("UPDATE bots SET desired_state=$2,generation=$3,last_error=NULL,health=jsonb_build_object('runtimeRequestedAt',(extract(epoch from now())*1000)::bigint),observed_state=CASE WHEN $2='stopped' THEN 'stopping' ELSE 'starting' END WHERE id=$1",[bot.id,v.action==='stop'?'stopped':'running',generation]);await revokeLoginSessions(tx,{botId:bot.id});await tx.query('INSERT INTO commands(id,bot_id,idempotency_key,generation,kind) VALUES($1,$2,$3,$4,$5)',[id,bot.id,v.idempotencyKey,generation,v.action]);
    // Observe the normal resume launch; a separate 'login' command would stop it
    // and force QR login, discarding the chance to reuse the saved QQ session.
    let loginSessionId:string|undefined;
    if(v.action==='start'&&hasManagedQQLogin(fresh.protocol)){
      loginSessionId=randomUUID();await tx.query("INSERT INTO login_sessions(id,bot_id,generation,state,expires_at,message) VALUES($1,$2,$3,'starting',$4,$5)",[loginSessionId,bot.id,generation,new Date(Date.now()+180000),'正在尝试恢复已保存的QQ会话；如无法恢复，请扫码登录']);
    }
    return{id,status:'pending',loginSessionId};})});
 app.delete<{Params:{id:string}}>('/v1/bots/:id',async req=>{return db.transaction(async tx=>{const bot=await lockOwnedBot(tx,owned,req,req.params.id);if(bot.desired_state==='deleting'){const [retry]=await tx.query("UPDATE commands SET status='pending',error=NULL,finished_at=NULL WHERE bot_id=$1 AND kind='delete' AND generation=$2 AND status='failed' RETURNING id",[bot.id,bot.generation]);if(retry)return retry;fail('机器人正在删除',409)}const id=randomUUID();await tx.query("UPDATE bots SET desired_state='deleting',generation=generation+1 WHERE id=$1",[bot.id]);await revokeLoginSessions(tx,{botId:bot.id});await tx.query("INSERT INTO commands(id,bot_id,idempotency_key,generation,kind) VALUES($1,$2,$1,$3,'delete')",[id,bot.id,bot.generation+1]);return{id}})});
 app.post<{Params:{id:string}}>('/v1/bots/:id/login-sessions',async req=>{const value=z.object({refresh:z.boolean().default(false)}).parse(req.body??{});return db.transaction(async tx=>{const bot=await lockOwnedBot(tx,owned,req,req.params.id);if(bot.desired_state==='deleting')fail('机器人正在删除');
   if((await tx.query("SELECT id FROM commands WHERE bot_id=$1 AND kind='switch-protocol' AND status IN ('pending','processing')",[bot.id])).length)fail('QQ协议切换尚未完成，请稍后连接',409);
   if(runtimeStatus(bot).online)fail('QQ已在线，无需重新扫码。此机器人不支持切换QQ；如需换号，请删除后重新创建。',409);
   const [old]=await tx.query("SELECT id FROM login_sessions WHERE bot_id=$1 AND generation=$2 AND state NOT IN ('online','expired','canceled','failed')",[bot.id,bot.generation]);if(old&&!value.refresh){
     await tx.query("UPDATE bots SET desired_state='running',observed_state='starting' WHERE id=$1 AND desired_state IN ('stopped','offline')",[bot.id]);return{id:old.id};
   }
   await revokeLoginSessions(tx,{botId:bot.id});
   // Explicit login resumes the Bot, including after Stop. Otherwise QQ can
   // authenticate successfully while health reconciliation detaches its AI.
   const [resumed]=await tx.query("UPDATE bots SET desired_state='running',observed_state='starting',generation=generation+1,health=jsonb_build_object('runtimeRequestedAt',(extract(epoch from now())*1000)::bigint),last_error=NULL WHERE id=$1 RETURNING generation",[bot.id]);
   bot.generation=resumed.generation;
   const id=randomUUID();await tx.query("INSERT INTO login_sessions(id,bot_id,generation,state,expires_at) VALUES($1,$2,$3,'created',$4)",[id,bot.id,bot.generation,new Date(Date.now()+120000)]);await tx.query("INSERT INTO commands(id,bot_id,idempotency_key,generation,kind) VALUES($1,$2,$3,$4,'login')",[randomUUID(),bot.id,`login-${id}`,bot.generation]);return{id};})});
 async function loginOwned(req:any){if(!z.uuid().safeParse(req.params.id).success)fail('登录会话不存在',404);const [s]=await db.query('SELECT * FROM login_sessions WHERE id=$1',[req.params.id]);if(!s)fail('登录会话不存在',404);await owned(req,s.bot_id);return s}
 registerProtocolSettings(app,db,owned);
 registerSocialMedia(app,root,owned);
 registerQQFavorites(app,root,owned,{caption:createFavoriteCaptioner(db,key,root)});
 registerBehaviorData(app,root,owned,runtime,db);
 registerAutonomous(app,db,owned);
 registerBilibili(app,db,key,owned,root);
 const publicLogin=(s:any)=>({...s,qr:undefined,hasQr:!!s.qr});
 app.get<{Params:{id:string}}>('/v1/login-sessions/:id',async req=>publicLogin(await loginOwned(req)));
 app.get<{Params:{id:string}}>('/v1/bots/:id/login-session',async req=>{await owned(req,req.params.id);const [s]=await db.query('SELECT * FROM login_sessions WHERE bot_id=$1 ORDER BY created_at DESC LIMIT 1',[req.params.id]);return s?publicLogin(s):null});
 app.delete<{Params:{id:string}}>('/v1/login-sessions/:id',async req=>{const s=await loginOwned(req);if(terminalLogin.has(s.state))return{ok:true};await db.transaction(async tx=>{await revokeLoginSessions(tx,{sessionId:s.id});await tx.query("INSERT INTO commands(id,bot_id,idempotency_key,generation,kind) VALUES($1,$2,$3,$4,'cancel-login') ON CONFLICT DO NOTHING",[randomUUID(),s.bot_id,`cancel-${s.id}`,s.generation]);});return{ok:true}});
 app.get<{Params:{id:string}}>('/v1/login-sessions/:id/qr',async(req,reply)=>{const s=await loginOwned(req);if(!s.qr||terminalLogin.has(s.state)||new Date(s.expires_at).getTime()<Date.now())fail('二维码已失效',410);let bytes:Buffer;if(s.qr.startsWith('data:image/png;base64,')){bytes=Buffer.from(s.qr.slice(22),'base64');if(bytes.length>1024*1024)fail('二维码过大')}else bytes=await QRCode.toBuffer(s.qr,{width:320});reply.header('Cache-Control','no-store').type('image/png');return bytes});
 app.get<{Params:{id:string}}>('/v1/login-sessions/:id/events',async(req,reply)=>{await loginOwned(req);const owner=req.user!.id;if((streamOwners.get(owner)||0)>=4)fail('实时连接过多，请关闭其他页面后重试',429);streamOwners.set(owner,(streamOwners.get(owner)||0)+1);reply.hijack();streams.add(reply.raw);reply.raw.writeHead(200,{'Content-Type':'text/event-stream','Cache-Control':'no-store','Connection':'keep-alive','X-Accel-Buffering':'no'});let closed=false,busy=false,last=-1;const send=async()=>{if(closed||busy)return;busy=true;try{const [valid]=await db.query('SELECT id FROM sessions WHERE id=$1 AND expires_at>now()',[digest(req.cookies.fish_session!)]);if(!valid){reply.raw.end();return}const s=await loginOwned(req);if(closed)return;if(s.sequence!==last){last=s.sequence;if(!reply.raw.write(`id: ${s.sequence}\ndata: ${JSON.stringify(publicLogin(s))}\n\n`))reply.raw.end()}else if(!reply.raw.write(': heartbeat\n\n'))reply.raw.end();if(terminalLogin.has(s.state))reply.raw.end()}catch{reply.raw.end()}finally{busy=false}};const timer=setInterval(()=>void send(),1000);reply.raw.on('close',()=>{closed=true;streams.delete(reply.raw);const remaining=(streamOwners.get(owner)||1)-1;if(remaining)streamOwners.set(owner,remaining);else streamOwners.delete(owner);clearInterval(timer)});await send()});
 app.post<{Params:{id:string}}>('/v1/bots/:id/mock-transition',async req=>{if(!mockProtocolAllowed())fail('接口不存在',404);const bot=await owned(req,req.params.id);if(bot.protocol!=='mock')fail('仅模拟协议可使用测试状态');const v=z.object({state:z.enum(['waiting_scan','waiting_confirm','online','expired','failed']),uin:z.string().regex(/^\d{5,20}$/).optional()}).parse(req.body);return runtime.mockTransition(bot,v)});
 app.get<{Params:{id:string}}>('/v1/bots/:id/logs',async req=>{const bot=await owned(req,req.params.id);try{const file=await open(path.join(root,'bots',bot.id,'runtime.log'));
   let text:string;
   try{
    const {size}=await file.stat(),start=Math.max(0,size-48000),buffer=Buffer.alloc(size-start);
    const {bytesRead}=await file.read(buffer,0,buffer.length,start);
    text=buffer.toString('utf8',0,bytesRead);
    if(start){const end=text.indexOf('\n');text=end<0?'':text.slice(end+1);}
   }finally{await file.close()}
   const secrets=await runtime.logSecrets(bot);return{lines:redact(text,[secrets.apiKey,secrets.token,process.env.EULER_SIGNER_TOKEN||'']).split('\n').slice(-160)}}catch{return{lines:[]}}});
 registerAdminOperations(app,db);
 registerConsoleStatistics(app,db,root);
 app.get('/v1/admin/overview',async req=>{requireAdmin(req);const bots=await db.query('SELECT id,name,protocol,desired_state,observed_state,health,last_error FROM bots WHERE deleted_at IS NULL');return{total:bots.length,online:bots.filter(b=>b.protocol!=='mock'&&runtimeStatus(b).online).length,bots,server:{platform:os.platform(),totalMemory:os.totalmem(),freeMemory:os.freemem(),apiRss:process.memoryUsage().rss,uptime:os.uptime(),database:databaseUrl?'PostgreSQL':'PGlite（嵌入API进程，内存不可单独分摊）'}}});
 app.get('/v1/admin/memory',async req=>{requireAdmin(req);return{intervalSeconds:30,capacity:240,staleAfterSeconds:90,resetOnRestart:true,...memory.status(),samples:memory.snapshot()}});
 try{await access(path.resolve('apps/web/dist'));await app.register(serveStatic,{root:path.resolve('apps/web/dist')});app.setNotFoundHandler((req,reply)=>(req.url.startsWith('/v1/')||req.url.startsWith('/internal/'))?reply.code(404).send({error:'接口不存在'}):reply.sendFile('index.html'))}catch{}
 try{if(!externalAgent&&options.agent!==false)await agent.begin()}catch(error){await db.close();throw error}const memory=createMemoryMonitor(async()=>{const [sample,processes,host,bots]=await Promise.all([collectMemory(),collectProcesses(root,runtime.pythonExecutable),readHostSnapshot(root),db.query("SELECT b.id,b.name,b.group_id,b.protocol,b.runtime_mode,b.desired_state,b.observed_state,b.expected_uin,b.health,i.heartbeat_at,q.qq_uin FROM bots b LEFT JOIN instances i ON i.bot_id=b.id LEFT JOIN qq_bindings q ON q.bot_id=b.id WHERE b.deleted_at IS NULL")]);for(const p of processes.processes||[])if(p.kind==='api')p.ownerId='platform';sample.details.processSnapshot=processes;sample.details.view=buildMemoryView(sample,processes,bots,host);return sample});app.addHook('onClose',async()=>{await memory.close();budget.clear();try{if(!externalAgent&&options.agent!==false)await agent.shutdown()}finally{await db.close()}});
 return{app,db,agent,root};
}
