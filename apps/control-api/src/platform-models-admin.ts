import {recoverCreditReceipts} from './platform-credits.js';
import {randomUUID} from 'node:crypto';
import type {FastifyInstance} from 'fastify';
import {z} from 'zod';
import type {Sql} from '../../../packages/database/index.js';
import {encrypt,decrypt} from '../../../packages/shared/security.js';
import {modelHttp} from '../../../packages/shared/model-http.js';
import {platformUpstream} from '../../../packages/shared/platform-model.js';
import {discoverModels,resolveModelBaseUrl} from '../../../packages/shared/model-catalog.js';
import {modelBaseCandidates} from '../../../packages/shared/model-url.js';

const fields='id,name,base_url,model,priority,enabled,revision,last_status,last_checked_at,last_http_status,updated_at';
const input=z.object({name:z.string().trim().min(1).max(100),baseUrl:z.string().min(1).max(6000),model:z.string().trim().min(1).max(200),priority:z.number().int().min(0).max(10000),enabled:z.boolean(),apiKey:z.string().trim().max(4096).regex(/^[^\s\x00-\x1f\x7f]*$/,'Key 不能含空格或控制字符').optional()}).strict();
const fail=(message:string,statusCode=400):never=>{throw Object.assign(new Error(message),{statusCode})};
export type PlatformProvider={id:string;name:string;base_url:string;model:string;secret:string;priority:number;revision:number};

