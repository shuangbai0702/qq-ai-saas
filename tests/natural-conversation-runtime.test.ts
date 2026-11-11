import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import path from 'node:path';
import {mkdtemp,mkdir,writeFile} from 'node:fs/promises';
import {createApp} from '../apps/control-api/src/app.js';
import {defaultConfig} from '../packages/contracts/index.js';
import {MockProtocolAdapter} from '../adapters/mock/index.js';
import {permitModelFixture} from './helpers/model-fixture.js';
import {issueTestInvite} from './helpers/invitations.js';
const sleep=(ms:number)=>new Promise(r=>setTimeout(r,ms));
async function until(fn:()=>Promise<boolean>,label:string){for(let i=0;i<150;i++){if(await fn())return;await sleep(200)}throw new Error(label)}

test('native SDK: multiple speakers, semantic silence, bounded calls and per-bot dialogue isolation',{timeout:110000},async()=>{
 const requests:any[]=[];
 const server=http.createServer(async(req,res)=>{
  let raw='';for await(const b of req)raw+=b;const body=JSON.parse(raw);requests.push(body);
  const content=body.messages.findLast((x:any)=>x.role==='user')?.content;
  const input=Array.isArray(content)?content.map((x:any)=>x.text||'').join('\n'):String(content||'');
  const reply=input.includes('比赛')?'[NO_REPLY]':input.includes('下班')?'那先坐会儿，今天够忙的。':input.includes('我也')?'给你也留个位子。':'我刚好想聊聊热茶。';
  res.setHeader('Content-Type','application/json');res.end(JSON.stringify({id:'natural-'+requests.length,model:body.model,choices:[{index:0,message:{role:'assistant',content:reply},finish_reason:'stop'}]}));
 });
 await new Promise<void>(r=>server.listen(0,'127.0.0.1',r));permitModelFixture(server);
 const current=await createApp({root:await mkdtemp(path.resolve('.local/natural-runtime-'))});
 try{
  const auth=await current.app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(current.db,2),email:'natural-runtime@test.invalid',name:'自然交流',password:'natural-runtime-123'}});
  const cookies={fish_session:auth.cookies[0].value};
  const config={...defaultConfig,baseUrl:`http://127.0.0.1:${(server.address() as any).port}/v1`,model:'natural-fixture',proactive:false,behavior:{...defaultConfig.behavior!,naturalConversation:true,ownerUin:'',hourlyInterjectionLimit:2,followupSeconds:120,followupReplyProbability:.2,randomReplyProbability:0,quietStart:0,quietEnd:0,typingSimulation:false,typoEnabled:false,splitMessages:false,burstDebounceMs:0,styleLearning:false,memorySummary:false,memeEnabled:false,fatigueMaxReplies:1}};
  const create=async(name:string,uin:string)=>{
   const r=await current.app.inject({method:'POST',url:'/v1/bots',cookies,payload:{name,protocol:'mock',runtimeMode:'shared',expectedUin:uin,apiKey:'natural-runtime-key',config}});assert.equal(r.statusCode,201,r.body);const id=r.json().id;
   await current.app.inject({method:'POST',url:`/v1/bots/${id}/actions`,cookies,payload:{action:'start',idempotencyKey:'start'}});
   await until(async()=>!!(await current.db.query('SELECT health FROM bots WHERE id=$1',[id]))[0].health?.protocol?.ready,'protocol ready');
   const ctx=await current.agent.context((await current.db.query('SELECT * FROM bots WHERE id=$1',[id]))[0]);const mock=current.agent.protocols.mock as MockProtocolAdapter;
   await mock.call(ctx,'/transition',{state:'online',uin});await until(async()=>!!(await mock.call(ctx,'/state')as any).connected,'QQ connected');
   await until(async()=>!!(await current.db.query('SELECT health FROM bots WHERE id=$1',[id]))[0].health?.ai?.ready,'AI ready');
   return {id,ctx,mock,actions:async()=>(await mock.call(ctx,'/messages')as any[]).filter(x=>x.action==='send_group_msg')};
  };
  const a=await create('自然甲','90008881');
  const message=(text:string,sender='11111111',mention=false)=>a.mock.call(a.ctx,'/message',{group:'778899',sender,text,mention});
  await message('来聊会儿',undefined,true);await until(async()=>(await a.actions()).length===1,'initial reply');
  await message('今天刚下班');await until(async()=>(await a.actions()).length===2,'same partner without matching words');
  await message('我也坐会儿','22222222');await until(async()=>(await a.actions()).length===3,'another participant joins despite soft fatigue');
  const before=requests.length;await message('他们在说比赛','33333333');await until(async()=>requests.length===before+1,'ambiguous turn reaches one model judgment');await sleep(500);
  assert.equal((await a.actions()).length,3,'NO_REPLY must not turn into a canned reply or image');
  await message('另开一个话题','44444444');await until(async()=>requests.length===before+2&&(await a.actions()).length===4,'silence does not consume a new-entry quota or break ongoing conversation');
  const b=await create('自然乙','90008882');
  await b.mock.call(b.ctx,'/message',{group:'778899',sender:'11111111',text:'来聊会儿',mention:true});await until(async()=>(await b.actions()).length===1,'second bot reply');
  const bRequest=requests.at(-1);assert.ok(JSON.stringify(bRequest).includes(b.id));
  assert.equal(JSON.stringify(bRequest).includes('今天刚下班'),false,'shared runtime cannot borrow another bot\'s conversation');
  const joined=requests.find(r=>JSON.stringify(r.messages.findLast((m:any)=>m.role==='user')?.content||'').includes('我也坐会儿'));
  assert.ok(JSON.stringify(joined).includes('conversation_candidate'));assert.ok(JSON.stringify(joined).includes('recent_own_turns'));
  await until(async()=>!!(await current.db.query('SELECT health FROM bots WHERE id=$1',[a.id]))[0].health?.ai?.metrics?.replyLatencySamples?.length,'receipt latency published');
  const samples=(await current.db.query('SELECT health FROM bots WHERE id=$1',[a.id]))[0].health.ai.metrics.replyLatencySamples;
  assert.ok(samples.every((x:any)=>x.firstMs>=0&&x.completeMs>=x.firstMs));assert.ok(samples.length<=16);
  await mkdir('test-results/natural-conversation',{recursive:true});
  await writeFile('test-results/natural-conversation/native-runtime.json',JSON.stringify({passed:true,evidence:'Real AstrBot SDK, shared Agent and OneBot; model and QQ transports are fixtures',modelRequests:requests.length,firstBotReplies:(await a.actions()).length,secondBotReplies:(await b.actions()).length,silentDoesNotSpendEntryQuota:true,botIsolation:true},null,2));
 }finally{await current.app.close();await new Promise<void>(r=>server.close(()=>r()))}
});
