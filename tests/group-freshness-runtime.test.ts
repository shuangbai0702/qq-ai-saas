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

const sleep=(ms:number)=>new Promise(r=>setTimeout(r,ms));
async function until(check:()=>Promise<boolean>,why:string){for(let i=0;i<300;i++){if(await check())return;await sleep(100)}throw Error('Group freshness: '+why)}

test('native group preflight keeps, replaces and drops delayed drafts with true receipts and matching history',{timeout:120000},async()=>{
 const waiting=new Map<string,()=>void>(),reviews:any[]=[],replyRequests:any[]=[];
 const model=http.createServer(async(req,res)=>{
  let raw='';for await(const chunk of req)raw+=chunk;const body=JSON.parse(raw);
  const system=body.messages.filter((m:any)=>m.role==='system').map((m:any)=>m.content).join('\n');
  let answer:string;
  if(system.includes('你是回复适宜性复核器')){
   const payload=JSON.parse(nativeMessages(body).at(-1).content);reviews.push({body,payload});
   const latest=JSON.stringify(payload.review.latest_messages);
   answer=latest.includes('LATEST_FALLBACK')?'invalid JSON':JSON.stringify(latest.includes('LATEST_REPLACE')?{action:'replace',text:'结合刚才的补充回答。',reason:'updated'}:latest.includes('LATEST_DROP')?{action:'drop',reason:'already_resolved'}:{action:'keep',reason:'unrelated'});
  }else{
   const input=JSON.stringify(nativeMessages(body).at(-1));
   const label=input.match(/CASE_(REPLACE|KEEP|DROP|FALLBACK|PARTIAL|HOTSEED|HOTBATCH)/)?.[1];
   if(!label)throw Error('Unexpected model request');
   replyRequests.push({label,body});
   await new Promise<void>(resolve=>waiting.set(label,resolve));
   answer=label==='PARTIAL'?'前半段。[QQ_BREAK]不应发送的后半段。':label==='HOTBATCH'?'合起来接这几句。[QQ_BREAK]再补一句。':'原始回复：CASE_'+label+'。';
  }
  res.setHeader('Content-Type','application/json');res.end(JSON.stringify({id:'freshness',model:body.model,choices:[{index:0,message:{role:'assistant',content:answer},finish_reason:'stop'}],usage:{prompt_tokens:100,completion_tokens:10,total_tokens:110}}));
 });
 await new Promise<void>(r=>model.listen(0,'127.0.0.1',r));permitModelFixture(model);
 const root=await mkdtemp(path.resolve('.local/group-freshness-'));
 let current!:Awaited<ReturnType<typeof createApp>>;
 const python=path.resolve(`references/astrbot/.venv/${process.platform==='win32'?'Scripts/python.exe':'bin/python'}`);
 try{
  current=await createApp({root});
  const registered=await current.app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(current.db),email:'freshness@fixture.invalid',password:'freshness-fixture-123',name:'群发送复核验收'}}),cookies={fish_session:registered.cookies[0].value};
  const config={...defaultConfig,baseUrl:`http://127.0.0.1:${(model.address()as any).port}/v1`,model:'freshness-fixture',proactive:false,behavior:{...defaultConfig.behavior!,groupParticipationEnabled:false,groupParticipationGroups:['778100','778101','778102','778103','778104','778105'],followupSeconds:0,randomReplyProbability:0,quietStart:0,quietEnd:0,ownerContactEnabled:false,typingSimulation:false,typoEnabled:false,splitMessages:true,sendIntervalMs:1200,burstDebounceMs:0,styleLearning:false,memorySummary:false,memeEnabled:false,fatigueEnabled:false}};
  const created=await current.app.inject({method:'POST',url:'/v1/bots',cookies,payload:{name:'群发送复核验收',protocol:'mock',runtimeMode:'shared',expectedUin:'90008773',apiKey:'freshness-fixture-key',config}});assert.equal(created.statusCode,201,created.body);const id=created.json().id;
  await current.app.inject({method:'POST',url:`/v1/bots/${id}/actions`,cookies,payload:{action:'start',idempotencyKey:'start'}});
  await until(async()=>!!(await current.db.query('SELECT health FROM bots WHERE id=$1',[id]))[0].health?.protocol?.ready,'protocol ready');
  const ctx=await current.agent.context((await current.db.query('SELECT * FROM bots WHERE id=$1',[id]))[0]),mock=current.agent.protocols.mock as MockProtocolAdapter;
  await mock.call(ctx,'/transition',{state:'online',uin:'90008773'});await until(async()=>!!(await mock.call(ctx,'/state')as any).connected,'QQ connected');
  await until(async()=>!!(await current.db.query('SELECT health FROM bots WHERE id=$1',[id]))[0].health?.ai?.ready,'AI ready');
  const sends=async()=>(await mock.call(ctx,'/messages')as any[]).filter(x=>x.action==='send_group_msg');
  const state=async(sql:string)=>JSON.parse((await promisify(execFile)(python,['-B','-c','import sqlite3,sys,json;d=sqlite3.connect(sys.argv[1]);print(json.dumps(d.execute(sys.argv[2]).fetchall()))',path.join(root,'bots',id,'behavior.sqlite'),sql],{windowsHide:true})).stdout);
  for(const [index,label] of ['REPLACE','KEEP','DROP','FALLBACK'].entries()){
   const group=String(778100+index),event=83000+index*10;
   await mock.call(ctx,'/message',{group,sender:'12345678',mention:true,messageId:event,text:'CASE_'+label});
   await until(async()=>waiting.has(label),'original draft held at '+label);
   await mock.call(ctx,'/message',{group,sender:'22345678',mention:false,messageId:event+1,text:'LATEST_'+label});
   await until(async()=>(await state(`SELECT status FROM pipeline_events WHERE event='${event+1}'`)).length===1,'new message observed before releasing draft');
   waiting.get(label)!();
   await until(async()=>(await state(`SELECT status FROM pipeline_events WHERE event='${event}'`)).some((r:any[])=>['sent','superseded'].includes(r[0])),'original Work settled');
  }
  const sent=await sends();assert.equal(sent.length,3,'drop emits no protocol effect; unavailable checker preserves a quoted real answer');
  const text=(p:any)=>p.params.message.filter((c:any)=>c.type==='text').map((c:any)=>c.data.text).join('');
  assert.equal(text(sent[0]),'结合刚才的补充回答。');assert.equal(text(sent[1]),'原始回复：CASE_KEEP。');
  assert.equal(text(sent[2]),'原始回复：CASE_FALLBACK。');assert.ok(sent[2].params.message.some((c:any)=>c.type==='reply'&&String(c.data.id)==='83030'),'fallback quotes the original trusted protocol message');
  assert.equal(reviews.length,4);assert.ok(reviews.every(r=>!r.body.tools?.length),'review cannot invoke tools');
  assert.ok(reviews.every(r=>r.payload.review.latest_messages.every((m:any)=>m.sender_id==='22345678')),'group identities retained');
  await mock.call(ctx,'/message',{group:'778104',sender:'12345678',mention:true,messageId:83040,text:'CASE_PARTIAL'});
  await until(async()=>waiting.has('PARTIAL'),'partial draft held');waiting.get('PARTIAL')!();
  await until(async()=>(await sends()).length===4,'first partial bubble really delivered');
  assert.equal(reviews.length,4,'unchanged group adds no model check');
  await mock.call(ctx,'/message',{group:'778104',sender:'22345678',mention:false,messageId:83041,text:'LATEST_DROP'});
  await until(async()=>(await state("SELECT status FROM pipeline_events WHERE event='83040'")).some((r:any[])=>r[0]==='partial'),'remaining bubble stopped after a new message');
  assert.equal((await sends()).length,4,'confirmed first bubble is not resent and stale tail never sent');
  assert.equal(reviews.length,5);assert.equal(reviews[4].payload.review.already_sent,'前半段。');
  const [binding]=await current.db.query('SELECT group_id FROM bots WHERE id=$1',[id]);
  const native=path.join(root,'bots',binding.group_id||id,'ai','data','data_v4.db');
  const histories=JSON.parse((await promisify(execFile)(python,['-B','-c',`import sqlite3,sys,json;d=sqlite3.connect(sys.argv[1]);print(json.dumps([json.loads(r[0]) for r in d.execute('SELECT content FROM conversations')]))`,native],{windowsHide:true})).stdout);
  const assistant=histories.flat().filter((m:any)=>m.role==='assistant').map((m:any)=>JSON.stringify(m.content)).join('\n');
  assert.ok(assistant.includes('结合刚才的补充回答'),'native Agent history keeps actual replacement');
  assert.ok(!assistant.includes('原始回复：CASE_REPLACE')&&!assistant.includes('原始回复：CASE_DROP'),'unsent drafts never become Agent history');
  assert.ok(assistant.includes('前半段。')&&!assistant.includes('不应发送的后半段'),'partial history records only confirmed content');
  const detail=(await current.app.inject({url:`/v1/bots/${id}`,cookies})).json();
  const applied=await current.app.inject({method:'PUT',url:`/v1/bots/${id}/config`,cookies,payload:{expectedRevision:detail.bot.revision,name:detail.bot.name,expectedUin:detail.bot.expected_uin,config:{...config,behavior:{...config.behavior,followupSeconds:120}}}});
  assert.equal(applied.statusCode,200,applied.body);
  await until(async()=>(await current!.db.query('SELECT applied_revision FROM bots WHERE id=$1',[id]))[0].applied_revision===applied.json().revision,'hot-followup setting applied');
  await until(async()=>!!(await mock.call(ctx,'/state')as any).connected,'fixture reconnected after config');
  await mock.call(ctx,'/message',{group:'778105',sender:'12345678',mention:true,messageId:83100,text:'CASE_HOTSEED'});
  await until(async()=>waiting.has('HOTSEED'),'hot conversation seeded');waiting.get('HOTSEED')!();
  await until(async()=>(await sends()).length===5,'seed response confirmed');
  const began=performance.now();
  for(const [i,sender] of ['12345678','22345678','32345678'].entries())await mock.call(ctx,'/message',{group:'778105',sender,mention:false,messageId:83101+i,text:i===2?'CASE_HOTBATCH':'HOT_INPUT_'+i});
  await until(async()=>waiting.has('HOTBATCH'),'fixed hot window reaches model');
  assert.ok(performance.now()-began>=4500,'hot followup waits five seconds from the first message');
  const request=replyRequests.find(x=>x.label==='HOTBATCH');
  assert.ok(['HOT_INPUT_0','HOT_INPUT_1','CASE_HOTBATCH'].every(x=>JSON.stringify(request.body).includes(x)),'all three real messages enter one model request');
  assert.ok(JSON.stringify(request.body).includes('不随人数相乘'),'one total response contract reaches the model');
  waiting.get('HOTBATCH')!();
  await until(async()=>(await state("SELECT status FROM pipeline_events WHERE event='83103'")).some((r:any[])=>r[0]==='sent'),'merged Work sent');
  assert.equal((await sends()).length,7,'three people receive two bubbles total, not three times two');
  assert.equal(replyRequests.filter(x=>x.label==='HOTBATCH').length,1);
  assert.deepEqual(await state("SELECT status FROM pipeline_events WHERE event IN ('83101','83102','83103') ORDER BY event"),[['combined'],['combined'],['sent']]);
 }finally{for(const release of waiting.values())release();await current?.app.close();await new Promise<void>(r=>model.close(()=>r()))}
});
