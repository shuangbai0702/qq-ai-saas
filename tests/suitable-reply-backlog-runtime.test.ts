import {permitModelFixture,nativeMessages} from './helpers/model-fixture.js';
import {issueTestInvite} from './helpers/invitations.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {mkdtemp} from 'node:fs/promises';
import path from 'node:path';
import {createApp} from '../apps/control-api/src/app.js';
import {defaultConfig} from '../packages/contracts/index.js';
import {MockProtocolAdapter} from '../adapters/mock/index.js';

async function until(check:()=>Promise<boolean>,why:string){
 for(let i=0;i<300;i++){if(await check())return;await new Promise(r=>setTimeout(r,100))}throw Error(why);
}

test('a suitable reply reaches the real SDK protocol boundary despite separate unadopted backlog',{timeout:90000},async()=>{
 let release:()=>void=()=>{},entered=false,mainCalls=0,reviews=0;
 const model=http.createServer(async(req,res)=>{
  let raw='';for await(const chunk of req)raw+=chunk;const body=JSON.parse(raw);
  const system=body.messages.filter((m:any)=>m.role==='system').map((m:any)=>m.content).join('\n');
  let content:string;
  if(system.includes('你是群聊发送前复核器')){
   reviews++;const payload=JSON.parse(nativeMessages(body).at(-1).content);
   assert.ok(payload.review.latest_messages.some((m:any)=>m.text==='UNRELATED_NEW_INPUT'));
   content=JSON.stringify({action:'keep',reason:'still_suitable',responded_events:[],ignored_events:[]});
  }else{mainCalls++;entered=true;await new Promise<void>(r=>release=r);content='这是原问题的合适回答。'}
  res.setHeader('Content-Type','application/json');res.end(JSON.stringify({id:'suitable',model:body.model,choices:[{index:0,message:{role:'assistant',content},finish_reason:'stop'}],usage:{prompt_tokens:100,completion_tokens:10,total_tokens:110}}));
 });
 await new Promise<void>(r=>model.listen(0,'127.0.0.1',r));permitModelFixture(model);
 const root=await mkdtemp(path.resolve('.local/suitable-backlog-'));
 let current!:Awaited<ReturnType<typeof createApp>>;
 try{
  current=await createApp({root});
  const registered=await current.app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(current.db),email:'suitable@fixture.invalid',password:'fixture-suitable-123',name:'fixture'}}),cookies={fish_session:registered.cookies[0].value};
  const config={...defaultConfig,baseUrl:`http://127.0.0.1:${(model.address()as any).port}/v1`,model:'suitable-fixture',proactive:false,behavior:{...defaultConfig.behavior!,groupParticipationEnabled:false,followupSeconds:0,randomReplyProbability:0,quietStart:0,quietEnd:0,ownerContactEnabled:false,typingSimulation:false,typoEnabled:false,splitMessages:false,sendIntervalMs:0,burstDebounceMs:0,styleLearning:false,memorySummary:false,memeEnabled:false,fatigueEnabled:false}};
  const created=await current.app.inject({method:'POST',url:'/v1/bots',cookies,payload:{name:'suitable fixture',protocol:'mock',runtimeMode:'shared',expectedUin:'90008773',apiKey:'fixture-key',config}});
  assert.equal(created.statusCode,201,created.body);const id=created.json().id;
  await current.app.inject({method:'POST',url:`/v1/bots/${id}/actions`,cookies,payload:{action:'start',idempotencyKey:'start'}});
  await until(async()=>!!(await current.db.query('SELECT health FROM bots WHERE id=$1',[id]))[0].health?.protocol?.ready,'protocol ready');
  const ctx=await current.agent.context((await current.db.query('SELECT * FROM bots WHERE id=$1',[id]))[0]),mock=current.agent.protocols.mock as MockProtocolAdapter;
  await mock.call(ctx,'/transition',{state:'online',uin:'90008773'});
  await until(async()=>!!(await current.db.query('SELECT health FROM bots WHERE id=$1',[id]))[0].health?.ai?.ready,'AI ready');
  const python=path.resolve(`references/astrbot/.venv/${process.platform==='win32'?'Scripts/python.exe':'bin/python'}`);
  const state=async(sql:string)=>JSON.parse((await promisify(execFile)(python,['-B','-c','import sqlite3,sys,json;d=sqlite3.connect(sys.argv[1]);r=d.execute(sys.argv[2]).fetchall();d.commit();print(json.dumps(r))',path.join(root,'bots',id,'behavior.sqlite'),sql],{windowsHide:true})).stdout);
  await mock.call(ctx,'/message',{group:'778101',sender:'12345678',mention:true,messageId:83010,text:'ORIGINAL_QUESTION'});
  await until(async()=>entered,'model held');
  await state(`INSERT INTO pipeline_work_inputs(scope,event,subject,owner,payload,status,created) VALUES('group:778101','unadopted-backlog','12345678','83010','{"post_type":"message"}','owned',1)`);
  await mock.call(ctx,'/message',{group:'778101',sender:'22345678',mention:false,messageId:83011,text:'UNRELATED_NEW_INPUT'});
  await until(async()=>(await state("SELECT event FROM pipeline_events WHERE event='83011'")).length===1,'new input observed');
  release();
  await until(async()=>(await state("SELECT status FROM pipeline_events WHERE event='83010'")).some((r:any[])=>r[0]==='sent'),'suitable reply confirmed');
  const sent=(await mock.call(ctx,'/messages')as any[]).filter(x=>x.action==='send_group_msg');
  assert.equal(sent.length,1);assert.equal(sent[0].params.message.filter((x:any)=>x.type==='text').map((x:any)=>x.data.text).join(''),'这是原问题的合适回答。');
  assert.equal(mainCalls,1);assert.equal(reviews,1);
  assert.deepEqual(await state("SELECT attempted,confirmed FROM pipeline_events WHERE event='83010'"),[[1,1]]);
  assert.deepEqual(await state("SELECT seen,status FROM pipeline_work_inputs WHERE event='unadopted-backlog'"),[[0,'owned']],'separate unseen input has not falsely been marked processed');
 }finally{release();await current?.app.close();await new Promise<void>(r=>model.close(()=>r()))}
});
