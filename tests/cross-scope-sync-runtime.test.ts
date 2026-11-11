import test from 'node:test';import assert from 'node:assert/strict';import http from 'node:http';
import path from 'node:path';import {mkdtemp,readFile} from 'node:fs/promises';
import {createApp} from '../apps/control-api/src/app.js';import {defaultConfig} from '../packages/contracts/index.js';
import {MockProtocolAdapter} from '../adapters/mock/index.js';
import {permitModelFixture,platformContext} from './helpers/model-fixture.js';import {issueTestInvite} from './helpers/invitations.js';
const sleep=(ms:number)=>new Promise(r=>setTimeout(r,ms));
async function until(check:()=>Promise<boolean>,why:string){for(let i=0;i<300;i++){if(await check())return;await sleep(100)}throw Error(why)}

test('real SDK sees same-bot private/group references, retains owner attention and permits mentioned NO_REPLY',{timeout:120000},async()=>{
 const calls:any[]=[];const model=http.createServer(async(req,res)=>{
  let raw='';for await(const chunk of req)raw+=chunk;const body=JSON.parse(raw);calls.push(body);
  const content=calls.length===2?'[NO_REPLY]':'fixture reply '+calls.length;
  res.setHeader('Content-Type','application/json');res.end(JSON.stringify({id:'sync-fixture',model:body.model,choices:[{index:0,message:{role:'assistant',content},finish_reason:'stop'}]}));
 });await new Promise<void>(r=>model.listen(0,'127.0.0.1',r));permitModelFixture(model);
 const root=await mkdtemp(path.resolve('.local/cross-scope-runtime-')),current=await createApp({root});
 try{
  const auth=await current.app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(current.db),email:'sync@test.invalid',name:'sync',password:'sync-fixture-123'}}),cookies={fish_session:auth.cookies[0].value};
  const config={...defaultConfig,proactive:false,baseUrl:`http://127.0.0.1:${(model.address()as any).port}/v1`,model:'sync-fixture',behavior:{...defaultConfig.behavior!,ownerUin:'12345678',groupParticipationEnabled:false,ownerContactEnabled:false,autonomousGroupContact:false,proactiveModelSchedule:false,selfDevelopment:false,followupSeconds:0,typingSimulation:false,typoEnabled:false,splitMessages:false,burstDebounceMs:0,styleLearning:false,memorySummary:false,memorySemantic:false,memeEnabled:false,fatigueEnabled:false}};
  const created=await current.app.inject({method:'POST',url:'/v1/bots',cookies,payload:{name:'跨Scope验收',protocol:'mock',runtimeMode:'shared',expectedUin:'90008874',apiKey:'sync-local-fixture',config}});assert.equal(created.statusCode,201,created.body);const id=created.json().id;
  await current.app.inject({method:'POST',url:`/v1/bots/${id}/actions`,cookies,payload:{action:'start',idempotencyKey:'start'}});
  await until(async()=>!!(await current.db.query('SELECT health FROM bots WHERE id=$1',[id]))[0].health?.protocol?.ready,'protocol ready');
  const ctx=await current.agent.context((await current.db.query('SELECT * FROM bots WHERE id=$1',[id]))[0]),mock=current.agent.protocols.mock as MockProtocolAdapter;
  await mock.call(ctx,'/transition',{state:'online',uin:'90008874'});await until(async()=>!!(await mock.call(ctx,'/state')as any).connected,'connected');
  await until(async()=>!!(await current.db.query('SELECT health FROM bots WHERE id=$1',[id]))[0].health?.ai?.ready,'AI ready');
  const sent=async()=>(await mock.call(ctx,'/messages')as any[]).filter(x=>x.action.startsWith('send_'));
  const settled=()=>until(async()=>{try{return JSON.parse(await readFile(path.join(root,'bots',id,'memory-activity.json'),'utf8')).active===false}catch{return false}},'settled');
  await mock.call(ctx,'/message',{sender:'12345678',messageId:91000,text:'PRIVATE_SCOPE_SIGNAL'});
  await until(async()=>calls.length===1&&(await sent()).length===1,'private reply');await settled();
  await mock.call(ctx,'/message',{group:'778180',sender:'12345678',messageId:91001,mention:true,text:'GROUP_A_SIGNAL'});
  await until(async()=>calls.length===2,'mentioned request');await settled();assert.equal((await sent()).length,1,'NO_REPLY must not dispatch even when mentioned');
  const group=platformContext(calls[1]).recent_cross_session_messages;
  assert.ok(group.some((r:any)=>r.source_scope==='private:12345678'&&r.text==='PRIVATE_SCOPE_SIGNAL'&&r.sender_id==='12345678'&&r.attention==='[important]'));
  await mock.call(ctx,'/message',{group:'778181',sender:'12345678',messageId:91002,mention:true,text:'GROUP_B_SIGNAL'});
  await until(async()=>calls.length===3&&(await sent()).length===2,'other group reply');await settled();
  const other=platformContext(calls[2]).recent_cross_session_messages;
  assert.ok(other.some((r:any)=>r.source_scope==='group:778180'&&r.text==='GROUP_A_SIGNAL'),'unanswered group observation reaches other group');
  await mock.call(ctx,'/message',{sender:'12345678',messageId:91003,text:'PRIVATE_FOLLOWUP'});
  await until(async()=>calls.length===4&&(await sent()).length===3,'private followup');await settled();
  const back=platformContext(calls[3]).recent_cross_session_messages;
  assert.ok(back.some((r:any)=>r.source_scope==='group:778181'&&r.text==='GROUP_B_SIGNAL'));
  assert.deepEqual(calls[3].messages.slice(0,calls[0].messages.length),calls[0].messages,'previously submitted private wire must remain byte-exact');
  assert.equal(calls.length,4,'no extra decision or extraction request');
 }finally{await current.app.close();await new Promise<void>(r=>model.close(()=>r()))}
});
