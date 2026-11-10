import test from 'node:test';import assert from 'node:assert/strict';
import http from 'node:http';import path from 'node:path';
import {mkdtemp,rm,readFile,writeFile} from 'node:fs/promises';
import {createApp} from '../apps/control-api/src/app.js';
import {issueTestInvite} from './helpers/invitations.js';
import {permitModelFixture} from './helpers/model-fixture.js';
import {defaultConfig} from '../packages/contracts/index.js';
import {MockProtocolAdapter} from '../adapters/mock/index.js';
import {BiliWeb} from '../apps/node-agent/src/bilibili/web-adapter.js';

const sleep=(ms:number)=>new Promise(r=>setTimeout(r,ms));
async function wait(fn:()=>Promise<boolean>,label:string,ms=90000){const until=Date.now()+ms;while(Date.now()<until){if(await fn())return;await sleep(300)}throw new Error('Timeout '+label)}
test('real AstrBot ToolLoop invokes Bilibili through trusted Work, API and Node Agent, then QQ OutputPlan',{timeout:160000},async t=>{
 const root=await mkdtemp(path.resolve('.local/bilibili-native-test-'));let modelCalls=0;const modelRequests:any[]=[];let recalling=false;
 const provider=http.createServer(async(req,res)=>{
  let raw='';for await(const c of req)raw+=c;if(req.method==='GET'){res.setHeader('Content-Type','application/json');res.end('{"data":[{"id":"fixture-model"}]}');return}
  const body=JSON.parse(raw);modelRequests.push(body);modelCalls++;
  const called=body.messages.some((v:any)=>v.role==='tool'&&String(v.content).includes(recalling?'\"experiences\"':'BILI-REAL-TOOL-RESULT'));
  let message=called?{role:'assistant',content:'找到热门视频：BILI-REAL-TOOL-RESULT。'}:{role:'assistant',content:null,tool_calls:[{id:'bili-test-call',type:'function',function:{name:'bilibili_popular',arguments:'{}'}}]};
  if(recalling)message=called?{role:'assistant',content:'MEMORY-RECALL-CONFIRMED：过去读取过热门元数据，没有观看画面。'}:{role:'assistant',content:null,tool_calls:[{id:'memory-read-call',type:'function',function:{name:'hosted_recall_experience',arguments:'{"query":"popular"}'}}]};
  if(body.stream){const delta=called?message:{role:'assistant',tool_calls:[{index:0,...message.tool_calls![0]}]};res.writeHead(200,{'Content-Type':'text/event-stream'});res.end('data: '+JSON.stringify({id:'bili-fixture',object:'chat.completion.chunk',model:'fixture-model',choices:[{index:0,delta,finish_reason:called?'stop':'tool_calls'}]})+'\n\ndata: [DONE]\n\n')}
  else{res.setHeader('Content-Type','application/json');res.end(JSON.stringify({id:'bili-fixture',object:'chat.completion',model:'fixture-model',choices:[{index:0,message,finish_reason:called?'stop':'tool_calls'}],usage:{prompt_tokens:100,completion_tokens:20,total_tokens:120}}))}
 });
 await new Promise<void>(r=>provider.listen(0,'127.0.0.1',r));permitModelFixture(provider);t.after(()=>provider.close());
 const oldUrl=process.env.FISHBOT_TOOL_API_URL;
 const{app,db,agent}=await createApp({root});t.after(async()=>{await app.close();await rm(root,{recursive:true,force:true});if(oldUrl===undefined)delete process.env.FISHBOT_TOOL_API_URL;else process.env.FISHBOT_TOOL_API_URL=oldUrl});
 const address=await app.listen({host:'127.0.0.1',port:0});process.env.FISHBOT_TOOL_API_URL=address;
 let remoteReads=0;agent.bilibili.web=new BiliWeb(async()=>{remoteReads++;return new Response(JSON.stringify({code:0,data:{list:[{bvid:'BV1xx411c7mD',title:'BILI-REAL-TOOL-RESULT',owner:{name:'fixture'}}]}}))});agent.bilibili.accounts.web=agent.bilibili.web;
 const auth=await app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(db),email:'bili-native@example.com',password:'test-password-123',name:'native'}});assert.equal(auth.statusCode,200,auth.body);
 const cookies={fish_session:auth.cookies[0].value},call=(method:any,url:string,payload?:any)=>app.inject({method,url,payload,cookies});
 const create=await call('POST','/v1/bots',{name:'Bili native test',protocol:'mock',expectedUin:'90003999',apiKey:'bili-fixture-key',config:{...defaultConfig,systemPrompt:'只依据工具真实结果回复。',model:'fixture-model',baseUrl:`http://127.0.0.1:${(provider.address() as any).port}/v1`,proactive:false,behavior:{...defaultConfig.behavior,ownerContactEnabled:false,burstDebounceMs:0,typingSimulation:false,splitMessages:false}}});assert.equal(create.statusCode,201,create.body);const id=create.json().id;
 const view=await call('GET',`/v1/bots/${id}/bilibili`);assert.equal((await call('PUT',`/v1/bots/${id}/bilibili`,{expectedRevision:view.json().revision,config:{enabled:true,allowAnonymous:true,scopes:['public.read']}})).statusCode,200);
 await call('POST',`/v1/bots/${id}/actions`,{action:'start',idempotencyKey:'native-start'});
 await wait(async()=>(await call('GET',`/v1/bots/${id}`)).json().bot.health?.protocol?.ready,'AstrBot ready');
 const[b]=await db.query('SELECT * FROM bots WHERE id=$1',[id]),ctx=await agent.context(b),mock=agent.protocols.mock as MockProtocolAdapter;
 await mock.call(ctx,'/transition',{state:'online',uin:'90003999'});await wait(async()=>(await call('GET',`/v1/bots/${id}`)).json().bot.health?.ai?.ready,'online AstrBot ready');await wait(async()=>(await call('GET',`/v1/bots/${id}`)).json().bot.health?.protocol?.online,'online health');
 await wait(async()=>!!(await mock.call(ctx,'/state')).connected,'QQ reverse WebSocket connected');
 await agent.bilibili.publishBridges();await mock.call(ctx,'/message',{text:'看看B站热门视频',sender:'768687099'});
 await wait(async()=>{const m:any=await mock.call(ctx,'/messages');return m.some((v:any)=>JSON.stringify(v).includes('BILI-REAL-TOOL-RESULT'))},'tool output delivered');
 assert.equal(remoteReads,1);assert.equal(modelCalls,2);assert.ok(modelRequests[0].tools.some((v:any)=>v.function.name==='bilibili_popular'));assert.ok(modelRequests[1].messages.some((v:any)=>v.role==='tool'&&String(v.content).includes('not_applicable')));
 const effects=await db.query('SELECT * FROM tool_effects WHERE bot_id=$1',[id]);assert.equal(effects.length,1);assert.equal(effects[0].result.receipt.status,'not_applicable');assert.match(effects[0].work_id,/^foreground:/);assert.ok(!JSON.stringify(modelRequests).includes('bilibili-bridge.json'));
 const beforeProtocol=await agent.host.record(id,'protocol');
 const settings=await call('GET',`/v1/bots/${id}/bilibili`);assert.equal((await call('PUT',`/v1/bots/${id}/bilibili`,{expectedRevision:settings.json().revision,config:{enabled:false,scopes:[]}})).statusCode,200);
 await agent.bilibili.publishBridges();recalling=true;const beforeRecall=modelRequests.length;
 await mock.call(ctx,'/message',{text:'回顾你最近真实在B站做过什么',sender:'768687099'});
 await wait(async()=>JSON.stringify(await mock.call(ctx,'/messages')).includes('MEMORY-RECALL-CONFIRMED'),'memory recall with Bilibili disabled');
 const recallRequest=modelRequests[beforeRecall];assert.ok(recallRequest.tools.some((v:any)=>v.function.name==='hosted_recall_experience'));assert.ok(!recallRequest.tools.some((v:any)=>v.function.name==='bilibili_popular'));
 assert.ok(JSON.stringify(recallRequest.messages).includes(effects[0].id),'current context already includes the same durable receipt');
 assert.equal(remoteReads,1,'memory recall does not contact Bilibili');assert.equal((await db.query('SELECT id FROM tool_effects WHERE bot_id=$1',[id])).length,1,'reading memory is not another execution experience');
 assert.equal(effects[0].origin_scope,'private:768687099');assert.equal((await agent.host.record(id,'protocol')).pid,beforeProtocol.pid);

});


