import test from 'node:test';import assert from 'node:assert/strict';
import {dshParentMatches,reserveDshModel,DshHost} from '../apps/node-agent/src/dsh-host.js';

test('autonomous delegation identity retains the original epoch and service principal',()=>{
 const bot={id:'bot',tenant_id:'tenant',generation:1,revision:2};
 const parent={bot_id:'bot',tenant_id:'tenant',generation:1,config_revision:2,authority:'autonomous',work_id:'cycle',mode_epoch:3,origin_scope:'self',origin_event:'cycle',sender_id:'service:bot'};
 assert(dshParentMatches(bot,parent));
 for(const change of [{mode_epoch:undefined},{mode_epoch:0},{mode_epoch:1.5},{sender_id:'12345'},{sender_id:'service:other'},{authority:'forged'},{tenant_id:'foreign'}])assert(!dshParentMatches(bot,{...parent,...change}));
});

test('DSH model borrows the original activity reservation and preserves actual, unknown and undispatched receipts',async()=>{
 for(const [state,usage,phase,tokens] of [['completed',{totalTokens:77},'settled',77],['completed',{inputTokens:30,outputTokens:4},'settled',34],['completed',undefined,'unknown',undefined],['unknown',{totalTokens:77},'unknown',undefined],['not_dispatched',undefined,'released',0]] as const){
  const frames:any[]=[],options:any={messages:[{role:'user',content:'原委托'}],tools:[],maxTokens:8192};
  const reservation=await reserveDshModel(options,async(operation,data,receipt)=>{frames.push({operation,data,receipt});return{maxTokens:640}});
  assert.equal(reservation.maxTokens,640);assert.equal(options.maxTokens,8192);assert.equal(frames[0].operation,'model_budget');assert.equal(frames[0].data.phase,'reserve');assert(frames[0].data.inputTokens>0);assert.equal(frames[0].data.outputTokens,8192);
  await reservation.settle({state,usage});assert.equal(frames[1].receipt,true);assert.equal(frames[1].data.phase,phase);assert.equal(frames[1].data.tokens,tokens);assert.equal(frames[1].data.requestId,frames[0].data.requestId);
 }
 const requests:string[]=[];for(let i=0;i<2;i++)await reserveDshModel({},async(_op,data)=>{requests.push(data.requestId);return{maxTokens:512}});assert.notEqual(requests[0],requests[1]);
 const invalid:any[]=[];await assert.rejects(reserveDshModel({},async(_op,data,receipt)=>{invalid.push({data,receipt});return{maxTokens:100000}}),/budget_cap/);assert.equal(invalid[1].data.phase,'released');assert.equal(invalid[1].receipt,true);
 await assert.rejects(reserveDshModel({},async()=>{throw Error('original cycle budget exhausted')}),/budget exhausted/);
});

