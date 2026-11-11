import {permitModelFixture} from './helpers/model-fixture.js';
import {issueTestInvite} from './helpers/invitations.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import {mkdtemp} from 'node:fs/promises';
import path from 'node:path';
import {createApp} from '../apps/control-api/src/app.js';
import {defaultConfig} from '../packages/contracts/index.js';
import {MockProtocolAdapter} from '../adapters/mock/index.js';

const sleep=(ms:number)=>new Promise(r=>setTimeout(r,ms));
async function until(check:()=>Promise<boolean>,why:string){for(let i=0;i<250;i++){if(await check())return;await sleep(100)}throw Error('Pipeline runtime: '+why)}

test('native pipeline owns inbound identity, duplicate suppression and self-message filtering',{timeout:90000},async()=>{
 const calls:any[]=[];
 const model=http.createServer(async(req,res)=>{let raw='';for await(const chunk of req)raw+=chunk;const body=JSON.parse(raw);calls.push(body);res.setHeader('Content-Type','application/json');res.end(JSON.stringify({id:'pipeline-fixture',model:body.model,choices:[{index:0,message:{role:'assistant',content:'收到你的真实消息。'},finish_reason:'stop'}]}))});
 await new Promise<void>(r=>model.listen(0,'127.0.0.1',r));permitModelFixture(model);
 const root=await mkdtemp(path.resolve('.local/pipeline-native-')),current=await createApp({root});
 try{
  const registered=await current.app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(current.db),email:'pipeline@fixture.invalid',password:'pipeline-fixture-123',name:'管线验收'}}),cookies={fish_session:registered.cookies[0].value};
  const config={...defaultConfig,baseUrl:`http://127.0.0.1:${(model.address()as any).port}/v1`,model:'pipeline-fixture',proactive:false,behavior:{...defaultConfig.behavior!,ownerUin:'12345678',groupParticipationEnabled:false,typingSimulation:false,typoEnabled:false,splitMessages:false,burstDebounceMs:0,styleLearning:false,memorySummary:false,memeEnabled:false,fatigueEnabled:false}};
  const created=await current.app.inject({method:'POST',url:'/v1/bots',cookies,payload:{name:'管线验收',protocol:'mock',runtimeMode:'shared',expectedUin:'90008771',apiKey:'pipeline-fixture-key',config}});assert.equal(created.statusCode,201,created.body);const id=created.json().id;
  await current.app.inject({method:'POST',url:`/v1/bots/${id}/actions`,cookies,payload:{action:'start',idempotencyKey:'start'}});await until(async()=>!!(await current.db.query('SELECT health FROM bots WHERE id=$1',[id]))[0].health?.protocol?.ready,'protocol ready');
  const ctx=await current.agent.context((await current.db.query('SELECT * FROM bots WHERE id=$1',[id]))[0]),mock=current.agent.protocols.mock as MockProtocolAdapter;
  await mock.call(ctx,'/transition',{state:'online',uin:'90008771'});await until(async()=>!!(await mock.call(ctx,'/state')as any).connected,'QQ connected');
  await until(async()=>!!(await current.db.query('SELECT health FROM bots WHERE id=$1',[id]))[0].health?.ai?.ready,'AI ready');
  const sends=async()=>(await mock.call(ctx,'/messages')as any[]).filter(x=>x.action.startsWith('send_'));
  const inbound=(group?:string,sender='12345678')=>mock.call(ctx,'/message',{...(group?{group,mention:true}:{}),sender,messageId:81001,text:'同一条真实入站'});
  await inbound();await until(async()=>(await sends()).length===1,'first private reply');
  await inbound();await sleep(900);assert.equal((await sends()).length,1,'redelivery of a real QQ event cannot run the reply pipeline twice');
  await inbound('22222');await until(async()=>(await sends()).length===2,'same protocol ID in another scope is independent');
  await inbound('22222');await sleep(900);assert.equal((await sends()).length,2,'group redelivery is also idempotent');
  await inbound('33333');await until(async()=>(await sends()).length===3,'another group keeps its own event namespace');
  await inbound('33333','90008771');await sleep(900);assert.equal((await sends()).length,3,'own messages never elicit replies or refresh relationships');
  assert.equal(calls.length,3,'duplicate input is stopped before model and tool execution');
 }finally{await current.app.close();await new Promise<void>(r=>model.close(()=>r()))}
});
