import test from 'node:test';import assert from 'node:assert/strict';
import {mkdtemp} from 'node:fs/promises';import path from 'node:path';import os from 'node:os';import http from 'node:http';
import {DshHost} from '../apps/node-agent/src/dsh-host.js';
// @ts-ignore
import {createKernel,installedDsh,delegateLocal,sessionIdentity} from '../packages/dsh/kernel.mjs';

for(const textEdited of [false,true])test('restarted production host preserves admitted media after ACK loss'+(textEdited?' and an authenticated tool prompt edit':''),async t=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'dsh-media-ack-loss-'));
 const bot={id:'bot',tenant_id:'tenant',generation:1,revision:2,desired_state:'running',runtime_mode:'dedicated',health:{sampledAt:new Date().toISOString(),protocol:{online:true},ai:{ready:true}}};
 const parent={bot_id:bot.id,tenant_id:bot.tenant_id,generation:bot.generation,config_revision:bot.revision,authority:'foreground',work_id:'original-work',origin_scope:'private:12345',origin_event:'original-event',sender_id:'12345',settings_revision:3,account_revision:4,account_uid:'888'};
 const identity={tenantId:bot.tenant_id,botId:bot.id,workId:parent.work_id,scope:parent.origin_scope,delegationId:'original-delegate',generation:bot.generation,revision:bot.revision};
 const sessionId=sessionIdentity(identity),botRoot=path.join(root,'bots',bot.id),storageRoot=path.join(botRoot,'dsh-sessions');
 let modelCalls=0,dispatches=0;
 const native=await installedDsh(),{LlmAdapter}=await native.load('dsh-llm');
 class Fixture extends LlmAdapter{async *stream(){
  modelCalls++;const id='clip-'+modelCalls,args=JSON.stringify({bvid:'BV1GJ411x7h7'});
  yield{type:'block-start',index:0,blockType:'tool-call'};
  yield{type:'tool-call-delta',index:0,id,name:'bilibili_analyze',argumentsDelta:args};
  yield{type:'block-end',index:0,block:{type:'tool-call',id,name:'bilibili_analyze',arguments:args}};
  yield{type:'finish',reason:'tool-calls'};
 }}
 const nativeHost={identity,assertCurrent:async()=>{},authorize:async()=>true,reserveModel:async()=>({settle:async()=>{}}),
  discover:async()=>[{id:'analyze',capability:'bilibili',effect:'read',available:true,version:'1',definition:{name:'bilibili_analyze',description:'Original media Worker',parameters:{type:'object',properties:{bvid:{type:'string'}},required:['bvid'],additionalProperties:false},output:{schema:{}}}}],
  execute:async()=>({kind:'fishbot_dsh_wait',actionId:'clip-'+(++dispatches)})};
 let kernel=await createKernel({storageRoot,adapter:new Fixture()});
 try{assert.equal((await delegateLocal(kernel,nativeHost,{goal:'核实两个片段'})).actionId,'clip-1')}finally{await kernel.close()}
 kernel=await createKernel({storageRoot,adapter:new Fixture()});
 try{assert.equal((await delegateLocal(kernel,nativeHost,undefined,{resumeSessionId:sessionId,completion:{id:'clip-1',text:'第一段真实分析结果'}})).actionId,'clip-2')}finally{await kernel.close()}
 assert.equal(modelCalls,2);assert.equal(dispatches,2);
 const jobs:any={
  'clip-1':{...parent,id:'clip-1',operation:'analyze',authority:'foreground',completion_state:'claimed',completion_owner:'original-owner',completion_until:new Date(Date.now()+120000).toISOString(),result:{execution:'completed',receipt:{status:'not_applicable'}},continuation:{dsh:{sessionId,delegationId:identity.delegationId}}},
  'clip-2':{...parent,id:'clip-2',operation:'analyze',authority:'foreground',state:'queued',cancel_requested:false,continuation:{sourceActionId:'clip-1',sourceOwner:'original-owner',dsh:{sessionId,delegationId:identity.delegationId}}}
 };
 let upstreamModels=0;
 const upstream=http.createServer(async(req,res)=>{for await(const _ of req){};if(req.url==='/autonomous/tool'){res.end(JSON.stringify({valid:true}));return}upstreamModels++;res.writeHead(500);res.end('{}')});
 await new Promise<void>(resolve=>upstream.listen(0,'127.0.0.1',resolve));t.after(()=>new Promise<void>(resolve=>upstream.close(()=>resolve())));
 const port=(upstream.address() as import('node:net').AddressInfo).port,oldPort=process.env.FISHBOT_DSH_PORT;process.env.FISHBOT_DSH_PORT='0';
 t.after(()=>{if(oldPort===undefined)delete process.env.FISHBOT_DSH_PORT;else process.env.FISHBOT_DSH_PORT=oldPort});
 const originalConfig={baseUrl:'http://127.0.0.1:'+port+'/v1',model:'fixture',behavior:{ownerUin:'12345'},toolPrompts:{dsh:'old'}};
 if(textEdited)bot.revision=3;
 const revisionRows=[{revision:2,config:originalConfig},{revision:3,config:{...originalConfig,toolPrompts:{dsh:'new'}},detail:{source:'authenticated_web_config',actorId:'user',credentialChanged:false}}];
 const agent:any={root,key:Buffer.alloc(32,1),closed:false,context:async()=>({root:botRoot,apiKey:'unused-fixture-key',config:{baseUrl:'http://127.0.0.1:'+port+'/v1',model:'fixture',behavior:{ownerUin:'12345'}}}),
  db:{query:async(sql:string)=>sql.includes('bot_config_revisions')?revisionRows:sql.includes('SELECT ai_port')?[{ai_port:port-1,token:'ipc'}]:[bot]},
  bilibili:{repo:{get:async(_tenant:string,_bot:string,id:string)=>jobs[id],settings:async()=>({revision:3,config:{enabled:true,scopes:['media.analyze']}}),pending:async()=>[jobs['clip-1'],jobs['clip-2']]},catalog:()=>[]}};
 const host=new DshHost(agent,async()=>({discover:async()=>[],close:async()=>{}}));await host.start();t.after(()=>host.stop());
 const {readFile}=await import('node:fs/promises');const bridge=JSON.parse(await readFile(path.join(botRoot,'ai/dsh-bridge.json'),'utf8'));
 const response=await fetch(bridge.url,{method:'POST',headers:{Authorization:'Bearer '+bridge.token,'Content-Type':'application/json'},body:JSON.stringify({protocol:2,parent,delegationId:identity.delegationId,completion:{id:'clip-1',owner:'original-owner'}})});
 assert.equal(response.status,200);assert.deepEqual((await response.text()).trim(),JSON.stringify({type:'pending'}));
 assert.equal(modelCalls,2);assert.equal(dispatches,2);assert.equal(upstreamModels,0);
});