test('Python Bilibili tool cancellation and stale configuration boundaries',{timeout:20000},async()=>{
 const {execFile}=await import('node:child_process'),{promisify}=await import('node:util');
 const python=path.resolve(`references/astrbot/.venv/${process.platform==='win32'?'Scripts/python.exe':'bin/python'}`);
 const result=await promisify(execFile)(python,['-B','tests/bilibili_tools_test.py'],{windowsHide:true,timeout:15000,maxBuffer:1024*1024});
 assert.match(result.stdout,/PASS Bilibili cancellation propagates/);
});

test('durable analysis resumes the original Work, completes its authorized write and delivers once',{timeout:120000},async t=>{
 const root=await mkdtemp(path.resolve('.local/bilibili-continuation-native-'));let calls=0;
 const provider=http.createServer(async(req,res)=>{let raw='';for await(const c of req)raw+=c;if(req.method==='GET'){res.end('{"data":[]}');return}const body=JSON.parse(raw);calls++;
  const continuation=body.messages.some((m:any)=>m.role==='user'&&JSON.stringify(m.content).includes('平台工具结果续接')&&JSON.stringify(m.content).includes('隔离fixture结果'));
  const liked=body.messages.some((m:any)=>m.role==='tool'&&String(m.content).includes('targetState'));
  const pending=body.messages.some((m:any)=>m.role==='tool'&&String(m.content).includes('"execution": "pending"'));
  const message=continuation?(liked?{role:'assistant',content:'真实续接结果：仅采样帧，声音未知。'}:{role:'assistant',content:null,tool_calls:[{id:'like-after-media',type:'function',function:{name:'bilibili_like',arguments:JSON.stringify({bvid:'BV1xx411c7mD',value:true})}}]}):pending?{role:'assistant',content:'任务已经排队，稍后带结果回来。'}:{role:'assistant',content:null,tool_calls:[{id:'media-call',type:'function',function:{name:'bilibili_analyze',arguments:JSON.stringify({bvid:'BV1xx411c7mD',question:'查看采样画面'})}}]};
  if(body.stream){const delta=(message as any).tool_calls?{role:'assistant',tool_calls:[{index:0,...(message as any).tool_calls[0]}]}:message;res.writeHead(200,{'Content-Type':'text/event-stream'});res.end('data: '+JSON.stringify({id:'fixture',model:'fixture-model',choices:[{index:0,delta,finish_reason:(message as any).tool_calls?'tool_calls':'stop'}]})+'\n\ndata: [DONE]\n\n')}
  else{res.setHeader('Content-Type','application/json');res.end(JSON.stringify({id:'fixture',model:'fixture-model',choices:[{index:0,message,finish_reason:(message as any).tool_calls?'tool_calls':'stop'}],usage:{prompt_tokens:100,completion_tokens:20,total_tokens:120}}))}
 });await new Promise<void>(r=>provider.listen(0,'127.0.0.1',r));permitModelFixture(provider);t.after(()=>provider.close());
 const old=process.env.FISHBOT_TOOL_API_URL,{app,db,agent}=await createApp({root});let release!:()=>void;const gate=new Promise<void>(r=>release=r);
 t.after(async()=>{release();await app.close();await rm(root,{recursive:true,force:true});if(old===undefined)delete process.env.FISHBOT_TOOL_API_URL;else process.env.FISHBOT_TOOL_API_URL=old});
 process.env.FISHBOT_TOOL_API_URL=await app.listen({host:'127.0.0.1',port:0});
 const realCatalog=agent.bilibili.catalog.bind(agent.bilibili);agent.bilibili.catalog=(b:any)=>{agent.bilibili.mediaReady=true;return realCatalog(b)};
 const realExecute=agent.bilibili.execute.bind(agent.bilibili);let writes=0;agent.bilibili.web=new BiliWeb(async(url,init)=>{if(init?.method==='POST'){writes++;return new Response(JSON.stringify({code:0,data:{}}))}assert.equal(new URL(String(url)).pathname,'/x/web-interface/archive/has/like');return new Response(JSON.stringify({code:0,data:0}))});
 agent.bilibili.execute=async(job:any,parent:AbortSignal)=>{if(job.operation!=='analyze')return realExecute(job,parent);await gate;await agent.bilibili.repo.finish(job,{schemaVersion:'fishbot.tool-result.v1',actionId:job.id,execution:'completed',receipt:{status:'not_applicable'},data:{modality:'frames',analysis:'隔离fixture结果',limitations:['声音未知']},usage:{modelRequests:1,costStatus:'unknown'},versions:{adapter:'fixture'}})};
 const auth=await app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(db),email:'media-native@example.com',password:'test-password-123',name:'continuation'}});assert.equal(auth.statusCode,200,auth.body);const cookies={fish_session:auth.cookies[0].value},call=(method:any,url:string,payload?:any)=>app.inject({method,url,payload,cookies});
 const created=await call('POST','/v1/bots',{name:'continuation',protocol:'mock',expectedUin:'90003998',apiKey:'fixture-key',config:{...defaultConfig,model:'fixture-model',baseUrl:`http://127.0.0.1:${(provider.address() as any).port}/v1`,proactive:false,behavior:{...defaultConfig.behavior,ownerUin:'768687099',ownerContactEnabled:false,burstDebounceMs:0,typingSimulation:false,splitMessages:false}}});assert.equal(created.statusCode,201,created.body);const id=created.json().id;
 const view=(await call('GET',`/v1/bots/${id}/bilibili`)).json();assert.equal((await call('PUT',`/v1/bots/${id}/bilibili`,{expectedRevision:view.revision,config:{enabled:true,scopes:['public.read','media.analyze','like.write'],model:{provider:'bot'}}})).statusCode,200);
 await db.query("INSERT INTO bilibili_accounts(bot_id,tenant_id,uid,label,secret) SELECT id,tenant_id,'123','isolated account',$2 FROM bots WHERE id=$1",[id,agent.bilibili.accounts.seal((await db.query('SELECT tenant_id FROM bots WHERE id=$1',[id]))[0].tenant_id,id,{cookies:{SESSDATA:'isolated-session',bili_jct:'isolated-csrf',DedeUserID:'123'},refreshToken:'isolated-refresh'})]);
 await call('POST',`/v1/bots/${id}/actions`,{action:'start',idempotencyKey:'start'});await wait(async()=>(await call('GET',`/v1/bots/${id}`)).json().bot.health?.protocol?.ready,'ready');const[b]=await db.query('SELECT * FROM bots WHERE id=$1',[id]),ctx=await agent.context(b),mock=agent.protocols.mock as MockProtocolAdapter;await mock.call(ctx,'/transition',{state:'online',uin:'90003998'});await wait(async()=>(await call('GET',`/v1/bots/${id}`)).json().bot.health?.ai?.ready,'online AstrBot ready');await wait(async()=>(await call('GET',`/v1/bots/${id}`)).json().bot.health?.protocol?.online,'online');await agent.bilibili.publishBridges();await mock.call(ctx,'/message',{text:'帮我看这个视频的采样帧，然后点赞，告诉我实际结果',sender:'768687099'});
 await wait(async()=>JSON.stringify(await mock.call(ctx,'/messages')).includes('任务已经排队'),'pending response');release();
 try{await wait(async()=>JSON.stringify(await mock.call(ctx,'/messages')).includes('真实续接结果'),'durable result delivered',35000)}catch(e){await writeFile('.local/bilibili-completion-20261103/continuation-debug.json',JSON.stringify({calls,effects:await db.query('SELECT state,completion_state,result FROM tool_effects WHERE bot_id=$1',[id]),messages:await mock.call(ctx,'/messages'),health:(await call('GET',`/v1/bots/${id}`)).json(),log:await readFile(path.join(root,'bots',id,'runtime.log'),'utf8').catch(()=>'' )},null,2));throw e}
 const allEffects=await db.query('SELECT * FROM tool_effects WHERE bot_id=$1 ORDER BY created_at',[id]),effects=allEffects.filter(e=>e.operation==='analyze');assert.equal(allEffects.length,2);assert.equal(effects.length,1);const written=allEffects.find(e=>e.operation==='like');assert.equal(written.work_id,effects[0].work_id);assert.equal(written.continuation.sourceActionId,effects[0].id);assert.equal(written.origin_event,effects[0].origin_event);assert.equal(written.result.receipt.status,'confirmed');assert.equal(writes,1);assert.equal(effects[0].origin_scope,'private:768687099');assert.equal(effects[0].result.execution,'completed');
 const messages:any=await mock.call(ctx,'/messages');assert.equal(messages.filter((m:any)=>JSON.stringify(m).includes('真实续接结果')).length,1);assert.equal(calls,4);
 await db.query("UPDATE tool_effects SET completion_until=now()-interval '1 second' WHERE id=$1",[effects[0].id]);await agent.bilibili.publishBridges();await wait(async()=>{const[r]=await db.query('SELECT completion_state FROM tool_effects WHERE id=$1',[effects[0].id]);return r.completion_state==='done'},'lost acknowledgement reconciled',15000);assert.equal(calls,4);assert.equal((await mock.call(ctx,'/messages') as any[]).filter((m:any)=>JSON.stringify(m).includes('真实续接结果')).length,1);
});


