import {TaskHandoffSchema} from '../packages/contracts/autonomous.js';
import {permitModelFixture,platformContext,nativeMessages} from './helpers/model-fixture.js';
import {issueTestInvite} from './helpers/invitations.js';
import test from 'node:test';import assert from 'node:assert/strict';import http from 'node:http';
import {mkdtemp} from 'node:fs/promises';import {execFile} from 'node:child_process';import {promisify} from 'node:util';import path from 'node:path';
import {createApp} from '../apps/control-api/src/app.js';import {defaultConfig} from '../packages/contracts/index.js';import {MockProtocolAdapter} from '../adapters/mock/index.js';
const sleep=(ms:number)=>new Promise(r=>setTimeout(r,ms));
async function until(check:()=>Promise<boolean>,why:string){for(let i=0;i<300;i++){if(await check())return;await sleep(100)}throw Error(why)}

test('real SDK queues adjacent Work through confirmed settlement and skips only explicitly covered events',{timeout:120000},async()=>{
 const waiting=new Map<string,()=>void>(),requests:any[]=[],reviews:any[]=[];let releaseEmbedding:(()=>void)|undefined;
 const model=http.createServer(async(req,res)=>{
  let raw='';for await(const b of req)raw+=b;const body=JSON.parse(raw);let answer;
  if(req.url?.endsWith('/embeddings')){
   await new Promise<void>(r=>{releaseEmbedding=r});res.setHeader('Content-Type','application/json');
   res.end(JSON.stringify({data:body.input.map((_:any,index:number)=>({index,embedding:[1,0]})),usage:{prompt_tokens:1,total_tokens:1}}));return;
  }
  if(body.messages.some((m:any)=>m.role==='system'&&m.content.includes('你是回复适宜性复核器'))){
   const data=JSON.parse(nativeMessages(body).at(-1).content);reviews.push(data);
   answer=JSON.stringify({action:'keep',responded_events:data.review.latest_messages.filter((m:any)=>m.text.includes('EXTRA_A')).map((m:any)=>m.event)});
  }else{
   const data=platformContext(body),text=JSON.stringify(nativeMessages(body).at(-1));
   const label=text.includes('FIRST_A')?'FIRST_A':text.includes('OTHER_C')?'OTHER_C':'UNEXPECTED_B';
   requests.push({label,body,data});
   if(label==='FIRST_A')await new Promise<void>(r=>waiting.set(label,r));
   answer=label==='FIRST_A'?'已回答甲的原问题和补充。':'这是乙的独立答案。';
  }
  res.setHeader('Content-Type','application/json');res.end(JSON.stringify({id:'coordination',model:body.model,choices:[{index:0,message:{role:'assistant',content:answer},finish_reason:'stop'}]}));
 });await new Promise<void>(r=>model.listen(0,'127.0.0.1',r));permitModelFixture(model);
 const root=await mkdtemp(path.resolve('.local/group-coordination-')),current=await createApp({root});
 try{
  const auth=await current.app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(current.db),email:'coordination@test.invalid',name:'coordination',password:'coordination-123'}}),cookies={fish_session:auth.cookies[0].value};
  const config={...defaultConfig,baseUrl:`http://127.0.0.1:${(model.address()as any).port}/v1`,model:'coordination',proactive:false,behavior:{...defaultConfig.behavior!,ownerUin:'12345678',groupParticipationEnabled:false,groupParticipationGroups:['778180','778181'],naturalConversation:true,followupSeconds:0,followupReplyProbability:0,typingSimulation:false,typoEnabled:false,splitMessages:false,burstDebounceMs:0,styleLearning:false,memorySummary:false,memeEnabled:false,fatigueEnabled:false}};
  const created=await current.app.inject({method:'POST',url:'/v1/bots',cookies,payload:{name:'协调验收',protocol:'mock',runtimeMode:'shared',expectedUin:'90008873',apiKey:'coordination-fixture',config}});assert.equal(created.statusCode,201,created.body);const id=created.json().id;
  await current.app.inject({method:'POST',url:`/v1/bots/${id}/actions`,cookies,payload:{action:'start',idempotencyKey:'start'}});
  await until(async()=>!!(await current.db.query('SELECT health FROM bots WHERE id=$1',[id]))[0].health?.protocol?.ready,'protocol ready');
  const ctx=await current.agent.context((await current.db.query('SELECT * FROM bots WHERE id=$1',[id]))[0]),mock=current.agent.protocols.mock as MockProtocolAdapter;
  await mock.call(ctx,'/transition',{state:'online',uin:'90008873'});await until(async()=>!!(await mock.call(ctx,'/state')as any).connected,'QQ connected');
  await until(async()=>!!(await current.db.query('SELECT health FROM bots WHERE id=$1',[id]))[0].health?.ai?.ready,'AI ready');
  const state=async(sql:string)=>JSON.parse((await promisify(execFile)(process.env.FISHBOT_PYTHON||'python',['-B','-c','import sqlite3,sys,json;d=sqlite3.connect(sys.argv[1]);print(json.dumps(d.execute(sys.argv[2]).fetchall()))',path.join(root,'bots',id,'behavior.sqlite'),sql],{windowsHide:true})).stdout);
  await mock.call(ctx,'/message',{group:'778180',sender:'12345678',mention:true,messageId:84000,text:'FIRST_A'});
  await until(async()=>waiting.has('FIRST_A'),'first generation held');
  await mock.call(ctx,'/message',{group:'778180',sender:'12345678',mention:true,messageId:84001,text:'EXTRA_A'});
  await mock.call(ctx,'/message',{group:'778180',sender:'87654321',mention:true,messageId:84002,text:'OTHER_C'});
  await until(async()=>(await state("SELECT count(*) FROM pipeline_events WHERE event IN ('84001','84002')"))[0][0]===2,'both arrivals observed');
  assert.equal(requests.length,1,'same Scope cannot run a competing main generation');waiting.get('FIRST_A')!();
  await until(async()=>(await state("SELECT status FROM pipeline_events WHERE event='84002'"))[0]?.[0]==='sent','independent question sent');
  assert.deepEqual(requests.map(x=>x.label),['FIRST_A','OTHER_C']);
  assert.deepEqual(await state("SELECT status,reason FROM pipeline_events WHERE event='84001'"),[['silent','confirmed_event_coverage']]);
  const timeline=requests[1].data.conversation_timeline;
  assert.ok(timeline.some((x:any)=>x.role==='self'&&x.text.includes('已回答甲')&&x.recipient_id==='12345678'&&x.receipt==='confirmed'&&x.message_ids.length),'queued Work sees reply confirmed after its own ingress');
  assert.ok(reviews[0].context.conversation.timeline.some((x:any)=>x.sender_id==='87654321'&&x.source_event==='84002'),'review sees bounded authoritative multi-person timeline');
  assert.equal((await mock.call(ctx,'/messages')as any[]).filter(x=>x.action==='send_group_msg').length,2,'independent question survives coverage of another sender event');
  // A new event during context assembly must not be hidden by a later cursor
  // snapshot. Hold the real opt-in meme embedding HTTP request before main
  // generation. Ordinary fact/summary embedding lookup is retired; the
  // remaining semantic catalog path supplies the same genuine assembly await.
  const detail=(await current.app.inject({url:`/v1/bots/${id}`,cookies})).json();
  const changed=await current.app.inject({method:'PUT',url:`/v1/bots/${id}/config`,cookies,payload:{expectedRevision:detail.bot.revision,name:detail.bot.name,expectedUin:detail.bot.expected_uin,config:{...config,behavior:{...config.behavior,memeEnabled:true,memeSemantic:true,memePacks:[],embeddingModel:'fixture-embedding',embeddingBaseUrl:config.baseUrl}}}});
  assert.equal(changed.statusCode,200,changed.body);
  await until(async()=>(await current.db.query('SELECT applied_revision FROM bots WHERE id=$1',[id]))[0].applied_revision===changed.json().revision,'embedding config ready');
  await until(async()=>!!(await mock.call(ctx,'/state')as any).connected,'reconnected fixture');
  await promisify(execFile)(process.env.FISHBOT_PYTHON||'python',['-B','-c',"import sqlite3,sys,time;d=sqlite3.connect(sys.argv[1]);d.execute('INSERT INTO sticker_inbox VALUES(?,?,?,?,?,?,?,1,?)',('coordination-fixture','group:778181','12345678','fixture','approved','真实离线表情夹具',time.time(),b'png'));d.commit()",path.join(root,'bots',id,'behavior.sqlite')],{windowsHide:true});
  waiting.delete('FIRST_A');
  await mock.call(ctx,'/message',{group:'778181',sender:'12345678',mention:true,messageId:84100,text:'FIRST_A'});
  await until(async()=>!!releaseEmbedding,'embedding holds context assembly');
  await mock.call(ctx,'/message',{group:'778181',sender:'12345678',mention:true,messageId:84101,text:'EXTRA_A'});
  await until(async()=>(await state("SELECT count(*) FROM pipeline_events WHERE event='84101'"))[0][0]===1,'arrival during embedding observed');
  releaseEmbedding!();await until(async()=>waiting.has('FIRST_A'),'main generation starts after assembly');waiting.get('FIRST_A')!();
  await until(async()=>(await state("SELECT reason FROM pipeline_events WHERE event='84101'"))[0]?.[0]==='confirmed_event_coverage','assembly-time arrival is reviewed and covered');
  assert.deepEqual(requests.map(x=>x.label),['FIRST_A','OTHER_C','FIRST_A']);
  assert.equal(reviews.length,2,'arrival during assembly adds exactly one necessary review');
 }finally{releaseEmbedding?.();for(const release of waiting.values())release();await current.app.close();await new Promise<void>(r=>model.close(()=>r()))}
});

