import type {FastifyInstance} from 'fastify';import {z} from 'zod';import type {NodeAgent} from '../../node-agent/src/agent.js';import {discoverModels} from '../../../packages/shared/model-catalog.js';
export function registerModelCatalog(app:FastifyInstance,agent:NodeAgent,owned:(req:any,id:string)=>Promise<any>){
 app.post('/v1/model-catalog',{config:{rateLimit:{max:12,timeWindow:'1 minute'}}},async(req,reply)=>{
  const value=z.object({baseUrl:z.url().max(2000),apiKey:z.string().max(4096).regex(/^[^\x00-\x1f\x7f]*$/,'Key 不能包含控制字符').optional(),botId:z.uuid().optional()}).parse(req.body);let key=value.apiKey;
  if(value.botId){const bot=await owned(req,value.botId);if(!key)key=(await agent.context(bot)).apiKey}
  if(!key)throw Object.assign(new Error('请输入模型 Key，或选择已经保存 Key 的机器人'),{statusCode:400});
  const cancel=new AbortController();const abort=()=>cancel.abort();const closed=()=>{if(!reply.raw.writableEnded)cancel.abort()};req.raw.once('aborted',abort);reply.raw.once('close',closed);
  try{reply.header('Cache-Control','no-store');return await discoverModels(value.baseUrl,key,cancel.signal)}finally{req.raw.off('aborted',abort);reply.raw.off('close',closed)}
 });
}
