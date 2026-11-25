/** Exercise production NDJSON discovery/execution/materialization with the native SDK. */
import test from 'node:test';import assert from 'node:assert/strict';import http from 'node:http';
import {mkdtemp,readFile,readdir} from 'node:fs/promises';import path from 'node:path';import os from 'node:os';
import {DshHost,DSH_MAIN_REPLY_REMINDER} from '../apps/node-agent/src/dsh-host.js';

test('production DSH host routes QQ history, actual image and unknown publication without leaking raw history to main',async t=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'dsh-qq-host-'));
 const bot={id:'fixture',tenant_id:'tenant',generation:1,revision:2};
 const parent={bot_id:bot.id,tenant_id:bot.tenant_id,generation:1,config_revision:2,authority:'foreground',work_id:'work',origin_scope:'private:12345',origin_event:'request',sender_id:'12345'};
 const image=(await readFile('tests/fixtures/model-vision-probe.jpg')).toString('base64');
 const source='qh_'+'a'.repeat(24),sentinel='HISTORY_CONTENT_NOT_FOR_MAIN',reference={kind:'qq_history_page',scope:parent.origin_scope,refs:['b'.repeat(64)],more:false,before:null};
 const selected={kind:'qq_history_image',scope:parent.origin_scope,source_id:source};
 const items=[
  {id:'qq_history_search',capability:'chat_history',effect:'read',parameters:{type:'object',properties:{},additionalProperties:false}},
  {id:'qq_history_image',capability:'chat_history',effect:'read',parameters:{type:'object',properties:{source_id:{type:'string'}},required:['source_id'],additionalProperties:false}},
  {id:'hosted_qq_publish',capability:'qq_space',effect:'write',parameters:{type:'object',properties:{content:{type:'string'},image_sources:{type:'array',items:{type:'string'}}},required:['content','image_sources'],additionalProperties:false}},
 ].map(({parameters,...item})=>({...item,version:'1',available:true,definition:{name:item.id,description:item.id,parameters,output:{schema:{}}}}));
 let models=0,publications=0,materializations=0,validations=0;
 const upstream=http.createServer(async(req,res)=>{
  let raw='';for await(const chunk of req)raw+=chunk;
  if(req.url==='/autonomous/tool'){const body=JSON.parse(raw);assert.equal(body.operation,'validate_parent');assert.deepEqual(body.data.record,parent);validations++;res.end('{"valid":true}');return;}
  assert.equal(req.url,'/v1/chat/completions');assert.equal(req.headers.authorization,'Bearer synthetic-key');
  const body=JSON.parse(raw),index=models++;
  const pictures=body.messages.flatMap((m:any)=>Array.isArray(m.content)?m.content.filter((p:any)=>p.type==='image_url'):[]);
  assert.equal(pictures.length,index>1?1:0);if(pictures.length)assert.equal(pictures[0].image_url.url,'data:image/jpeg;base64,'+image);
  if(index>0)assert(raw.includes(sentinel));assert(!raw.includes(DSH_MAIN_REPLY_REMINDER));
  if(index===3)assert.match(body.messages.findLast((m:any)=>m.role==='tool').content,/unknown/);
  const args=index===0?{}:index===1?{source_id:source}:{content:'fixture post',image_sources:[source]};
  const delta=index<3?{role:'assistant',tool_calls:[{index:0,id:'call-'+index,type:'function',function:{name:items[index].id,arguments:JSON.stringify(args)}}]}:{role:'assistant',content:'QQ尚未确认发表，先核对空间，不重复提交。'};
  res.writeHead(200,{'Content-Type':'text/event-stream'});
  for(const row of [{choices:[{index:0,delta,finish_reason:null}]},{choices:[{index:0,delta:{},finish_reason:index<3?'tool_calls':'stop'}],usage:{prompt_tokens:100,completion_tokens:20,total_tokens:120}}])res.write('data: '+JSON.stringify(row)+'\n\n');
  res.end('data: [DONE]\n\n');
 });
 await new Promise<void>(resolve=>upstream.listen(0,'127.0.0.1',resolve));t.after(()=>new Promise<void>(resolve=>upstream.close(()=>resolve())));
 const port=(upstream.address() as import('node:net').AddressInfo).port;
 const previous=process.env.FISHBOT_MODEL_ALLOWED_PRIVATE_ORIGINS;process.env.FISHBOT_MODEL_ALLOWED_PRIVATE_ORIGINS='http://127.0.0.1:'+port;
 t.after(()=>{if(previous===undefined)delete process.env.FISHBOT_MODEL_ALLOWED_PRIVATE_ORIGINS;else process.env.FISHBOT_MODEL_ALLOWED_PRIVATE_ORIGINS=previous});
 const live={...bot,desired_state:'running',runtime_mode:'dedicated',health:{sampledAt:new Date().toISOString(),protocol:{online:true},ai:{ready:true}}};
 const agent:any={root,key:Buffer.alloc(32,1),closed:false,
  context:async()=>({root:path.join(root,'bots',bot.id),apiKey:'synthetic-key',config:{baseUrl:`http://127.0.0.1:${port}/v1`,model:'fixture',behavior:{ownerUin:'12345',qzone:true}}}),
  db:{query:async(sql:string)=>sql.includes('SELECT ai_port')?[{ai_port:port-1,token:'ipc'}]:[live]},
  bilibili:{repo:{settings:async()=>({revision:1,config:{enabled:false,scopes:[]}})},catalog:()=>[]}};
 const host=new DshHost(agent,async()=>({discover:async()=>[],close:async()=>{}}),{port:0});await host.start();t.after(()=>host.stop());
 const manifest=JSON.parse(await readFile(path.join(root,'bots',bot.id,'ai/dsh-bridge.json'),'utf8'));
 assert(manifest.capabilities.includes('chat_history'));assert(manifest.capabilities.includes('qq_space'));
 const body={protocol:2,parent,delegationId:'publish',input:{goal:'查到之前那张图配文发空间'}};
 async function submit(){
  const response=await fetch(manifest.url,{method:'POST',headers:{Authorization:'Bearer '+manifest.token,'Content-Type':'application/json'},body:JSON.stringify(body)});
  assert.equal(response.status,200);let buffer='',final='';
  for await(const chunk of response.body!){
   buffer+=Buffer.from(chunk).toString();let index;
   while((index=buffer.indexOf('\n'))>=0){
    const frame=JSON.parse(buffer.slice(0,index));buffer=buffer.slice(index+1);
    if(frame.type==='final'){final=frame.text;continue;}
    assert.equal(frame.type,'tool');let value;
    if(frame.operation==='discover')value=items;
    else if(frame.operation==='execute'){
     if(frame.tool==='qq_history_search')value=reference;
     else if(frame.tool==='qq_history_image'){assert.deepEqual(frame.args,{source_id:source});value=selected;}
     else{assert.equal(frame.tool,'hosted_qq_publish');assert.deepEqual(frame.args.image_sources,[source]);publications++;value={execution:'failed',receipt:{status:'unknown'}};}
    }else{
     assert.equal(frame.operation,'history_content');materializations++;
     if(frame.reference.kind==='qq_history_page'){assert.deepEqual(frame.reference,reference);value={text:JSON.stringify({entries:[{event_id:'native-photo-event',at:'2026-11-20',sender_id:'12345',text:sentinel,images:[{source_id:source,position:1}]}]})};}
     else{assert.deepEqual(frame.reference,selected);value={text:JSON.stringify(selected),images:[{mimeType:'image/jpeg',width:320,height:180,data:image}]};}
    }
    const answered=await fetch(manifest.url.replace('/delegate','/tool-result'),{method:'POST',headers:{Authorization:'Bearer '+manifest.token,'Content-Type':'application/json'},body:JSON.stringify({id:frame.id,ok:true,value})});assert.equal(answered.status,200);
   }
  }
  return final;
 }
 const final=await submit();assert.match(final,/尚未确认/);assert(final.endsWith(DSH_MAIN_REPLY_REMINDER));assert(!final.includes(sentinel));assert(!final.includes(source));assert(!final.includes(image));
 assert.equal(publications,1);assert.equal(models,4);assert.equal(materializations,5);assert(validations>10);
 assert.equal(await submit(),final);assert.equal(publications,1);assert.equal(models,4);
 for(const file of await readdir(path.join(root,'bots',bot.id,'dsh-sessions'),{recursive:true})){
  if(!file.endsWith('.jsonl'))continue;
  const content=await readFile(path.join(root,'bots',bot.id,'dsh-sessions',file),'utf8');assert(!content.includes(sentinel));assert(!content.includes(image));assert(!content.includes('native-photo-event'));
 }
});
