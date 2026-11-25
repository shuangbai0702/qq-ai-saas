import test from 'node:test';import assert from 'node:assert/strict';
import {dshToken,dshParentMatches,awaitBilibiliReceipt,delegatedCapabilityEnabled,DSH_MAIN_REPLY_REMINDER} from '../apps/node-agent/src/dsh-host.js';
const bot={id:'bot',tenant_id:'tenant',generation:1,revision:2};
const parent={bot_id:'bot',tenant_id:'tenant',generation:1,config_revision:2,authority:'foreground',work_id:'work',origin_scope:'group:12345',origin_event:'event',sender_id:'12345'};

test('DSH private/group history shares the original history switch; QQ space follows its own feature',()=>{
 assert(delegatedCapabilityEnabled({},'chat_history'));
 assert(!delegatedCapabilityEnabled({delegatedCapabilities:{groupHistory:false}},'chat_history'));
 assert(!delegatedCapabilityEnabled({},'qq_space'));
 assert(delegatedCapabilityEnabled({behavior:{qzone:true}},'qq_space'));
 assert(!delegatedCapabilityEnabled({behavior:{qzone:false}},'qq_space'));
 assert(!delegatedCapabilityEnabled({},'ungranted'));
});

test('model proxy checks Work after the request body await before dispatch',async()=>{
 const {DshHost}=await import('../apps/node-agent/src/dsh-host.js');
 const {Readable}=await import('node:stream');const {EventEmitter}=await import('node:events');
 const host:any=new DshHost({} as any);let bodyRead=false,checks=0;
 const id='fishbot-'+'a'.repeat(64);
 host.modelRoutes.set(id,{token:'token',signal:new AbortController().signal,assertCurrent:async()=>{checks++;assert(bodyRead);throw Error('stale_work')}});
 const req:any=Readable.from((async function*(){yield '{}';bodyRead=true})());
 req.url='/model/'+id+'/v1/chat/completions';req.method='POST';req.headers={authorization:'Bearer token'};
 await assert.rejects(host.modelRequest(req,new EventEmitter()),/stale_work/);
 assert.equal(checks,1);
});
test('production delegation tokens and parent bind tenant, Bot and exact generation/revision',()=>{
 const key=Buffer.alloc(32,1),token=dshToken(key,bot);assert.equal(token.length,64);
 for(const change of [{id:'other'},{tenant_id:'other'},{generation:2},{revision:3}])assert.notEqual(token,dshToken(key,{...bot,...change}));
 assert(dshParentMatches(bot,parent));
 for(const change of [{tenant_id:'other'},{generation:2},{config_revision:3},{authority:'autonomous'},{origin_event:null}])assert(!dshParentMatches(bot,{...parent,...change}));
});
test('production Bilibili waits original receipt, preserves unknown and never claims another task',async()=>{
 let reads=0,checks=0;const result={execution:'failed',receipt:{status:'unknown'}};
 const repo={get:async()=>++reads===1?{state:'queued'}:{state:'unknown',result},claim:()=>{throw Error('must not steal executor lease')}};
 assert.equal(await awaitBilibiliReceipt(repo,bot,'id',async()=>{checks++},new AbortController().signal,Date.now()+1000),result);
 assert.equal(checks,2);assert.equal(reads,2);
 const canceled=new AbortController();canceled.abort(new Error('stale owner'));
 await assert.rejects(awaitBilibiliReceipt(repo,bot,'id',async()=>canceled.signal.throwIfAborted(),canceled.signal,Date.now()+1000),/stale owner/);
 await assert.rejects(awaitBilibiliReceipt({get:async()=>({state:'claimed'})},bot,'id',async()=>{},new AbortController().signal,Date.now()-1),/pending_do_not_repeat/);
});