test('atomic model acceptance emits the existing strict Node handoff contract',async()=>{
 const code=`import sys,tempfile,json,time
from pathlib import Path
sys.path.insert(0,'adapters/astrbot')
from behavior import BehaviorStore
from bot_pipeline import Work
import agreement_state
with tempfile.TemporaryDirectory() as folder:
 st=BehaviorStore(Path(folder)/'behavior.sqlite');db=st.db
 identity={'tenantId':'tenant','botId':'bot','generation':1,'revision':1,'epoch':1}
 scope='private:12345678';who='12345678'
 w=Work('bot',scope,who,'99999999','source','message',time.time(),identity,ledger_owner=True)
 row,error=agreement_state.apply_update(db,'propose','remind at 18',who,[who,'bot'],scope,'source');db.commit()
 st.pipeline.propose_handoff(w,identity,{'objective':'requested reminder','delaySeconds':300,'kind':'message','agreement':{'id':row['id'],'revision':1},'acceptAgreement':True})
 print(json.dumps(st.pipeline.pending_handoffs(1)[0]));st.close()
`;
 const r=await promisify(execFile)('python',['-B','-c',code],{windowsHide:true});
 const handoff=TaskHandoffSchema.parse(JSON.parse(r.stdout));assert.equal(handoff.agreement?.revision,2);assert.equal(handoff.kind,'message');assert.equal(handoff.senderId,'12345678');
});
