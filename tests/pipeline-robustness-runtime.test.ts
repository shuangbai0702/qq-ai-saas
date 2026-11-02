import {permitModelFixture} from './helpers/model-fixture.js';
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
async function until(check:()=>Promise<boolean>,why:string){for(let i=0;i<250;i++){if(await check())return;await sleep(100)}throw Error('Robust pipeline: '+why)}

test('native semantic entry sends every bubble with one final image and refunds merged silent work',{timeout:90000},async()=>{
 const calls:{kind:string;body:any}[]=[];
 const model=http.createServer(async(req,res)=>{
  let raw='';for await(const chunk of req)raw+=chunk;const body=JSON.parse(raw);
  const system=body.messages.filter((m:any)=>m.role==='system').map((m:any)=>m.content).join('\n');
  const kind=system.includes('判断是否以当前人格自然加入群聊')?'group':'reply';calls.push({kind,body});
  const input=JSON.stringify(body.messages),image=input.match(/"id"\s*:\s*"(bf_[a-f0-9]{16})"/)?.[1]||input.match(/\\"id\\"\s*:\s*\\"(bf_[a-f0-9]{16})\\"/)?.[1];
  const answer=kind==='group'?JSON.stringify({reply:true,target:'open',participation:'open',information:'substantive',interest:'strong',reason_code:'shared_interest',confidence:'high'}):input.includes('COMBINED_SILENT')?'[NO_REPLY]':'[AT:12345678]先接上这个话题。[QQ_BREAK]还有一点想法。[QQ_BREAK]就这样聊聊。'+(image?'[MEME:'+image+']':'');
  res.setHeader('Content-Type','application/json');res.end(JSON.stringify({id:'robust-pipeline',model:body.model,choices:[{index:0,message:{role:'assistant',content:answer},finish_reason:'stop'}]}));
 });
 await new Promise<void>(r=>model.listen(0,'127.0.0.1',r));permitModelFixture(model);
 const root=await mkdtemp(path.resolve('.local/pipeline-robustness-')),current=await createApp({root});
 const python=path.resolve(`references/astrbot/.venv/${process.platform==='win32'?'Scripts/python.exe':'bin/python'}`);
 try{
  const registered=await current.app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(current.db),email:'robustness@fixture.invalid',password:'robustness-fixture-123',name:'管线边界验收'}}),cookies={fish_session:registered.cookies[0].value};
  const config={...defaultConfig,baseUrl:`http://127.0.0.1:${(model.address()as any).port}/v1`,model:'robustness-fixture',proactive:false,behavior:{...defaultConfig.behavior!,groupParticipationEnabled:true,groupParticipationGroups:['777111','777222'],groupParticipationIntervalMinutes:10,quietStart:0,quietEnd:0,ownerContactEnabled:false,typingSimulation:false,typoEnabled:false,splitMessages:true,sendIntervalMs:300,burstDebounceMs:600,styleLearning:false,memorySummary:false,memeEnabled:true,fatigueEnabled:false}};
  const created=await current.app.inject({method:'POST',url:'/v1/bots',cookies,payload:{name:'管线边界验收',protocol:'mock',runtimeMode:'shared',expectedUin:'90008772',apiKey:'robustness-fixture-key',config}});assert.equal(created.statusCode,201,created.body);const id=created.json().id;
  await current.app.inject({method:'POST',url:`/v1/bots/${id}/actions`,cookies,payload:{action:'start',idempotencyKey:'start'}});await until(async()=>!!(await current.db.query('SELECT health FROM bots WHERE id=$1',[id]))[0].health?.ai?.ready,'AI ready');
  const ctx=await current.agent.context((await current.db.query('SELECT * FROM bots WHERE id=$1',[id]))[0]),mock=current.agent.protocols.mock as MockProtocolAdapter;
  await mock.call(ctx,'/transition',{state:'online',uin:'90008772'});await until(async()=>!!(await mock.call(ctx,'/state')as any).connected,'QQ connected');
  const sends=async()=>(await mock.call(ctx,'/messages')as any[]).filter(x=>x.action.startsWith('send_'));
  await mock.call(ctx,'/message',{group:'777111',sender:'12345678',mention:false,messageId:82001,text:'公开讨论这个话题'});
  await until(async()=>(await sends()).length===3,'all three semantic reply bubbles');
  const sent=await sends();assert.ok(sent[0].params.message.some((x:any)=>x.type==='at'),'real mention belongs to first bubble');
  assert.ok(sent[2].params.message.some((x:any)=>x.type==='image'),'model-selected fish image arrives with final bubble');
  assert.equal(sent.flatMap(x=>x.params.message).filter((x:any)=>x.type==='image').length,1);
  assert.equal(sent.slice(1).flatMap(x=>x.params.message).filter((x:any)=>x.type==='at').length,0);
  await until(async()=>{const b=(await current.db.query('SELECT health FROM bots WHERE id=$1',[id]))[0];return b.health?.ai?.metrics?.lastPipeline?.status==='sent'},'full pipeline receipt commits');
  const state=async(sql:string)=>JSON.parse((await promisify(execFile)(python,['-B','-c','import sqlite3,sys,json;d=sqlite3.connect(sys.argv[1]);print(json.dumps(d.execute(sys.argv[2]).fetchall()))',path.join(root,'bots',id,'behavior.sqlite'),sql],{windowsHide:true})).stdout);
  await mock.call(ctx,'/message',{group:'777222',sender:'12345678',mention:false,messageId:82002,text:'COMBINED_SILENT first'});
  await until(async()=>(await state("SELECT status FROM pipeline_events WHERE event='82002'")).length===1,'first real burst event is bound');
  await mock.call(ctx,'/message',{group:'777222',sender:'12345678',mention:false,messageId:82003,text:'COMBINED_SILENT selected'});
  await until(async()=>calls.filter(x=>x.kind==='reply').length===2,'merged silent reply evaluated once');
  await until(async()=>JSON.stringify(await state("SELECT state FROM autonomy_sends WHERE scope='group:777222'"))==='[["released"]]','selected silent reservation refunded immediately');
  assert.equal((await sends()).length,3,'silent work cannot send or consume a send slot');
  assert.deepEqual(await state("SELECT status FROM pipeline_events WHERE event IN ('82002','82003') ORDER BY event"),[['combined'],['silent']]);
  assert.equal(calls.filter(x=>x.kind==='group').length,2);assert.equal(calls.filter(x=>x.kind==='reply').length,2);
 }finally{await current.app.close();await new Promise<void>(r=>model.close(()=>r()))}
});
