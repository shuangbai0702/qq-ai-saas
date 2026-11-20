import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp} from 'node:fs/promises';import os from 'node:os';import path from 'node:path';
import {createKernel,installedDsh,delegateLocal,sessionIdentity,delegateSchema} from '../packages/dsh/kernel.mjs';
const final='公开资料已核实，来源：https://example.com/source';
async function* text(value){const s=typeof value==='string'?value:JSON.stringify(value);yield{type:'block-start',index:0,blockType:'text'};yield{type:'text-delta',index:0,text:s};yield{type:'block-end',index:0,block:{type:'text',text:s}};yield{type:'finish',reason:'stop'};}
async function* call(name,args){yield{type:'block-start',index:0,blockType:'tool-call'};yield{type:'tool-call-delta',index:0,id:'call-1',name,argumentsDelta:JSON.stringify(args)};yield{type:'block-end',index:0,block:{type:'tool-call',id:'call-1',name,arguments:JSON.stringify(args)}};yield{type:'finish',reason:'tool-calls'};}
function tool(id='lookup',version='1'){return{id,version,capability:'web_search',effect:'read',available:true,definition:{name:id,description:'Read public source',parameters:{type:'object',properties:{query:{type:'string'}},required:['query'],additionalProperties:false},output:{schema:{type:'object',properties:{answer:{type:'string'}},required:['answer'],additionalProperties:false}}}};}
function host(id='bot-a'){const state={tools:[tool()],calls:[],reservations:[],allowed:true};return{state,identity:{tenantId:'tenant',botId:id,workId:'work-'+id,scope:'private:fixture',delegationId:'delegate-1'},async assertCurrent(){if(!state.allowed)throw Error('Work revoked');},async discover(){return state.tools;},async authorize(){return state.allowed;},async reserveModel(){const row={};state.reservations.push(row);return{async settle(value){Object.assign(row,value);}};},async execute(item,args,exec){state.calls.push({id:item.id,args,identity:exec.identity});return{answer:'verified'};}};}
async function rig(stream,storageRoot){const native=await installedDsh(),{LlmAdapter}=await native.load('dsh-llm');class Fixture extends LlmAdapter{stream(o){return stream(o);}}return createKernel({storageRoot:storageRoot||await mkdtemp(path.join(os.tmpdir(),'fishbot-dsh-')),adapter:new Fixture()});}
test('installed native kernel executes scoped external tool and returns final report only',async()=>{const h=host();let n=0;const k=await rig(async function*(o){assert(o.tools.every(t=>t.name==='lookup'));if(!n++)yield*call('lookup',{query:'public source'});else yield*text(final);});try{assert.deepEqual(await delegateLocal(k,h,{goal:'核实公开资料'}),final);assert.equal(h.state.calls.length,1);assert.equal(h.state.calls[0].identity.botId,'bot-a');assert(h.state.reservations.every(r=>r.state==='completed'));assert.equal(delegateSchema.name,'dsh_delegate');assert(!JSON.stringify(delegateSchema).includes('dsh_task'));}finally{await k.close();}});
test('native persistence resumes completed task without model re-execution or tools',async()=>{const root=await mkdtemp(path.join(os.tmpdir(),'fishbot-dsh-resume-')),h=host();let calls=0;let k=await rig(async function*(){calls++;yield*text(final);},root);try{await delegateLocal(k,h,{goal:'恢复验证'});}finally{await k.close();}k=await rig(async function*(){throw Error('must not replay');},root);try{assert.deepEqual(await delegateLocal(k,h,{goal:'恢复验证'},{resumeSessionId:sessionIdentity(h.identity)}),final);assert.equal(calls,1);await assert.rejects(delegateLocal(k,host('other'),{goal:'恢复验证'},{resumeSessionId:sessionIdentity(h.identity)}),/ownership/);}finally{await k.close();}});
test('model failure and malformed final cannot be mistaken for success',async()=>{const h=host(),k=await rig(async function*(){throw Error('network disconnected');});try{const r=await delegateLocal(k,h,{goal:'失败'});assert.match(r,/委托未完成/);assert.equal(h.state.reservations[0].state,'unknown');}finally{await k.close();}const k2=await rig(async function*(){yield*text({executionLog:'must never leak'});});try{await assert.rejects(delegateLocal(k2,host(),{goal:'格式错误'}),/Structured final report/);}finally{await k2.close();}});
test('native cancellation, timeout and budget denial fail closed',async()=>{const wait=async function*(o){await new Promise((_,reject)=>{o.signal.addEventListener('abort',()=>reject(o.signal.reason),{once:true});});};for(const external of [false,true]){const k=await rig(wait),h=host(),c=new AbortController();try{const p=delegateLocal(k,h,{goal:'取消'},{timeoutMs:external?5000:30,signal:c.signal});if(external)setTimeout(()=>c.abort(),50);const r=await p;assert.match(r,/委托未完成/);assert.match(r,/结果无法确认/);}finally{await k.close();}}let called=false;const k=await rig(async function*(){called=true;yield*text(final);}),h=host();h.reserveModel=async()=>null;try{assert.match(await delegateLocal(k,h,{goal:'预算不足'}),/委托未完成/);assert.equal(called,false);}finally{await k.close();}});