test('native ToolLoop preserves image bytes with explicit provider modalities',{timeout:90000},async t=>{
 const root=await mkdtemp(path.resolve('.local/native-image-contract-'));let imageSeen=false;
 const provider=http.createServer(async(req,res)=>{if(req.method==='GET'){res.end('{"data":[]}');return}let raw='';for await(const c of req)raw+=c;const body=JSON.parse(raw);imageSeen=body.messages.some((m:any)=>Array.isArray(m.content)&&m.content.some((p:any)=>p.type==='image_url'&&p.image_url?.url?.startsWith('data:image/')));const message={role:'assistant',content:imageSeen?'NATIVE-IMAGE-BYTES-CONFIRMED':'IMAGE-MISSING'};res.setHeader('Content-Type','application/json');res.end(JSON.stringify({choices:[{index:0,message,finish_reason:'stop'}]}))});await new Promise<void>(r=>provider.listen(0,'127.0.0.1',r));permitModelFixture(provider);t.after(()=>provider.close());
 const{app,db,agent}=await createApp({root});t.after(async()=>{await app.close();await rm(root,{recursive:true,force:true})});
 const a=await app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(db),email:'native-image@test.com',password:'test-password-123',name:'image'}}),cookies={fish_session:a.cookies[0].value},call=(method:any,url:string,payload?:any)=>app.inject({method,url,payload,cookies});
 const created=await call('POST','/v1/bots',{name:'image contract',protocol:'mock',expectedUin:'90004999',apiKey:'fixture-key',config:{...defaultConfig,model:'fixture-model',baseUrl:`http://127.0.0.1:${(provider.address() as any).port}/v1`,proactive:false,behavior:{...defaultConfig.behavior,typingSimulation:false,splitMessages:false,burstDebounceMs:0}}});assert.equal(created.statusCode,201,created.body);const id=created.json().id;
 await call('POST',`/v1/bots/${id}/actions`,{action:'start',idempotencyKey:'image-start'});await wait(async()=>(await call('GET',`/v1/bots/${id}`)).json().bot.health?.protocol?.ready,'ready');const[b]=await db.query('SELECT * FROM bots WHERE id=$1',[id]),ctx=await agent.context(b),mock=agent.protocols.mock as MockProtocolAdapter;await mock.call(ctx,'/transition',{state:'online',uin:'90004999'});await wait(async()=>(await call('GET',`/v1/bots/${id}`)).json().bot.health?.ai?.ready,'online AstrBot ready');await wait(async()=>(await call('GET',`/v1/bots/${id}`)).json().bot.health?.protocol?.online,'online');
 const cfg=JSON.parse(await readFile(path.join(root,'bots',id,'ai/data/cmd_config.json'),'utf8'));assert.deepEqual(cfg.provider[0].modalities,['text','image','tool_use']);
 const image=(await readFile(path.resolve('tests/fixtures/model-vision-probe.jpg'))).toString('base64');await mock.call(ctx,'/message',{sender:'768687099',text:'查看图片',imageUrl:'base64://'+image});
 await wait(async()=>JSON.stringify(await mock.call(ctx,'/messages')).includes('NATIVE-IMAGE-BYTES-CONFIRMED'),'native image received',40000);assert.equal(imageSeen,true);
});
