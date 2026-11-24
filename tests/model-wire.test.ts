import test from 'node:test';import assert from 'node:assert/strict';import http from 'node:http';
import {modelHttp,modelRequestHeaders,modelResponsesWire} from '../packages/shared/model-http.js';
import {chatCompletionFromResponses,responsesRequest,responsesStreamToChat} from '../packages/shared/model-wire.js';
import {dshClientHeaders,dshHarnessVersion} from '../packages/shared/model-attribution.js';
import {permitModelFixture} from './helpers/model-fixture.js';

// The identity a Codex-oriented relay allow-lists: product/version (+url).
const HARNESS=/^deepseek-harness\/\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)? \(\+https:\/\/github\.com\/deepseek-ai\/deepseek-harness\)$/;

function withEnv(name:string,value:string|undefined,run:()=>Promise<void>|void){
 const before=process.env[name];
 if(value===undefined)delete process.env[name];else process.env[name]=value;
 return Promise.resolve().then(run).finally(()=>{if(before===undefined)delete process.env[name];else process.env[name]=before});
}

test('model requests enforce one harness identity without mutating caller headers',()=>{
 assert.match(dshClientHeaders()['user-agent'],HARNESS);
 const headers=modelRequestHeaders(new URL('https://relay.example/v1/models'),{Authorization:'Bearer fixture'});
 assert.match(String(headers.get('user-agent')),HARNESS);
 const input=new Headers({'User-Agent':'fixture/1.0',Authorization:'Bearer fixture','Content-Type':'application/json'});
 const output=modelRequestHeaders(new URL('https://relay.example/v1/models'),input);
 assert.equal(output.get('user-agent'),dshClientHeaders()['user-agent']);
 assert.equal(input.get('user-agent'),'fixture/1.0');
 assert.equal(output.get('Authorization'),'Bearer fixture');assert.equal(output.get('Content-Type'),'application/json');
 return withEnv('FISHBOT_DSH_HARNESS_VERSION','0.3.1',()=>{
  assert.equal(dshHarnessVersion(),'0.3.1');
  for(const value of ['not a version','0.3.1\r\nX-Other: injected','１.２.３','1.2.3-'+ 'a'.repeat(100)]){
   process.env.FISHBOT_DSH_HARNESS_VERSION=value;
   assert.equal(dshHarnessVersion(),'0.2.0-rc.2','an unusable override keeps the pinned identity');
  }
 });
});

test('the responses wire is selected only for an explicitly named chat-completions origin',async()=>{
 const chat='https://relay.example/v1/chat/completions';
 assert.equal(modelResponsesWire(new URL(chat)),false,'no operator origin means no rewriting');
 await withEnv('FISHBOT_MODEL_RESPONSES_ORIGINS','https://relay.example',()=>{
  assert.equal(modelResponsesWire(new URL(chat)),true);
  assert.equal(modelResponsesWire(new URL('https://relay.example/v1/models')),false,'only the completion route is bridged');
  assert.equal(modelResponsesWire(new URL('https://relay.example.evil.test/v1/chat/completions')),false,'origin must match exactly');
  assert.equal(modelResponsesWire(new URL('http://relay.example/v1/chat/completions')),false,'scheme is part of the origin');
 });
});

test('header enforcement preserves native context bytes and route across repeated requests',async()=>{
 const body=JSON.stringify({model:'fixture',messages:[
  {role:'system',content:'fixed system'},
  {role:'user',content:[{type:'text',text:'来源与时间 2026-11-22'},{type:'image_url',image_url:{url:'data:image/png;base64,AA=='}}]},
  {role:'assistant',content:null,tool_calls:[{id:'call_1',type:'function',function:{name:'echo',arguments:'{}'}}]},
  {role:'tool',tool_call_id:'call_1',content:'真实回执'},
 ]},null,2);
 const seen:{url?:string;body:string;ua?:string;authorization?:string}[]=[];
 const server=http.createServer(async(req,res)=>{
  const chunks:Buffer[]=[];for await(const chunk of req)chunks.push(chunk);
  seen.push({url:req.url,body:Buffer.concat(chunks).toString('utf8'),ua:req.headers['user-agent'],authorization:req.headers.authorization});
  res.end('{}');
 });
 await new Promise<void>(resolve=>server.listen(0,'127.0.0.1',resolve));
 try{
  permitModelFixture(server);
  await withEnv('FISHBOT_MODEL_RESPONSES_ORIGINS',undefined,async()=>{
   for(const ua of ['SDK/1.0','other/2.0']){
    const response=await modelHttp(`http://127.0.0.1:${(server.address()as any).port}/v1/chat/completions`,{method:'POST',headers:{Authorization:'Bearer fixture','Content-Type':'application/json','User-Agent':ua},body});
    await response.text();
   }
  });
  assert.equal(seen.length,2);
  for(const request of seen){assert.equal(request.body,body);assert.equal(request.url,'/v1/chat/completions');assert.match(request.ua!,HARNESS);assert.equal(request.authorization,'Bearer fixture');}
  assert.equal(seen[0].ua,seen[1].ua);
 }finally{await new Promise<void>(resolve=>server.close(()=>resolve()))}
});

