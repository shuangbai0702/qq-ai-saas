import test from 'node:test';import assert from 'node:assert/strict';import http from 'node:http';
import {mkdtemp} from 'node:fs/promises';import os from 'node:os';import path from 'node:path';
import {nativeModel} from '../packages/dsh/native-model.mjs';
import {createKernel,delegateLocal} from '../packages/dsh/kernel.mjs';

test('installed native adapter sends the corrected output allowance on the actual wire',async t=>{
 let wire;const report={result:'local fixture',evidence:[],unfinished:[],limitations:[]};
 const server=http.createServer(async(req,res)=>{
  let body='';for await(const chunk of req)body+=chunk;wire=JSON.parse(body);
  res.writeHead(200,{'Content-Type':'text/event-stream'});
  for(const value of [{id:'local',object:'chat.completion.chunk',created:0,model:'fixture',choices:[{index:0,delta:{role:'assistant',content:JSON.stringify(report)},finish_reason:null}]},{id:'local',object:'chat.completion.chunk',created:0,model:'fixture',choices:[{index:0,delta:{},finish_reason:'stop'}],usage:{prompt_tokens:10,completion_tokens:8,total_tokens:18}}])res.write('data: '+JSON.stringify(value)+'\n\n');
  res.end('data: [DONE]\n\n');
 });
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));t.after(()=>new Promise(resolve=>server.close(resolve)));
 const name='FISHBOT_DSH_FIXTURE_KEY';process.env[name]='synthetic-local-key';t.after(()=>{delete process.env[name];});
 const adapter=await nativeModel({baseURL:'http://127.0.0.1:'+server.address().port+'/v1',model:'fixture',keyEnv:name});t.after(()=>adapter.close());
 const kernel=await createKernel({storageRoot:await mkdtemp(path.join(os.tmpdir(),'dsh-model-wire-')),adapter});t.after(()=>kernel.close());
 const final=await delegateLocal(kernel,{identity:{tenantId:'local',botId:'local',workId:'local',scope:'private:local',delegationId:'wire'},assertCurrent:async()=>{},discover:async()=>[],authorize:async()=>false,reserveModel:async()=>({settle:async()=>{}})},{goal:'fixture'});
 assert.deepEqual(final,report);assert.equal(wire.max_completion_tokens??wire.max_tokens,8192);
 assert.equal(wire.stream,true);assert.equal(wire.messages[0].role,'system');
});
