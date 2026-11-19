import {randomUUID} from 'node:crypto';
import {readFile} from 'node:fs/promises';
import path from 'node:path';
import {once} from 'node:events';
import type {FastifyInstance,FastifyReply,FastifyRequest} from 'fastify';
import {z} from 'zod';
import type {Sql} from '../../../packages/database/index.js';
import {modelHttp} from '../../../packages/shared/model-http.js';
import {decrypt} from '../../../packages/shared/security.js';
import {platformModelId,platformModelPath,verifyPlatformModelCredential} from '../../../packages/shared/platform-model.js';
import type {PlatformModels} from './platform-models-admin.js';

export const platformSecretPurpose='fishbot:platform-model:v1';
const platformCredentials=z.object({primary:z.string().min(1),fallback:z.string().min(1)});
type PlatformSecrets=z.infer<typeof platformCredentials>;
const quotaRejectionCodes=new Set(['insufficient_quota','insufficient_user_quota','insufficient_token_quota','quota_exhausted']);
const contract=z.object({model:z.literal(platformModelId),messages:z.array(z.record(z.string(),z.unknown())).min(1),stream:z.boolean().optional(),max_tokens:z.number().int().min(1).max(65536).optional(),max_completion_tokens:z.number().int().min(1).max(65536).optional(),tools:z.array(z.unknown()).optional(),tool_choice:z.unknown().optional(),parallel_tool_calls:z.boolean().optional(),temperature:z.number().optional(),top_p:z.number().optional(),stop:z.union([z.string(),z.array(z.string())]).optional(),response_format:z.record(z.string(),z.unknown()).optional(),prompt_cache_key:z.string().max(256).optional()});
const preDispatchRejections=new Set([401,403,404,413,429]);
const invalid=(message:string,statusCode=400)=>Object.assign(new Error(message),{statusCode});
const maxFrameChars=2*1024*1024;
const maxResponseBytes=4*1024*1024;

export async function loadPlatformModelSecret(root:string,key:Buffer):Promise<PlatformSecrets|''>{
 try{
  const value=decrypt(key,platformSecretPurpose,(await readFile(path.join(root,'platform-model-secret'),'utf8')).trim());
  if(!value)return '';
  return value.startsWith('{')?platformCredentials.parse(JSON.parse(value)):{primary:'',fallback:value};
 }catch(error){if((error as NodeJS.ErrnoException).code==='ENOENT')return '';throw error}
}

async function authorizedBot(db:Sql,key:Buffer,auth:string,lock=false){
 const id=auth.match(/^Bearer (fish-platform\.([0-9a-f-]{36})\.[A-Za-z0-9_-]{43})$/i);
 if(!id||!z.uuid().safeParse(id[2]).success)throw invalid('平台模型认证无效',401);
 const [bot]=await db.query(`SELECT b.id,b.tenant_id,b.generation,b.revision,r.config FROM bots b
  JOIN bot_config_revisions r ON r.bot_id=b.id AND r.revision=b.revision WHERE b.id=$1 AND b.deleted_at IS NULL${lock?' FOR SHARE OF b':''}`,[id[2]]);
 if(!bot||bot.config?.platformModel!==true||!verifyPlatformModelCredential(key,bot,id[1]))throw invalid('平台模型认证已失效',401);
 return bot;
}

async function reserve(db:Sql,key:Buffer,auth:string,tokens:number){
 return db.transaction(async tx=>{
  const bot=await authorizedBot(tx,key,auth,true);
  const [budget]=await tx.query(`UPDATE platform_model_credits SET reserved=reserved+$2
   WHERE tenant_id=$1 AND granted-spent-reserved >= $2 RETURNING tenant_id`,[bot.tenant_id,tokens]);
  if(!budget)throw invalid('平台模型余额不足以处理本次请求',402);
  const reservation=randomUUID();
  await tx.query('INSERT INTO platform_model_reservations(id,tenant_id,bot_id,tokens) VALUES($1,$2,$3,$4)',[reservation,bot.tenant_id,bot.id,tokens]);
  return reservation;
 });
}

async function settle(db:Sql,id:string,used:number){
 // The row is only removed in the same commit that applies authoritative usage.
 await db.transaction(async tx=>{
  const [row]=await tx.query('DELETE FROM platform_model_reservations WHERE id=$1 RETURNING tenant_id,tokens',[id]);
  if(row)await tx.query('UPDATE platform_model_credits SET reserved=reserved-$2,spent=spent+$3 WHERE tenant_id=$1',[row.tenant_id,row.tokens,used]);
 });
}

