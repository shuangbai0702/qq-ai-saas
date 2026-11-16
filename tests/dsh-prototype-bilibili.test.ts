import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp,readFile} from 'node:fs/promises';import os from 'node:os';import path from 'node:path';import {createHash} from 'node:crypto';
import {createApp} from '../apps/control-api/src/app.js';import {issueTestInvite} from './helpers/invitations.js';import {openToolWork,attachToolIpc} from './helpers/tool-work.js';
import {BilibiliExecutor} from '../apps/node-agent/src/bilibili/executor.js';import {BiliWeb} from '../apps/node-agent/src/bilibili/web-adapter.js';
import {biliOperations} from '../packages/contracts/bilibili.js';
import {bilibiliSource} from '../scripts/dsh-prototype/bilibili-source.js';
// @ts-ignore explicit local mjs prototype
import {installedDsh,createKernel,delegateLocal} from '../scripts/dsh-prototype/kernel.mjs';

// No production roots, credentials, accounts or QQ processes. External reads require explicit opt-in.
test('DSH Bilibili uses actual catalog, original Work admission, SQLite receipt and executor',{timeout:120000},async t=>{
 const real=process.env.FISHBOT_DSH_PUBLIC_READ==='1',root=await mkdtemp(path.join(os.tmpdir(),'fishbot-dsh-bili-')),app=await createApp({root,agent:false});t.after(()=>app.app.close());
 const auth=await app.app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(app.db),email:'dsh-local@example.invalid',password:'local-fixture-password',name:'local fixture'}});assert.equal(auth.statusCode,200,auth.body);
 const cookies={fish_session:auth.cookies[0].value};const created=await app.app.inject({method:'POST',url:'/v1/bots',cookies,payload:{name:'isolated dsh',protocol:'mock',apiKey:'synthetic-key'}});assert.equal(created.statusCode,201,created.body);
 const id=created.json().id;await app.db.query("UPDATE bot_config_revisions SET config=jsonb_set(config,'{behavior,ownerUin}','\"768687099\"') WHERE bot_id=$1",[id]);await app.db.query("UPDATE bots SET desired_state='running',health=$2 WHERE id=$1",[id,JSON.stringify({sampledAt:new Date().toISOString(),protocol:{online:true},ai:{ready:true}})]);
 const [bot]=await app.db.query('SELECT * FROM bots WHERE id=$1',[id]),native=await openToolWork(root,app.db,bot,'fixture-autonomous',1),closeIpc=await attachToolIpc(app.db,bot,native);t.after(async()=>{await closeIpc();await native.close();});
 await native.repo.configure(bot.tenant_id,id,1,{enabled:true,allowAnonymous:true,scopes:['public.read']});
 const key=Buffer.from((await readFile(path.join(root,'master.key'),'utf8')).trim(),'hex');let remoteReads=0;
 const web=real?new BiliWeb():new BiliWeb(async()=>{remoteReads++;return Response.json({code:0,data:{bvid:'BV1GJ411x7h7',aid:1,title:'Public fixture',owner:{mid:1,name:'fixture'},pages:[{cid:1,page:1,part:'fixture',duration:1}]}});});
 const executor=new BilibiliExecutor(app.db,root,key,web),scope='private:768687099',event='dsh-local-read';
 const workId='foreground:'+createHash('sha256').update('['+[id,scope,'768687099',event].map(v=>JSON.stringify(v)).join(', ')+']').digest('hex');
 await native.sql("INSERT INTO pipeline_events(scope,subject,event,at,source,stage,status,reason) VALUES(?,?,?,?,'message','model','received','')",[scope,'768687099',event,Date.now()/1000]);
 const identity={tenantId:bot.tenant_id,botId:id,workId,scope,delegationId:'read-1',generation:bot.generation,revision:bot.revision};let receipt:any;const receipts:any[]=[];
 const reads=real?[{operation:'video_info',args:{bvid:'BV1GJ411x7h7'}},{operation:'search',args:{query:'人工智能',limit:1}},{operation:'user_profile',args:{uid:'2'}},{operation:'comments',args:{aid:'170001',limit:1}},{operation:'transcript',args:{bvid:'BV1GJ411x7h7',segmentLimit:2}}]:[{operation:'video_info',args:{bvid:'BV1GJ411x7h7'}}];
 const source=bilibiliSource({identity,async readManifest(){const settings=await native.repo.settings(bot.tenant_id,id);return{version:1,botId:id,generation:bot.generation,revision:bot.revision,settingsRevision:settings.revision,operations:executor.catalog({config:settings.config})};},async dispatch(request:any,exec:any){
  assert.equal(exec.identity.botId,id);assert(reads.some(r=>r.operation===request.operation));
  const row=await native.repo.admit(bot,{...request,workId,stepId:String(exec.callId),authority:'foreground',senderId:'768687099',origin:{scope,eventId:event,objective:'read public video metadata'},deadlineAt:Date.now()+90000});
  await executor.execute(await native.repo.claim('isolated-dsh-worker'),exec.signal);receipt=(await native.repo.get(bot.tenant_id,id,row.id)).result;receipts.push({operation:request.operation,execution:receipt.execution,status:receipt.receipt.status,error:receipt.error?.code});return receipt;
 }});
 const dsh=await installedDsh(),{LlmAdapter}=await dsh.load('dsh-llm');let calls=0;
 class Fixture extends LlmAdapter{async *stream(){
  const request=reads[calls++];
  if(request){const args=JSON.stringify(request.args),name='bilibili_'+request.operation;
   yield{type:'block-start',index:0,blockType:'tool-call'};yield{type:'tool-call-delta',index:0,id:'public-read-'+calls,name,argumentsDelta:args};yield{type:'block-end',index:0,block:{type:'tool-call',id:'public-read-'+calls,name,arguments:args}};yield{type:'finish',reason:'tool-calls'};
  }else{const done=receipts.filter(r=>r.execution==='completed').map(r=>r.operation),failed=receipts.filter(r=>r.execution!=='completed');
   const text=JSON.stringify({result:'完成公共读取：'+done.join('、'),evidence:done.length?['https://www.bilibili.com/video/BV1GJ411x7h7']:[],unfinished:failed.map(r=>r.operation),limitations:failed.map(r=>r.error||'unknown')});
   yield{type:'block-start',index:0,blockType:'text'};yield{type:'text-delta',index:0,text};yield{type:'block-end',index:0,block:{type:'text',text}};yield{type:'finish',reason:'stop'};
  }
 }}
 const kernel=await createKernel({storageRoot:path.join(root,'dsh-sessions'),adapter:new Fixture()});t.after(()=>kernel.close());
 const host={identity,assertCurrent:async()=>{},authorize:async()=>true,reserveModel:async()=>({settle:async()=>{}}),discover:source.discover,validateArguments:source.validateArguments,execute:source.execute};
 const result=await delegateLocal(kernel,host,{goal:'读取这个公开视频的详情'});assert(receipt,JSON.stringify({result}));assert.equal(receipts.length,reads.length);assert.equal(receipt.schemaVersion,'fishbot.tool-result.v1');assert(['not_applicable','rejected','unknown'].includes(receipt.receipt.status));
 if(!real){assert.equal(receipt.execution,'completed');assert.equal(remoteReads,1);assert.equal((await source.discover()).every((v:any)=>v.effect==='read'),true);}
 console.log(JSON.stringify({kind:'DSH-original-Work-public-read',realNetwork:real,operations:receipts,finalOnly:Object.keys(result),paidModelCalls:0,productionAccess:false}));
});


