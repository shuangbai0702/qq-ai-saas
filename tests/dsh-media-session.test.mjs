import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp} from 'node:fs/promises';
import os from 'node:os';import path from 'node:path';
import {createKernel,installedDsh,delegateLocal,sessionIdentity} from '../packages/dsh/kernel.mjs';
async function* output(value,call=false){
 const text=call?JSON.stringify(value):value;
 yield {type:'block-start',index:0,blockType:call?'tool-call':'text'};
 if(call)yield {type:'tool-call-delta',index:0,id:'media-call',name:'bilibili_analyze',argumentsDelta:text};
 else yield {type:'text-delta',index:0,text};
 yield {type:'block-end',index:0,block:call?{type:'tool-call',id:'media-call',name:'bilibili_analyze',arguments:text}:{type:'text',text}};
 yield {type:'finish',reason:call?'tool-calls':'stop'};
}
test('original native Session suspends at durable media admission and resumes once with authoritative result',async()=>{
 const storageRoot=await mkdtemp(path.join(os.tmpdir(),'dsh-media-session-'));
 const native=await installedDsh(),{LlmAdapter}=await native.load('dsh-llm');let models=0,dispatches=0;
 class Fixture extends LlmAdapter{async *stream(options){models++;if(models===1)yield*output({bvid:'BV1GJ411x7h7'},true);else{assert(JSON.stringify(options.messages).includes('00:42'));yield*output('00:42画面支持该结论；另一段尚未确认。')}}}
 const identity={tenantId:'tenant',botId:'bot',workId:'original-work',scope:'private:owner',delegationId:'call'};
 const host={identity,assertCurrent:async()=>{},authorize:async()=>true,reserveModel:async()=>({settle:async()=>{}}),
  discover:async()=>[{id:'analyze',capability:'bilibili',effect:'read',available:true,version:'1',definition:{name:'bilibili_analyze',description:'Existing media Worker',parameters:{type:'object',properties:{bvid:{type:'string'}},required:['bvid'],additionalProperties:false},output:{schema:{}}}}],
  execute:async()=>{dispatches++;return {kind:'fishbot_dsh_wait',actionId:'original-job'}}};
 let kernel=await createKernel({storageRoot,adapter:new Fixture()});
 try{assert.deepEqual(await delegateLocal(kernel,host,{goal:'核实视频'}),{kind:'fishbot_dsh_wait',actionId:'original-job'});assert.equal(models,1);assert.equal(dispatches,1)}finally{await kernel.close()}
 const completion={id:'original-job',text:'原Worker已确认：00:42画面可见目标。'};
 kernel=await createKernel({storageRoot,adapter:new Fixture()});
 try{
  const text=await delegateLocal(kernel,host,undefined,{resumeSessionId:sessionIdentity(identity),completion});
  assert.equal(typeof text,'string');assert(text.includes('00:42'));assert.equal(models,2);assert.equal(dispatches,1);
  assert.equal(await delegateLocal(kernel,host,undefined,{resumeSessionId:sessionIdentity(identity),completion}),text);
  assert.equal(models,2);assert.equal(dispatches,1);
 }finally{await kernel.close()}
});


test('same Work follow-up can wait for media and recover on the original native Session',async()=>{
 const storageRoot=await mkdtemp(path.join(os.tmpdir(),'dsh-followup-media-'));
 const native=await installedDsh(),{LlmAdapter}=await native.load('dsh-llm');let models=0,dispatches=0;
 class Fixture extends LlmAdapter{async *stream(options){models++;if(models===1)yield*output('原任务结论');else if(models===2){assert(JSON.stringify(options.messages).includes('原任务结论'));yield*output({bvid:'BV1GJ411x7h7'},true)}else{const wire=JSON.stringify(options.messages);assert(wire.includes('追加核实'));assert(wire.includes('00:42'));yield*output('追问已核实00:42')}}}
 const identity={tenantId:'tenant',botId:'bot',workId:'work',scope:'private:owner',delegationId:'original'};
 const host={identity,assertCurrent:async()=>{},authorize:async()=>true,reserveModel:async()=>({settle:async()=>{}}),discover:async()=>[{id:'analyze',capability:'bilibili',effect:'read',available:true,version:'1',definition:{name:'bilibili_analyze',description:'Media',parameters:{type:'object',properties:{bvid:{type:'string'}},required:['bvid'],additionalProperties:false},output:{schema:{}}}}],execute:async()=>{dispatches++;return{kind:'fishbot_dsh_wait',actionId:'followup-job'}}};
 let kernel=await createKernel({storageRoot,adapter:new Fixture()});
 const resumeSessionId=sessionIdentity(identity),followup={id:'next-call'};
 try{
  assert.equal(await delegateLocal(kernel,host,{goal:'原任务'}),'原任务结论');
  assert.deepEqual(await delegateLocal(kernel,host,{goal:'追加核实'},{resumeSessionId,followup}),{kind:'fishbot_dsh_wait',actionId:'followup-job'});
 }finally{await kernel.close()}
 kernel=await createKernel({storageRoot,adapter:new Fixture()});
 try{
  const completion={id:'followup-job',text:'00:42已确认'};
  assert.equal(await delegateLocal(kernel,host,undefined,{resumeSessionId,completion}),'追问已核实00:42');
  assert.equal(await delegateLocal(kernel,host,{goal:'追加核实'},{resumeSessionId,followup}),'追问已核实00:42');
  assert.equal(models,3);assert.equal(dispatches,1);
 }finally{await kernel.close()}
});
