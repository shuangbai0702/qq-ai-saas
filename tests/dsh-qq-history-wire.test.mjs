import test from 'node:test';import assert from 'node:assert/strict';import http from 'node:http';
import {mkdtemp,readFile,readdir} from 'node:fs/promises';import os from 'node:os';import path from 'node:path';
import {nativeModel} from '../packages/dsh/native-model.mjs';
import {createKernel,delegateLocal} from '../packages/dsh/kernel.mjs';
import {prepareHistoryModel} from '../packages/dsh/history-content.mjs';

test('real DSH HTTP wire: history -> see image -> publish; Session retains references, main receives final only',async t=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'dsh-qq-history-wire-'));
 const image=(await readFile('tests/fixtures/model-vision-probe.jpg')).toString('base64');
 const source='qh_'+'a'.repeat(24),rawText='FULL_PRIVATE_HISTORY_SENTINEL 澪的照片';
 const page={kind:'qq_history_page',scope:'private:12345',refs:['b'.repeat(64)],more:false,before:null};
 const pageData={scope:page.scope,entries:[{event_id:'original-photo-event-100',sender_id:'12345',at:'2026-11-20',text:rawText,images:[{source_id:source,position:1}]}]};
 const picture={kind:'qq_history_image',scope:page.scope,source_id:source};
 const tools=[['qq_history_search','chat_history','read',{type:'object',properties:{},additionalProperties:false}],
  ['qq_history_image','chat_history','read',{type:'object',properties:{source_id:{type:'string'}},required:['source_id'],additionalProperties:false}],
  ['hosted_qq_publish','qq_space','write',{type:'object',properties:{content:{type:'string'},image_sources:{type:'array',items:{type:'string'}}},required:['content','image_sources'],additionalProperties:false}]]
  .map(([name,capability,effect,parameters])=>({id:name,capability,effect,available:true,version:'1',definition:{name,description:String(name),parameters,output:{schema:{}}}}));
 const wires=[],executed=[];let materializations=0;
 const server=http.createServer(async(req,res)=>{
  let raw='';for await(const chunk of req)raw+=chunk;const wire=JSON.parse(raw);wires.push(wire);
  const index=wires.length-1;
  assert.equal(wire.messages[0].role,'system');assert(!raw.includes('MAIN_JOURNAL_FULL_REPLAY'));
  if(index>0){assert(raw.includes(rawText));assert(raw.includes('2026-11-20'));}
  const images=wire.messages.flatMap(m=>Array.isArray(m.content)?m.content.filter(p=>p.type==='image_url'):[]);
  assert.equal(images.length,index>1?1:0);
  if(images.length)assert.equal(images[0].image_url.url,'data:image/jpeg;base64,'+image);
  if(index===3){const result=wire.messages.findLast(m=>m.role==='tool');assert.match(result.content,/confirmed/);}
  const name=tools[index]?.id,args=index===0?{}:index===1?{source_id:source}:{content:'这张澪好可爱',image_sources:[source]};
  const delta=index<3?{role:'assistant',tool_calls:[{index:0,id:'call-'+index,type:'function',function:{name,arguments:JSON.stringify(args)}}]}:{role:'assistant',content:'已确认发布带图说说。'};
  res.writeHead(200,{'Content-Type':'text/event-stream'});
  for(const row of [{choices:[{index:0,delta,finish_reason:null}]},{choices:[{index:0,delta:{},finish_reason:index<3?'tool_calls':'stop'}],usage:{prompt_tokens:100,completion_tokens:20,total_tokens:120}}])res.write('data: '+JSON.stringify(row)+'\n\n');
  res.end('data: [DONE]\n\n');
 });
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));t.after(()=>new Promise(resolve=>server.close(resolve)));
 const keyEnv='FISHBOT_DSH_QQ_WIRE_KEY';process.env[keyEnv]='local-fixture';t.after(()=>{delete process.env[keyEnv]});
 const adapter=await nativeModel({baseURL:'http://127.0.0.1:'+server.address().port+'/v1',model:'fixture',keyEnv});t.after(()=>adapter.close());
 const kernel=await createKernel({storageRoot:root,adapter});t.after(()=>kernel.close());
 const assertCurrent=async()=>{};
 const host={identity:{tenantId:'tenant',botId:'bot',workId:'work',scope:page.scope,delegationId:'qq-space'},assertCurrent,
  discover:async()=>tools,authorize:async()=>true,reserveModel:async()=>({settle:async()=>{}}),
  prepareModel:options=>prepareHistoryModel(options,{assertCurrent,registerImages:adapter.registerImages,resolve:async ref=>{
   materializations++;if(ref.kind==='qq_history_page'){assert.deepEqual(ref,page);return{text:JSON.stringify(pageData)};}
   assert.deepEqual(ref,picture);return {text:JSON.stringify(picture),images:[{mimeType:'image/jpeg',width:320,height:180,data:image}]};
  }}),
  execute:async(item,args)=>{executed.push([item.id,args]);return item.id==='qq_history_search'?page:item.id==='qq_history_image'?picture:{execution:'completed',receipt:{status:'confirmed'}}},
 };
 const input={goal:'找到之前澪那张图，配文发空间'};
 assert.equal(await delegateLocal(kernel,host,input),'已确认发布带图说说。');
 assert.deepEqual(executed.map(x=>x[0]),tools.map(x=>x.id));assert.deepEqual(executed[2][1].image_sources,[source]);
 assert.equal(wires.length,4);assert.equal(materializations,5);
 const before=wires.length;
 // Same native request identity returns its recorded final, not another post.
 const {sessionIdentity}=await import('../packages/dsh/kernel.mjs');
 assert.equal(await delegateLocal(kernel,host,input,{resumeSessionId:sessionIdentity(host.identity)}),'已确认发布带图说说。');assert.equal(wires.length,before);assert.equal(executed.length,3);
 for(const file of await readdir(root,{recursive:true})){if(!file.endsWith('.jsonl'))continue;const raw=await readFile(path.join(root,file),'utf8');assert(!raw.includes(rawText));assert(!raw.includes(image));assert(!raw.includes('original-photo-event-100'));}
});

test('history request projection preserves unrelated blocks and stale cancellation, rather than hiding it as a read error',async()=>{
 const reference={kind:'qq_history_page',scope:'private:12345',entries:[]};
 const unrelated={role:'tool',toolCallId:'unrelated',content:[{type:'text',text:'unchanged receipt'}]};
 const input={messages:[{role:'assistant',content:[{type:'tool-call',id:'history',name:'qq_history_search'}]},
  {role:'tool',toolCallId:'history',content:[{type:'text',text:'Untrusted external result:\n'+JSON.stringify(reference)}]},unrelated]};
 let live=true;const check=async()=>{if(!live)throw Error('stale Work')};
 const result=await prepareHistoryModel(input,{resolve:async()=>{throw Error('retired')},assertCurrent:check});
 assert.equal(result.messages[2],unrelated);assert.match(result.messages[1].content[0].text,/退休/);assert(!input.messages[1].content[0].text.includes('不可读取'));
 await assert.rejects(prepareHistoryModel(input,{resolve:async()=>{live=false;throw Error('stale')},assertCurrent:check}),/stale Work/);
});
