import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp} from 'node:fs/promises';
import os from 'node:os';import path from 'node:path';
import {installedDsh,createKernel,delegateLocal} from '../packages/dsh/kernel.mjs';
import {nativePublicWeb} from '../scripts/dsh-prototype/native-web.mjs';

test('native web provider blocks nonpublic addresses without disabling its guard',async()=>{
 const native=await installedDsh(),web=await nativePublicWeb(native);
 try{const [tool]=await web.discover();assert(tool);await assert.rejects(web.execute(tool,{url:'http://127.0.0.1:8787'},{callId:'ssrf',signal:new AbortController().signal}),/private|non-public|loopback|blocked/i);}finally{await web.close();}
});

test('native DSH delegates a real public webpage read and returns only final evidence',{skip:process.env.FISHBOT_DSH_PUBLIC_READ!=='1'},async()=>{
 const native=await installedDsh(),web=await nativePublicWeb(native),{LlmAdapter}=await native.load('dsh-llm'),[tool]=await web.discover();let turns=0,reads=0;
 const url='https://1.1.1.1/cdn-cgi/trace';
 class Fixture extends LlmAdapter{async *stream(o){if(!turns++){
  assert(o.tools.some(v=>v.name===tool.definition.name));const args=JSON.stringify({url});
  yield{type:'block-start',index:0,blockType:'tool-call'};yield{type:'tool-call-delta',index:0,id:'read',name:tool.definition.name,argumentsDelta:args};yield{type:'block-end',index:0,block:{type:'tool-call',id:'read',name:tool.definition.name,arguments:args}};yield{type:'finish',reason:'tool-calls'};
 }else{assert(!o.messages.findLast(m=>m.role==='tool').isError);const text='公开页面读取完成。来源：'+url;yield{type:'block-start',index:0,blockType:'text'};yield{type:'text-delta',index:0,text};yield{type:'block-end',index:0,block:{type:'text',text}};yield{type:'finish',reason:'stop'};}}}
 const kernel=await createKernel({storageRoot:await mkdtemp(path.join(os.tmpdir(),'fishbot-dsh-web-')),adapter:new Fixture()});
 try{const result=await delegateLocal(kernel,{identity:{tenantId:'fixture',botId:'web',workId:'local-read',scope:'private:fixture',delegationId:'web-read'},assertCurrent:async()=>{},authorize:async()=>true,reserveModel:async()=>({settle:async()=>{}}),discover:web.discover,execute:async(...args)=>{const value=await web.execute(...args);assert.equal(value.statusCode,200);reads++;return value;}},{goal:'读取指定公开页面，只报告是否成功和来源',background:url});assert.equal(reads,1);assert.equal(typeof result,'string');assert(result.includes(url));console.log(JSON.stringify({nativeWebRead:true,paidModelCalls:0,rawBodyLogged:false}));}finally{await kernel.close();await web.close();}
});
