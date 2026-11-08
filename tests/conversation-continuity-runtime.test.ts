import {runtimeClient} from '../apps/node-agent/src/autonomous.js';
import {permitModelFixture,nativeMessages} from './helpers/model-fixture.js';import {issueTestInvite} from './helpers/invitations.js';
import test from 'node:test';import assert from 'node:assert/strict';import http from 'node:http';import path from 'node:path';import {mkdtemp,rm} from 'node:fs/promises';
import {createApp} from '../apps/control-api/src/app.js';import {defaultConfig} from '../packages/contracts/index.js';import {MockProtocolAdapter} from '../adapters/mock/index.js';
const sleep=(ms:number)=>new Promise(r=>setTimeout(r,ms));async function wait(fn:()=>Promise<boolean>,label:string){for(let n=0;n<100;n++){if(await fn())return;await sleep(300)}throw new Error('timeout '+label)}

for(const mode of ['passive','active'] as const)test(`native SDK ${mode}: private context, real At and direct owner cross-group tools`,{timeout:100000},async t=>{
 const requests:any[]=[];const server=http.createServer(async(req,res)=>{let text='';for await(const chunk of req)text+=chunk;const body=JSON.parse(text);requests.push(body);
  const input=JSON.stringify(nativeMessages(body).findLast((m:any)=>m.role==='user')?.content||''),last=nativeMessages(body).at(-1);let message:any={role:'assistant',content:input.includes('MENTION_ME')?'来啦[AT:11111111][QQ_BREAK]接着聊':'我记得你刚在群里说过。'},finish='stop';
  if(input.includes('GROUP_SEND_TEST')&&last?.role!=='tool'){assert.ok(body.tools.some((tool:any)=>tool.function.name==='hosted_qq_group_send'));assert.ok(body.tools.some((tool:any)=>tool.function.name==='bilibili_search'),'inner search must exist independently of autonomy mode');assert.equal(body.tools.some((tool:any)=>tool.function.name==='propose_background_task'),mode==='active');message={role:'assistant',content:null,tool_calls:[{id:'group-send',type:'function',function:{name:'hosted_qq_group_send',arguments:JSON.stringify({group_id:'55555',text:'真实群消息继续聊',mention_user_id:'11111111'})}}]};finish='tool_calls'}
  if(input.includes('GROUP_READ_TEST')&&last?.role!=='tool'){assert.ok(body.tools.some((tool:any)=>tool.function.name==='hosted_qq_group_context'));message={role:'assistant',content:null,tool_calls:[{id:'group-read',type:'function',function:{name:'hosted_qq_group_context',arguments:JSON.stringify({group_id:'55555'})}}]};finish='tool_calls'}
  if(input.includes('普通用户看看功能')&&last?.role!=='tool'){
   message={role:'assistant',content:null,tool_calls:[
    {id:'peer-group-read',type:'function',function:{name:'hosted_qq_group_context',arguments:JSON.stringify({group_id:'55555'})}},
    {id:'peer-group-send',type:'function',function:{name:'hosted_qq_group_send',arguments:JSON.stringify({group_id:'55555',text:'不应发送的越权群消息'})}},
   ]};finish='tool_calls';
  }
  res.setHeader('Content-Type','application/json');res.end(JSON.stringify({id:'continuity-'+requests.length,model:body.model,object:'chat.completion',choices:[{index:0,message,finish_reason:finish}],usage:{total_tokens:100}}));
 });await new Promise<void>(r=>server.listen(0,'127.0.0.1',r));permitModelFixture(server);t.after(()=>server.close());
 const root=await mkdtemp(path.resolve('.local/continuity-runtime-'));const current=await createApp({root}).catch(async error=>{await rm(root,{recursive:true,force:true});throw error});t.after(async()=>{await current.app.close();await rm(root,{recursive:true,force:true})});const {app,db,agent}=current;
 const registered=await app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(db),email:'continuity@fixture.invalid',password:'continuity-test-123',name:'临时记忆验收'}});const cookies={fish_session:registered.cookies[0].value};const call=(method:any,url:string,payload?:any)=>app.inject({method,url,payload,cookies});
 const config={...defaultConfig,groups:['55555'],baseUrl:`http://127.0.0.1:${(server.address()as any).port}/v1`,model:'continuity-fixture',proactive:false,behavior:{...defaultConfig.behavior,bilibiliSearchEnabled:true,ownerUin:'11111111',qqProfile:false,qzone:false,naturalConversation:false,groupParticipationEnabled:false,memeEnabled:false,memory:true,memoryAutoCapture:false,memorySummary:true,memorySemantic:false,dailySummaryLimit:0,typingSimulation:false,typoEnabled:false,splitMessages:true,sendIntervalMs:300,burstDebounceMs:0,followupSeconds:0,styleLearning:false,attentionEnabled:false,fatigueEnabled:false}};
 const made=await call('POST','/v1/bots',{name:'临时记忆与真实艾特',runtimeMode:'shared',protocol:'mock',expectedUin:'90006001',config,apiKey:'continuity-fixture-key'});assert.equal(made.statusCode,201,made.body);const id=made.json().id;
 const bili=await call('GET',`/v1/bots/${id}/bilibili`);assert.equal((await call('PUT',`/v1/bots/${id}/bilibili`,{expectedRevision:bili.json().revision,config:{enabled:true,allowAnonymous:true,scopes:['public.read']}})).statusCode,200);
 await call('POST',`/v1/bots/${id}/actions`,{action:'start',idempotencyKey:'start'});await wait(async()=>(await call('GET',`/v1/bots/${id}`)).json().bot.health?.ai?.ready,'AI ready');
 const [record]=await db.query('SELECT * FROM bots WHERE id=$1',[id]),ctx=await agent.context(record),mock=agent.protocols.mock as MockProtocolAdapter;await mock.call(ctx,'/transition',{state:'online',uin:'90006001'});await wait(async()=>!!(await mock.call(ctx,'/state')as any).connected,'protocol online');
 if(mode==='active'){
  // Exercise the actual active foreground without scheduling unrelated model work.
  await agent.autonomy.stop();
  const policy=await agent.autonomy.repo.configure(record.tenant_id,id,1,{mode:'active',rollout:'shadow'});
  await runtimeClient(db)({...record,bot_id:id},'policy',{tenantId:record.tenant_id,botId:id,generation:record.generation,revision:record.revision,epoch:policy.epoch,config:policy.config,until:Date.now()+14000});
 }
 await agent.bilibili.publishBridges();
 const actions=()=>mock.call(ctx,'/messages')as Promise<any[]>;
 await mock.call(ctx,'/message',{group:'55555',sender:'11111111',mention:false,text:'CONTINUITY_UNANSWERED 刚在群里开了头'});await sleep(900);assert.equal(requests.length,0,'unmentioned recording must not wake a reply or summary model');
 await mock.call(ctx,'/message',{sender:'11111111',text:'CONTINUITY_QUERY 群里我刚才说了什么'});await wait(async()=>(await actions()).some(x=>x.action==='send_private_msg'),'private reply');
 const privateRequest=requests.find(r=>JSON.stringify(r.messages).includes('CONTINUITY_QUERY'));assert.ok(privateRequest);assert.match(JSON.stringify(privateRequest),/recent_cross_session_messages/);assert.match(JSON.stringify(privateRequest),/CONTINUITY_UNANSWERED/);assert.match(JSON.stringify(privateRequest),/group:55555/);
 await mock.call(ctx,'/message',{group:'55555',sender:'11111111',text:'MENTION_ME'});await wait(async()=>(await actions()).filter(x=>x.action==='send_group_msg').length>=2,'split group reply');
 let groupReplies=(await actions()).filter(x=>x.action==='send_group_msg');assert.deepEqual(groupReplies[0].params.message[0],{type:'at',data:{qq:'11111111'}});assert.equal(groupReplies[1].params.message.some((m:any)=>m.type==='at'),false);assert.ok(!JSON.stringify(groupReplies).includes('[AT:'));
 await mock.call(ctx,'/message',{sender:'11111111',text:'GROUP_SEND_TEST 去指定群叫我接着聊'});await wait(async()=>requests.some(r=>r.messages?.some((m:any)=>m.role==='tool'&&String(m.content).includes('真实艾特'))),'native tool receipt consumed');
 groupReplies=(await actions()).filter(x=>x.action==='send_group_msg');assert.equal(groupReplies.length,3);assert.deepEqual(groupReplies[2].params.message[0],{type:'at',data:{qq:'11111111'}});assert.equal(String(groupReplies[2].params.group_id),'55555');
 await mock.call(ctx,'/message',{sender:'11111111',text:'GROUP_READ_TEST 看看指定群的最近消息'});
 await wait(async()=>requests.some(r=>r.messages?.some((m:any)=>m.role==='tool'&&String(m.content).includes('recent_received'))),'actual group read consumed by native loop');
 if(mode==='active'){
  await wait(async()=>{const [s]=await db.query('SELECT s.*,b.generation,b.revision FROM autonomy_settings s JOIN bots b ON b.id=s.bot_id WHERE s.bot_id=$1',[id]);const snapshot=await runtimeClient(db)(s,'snapshot',{});return snapshot.foregroundFeedback?.result?.evidence?.some((e:any)=>e.source==='qq_group_context')&&snapshot.foregroundFeedback.result.toolResults.some((r:any)=>r.tool==='hosted_qq_group_context'&&r.status==='returned')},'fresh outer feedback contains real read receipt');
 }
 const peerStart=requests.length,groupWritesBefore=(await actions()).filter(x=>x.action==='send_group_msg').length;
 await mock.call(ctx,'/message',{sender:'22222222',text:'普通用户看看功能'});
 await wait(async()=>requests.slice(peerStart).some(r=>['peer-group-read','peer-group-send'].every(id=>r.messages.some((m:any)=>m.role==='tool'&&m.tool_call_id===id&&JSON.parse(m.content).receipt?.status==='rejected'))),'real peer group read/send calls are rejected');
 if(mode==='active') {
  const [handoffs]=await db.query('SELECT count(*)::int AS count FROM autonomy_goals WHERE bot_id=$1',[id]);
  assert.equal(handoffs.count,0,'immediate execution must not require or duplicate a durable goal');
 }
 const peer=requests.slice(peerStart).find(r=>nativeMessages(r).at(-1)?.role==='user');
 const owner=requests.find(r=>JSON.stringify(nativeMessages(r).at(-1)?.content).includes('GROUP_READ_TEST'));
 const groupSchema=(r:any)=>r.tools.filter((tool:any)=>tool.function.name.startsWith('hosted_qq_group_'));
 assert.deepEqual(groupSchema(peer),groupSchema(owner),'configured group schemas remain visible and stable for every author');
 assert.equal((await actions()).filter(x=>x.action==='send_group_msg').length,groupWritesBefore,'denied peer calls cannot send any protocol group message');
});