test('a responses-only relay is bridged both ways without changing the caller contract',async()=>{
 const seen:{url?:string;ua?:string;body?:any}[]=[];
 const server=http.createServer(async(req,res)=>{
  let raw='';for await(const chunk of req)raw+=chunk;
  const body=raw?JSON.parse(raw):undefined;seen.push({url:req.url,ua:String(req.headers['user-agent']),body});
  if(!HARNESS.test(String(req.headers['user-agent']))){res.writeHead(403,{'Content-Type':'application/json'});res.end(JSON.stringify({error:{code:'client_not_allowed'}}));return}
  if(req.url?.endsWith('/chat/completions')){res.writeHead(400,{'Content-Type':'application/json'});res.end(JSON.stringify({error:{code:'chat_completions_disabled'}}));return}
  if(!req.url?.endsWith('/responses')){res.writeHead(404);res.end('{}');return}
  if(body?.stream){
   res.writeHead(200,{'Content-Type':'text/event-stream'});
   const send=(event:any)=>res.write('event: '+event.type+'\ndata: '+JSON.stringify(event)+'\n\n');
   send({type:'response.created',response:{id:'resp_fixture'}});
   send({type:'response.output_item.added',output_index:0,item:{type:'function_call',call_id:'call_1',name:'probe_echo'}});
   send({type:'response.function_call_arguments.delta',output_index:0,delta:'{"text":'});
   send({type:'response.function_call_arguments.delta',output_index:0,delta:'"OK"}'});
   send({type:'response.output_text.delta',output_index:1,delta:'OK'});
   send({type:'response.completed',response:{status:'completed',usage:{input_tokens:11,output_tokens:5,total_tokens:16}}});
   res.end();return;
  }
  res.writeHead(200,{'Content-Type':'application/json'});
  res.end(JSON.stringify({id:'resp_fixture',created_at:1700000000,model:'gpt-5.6',status:'completed',output:[
   {type:'reasoning',summary:[]},
   {type:'message',role:'assistant',content:[{type:'output_text',text:'OK'}]},
   {type:'function_call',call_id:'call_1',name:'probe_echo',arguments:'{"text":"OK"}'},
  ],usage:{input_tokens:11,output_tokens:5,total_tokens:16}}));
 });
 await new Promise<void>(resolve=>server.listen(0,'127.0.0.1',resolve));
 try{
  const port=(server.address()as any).port,base='http://127.0.0.1:'+port;
  permitModelFixture(server);
  await withEnv('FISHBOT_MODEL_RESPONSES_ORIGINS','http://127.0.0.1:'+port,async()=>{
   const request={model:'gpt-5.6',max_tokens:64,temperature:0.2,messages:[{role:'system',content:'be brief'},{role:'user',content:'OK'}],
    tools:[{type:'function',function:{name:'probe_echo',description:'Return the word OK',parameters:{type:'object',properties:{text:{type:'string'}},required:['text']}}}],
    tool_choice:{type:'function',function:{name:'probe_echo'}}};
   const response=await modelHttp(base+'/v1/chat/completions',{method:'POST',headers:{Authorization:'Bearer fixture-key','Content-Type':'application/json'},body:JSON.stringify(request)});
   assert.equal(response.status,200);
   const completion:any=await response.json();
   const upstream=seen.at(-1)!;
   assert.equal(upstream.url,'/v1/responses','the disabled chat route is never called');
   assert.match(String(upstream.ua),HARNESS);
   assert.equal(upstream.body.instructions,undefined);
   assert.deepEqual(upstream.body.input,[{type:'message',role:'system',content:[{type:'input_text',text:'be brief'}]},{type:'message',role:'user',content:[{type:'input_text',text:'OK'}]}]);
   assert.equal(upstream.body.max_output_tokens,64);
   assert.equal(upstream.body.temperature,0.2);
   assert.deepEqual(upstream.body.tools,[{type:'function',name:'probe_echo',description:'Return the word OK',parameters:{type:'object',properties:{text:{type:'string'}},required:['text']},strict:false}]);
   assert.deepEqual(upstream.body.tool_choice,{type:'function',name:'probe_echo'});
   assert.equal(upstream.body.stream,undefined);
   assert.equal(completion.object,'chat.completion');
   assert.equal(completion.model,'gpt-5.6');
   assert.equal(completion.choices[0].message.content,'OK');
   assert.equal(completion.choices[0].message.tool_calls[0].id,'call_1');
   assert.equal(completion.choices[0].message.tool_calls[0].function.arguments,'{"text":"OK"}');
   assert.equal(completion.choices[0].finish_reason,'tool_calls');
   assert.deepEqual(completion.usage,{prompt_tokens:11,completion_tokens:5,total_tokens:16});

   const streamed=await modelHttp(base+'/v1/chat/completions',{method:'POST',headers:{Authorization:'Bearer fixture-key','Content-Type':'application/json'},body:JSON.stringify({...request,stream:true,stream_options:{include_usage:true}})});
   assert.equal(streamed.status,200);
   assert.match(String(streamed.headers.get('content-type')),/text\/event-stream/);
   const text=await streamed.text(),frames=text.split('\n\n').map(block=>block.split('\n').find(line=>line.startsWith('data:'))?.slice(5).trim()).filter(Boolean);
   assert.equal(frames.at(-1),'[DONE]');
   const chunks=frames.slice(0,-1).map(frame=>JSON.parse(frame!));
   assert.equal(chunks[0].choices[0].delta.role,'assistant');
   assert.equal(chunks.map(chunk=>chunk.choices[0].delta.content||'').join(''),'OK');
   const calls=chunks.flatMap(chunk=>chunk.choices[0].delta.tool_calls||[]);
   assert.equal(calls[0].id,'call_1');
   assert.equal(calls[0].function.name,'probe_echo');
   assert.equal(calls.map(call=>call.function?.arguments||'').join(''),'{"text":"OK"}');
   const terminal=chunks.at(-1);
   assert.equal(terminal.choices[0].finish_reason,'tool_calls');
   assert.equal(terminal.usage.total_tokens,16);
   assert.equal(seen.at(-1)!.body.stream,true);
  });
 }finally{await new Promise<void>(resolve=>server.close(()=>resolve()))}
});

