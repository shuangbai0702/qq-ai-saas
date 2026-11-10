import type {FastifyInstance} from 'fastify';
import {execFile} from 'node:child_process';
import path from 'node:path';
import {z} from 'zod';
import type {NodeAgent} from '../../node-agent/src/agent.js';
import type {Sql} from '../../../packages/database/index.js';

const scope=z.string().regex(/^(group|private):\d{4,20}$/);
const person=z.string().regex(/^\d{4,20}$/);
const fact={scope,subject:person,name:z.string().min(1).max(40),expectedVersion:z.number().int().positive()};
const mutation=z.discriminatedUnion('action',[
 z.object({action:z.literal('edit_fact'),...fact,value:z.string().trim().min(1).max(300)}),
 z.object({action:z.literal('forget_fact'),...fact}),
 z.object({action:z.literal('set_pattern'),scope,expression:z.string().min(2).max(16),expectedUpdated:z.number().finite(),expectedEpoch:z.number().int().nonnegative(),disabled:z.boolean()}),
 z.object({action:z.literal('clear_learning'),scope}),
 z.object({action:z.literal('forget_summary'),scope,id:z.number().int().positive(),expectedUpdated:z.number().finite()}),
 z.object({action:z.literal('pause_contact'),scope}),
 z.object({action:z.literal('reset_relation'),scope,subject:person,expectedLastSeen:z.number().finite()}),
]);

