import type {FastifyInstance} from 'fastify';
import {z} from 'zod';
import type {Sql} from '../../../packages/database/index.js';
import {AutonomyRepository,AutonomyConflict} from '../../../packages/database/autonomous.js';
import {MAX_CURRENT_GOALS,AutonomyConfigSchema} from '../../../packages/contracts/autonomous.js';
import {runtimeClient} from '../../../packages/shared/runtime-client.js';
import {runtimeStatus} from '../../../packages/shared/runtime-status.js';
import {requireAdmin} from './http-guards.js';

/** Durable control only. No model request or QQ send runs in the public API. */
export function registerAutonomous(app:FastifyInstance,db:Sql,owned:(req:any,id:string)=>Promise<any>){
 const repo=new AutonomyRepository(db);
 app.get('/v1/admin/autonomous',async req=>{
  requireAdmin(req);const {page,filter}=z.object({page:z.coerce.number().int().min(1).max(10000).default(1),filter:z.enum(['all','active','attention']).default('attention')}).strict().parse(req.query),pageSize=20;
  const pending="EXISTS(SELECT 1 FROM autonomy_cycles c WHERE c.bot_id=s.bot_id AND c.tenant_id=s.tenant_id AND c.finished_at IS NOT NULL AND c.work_expires_at IS NOT NULL AND (c.reason='lease_expired' OR c.decision->>'receiptPending'='true'))";
  const where=`WHERE b.deleted_at IS NULL AND ($1='all' OR $1='active' AND s.config->>'mode'='active' OR $1='attention' AND (${pending} OR s.last_error IS NOT NULL OR s.lease_until<now() OR s.runtime_epoch<>s.epoch))`;
  const [count]=await db.query(`SELECT count(*)::int AS total FROM autonomy_settings s JOIN bots b ON b.id=s.bot_id AND b.tenant_id=s.tenant_id ${where}`,[filter]);
  const rows=await db.query(`SELECT s.bot_id,s.tenant_id,b.name,b.desired_state,b.health,s.epoch,s.runtime_epoch,s.state,s.config->>'mode' AS mode,s.config->>'rollout' AS rollout,s.next_wake_at,s.last_error,s.lease_until,s.updated_at,${pending} AS unconfirmed,(SELECT json_build_object('usedTokens',d.used_tokens,'reservedTokens',d.reserved_tokens,'modelCalls',d.model_calls,'toolCalls',d.tool_calls,'externalActions',d.external_actions) FROM autonomy_daily_usage d WHERE d.bot_id=s.bot_id AND d.tenant_id=s.tenant_id AND d.day=to_char(now() AT TIME ZONE 'Asia/Shanghai','YYYY-MM-DD')) AS usage FROM autonomy_settings s JOIN bots b ON b.id=s.bot_id AND b.tenant_id=s.tenant_id ${where} ORDER BY s.updated_at DESC,s.bot_id LIMIT $2 OFFSET $3`,[filter,pageSize,(page-1)*pageSize]);
  return{items:rows.map(({health,desired_state,...s})=>({...s,online:runtimeStatus({health,desired_state}).online,fresh:runtimeStatus({health,desired_state}).fresh,automaticReceiptCheckSeconds:30,automaticReceiptCheckMaxSeconds:300,unknownPolicy:'核对同一动作的原回执；结果不明不重发。'})),total:count.total,page,pageSize};
 });
 app.post<{Params:{id:string}}>('/v1/admin/autonomous/:id/pause',async req=>{
  requireAdmin(req);const id=z.string().uuid().parse(req.params.id),body=z.object({expectedEpoch:z.number().int().positive(),reason:z.string().trim().min(2).max(200)}).strict().parse(req.body);
  const [bot]=await db.query('SELECT id,tenant_id,generation,revision FROM bots WHERE id=$1 AND deleted_at IS NULL',[id]);if(!bot)throw Object.assign(new Error('机器人不存在'),{statusCode:404});
  const s=await repo.ensure(bot.tenant_id,bot.id);
  try{return await repo.configure(bot.tenant_id,bot.id,body.expectedEpoch,{...s.config,mode:'passive'},{actorId:req.user!.id,reason:body.reason})}catch(error){if(error instanceof AutonomyConflict)throw Object.assign(error,{statusCode:409});throw error}
 });
 app.get<{Params:{id:string}}>('/v1/admin/autonomous/:id/receipts',async req=>{
  requireAdmin(req);const id=z.string().uuid().parse(req.params.id);
  const [bot]=await db.query('SELECT id,tenant_id,generation,revision FROM bots WHERE id=$1 AND deleted_at IS NULL',[id]);if(!bot)throw Object.assign(new Error('机器人不存在'),{statusCode:404});
  const pending=await repo.unconfirmed(bot.tenant_id,bot.id),call=runtimeClient(db),items=[];
  for(const work of pending){
   const receipt=await call({...bot,bot_id:bot.id},'receipt',{id:work.id},2000).catch(()=>null);
   items.push({id:work.id,cycle_id:work.id,kind:'research',status:receipt?.result?.status||'unknown',effects:receipt?.effects||[],receipt_check:work.result?.receiptCheck});
  }
  return{items,automaticReceiptCheckSeconds:30,automaticReceiptCheckMaxSeconds:300,canResend:false,reason:'按需读取原Work回执；不复制执行历史、不重新派发。QQ离线时保留预算和原Work状态，不自动拉起QQ。'};
 });
 app.get<{Params:{id:string}}>('/v1/bots/:id/autonomous',async req=>{
  const bot=await owned(req,req.params.id);const value=await repo.view(bot.tenant_id,bot.id);
  const status=runtimeStatus(bot);
  return{...value,canConfigureShadow:!!req.user?.is_admin,pendingRuntime:value.settings.epoch!==value.settings.runtime_epoch,online:bot.desired_state==='running'&&status.online,fresh:status.fresh};
 });
 app.put<{Params:{id:string}}>('/v1/bots/:id/autonomous',async req=>{
  const bot=await owned(req,req.params.id),body=z.object({expectedEpoch:z.number().int().positive(),config:AutonomyConfigSchema}).strict().parse(req.body);
  if(body.config.rollout==='shadow'&&!req.user?.is_admin)throw Object.assign(new Error('影子模式仅供管理员测试'),{statusCode:403});
  try{return await repo.configure(bot.tenant_id,bot.id,body.expectedEpoch,body.config)}catch(error){if(error instanceof AutonomyConflict)throw Object.assign(error,{statusCode:409});throw error}
 });
 app.post<{Params:{id:string}}>('/v1/bots/:id/autonomous/wake',async req=>{
  const bot=await owned(req,req.params.id);const body=z.object({expectedEpoch:z.number().int().positive(),requestId:z.string().uuid().optional()}).strict().parse(req.body),s=await repo.ensure(bot.tenant_id,bot.id);
  if(s.epoch!==body.expectedEpoch||s.config.mode!=='active')throw Object.assign(new Error('自主模式已变化，请刷新'),{statusCode:409});
  // Offline wake persists, but claim() never creates or starts a QQ runtime.
  await repo.wake(bot.tenant_id,bot.id,s.epoch,'operator-wake',0,body.requestId??String(req.id));return{queued:true,startsQQ:false};
 });
 app.post<{Params:{id:string}}>('/v1/bots/:id/autonomous/goals',async req=>{
  const bot=await owned(req,req.params.id);
  const body=z.object({id:z.string().uuid(),expectedEpoch:z.number().int().positive(),expectedRevision:z.number().int().positive(),operation:z.enum(['pause','resume','abandon'])}).strict().parse(req.body);
  try{return await repo.controlGoal(bot.tenant_id,bot.id,req.user!.id,body)}catch(error){if(error instanceof AutonomyConflict)throw Object.assign(error,{statusCode:409});throw error}
 });
}
