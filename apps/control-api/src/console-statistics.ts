import type {FastifyInstance} from 'fastify';
import {safeSubprocessEnv} from '../../../packages/shared/security.js';
import {execFile} from 'node:child_process';
import path from 'node:path';
import {z} from 'zod';
import type {Sql} from '../../../packages/database/index.js';
import {requireAdmin} from './http-guards.js';

export function registerConsoleStatistics(app:FastifyInstance,db:Sql,root:string){
 let active=0;
 app.get<{Params:{id:string}}>('/v1/admin/bots/:id/statistics',{config:{rateLimit:{max:30,timeWindow:'1 minute'}}},async(req,reply)=>{
  requireAdmin(req);
  const parsed=z.uuid().safeParse(req.params.id);
  if(!parsed.success)return reply.code(400).send({error:'机器人 ID 无效'});
  const id=parsed.data;
  const [bot]=await db.query('SELECT id FROM bots WHERE id=$1 AND deleted_at IS NULL',[id]);
  if(!bot)return reply.code(404).send({error:'机器人不存在'});
  reply.header('Cache-Control','no-store');
  if(active>=2)return reply.code(429).send({error:'统计请求较多，请稍后重试'});
  const python=path.resolve(process.env.ASTRBOT_PYTHON||`references/astrbot/.venv/${process.platform==='win32'?'Scripts/python.exe':'bin/python'}`);
  const env=safeSubprocessEnv({PYTHONIOENCODING:'utf-8'});
  active++;
  try{return await new Promise((resolve,reject)=>{
   execFile(python,[path.resolve('adapters/astrbot/console_statistics.py'),path.resolve(root),id],{windowsHide:true,timeout:6000,maxBuffer:128*1024,env},(error,out)=>{
    if(error){reject(Object.assign(new Error('统计读取失败，请稍后重试'),{statusCode:503}));return}
    try{resolve(JSON.parse(out))}catch{reject(Object.assign(new Error('统计返回异常'),{statusCode:503}))}
   });
  })}finally{active--}
 });
}
