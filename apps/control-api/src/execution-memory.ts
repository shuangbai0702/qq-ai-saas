import type {FastifyInstance} from 'fastify';
import {z} from 'zod';
import type {Sql} from '../../../packages/database/index.js';
import {ExecutionMemory} from '../../../packages/database/execution-memory.js';
import {toolConflict} from '../../../packages/database/tool-effects.js';

export function registerExecutionMemory(app:FastifyInstance,db:Sql,runtime:(req:any)=>Promise<any>){
 const repo=new ExecutionMemory(db);
 const input=z.object({scope:z.string().regex(/^(self|(?:private|group):[0-9]{1,20})$/),senderId:z.string().regex(/^[0-9]{1,20}$/),query:z.string().max(100).default(''),limit:z.number().int().min(1).max(12).default(6),shareOwnerActivity:z.boolean().default(false),work:z.object({id:z.string().uuid(),epoch:z.number().int().positive()}).strict().optional()}).strict();
 app.post<{Params:{id:string}}>('/internal/v1/bots/:id/memory/experience',async req=>{
  const bot=await runtime(req),v=input.parse(req.body);
  const [config]=await db.query('SELECT config FROM bot_config_revisions WHERE bot_id=$1 AND revision=$2',[bot.id,bot.revision]);
  const behavior=config?.config?.behavior||{},owner=String(behavior.ownerUin||'');
  if(behavior.memory===false)return{memoryEnabled:false,experiences:[],sampledAt:Date.now()};
  if(v.scope==='self'){
   if(!v.work)throw toolConflict('自主经历需要当前真实任务身份',403);
   const [work]=await db.query("SELECT a.id FROM autonomy_actions a JOIN autonomy_settings s ON s.bot_id=a.bot_id AND s.tenant_id=a.tenant_id AND s.epoch=a.epoch WHERE a.id=$1 AND a.bot_id=$2 AND a.tenant_id=$3 AND a.epoch=$4 AND a.status IN ('planned','executing') AND a.expires_at>now() AND s.config->>'mode'='active'",[v.work.id,bot.id,bot.tenant_id,v.work.epoch]);
   if(!work)throw toolConflict('自主经历任务已失效',403);
  }else if(v.scope.startsWith('private:')&&v.scope!=='private:'+v.senderId)throw toolConflict('记忆会话身份不一致',403);
  if(v.shareOwnerActivity&&(!owner||owner!==v.senderId))throw toolConflict('跨会话公开经历只允许真实主人查询',403);
  // This is local ledger recall. It never logs in, calls Bilibili or grants a
  // disabled operation, and is independent of current Bilibili scopes/mode.
  return repo.recall(bot.tenant_id,bot.id,{...v,ownerId:owner});
 });
}
