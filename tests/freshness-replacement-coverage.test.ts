import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import {mkdtemp} from 'node:fs/promises';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import path from 'node:path';
import {createApp} from '../apps/control-api/src/app.js';
import {defaultConfig} from '../packages/contracts/index.js';
import {MockProtocolAdapter} from '../adapters/mock/index.js';
import {issueTestInvite} from './helpers/invitations.js';
import {permitModelFixture,platformContext,nativeMessages} from './helpers/model-fixture.js';

test('replacement coverage survives the unchanged final check and avoids a second main pass',{timeout:90000},async()=>{
 const gates=new Map<string,()=>void>(),mains:string[]=[],reviews:any[]=[],errors:unknown[]=[];
 const model=http.createServer(async(req,res)=>{
  try{
   let raw='';for await(const c of req)raw+=c;const body=JSON.parse(raw);
   let answer:string;
   if(body.messages.some((m:any)=>m.role==='system'&&String(m.content).includes('你是回复适宜性复核器'))){
    const payload=JSON.parse(nativeMessages(body).at(-1).content);reviews.push(payload);
    answer=JSON.stringify({action:'replace',text:'你刚才说：明天八点开始。',reason:'answer_new_question',
     responded_events:payload.context.scope==='private:12345001'?payload.changes.messages.filter((m:any)=>m.source_event==='86001').map((m:any)=>m.source_event):[]});
   }else{
    const scope=platformContext(body).identity.scope;mains.push(scope);
    if(mains.filter(s=>s===scope).length===1)await new Promise<void>(r=>gates.set(scope,r));
    answer='已经收到。';
   }
   res.setHeader('Content-Type','application/json');res.end(JSON.stringify({id:'coverage',model:body.model,choices:[{index:0,message:{role:'assistant',content:answer},finish_reason:'stop'}],usage:{prompt_tokens:1,completion_tokens:1,total_tokens:2}}));
  }catch(e){errors.push(e);console.error('MODEL_FIXTURE_ERROR',e);res.writeHead(500).end('{}')}
 });
 await new Promise<void>(r=>model.listen(0,'127.0.0.1',r));permitModelFixture(model,['coverage']);
 const root=await mkdtemp(path.resolve('.local/replacement-coverage-')),current=await createApp({root});
 const python=path.resolve('references/astrbot/.venv/'+(process.platform==='win32'?'Scripts/python.exe':'bin/python'));
 async function until(check:()=>Promise<boolean>,why:string){for(let i=0;i<250;i++){if(await check())return;await new Promise(r=>setTimeout(r,100))}throw Error(why)}
 try{
  const reg=await current.app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(current.db),email:'coverage@fixture.invalid',name:'coverage',password:'coverage-fixture-123'}});
  assert.equal(reg.statusCode,200,reg.body);const cookies={fish_session:reg.cookies[0].value};
  const config={...defaultConfig,baseUrl:`http://127.0.0.1:${(model.address()as any).port}/v1`,model:'coverage',proactive:false,
   behavior:{...defaultConfig.behavior!,ownerContactEnabled:false,groupParticipationEnabled:false,naturalConversation:true,followupSeconds:0,
    typingSimulation:false,typoEnabled:false,splitMessages:false,burstDebounceMs:0,styleLearning:false,memorySummary:false,memeEnabled:false,fatigueEnabled:false}};
  const created=await current.app.inject({method:'POST',url:'/v1/bots',cookies,payload:{name:'coverage',protocol:'mock',runtimeMode:'shared',expectedUin:'90008875',apiKey:'fixture',config}});
  assert.equal(created.statusCode,201,created.body);const id=created.json().id;
  const state=async(sql:string)=>JSON.parse((await promisify(execFile)(python,['-B','-c','import sqlite3,sys,json;d=sqlite3.connect(sys.argv[1]);print(json.dumps(d.execute(sys.argv[2]).fetchall()))',path.join(root,'bots',id,'behavior.sqlite'),sql],{windowsHide:true})).stdout);
  await current.app.inject({method:'POST',url:`/v1/bots/${id}/actions`,cookies,payload:{action:'start',idempotencyKey:'start'}});
  await until(async()=>!!(await current.db.query('SELECT health FROM bots WHERE id=$1',[id]))[0].health?.protocol?.ready,'protocol');
  const ctx=await current.agent.context((await current.db.query('SELECT * FROM bots WHERE id=$1',[id]))[0]),mock=current.agent.protocols.mock as MockProtocolAdapter;
  await mock.call(ctx,'/transition',{state:'online',uin:'90008875'});
  await until(async()=>!!(await mock.call(ctx,'/state')as any).connected,'connection');
  await until(async()=>!!(await current.db.query('SELECT health FROM bots WHERE id=$1',[id]))[0].health?.ai?.ready,'AI');
  for(const [index,sender] of ['12345001','12345002'].entries()){
   const scope='private:'+sender,base=86000+index*10;
   await mock.call(ctx,'/message',{sender,messageId:base,text:'[QQ语音] 明天八点开始。'});
   await until(async()=>gates.has(scope),'main held');
   await mock.call(ctx,'/message',{sender,messageId:base+1,text:'我刚刚说了啥'});
   await until(async()=>(await state(`SELECT count(*) FROM pipeline_work_inputs WHERE event='${base+1}' AND owner='${base}'`))[0][0]===1,'new input owned');
   gates.get(scope)!();gates.delete(scope);
   await until(async()=>(await state(`SELECT passes,status FROM pipeline_events WHERE event='${base}'`))[0]?.[1]==='sent'&&(await state(`SELECT count(*) FROM pipeline_scope_work WHERE scope='${scope}'`))[0][0]===0,'settled');
   assert.deepEqual(errors,[]);
   assert.equal(mains.filter(s=>s===scope).length,index===0?1:2,'only explicit confirmed coverage suppresses continuation');
   assert.equal(reviews.filter(r=>r.context.scope===scope).length,1);
   assert.deepEqual(await state(`SELECT seen,status FROM pipeline_work_inputs WHERE event='${base+1}'`),[[index===0?2:1,'settled']]);
  }
  assert.deepEqual(errors,[]);
 }finally{for(const r of gates.values())r();await current.app.close();await new Promise<void>(r=>model.close(()=>r()))}
});