test('production autonomous DSH wire obeys current RunControl cap and budget denial prevents provider dispatch',async t=>{
 const {mkdtemp,readFile}=await import('node:fs/promises');const path=await import('node:path');const os=await import('node:os');const http=await import('node:http');
 const root=await mkdtemp(path.join(os.tmpdir(),'dsh-autonomous-budget-')),wire:any[]=[];
 const upstream=http.createServer(async(req,res)=>{let raw='';for await(const chunk of req)raw+=chunk;
  if(req.url==='/autonomous/tool'){res.end(JSON.stringify({valid:true}));return}
  const body=JSON.parse(raw);wire.push(body);res.writeHead(200,{'Content-Type':'text/event-stream'});
  if(!body.messages.some((message:any)=>message.role==='tool')){
   res.write('data: '+JSON.stringify({choices:[{index:0,delta:{role:'assistant',tool_calls:[{index:0,id:'read-source',type:'function',function:{name:'fixture_lookup',arguments:'{}'}}]},finish_reason:null}]})+'\n\n');
   res.write('data: '+JSON.stringify({choices:[{index:0,delta:{},finish_reason:'tool_calls'}],usage:{prompt_tokens:40,completion_tokens:10,total_tokens:50}})+'\n\n');res.end('data: [DONE]\n\n');return;
  }
  for(const row of [{choices:[{index:0,delta:{role:'assistant',content:'授权资料已核实'},finish_reason:null}]},{choices:[{index:0,delta:{},finish_reason:'stop'}],usage:{prompt_tokens:40,completion_tokens:10,total_tokens:50}}])res.write('data: '+JSON.stringify(row)+'\n\n');res.end('data: [DONE]\n\n');
 });await new Promise<void>(resolve=>upstream.listen(0,'127.0.0.1',resolve));t.after(()=>new Promise<void>(resolve=>upstream.close(()=>resolve())));
 const port=(upstream.address() as import('node:net').AddressInfo).port,env={port:process.env.FISHBOT_DSH_PORT,origins:process.env.FISHBOT_MODEL_ALLOWED_PRIVATE_ORIGINS};
 process.env.FISHBOT_DSH_PORT='0';process.env.FISHBOT_MODEL_ALLOWED_PRIVATE_ORIGINS='http://127.0.0.1:'+port;
 t.after(()=>{for(const [key,value] of Object.entries({FISHBOT_DSH_PORT:env.port,FISHBOT_MODEL_ALLOWED_PRIVATE_ORIGINS:env.origins}))if(value===undefined)delete process.env[key];else process.env[key]=value});
 const bot={id:'bot',tenant_id:'tenant',generation:1,revision:2,desired_state:'running',runtime_mode:'dedicated',health:{sampledAt:new Date().toISOString(),protocol:{online:true},ai:{ready:true}}};
 const parent={bot_id:bot.id,tenant_id:bot.tenant_id,generation:1,config_revision:2,authority:'autonomous',mode_epoch:3,work_id:'cycle',origin_scope:'self',origin_event:'cycle',sender_id:'service:bot'};
 // @ts-ignore Native DSH composition is ESM.
 const {createKernel,installedDsh,delegateLocal,sessionIdentity}=await import('../packages/dsh/kernel.mjs');
 const originalIdentity={tenantId:'tenant',botId:'bot',workId:'old-cycle',scope:'self',delegationId:'media-delegate'},originalSession=sessionIdentity(originalIdentity);
 const native=await installedDsh(),{LlmAdapter}=await native.load('dsh-llm');
 class MediaFixture extends LlmAdapter{async *stream(){const args=JSON.stringify({bvid:'BV1GJ411x7h7'});yield{type:'block-start',index:0,blockType:'tool-call'};yield{type:'tool-call-delta',index:0,id:'first-clip',name:'bilibili_analyze',argumentsDelta:args};yield{type:'block-end',index:0,block:{type:'tool-call',id:'first-clip',name:'bilibili_analyze',arguments:args}};yield{type:'finish',reason:'tool-calls'}}}
 const originalHost={identity:originalIdentity,assertCurrent:async()=>{},authorize:async()=>true,reserveModel:async()=>({settle:async()=>{}}),discover:async()=>[{id:'analyze',capability:'bilibili',effect:'read',available:true,version:'1',definition:{name:'bilibili_analyze',description:'Original Worker',parameters:{type:'object',properties:{bvid:{type:'string'}},required:['bvid']},output:{schema:{}}}}],execute:async()=>({kind:'fishbot_dsh_wait',actionId:'old-media'})};
 const originalKernel=await createKernel({storageRoot:path.join(root,'bots',bot.id,'dsh-sessions'),adapter:new MediaFixture()});
 try{assert.equal((await delegateLocal(originalKernel,originalHost,{goal:'核对原媒体'})).kind,'fishbot_dsh_wait')}finally{await originalKernel.close()}
 const media={bot_id:'bot',tenant_id:'tenant',generation:1,config_revision:2,settings_revision:1,account_revision:null,account_uid:null,id:'old-media',work_id:'old-cycle',authority:'autonomous',mode_epoch:3,operation:'analyze',completion_state:'claimed',completion_owner:'media-owner',completion_until:new Date(Date.now()+120000).toISOString(),result:{execution:'completed',receipt:{status:'not_applicable'},data:{analysis:'原Worker片段真实证据00:42'}},continuation:{dsh:{sessionId:originalSession,delegationId:'media-delegate'}}};
 let schedulerOwner=media.completion_owner;
 const agent:any={root,key:Buffer.alloc(32,1),closed:false,context:async()=>({root:path.join(root,'bots',bot.id),apiKey:'synthetic-key',config:{baseUrl:'http://127.0.0.1:'+port+'/v1',model:'fixture',behavior:{ownerUin:'12345'}}}),db:{query:async(sql:string)=>sql.includes('SELECT decision')?[{decision:{continuation:{id:media.id,owner:schedulerOwner,parentWorkId:media.work_id}}}]:sql.includes('SELECT ai_port')?[{ai_port:port-1,token:'ipc'}]:[bot]},bilibili:{repo:{get:async()=>media,pending:async()=>[],settings:async()=>({revision:1,config:{enabled:false,scopes:[],allowAutonomous:false}})},catalog:()=>[]}};
 let sourceExecutions=0;const host=new DshHost(agent,async()=>({discover:async()=>[{id:'fixture_lookup',capability:'web_search',effect:'read',available:true,version:'1',definition:{name:'fixture_lookup',description:'fixture source',parameters:{type:'object',properties:{},additionalProperties:false},output:{schema:{}}}}],validateArguments:async()=>{},execute:async()=>{sourceExecutions++;return{verified:true}},close:async()=>{}}));await host.start();t.after(()=>host.stop());
 const bridge=JSON.parse(await readFile(path.join(root,'bots',bot.id,'ai/dsh-bridge.json'),'utf8'));
 for(const scenario of ['allowed','denied','media']){
  const denied=scenario==='denied',isMedia=scenario==='media';
  const response=await fetch(bridge.url,{method:'POST',headers:{Authorization:'Bearer '+bridge.token,'Content-Type':'application/json'},body:JSON.stringify(isMedia?{protocol:2,parent:{...parent,work_id:'new-cycle',origin_event:'new-cycle'},delegationId:'media-delegate',completion:{id:media.id,owner:media.completion_owner}}:{protocol:2,parent,delegationId:denied?'denied':'allowed',input:{goal:'查证'}})});
  assert.equal(response.status,200);let buffer='',final='',reserves=0,settled=0,toolBudget=0;
  for await(const chunk of response.body!){buffer+=Buffer.from(chunk).toString();let newline;
   while((newline=buffer.indexOf('\n'))>=0){const frame=JSON.parse(buffer.slice(0,newline));buffer=buffer.slice(newline+1);
    if(frame.type==='tool'){
     if(frame.operation==='tool_budget')toolBudget++;const budget=frame.operation==='model_budget',reserve=budget&&frame.phase==='reserve';if(reserve)reserves++;if(budget&&frame.phase==='settled'){settled++;assert.equal(frame.tokens,50)}
     const ok=!(denied&&reserve),value=reserve?{maxTokens:640}:budget?{ok:true}:[];
     const ack=await fetch(bridge.url.replace('/delegate','/tool-result'),{method:'POST',headers:{Authorization:'Bearer '+bridge.token,'Content-Type':'application/json'},body:JSON.stringify({id:frame.id,ok,value})});assert.equal(ack.status,200);
    }else{assert.equal(frame.type,'final');final=frame.text}
   }
  }
  assert.equal(reserves,scenario==='allowed'?2:1);assert.equal(settled,denied?0:scenario==='allowed'?2:1);assert.equal(toolBudget,scenario==='allowed'?1:0);assert.match(final,denied?/委托未完成/:/授权资料已核实/);
 }
 schedulerOwner='different-current-cycle-completion-owner';
 const forged=await fetch(bridge.url,{method:'POST',headers:{Authorization:'Bearer '+bridge.token,'Content-Type':'application/json'},body:JSON.stringify({protocol:2,parent:{...parent,work_id:'new-cycle',origin_event:'new-cycle'},delegationId:'media-delegate',completion:{id:media.id,owner:media.completion_owner}})});
 assert.equal(forged.status,409);assert.deepEqual(await forged.json(),{error:'dsh_delegation_failed'});
 assert.equal(sourceExecutions,1);assert.equal(wire.length,3);for(const request of wire)assert.equal(request.max_tokens??request.max_completion_tokens,640);assert(JSON.stringify(wire[2].messages).includes('00:42'));assert(!JSON.stringify(wire[0].messages).includes('00:42'));
});
