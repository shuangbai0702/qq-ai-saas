import type {FastifyInstance} from 'fastify';import {z} from 'zod';import type {NodeAgent} from '../../node-agent/src/agent.js';import {discoverModels} from '../../../packages/shared/model-catalog.js';
import {modelCredentialDestination} from '../../../packages/shared/model-http.js';
import {modelBaseCandidates} from '../../../packages/shared/model-url.js';
export function registerModelCatalog(app:FastifyInstance,agent:NodeAgent,owned:(req:any,id:string)=>Promise<any>){
 app.post('/v1/model-catalog',{config:{rateLimit:{max:12,timeWindow:'1 minute'}}},async(req,reply)=>{
  const value=z.object({baseUrl:z.string().min(1).max(6000),apiKey:z.string().trim().max(4096).regex(/^[^\x00-\x1f\x7f]*$/,'Key 不能包含控制字符').optional(),botId:z.uuid().optional()}).parse(req.body);let key=value.apiKey,baseUrl=value.baseUrl;
  if(value.botId){
   const bot=await owned(req,value.botId);
   if(!key){
    const saved=await agent.personalModelConnection(bot);
    if(!modelBaseCandidates(value.baseUrl).includes(modelCredentialDestination(saved.baseUrl)))throw Object.assign(new Error('模型地址已更改，请输入该地址的 API Key'),{statusCode:400});
    baseUrl=saved.baseUrl;
    key=saved.apiKey;
   }
  }
  if(!key)throw Object.assign(new Error('请输入模型 Key，或选择已经保存 Key 的机器人'),{statusCode:400});
  const cancel=new AbortController();const abort=()=>cancel.abort();const closed=()=>{if(!reply.raw.writableEnded)cancel.abort()};req.raw.once('aborted',abort);reply.raw.once('close',closed);
  try{reply.header('Cache-Control','no-store');return await discoverModels(baseUrl,key,cancel.signal)}finally{req.raw.off('aborted',abort);reply.raw.off('close',closed)}
 });
}