function usage(data:any):number|undefined{
 const u=data?.usage;
 if(!u)return;
 const {prompt_tokens:input,completion_tokens:output,total_tokens:total}=u;
 if([input,output,total].every(n=>Number.isSafeInteger(n)&&n>=0)&&total>0&&total===input+output)return total;
}

function publicFrame(data:any){
 if(data?.error)return {error:{message:'平台模型服务暂时无法完成请求',type:'platform_model_error'}};
 const {id,object,created,choices,usage}=data;
 return {id,object,created,model:platformModelId,choices,usage};
}

async function readCompletion(response:Response,byteLimit=maxResponseBytes){
 const reader=response.body?.getReader();
 if(!reader)throw invalid('平台模型没有返回数据',502);
 const decoder=new TextDecoder();
 const parts:string[]=[];
 let received=0;
 try{
  while(true){
   const chunk=await reader.read();
   if(chunk.done)break;
   received+=chunk.value.byteLength;
   if(received>byteLimit)throw invalid('平台模型响应过大',502);
   parts.push(decoder.decode(chunk.value,{stream:true}));
  }
  parts.push(decoder.decode());
  return JSON.parse(parts.join(''));
 }finally{
  await reader.cancel().catch(()=>{});
  reader.releaseLock();
 }
}

async function quotaRejected(response:Response){
 if(response.status===402)return true;
 if(response.status!==403&&response.status!==429)return false;
 try{
  const data=await readCompletion(response,16*1024);
  return !data?.usage&&!data?.choices&&[data?.error?.code,data?.error?.type].some(code=>quotaRejectionCodes.has(code));
 }catch{return false}
}

async function streamModel(upstream:Response,reply:FastifyReply,signal:AbortSignal,adopt:(frame:unknown)=>Promise<void>){
 if(!upstream.body)throw invalid('平台模型没有返回数据',502);
 reply.hijack();
 const out=reply.raw;
 out.writeHead(200,{'Content-Type':'text/event-stream; charset=utf-8','Cache-Control':'no-store','X-Accel-Buffering':'no'});
 const reader=upstream.body.getReader(),decoder=new TextDecoder();
 let buffer='';
 const output=async(line:string)=>{
  if(out.destroyed||out.writableEnded)return;
  if(out.write(line))return;
  const close=new AbortController();
  const abort=()=>close.abort();
  out.once('close',abort);
  try{if(!out.destroyed)await once(out,'drain',{signal:AbortSignal.any([signal,close.signal])})}catch(error){if(!close.signal.aborted)throw error}finally{out.off('close',abort)}
 };
 try{
  while(true){
   const chunk=await reader.read();
   if(chunk.done)break;
   buffer+=decoder.decode(chunk.value,{stream:true});
   let split:number;
   while((split=buffer.indexOf('\n'))>=0){
    if(split>maxFrameChars)throw new Error('platform_stream_frame_too_large');
    const line=buffer.slice(0,split);buffer=buffer.slice(split+1);
    if(!line.startsWith('data:'))continue;
    const payload=line.slice(5).trim();
    if(payload==='[DONE]'){await output('data: [DONE]\n\n');return;}
    const frame=JSON.parse(payload);
    await adopt(frame);
    await output('data: '+JSON.stringify(publicFrame(frame))+'\n\n');
   }
   if(buffer.length>maxFrameChars)throw new Error('platform_stream_frame_too_large');
  }
 }finally{
  await reader.cancel().catch(()=>{});
  reader.releaseLock();
  if(!out.destroyed&&!out.writableEnded)out.end();
 }
}

