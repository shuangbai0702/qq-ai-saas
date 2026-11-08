import {permitModelFixture,platformContext,nativeMessages} from './helpers/model-fixture.js';
import {issueTestInvite} from './helpers/invitations.js';
import test from 'node:test';import assert from 'node:assert/strict';import http from 'node:http';import {mkdtemp} from 'node:fs/promises';import path from 'node:path';
import {createApp} from '../apps/control-api/src/app.js';import {defaultConfig} from '../packages/contracts/index.js';import {MockProtocolAdapter} from '../adapters/mock/index.js';
const sleep=(ms:number)=>new Promise(r=>setTimeout(r,ms));async function until(fn:()=>Promise<boolean>,why:string){for(let i=0;i<200;i++){if(await fn())return;await sleep(100)}throw new Error(why)}
test('native followup bypasses lexical and entry limits; all-message mode cannot bypass autonomy whitelist',{timeout:100000},async()=>{
 const requests:any[]=[];const server=http.createServer(async(req,res)=>{let raw='';for await(const b of req)raw+=b;const body=JSON.parse(raw);requests.push(body);const latest=nativeMessages(body).filter((m:any)=>m.role==='user').at(-1);const silent=JSON.stringify(latest?.content).includes('候选保持旁听');res.setHeader('Content-Type','application/json');res.end(JSON.stringify({id:'group-participation',model:body.model,choices:[{index:0,message:{role:'assistant',content:silent?'[NO_REPLY]':'接着聊呀。'},finish_reason:'stop'}]}))});await new Promise<void>(r=>server.listen(0,'127.0.0.1',r));permitModelFixture(server);
 const current=await createApp({root:await mkdtemp(path.resolve('.local/group-participation-'))});
 try{
  const auth=await current.app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(current.db),email:'group-participation@test.invalid',name:'群搭话',password:'group-participation-123'}});const cookies={fish_session:auth.cookies[0].value};
  let config={...defaultConfig,baseUrl:`http://127.0.0.1:${(server.address()as any).port}/v1`,model:'group-participation',proactive:false,behavior:{...defaultConfig.behavior!,ownerUin:'77778888',naturalConversation:true,groupParticipationEnabled:true,groupParticipationGroups:[],hourlyInterjectionLimit:0,followupSeconds:120,followupReplyProbability:1,randomReplyProbability:0.2,quietStart:0,quietEnd:0,typingSimulation:false,typoEnabled:false,splitMessages:false,burstDebounceMs:0,styleLearning:false,memorySummary:false,memeEnabled:false,attentionEnabled:true,fatigueEnabled:true,fatigueMaxReplies:1}};
  const created=await current.app.inject({method:'POST',url:'/v1/bots',cookies,payload:{name:'群搭话',protocol:'mock',runtimeMode:'shared',expectedUin:'90007771',apiKey:'group-participation-key',config}});assert.equal(created.statusCode,201,created.body);const id=created.json().id;
  await current.app.inject({method:'POST',url:`/v1/bots/${id}/actions`,cookies,payload:{action:'start',idempotencyKey:'start'}});await until(async()=>!!(await current.db.query('SELECT health FROM bots WHERE id=$1',[id]))[0].health?.ai?.ready,'AI ready');
  const ctx=await current.agent.context((await current.db.query('SELECT * FROM bots WHERE id=$1',[id]))[0]),mock=current.agent.protocols.mock as MockProtocolAdapter;
  await mock.call(ctx,'/transition',{state:'online',uin:'90007771'});await until(async()=>!!(await mock.call(ctx,'/state')as any).connected,'QQ connected');
  const actions=async()=>(await mock.call(ctx,'/messages')as any[]).filter(x=>x.action==='send_group_msg'),message=(text:string,mention=false,sender='77778888')=>mock.call(ctx,'/message',{group:'778899',sender,text,mention});
  await message('无人邀请的新话题');await sleep(400);assert.equal(requests.length,0,'legacy random value is ignored');
  await message('来聊聊',true);await until(async()=>(await actions()).length===1,'mention');
  await message('今天刚下班');await until(async()=>(await actions()).length===2,'same partner needs no repeated words');
  await message('我是双白，我也坐一会儿',false,'87654321');await until(async()=>(await actions()).length===3,'another participant is an ongoing conversation candidate');assert.equal(requests.length,3,'no separate judge for followup');

  const first=platformContext(requests[0]),joined=platformContext(requests[2]);
  const samePartner=platformContext(requests[1]);
  assert.equal(first.participation_entry.explicitly_addressed,true);assert.equal(first.conversation_candidate,true,'mention activates the main model without forcing a reply');
  assert.equal(samePartner.participation_entry.same_partner_followup,true);assert.equal(samePartner.identity.directed_to_bot,false);
  assert.equal(samePartner.conversation_candidate,true,'a followup window must not invent an explicit addressee');
  assert.equal(joined.participation_entry.reply_required_by_window,false);
  assert.ok(joined.conversation_timeline.some((x:any)=>x.role==='self'&&x.receipt==='confirmed'&&x.message_ids.length),'native sends reach the same timeline with scoped receipts');
  assert.equal(first.identity.sender_id,'77778888');assert.equal(first.identity.owner,true);assert.equal(joined.identity.sender_id,'87654321');assert.equal(joined.identity.owner,false);
  assert.equal(joined.identity.owner_id,config.behavior.ownerUin||null,'a nickname or claim cannot replace configured owner');
  assert.ok(joined.dialogue.feedback.some((f:any)=>f.recipient_id==='77778888'&&f.event_id&&typeof f.at==='number'),'past assistant replies retain their real recipients');
  const history=nativeMessages(requests[2]).filter((m:any)=>m.role==='user').map((m:any)=>JSON.stringify(m.content));
  assert.ok(history.some((m:string)=>m.includes('[QQ消息来源]')&&m.includes('77778888')),'native history preserves earlier author');
  const currentSource=history.at(-1);assert.ok(currentSource&&currentSource.includes('[QQ消息来源]')&&currentSource.includes('87654321'),'current source is preserved with the author claim treated as text');
  const apply=async(next:typeof config)=>{config=next;const d=(await current.app.inject({url:`/v1/bots/${id}`,cookies})).json();const saved=await current.app.inject({method:'PUT',url:`/v1/bots/${id}/config`,cookies,payload:{expectedRevision:d.bot.revision,name:d.bot.name,expectedUin:d.bot.expected_uin,config}});assert.equal(saved.statusCode,200,saved.body);await until(async()=>(await current.db.query('SELECT applied_revision FROM bots WHERE id=$1',[id]))[0].applied_revision===saved.json().revision,'applied');await until(async()=>!!(await mock.call(ctx,'/state')as any).connected,'connected after apply')};
  await apply({...config,historyTurns:29,behavior:{...config.behavior,naturalConversation:false}});
  await message('继续换个说法',false,'87654321');await until(async()=>(await actions()).length===4,'same partner follows after apply with zero entry quota');
  await message('其他人的独立话题',false,'12345678');await sleep(400);assert.equal(requests.length,4,'disabled multi-person followup does not become a cold random entry');
  await apply({...config,trigger:'all',behavior:{...config.behavior,followupSeconds:0}});
  await message('全消息也不能绕过白名单');await sleep(400);assert.equal(requests.length,4);
  await message('直接叫你回答',true);await until(async()=>(await actions()).length===5,'explicit mention remains available');
  await apply({...config,behavior:{...config.behavior,naturalConversation:true,followupSeconds:120}});
  await message('继续建立真实对话',true);await until(async()=>(await actions()).length===6,'explicit exchange restores the conversation');
  await message('候选保持旁听',false,'12345678');
  await until(async()=>(await current.db.query('SELECT health FROM bots WHERE id=$1',[id]))[0].health?.ai?.metrics?.lastPipeline?.reason==='model_no_reply_marker','candidate silence completes the actual native pipeline');
  assert.equal(requests.length,7,'participation stays inside the original model call');assert.equal((await actions()).length,6,'NO_REPLY does not create a protocol send');
  const candidateSystem=requests[6].messages.filter((m:any)=>m.role==='system').map((m:any)=>m.content).join('\n');
  const entry=requests[6].messages.filter((m:any)=>m.role==='user'&&typeof m.content==='string'&&m.content.startsWith('[FishBot 平台本轮事件资料 v1]')).at(-1);
  assert.ok(platformContext(requests[6]).currentGuidance.includes('[群聊参与判断，先于回复格式与任务执行]'));
  assert.ok(!candidateSystem.includes('[群聊参与判断，先于回复格式与任务执行]'),'per-event guidance must not rewrite immutable System');
  assert.equal(platformContext(requests[6]).participation_entry.reply_required_by_window,false);
  await message('我也接着刚才的话题聊',false,'12345678');await until(async()=>(await actions()).length===7,'silence does not disable another meaningful followup');
  assert.equal(requests.length,8);
 }finally{await current.app.close();await new Promise<void>(r=>server.close(()=>r()))}
});
