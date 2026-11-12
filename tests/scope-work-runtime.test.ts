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

const pause=(ms:number)=>new Promise(r=>setTimeout(r,ms));
async function until(check:()=>Promise<boolean>,why:string){
 for(let i=0;i<300;i++){if(await check())return;await pause(100)}
 throw Error(why);
}
function textOf(message:any):string{
 return typeof message.content==='string'?message.content:(message.content||[]).filter((p:any)=>p.type==='text').map((p:any)=>p.text).join('\n');
}
function sourced(body:any){
 return nativeMessages(body).flatMap((message:any)=>{
  const text=textOf(message),marker='[QQ消息来源]';
  if(message.role!=='user'||!text.startsWith(marker))return [];
  const line=text.indexOf('\n');
  return [{...JSON.parse(text.slice(marker.length,line)),text:text.slice(line+1)}];
 });
}

test('real SDK keeps one active Work per Scope, absorbs bursts and hands off only unseen inputs after four main passes',{timeout:180000},async()=>{
 const main:any[]=[],reviews:any[]=[],gates=new Map<string,()=>void>(),errors:unknown[]=[];
 const model=http.createServer(async(req,res)=>{
  try{
   req.setEncoding('utf8'); // Preserve multibyte Schema text across TCP chunks.
   let raw='';for await(const chunk of req)raw+=chunk;const body=JSON.parse(raw);
   let answer:string;
   if(body.messages.some((m:any)=>m.role==='system'&&textOf(m).includes('你是回复适宜性复核器'))){
    const payload=JSON.parse(textOf(nativeMessages(body).at(-1)));reviews.push({body,payload});
    // Dropping an obsolete draft must preserve each independent new question:
    // these deliberately have no semantic coverage claim and need a main pass.
    answer=JSON.stringify(payload.context.scope==='private:12345001'
     ?{action:'keep',reason:'draft_valid_but_new_question_unhandled',responded_events:[]}
     :payload.context.scope==='private:12345002'
     ?{action:'replace',text:'已收到补充，接下来一起回答。',reason:'fixture_needs_current_questions',responded_events:[]}
     :payload.context.scope==='group:778190'&&payload.review.latest_messages.every((x:any)=>main.filter(m=>m.scope===payload.context.scope).at(-1)?.inputs.some((i:any)=>i.event_id===x.event))
     ?{action:'keep',reason:'current_main_covers_group_inputs',responded_events:[]}
     :{action:'drop',reason:'fixture_needs_current_questions',responded_events:[]});
   }else{
    const data=platformContext(body),scope=data.identity.scope;
    const index=main.filter(x=>x.scope===scope).length+1,key=scope+':'+index;
    const inputs=sourced(body).filter((x:any)=>x.scope===scope);
    main.push({scope,index,body,data,inputs});
    await new Promise<void>(r=>gates.set(key,r));
    // Compact identifiers keep the 100-input case below ordinary public
    // reply limits; this suite tests scope ownership, not long-text rendering.
    answer='完整回答：'+inputs.map((x:any)=>{
     const tag=x.text.match(/SCOPE_CASE_[A-Z0-9_]+/)?.[0];
     return tag?.match(/^SCOPE_CASE_P\d+_(\d+)$/)?.[1]||tag;
    }).filter(Boolean).join('，');
   }
   res.setHeader('Content-Type','application/json');
   res.end(JSON.stringify({id:'scope-work',model:body.model,choices:[{index:0,message:{role:'assistant',content:answer},finish_reason:'stop'}],usage:{prompt_tokens:1,completion_tokens:1,total_tokens:2}}));
  }catch(error){errors.push(error);res.writeHead(500).end(JSON.stringify({error:'fixture failed'}))}
 });
 await new Promise<void>(r=>model.listen(0,'127.0.0.1',r));permitModelFixture(model);
 const root=await mkdtemp(path.resolve('.local/scope-work-')),current=await createApp({root});
 try{
  const registered=await current.app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(current.db),email:'scope-work@test.invalid',name:'scope-work',password:'scope-work-fixture-123'}});
  assert.equal(registered.statusCode,200,registered.body);
  const cookies={fish_session:registered.cookies[0].value};
  const config={...defaultConfig,baseUrl:`http://127.0.0.1:${(model.address()as any).port}/v1`,model:'scope-work-fixture',proactive:false,
   behavior:{...defaultConfig.behavior!,ownerUin:'12345678',ownerContactEnabled:false,groupParticipationEnabled:false,groupParticipationGroups:['778190'],naturalConversation:true,
    followupSeconds:0,followupReplyProbability:0,typingSimulation:false,typoEnabled:false,splitMessages:false,burstDebounceMs:0,
    styleLearning:false,memorySummary:false,memeEnabled:false,fatigueEnabled:false}};
  const created=await current.app.inject({method:'POST',url:'/v1/bots',cookies,payload:{name:'Scope单Work验收',protocol:'mock',runtimeMode:'shared',expectedUin:'90008874',apiKey:'scope-work-fixture',config}});
  assert.equal(created.statusCode,201,created.body);const id=created.json().id;
  // Shared process and unified Journal are distinct. Authorize an empty,
  // never-started test store before launching the actual SDK worker.
  await promisify(execFile)(process.env.FISHBOT_PYTHON||'python',['-B','-c',
   "import sys,json;sys.path.insert(0,'adapters/astrbot');from behavior import BehaviorStore;from shared_context import activate_authorized;s=BehaviorStore(sys.argv[1]);activate_authorized(s.db,json.loads(sys.argv[2]),{},'fixture-scope-work-shared','a'*40);s.close()",
   path.join(root,'bots',id,'behavior.sqlite'),JSON.stringify(config)],{windowsHide:true});
  await current.app.inject({method:'POST',url:`/v1/bots/${id}/actions`,cookies,payload:{action:'start',idempotencyKey:'start'}});
  await until(async()=>!!(await current.db.query('SELECT health FROM bots WHERE id=$1',[id]))[0].health?.protocol?.ready,'protocol ready');
  const ctx=await current.agent.context((await current.db.query('SELECT * FROM bots WHERE id=$1',[id]))[0]),mock=current.agent.protocols.mock as MockProtocolAdapter;
  await mock.call(ctx,'/transition',{state:'online',uin:'90008874'});
  await until(async()=>!!(await mock.call(ctx,'/state')as any).connected,'QQ connected');
  await until(async()=>!!(await current.db.query('SELECT health FROM bots WHERE id=$1',[id]))[0].health?.ai?.ready,'AI ready');
  const state=async(sql:string)=>JSON.parse((await promisify(execFile)(process.env.FISHBOT_PYTHON||'python',['-B','-c',
   'import sqlite3,sys,json;d=sqlite3.connect(sys.argv[1]);print(json.dumps(d.execute(sys.argv[2]).fetchall()))',path.join(root,'bots',id,'behavior.sqlite'),sql],{windowsHide:true})).stdout);
  assert.deepEqual(await state("SELECT state FROM fishbot_shared_context WHERE id=1"),[['active']]);
  const send=async(scope:string,event:number,label:string,sender?:string)=>{
   await mock.call(ctx,'/message',{...(scope.startsWith('group:')?{group:scope.slice(6)}:{}),sender:sender||scope.slice(8),mention:true,messageId:event,text:label});
  };
  const held=async(scope:string,index:number)=>until(async()=>gates.has(scope+':'+index),'main held '+scope+':'+index);
  const release=(scope:string,index:number)=>{const key=scope+':'+index;assert.ok(gates.has(key),'held main '+key);gates.get(key)!();gates.delete(key)};
  const mainFor=(scope:string)=>main.filter(x=>x.scope===scope);
  const reviewFor=(scope:string)=>reviews.filter(x=>x.payload.context.scope===scope);
  const finished=async(scope:string,event:number)=>until(async()=>{
   const events=await state(`SELECT status FROM pipeline_events WHERE scope='${scope}' AND event='${event}'`);
   return events[0]?.[0]==='sent'&&(await state(`SELECT count(*) FROM pipeline_scope_work WHERE scope='${scope}'`))[0][0]===0;
  },'Work settled '+event);
  const messages=async()=>(await mock.call(ctx,'/messages')as any[]).filter(x=>['send_private_msg','send_group_msg'].includes(x.action));
  const sentText=(message:any)=>message.params.message.filter((x:any)=>x.type==='text').map((x:any)=>x.data.text).join('');

  // All arrivals while main #1 is held belong to the same original Work.
  // The checker sees at most its bounded window; main #2 must still receive
  // every independent question, including the events outside that window.
  for(const [caseIndex,count]of [2,10,100].entries()){
   const scope='private:'+String(12345001+caseIndex),base=85000+caseIndex*200;
   const labels=Array.from({length:count},(_,i)=>`SCOPE_CASE_P${count}_${i}`);
   await send(scope,base,labels[0]);await held(scope,1);
   for(let i=1;i<count;i++)await send(scope,base+i,labels[i]);
   await until(async()=>(await state(`SELECT count(*) FROM pipeline_work_inputs WHERE scope='${scope}' AND owner='${base}' AND event<>'${base}' AND status='owned'`))[0][0]===count-1,'all burst events durably joined');
   assert.equal(mainFor(scope).length,1,'no competing same-Scope model task');
   assert.deepEqual(await state(`SELECT event FROM pipeline_events WHERE scope='${scope}'`),[[String(base)]],'joined inputs have no second Work');
   release(scope,1);await held(scope,2);
   assert.deepEqual(await state(`SELECT event FROM pipeline_scope_work WHERE scope='${scope}'`),[[String(base)]],'second main retains original Work owner');
   const second=mainFor(scope)[1];
   for(let i=0;i<count;i++)assert.ok(second.inputs.some((x:any)=>String(x.event_id)===String(base+i)&&x.text.includes(labels[i])),'main sees independent question '+labels[i]);
   release(scope,2);await finished(scope,base);
   assert.equal(mainFor(scope).length,2,'one continuation absorbs the whole burst');
   assert.equal(reviewFor(scope).length,1,'events already in main #2 do not invoke Freshness again');
   assert.deepEqual(await state(`SELECT status,seen,count(*) FROM pipeline_work_inputs WHERE scope='${scope}' AND event<>'${base}' GROUP BY status,seen`),[['settled',1,count-1]],'all joined events settle after actual main inclusion');
   const sent=(await messages()).filter(x=>String(x.params.user_id)===scope.slice(8));
   assert.equal(sent.length,count===100?1:2,'keep/replace sends its suitable draft, while drop emits nothing; independent questions still reach main');
   if(count===2)assert.equal(sentText(sent[0]),'完整回答：0','keep preserves the original suitable reply');
   if(count===10)assert.equal(sentText(sent[0]),'已收到补充，接下来一起回答。','replace sends the reviewed draft without another rewrite request');
   assert.equal(sentText(sent.at(-1)),'完整回答：'+Array.from({length:count},(_,i)=>String(i)).join('，'),'final response retains every independent question index');
  }

  // Three group members are one Scope, whereas another person's private
  // conversation keeps a separate owner even though the Journal is shared.
  const group='group:778190',privateScope='private:12345004',base=86000;
  await send(group,base,'SCOPE_CASE_GROUP_A','12345678');await held(group,1);
  await send(group,base+1,'SCOPE_CASE_GROUP_B','87654321');
  await send(group,base+2,'SCOPE_CASE_GROUP_C','22345678');
  await until(async()=>(await state(`SELECT count(*) FROM pipeline_work_inputs WHERE scope='${group}' AND event<>'${base}'`))[0][0]===2,'group arrivals durably joined');
  assert.deepEqual(await state(`SELECT subject,owner FROM pipeline_work_inputs WHERE scope='${group}' AND event<>'${base}' ORDER BY event`),[['87654321',String(base)],['22345678',String(base)]]);
  release(group,1);await held(group,2);
  // Force the unrelated private observation AFTER the group request snapshot.
  // This is a real state-version change during generation, not a timing race
  // over which writer happened to obtain the lock first.
  await send(privateScope,base+1,'SCOPE_CASE_OTHER_PRIVATE');
  await until(async()=>(await state(`SELECT count(*) FROM pipeline_events WHERE scope='${privateScope}'`))[0][0]===1,'private scope independently admitted');
  assert.deepEqual(await state(`SELECT count(*) FROM pipeline_work_inputs WHERE scope='${privateScope}' AND event<>'${base+1}'`),[[0]],'private input cannot join group Work');
  release(group,2);await held(privateScope,1);release(privateScope,1);
  await finished(group,base);await finished(privateScope,base+1);
  assert.deepEqual(await state(`SELECT event,status,attempted,confirmed FROM pipeline_events WHERE scope='${group}' ORDER BY event`),[[String(base),'sent',1,1]],'group continuation keeps its original owner and sends once');
  assert.deepEqual(await state(`SELECT status,seen,count(*) FROM pipeline_work_inputs WHERE scope='${group}' GROUP BY status,seen`),[['settled',1,3]],'all group inputs settle after the actual answer');
  assert.equal(mainFor(group).length,2);assert.equal(mainFor(privateScope).length,1);
  assert.equal(reviewFor(group).length,1,'only the joined group inputs require review; another private Scope must not trigger an additional check');assert.equal(reviewFor(privateScope).length,0,'other Scope arrivals cannot trigger private review');
  assert.ok(mainFor(group)[1].inputs.some((x:any)=>x.sender_id==='87654321'&&x.event_id===String(base+1)));
  assert.ok(mainFor(group)[1].inputs.some((x:any)=>x.sender_id==='22345678'&&x.event_id===String(base+2)));
  const groupSent=(await messages()).filter(x=>String(x.params.group_id)===group.slice(6));
  assert.equal(groupSent.length,1);assert.ok(['GROUP_A','GROUP_B','GROUP_C'].every(tag=>sentText(groupSent[0]).includes(tag)),'no group member independent question is lost');
  assert.ok(!sentText(groupSent[0]).includes('OTHER_PRIVATE'),'private material does not become a group answer');

  // Keep introducing one unseen event per pass. Four main calls are bounded
  // within one activation; remaining input reactivates the SAME logical Work.
  const handoffScope='private:12345005',handoff=87000;let originalHandoffOrigin:any;
  await send(handoffScope,handoff,'SCOPE_CASE_HANDOFF_0');
  for(let pass=1;pass<=4;pass++){
   await held(handoffScope,pass);
   assert.deepEqual(await state(`SELECT event FROM pipeline_scope_work WHERE scope='${handoffScope}'`),[[String(handoff)]],'continuation preserves original Work through pass '+pass);
   await send(handoffScope,handoff+pass,'SCOPE_CASE_HANDOFF_'+pass);
   await until(async()=>(await state(`SELECT count(*) FROM pipeline_work_inputs WHERE scope='${handoffScope}' AND event='${handoff+pass}' AND status='owned' AND seen=0`))[0][0]===1,'next event joined before releasing pass '+pass);
   if(pass===4){
    const staged=await state(`SELECT message FROM fishbot_shared_inputs WHERE scope='${handoffScope}' AND event='${handoff+pass}'`);
    originalHandoffOrigin=sourced({messages:[JSON.parse(staged[0][0])]})[0];
   }
   release(handoffScope,pass);
  }
  await held(handoffScope,5);
  assert.deepEqual(await state(`SELECT event FROM pipeline_scope_work WHERE scope='${handoffScope}'`),[[String(handoff+1)]],'next activation anchors the earliest still-unanswered input, not only the last arrival');
  assert.deepEqual(await state(`SELECT event FROM pipeline_events WHERE scope='${handoffScope}' ORDER BY event`),[[String(handoff)],[String(handoff+1)]]);
  assert.equal((await state(`SELECT DISTINCT work_id FROM pipeline_events WHERE scope='${handoffScope}'`)).length,1,'both activations have the same permanent logical Work ID');
  assert.deepEqual(await state(`SELECT passes,exit_reason FROM pipeline_events WHERE scope='${handoffScope}' AND event='${handoff}'`),[[4,'activation_pass_limit']]);
  assert.ok(mainFor(handoffScope)[4].inputs.some((x:any)=>x.event_id===String(handoff+4)&&x.text.includes('SCOPE_CASE_HANDOFF_4')));
  assert.equal(mainFor(handoffScope)[4].inputs.find((x:any)=>x.event_id===String(handoff+4)).at,originalHandoffOrigin.at,'handoff preserves the first observed protocol time instead of conversion time');
  release(handoffScope,5);await finished(handoffScope,handoff+1);
  assert.equal(mainFor(handoffScope).length,5);assert.equal(reviewFor(handoffScope).length,4);
  assert.deepEqual(await state(`SELECT status,seen,count(*) FROM pipeline_work_inputs WHERE scope='${handoffScope}' AND event<>'${handoff}' GROUP BY status,seen`),[['settled',1,4]]);


  // Compare the full serialized old message objects. Facts, metadata,
  // media/tool forms, roles and ordering are protected, not just human text.
  for(let index=1;index<main.length;index++){
   const previous=main[index-1].body.messages,next=main[index].body.messages;
   assert.equal(JSON.stringify(next.slice(0,previous.length)),JSON.stringify(previous),'main request preserves committed wire prefix at '+index);
   const priorInScope=main.slice(0,index).find((request:any)=>request.scope===main[index].scope);
   if(priorInScope)assert.deepEqual(main[index].body.tools,priorInScope.body.tools,'same-Scope continuation tool catalog stable');
  }
  assert.equal(main.length,14,'main requests are bounded independently of burst size');
  assert.equal(reviews.length,8,'only unseen same-Scope revisions reach the auxiliary model; unrelated private memory does not interrupt a draft');
  assert.deepEqual(errors,[],'HTTP fixtures completed without hidden failures');
 }finally{
  for(const release of gates.values())release();
  await current.app.close();await new Promise<void>(r=>model.close(()=>r()));
 }
});