export function registerPlatformModel(app:FastifyInstance,db:Sql,key:Buffer,models:PlatformModels,transport=modelHttp){
 const error=(reply:FastifyReply,status:number,message:string)=>reply.code(status).send({error:{message,type:'platform_model_error'}});
 app.post(platformModelPath+'/chat/completions',{bodyLimit:16*1024*1024,config:{rateLimit:{max:180,timeWindow:'1 minute'}},onRequest:async(req,reply)=>{
  if(!await models.available())return error(reply,503,'平台模型暂不可用');
  try{await authorizedBot(db,key,req.headers.authorization||'')}catch(cause){
   const failure=cause as Error & {statusCode?:number};
   if(failure.statusCode!==401)throw cause;
   return error(reply,401,failure.message);
  }
 }},async(req:FastifyRequest,reply:FastifyReply)=>{
  const parsed=contract.safeParse(req.body);
  if(!parsed.success)return error(reply,400,'平台模型请求格式无效');
  const {max_tokens,max_completion_tokens,...request}=parsed.data;
  const outputLimit=max_completion_tokens??max_tokens??32768;
  const providers=await models.providers();
  if(!providers.length)return error(reply,503,'平台模型暂不可用');
  const bodyFor=(model:string)=>JSON.stringify({...request,model,max_tokens:outputLimit,...(request.stream?{stream_options:{include_usage:true}}:{})});
  let provider=providers[0];
  const firstProvider={baseUrl:provider.base_url,model:provider.model};
  const initialBody=bodyFor(firstProvider.model);
  const fallbackModelExtra=Math.max(0,...providers.map(p=>Buffer.byteLength(bodyFor(p.model))-Buffer.byteLength(initialBody)));
  const media=request.messages.some(message=>Array.isArray(message.content)&&message.content.some(part=>part?.type==='image_url'||part?.type==='input_audio'));
  // UTF-8 byte count bounds byte-tokenized text. Media uses a full-context hold.
  const hold=(media?1_048_576:Buffer.byteLength(initialBody)+fallbackModelExtra+4096)+outputLimit;
  let id:string;
  try{id=await reserve(db,key,req.headers.authorization||'',hold)}catch(e){const failure=e as Error & {statusCode?:number};if(!failure.statusCode)throw e;return error(reply,failure.statusCode,failure.message)}
  let adopted=false;
  const adopt=async(frame:unknown)=>{
   const used=usage(frame);
   if(used===undefined||adopted)return;
   await settle(db,id,used);
   adopted=true;
   await models.observe(provider,'available',200).catch(()=>{});
   if(used>hold)app.log.error({reservation:id,used,hold},'platform-model provider exceeded reservation');
  };
  // An HTTP client leaving does not revoke the provider call. Keep reading for its usage receipt.
  const signal=AbortSignal.timeout(110000);
  try{
   const send=(baseUrl:string,credential:string,body:string)=>transport(baseUrl+'/chat/completions',{
    method:'POST',headers:{Authorization:'Bearer '+credential,'Content-Type':'application/json'},body,signal,
   });
   let upstream=await send(provider.base_url,models.credential(provider),initialBody);
   let rejectedForQuota=await quotaRejected(upstream);
   for(let next=1;rejectedForQuota&&next<providers.length;next++){
    await upstream.body?.cancel().catch(()=>{});
    await models.observe(provider,'limited',upstream.status).catch(()=>{});
    app.log.info({reservation:id,providerId:provider.id},'platform-model quota rejected; using next priority');
    provider=providers[next];
    upstream=await send(provider.base_url,models.credential(provider),bodyFor(provider.model));
    rejectedForQuota=await quotaRejected(upstream);
   }
   if(!upstream.ok){
    await models.observe(provider,rejectedForQuota?'limited':'failed',upstream.status).catch(()=>{});
    await upstream.body?.cancel().catch(()=>{});
    if(rejectedForQuota||preDispatchRejections.has(upstream.status))await settle(db,id,0);
    if(rejectedForQuota)return error(reply,503,'平台模型供应商额度暂不可用');
    return error(reply,upstream.status===429?429:502,'平台模型服务暂时无法完成请求');
   }
   if(request.stream){
    await streamModel(upstream,reply,signal,async frame=>{if(Array.isArray((frame as any)?.choices)&&!(frame as any).choices.length)await adopt(frame)});
   }else{
    const result=await readCompletion(upstream);
    await adopt(result);
    if(!adopted){await models.observe(provider,'unconfirmed',upstream.status).catch(()=>{});return error(reply,502,'平台模型未返回可核对的用量，本次额度暂时保留')}
    return publicFrame(result);
   }
  }catch{
   await models.observe(provider,'unconfirmed').catch(()=>{});
   if(!reply.sent&&!reply.raw.destroyed)return error(reply,502,'平台模型未完成请求，本次用量暂无法确认');
  }finally{
   if(!adopted)app.log.info({reservation:id},'platform-model usage was not settled from a completion receipt');
  }
 });
}
