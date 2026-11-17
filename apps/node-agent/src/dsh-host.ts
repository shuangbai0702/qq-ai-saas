/** Native DSH composition owned by the existing Agent, not another executor. */
import http from 'node:http';
import path from 'node:path';
import {createHmac,timingSafeEqual,randomUUID} from 'node:crypto';
import {readFile,rm} from 'node:fs/promises';
import {Readable} from 'node:stream';
import {pipeline} from 'node:stream/promises';
import {modelHttp} from '../../../packages/shared/model-http.js';
import type {NodeAgent} from './agent.js';
import {atomicJson} from '../../../packages/shared/fs-atomic.js';
import {runtimeClient} from '../../../packages/shared/runtime-client.js';
import {runtimeStatus} from '../../../packages/shared/runtime-status.js';
import {bilibiliSource} from '../../../packages/dsh/bilibili-source.js';
// Native ESM is copied and fingerprinted by production-build.
// @ts-ignore
import {installedDsh,createKernel,delegateLocal,sessionIdentity} from '../../../packages/dsh/kernel.mjs';
// @ts-ignore
import {nativeModel} from '../../../packages/dsh/native-model.mjs';
// @ts-ignore
import {mcpSource} from '../../../packages/dsh/mcp-source.mjs';

export function dshToken(key:Buffer,bot:any){return createHmac('sha256',key).update(JSON.stringify(['dsh',bot.tenant_id,bot.id,bot.generation,bot.revision])).digest('hex')}
export function dshParentMatches(bot:any,parent:any){return !!parent&&parent.bot_id===bot.id&&parent.tenant_id===bot.tenant_id&&parent.generation===bot.generation&&parent.config_revision===bot.revision&&(parent.authority==='foreground'||parent.authority==='autonomous'&&Number.isSafeInteger(parent.mode_epoch)&&parent.mode_epoch>0&&parent.sender_id==='service:'+bot.id)&&typeof parent.work_id==='string'&&typeof parent.origin_scope==='string'&&typeof parent.origin_event==='string'&&typeof parent.sender_id==='string'}
export async function awaitBilibiliReceipt(repo:any,bot:any,id:string,assertCurrent:()=>Promise<void>,signal:AbortSignal,deadline:number){
 // The original BilibiliExecutor owns claim/dispatch. Never compete for its lease.
 for(;;){
  await assertCurrent();const job=await repo.get(bot.tenant_id,bot.id,id);
  if(job.result)return job.result;
  if(!['queued','claimed','dispatching'].includes(job.state))throw Error('bilibili_receipt_unavailable');
  if(Date.now()>=deadline)throw Error('bilibili_receipt_pending_do_not_repeat');
  await new Promise<void>((resolve,reject)=>{const abort=()=>{clearTimeout(timer);reject(signal.reason)};const timer=setTimeout(()=>{signal.removeEventListener('abort',abort);resolve()},250);signal.addEventListener('abort',abort,{once:true});if(signal.aborted)abort()});
 }
}
/** A lost pending response/ACK must reuse the original media responsibility,
 * not interpret the native session's last aborted turn as a final failure. */
export async function pendingDshMediaContinuation(repo:any,bot:any,parent:any,sessionId:string,delegationId:string,completedId:string){
 const autonomous=parent.authority==='autonomous',pending=await repo.pending(bot,autonomous?undefined:parent.work_id);
 for(const item of pending){
  if(item.id===completedId||item.operation!=='analyze'||item.authority!==parent.authority)continue;
  const job=await repo.get(bot.tenant_id,bot.id,item.id),source=job?.continuation;
  if(!job||job.id!==item.id||job.bot_id!==bot.id||job.tenant_id!==bot.tenant_id||job.operation!=='analyze'||job.authority!==parent.authority||job.cancel_requested||job.generation!==bot.generation||job.config_revision!==bot.revision||
     (autonomous?job.mode_epoch!==parent.mode_epoch:['work_id','sender_id','origin_scope','origin_event'].some(key=>job[key]!==parent[key]))||
     ['settings_revision','account_revision','account_uid'].some(key=>job[key]!==parent[key])||
     (!autonomous&&(!source?.sourceActionId||!source.sourceOwner))||source?.dsh?.sessionId!==sessionId||source.dsh.delegationId!==delegationId)continue;
  if(['queued','claimed','dispatching'].includes(job.state)||['pending','claimed'].includes(job.completion_state))return true;
 }
 return false;
}
/** Borrow the current autonomous RunControl; it owns activity counters and
 * the existing cycle request reservation. Foreground has no autonomous cap. */
