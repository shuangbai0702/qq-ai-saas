import {permitModelFixture} from './helpers/model-fixture.js';
import {issueTestInvite} from './helpers/invitations.js';
import test from 'node:test';import assert from 'node:assert/strict';import http from 'node:http';import {mkdtemp} from 'node:fs/promises';import path from 'node:path';
import {createApp} from '../apps/control-api/src/app.js';import {defaultConfig} from '../packages/contracts/index.js';import {MockProtocolAdapter} from '../adapters/mock/index.js';
const sleep=(ms:number)=>new Promise(r=>setTimeout(r,ms));async function until(fn:()=>Promise<boolean>,why:string){for(let i=0;i<200;i++){if(await fn())return;await sleep(100)}throw new Error(why)}
test('native followup bypasses lexical and entry limits; all-message mode cannot bypass autonomy whitelist',{timeout:100000},async()=>{
 const requests:any[]=[];const server=http.createServer(async(req,res)=>{let raw='';for await(const b of req)raw+=b;const body=JSON.parse(raw);requests.push(body);res.setHeader('Content-Type','application/json');res.end(JSON.stringify({id:'group-participation',model:body.model,choices:[{index:0,message:{role:'assistant',content:'接着聊呀。'},finish_reason:'stop'}]}))});await new Promise<void>(r=>server.listen(0,'127.0.0.1',r));permitModelFixture(server);
 const current=await createApp({root:await mkdtemp(path.resolve('.local/group-participation-'))});
 try{
  const auth=await current.app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(current.db),email:'group-participation@test.invalid',name:'群搭话',password:'group-participation-123'}});const cookies={fish_session:auth.cookies[0].value};
  let config={...defaultConfig,baseUrl:`http://127.0.0.1:${(server.address()as any).port}/v1`,model:'group-participation',proactive:false,behavior:{...defaultConfig.behavior!,naturalConversation:true,groupParticipationEnabled:true,groupParticipationGroups:[],hourlyInterjectionLimit:0,followupSeconds:120,followupReplyProbability:1,randomReplyProbability:0.2,quietStart:0,quietEnd:0,typingSimulation:false,typoEnabled:false,splitMessages:false,burstDebounceMs:0,styleLearning:false,memorySummary:false,memeEnabled:false,attentionEnabled:true,fatigueEnabled:true,fatigueMaxReplies:1}};
  const created=await current.app.inject({method:'POST',url:'/v1/bots',cookies,payload:{name:'群搭话',protocol:'mock',runtimeMode:'shared',expectedUin:'90007771',apiKey:'group-participation-key',config}});assert.equal(created.statusCode,201,created.body);const id=created.json().id;
  await current.app.inject({method:'POST',url:`/v1/bots/${id}/actions`,cookies,payload:{action:'start',idempotencyKey:'start'}});await until(async()=>!!(await current.db.query('SELECT health FROM bots WHERE id=$1',[id]))[0].health?.ai?.ready,'AI ready');
  const ctx=await current.agent.context((await current.db.query('SELECT * FROM bots WHERE id=$1',[id]))[0]),mock=current.agent.protocols.mock as MockProtocolAdapter;
  await mock.call(ctx,'/transition',{state:'online',uin:'90007771'});await until(async()=>!!(await mock.call(ctx,'/state')as any).connected,'QQ connected');
  const actions=async()=>(await mock.call(ctx,'/messages')as any[]).filter(x=>x.action==='send_group_msg'),message=(text:string,mention=false,sender='77778888')=>mock.call(ctx,'/message',{group:'778899',sender,text,mention});
  await message('无人邀请的新话题');await sleep(400);assert.equal(requests.length,0,'legacy random value is ignored');
  await message('来聊聊',true);await until(async()=>(await actions()).length===1,'mention');
  await message('今天刚下班');await until(async()=>(await actions()).length===2,'same partner needs no repeated words');
  await message('我也坐一会儿',false,'87654321');await until(async()=>(await actions()).length===3,'another participant is an ongoing conversation candidate');assert.equal(requests.length,3,'no separate judge for followup');
  const apply=async(next:typeof config)=>{config=next;const d=(await current.app.inject({url:`/v1/bots/${id}`,cookies})).json();const saved=await current.app.inject({method:'PUT',url:`/v1/bots/${id}/config`,cookies,payload:{expectedRevision:d.bot.revision,name:d.bot.name,expectedUin:d.bot.expected_uin,config}});assert.equal(saved.statusCode,200,saved.body);await until(async()=>(await current.db.query('SELECT applied_revision FROM bots WHERE id=$1',[id]))[0].applied_revision===saved.json().revision,'applied');await until(async()=>!!(await mock.call(ctx,'/state')as any).connected,'connected after apply')};
  await apply({...config,historyTurns:29,behavior:{...config.behavior,naturalConversation:false}});
  await message('继续换个说法',false,'87654321');await until(async()=>(await actions()).length===4,'same partner follows after apply with zero entry quota');
  await message('其他人的独立话题',false,'12345678');await sleep(400);assert.equal(requests.length,4,'disabled multi-person followup does not become a cold random entry');
  await apply({...config,trigger:'all',behavior:{...config.behavior,followupSeconds:0}});
  await message('全消息也不能绕过白名单');await sleep(400);assert.equal(requests.length,4);
  await message('直接叫你回答',true);await until(async()=>(await actions()).length===5,'explicit mention remains available');
 }finally{await current.app.close();await new Promise<void>(r=>server.close(()=>r()))}
});