test('production host uses original model security transport, returns final only, resumes without replay',async t=>{
 const {DshHost}=await import('../apps/node-agent/src/dsh-host.js');
 const {mkdtemp,readFile,access}=await import('node:fs/promises');const path=await import('node:path');const os=await import('node:os');const http=await import('node:http');
 const root=await mkdtemp(path.join(os.tmpdir(),'fishbot-dsh-production-'));
 const final='final only\nhttps://example.com/';let modelCalls=0,visionCalls=0,toolPrompt='INITIAL_TOOL_PROMPT';
 const image=(await readFile(path.join('tests','fixtures','model-vision-probe.jpg'))).toString('base64');
 const upstream=http.createServer(async(req,res)=>{let raw='';for await(const c of req)raw+=c;if(req.url==='/autonomous/tool'){if(live.revision!==bot.revision){assert.equal(req.headers['x-member-revision'],String(live.revision));assert.equal(JSON.parse(raw).requestRevision,bot.revision)}res.end(JSON.stringify({valid:true}));return}
  modelCalls++;assert.equal(req.headers.authorization,'Bearer synthetic-server-key');assert.equal(req.url,'/v1/chat/completions');const body=JSON.parse(raw);assert.equal(body.model,'delegated-fixture');assert(!raw.includes('main Journal'));assert(!raw.includes(DSH_MAIN_REPLY_REMINDER));
  if(body.messages[1]?.content?.some?.((part:any)=>part.type==='image_url')){
   visionCalls++;
   assert.equal(raw.split(toolPrompt).length-1,1);assert(!raw.includes('INITIAL_TOOL_PROMPT'));
   assert.match(body.messages[0].content,/分析提供的图片/);assert.equal(body.messages.length,2);
   assert(!body.tools?.length);
   assert.equal(body.messages[1].content.find((part:any)=>part.type==='image_url').image_url.url,'data:image/jpeg;base64,'+image);
  }
  if(!body.messages[1]?.content?.some?.((part:any)=>part.type==='image_url')){assert(raw.includes(toolPrompt));assert.equal(raw.split(toolPrompt).length-1,1);if(toolPrompt==='UPDATED_TOOL_PROMPT')assert(!raw.includes('INITIAL_TOOL_PROMPT'));}
  const followup=body.messages.some((m:any)=>typeof m.content==='string'&&m.content.includes('followup-goal'));
  if(followup){assert(body.messages.some((m:any)=>typeof m.content==='string'&&m.content.includes('\"goal\":\"fixture\"')));assert(body.messages.some((m:any)=>typeof m.content==='string'&&m.content.includes(final)));}
  const answer=followup?'followup conclusion':final;
  if(modelCalls===1){live.revision=3;toolPrompt='UPDATED_TOOL_PROMPT';}
  res.writeHead(200,{'Content-Type':'text/event-stream'});for(const row of [{choices:[{index:0,delta:{role:'assistant',content:answer},finish_reason:null}]},{choices:[{index:0,delta:{},finish_reason:'stop'}],usage:{prompt_tokens:100,completion_tokens:20,total_tokens:120}}])res.write('data: '+JSON.stringify(row)+'\n\n');res.end('data: [DONE]\n\n');
 });await new Promise<void>(resolve=>upstream.listen(0,'127.0.0.1',resolve));t.after(()=>new Promise<void>(resolve=>upstream.close(()=>resolve())));
 const port=(upstream.address() as import('node:net').AddressInfo).port;
 const env={port:process.env.FISHBOT_DSH_PORT,origins:process.env.FISHBOT_MODEL_ALLOWED_PRIVATE_ORIGINS};process.env.FISHBOT_DSH_PORT='0';process.env.FISHBOT_MODEL_ALLOWED_PRIVATE_ORIGINS='http://127.0.0.1:'+port;
 t.after(()=>{for(const [k,v] of Object.entries({FISHBOT_DSH_PORT:env.port,FISHBOT_MODEL_ALLOWED_PRIVATE_ORIGINS:env.origins}))if(v===undefined)delete process.env[k];else process.env[k]=v});
 const live={...bot,desired_state:'running',runtime_mode:'dedicated',health:{sampledAt:new Date().toISOString(),protocol:{online:true},ai:{ready:true}}};
 const agent:any={root,key:Buffer.alloc(32,1),closed:false,context:async()=>({root:path.join(root,'bots',bot.id),apiKey:'synthetic-server-key',config:{baseUrl:'http://127.0.0.1:'+port+'/v1',model:'fixture',delegationModel:'delegated-fixture',toolPrompts:{dsh:toolPrompt},behavior:{ownerUin:'12345'}}}),
  db:{query:async(sql:string)=>sql.includes('SELECT r.revision,r.config,a.detail')?[{revision:2,config:{toolPrompts:{dsh:'INITIAL_TOOL_PROMPT'}}},{revision:3,config:{toolPrompts:{dsh:'UPDATED_TOOL_PROMPT'}},detail:{source:'authenticated_web_config',actorId:'user',credentialChanged:false}}]:sql.includes('SELECT runtime_mode')?[live]:sql.includes('SELECT ai_port')?[{ai_port:port-1,token:'ipc'}]:[live]},
  bilibili:{repo:{settings:async()=>({revision:1,config:{enabled:false,scopes:[]}})},catalog:()=>[]}};
 const host=new DshHost(agent,async()=>({discover:async()=>[],close:async()=>{}}));await host.start();t.after(()=>host.stop());
 const file=path.join(root,'bots',bot.id,'ai/dsh-bridge.json'),manifest=JSON.parse(await readFile(file,'utf8'));
 const payload={protocol:2,parent,delegationId:'call',input:{goal:'fixture'}};
 const response=(body:any=payload)=>fetch(manifest.url,{method:'POST',headers:{Authorization:'Bearer '+manifest.token,'Content-Type':'application/json'},body:JSON.stringify(body)});
 const denied=await fetch(manifest.url,{method:'POST',headers:{Authorization:'Bearer invalid'},body:JSON.stringify(payload)});assert.equal(denied.status,409);assert.equal(modelCalls,0);
 const followup={...payload,delegationId:'followup-call',continueDelegationId:'call',input:{goal:'followup-goal'}};
 for(const [body,expected] of [[payload,final],[payload,final],[followup,'followup conclusion'],[followup,'followup conclusion']] as const){
  if(body===followup)toolPrompt='UPDATED_TOOL_PROMPT';
  const result=await response(body);assert.equal(result.status,200);let buffer='',text='',finals=0;
  for await(const chunk of result.body!){buffer+=Buffer.from(chunk).toString();let newline;while((newline=buffer.indexOf('\n'))>=0){const frame=JSON.parse(buffer.slice(0,newline));buffer=buffer.slice(newline+1);
   if(frame.type==='tool'){assert.equal(frame.operation,'discover');const answered=await fetch(manifest.url.replace('/delegate','/tool-result'),{method:'POST',headers:{Authorization:'Bearer '+manifest.token,'Content-Type':'application/json'},body:JSON.stringify({id:frame.id,ok:true,value:[]})});assert.equal(answered.status,200)}
   else{assert.equal(frame.type,'final');finals++;text=frame.text}
  }}assert.equal(finals,1);assert.equal(text,expected+'\n\n'+DSH_MAIN_REPLY_REMINDER);assert.equal(text.split(DSH_MAIN_REPLY_REMINDER).length-1,1);
 }assert.equal(modelCalls,2);
 const foreign=await response({...followup,parent:{...parent,origin_scope:'group:54321'}});
 assert.deepEqual(JSON.parse((await foreign.text()).trim()),{type:'error',error:'dsh_delegation_failed'});assert.equal(modelCalls,2);
 assert(manifest.capabilities.includes('image_analysis'));
 const vision=await fetch(manifest.url,{method:'POST',headers:{Authorization:'Bearer '+manifest.token,'Content-Type':'application/json'},body:JSON.stringify({...payload,delegationId:'image-call',imageAnalysis:true})});
 assert.equal(vision.status,200);let buffer='',visionFinal='';
 for await(const chunk of vision.body!){
  buffer+=Buffer.from(chunk).toString();let newline;
  while((newline=buffer.indexOf('\n'))>=0){
   const frame=JSON.parse(buffer.slice(0,newline));buffer=buffer.slice(newline+1);
   if(frame.type==='tool'){
    assert.equal(frame.operation,'vision_images');
    const url=manifest.url.replace('/delegate','/tool-result');
    const headers={Authorization:'Bearer '+manifest.token,'Content-Type':'application/json','X-FishBot-Vision-Callback':frame.id};
    const denied=await fetch(url,{method:'POST',headers:{...headers,Authorization:'Bearer invalid'},body:'{}'});
    assert.equal(denied.status,403);
    const answered=await fetch(url,{method:'POST',headers,body:JSON.stringify({id:frame.id,ok:true,value:{eventId:'event',positions:[2],images:[{mimeType:'image/jpeg',width:320,height:180,data:image}]}})});
    assert.equal(answered.status,200);
   }else{assert.equal(frame.type,'final');visionFinal=frame.text}
  }
 }
 assert(visionFinal.startsWith('DSH 视觉观察，QQ 事件 "event"，图片位置 2。'));
 assert(visionFinal.includes(JSON.stringify(final)));
 assert(visionFinal.endsWith('\n\n'+DSH_MAIN_REPLY_REMINDER));
 assert.equal(visionCalls,1);assert.equal(modelCalls,3);
 // @ts-expect-error the installed DSH kernel bridge is an untyped local ESM module
 const {sessionIdentity}=await import('../packages/dsh/kernel.mjs');
 const visionSession=sessionIdentity({tenantId:bot.tenant_id,botId:bot.id,workId:parent.work_id,scope:parent.origin_scope,delegationId:'image-call'});
 await assert.rejects(access(path.join(root,'bots',bot.id,'dsh-sessions','_no-cwd',visionSession)));
 await host.stop();await host.stop();await assert.rejects(access(file));
});


test('cold boot retires only its own production bridge before optional DSH startup',async()=>{
 const {DshHost}=await import('../apps/node-agent/src/dsh-host.js');const {mkdtemp,mkdir,writeFile,readFile,access}=await import('node:fs/promises');const path=await import('node:path');const os=await import('node:os');
 const root=await mkdtemp(path.join(os.tmpdir(),'dsh-bridge-cold-'));const file=path.join(root,'bots',bot.id,'ai/dsh-bridge.json');await mkdir(path.dirname(file),{recursive:true});
 const agent:any={root,db:{query:async()=>[bot]}};const host=new DshHost(agent);
 await writeFile(file,JSON.stringify({mode:'production',botId:bot.id,tenantId:'foreign'}));await host.clearPreviousBridges();assert((await readFile(file,'utf8')).includes('foreign'));
 await writeFile(file,JSON.stringify({mode:'production',botId:bot.id,tenantId:bot.tenant_id,generation:0}));await host.clearPreviousBridges();await assert.rejects(access(file));
});