test('dynamic discovery: additions, deletion, rename, incompatible schema and rights are isolated',async()=>{
 for(const mode of ['add','delete','rename','schema','revoke','unavailable']){
  const h=host();let n=0;
  const k=await rig(async function*(o){
   if(!n++){
    if(mode==='add'){h.state.tools.push(tool('new_public_source'));assert(!o.tools.some(x=>x.name==='new_public_source'));}
    if(mode==='delete')h.state.tools=[];
    if(mode==='rename')h.state.tools=[tool('renamed_source')];
    if(mode==='schema')h.state.tools[0].definition.parameters.properties.query.type='number';
    if(mode==='revoke')h.authorize=async()=>false;
    if(mode==='unavailable')h.state.tools[0].available=false;
    yield*call('lookup',{query:'test'});
   }else{const result=o.messages.findLast(m=>m.role==='tool');assert.equal(!!result.isError,mode!=='add');yield*text(final);}
  });
  try{await delegateLocal(k,h,{goal:'动态合同'});assert.equal(h.state.calls.length,mode==='add'?1:0);}finally{await k.close();}
 }
 const a=host('a'),b=host('b');a.state.tools=[tool('read_a')];b.state.tools=[tool('read_b')];
 const k=await rig(async function*(o){assert.equal(o.tools.length,1);assert(['read_a','read_b'].includes(o.tools[0].name));yield*text(final);});
 try{const [ra,rb]=await Promise.all([delegateLocal(k,a,{goal:'A'}),delegateLocal(k,b,{goal:'B'})]);assert.deepEqual(ra,rb);}finally{await k.close();}
});
test('schema validates arguments and outputs; unsupported tool does not break other capabilities',async()=>{
 for(const wrongOutput of [false,true]){
  const h=host();if(wrongOutput)h.execute=async()=>({answer:99});let n=0;
  const k=await rig(async function*(o){if(!n++)yield*call('lookup',{query:wrongOutput?'valid':99});else{assert.equal(o.messages.findLast(m=>m.role==='tool').isError,true);yield*text(final);}});
  try{await delegateLocal(k,h,{goal:'参数与回包验证'});assert.equal(h.state.calls.length,0);}finally{await k.close();}
 }
 const h=host(),bad=tool('bad');bad.definition.parameters={type:'object',properties:{bad:{$ref:'https://external.invalid/schema'}}};h.state.tools.push(bad);
 const k=await rig(async function*(o){assert.deepEqual(o.tools.map(t=>t.name),['lookup']);yield*text(final);});
 try{const r=await delegateLocal(k,h,{goal:'单工具故障'});assert.match(r,/工具合同不可用/);}finally{await k.close();}
});
test('read-only boundary prevents new write grants; duplicate task cannot execute concurrently',async()=>{
 const h=host(),write=tool('new_write');write.effect='write';h.state.tools.push(write);let release,entered;
 const gate=new Promise(r=>release=r),start=new Promise(r=>entered=r);
 const k=await rig(async function*(o){assert.deepEqual(o.tools.map(t=>t.name),['lookup']);entered();await gate;yield*text(final);});
 try{const first=delegateLocal(k,h,{goal:'single'});await start;await assert.rejects(delegateLocal(k,h,{goal:'single'}),/already running/);release();await first;}finally{release();await k.close();}
});

