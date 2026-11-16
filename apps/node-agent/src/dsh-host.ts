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
export function dshParentMatches(bot:any,parent:any){return !!parent&&parent.bot_id===bot.id&&parent.tenant_id===bot.tenant_id&&parent.generation===bot.generation&&parent.config_revision===bot.revision&&parent.authority==='foreground'&&typeof parent.work_id==='string'&&typeof parent.origin_scope==='string'&&typeof parent.origin_event==='string'&&typeof parent.sender_id==='string'}
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
export class DshHost{
 private server?:http.Server;private search:any;private closing=false;
 private running=new Map<string,{abort:AbortController;done:Promise<void>}>();
 private snapshots=new Map<string,string>();
 private modelRoutes=new Map<string,{baseUrl:string;apiKey:string;token:string;signal:AbortSignal;assertCurrent:()=>Promise<void>}>();
 private port=Number(process.env.FISHBOT_DSH_PORT||24878);
 private stopping?:Promise<void>;
 constructor(private agent:NodeAgent,private connectSearch=mcpSource){}
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
   const value={version:1,mode:'production',botId:bot.id,tenantId:bot.tenant_id,generation:bot.generation,revision:bot.revision,url:`http://127.0.0.1:${this.port}/delegate`,token:dshToken(this.agent.key,bot)};
   const serialized=JSON.stringify(value);if(this.snapshots.get(bot.id)===serialized)continue;
   await atomicJson(path.join(ctx.root,'ai/dsh-bridge.json'),value);this.snapshots.set(bot.id,serialized);
  }
 }
 private async request(req:http.IncomingMessage,res:http.ServerResponse){
  if(req.url?.startsWith('/model/'))return this.modelRequest(req,res);
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
   const ctx=await this.agent.context(bot),bound={tenantId:bot.tenant_id,botId:bot.id,workId:parent.work_id,scope:parent.origin_scope,delegationId:body.delegationId,generation:bot.generation,revision:bot.revision};
   task=String(sessionIdentity(bound));if(this.running.has(task))throw Error('already_running');
   const done=new Promise<void>(resolve=>{release=resolve});this.running.set(task,{abort,done});
   const rpc=runtimeClient(this.agent.db),assertCurrent=async()=>{
    abort.signal.throwIfAborted();if(this.closing||this.agent.closed)throw Error('host_closing');
    await rpc({bot_id:bot.id,tenant_id:bot.tenant_id,generation:bot.generation,revision:bot.revision},'tool',{operation:'validate_parent',data:{record:parent}});
   };
   await assertCurrent();
   const repo=this.agent.bilibili.repo;
   const settings=await repo.settings(bot.tenant_id,bot.id);
   const bili=bilibiliSource({identity:bound,allowedScopes:settings.config.scopes.filter((s:string)=>s!=='media.analyze'),
    readManifest:async()=>{const live=await repo.settings(bot.tenant_id,bot.id);return{version:1,botId:bot.id,generation:bot.generation,revision:bot.revision,settingsRevision:live.revision,operations:live.config.enabled?this.agent.bilibili.catalog({config:live.config}).filter(row=>row.scope==='public.read'||parent.sender_id===ctx.config.behavior.ownerUin):[]}},
    dispatch:async(request:any,exec:any)=>{await assertCurrent();const deadline=Date.now()+90000;
     const job=await repo.admit(bot,{...request,workId:parent.work_id,stepId:body.delegationId+':'+exec.callId,authority:'foreground',senderId:parent.sender_id,origin:{scope:parent.origin_scope,eventId:parent.origin_event,objective:body.input.goal},deadlineAt:deadline});
     return awaitBilibiliReceipt(repo,bot,job.id,assertCurrent,abort.signal,deadline);
    }});
   const source=(item:any)=>item.capability==='bilibili'?bili:this.search;
   // Unique, short-lived environment credential reference prevents cross-Bot key races.
   const keyEnv='FISHBOT_DSH_KEY_'+randomUUID().replaceAll('-',''),modelToken=randomUUID();process.env[keyEnv]=modelToken;
   this.modelRoutes.set(task,{baseUrl:ctx.config.baseUrl,apiKey:ctx.apiKey,token:modelToken,signal:abort.signal,assertCurrent});
   let adapter:any,kernel:any;
   try{
    adapter=await nativeModel({baseURL:`http://127.0.0.1:${this.port}/model/${task}/v1`,model:ctx.config.model,keyEnv});
    kernel=await createKernel({storageRoot:path.join(ctx.root,'dsh-sessions'),adapter});
    const storage=path.join(ctx.root,'dsh-sessions','_no-cwd',task,'session.v4.jsonl');
    let resume=false;try{await readFile(storage);resume=true}catch(error:any){if(error.code!=='ENOENT')throw error}
    const host={identity:bound,assertCurrent,
     authorize:async(item:any)=>item.effect==='read'||item.capability==='bilibili'&&parent.sender_id===ctx.config.behavior.ownerUin,
     discover:async()=>[...await this.search.discover(),...await bili.discover()],
     validateArguments:(item:any,args:any)=>source(item).validateArguments(item,args),
     execute:async(item:any,args:any,exec:any)=>{await assertCurrent();return source(item).execute(item,args,exec)},
     // Foreground follows the original foreground Work policy (no autonomous cycle).
     // Autonomous tasks are rejected above; they must reserve in the original cycle budget.
     reserveModel:async()=>{await assertCurrent();return{settle:async(v:any)=>{console.info('dsh model',JSON.stringify({botId:bot.id,workId:parent.work_id,state:v.state,usage:v.usage}))}}},
    };
    const result=await delegateLocal(kernel,host,body.input,{signal:abort.signal,timeoutMs:120000,...(resume?{resumeSessionId:task}:{})});
    await assertCurrent();res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify(result));
   }finally{this.modelRoutes.delete(task);try{await kernel?.close()}finally{try{await adapter?.close()}finally{delete process.env[keyEnv]}}}
  }catch{if(!res.headersSent)res.writeHead(409,{'Content-Type':'application/json'});res.end(JSON.stringify({error:'dsh_delegation_failed'}))}
  finally{if(task&&release){this.running.delete(task);release()}}
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