test('Bilibili source preserves authoritative constraints, Bot identity and public-read-only manifest',async()=>{
 const identity={botId:'local-bot',generation:1,revision:1};let calls=0;
 const row=(operation:keyof typeof biliOperations)=>({operation,scope:biliOperations[operation].scope,write:biliOperations[operation].write,args:biliOperations[operation].args.toJSONSchema(),status:'available_to_attempt'});
 let manifest:any={version:1,...identity,settingsRevision:1,operations:[row('video_info'),row('transcript'),row('account_info'),row('like'),row('analyze'),{operation:'not_registered',scope:'public.read',write:false,args:{}}]};
 const source=bilibiliSource({identity,readManifest:async()=>manifest,dispatch:async()=>{calls++;return{};}}),tools=await source.discover();
 assert.deepEqual(tools.map((v:any)=>v.id),['video_info','transcript']);
 assert.deepEqual(tools[0].definition.parameters,biliOperations.video_info.args.toJSONSchema());
 await assert.rejects(source.validateArguments(tools[0],{bvid:'wrong'}));
 await assert.rejects(source.validateArguments(tools[1],{bvid:'BV1GJ411x7h7',start:20,end:10}));
 await source.validateArguments(tools[0],{bvid:'BV1GJ411x7h7'});
 manifest={...manifest,botId:'other-bot'};await assert.rejects(source.discover(),/Bot manifest/);assert.equal(calls,0);
});
