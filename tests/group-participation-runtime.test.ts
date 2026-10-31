import test from 'node:test';import assert from 'node:assert/strict';import http from 'node:http';import path from 'node:path';import {mkdtemp} from 'node:fs/promises';
import {createApp} from '../apps/control-api/src/app.js';import {defaultConfig} from '../packages/contracts/index.js';import {MockProtocolAdapter} from '../adapters/mock/index.js';import {permitModelFixture} from './helpers/model-fixture.js';import {issueTestInvite} from './helpers/invitations.js';
const sleep=(ms:number)=>new Promise(r=>setTimeout(r,ms));async function until(fn:()=>Promise<boolean>,label:string){for(let i=0;i<150;i++){if(await fn())return;await sleep(200)}throw new Error(label)}
test('native group followup respects topic and hourly quota, explicit mentions bypass it and apply preserves the quota',{timeout:100000},async()=>{
 const requests:any[]=[];const server=http.createServer(async(req,res)=>{let raw='';for await(const b of req)raw+=b;const body=JSON.parse(raw);requests.push(body);res.setHeader('Content-Type','application/json');res.end(JSON.stringify({id:'group-participation',model:body.model,choices:[{index:0,message:{role:'assistant',content:'白饭确实香呀。'},finish_reason:'stop'}]}))});await new Promise<void>(r=>server.listen(0,'127.0.0.1',r));permitModelFixture(server);
 const current=await createApp({root:await mkdtemp(path.resolve('.local/group-participation-'))});
 try{
  const auth=await current.app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(current.db),email:'group-participation@test.invalid',name:'群搭话',password:'group-participation-123'}});const cookies={fish_session:auth.cookies[0].value};
  const config={...defaultConfig,baseUrl:`http://127.0.0.1:${(server.address()as any).port}/v1`,model:'group-participation',proactive:false,behavior:{...defaultConfig.behavior!,hourlyInterjectionLimit:1,followupSeconds:120,followupReplyProbability:1,randomReplyProbability:0,quietStart:0,quietEnd:0,typingSimulation:false,typoEnabled:false,splitMessages:false,burstDebounceMs:0,styleLearning:false,memorySummary:false,memeEnabled:false,attentionEnabled:true,fatigueEnabled:true}};
  const created=await current.app.inject({method:'POST',url:'/v1/bots',cookies,payload:{name:'群搭话',protocol:'mock',runtimeMode:'shared',expectedUin:'90007771',apiKey:'group-participation-key',config}});assert.equal(created.statusCode,201,created.body);const id=created.json().id;
  await current.app.inject({method:'POST',url:`/v1/bots/${id}/actions`,cookies,payload:{action:'start',idempotencyKey:'start'}});await until(async()=>!!(await current.db.query('SELECT health FROM bots WHERE id=$1',[id]))[0].health?.ai?.ready,'ready');const ctx=await current.agent.context((await current.db.query('SELECT * FROM bots WHERE id=$1',[id]))[0]),mock=current.agent.protocols.mock as MockProtocolAdapter;
  await mock.call(ctx,'/transition',{state:'online',uin:'90007771'});await until(async()=>!!(await mock.call(ctx,'/state')as any).connected,'connected');
  const actions=async()=>(await mock.call(ctx,'/messages')as any[]).filter(x=>x.action==='send_group_msg');
  const message=(text:string,mention=false)=>mock.call(ctx,'/message',{group:'777777',sender:'12345678',text,mention});
  await message('白饭好吃吗',true);await until(async()=>requests.length===1&&(await actions()).length===1,'explicit initial reply');
  await message('明天爬山去吗');await sleep(800);assert.equal(requests.length,1,'unrelated followup does not infer');
  await message('白饭配什么好呢');await until(async()=>requests.length===2&&(await actions()).length===2,'unmentioned related followup reaches native SDK');
  await message('白饭再来一点');await sleep(800);assert.equal(requests.length,2,'hourly quota suppresses extra unmentioned inference');
  await message('白饭再聊聊',true);await until(async()=>requests.length===3&&(await actions()).length===3,'explicit mention bypasses quota');
  const d=(await current.app.inject({url:`/v1/bots/${id}`,cookies})).json();const saved=await current.app.inject({method:'PUT',url:`/v1/bots/${id}/config`,cookies,payload:{expectedRevision:d.bot.revision,name:d.bot.name,expectedUin:d.bot.expected_uin,config:{...config,historyTurns:29}}});assert.equal(saved.statusCode,200,saved.body);await until(async()=>(await current.db.query('SELECT revision,applied_revision,health FROM bots WHERE id=$1',[id])).some(b=>b.revision===saved.json().revision&&b.applied_revision===b.revision&&b.health?.ai?.ready),'live apply');await until(async()=>!!(await mock.call(ctx,'/state')as any).connected,'reconnected after member apply');
  await message('白饭还想再聊');await sleep(800);assert.equal(requests.length,3,'config apply cannot reset the persisted hourly quota');
  await message('白饭说完了',true);await until(async()=>requests.length===4&&(await actions()).length===4,'mention remains responsive after apply');
  const latest=(await current.app.inject({url:`/v1/bots/${id}`,cookies})).json();const softened={...config,behavior:{...config.behavior,attentionEnabled:false,hourlyInterjectionLimit:2}};
  const changed=await current.app.inject({method:'PUT',url:`/v1/bots/${id}/config`,cookies,payload:{expectedRevision:latest.bot.revision,name:latest.bot.name,expectedUin:latest.bot.expected_uin,config:softened}});assert.equal(changed.statusCode,200,changed.body);
  await until(async()=>(await current.db.query('SELECT applied_revision FROM bots WHERE id=$1',[id]))[0].applied_revision===changed.json().revision,'soft topic config applied');await until(async()=>!!(await mock.call(ctx,'/state')as any).connected,'soft topic connected');
  await message('你好，又来了');await until(async()=>requests.length===5&&(await actions()).length===5,'natural followup without shared words reaches contextual model when lexical filter is off');
  await message('换个说法接着聊');await sleep(800);assert.equal(requests.length,5,'soft topic handling still enforces persisted hourly quota');

  const allCurrent=(await current.app.inject({url:`/v1/bots/${id}`,cookies})).json();
  const allConfig={...softened,trigger:'all' as const,behavior:{...softened.behavior,fatigueMaxReplies:1,hourlyInterjectionLimit:20}};
  const allSaved=await current.app.inject({method:'PUT',url:`/v1/bots/${id}/config`,cookies,payload:{expectedRevision:allCurrent.bot.revision,name:allCurrent.bot.name,expectedUin:allCurrent.bot.expected_uin,config:allConfig}});assert.equal(allSaved.statusCode,200,allSaved.body);
  await until(async()=>(await current.db.query('SELECT applied_revision FROM bots WHERE id=$1',[id]))[0].applied_revision===allSaved.json().revision,'all mode applied');await until(async()=>!!(await mock.call(ctx,'/state')as any).connected,'all mode connected');
  await message('全群模式第一次自然接话');await until(async()=>requests.length===6&&(await actions()).length===6,'all mode first reply');
  await message('全群模式第二次自然接话');await sleep(800);assert.equal(requests.length,6,'all mode must still honor configured fatigue');
  await message('直接叫你回答',true);await until(async()=>requests.length===7&&(await actions()).length===7,'explicit mention still bypasses fatigue');

 }finally{await current.app.close();await new Promise<void>(r=>server.close(()=>r()))}
});
