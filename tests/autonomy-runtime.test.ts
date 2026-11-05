import {permitModelFixture,nativeMessages} from './helpers/model-fixture.js';
import {issueTestInvite} from './helpers/invitations.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {mkdtemp,mkdir,readFile,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {createApp} from '../apps/control-api/src/app.js';
import {defaultConfig} from '../packages/contracts/index.js';
import {MockProtocolAdapter} from '../adapters/mock/index.js';

const sleep=(ms:number)=>new Promise(r=>setTimeout(r,ms));
async function until(check:()=>Promise<boolean>,why:string){for(let i=0;i<250;i++){if(await check())return;await sleep(100)}throw new Error('Autonomy runtime: '+why)}

test('native SDK: whitelist entry, refreshed clocks, owner timer, stale decision and model-selected images',{timeout:120000},async()=>{
 const calls:{kind:string;body:any}[]=[];let releaseOwner:(()=>void)|undefined;
 const model=http.createServer(async(req,res)=>{
  let raw='';for await(const b of req)raw+=b;const body=JSON.parse(raw),system=body.messages.filter((m:any)=>m.role==='system').map((m:any)=>m.content).join('\n');
  const selected=body.messages.map((m:any)=>typeof m.content==='string'?m.content:JSON.stringify(m.content)).join('\n').match(/"id"\s*:\s*"(bf_[a-f0-9]{16})"/)?.[1];const meme=selected?'[MEME:'+selected+']':'';const kind=system.includes('判断是否以当前人格自然加入群聊')?'group':system.includes('你是当前机器人的主动联系判断器')?'owner':'reply';calls.push({kind,body});let answer=JSON.stringify(nativeMessages(body).at(-1)).includes('平台内部主动聊天时刻')?'刚想到我们之前聊的那个话题，想听听你的近况。':'接着聊。'+meme;
  if(kind==='group'){
   const silent=JSON.parse(nativeMessages(body).at(-1).content).messages.at(-1).text.includes('SILENT_TOPIC');
   const other=JSON.parse(nativeMessages(body).at(-1).content).messages.at(-1).text.includes('OTHER_TARGET_QUESTION');
   answer=JSON.stringify({reply:!silent,target:other?'other':'open',participation:other?'side':'open',information:'substantive',interest:'strong',reason_code:'shared_interest',confidence:'high',cooldown_intervals:silent?1:0,cooldown_reason:silent?'no_motive':'none'});
  }
  if(kind==='owner'){
   if(calls.filter(x=>x.kind==='owner').length===1)await new Promise<void>(r=>{releaseOwner=r});
   answer=JSON.stringify({contact:true,topic:'想继续聊之前的共同兴趣'});
  }
  res.setHeader('Content-Type','application/json');res.end(JSON.stringify({id:'autonomy-native',model:body.model,choices:[{index:0,message:{role:'assistant',content:answer},finish_reason:'stop'}]}));
 });
 await new Promise<void>(r=>model.listen(0,'127.0.0.1',r));permitModelFixture(model);
 const root=await mkdtemp(path.resolve('.local/autonomy-native-')),current=await createApp({root});
 const python=path.resolve('references/astrbot/.venv/'+(process.platform==='win32'?'Scripts/python.exe':'bin/python'));
 try{
  const auth=await current.app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(current.db),email:'autonomy@fixture.invalid',password:'autonomy-fixture-123',name:'自主行为验收'}});const cookies={fish_session:auth.cookies[0].value};
  const config={...defaultConfig,baseUrl:`http://127.0.0.1:${(model.address()as any).port}/v1`,model:'autonomy-fixture',proactive:true,behavior:{...defaultConfig.behavior!,groupParticipationEnabled:true,groupParticipationGroups:['778899','889900','990011','991122'],groupParticipationIntervalMinutes:10,groupParticipationStyle:'active',ownerContactEnabled:true,ownerUin:'11111111',ownerContactIntervalMinutes:15,ownerContactIdleMinutes:5,ownerContactDailyChecks:12,proactiveModelSchedule:false,autonomousGroupContact:false,proactiveChance:0,proactiveIntervalSeconds:60,quietStart:0,quietEnd:0,typingSimulation:false,typoEnabled:false,splitMessages:false,burstDebounceMs:800,styleLearning:false,memorySummary:false,memeEnabled:true,fatigueEnabled:false}};
  const created=await current.app.inject({method:'POST',url:'/v1/bots',cookies,payload:{name:'自主行为验收',protocol:'mock',runtimeMode:'shared',expectedUin:'90007661',apiKey:'autonomy-fixture-key',config}});assert.equal(created.statusCode,201,created.body);const id=created.json().id;
  await current.app.inject({method:'POST',url:`/v1/bots/${id}/actions`,cookies,payload:{action:'start',idempotencyKey:'start'}});await until(async()=>!!(await current.db.query('SELECT health FROM bots WHERE id=$1',[id]))[0].health?.ai?.ready,'AI ready');
  const ctx=await current.agent.context((await current.db.query('SELECT * FROM bots WHERE id=$1',[id]))[0]),mock=current.agent.protocols.mock as MockProtocolAdapter;
  await mock.call(ctx,'/transition',{state:'online',uin:'90007661'});await until(async()=>!!(await mock.call(ctx,'/state')as any).connected,'QQ connected');
  const actions=async()=>(await mock.call(ctx,'/messages')as any[]).filter(x=>x.action.startsWith('send_'));
  // A protocol ACK precedes reply bookkeeping. Wait for the actual runtime
  // idle mark before testing a new unsolicited entry against its global gate.
  const settled=()=>until(async()=>{try{return JSON.parse(await readFile(path.join(root,'bots',id,'memory-activity.json'),'utf8')).active===false}catch{return false}},'previous reply work settled');
  const groupCalls=()=>calls.filter(x=>x.kind==='group').length,ownerCalls=()=>calls.filter(x=>x.kind==='owner').length;
  const message=(group:string|undefined,text:string,sender='22222222',mention:boolean|string=false)=>mock.call(ctx,'/message',{...(group?{group}:{}),sender,text,mention});
  const dbfile=path.join(root,'bots',id,'behavior.sqlite');
  const shiftClock=async(sql:string)=>promisify(execFile)(python,['-B','-c','import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.executescript(sys.argv[2]); c.close()',dbfile,sql],{windowsHide:true});
  await message('667788','名单之外');await sleep(400);assert.equal(calls.length,0,'no random or all-message bypass outside whitelist');
  await message('778899','公开聊一个话题');await until(async()=>(await actions()).length===1,'semantic entry reply');assert.equal(groupCalls(),1);
  const first=(await actions())[0];assert.ok(first.params.message.some((x:any)=>x.type==='image'),'formal model still selects image after participation decision');
  await message('778899','接着换一种说法');await until(async()=>(await actions()).length===2,'same partner followup');
  await message('778899','我也来说两句','33333333');await until(async()=>(await actions()).length===3,'another member followup');assert.equal(groupCalls(),1,'ongoing conversation has no repeated decision');
  await settled();
  const readGroupClock=async()=>JSON.parse((await promisify(execFile)(python,['-B','-c',"import sqlite3,sys,json;d=sqlite3.connect(sys.argv[1]);print(json.dumps(d.execute(\"SELECT interaction,version FROM autonomy_groups WHERE scope='group:778899'\").fetchone()))",dbfile],{windowsHide:true})).stdout);
  const previousClock=await readGroupClock();await message('778899','我这是回复别人的话','33333333','44444444');await sleep(400);
  assert.deepEqual(await readGroupClock(),previousClock,'same recent partner @ somebody else cannot refresh the bot interaction clock');
  await message('889900','');await sleep(400);assert.equal(groupCalls(),1,'nontext current input cannot reuse ambient context or consume the new group window');
  await message('889900','SILENT_TOPIC');await until(async()=>groupCalls()===2,'second group independent decision');await sleep(200);
  await message('889900','又有新话题');await sleep(400);assert.equal(groupCalls(),2,'new message alone cannot bypass 10 minutes');
  await settled();const now=Math.floor(Date.now()/1000);await shiftClock(`UPDATE autonomy_groups SET checked=${now-601} WHERE scope='group:889900';`);
  const cooldown=await promisify(execFile)(python,['-B','-c',"import sqlite3,sys,json;d=sqlite3.connect(sys.argv[1]);print(json.dumps(d.execute(\"SELECT extra_intervals,base_seconds,cooldown_until-at FROM autonomy_decisions WHERE scope='group:889900' ORDER BY at DESC LIMIT 1\").fetchone()))",dbfile],{windowsHide:true});
  assert.deepEqual(JSON.parse(cooldown.stdout),[1,600,1200]);
  await message('889900','基础间隔过了但额外冷却还在');await sleep(400);assert.equal(groupCalls(),2,'model-chosen extra cooldown survives the base interval');
  await shiftClock(`UPDATE initiative_pauses SET until=${now-1} WHERE scope='group:889900';`);
  await until(async()=>(await actions()).length===4,'cooldown expiry rechecks the already received input without another message');assert.equal(groupCalls(),3);
  await message(undefined,'主人先正常聊一句','11111111');await until(async()=>(await actions()).filter(x=>x.action==='send_private_msg').length===1,'real owner private contact');
  await until(async()=>{const r=await promisify(execFile)(python,['-B','-c',"import sqlite3,sys;d=sqlite3.connect(sys.argv[1]);r=d.execute(\"SELECT armed FROM autonomy_owner WHERE owner='11111111'\").fetchone();print(r[0] if r else 0)",dbfile],{windowsHide:true});return r.stdout.trim()==='1'},'scheduler arms owner interval before fixture time advance');
  const advanceOwner=async()=>{const at=Math.floor(Date.now()/1000)-1001;await shiftClock(`UPDATE autonomy_owner SET checked=${at},interaction=${at} WHERE owner='11111111'; UPDATE contacts SET last_user=${at},last_bot=${at},last_attempt=0 WHERE scope='private:11111111';`)};
  await advanceOwner();await until(async()=>ownerCalls()===1&&!!releaseOwner,'owner timer calls model without proactive probability');
  await message('778899','我正在这里和你聊','11111111',true);await until(async()=>(await actions()).filter(x=>x.action==='send_group_msg').length===5,'owner starts group conversation during decision');releaseOwner!();await sleep(600);
  assert.equal((await actions()).filter(x=>x.action==='send_private_msg').length,1,'late owner decision cannot interrupt fresh group interaction');
  await advanceOwner();await until(async()=>(await actions()).filter(x=>x.action==='send_private_msg').length===2,'next timer checks again and delivers once');assert.equal(ownerCalls(),2);
  await sleep(5500);assert.equal(ownerCalls(),2,'5-second poll is not repeated model evaluation');assert.equal((await actions()).filter(x=>x.action==='send_private_msg').length,2,'unanswered owner is not chased');
  for(const call of calls.filter(x=>x.kind==='owner'))assert.ok(JSON.parse(nativeMessages(call.body).at(-1).content).owner_memory.recent_private.some((x:any)=>x.user==='主人先正常聊一句'),'timer judgment receives actual private history despite having no native chat contexts');
  assert.equal(JSON.parse(nativeMessages(calls.find(x=>x.kind==='group')!.body).at(-1).content).style,'active','configured participation style reaches the group decision model');
  const beforeAddressing=groupCalls(),beforeAddressingSends=(await actions()).length;
  await message('990011','SELF_RELATED_TO_OTHER 这个话题也和机器人有关','22222222','33333333');
  await message('990011','','22222222');
  await until(async()=>(await actions()).length===beforeAddressingSends+1,'model can admit a relevant topic despite an @ to another person');
  const addressed=calls.filter(x=>x.kind==='group').at(-1)!;assert.equal(JSON.parse(nativeMessages(addressed.body).at(-1).content).platform_addressing.mentions_other,true);
  await message('991122','OTHER_TARGET_QUESTION 这是只说给另一个人的','22222222','33333333');await until(async()=>groupCalls()===beforeAddressing+2,'other-person addressing reaches the semantic model');await sleep(400);
  assert.equal((await actions()).length,beforeAddressingSends+1,'disabled side comments still prevent joining somebody else’s conversation');
  for(const call of calls)assert.equal(call.body.max_tokens,call.kind==='reply'?undefined:192,'actual SDK HTTP payload bounds decisions without changing normal replies');
  await mkdir('test-results/autonomy',{recursive:true});await writeFile('test-results/autonomy/native-runtime.json',JSON.stringify({passed:true,transport:'real AstrBot SDK and shared runtime; model and QQ are isolated fixtures',groupDecisions:groupCalls(),ownerDecisions:ownerCalls(),messages:(await actions()).length,checks:['whitelist','no random bypass','10-minute gate','expiry replays pending real input without another message','model extra cooldown persisted and expired','real conversation refresh','multi-member followup','model-selected image','owner timer','new group interaction invalidates pending private message','no repeated polling call','nontext input preserves group window','semantic @other admission','side-comment policy retained','burst retains text before nontext','@other followup does not refresh interaction']},null,2));
 }finally{releaseOwner?.();await current.app.close();await new Promise<void>(r=>model.close(()=>r()))}
});
