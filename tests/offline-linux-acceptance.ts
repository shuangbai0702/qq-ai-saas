// Explicit Linux acceptance entry; isolated PGlite root, real AstrBot SDK and
// mock QQ transport. Never reads/writes the production PostgreSQL database.
import assert from 'node:assert/strict';import path from 'node:path';import os from 'node:os';
import {mkdtemp,mkdir,writeFile,readFile} from 'node:fs/promises';import http from 'node:http';
import {createApp} from '../apps/control-api/src/app.js';import {issueInvitation} from '../packages/shared/invitations.js';
import {defaultConfig} from '../packages/contracts/index.js';import {MockProtocolAdapter} from '../adapters/mock/index.js';
delete process.env.DATABASE_URL;delete process.env.PGHOST;process.env.DB_AUTO_MIGRATE='true';
const sleep=(ms:number)=>new Promise(r=>setTimeout(r,ms));
async function wait(fn:()=>Promise<boolean>,what:string){for(let i=0;i<480;i++){if(await fn())return;await sleep(500)}throw new Error(what)}
const model=http.createServer(async(req,res)=>{for await(const _ of req){}res.setHeader('content-type','application/json');res.end(JSON.stringify({id:'offline-acceptance',choices:[{index:0,message:{role:'assistant',content:'恢复验收成功。'},finish_reason:'stop'}]}))});
await new Promise<void>(r=>model.listen(0,'127.0.0.1',r));const origin=`http://127.0.0.1:${(model.address()as any).port}`;process.env.FISHBOT_MODEL_ALLOWED_PRIVATE_ORIGINS=origin;
const root=await mkdtemp(path.join(os.tmpdir(),'fish-offline-linux-'));let current=await createApp({root,databaseUrl:''});
const evidence:any={root,scope:'Linux real AstrBot processes and OneBot WebSocket; fixture model and mock QQ'};
try{
 const deadlines=process.argv.includes('--deadlines');if(!deadlines)current.agent.offlineGrace={offline:30000,fault:60000,login:30000};
 const invite=await issueInvitation(current.db,null,{botLimit:3});const auth=await current.app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:invite.code,email:'offline-linux@test.invalid',password:'offline-linux-123',name:'Linux acceptance'}});assert.equal(auth.statusCode,200,auth.body);const cookies={fish_session:auth.cookies[0].value};
 const call=(method:any,url:string,payload?:any)=>current.app.inject({method,url,payload,cookies});const ids:string[]=[];
 for(let i=0;i<3;i++){
  const created=await call('POST','/v1/bots',{name:'Offline acceptance '+i,protocol:'mock',runtimeMode:i===0?'dedicated':'shared',expectedUin:'9000800'+i,apiKey:'synthetic-offline-key-'+i,config:{...defaultConfig,baseUrl:origin+'/v1',model:'acceptance',systemPrompt:'简短回答。'}});assert.equal(created.statusCode,201,created.body);ids.push(created.json().id);
  await call('POST',`/v1/bots/${ids[i]}/actions`,{action:'start',idempotencyKey:'start'});
 }
 const bots=()=>current.db.query('SELECT * FROM bots ORDER BY name');
 await wait(async()=>(await bots()).every(b=>b.health.ai?.ready&&b.health.protocol?.ready),'initial runtimes ready');
 const context=async(id:string)=>current.agent.context((await current.db.query('SELECT * FROM bots WHERE id=$1',[id]))[0]);
 const protocol=()=>current.agent.protocols.mock as MockProtocolAdapter;
 const benchmark=async(id:string)=>{const ctx=await context(id),samples:number[]=[];
  await wait(async()=>!!(await protocol().call(ctx,'/state')as any).connected,'benchmark OneBot connected');
  for(let i=0;i<20;i++){const count=(await protocol().call(ctx,'/messages')as any[]).filter(x=>x.action==='send_private_msg').length,start=Date.now();await protocol().call(ctx,'/message',{text:'回复验收 '+i,sender:String(12345000+i)});
   await wait(async()=>(await protocol().call(ctx,'/messages')as any[]).filter(x=>x.action==='send_private_msg').length>count,'benchmark reply');samples.push(Date.now()-start)}
  samples.sort((a,b)=>a-b);return{delivered:samples.length,p95Ms:samples[Math.ceil(samples.length*.95)-1],maxMs:samples.at(-1)};
 };
 const memory=async()=>{const result:any={};for(const id of [...ids,group.id])for(const kind of ['ai','protocol']){const p=await current.agent.host.inspect(id,kind);if(p)try{result[id+':'+kind]={pid:p.pid,smaps:await readFile(`/proc/${p.pid}/smaps_rollup`,'utf8')}}catch{}}result.host=await readFile('/proc/meminfo','utf8');result.psi=await readFile('/proc/pressure/memory','utf8');return result};
 for(const id of ids){const ctx=await context(id);await protocol().call(ctx,'/transition',{state:'online',uin:ctx.expectedUin})}
 await wait(async()=>(await bots()).every(b=>b.health.protocol?.online),'initial online');
 const [group]=await current.db.query('SELECT * FROM runtime_groups');const groupPid=(await current.agent.host.inspect(group.id,'ai')).pid;
 evidence.before=await memory();if(!deadlines)evidence.baselineReplies=await benchmark(ids[0]);
 const configs=await current.db.query('SELECT * FROM bot_config_revisions ORDER BY bot_id,revision');const keys=await current.db.query('SELECT bot_id,ciphertext FROM secrets ORDER BY bot_id');
 for(const id of ids.slice(0,2)){const dir=path.join(root,'bots',id,'qq');await mkdir(dir,{recursive:true});await writeFile(path.join(dir,'identity.json'),'retained:'+id)}
 // Brief disconnection must preserve all handles.
 await protocol().call(await context(ids[0]),'/transition',{state:'failed'});await sleep(7000);await protocol().call(await context(ids[0]),'/transition',{state:'online',uin:'90008000'});
 await wait(async()=>(await current.db.query('SELECT health FROM bots WHERE id=$1',[ids[0]]))[0].health.protocol?.online,'brief disconnect recovery');assert.ok(await current.agent.host.inspect(ids[0],'ai'));
 evidence.shortDisconnectPreserved=true;
 const phaseAt=Date.now();
 if(deadlines){let freezeNext=true;const start=current.agent.ai.start.bind(current.agent.ai);current.agent.ai.start=async ctx=>{await start(ctx);if(ctx.botId===ids[0]&&freezeNext){freezeNext=false;const p=await current.agent.host.inspect(ctx.botId,'ai');process.kill(p.pid,'SIGSTOP')}};await call('POST',`/v1/bots/${ids[0]}/actions`,{action:'restart',idempotencyKey:'startup-deadline'});await protocol().call(await context(ids[1]),'/transition',{state:'waiting_scan'});}
 else for(const id of ids.slice(0,2))await protocol().call(await context(id),'/transition',{state:'failed'});
 if(deadlines){await wait(async()=>(await current.db.query('SELECT observed_state FROM bots WHERE id=$1',[ids[0]]))[0].observed_state==='offline','120s unready AI eviction');evidence.startupEvictedMs=Date.now()-phaseAt;assert.ok(evidence.startupEvictedMs>=120000&&evidence.startupEvictedMs<145000);}
 await wait(async()=>(await bots()).filter(b=>ids.slice(0,2).includes(b.id)).every(b=>b.observed_state==='offline'),'eviction after continuous grace');
 if(deadlines){evidence.waitingEvictedMs=Date.now()-phaseAt;assert.ok(evidence.waitingEvictedMs>=180000&&evidence.waitingEvictedMs<205000);}
 for(const id of ids.slice(0,2)){assert.equal(await current.agent.host.inspect(id,'protocol'),null);assert.equal(await current.agent.host.inspect(id,'ai'),null);assert.equal(await readFile(path.join(root,'bots',id,'qq/identity.json'),'utf8'),'retained:'+id)}
 assert.equal((await current.agent.host.inspect(group.id,'ai')).pid,groupPid);assert.equal((await current.db.query('SELECT health FROM bots WHERE id=$1',[ids[2]]))[0].health.protocol.online,true);
 evidence.detachedWithoutStoppingPeer=true;evidence.evicted=ids.slice(0,2);
 evidence.afterEviction=await memory();
 await current.app.close();current=await createApp({root,databaseUrl:''});current.agent.offlineGrace={offline:30000,fault:60000,login:30000};
 await sleep(6000);for(const id of ids.slice(0,2)){assert.equal((await current.db.query('SELECT observed_state FROM bots WHERE id=$1',[id]))[0].observed_state,'offline');assert.equal(await current.agent.host.inspect(id,'protocol'),null)}
 evidence.restartRetainedEviction=true;
 for(const id of ids.slice(0,2)){const login=await call('POST',`/v1/bots/${id}/login-sessions`,{});assert.equal(login.statusCode,200,login.body);await wait(async()=>!!(await current.agent.host.inspect(id,'protocol')),'protocol recreated');const ctx=await context(id);await wait(async()=>(await protocol().getHealth(ctx)).ready,'management ready');await protocol().call(ctx,'/transition',{state:'online',uin:ctx.expectedUin})}
 await wait(async()=>(await bots()).filter(b=>ids.slice(0,2).includes(b.id)).every(b=>b.health.ai?.ready&&b.health.protocol?.online),'AI and protocols restored');
 for(const id of ids.slice(0,2)){const ctx=await context(id);await wait(async()=>!!(await protocol().call(ctx,'/state')as any).connected,'restored OneBot connected');await protocol().call(ctx,'/message',{text:'恢复验证',sender:'12345678'});await wait(async()=>(await protocol().call(ctx,'/messages')as any[]).some(x=>x.action==='send_private_msg'),'real SDK reply after restore')}
 assert.deepEqual(await current.db.query('SELECT * FROM bot_config_revisions ORDER BY bot_id,revision'),configs);assert.deepEqual(await current.db.query('SELECT bot_id,ciphertext FROM secrets ORDER BY bot_id'),keys);
 evidence.loginRestored=true;evidence.actualSdkReplies=true;evidence.configAndCiphertextUnchanged=true;
 if(!deadlines){evidence.restoredReplies=await benchmark(ids[0]);assert.ok(evidence.restoredReplies.p95Ms-evidence.baselineReplies.p95Ms<=5000,'added P95 <=5 seconds in controlled model benchmark');}evidence.passed=true;
}finally{await current.app.close();await new Promise<void>(r=>model.close(()=>r()));await writeFile(path.join(root,'acceptance.json'),JSON.stringify(evidence,null,2));console.log(JSON.stringify(evidence))}