test('an unlisted origin keeps the native chat route and passes upstream errors through',async()=>{
 const server=http.createServer((req,res)=>{res.writeHead(400,{'Content-Type':'application/json'});res.end(JSON.stringify({error:{code:'chat_completions_disabled'}}))});
 await new Promise<void>(resolve=>server.listen(0,'127.0.0.1',resolve));
 try{
  const port=(server.address()as any).port,base='http://127.0.0.1:'+port;
  permitModelFixture(server);
  const response=await modelHttp(base+'/v1/chat/completions',{method:'POST',headers:{Authorization:'Bearer fixture-key'},body:JSON.stringify({model:'fixture',messages:[]})});
  assert.equal(response.status,400);
  assert.equal((await response.json()as any).error.code,'chat_completions_disabled','a failure is never translated into a fake completion');
 }finally{await new Promise<void>(resolve=>server.close(()=>resolve()))}
});

test('the bridge requires an authoritative terminal status and never invents usage',()=>{
 const output=[{type:'message',content:[{type:'output_text',text:'hi'}]}];
 for(const status of [undefined,'failed','cancelled','in_progress','queued'])assert.throws(()=>chatCompletionFromResponses({status,output},'fallback-model'),/not_complete/);
 const completion:any=chatCompletionFromResponses({status:'completed',output},'fallback-model');
 assert.equal(completion.model,'fallback-model');
 assert.equal(completion.choices[0].message.content,'hi');
 assert.equal(completion.choices[0].finish_reason,'stop');
 assert.equal('usage' in completion,false);
});

test('Responses SSE rejects missing and failed terminals and never completes truncated tool calls',async()=>{
 const call={type:'response.output_item.added',output_index:0,item:{type:'function_call',call_id:'call_1',name:'write_tool'}};
 const read=(events:any[])=>new Response(new Blob([events.map(event=>'data: '+JSON.stringify(event)+'\n\n').join('')]).stream().pipeThrough(responsesStreamToChat('fixture'))).text();
 await assert.rejects(()=>read([call]),/missing_terminal/);
 await assert.rejects(()=>read([call,{type:'response.failed',response:{error:{message:'upstream'}}}]),/responses_failed/);
 await assert.rejects(()=>read([{type:'response.completed',response:{status:'in_progress'}}]),/invalid_terminal/);
 const partial=await read([call,{type:'response.incomplete',response:{status:'incomplete'}}]);
 assert.match(partial,/"finish_reason":"length"/);assert.doesNotMatch(partial,/"finish_reason":"tool_calls"/);
});

test('Responses input preserves independent System regions, Note/card/Persona order and late reminders',()=>{
 const messages=[{role:'system',content:'PLATFORM'},{role:'user',content:'CORE NOTE'},{role:'system',content:'CARD A'},{role:'system',content:'CARD B'},{role:'user',content:'PERSONA'},{role:'user',content:'DIALOGUE'},{role:'developer',content:'PERIODIC REMINDER'}];
 const request=responsesRequest({model:'fixture',messages});
 assert.equal(request.instructions,undefined);
 assert.deepEqual(request.input.map((item:any)=>({role:item.role,content:item.content.map((part:any)=>part.text).join('')})),messages);
});
