import {qqExternalBudget,biliWorkEffects} from '../../../packages/database/external-action-budget.js';
import {cycleModelBudget} from '../../../packages/database/model-request-budget.js';
import type {FastifyInstance} from 'fastify';
import {z} from 'zod';
import {randomUUID} from 'node:crypto';
import QRCode from 'qrcode';
import {readFile} from 'node:fs/promises';
import path from 'node:path';
import type {Sql} from '../../../packages/database/index.js';
import {ToolEffects,toolConflict} from '../../../packages/database/tool-effects.js';
import {BilibiliConfigSchema,BiliCallSchema,biliOperations} from '../../../packages/contracts/bilibili.js';
import {BiliAccounts} from '../../node-agent/src/bilibili/account-broker.js';
import {BiliWeb} from '../../node-agent/src/bilibili/web-adapter.js';
import {verifyBridge} from '../../../packages/shared/tool-bridge.js';
import {encrypt} from '../../../packages/shared/security.js';
import {registerAutonomousGoals} from './autonomous-goals.js';
import {registerExecutionMemory} from './execution-memory.js';

export function registerBilibili(app:FastifyInstance,db:Sql,key:Buffer,owned:(req:any,id:string)=>Promise<any>,root:string){
 const repo=new ToolEffects(db),broker=new BiliAccounts(db,key,new BiliWeb());
 const publicAccount=async(t:string,b:string)=>{const [a]=await db.query('SELECT uid,label,revision,state,refresh_state,updated_at FROM bilibili_accounts WHERE tenant_id=$1 AND bot_id=$2',[t,b]);return a??null};
 app.get<{Params:{id:string}}>('/v1/bots/:id/bilibili',async req=>{
  const b=await owned(req,req.params.id),s=await repo.settings(b.tenant_id,b.id);
  const [login]=await db.query('SELECT id,state,expires_at,updated_at FROM bilibili_logins WHERE bot_id=$1 AND tenant_id=$2',[b.id,b.tenant_id]);
  const [cooldown]=await db.query("SELECT until_at,reason FROM tool_service_cooldowns WHERE service='bilibili' AND until_at>now()");
  let mediaReady=false,mediaReason='媒体状态尚未核实',mediaDependencies:any=null;try{const raw=await readFile(path.join(root,'media-status.json'),'utf8');if(raw.length<4096){const m=JSON.parse(raw);mediaReady=m.ready===true&&Date.now()-m.at<15000;mediaDependencies=m;mediaReason=String(m.reason||mediaReason)}}catch{}
  const [botModel]=await db.query('SELECT r.config,(s.ciphertext IS NOT NULL) AS has_key FROM bot_config_revisions r LEFT JOIN secrets s ON s.bot_id=r.bot_id AND s.tenant_id=$3 WHERE r.bot_id=$1 AND r.revision=$2',[b.id,b.revision,b.tenant_id]);
  const modelConfigured=s.config.model.provider==='openai'?!!(s.model_secret&&s.config.model.baseUrl&&s.config.model.model):s.config.model.provider==='bot'&&!!(botModel?.has_key&&botModel.config?.baseUrl&&botModel.config?.model);
  const mediaExecutable=mediaReady&&modelConfigured;
  const jobs=await db.query('SELECT id,operation,state,result,cancel_requested,created_at,updated_at FROM tool_effects WHERE bot_id=$1 AND tenant_id=$2 ORDER BY created_at DESC LIMIT 20',[b.id,b.tenant_id]);
  return{config:s.config,revision:s.revision,hasModelKey:!!s.model_secret,account:await publicAccount(b.tenant_id,b.id),login:login?{...login,state:+new Date(login.expires_at)<Date.now()&&['created','waiting_scan','waiting_confirm'].includes(login.state)?'expired':login.state}:null,cooldown:cooldown??null,jobs,
   operations:Object.entries(biliOperations).map(([name,op])=>({name,scope:op.scope,write:op.write,implemented:name!=='analyze'||mediaExecutable,accountVerified:false})),mediaReady:mediaExecutable,mediaDependencies,modelConfigured,modelVerification:'configured_only_actual_vision_support_requires_provider_receipt',mediaReason:!mediaReady?mediaReason:!modelConfigured?'媒体依赖已就绪，图片模型或Key未配置':mediaReason};
 });
 app.put<{Params:{id:string}}>('/v1/bots/:id/bilibili',async req=>{
  const b=await owned(req,req.params.id),v=z.object({expectedRevision:z.number().int().positive(),config:BilibiliConfigSchema,modelKey:z.string().min(1).max(2000).regex(/^[^\r\n]+$/).optional(),clearModelKey:z.boolean().optional()}).strict().parse(req.body);
  const secret=v.clearModelKey?null:v.modelKey?encrypt(key,broker.scope(b.tenant_id,b.id,'model'),v.modelKey):undefined;
  return repo.configure(b.tenant_id,b.id,v.expectedRevision,v.config,secret);
 });
 app.post<{Params:{id:string}}>('/v1/bots/:id/bilibili/login',async req=>{
  const b=await owned(req,req.params.id);z.object({}).strict().parse(req.body??{});
  return db.transaction(async tx=>{await tx.query('SELECT id FROM bots WHERE id=$1 FOR UPDATE',[b.id]);const [a]=await tx.query('SELECT 1 FROM bilibili_accounts WHERE bot_id=$1 AND tenant_id=$2',[b.id,b.tenant_id]);
   // Existing account may re-authenticate, but verified UID must remain equal.
   const [old]=await tx.query("SELECT id,state FROM bilibili_logins WHERE bot_id=$1 AND tenant_id=$2 AND state IN ('created','waiting_scan','waiting_confirm') AND expires_at>now()",[b.id,b.tenant_id]);if(old)return old;
   const id=randomUUID();await tx.query("INSERT INTO bilibili_logins(bot_id,tenant_id,id,secret,state,expires_at) VALUES($1,$2,$3,'','created',now()+interval '180 seconds') ON CONFLICT(bot_id) DO UPDATE SET id=$3,secret='',state='created',expires_at=now()+interval '180 seconds',updated_at=now()",[b.id,b.tenant_id,id]);return{id,state:'created',reauthenticate:!!a};
  });
 });
 app.get<{Params:{id:string;login:string}}>('/v1/bots/:id/bilibili/login/:login/qr',async(req,reply)=>{
  const b=await owned(req,req.params.id),[s]=await db.query("SELECT secret FROM bilibili_logins WHERE bot_id=$1 AND tenant_id=$2 AND id=$3 AND state IN ('waiting_scan','waiting_confirm') AND expires_at>now()",[b.id,b.tenant_id,req.params.login]);if(!s)throw toolConflict('二维码未准备好或已过期',410);
  const qr=broker.open(b.tenant_id,b.id,s.secret,'login');return reply.header('Cache-Control','no-store').type('image/png').send(await QRCode.toBuffer(qr.url,{width:300}));
 });
 app.delete<{Params:{id:string}}>('/v1/bots/:id/bilibili/login',async req=>{const b=await owned(req,req.params.id);await db.query("UPDATE bilibili_logins SET state='canceled',secret='',updated_at=now() WHERE bot_id=$1 AND tenant_id=$2",[b.id,b.tenant_id]);return{ok:true}});
 app.post<{Params:{id:string}}>('/v1/bots/:id/bilibili/refresh',async req=>{
  const b=await owned(req,req.params.id);z.object({}).strict().parse(req.body??{});
  const rows=await db.query("UPDATE bilibili_accounts SET refresh_state='requested',updated_at=now() WHERE bot_id=$1 AND tenant_id=$2 AND refresh_state IN ('idle','auth_required') RETURNING bot_id",[b.id,b.tenant_id]);if(!rows.length)throw toolConflict('当前无法刷新，请检查账号状态或重新扫码');return{queued:true};
 });
 app.delete<{Params:{id:string}}>('/v1/bots/:id/bilibili/account',async req=>{
  const b=await owned(req,req.params.id),v=z.object({expectedRevision:z.number().int().positive()}).strict().parse(req.body);
  return db.transaction(async tx=>{await tx.query('SELECT id FROM bots WHERE id=$1 FOR UPDATE',[b.id]);const r=await tx.query('DELETE FROM bilibili_accounts WHERE bot_id=$1 AND tenant_id=$2 AND revision=$3 RETURNING uid',[b.id,b.tenant_id,v.expectedRevision]);if(!r.length)throw toolConflict('账号已变化，请刷新');
   await tx.query("UPDATE bilibili_logins SET state='canceled',secret='' WHERE bot_id=$1 AND tenant_id=$2",[b.id,b.tenant_id]);await tx.query("UPDATE tool_effects SET cancel_requested=true WHERE bot_id=$1 AND tenant_id=$2 AND state IN ('queued','claimed','dispatching')",[b.id,b.tenant_id]);
   await tx.query("UPDATE bilibili_settings SET revision=revision+1,config=jsonb_set(jsonb_set(jsonb_set(config,'{scopes}','[\"public.read\"]'),'{enabled}','false'),'{allowAnonymous}','false'),updated_at=now() WHERE bot_id=$1 AND tenant_id=$2",[b.id,b.tenant_id]);return{ok:true,remoteLogout:false};
  });
 });
 app.post<{Params:{id:string;job:string}}>('/v1/bots/:id/bilibili/jobs/:job/cancel',async req=>{const b=await owned(req,req.params.id);const rows=await db.query("UPDATE tool_effects SET cancel_requested=true,updated_at=now() WHERE id=$1 AND bot_id=$2 AND tenant_id=$3 AND state IN ('queued','claimed','dispatching') RETURNING state",[req.params.job,b.id,b.tenant_id]);if(!rows.length)throw toolConflict('任务已结束或不存在',404);return{requested:true,mayHaveDispatched:rows[0].state==='dispatching'}});
 // Separate audience from user sessions and Agent->worker group credentials.
 const runtime=async(req:any)=>{const [b]=await db.query('SELECT * FROM bots WHERE id=$1 AND deleted_at IS NULL',[req.params.id]);const token=String(req.headers.authorization||'').replace(/^Bearer /,'');if(!b||!verifyBridge(key,b,token))throw toolConflict('工具运行身份无效',401);return b};
 registerExecutionMemory(app,db,runtime);
 registerAutonomousGoals(app,db,runtime);
 app.post<{Params:{id:string}}>('/internal/v1/bots/:id/model-request-budget',async req=>{const b=await runtime(req),v=z.object({workId:z.string().min(1).max(200),epoch:z.number().int().positive(),requestId:z.string().min(1).max(300),phase:z.enum(['reserve','settled','released','unknown']),source:z.literal('main'),tokens:z.number().int().nonnegative().optional()}).strict().parse(req.body);return cycleModelBudget(db,b,v)});
 app.post<{Params:{id:string}}>('/internal/v1/bots/:id/external-action-budget',async req=>{const b=await runtime(req),v=z.object({workId:z.string().min(1).max(200),epoch:z.number().int().positive(),logicalActionId:z.string().min(1).max(300),phase:z.enum(['reserve','confirmed','unknown','released'])}).strict().parse(req.body);return qqExternalBudget(db,b,v)});
 app.post<{Params:{id:string}}>('/internal/v1/bots/:id/external-effects',async req=>{const b=await runtime(req),v=z.object({workId:z.string().min(1).max(200)}).strict().parse(req.body);return{effects:await biliWorkEffects(db,b.id,b.tenant_id,v.workId)}});
 app.post<{Params:{id:string}}>('/internal/v1/bots/:id/tools/completion',async req=>{const b=await runtime(req);return{completion:await repo.completion(b)}});
 app.post<{Params:{id:string}}>('/internal/v1/bots/:id/tools/completion-ack',async req=>{const b=await runtime(req),v=z.object({id:z.string().uuid(),owner:z.string().uuid(),status:z.enum(['done','unknown'])}).strict().parse(req.body);return repo.acknowledgeCompletion(b,v.id,v.owner,v.status)});
 app.post<{Params:{id:string}}>('/internal/v1/bots/:id/tools/cancel-work',async req=>{const b=await runtime(req),v=z.object({workId:z.string().min(1).max(200),stepId:z.string().min(1).max(200)}).strict().parse(req.body);return repo.cancelWork(b,v.workId,v.stepId)});
 app.post<{Params:{id:string}}>('/internal/v1/bots/:id/tools',async req=>{const b=await runtime(req);return repo.admit(b,BiliCallSchema.parse(req.body))});
 app.get<{Params:{id:string;job:string}}>('/internal/v1/bots/:id/tools/:job',async req=>{const b=await runtime(req),r=await repo.get(b.tenant_id,b.id,req.params.job);if(!r)throw toolConflict('工具任务不存在',404);return r});
 app.delete<{Params:{id:string;job:string}}>('/internal/v1/bots/:id/tools/:job',async req=>{const b=await runtime(req);await db.query("UPDATE tool_effects SET cancel_requested=true,updated_at=now() WHERE id=$1 AND bot_id=$2 AND tenant_id=$3 AND state IN ('queued','claimed','dispatching')",[req.params.job,b.id,b.tenant_id]);return{cancelRequested:true}});
}