export function registerBehaviorData(app:FastifyInstance,root:string,owned:(req:any,id:string)=>Promise<any>,agent:NodeAgent,db:Sql){
 let maintenance=0;
 const python=path.resolve(process.env.ASTRBOT_PYTHON||`references/astrbot/.venv/${process.platform==='win32'?'Scripts/python.exe':'bin/python'}`);
 async function access(bot:any,input:unknown,runtimeOnly=false){
  const id=bot.id;
  const processId=bot.runtime_mode==='shared'?bot.group_id:id;
  if(!(bot.desired_state==='stopped'&&bot.observed_state==='stopped')&&processId&&await agent.host.inspect(processId,'ai')){
   const ctx=await agent.context(bot);let port=ctx.aiPort+1,route='/behavior-data',token=ctx.token,groupGeneration='';
   if(bot.runtime_mode==='shared'){const group=await agent.shared.group(id);if(!group)throw Object.assign(new Error('运行组尚未就绪'),{statusCode:409});port=group.health_port;token=group.token;groupGeneration=String(group.generation);route=`/members/${id}/behavior-data`}
   const response=await fetch(`http://127.0.0.1:${port}${route}`,{method:'POST',headers:{Authorization:`Bearer ${token}`,'Content-Type':'application/json','X-Group-Generation':groupGeneration,'X-Member-Generation':String(bot.generation),'X-Member-Revision':String(bot.applied_revision)},body:JSON.stringify(input),signal:AbortSignal.timeout(4000)});
   if(!response.ok)throw Object.assign(new Error('机器人正在切换状态，请稍后重试'),{statusCode:409});const result:any=await response.json();if(result.error)throw Object.assign(new Error(result.error),{statusCode:result.status||400});return {...result,accessMode:'runtime-owner'};
  }
  if(runtimeOnly||bot.desired_state==='running')throw Object.assign(new Error('运行实例尚未就绪，请稍后重试'),{statusCode:409});
  const env={...process.env,PYTHONIOENCODING:'utf-8'};delete (env as any).MASTER_KEY;delete (env as any).DATABASE_URL;delete (env as any).FISHBOT_MODEL_KEY;
  if(maintenance>=2)throw Object.assign(new Error('维护请求较多，请稍后重试'),{statusCode:429});
  maintenance++;let data:string;try{data=await new Promise<string>((resolve,reject)=>{
   const child=execFile(python,[path.resolve('adapters/astrbot/data_admin.py'),path.join(root,'bots',id,'behavior.sqlite')],{windowsHide:true,timeout:7000,maxBuffer:512*1024,env},(error,out)=>error?reject(Object.assign(new Error('行为数据访问失败'),{statusCode:503})):resolve(out));
   child.stdin?.on('error',()=>{});child.stdin?.end(JSON.stringify(input));
  });}finally{maintenance--}
  const result=JSON.parse(data);if(result.error)throw Object.assign(new Error(result.error),{statusCode:result.status||400});return {...result,accessMode:'stopped-maintenance'};
 }
 app.get<{Params:{id:string}}>('/v1/bots/:id/behavior-data',async req=>{
  const bot=await owned(req,req.params.id);
  const query=z.object({scope:scope.optional(),subject:person.optional(),query:z.string().max(80).optional(),page:z.coerce.number().int().min(0).max(10000).default(0)}).parse(req.query);
  return access(bot,{action:'list',...query});
 });
 app.post<{Params:{id:string}}>('/v1/bots/:id/behavior-data',{config:{rateLimit:{max:30,timeWindow:'1 minute'}}},async req=>{
  const bot=await owned(req,req.params.id);if(bot.desired_state==='deleting')throw Object.assign(new Error('机器人正在删除'),{statusCode:409});
  return access(bot,mutation.parse(req.body));
 });
 async function adminBot(req:any){
  if(!req.user?.is_admin)throw Object.assign(new Error('需要管理员权限'),{statusCode:403});
  const id=z.uuid().parse(req.params.id);
  const [bot]=await db.query('SELECT * FROM bots WHERE id=$1 AND deleted_at IS NULL',[id]);
  if(!bot)throw Object.assign(new Error('机器人不存在'),{statusCode:404});
  if(bot.desired_state!=='running')throw Object.assign(new Error('请先启动机器人'),{statusCode:409});
  return bot;
 }
 app.post<{Params:{id:string}}>('/v1/admin/bots/:id/force-compress',{config:{rateLimit:{max:6,timeWindow:'1 minute'}}},async req=>{
  const bot=await adminBot(req);
  const body=z.object({requestId:z.uuid(),allowPartial:z.boolean().default(false)}).strict().parse(req.body);
  await db.query("INSERT INTO audit_events(tenant_id,event,bot_id,detail) VALUES($1,'admin.memory.force_compress',$2,$3)",[bot.tenant_id,bot.id,JSON.stringify({...body,actorId:req.user!.id})]);
  return access(bot,{action:'admin_force_compress',...body,administrator:req.user!.id},true);
 });
 app.get<{Params:{id:string}}>('/v1/admin/bots/:id/force-compress',async req=>{
  const bot=await adminBot(req),query=z.object({requestId:z.uuid()}).strict().parse(req.query);
  return access(bot,{action:'admin_force_compress_status',...query},true);
 });
 app.get<{Params:{id:string}}>('/v1/bots/:id/stickers',async req=>access(await owned(req,req.params.id),{action:'list_stickers'}));
 app.get<{Params:{id:string;image:string}}>('/v1/bots/:id/stickers/:image',async(req,reply)=>{
  const bot=await owned(req,req.params.id),id=z.string().regex(/^cap_[a-f0-9]{16}$/).parse(req.params.image);
  const result=await access(bot,{action:'get_sticker',id});return reply.header('Cache-Control','private, no-store').type('image/png').send(Buffer.from(result.png,'base64'));
 });
 app.patch<{Params:{id:string;image:string}}>('/v1/bots/:id/stickers/:image',{config:{rateLimit:{max:30,timeWindow:'1 minute'}}},async req=>{
  const bot=await owned(req,req.params.id);if(bot.desired_state==='deleting')throw Object.assign(new Error('机器人正在删除'),{statusCode:409});
  const id=z.string().regex(/^cap_[a-f0-9]{16}$/).parse(req.params.image);
  const body=z.object({expectedVersion:z.number().int().positive(),status:z.enum(['approved','rejected']),meaning:z.string().trim().max(100).default('')}).parse(req.body);
  if(body.status==='approved'&&body.meaning.length<2)throw Object.assign(new Error('审核通过前请填写至少2字的适用场景'),{statusCode:400});
  return access(bot,{action:'review_sticker',id,...body});
 });
}
