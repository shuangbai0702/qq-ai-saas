import test from 'node:test';import assert from 'node:assert/strict';import http from 'node:http';import path from 'node:path';import {mkdtemp,readFile} from 'node:fs/promises';
import {createApp} from '../apps/control-api/src/app.js';import {defaultConfig} from '../packages/contracts/index.js';import {MockProtocolAdapter} from '../adapters/mock/index.js';
import {permitModelFixture} from './helpers/model-fixture.js';import {issueTestInvite} from './helpers/invitations.js';
const sleep=(ms:number)=>new Promise(r=>setTimeout(r,ms));
async function until(fn:()=>Promise<boolean>,what:string){for(let i=0;i<150;i++){if(await fn())return;await sleep(300)}throw new Error(what)}
test('real shared AstrBot sees unmentioned group atmosphere without extra inference and cleans upstream internal tags',{timeout:120000},async()=>{
 let releaseModel!:()=>void;const heldModel=new Promise<void>(resolve=>{releaseModel=resolve});
 const requests:any[]=[];const server=http.createServer(async(req,res)=>{let raw='';for await(const chunk of req)raw+=chunk;const body=JSON.parse(raw);requests.push(body);await heldModel;res.setHeader('content-type','application/json');res.end(JSON.stringify({id:'ambient-fixture',model:body.model,choices:[{index:0,message:{role:'assistant',content:'鱼鱼明白啦。<system_reminder>User ID: 123, Nickname: 内部</system_reminder><｜DSML｜function_calls>PRIVATE_TOOL_MARKER</｜DSML｜function_calls>'},finish_reason:'stop'}],usage:{total_tokens:20}}))});
 await new Promise<void>(r=>server.listen(0,'127.0.0.1',r));permitModelFixture(server);
 const current=await createApp({root:await mkdtemp(path.resolve('.local/ambient-runtime-'))});
 try{
  const registered=await current.app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(current.db),email:'ambient@test.invalid',password:'ambient-test-123',name:'群聊上下文'}});const cookies={fish_session:registered.cookies[0].value};
  const config={...defaultConfig,baseUrl:`http://127.0.0.1:${(server.address() as any).port}/v1`,model:'ambient-fixture',groups:['777777','888888'],behavior:{...defaultConfig.behavior,followupSeconds:0,randomReplyProbability:0,burstDebounceMs:0,socialAwareness:true,styleLearning:false}};
  const r=await current.app.inject({method:'POST',url:'/v1/bots',cookies,payload:{name:'群聊氛围',runtimeMode:'shared',protocol:'mock',config,apiKey:'synthetic-ambient-key'}});assert.equal(r.statusCode,201,r.body);const id=r.json().id;
  await current.app.inject({method:'POST',url:`/v1/bots/${id}/actions`,cookies,payload:{action:'start',idempotencyKey:'ambient-start'}});
  await until(async()=>{const[b]=await current.db.query('SELECT * FROM bots WHERE id=$1',[id]);return !!b.health?.ai?.ready},'shared ready');
  const[b]=await current.db.query('SELECT * FROM bots WHERE id=$1',[id]);const ctx=await current.agent.context(b);const mock=current.agent.protocols.mock as MockProtocolAdapter;
  await mock.call(ctx,'/transition',{state:'online',uin:'90003331'});await until(async()=>!!(await mock.call(ctx,'/state') as any).connected,'OneBot connected');
  const activity=async()=>readFile(path.join(ctx.root,'memory-activity.json'),'utf8').catch(()=>'missing');const beforeActivity=await activity();
  await mock.call(ctx,'/message',{text:'OTHER_GROUP_ATMOSPHERE',group:'888888',sender:'12345670',mention:false});
  await mock.call(ctx,'/message',{text:'UNMENTIONED_ATMOSPHERE_A',group:'777777',sender:'12345671',mention:false});
  await mock.call(ctx,'/message',{text:'UNMENTIONED_ATMOSPHERE_B',group:'777777',sender:'12345672',mention:false});
  await sleep(1000);assert.equal(requests.length,0,'ambient messages do not invoke the model');assert.equal(JSON.parse(await activity()).active,false,'ambient ingress may stamp lifecycle telemetry but cannot leave model work active');
  await mock.call(ctx,'/message',{text:'你觉得刚才气氛怎么样',group:'777777',sender:'12345673',mention:true});
  await until(async()=>requests.length===1,'one inference');assert.equal(JSON.parse(await activity()).active,true,'real in-flight model request keeps the AI active');releaseModel();const payload=JSON.stringify(requests[0]);assert.ok(payload.includes('UNMENTIONED_ATMOSPHERE_A'));assert.ok(payload.includes('UNMENTIONED_ATMOSPHERE_B'));assert.ok(payload.includes('12345671'));assert.ok(!payload.includes('OTHER_GROUP_ATMOSPHERE'));
  await until(async()=>((await mock.call(ctx,'/messages')) as any[]).some(x=>x.action==='send_group_msg'),'actual OneBot reply');
  await until(async()=>JSON.parse(await activity()).active===false,'completed AI task releases active stamp');assert.notEqual(await activity(),beforeActivity,'actual AI task stamps activity');
  const sent=JSON.stringify(await mock.call(ctx,'/messages'));assert.ok(sent.includes('鱼鱼明白啦'));assert.ok(!sent.includes('PRIVATE_TOOL_MARKER'));assert.ok(!sent.includes('system_reminder'));assert.ok(!sent.includes('DSML'));
 }finally{releaseModel();await current.app.close();await new Promise<void>(r=>server.close(()=>r()))}
});