export async function reserveDshModel(options:any,callback:(operation:string,data:any,receipt?:boolean)=>Promise<any>){
 const requestId='dsh:'+randomUUID(),inputTokens=Math.ceil(Buffer.byteLength(JSON.stringify({...options,signal:undefined,sessionId:undefined}))/2),outputTokens=8192;
 const reserved=await callback('model_budget',{phase:'reserve',requestId,inputTokens,outputTokens});
 try{
  if(!Number.isSafeInteger(reserved?.maxTokens)||reserved.maxTokens<1||reserved.maxTokens>outputTokens)throw Error('invalid_model_budget_cap');
 }catch(error){await callback('model_budget',{phase:'released',requestId,tokens:0},true);throw error;}
 return{maxTokens:reserved.maxTokens,settle:async(value:any)=>{
  const usage=value.usage,total=usage?.totalTokens??usage?.total_tokens??(Number.isSafeInteger(usage?.inputTokens)&&Number.isSafeInteger(usage?.outputTokens)?usage.inputTokens+usage.outputTokens:undefined);
  const actual=Number.isSafeInteger(total)&&total>=0;
  const phase=value.state==='not_dispatched'?'released':value.state==='completed'&&actual?'settled':'unknown';
  await callback('model_budget',{phase,requestId,...(phase==='released'?{tokens:0}:phase==='settled'?{tokens:total}:{})},true);
 }};
}
export class DshHost{
 private server?:http.Server;private search:any;private closing=false;
 private running=new Map<string,{abort:AbortController;done:Promise<void>}>();
 private snapshots=new Map<string,string>();
 private modelRoutes=new Map<string,{baseUrl:string;apiKey:string;token:string;signal:AbortSignal;assertCurrent:()=>Promise<void>}>();
 // Per-connection RPC waiters only; original tools/Work remain the authority.
 private callbacks=new Map<string,{task:string;token:string;resolve:(value:any)=>void;reject:(error:Error)=>void}>();
 private port=Number(process.env.FISHBOT_DSH_PORT||24878);
 private stopping?:Promise<void>;
 constructor(private agent:NodeAgent,private connectSearch=mcpSource){}
 /** Cold boot only, after the canonical Agent lease is held. A failed MCP
  * connection must not leave last process's bridge hiding working native tools. */
 async clearPreviousBridges(){
  for(const bot of await this.agent.db.query("SELECT id,tenant_id FROM bots WHERE deleted_at IS NULL")){
   const file=path.join(this.agent.root,'bots',bot.id,'ai/dsh-bridge.json');
   let value:any;try{value=JSON.parse(await readFile(file,'utf8'))}catch(error:any){if(error.code==='ENOENT')continue;throw error}
   if(value.mode==='production'&&value.botId===bot.id&&value.tenantId===bot.tenant_id)await rm(file);
  }
 }
 async start(){
  if(this.server)return;
  await installedDsh();
  this.search=await this.connectSearch({serverName:'exa',url:'https://mcp.exa.ai/mcp',allowedReadTools:['mcp__exa__web_search_exa','mcp__exa__web_fetch_exa']});
  try{this.server=http.createServer((req,res)=>{void this.request(req,res).catch(()=>{if(!res.headersSent)res.writeHead(500);res.end('{}')})});
   await new Promise<void>((resolve,reject)=>{this.server!.once('error',reject);this.server!.listen(this.port,'127.0.0.1',()=>{this.server!.removeListener('error',reject);resolve()})});
   this.port=(this.server.address() as import('node:net').AddressInfo).port;
   await this.publish();
  }catch(error){await this.stop();throw error}
 }
 async publish(){
  if(this.closing||!this.server)return;
  for(const bot of await this.agent.db.query("SELECT * FROM bots WHERE deleted_at IS NULL AND desired_state='running'")){
   const ctx=await this.agent.context(bot);
   // Reuse each Bot's existing credential/provider. Unsupported providers retain their original tools.
   if(!ctx.apiKey)continue;
   const value={version:2,mode:'production',capabilities:['group_history','execution_review','favorite_audit'],botId:bot.id,tenantId:bot.tenant_id,generation:bot.generation,revision:bot.revision,url:`http://127.0.0.1:${this.port}/delegate`,token:dshToken(this.agent.key,bot)};
   const serialized=JSON.stringify(value);if(this.snapshots.get(bot.id)===serialized)continue;
   await atomicJson(path.join(ctx.root,'ai/dsh-bridge.json'),value);this.snapshots.set(bot.id,serialized);
  }
 }
 private async request(req:http.IncomingMessage,res:http.ServerResponse){
  if(req.url?.startsWith('/model/'))return this.modelRequest(req,res);
  if(req.url==='/tool-result')return this.toolResult(req,res);
  if(this.closing||req.method!=='POST'||req.url!=='/delegate'||req.headers.origin){res.writeHead(403);res.end('{}');return}
  let task:string|undefined;let release!:()=>void;const abort=new AbortController();
  req.once('aborted',()=>abort.abort());res.once('close',()=>{if(!res.writableEnded)abort.abort()});
  try{
   let raw='';for await(const chunk of req){raw+=chunk;if(Buffer.byteLength(raw)>65536)throw Error('request_too_large')}
   const body=JSON.parse(raw),parent=body.parent;
   if(!parent||typeof parent.bot_id!=='string')throw Error('trusted_parent_required');
   const [bot]=await this.agent.db.query('SELECT * FROM bots WHERE id=$1 AND deleted_at IS NULL',[parent.bot_id]);
   if(!bot||!dshParentMatches(bot,parent)||!runtimeStatus(bot).online||!this.snapshots.has(bot.id))throw Error('stale_parent');
   const expected=Buffer.from('Bearer '+dshToken(this.agent.key,bot)),provided=Buffer.from(req.headers.authorization||'');
   if(expected.length!==provided.length||!timingSafeEqual(expected,provided))throw Error('unauthorized');
   if(typeof body.delegationId!=='string'||!body.delegationId||body.delegationId.length>256)throw Error('call_identity_required');
   const ctx=await this.agent.context(bot),bound:any={tenantId:bot.tenant_id,botId:bot.id,workId:parent.work_id,scope:parent.origin_scope,delegationId:body.delegationId,generation:bot.generation,revision:bot.revision};
   task=String(sessionIdentity(bound));if(this.running.has(task))throw Error('already_running');
   let completedMedia:any;
   if(body.completion){
    completedMedia=await this.agent.bilibili.repo.get(bot.tenant_id,bot.id,body.completion.id);
    if(!completedMedia||completedMedia.bot_id!==bot.id||completedMedia.tenant_id!==bot.tenant_id||completedMedia.generation!==bot.generation||completedMedia.config_revision!==bot.revision||completedMedia.operation!=='analyze'||completedMedia.authority!==parent.authority||completedMedia.continuation?.dsh?.delegationId!==body.delegationId||completedMedia.completion_state!=='claimed'||completedMedia.completion_owner!==body.completion.owner||+new Date(completedMedia.completion_until)<=Date.now()||!completedMedia.result)throw Error('invalid_media_completion');
    if(parent.authority==='autonomous'){
     // The original scheduler already admitted this receipt to the CURRENT
     // cycle. A persisted Session locates computation; it grants no old rights.
     const [cycle]=await this.agent.db.query("SELECT decision FROM autonomy_cycles WHERE id=$1 AND bot_id=$2 AND tenant_id=$3 AND epoch=$4 AND finished_at IS NULL AND state='acting'",[parent.work_id,bot.id,bot.tenant_id,parent.mode_epoch]);
     const source=cycle?.decision?.continuation;
     if(completedMedia.mode_epoch!==parent.mode_epoch||source?.id!==completedMedia.id||source.owner!==body.completion.owner||source.parentWorkId!==completedMedia.work_id||!/^fishbot-[a-f0-9]{64}$/.test(completedMedia.continuation.dsh.sessionId))throw Error('autonomous_media_not_admitted');
     task=completedMedia.continuation.dsh.sessionId;bound.sessionId=task;
     if(this.running.has(task!))throw Error('already_running');
     Object.assign(parent,{settings_revision:completedMedia.settings_revision,account_revision:completedMedia.account_revision,account_uid:completedMedia.account_uid});
    }else{
     if(completedMedia.work_id!==parent.work_id||completedMedia.origin_scope!==parent.origin_scope||completedMedia.origin_event!==parent.origin_event||completedMedia.sender_id!==parent.sender_id||completedMedia.continuation.dsh.sessionId!==task)throw Error('invalid_media_completion');
     Object.assign(parent,{settings_revision:completedMedia.settings_revision,account_revision:completedMedia.account_revision,account_uid:completedMedia.account_uid,continuation:{sourceActionId:completedMedia.id,sourceOwner:body.completion.owner}});
    }
   }else if(parent.continuation)throw Error('unexpected_continuation');
   const done=new Promise<void>(resolve=>{release=resolve});this.running.set(task!,{abort,done});
   const rpc=runtimeClient(this.agent.db),assertCurrent=async()=>{
    abort.signal.throwIfAborted();if(this.closing||this.agent.closed)throw Error('host_closing');
    await rpc({bot_id:bot.id,tenant_id:bot.tenant_id,generation:bot.generation,revision:bot.revision},'tool',{operation:'validate_parent',data:{record:parent}});
   };
   await assertCurrent();
   if(body.protocol!==2)throw Error('delegation_protocol_required');
   res.writeHead(200,{'Content-Type':'application/x-ndjson','Cache-Control':'no-store'});
   const callback=async(operation:string,data:any={},receipt=false)=>{
    if(!receipt)await assertCurrent();const id=randomUUID();
    return new Promise<any>((resolve,reject)=>{
     const cancel=()=>finish(null,Error('delegation_callback_canceled'));
     const timer=setTimeout(cancel,30000);
     const finish=(value:any,error?:Error)=>{clearTimeout(timer);abort.signal.removeEventListener('abort',cancel);this.callbacks.delete(id);error?reject(error):resolve(value)};
     this.callbacks.set(id,{task:task!,token:dshToken(this.agent.key,bot),resolve:v=>finish(v),reject:e=>finish(null,e)});
     abort.signal.addEventListener('abort',cancel,{once:true});
     res.write(JSON.stringify({type:'tool',id,operation,...data})+'\n');
    });
   };
   const local={
    discover:async()=>{if(completedMedia)return [];const rows=await callback('discover');if(!Array.isArray(rows))throw Error('invalid_local_catalog');return rows.filter((row:any)=>['group_history','execution_review','favorite_audit'].includes(row.capability)&&row.effect==='read')},
    validateArguments:async(item:any,args:any)=>{const {validateJsonSchemaValue}=await kernel.native.load('dsh-tools');if(validateJsonSchemaValue(item.definition.parameters,args).length)throw Error('tool_arguments_invalid')},
    execute:async(item:any,args:any)=>callback('execute',{tool:item.id,args}),
   };
   const repo=this.agent.bilibili.repo;
   const settings=await repo.settings(bot.tenant_id,bot.id);
   if(completedMedia&&settings.revision!==completedMedia.settings_revision)throw Error('media_settings_changed');
   const bili=bilibiliSource({identity:bound,allowedScopes:settings.config.scopes,
    readManifest:async()=>{const live=await repo.settings(bot.tenant_id,bot.id);return{version:1,botId:bot.id,generation:bot.generation,revision:bot.revision,settingsRevision:live.revision,operations:live.config.enabled&&(parent.authority!=='autonomous'||live.config.allowAutonomous)?this.agent.bilibili.catalog({config:live.config}).filter(row=>parent.authority==='autonomous'||row.scope==='public.read'||parent.sender_id===ctx.config.behavior.ownerUin):[]}},
    dispatch:async(request:any,exec:any)=>{await assertCurrent();const media=request.operation==='analyze',deadline=Date.now()+(media?600000:90000);
     const job=await repo.admit(bot,{...request,workId:parent.work_id,stepId:body.delegationId+':'+exec.callId,authority:parent.authority,...(parent.authority==='autonomous'?{epoch:parent.mode_epoch}:{senderId:parent.sender_id}),origin:{scope:parent.origin_scope,eventId:parent.origin_event,objective:(body.input?.goal||completedMedia?.continuation?.objective||'').slice(0,1000)},deadlineAt:deadline,...(completedMedia&&parent.authority==='foreground'?{continuation:{actionId:completedMedia.id,owner:body.completion.owner}}:{})},media?{sessionId:task!,delegationId:body.delegationId}:undefined);
     if(media)return {kind:'fishbot_dsh_wait',actionId:job.id};
     return awaitBilibiliReceipt(repo,bot,job.id,assertCurrent,abort.signal,deadline);
    }});
   const source=(item:any)=>item.capability==='bilibili'?bili:item.capability==='web_search'?this.search:local;
   // Unique, short-lived environment credential reference prevents cross-Bot key races.
   const keyEnv='FISHBOT_DSH_KEY_'+randomUUID().replaceAll('-',''),modelToken=randomUUID();process.env[keyEnv]=modelToken;
   this.modelRoutes.set(task!,{baseUrl:ctx.config.baseUrl,apiKey:ctx.apiKey,token:modelToken,signal:abort.signal,assertCurrent});
   let adapter:any,kernel:any;
   try{
    adapter=await nativeModel({baseURL:`http://127.0.0.1:${this.port}/model/${task}/v1`,model:ctx.config.model,keyEnv});
    kernel=await createKernel({storageRoot:path.join(ctx.root,'dsh-sessions'),adapter});
    const storage=path.join(ctx.root,'dsh-sessions','_no-cwd',task!,'session.v4.jsonl');
    let resume=false;try{await readFile(storage);resume=true}catch(error:any){if(error.code!=='ENOENT')throw error}
    const host={identity:bound,assertCurrent,
     authorize:async(item:any)=>item.effect==='read'||item.capability==='bilibili'&&(parent.authority==='autonomous'||parent.sender_id===ctx.config.behavior.ownerUin),
     discover:async()=>[...await this.search.discover(),...await bili.discover(),...await local.discover()],
     validateArguments:(item:any,args:any)=>source(item).validateArguments(item,args),
     execute:async(item:any,args:any,exec:any)=>{await assertCurrent();if(parent.authority==='autonomous'){await callback('tool_budget');await assertCurrent();}return source(item).execute(item,args,exec)},
     // Foreground follows the original foreground Work policy (no autonomous cycle).
     // Autonomous calls borrow the current RunControl and original cycle budget.
     reserveModel:async(options:any)=>{await assertCurrent();if(parent.authority==='autonomous')return reserveDshModel(options,callback);return{settle:async(v:any)=>{console.info('dsh model',JSON.stringify({botId:bot.id,workId:parent.work_id,state:v.state,usage:v.usage}))}}},
    };
    if(completedMedia&&!resume)throw Error('original_session_missing');
    if(completedMedia&&await pendingDshMediaContinuation(repo,bot,parent,task!,body.delegationId,completedMedia.id)){await assertCurrent();res.end(JSON.stringify({type:'pending'})+'\n');return;}
    const result=await delegateLocal(kernel,host,body.input,{signal:abort.signal,timeoutMs:completedMedia?90000:120000,...(resume?{resumeSessionId:task}:{}),...(completedMedia?{completion:{id:completedMedia.id,text:JSON.stringify(completedMedia.result)}}:{})});
    await assertCurrent();res.end(JSON.stringify(result?.kind==='fishbot_dsh_wait'?{type:'pending'}:{type:'final',text:result})+'\n');
   }finally{this.modelRoutes.delete(task!);try{await kernel?.close()}finally{try{await adapter?.close()}finally{delete process.env[keyEnv]}}}
  }catch{if(!res.headersSent){res.writeHead(409,{'Content-Type':'application/json'});res.end(JSON.stringify({error:'dsh_delegation_failed'}))}else res.end(JSON.stringify({type:'error',error:'dsh_delegation_failed'})+'\n')}
  finally{if(task&&release){for(const entry of this.callbacks.values())if(entry.task===task)entry.reject(Error('delegation_closed'));this.running.delete(task);release()}}
 }
 private async toolResult(req:http.IncomingMessage,res:http.ServerResponse){
  if(this.closing||req.method!=='POST'||req.headers.origin){res.writeHead(403);res.end('{}');return}
  let raw='';for await(const chunk of req){raw+=chunk;if(Buffer.byteLength(raw)>1024*1024)throw Error('callback_too_large')}
  const value=JSON.parse(raw),pending=this.callbacks.get(value.id);
  if(!pending||req.headers.authorization!=='Bearer '+pending.token){res.writeHead(403);res.end('{}');return}
  this.callbacks.delete(value.id);value.ok===true?pending.resolve(value.value):pending.reject(Error('original_tool_rejected'));
  res.end('{}');
 }
 private async modelRequest(req:http.IncomingMessage,res:http.ServerResponse){
  const match=/^\/model\/(fishbot-[a-f0-9]{64})\/v1\/chat\/completions$/.exec(req.url||'');
  const route=match&&this.modelRoutes.get(match[1]);
  if(!route||this.closing||req.method!=='POST'||req.headers.origin||req.headers.authorization!=='Bearer '+route.token){res.writeHead(403);res.end('{}');return}
  await route.assertCurrent();let body='';for await(const chunk of req){body+=chunk;if(Buffer.byteLength(body)>8*1024*1024)throw Error('model_request_too_large')}
  // Preserve the existing DNS pinning, redirect rejection and private-origin policy.
  const canceled=new AbortController();req.once('aborted',()=>canceled.abort());res.once('close',()=>{if(!res.writableEnded)canceled.abort()});
  const response=await modelHttp(route.baseUrl.replace(/\/+$/,'')+'/chat/completions',{method:'POST',headers:{Authorization:'Bearer '+route.apiKey,'Content-Type':'application/json'},body,signal:AbortSignal.any([route.signal,canceled.signal])});
  res.writeHead(response.status,{'Content-Type':response.headers.get('Content-Type')||'application/json'});
  if(response.body)await pipeline(Readable.fromWeb(response.body as any),res);else res.end();
 }
 stop(){return this.stopping??=this.stopOnce()}
 private async stopOnce(){
  this.closing=true;for(const v of this.running.values())v.abort.abort();
  const server=this.server;this.server=undefined;const closed=server?new Promise<void>(resolve=>server.close(()=>resolve())):Promise.resolve();
  await Promise.allSettled([...this.running.values()].map(v=>v.done));await closed;await this.search?.close();this.search=undefined;
  for(const [id,value] of this.snapshots){const file=path.join(this.agent.root,'bots',id,'ai/dsh-bridge.json');try{if(await readFile(file,'utf8')===value)await rm(file)}catch(error:any){if(error.code!=='ENOENT')throw error}}
  this.snapshots.clear();
 }
}
