import {randomUUID} from 'node:crypto';
import {z} from 'zod';
import type {FastifyInstance} from 'fastify';
import type {Sql} from '../../../packages/database/index.js';
import {lockOwnedBot,revokeLoginSessions} from './bot-access.js';
const conflict=(message:string)=>Object.assign(new Error(message),{statusCode:409});
export function registerProtocolSettings(app:FastifyInstance,db:Sql,owned:any){
 app.put<{Params:{id:string}}>('/v1/bots/:id/protocol',async req=>{
  const value=z.object({protocol:z.enum(['napcat','llbot']),expectedRevision:z.number().int().positive(),expectedGeneration:z.number().int().nonnegative()}).strict().parse(req.body);
  return db.transaction(async tx=>{
   const bot=await lockOwnedBot(tx,owned,req,req.params.id);
   if(bot.revision!==value.expectedRevision||bot.generation!==value.expectedGeneration)throw conflict('机器人版本已变化，请刷新后重试');
   if(!['napcat','llbot'].includes(bot.protocol))throw conflict('此机器人不支持在这两种QQ协议之间切换');
   if(bot.desired_state!=='stopped'||bot.observed_state!=='stopped')throw conflict('请先停止机器人，等待停止完成后再切换QQ协议');
   const [pending]=await tx.query("SELECT id FROM commands WHERE bot_id=$1 AND status IN ('pending','processing')",[bot.id]);
   if(pending)throw conflict('机器人还有未完成操作，请等待完成');
   if(bot.protocol===value.protocol)return{changed:false,protocol:bot.protocol};
   const id=randomUUID(),generation=bot.generation+1;
   await revokeLoginSessions(tx,{botId:bot.id},{includeOnline:true,message:'QQ协议正在切换，请等待完成后重新连接'});
   await tx.query("UPDATE bots SET generation=$2,observed_state='stopping',last_error=NULL WHERE id=$1",[bot.id,generation]);
   await tx.query("INSERT INTO commands(id,bot_id,idempotency_key,generation,kind) VALUES($1,$2,$3,$4,'switch-protocol')",[id,bot.id,`switch-protocol-${value.protocol}:${id}`,generation]);
   await tx.query("INSERT INTO audit_events(tenant_id,event,bot_id,detail) VALUES($1,'protocol.switch.requested',$2,$3)",[bot.tenant_id,bot.id,JSON.stringify({from:bot.protocol,to:value.protocol,generation})]);
   return{changed:true,command:id,protocol:bot.protocol,target:value.protocol,status:'pending'};
  });
 });
}