test('next task sees renamed/new tools without changing main delegate schema; large output stays task-local',async()=>{
 const h=host(),schema=JSON.stringify(delegateSchema);h.state.tools=[tool('renamed_source')];let n=0;
 const k=await rig(async function*(o){assert.deepEqual(o.tools.map(t=>t.name),['renamed_source']);if(!n++)yield*call('renamed_source',{query:'large'});else{assert(JSON.stringify(o.messages).length>200000);yield*text(final);}});
 h.execute=async()=>({answer:'x'.repeat(200000)});
 try{const r=await delegateLocal(k,h,{goal:'new tool'});assert.deepEqual(r,final);assert.equal(JSON.stringify(delegateSchema),schema);assert(JSON.stringify(r).length<1000);}finally{await k.close();}
});

test('abrupt process death preserves original session and native unknown without retrying a tool',async()=>{
 const {fork}=await import('node:child_process'),root=await mkdtemp(path.join(os.tmpdir(),'fishbot-dsh-crash-'));
 const child=fork(new URL('./helpers/dsh-crash-child.mjs',import.meta.url),[root],{stdio:['ignore','ignore','inherit','ipc']});
 const exit=new Promise(r=>child.once('exit',r));let timer;
 const ready=await new Promise((resolve,reject)=>{timer=setTimeout(()=>reject(Error('crash child did not reach persisted read')),10000);child.once('message',resolve);child.once('error',reject);child.once('exit',()=>reject(Error('premature child exit')));}).finally(()=>clearTimeout(timer));
 child.kill('SIGKILL');await exit;
 const k=await rig(async function*(){throw Error('must not auto retry an interrupted task');},root),h=host();h.identity=ready.identity;h.state.tools=[];let recovered=[];k.ctx.on('agent/created',({agent})=>{recovered=agent.session.snapshotEvents();});
 try{const r=await delegateLocal(k,h,{goal:'recover crash'},{resumeSessionId:sessionIdentity(h.identity)});assert.match(r,/委托未完成/);assert.match(r,/结果无法确认/);assert.equal(h.state.reservations.length,0);assert.equal(h.state.calls.length,0);const unknown=recovered.find(e=>e.type==='tool/result');assert(unknown?.data.message.isError);assert(JSON.stringify(unknown).includes('UNKNOWN')||JSON.stringify(unknown).includes('unknown'));}finally{await k.close();}
});


test('authoritative host validator retains full tool schema and rejects its constraints',async()=>{
 const h=host();h.state.tools[0].definition.parameters.properties.query.pattern='^public$';
 let validations=0,n=0;h.validateArguments=async(_tool,args)=>{validations++;if(args.query!=='public')throw Error('original validator rejected');};
 const k=await rig(async function*(o){assert.equal(o.tools[0].parameters.properties.query.pattern,'^public$');if(!n++)yield*call('lookup',{query:'invalid'});else{assert(o.messages.findLast(m=>m.role==='tool').isError);yield*text(final);}});
 try{await delegateLocal(k,h,{goal:'full schema'});assert.equal(validations,1);assert.equal(h.state.calls.length,0);}finally{await k.close();}
});

