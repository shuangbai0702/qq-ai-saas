import type {FastifyInstance} from 'fastify';
import {z} from 'zod';
import type {Sql} from '../../../packages/database/index.js';
import {AutonomyRepository,AutonomyConflict} from '../../../packages/database/autonomous.js';
import {MAX_CURRENT_GOALS,AutonomyConfigSchema} from '../../../packages/contracts/autonomous.js';
import {runtimeStatus} from '../../../packages/shared/runtime-status.js';

/** Durable control only. No model request or QQ send runs in the public API. */
export function registerAutonomous(app:FastifyInstance,db:Sql,owned:(req:any,id:string)=>Promise<any>){
 const repo=new AutonomyRepository(db);
 const admin=(req:any)=>{if(!req.user?.is_admin)throw Object.assign(new Error('需要管理员权限'),{statusCode:403})};
 app.get('/v1/admin/autonomous',async req=>{
  admin(req);const {page,filter}=z.object({page:z.coerce.number().int().min(1).max(10000).default(1),filter:z.enum(['all','active','attention']).default('attention')}).strict().parse(req.query),pageSize=20;
  const pending="EXISTS(SELECT 1 FROM autonomy_actions a WHERE a.bot_id=s.bot_id AND a.tenant_id=s.tenant_id AND (a.status='unknown' OR a.status='partial' AND a.result->'effects' @> '[{\"status\":\"unknown\"}]'::jsonb))";
  const where=`WHERE b.deleted_at IS NULL AND ($1='all' OR $1='active' AND s.config->>'mode'='active' OR $1='attention' AND (${pending} OR s.last_error IS NOT NULL OR s.lease_until<now() OR s.runtime_epoch<>s.epoch))`;
  const [count]=await db.query(`SELECT count(*)::int AS total FROM autonomy_settings s JOIN bots b ON b.id=s.bot_id AND b.tenant_id=s.tenant_id ${where}`,[filter]);
  const rows=await db.query(`SELECT s.bot_id,s.tenant_id,b.name,b.desired_state,b.health,s.epoch,s.runtime_epoch,s.state,s.config->>'mode' AS mode,s.config->>'rollout' AS rollout,s.next_wake_at,s.last_error,s.lease_until,s.updated_at,${pending} AS unconfirmed,(SELECT json_build_object('usedTokens',d.used_tokens,'reservedTokens',d.reserved_tokens,'modelCalls',d.model_calls,'toolCalls',d.tool_calls,'externalActions',d.external_actions) FROM autonomy_daily_usage d WHERE d.bot_id=s.bot_id AND d.tenant_id=s.tenant_id AND d.day=to_char(now() AT TIME ZONE 'Asia/Shanghai','YYYY-MM-DD')) AS usage FROM autonomy_settings s JOIN bots b ON b.id=s.bot_id AND b.tenant_id=s.tenant_id ${where} ORDER BY s.updated_at DESC,s.bot_id LIMIT $2 OFFSET $3`,[filter,pageSize,(page-1)*pageSize]);
  return{items:rows.map(({health,desired_state,...s})=>({...s,online:runtimeStatus({health,desired_state}).online,fresh:runtimeStatus({health,desired_state}).fresh,automaticReceiptCheckSeconds:30,automaticReceiptCheckMaxSeconds:300,unknownPolicy:'核对同一动作的原回执；结果不明不重发。'})),total:count.total,page,pageSize};
 });
 app.post<{Params:{id:string}}>('/v1/admin/autonomous/:id/pause',async req=>{
  admin(req);const id=z.string().uuid().parse(req.params.id),body=z.object({expectedEpoch:z.number().int().positive(),reason:z.string().trim().min(2).max(200)}).strict().parse(req.body);
  const [bot]=await db.query('SELECT id,tenant_id FROM bots WHERE id=$1 AND deleted_at IS NULL',[id]);if(!bot)throw Object.assign(new Error('机器人不存在'),{statusCode:404});
  const s=await repo.ensure(bot.tenant_id,bot.id);
  try{return await repo.configure(bot.tenant_id,bot.id,body.expectedEpoch,{...s.config,mode:'passive'},{actorId:req.user!.id,reason:body.reason})}catch(error){if(error instanceof AutonomyConflict)throw Object.assign(error,{statusCode:409});throw error}
 });
 app.get<{Params:{id:string}}>('/v1/admin/autonomous/:id/actions',async req=>{
  admin(req);const id=z.string().uuid().parse(req.params.id);
  const [bot]=await db.query('SELECT id,tenant_id FROM bots WHERE id=$1 AND deleted_at IS NULL',[id]);if(!bot)throw Object.assign(new Error('机器人不存在'),{statusCode:404});
  const items=await db.query("SELECT id,cycle_id,epoch,kind,target,status,created_at,finished_at,result->'effects' AS effects,result->'receiptCheck' AS receipt_check FROM autonomy_actions WHERE bot_id=$1 AND tenant_id=$2 AND (status='unknown' OR status='partial' AND result->'effects' @> '[{\"status\":\"unknown\"}]'::jsonb) ORDER BY created_at LIMIT 25",[id,bot.tenant_id]);
  return{items,automaticReceiptCheckSeconds:30,automaticReceiptCheckMaxSeconds:300,canResend:false,reason:'仅查原动作回执；暂停主动模式后仍会查证。QQ离线时保留记录，等待用户重新登录，不自动拉起QQ。'};
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
  const bot=await owned(req,req.params.id);const body=z.object({expectedEpoch:z.number().int().positive()}).strict().parse(req.body),s=await repo.ensure(bot.tenant_id,bot.id);
  if(s.epoch!==body.expectedEpoch||s.config.mode!=='active')throw Object.assign(new Error('自主模式已变化，请刷新'),{statusCode:409});
  // Offline wake persists, but claim() never creates or starts a QQ runtime.
  await repo.wake(bot.tenant_id,bot.id,s.epoch,'operator-wake',0);return{queued:true,startsQQ:false};
 });
 app.post<{Params:{id:string}}>('/v1/bots/:id/autonomous/goals',async req=>{
  const bot=await owned(req,req.params.id);
  const body=z.object({id:z.string().uuid(),expectedEpoch:z.number().int().positive(),expectedRevision:z.number().int().positive(),operation:z.enum(['pause','resume','abandon'])}).strict().parse(req.body);
  return db.transaction(async tx=>{
   const [s]=await tx.query('SELECT * FROM autonomy_settings WHERE bot_id=$1 AND tenant_id=$2 FOR UPDATE',[bot.id,bot.tenant_id]);
   if(!s||s.epoch!==body.expectedEpoch)throw Object.assign(new Error('自主状态已变化，请刷新'),{statusCode:409});
   const status=body.operation==='pause'?'paused_user':body.operation==='abandon'?'abandoned':s.config.mode==='active'?'active':'paused_mode';
   if(body.operation==='resume'){
    const [{count}]=await tx.query("SELECT count(*)::int AS count FROM autonomy_goals WHERE bot_id=$1 AND tenant_id=$2 AND status NOT IN ('completed','abandoned','canceled') AND id<>$3",[bot.id,bot.tenant_id,body.id]);
    if(count>=MAX_CURRENT_GOALS)throw Object.assign(new Error('当前目标已达3个上限，请先完成或结束其他目标'),{statusCode:409});
   }
   const rows=await tx.query('UPDATE autonomy_goals SET status=$4,revision=revision+1,updated_at=now() WHERE id=$1 AND bot_id=$2 AND tenant_id=$3 AND revision=$5 RETURNING id',[body.id,bot.id,bot.tenant_id,status,body.expectedRevision]);
   if(!rows.length)throw Object.assign(new Error('目标已更新，请刷新'),{statusCode:409});
   // Cancel the current decision/permit as well as updating the goal. A late
   // model answer cannot turn a manually paused goal back into active work.
   await tx.query('UPDATE autonomy_settings SET epoch=epoch+1,goal_revision=goal_revision+1,lease_owner=NULL,lease_until=NULL,updated_at=now() WHERE bot_id=$1 AND tenant_id=$2',[bot.id,bot.tenant_id]);
   await tx.query("UPDATE autonomy_actions SET status='canceled',finished_at=now() WHERE bot_id=$1 AND tenant_id=$2 AND status='planned'",[bot.id,bot.tenant_id]);
   await tx.query('DELETE FROM autonomy_wakes WHERE bot_id=$1 AND tenant_id=$2',[bot.id,bot.tenant_id]);
   if(s.config.mode==='active'){
    await repo.rearmHandoffs(bot.tenant_id,bot.id,s.epoch+1,tx);
    await repo.wake(bot.tenant_id,bot.id,s.epoch+1,'goal-edited',0,'',tx);
   }
   return{ok:true,pendingRuntime:true};
  });
 });
}
