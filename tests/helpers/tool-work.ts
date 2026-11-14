import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import path from 'node:path';
import http from 'node:http';
import {mkdir} from 'node:fs/promises';
import type {Sql} from '../../packages/database/index.js';
import type {RuntimeCall} from '../../packages/shared/runtime-client.js';
import {BilibiliTools} from '../../packages/database/bilibili-tools.js';

/** Actual SQLite Pipeline across JSON-line transport; no PG tool-history fake. */
export async function openToolWork(root:string,db:Sql,bot:any,workId:string,epoch:number){
 await mkdir(root,{recursive:true});
 const python=spawn(process.env.PYTHON||'python',['-u','tests/pipeline_tool_rpc_fixture.py',path.join(root,'pipeline.db')],{stdio:['pipe','pipe','inherit']});
 let sequence=0;const pending=new Map<number,{resolve:(r:any)=>void;reject:(e:Error)=>void}>();
 createInterface({input:python.stdout}).on('line',line=>{const r=JSON.parse(line),p=pending.get(r.id);pending.delete(r.id);if(r.error)p?.reject(Error(r.error));else p?.resolve(r.result)});
 const exit=new Promise<void>(resolve=>python.once('exit',()=>{for(const p of pending.values())p.reject(Error('pipeline fixture exited'));pending.clear();resolve()}));
 python.on('error',error=>{for(const p of pending.values())p.reject(error);pending.clear()});
 const call=(operation:string,data:any={},extra:any={})=>new Promise<any>((resolve,reject)=>{const id=++sequence;pending.set(id,{resolve,reject});python.stdin.write(JSON.stringify({id,operation,data,...extra})+'\n')});
 const transport:RuntimeCall=async(identity,operation,body:any)=>{if(identity.bot_id!==bot.id||identity.tenant_id!==bot.tenant_id||operation!=='tool')throw Error('fixture_identity');return call(body.operation,body.data)};
 try{await call('initialize',{}, {settings:{botId:bot.id,tenantId:bot.tenant_id,generation:bot.generation,revision:bot.revision},workId,epoch})}
 catch(error){python.stdin.end();await exit;throw error}
 const sql=(sql:string,params:any[]=[])=>call('fixture_sql',{sql,params});
 const repo=new BilibiliTools(db,transport);
 const seed=async(id:string,work:string,options:any={})=>{
  const request={bot_id:bot.id,tenant_id:bot.tenant_id,generation:bot.generation,config_revision:bot.revision,work_id:work,step_id:'analysis',operation:'analyze',args:{},args_digest:'hash',authority:'autonomous',mode_epoch:epoch,settings_revision:1,continuation:{},cancel_requested:false,...options.request};
  await sql("INSERT INTO pipeline_events(scope,subject,event,at,source,stage,status,reason,work_id,fingerprint,authority_epoch,expires_at,execution_started,request,result,lease_owner,lease_until,updated_at) VALUES(?,?,?,?,'tool','model_tools',?,'',?,?,?,?,1,?,?,?,?,?)",[options.scope??'self',request.sender_id??'service',id,Date.now()/1000,options.state??'waiting',work,request.step_id,epoch,Date.now()/1000+300,JSON.stringify(request),options.result?JSON.stringify(options.result):null,options.owner??'worker',Date.now()/1000+210,Date.now()/1000]);
  return repo.get(bot.tenant_id,bot.id,id);
 };
 return{repo,transport,call,sql,seed,close:async()=>{python.stdin.end();await exit}};
}

/** Transport-only IPC fixture; all tool state still runs in the real Pipeline. */
export async function attachToolIpc(db:Sql,bot:any,native:Awaited<ReturnType<typeof openToolWork>>){
 const token='isolated-tool-work-ipc';
 const server=http.createServer(async(req,res)=>{
  if(req.url!=='/autonomous/tool'||req.headers.authorization!=='Bearer '+token||req.headers['x-member-generation']!==String(bot.generation)||req.headers['x-member-revision']!==String(bot.revision)){res.writeHead(403);res.end('{}');return}
  try{let raw='';for await(const chunk of req)raw+=chunk;const body=JSON.parse(raw);res.setHeader('Content-Type','application/json');res.end(JSON.stringify(await native.call(body.operation,body.data)))}catch{res.writeHead(409);res.end('{}')}
 });
 await new Promise<void>(resolve=>server.listen(0,'127.0.0.1',resolve));
 const port=(server.address() as any).port;
 try{await db.query("UPDATE bots SET runtime_mode='dedicated',group_id=NULL WHERE id=$1",[bot.id]);await db.query("INSERT INTO instances(bot_id,generation,ai_port,protocol_port,token) VALUES($1,$2,$3,$4,$5) ON CONFLICT(bot_id) DO UPDATE SET ai_port=excluded.ai_port,protocol_port=excluded.protocol_port,token=excluded.token",[bot.id,bot.generation,port-1,port,token])}
 catch(error){await new Promise<void>(resolve=>server.close(()=>resolve()));throw error}
 return async()=>{await new Promise<void>(resolve=>server.close(()=>resolve()))};
}