test('revoked effect metadata cannot dispatch; completed session cannot be replaced',async()=>{
 const h=host();let n=0;const root=await mkdtemp(path.join(os.tmpdir(),'fishbot-dsh-identity-'));
 let k=await rig(async function*(o){if(!n++){h.state.tools[0].effect='write';yield*call('lookup',{query:'read'});}else{assert(o.messages.findLast(m=>m.role==='tool').isError);yield*text(final);}},root);
 try{await delegateLocal(k,h,{goal:'original'});assert.equal(h.state.calls.length,0);}finally{await k.close();}
 k=await rig(async function*(){throw Error('must not execute replacement');},root);
 try{await assert.rejects(delegateLocal(k,h,{goal:'different'},{resumeSessionId:sessionIdentity(h.identity)}),/input mismatch/);}finally{await k.close();}
});

test('Bilibili write requires host authorization and preserves original unknown receipt',async()=>{
 const h=host();h.state.tools[0].capability='bilibili';h.state.tools[0].effect='write';h.state.tools[0].definition.output.schema={};
 let n=0;h.authorize=async item=>item.capability==='bilibili'&&item.effect==='write';
 h.execute=async(item,args)=>{h.state.calls.push({item,args});return{execution:'unknown',receipt:{status:'unknown'}};};
 const k=await rig(async function*(o){if(!n++)yield*call('lookup',{query:'authorized'});else{const r=o.messages.findLast(m=>m.role==='tool');assert(JSON.stringify(r).includes('unknown'));yield*text(final+'；写操作结果未知，未重试');}});
 try{const r=await delegateLocal(k,h,{goal:'明确授权的B站写入'});assert.equal(h.state.calls.length,1);assert.match(r,/写操作结果未知，未重试/);}finally{await k.close();}
 const denied=host();denied.state.tools[0].capability='bilibili';denied.state.tools[0].effect='write';denied.authorize=async()=>false;
 const k2=await rig(async function*(o){assert.equal(o.tools.length,0);yield*text(final);});
 try{await delegateLocal(k2,denied,{goal:'no grant'});assert.equal(denied.state.calls.length,0);}finally{await k2.close();}
});


test('same Work follow-ups retain native history and replay only their own completed result after restart',async()=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'fishbot-dsh-followup-')),h=host();let requests=0;
 const stream=async function*(o){requests++;const all=JSON.stringify(o.messages);if(all.includes('follow-up-two')){assert(all.includes('follow-up-one'));assert(all.includes('original-objective'));yield*text('第二次追问结论');}else if(all.includes('follow-up-one')){assert(all.includes('original-objective'));yield*text('第一次追问结论');}else yield*text('原任务结论');};
 let k=await rig(stream,root);
 try{
  assert.equal(await delegateLocal(k,h,{goal:'original-objective'}),'原任务结论');
  const opts={resumeSessionId:sessionIdentity(h.identity),followup:{id:'next-call-1'}};
  assert.equal(await delegateLocal(k,h,{goal:'follow-up-one'},opts),'第一次追问结论');
  assert.equal(await delegateLocal(k,h,{goal:'follow-up-one'},opts),'第一次追问结论');
  assert.equal(requests,2);
  await assert.rejects(delegateLocal(k,h,{goal:'changed-body'},opts),/identity conflict/);
  assert.equal(await delegateLocal(k,h,{goal:'follow-up-two'},{...opts,followup:{id:'next-call-2'}}),'第二次追问结论');
 }finally{await k.close();}
 k=await rig(async function*(){throw Error('must not re-execute completed follow-up');},root);
 try{
  assert.equal(await delegateLocal(k,h,{goal:'follow-up-one'},{resumeSessionId:sessionIdentity(h.identity),followup:{id:'next-call-1'}}),'第一次追问结论');
  assert.equal(await delegateLocal(k,h,{goal:'original-objective'},{resumeSessionId:sessionIdentity(h.identity)}),'原任务结论');
  assert.equal(requests,3);
  await assert.rejects(delegateLocal(k,host('another-bot'),{goal:'follow-up-one'},{resumeSessionId:sessionIdentity(h.identity),followup:{id:'next-call-1'}}),/ownership/);
 }finally{await k.close();}
});