export async function createPlatformModels(db:Sql,key:Buffer,legacy:{primary:string;fallback:string}|'',transport=modelHttp){
 // A one-time import of the existing encrypted credential. Routing reads only
 // the database afterwards; disabled/editable providers are never overwritten.
 if(legacy)await db.transaction(async tx=>{
  await tx.query('LOCK TABLE platform_model_providers IN EXCLUSIVE MODE');
  if((await tx.query('SELECT id FROM platform_model_providers LIMIT 1')).length)return;
  for(const [id,name,base,model,credential,priority] of [
   ['legacy-primary','原主模型','https://crab-ai.net/v1','gpt-6.1-sol',legacy.primary,100],
   ['legacy-fallback','原备用模型',platformUpstream.baseUrl,platformUpstream.model,legacy.fallback,200],
  ] as const){
   if(credential)await tx.query('INSERT INTO platform_model_providers(id,name,base_url,model,secret,priority) VALUES($1,$2,$3,$4,$5,$6)',[id,name,base,model,encrypt(key,'platform-provider:'+id,credential),priority]);
  }
 });
 const available=async(connection:Sql=db)=>!!(await connection.query('SELECT id FROM platform_model_providers WHERE enabled=true LIMIT 1')).length;
 const providers=()=>db.query<PlatformProvider>('SELECT id,name,base_url,model,secret,priority,revision FROM platform_model_providers WHERE enabled=true ORDER BY priority,id');
 const credential=(p:PlatformProvider)=>decrypt(key,'platform-provider:'+p.id,p.secret);
 // A receipt from an edited configuration must not overwrite the new status.
 const observe=(p:PlatformProvider,status:string,httpStatus:number|null=null)=>db.query('UPDATE platform_model_providers SET last_status=$3,last_checked_at=now(),last_http_status=$4 WHERE id=$1 AND revision=$2 RETURNING id',[p.id,p.revision,status,httpStatus]);
 function register(app:FastifyInstance){
  const admin=(req:any)=>{if(!req.user?.is_admin)fail('需要管理员权限',403)};
  app.get('/v1/admin/platform-model-reservations',async req=>{
   admin(req);
   return {summary:await db.query("SELECT dispatch_state,count(*)::int AS count,sum(tokens)::text AS tokens,min(created_at) AS oldest FROM platform_model_reservations GROUP BY dispatch_state ORDER BY dispatch_state"),items:await db.query("SELECT id,tenant_id,bot_id,tokens,campaign_tokens,created_at,dispatch_state,provider_id,dispatched_at,usage_tokens,diagnostic_code FROM platform_model_reservations ORDER BY created_at LIMIT 200")};
  });
  app.post('/v1/admin/platform-model-reservations/reconcile',async req=>{admin(req);return {settled:await recoverCreditReceipts(db)}});
  app.get('/v1/admin/platform-models',async req=>{admin(req);return{items:await db.query(`SELECT ${fields},true AS has_key FROM platform_model_providers ORDER BY priority,id`),priorityOrder:'ascending'}});
  app.post('/v1/admin/platform-models/discover',{config:{rateLimit:{max:12,timeWindow:'1 minute'}}},async(req,reply)=>{
   admin(req);const body=z.object({baseUrl:input.shape.baseUrl,apiKey:input.shape.apiKey,providerId:z.string().max(80).optional()}).strict().parse(req.body);
   let base=body.baseUrl,secret=body.apiKey;
   if(!secret&&body.providerId){const [old]=await db.query<PlatformProvider>('SELECT * FROM platform_model_providers WHERE id=$1',[body.providerId]);if(!old)fail('模型不存在',404);if(!modelBaseCandidates(base).includes(old.base_url))fail('接口地址改变时须重新输入 Key');base=old.base_url;secret=credential(old)}
   if(!secret)fail('请输入模型 Key');
   const cancel=new AbortController(),closed=()=>{if(!reply.raw.writableEnded)cancel.abort()};req.raw.once('aborted',closed);reply.raw.once('close',closed);
   try{return await discoverModels(base,secret!,cancel.signal,transport)}finally{req.raw.off('aborted',closed);reply.raw.off('close',closed)}
  });
  async function save(req:any,id?:string){
   admin(req);
   const body=z.object({...input.shape,...(id?{expectedRevision:z.number().int().min(1)}:{})}).strict().parse(req.body);
   const previous=id?(await db.query<PlatformProvider>('SELECT * FROM platform_model_providers WHERE id=$1',[id]))[0]:undefined;
   if(id&&!previous)fail('模型不存在',404);
   if(previous&&previous.revision!==body.expectedRevision)fail('配置已被修改，请刷新后重试',409);
   if(!previous&&!body.apiKey)fail('请输入模型 Key');
   if(previous&&!body.apiKey&&!modelBaseCandidates(body.baseUrl).includes(previous.base_url))fail('接口地址改变时须重新输入 Key');
   const baseUrl=body.apiKey?await resolveModelBaseUrl(body.baseUrl,body.apiKey,undefined,transport):previous!.base_url,providerId=id||randomUUID();
   return db.transaction(async tx=>{
    if(id){
     const [old]=await tx.query<PlatformProvider>('SELECT * FROM platform_model_providers WHERE id=$1 FOR UPDATE',[id]);
     if(!old)fail('模型不存在',404);
     if(old.revision!==body.expectedRevision)fail('配置已被修改，请刷新后重试',409);
     if(baseUrl!==old.base_url&&!body.apiKey)fail('接口地址改变时须重新输入 Key');
     const changed=baseUrl!==old.base_url||body.model!==old.model||!!body.apiKey;
     await tx.query(`UPDATE platform_model_providers SET name=$2,base_url=$3,model=$4,secret=$5,priority=$6,enabled=$7,revision=revision+1,updated_at=now(),last_status=CASE WHEN $8 THEN 'unchecked' ELSE last_status END,last_checked_at=CASE WHEN $8 THEN NULL ELSE last_checked_at END,last_http_status=CASE WHEN $8 THEN NULL ELSE last_http_status END WHERE id=$1`,[id,body.name,baseUrl,body.model,body.apiKey?encrypt(key,'platform-provider:'+id,body.apiKey):old.secret,body.priority,body.enabled,changed]);
    }else{
     if(!body.apiKey)fail('请输入模型 Key');
     await tx.query('INSERT INTO platform_model_providers(id,name,base_url,model,secret,priority,enabled) VALUES($1,$2,$3,$4,$5,$6,$7)',[providerId,body.name,baseUrl,body.model,encrypt(key,'platform-provider:'+providerId,body.apiKey!),body.priority,body.enabled]);
    }
    await tx.query("INSERT INTO audit_events(tenant_id,event,detail) VALUES($1,'admin.platform_model.saved',$2)",[req.user.tenant_id,JSON.stringify({actorId:req.user.id,providerId,name:body.name,model:body.model,priority:body.priority,enabled:body.enabled})]);
    return{item:(await tx.query(`SELECT ${fields},true AS has_key FROM platform_model_providers WHERE id=$1`,[providerId]))[0]};
   });
  }
  app.post('/v1/admin/platform-models',async req=>save(req));
  app.put<{Params:{id:string}}>('/v1/admin/platform-models/:id',async req=>save(req,z.string().min(1).max(80).parse(req.params.id)));
  app.post<{Params:{id:string}}>('/v1/admin/platform-models/:id/probe',{config:{rateLimit:{max:6,timeWindow:'1 minute'}}},async req=>{
   admin(req);const id=z.string().min(1).max(80).parse(req.params.id),{expectedRevision}=z.object({expectedRevision:z.number().int().min(1)}).strict().parse(req.body);
   const [p]=await db.query<PlatformProvider>('SELECT * FROM platform_model_providers WHERE id=$1',[id]);
   if(!p)fail('模型不存在',404);if(p.revision!==expectedRevision)fail('配置已被修改，请刷新后重试',409);
   let status='unconfirmed',httpStatus:number|null=null;
   try{
    const response=await transport(p.base_url+'/chat/completions',{method:'POST',headers:{Authorization:'Bearer '+credential(p),'Content-Type':'application/json'},body:JSON.stringify({model:p.model,messages:[{role:'user',content:'Reply OK.'}],max_tokens:16,stream:false}),signal:AbortSignal.timeout(25000)});
    httpStatus=response.status;
    if(!response.ok){status=[401,403].includes(response.status)?'rejected':response.status===429||response.status===402?'limited':'failed';await response.body?.cancel().catch(()=>{})}
    else{
     const reader=response.body?.getReader();let text='',bytes=0;
     try{if(reader){const decoder=new TextDecoder();while(true){const r=await reader.read();if(r.done)break;bytes+=r.value.byteLength;if(bytes>65536)throw Error('probe_response_too_large');text+=decoder.decode(r.value,{stream:true})}text+=decoder.decode()}}finally{await reader?.cancel().catch(()=>{});reader?.releaseLock()}
     const data=JSON.parse(text);status=Array.isArray(data?.choices)&&data.choices.some((c:any)=>typeof c?.message?.content==='string'&&c.message.content.length>0)?'available':'unconfirmed';
    }
   }catch{/* Never expose upstream messages/credentials or infer timeout=refused. */}
   const updated=await observe(p,status,httpStatus);
   return{status,httpStatus,applied:updated.length>0};
  });
 }
 return{available,providers,credential,observe,register};
}
export type PlatformModels=Awaited<ReturnType<typeof createPlatformModels>>;
