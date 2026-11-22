import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import {execFileSync} from 'node:child_process';
import path from 'node:path';
import {mkdtemp,writeFile} from 'node:fs/promises';
import {createApp} from '../apps/control-api/src/app.js';
import {issueTestInvite} from './helpers/invitations.js';
import {permitModelFixture} from './helpers/model-fixture.js';
import {defaultConfig} from '../packages/contracts/index.js';
import {MockProtocolAdapter} from '../adapters/mock/index.js';

for(const region of ['persona','core_note','tools','facts','relations','expressions','cards','cards_note','cards_legacy'] as const)for(const mode of ['shared','dedicated'] as const)test(`real ${mode} Worker applies only ${region} during an in-flight SDK request`, {timeout:150000},async()=>{
 const cardsRegion=region==='cards'||region==='cards_note'||region==='cards_legacy';
 const cognition=region==='core_note'||region==='facts'||region==='relations'||region==='expressions';
 const seen:any[]=[];let unblock=()=>{};let hold=false,exercisedTool=false;
 const server=http.createServer(async(req,res)=>{
  if(req.method==='GET'){res.setHeader('Content-Type','application/json');res.end(JSON.stringify({data:[{id:'base-fixture'}]}));return;}
  const chunks:Buffer[]=[];for await(const part of req)chunks.push(part);const body=JSON.parse(Buffer.concat(chunks).toString('utf8'));seen.push(body);
  if(hold&&body.model==='base-fixture'){hold=false;await new Promise<void>(resolve=>{unblock=resolve;});}
  if(cardsRegion&&!exercisedTool&&JSON.stringify(body).includes('FIRST_REQUEST')){exercisedTool=true;res.setHeader('Content-Type','application/json');res.end(JSON.stringify({id:'fixture-tool',object:'chat.completion',created:1,model:body.model,choices:[{index:0,message:{role:'assistant',content:null,tool_calls:[{id:'fixture-context-read',type:'function',function:{name:'inspect_context',arguments:'{}'}}]},finish_reason:'tool_calls'}],usage:{prompt_tokens:10,completion_tokens:3,total_tokens:13}}));return;}
  res.setHeader('Content-Type','application/json');res.end(JSON.stringify({id:'fixture-response',object:'chat.completion',created:1,model:body.model,choices:[{index:0,message:{role:'assistant',content:'测试收到。'},finish_reason:'stop'}],usage:{prompt_tokens:10,completion_tokens:3,total_tokens:13}}));
 });
 await new Promise<void>(resolve=>server.listen(0,'127.0.0.1',resolve));permitModelFixture(server);const port=(server.address() as any).port;
 const root=await mkdtemp(path.resolve('.local/web-base-runtime-'));const x=await createApp({root,agent:false});let fixtureBotId:string|undefined;
 const wait=async(fn:()=>Promise<boolean>,label:string,timeout=60000)=>{const deadline=Date.now()+timeout;while(Date.now()<deadline){if(await fn())return;await new Promise(r=>setTimeout(r,100));}throw Error(label+' timed out; evidence root: '+root);};
 try{
  const registration=await x.app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(x.db,2),email:'runtime-base@example.test',name:'test',password:'runtime-base-123'}});assert.equal(registration.statusCode,200,registration.body);const cookies={fish_session:registration.cookies[0].value};
  const config={...defaultConfig,platformModel:false,baseUrl:`http://127.0.0.1:${port}/v1`,model:'base-fixture',characterCards:cardsRegion&&region!=='cards_legacy'?[{id:'one',name:'示例',kind:'speech' as const,content:'OLD_CARD_ONLY',enabled:true}]:[],systemPrompt:region==='persona'?'OLD_PERSONA_ONLY':'STATIC_PERSONA',toolPrompts:{dsh:'OLD_TOOL_PROMPT_ONLY'},proactive:false,behavior:{...defaultConfig.behavior,memory:cognition||region==='cards_note'||region==='cards_legacy',memorySummary:false,burstDebounceMs:0,typingSimulation:false,sendIntervalMs:0,groupParticipationEnabled:false}};
  const created=await x.app.inject({method:'POST',url:'/v1/bots',cookies,payload:{name:'runtime base',protocol:'mock',runtimeMode:mode,expectedUin:'91000001',config,apiKey:'synthetic-runtime-base-key'}});assert.equal(created.statusCode,201,created.body);const id=created.json().id;fixtureBotId=id;
  if(region==='core_note'||region==='cards_note'||region==='cards_legacy'){
   const python=path.resolve(process.env.ASTRBOT_PYTHON||'references/astrbot/.venv/Scripts/python.exe');
   execFileSync(python,['-X','utf8','-c',`import sqlite3,sys; db=sqlite3.connect(sys.argv[1]); db.execute("CREATE TABLE IF NOT EXISTS memory_core_notes(slot INTEGER PRIMARY KEY,text TEXT,scopes TEXT,people TEXT,sources TEXT,updated REAL)"); db.execute("INSERT INTO memory_core_notes VALUES(1,'OLD_CORE_NOTE_ONLY','[\\\"private:12345678\\\"]','[\\\"12345678\\\"]','[]',1)"); db.commit(); db.close()`,path.join(root,'bots',id,'behavior.sqlite')],{windowsHide:true});
  }
  if(region==='facts'){
   const python=path.resolve(process.env.ASTRBOT_PYTHON||'references/astrbot/.venv/Scripts/python.exe');
   execFileSync(python,['-X','utf8','-c',`import sqlite3,sys; db=sqlite3.connect(sys.argv[1]); db.execute("CREATE TABLE facts(scope TEXT,subject TEXT,name TEXT,value TEXT,event TEXT,updated REAL,version INTEGER,active INTEGER,PRIMARY KEY(scope,subject,name,version))"); db.execute("INSERT INTO facts VALUES('private:12345678','12345678','饮料','OLD_CORE_NOTE_ONLY','old-source',1,1,1)"); db.commit(); db.close()`,path.join(root,'bots',id,'behavior.sqlite')],{windowsHide:true});
  }
  if(region==='expressions'){
   const python=path.resolve(process.env.ASTRBOT_PYTHON||'references/astrbot/.venv/Scripts/python.exe');
   execFileSync(python,['-X','utf8','-c',`import sqlite3,sys; db=sqlite3.connect(sys.argv[1]); db.execute("CREATE TABLE social_patterns(scope TEXT,expression TEXT,situation TEXT,sources TEXT,weight REAL,updated REAL,PRIMARY KEY(scope,expression))"); db.execute("INSERT INTO social_patterns VALUES('private:12345678','好耶','OLD_CORE_NOTE_ONLY','[]',1,1)"); db.commit(); db.close()`,path.join(root,'bots',id,'behavior.sqlite')],{windowsHide:true});
  }
  if(region==='relations'){
   const python=path.resolve(process.env.ASTRBOT_PYTHON||'references/astrbot/.venv/Scripts/python.exe');
   execFileSync(python,['-X','utf8','-c',`import sqlite3,sys; sys.path.insert(0,sys.argv[2]); from relationships import SCHEMA; db=sqlite3.connect(sys.argv[1]); db.execute('CREATE TABLE '+SCHEMA); db.execute("INSERT INTO relations(scope,subject,affinity,emotion,expires,last_seen,trust,impression,semantic_updated) VALUES('private:12345678','12345678',5,'平静',0,1,.2,'OLD_CORE_NOTE_ONLY',1)"); db.commit(); db.close()`,path.join(root,'bots',id,'behavior.sqlite'),path.resolve('adapters/astrbot')],{windowsHide:true});
  }
  await x.db.query("UPDATE bots SET desired_state='running',generation=1 WHERE id=$1",[id]);
  let ctx=await x.agent.context((await x.db.query('SELECT * FROM bots WHERE id=$1',[id]))[0]);const mock=x.agent.protocols.mock as MockProtocolAdapter;
  await mock.beginLogin(ctx);await wait(async()=>{try{await mock.call(ctx,'/transition',{state:'online',uin:ctx.expectedUin});return true;}catch{return false;}},'mock');
  await x.agent.aiFor({runtime_mode:mode}).start(ctx);const group=mode==='shared'?await x.agent.shared.group(id):{id};const before=await x.agent.host.inspect(group.id,'ai');assert.ok(before);
  let peer:any;
  if(region==='cards'&&mode==='shared'){
   const made=await x.app.inject({method:'POST',url:'/v1/bots',cookies,payload:{name:'unrelated peer',protocol:'mock',runtimeMode:'shared',expectedUin:'91000002',config:{...config,characterCards:[]},apiKey:'synthetic-peer-key'}});assert.equal(made.statusCode,201,made.body);
   const peerId=made.json().id;await x.db.query("UPDATE bots SET desired_state='running',generation=1 WHERE id=$1",[peerId]);peer=await x.agent.context((await x.db.query('SELECT * FROM bots WHERE id=$1',[peerId]))[0]);
   await mock.beginLogin(peer);await wait(async()=>{try{await mock.call(peer,'/transition',{state:'online',uin:peer.expectedUin});return true}catch{return false}},'peer mock');await x.agent.shared.start(peer);assert.equal((await x.agent.shared.group(peerId)).id,group.id);
  }

  const beforeRequests=seen.length;hold=true;
  await wait(async()=>{try{await mock.call(ctx,'/message',{text:'FIRST_REQUEST',sender:'12345678',messageId:1001});return true;}catch{return false;}},'native OneBot connection');
  await wait(async()=>seen.length>beforeRequests,'first SDK HTTP request');const first=structuredClone(seen.at(-1));if(cardsRegion)assert.equal(JSON.stringify(first).includes('OLD_CARD_ONLY'),region!=='cards_legacy');else if(region!=='tools')assert.ok(JSON.stringify(first).includes(region==='persona'?'OLD_PERSONA_ONLY':'OLD_CORE_NOTE_ONLY'));else assert.ok(!JSON.stringify(first).includes('OLD_TOOL_PROMPT_ONLY'));
  const detail=(await x.app.inject({url:`/v1/bots/${id}`,cookies})).json();
  if(cognition)await x.db.query("UPDATE bots SET applied_revision=revision,observed_state='running' WHERE id=$1",[id]);
  const coreResponse=cognition?await x.app.inject({url:`/v1/bots/${id}/behavior-data`,cookies}):null;
  if(coreResponse)assert.equal(coreResponse.statusCode,200,coreResponse.body);
  const core=(region==='facts'?coreResponse?.json().facts[0]:region==='relations'?coreResponse?.json().relations[0]:region==='expressions'?coreResponse?.json().patterns[0]:coreResponse?.json().persona_core_note);
  const saved=await x.app.inject(cardsRegion?{method:'PUT',url:`/v1/bots/${id}/character-cards`,cookies,payload:{expectedRevision:detail.bot.revision,characterCards:[{id:'one',name:'示例',kind:'speech',enabled:true,...detail.config.characterCards[0],content:'NEW_CARD_ONLY'}]}}:!cognition?{method:'PUT',url:`/v1/bots/${id}/config`,cookies,payload:{expectedRevision:detail.bot.revision,name:'runtime base',expectedUin:'91000001',config:{...detail.config,...(region==='persona'?{systemPrompt:'NEW_PERSONA_ONLY'}:{toolPrompts:{dsh:'NEW_TOOL_PROMPT_ONLY'}})}}}:{method:'POST',url:`/v1/bots/${id}/behavior-data`,cookies,payload:region==='facts'?{action:'edit_fact',scope:'private:12345678',subject:'12345678',name:'饮料',value:'NEW_CORE_NOTE_ONLY',expectedVersion:core.version}:region==='relations'?{action:'edit_relation',scope:core.scope,subject:core.subject,affinity:core.affinity,trust:core.trust,impression:'NEW_CORE_NOTE_ONLY',expectedVersion:core.version}:region==='expressions'?{action:'edit_pattern',scope:core.scope,expression:core.expression,expectedVersion:core.version,expectedEpoch:coreResponse?.json().epoch,situation:'NEW_CORE_NOTE_ONLY',weight:1}:{action:'edit_core_note',text:'NEW_CORE_NOTE_ONLY',expectedVersion:core.version}});assert.equal(saved.statusCode,200,saved.body);
  ctx=await x.agent.context((await x.db.query('SELECT * FROM bots WHERE id=$1',[id]))[0]);
  if(!cognition)assert.equal(saved.json().applied,true,'normal authenticated web save applies without a separate test attach');
  else if(region==='core_note')assert.equal(saved.json().updatedRegion,'core_note');
  else assert.equal(saved.json().ok,true);
  const after=await x.agent.host.inspect(group.id,'ai');assert.equal(after?.pid,before?.pid);
  unblock();
  await wait(async()=>((await mock.call(ctx,'/messages')) as any[]).some(row=>row.action==='send_private_msg'),'in-flight reply survives edit');
  assert.deepEqual(seen[beforeRequests],first,'in-flight Wire stays unchanged');
  await mock.call(ctx,'/message',{text:'SECOND_REQUEST',sender:'12345678'});
  await wait(async()=>seen.some(body=>JSON.stringify(body).includes('SECOND_REQUEST')),'next request');
  // Observing send_private_msg is earlier than the protocol receipt and Work
  // settlement. A base edit must not leave either real input blocked_unknown.
  const python=path.resolve(process.env.ASTRBOT_PYTHON||'references/astrbot/.venv/Scripts/python.exe');
  await wait(async()=>{const statuses:string[]=JSON.parse(execFileSync(python,['-c',`import sqlite3,json,sys; d=sqlite3.connect('file:'+sys.argv[1]+'?mode=ro',uri=True); print(json.dumps([r[0] for r in d.execute('SELECT status FROM pipeline_work_inputs')]))`,path.join(root,'bots',id,'behavior.sqlite')],{windowsHide:true,encoding:'utf8'}));return statuses.length===2&&statuses.every(status=>status.startsWith('settled'));},'both inputs settle after user edit');
  const next=seen.find(body=>JSON.stringify(body).includes('SECOND_REQUEST'));
  if(cardsRegion){assert.equal(next.messages.filter((m:any)=>m.role==='system'&&m.content.includes('NEW_CARD_ONLY')).length,1);assert.ok(!JSON.stringify(next).includes('OLD_CARD_ONLY'));const cardIndex=next.messages.findIndex((m:any)=>m.content?.includes?.('NEW_CARD_ONLY'));const personaIndex=next.messages.findIndex((m:any)=>m.content?.includes?.('STATIC_PERSONA'));assert.ok(cardIndex<personaIndex);if(region==='cards_note'||region==='cards_legacy'){const noteIndex=next.messages.findIndex((m:any)=>m.content?.includes?.('OLD_CORE_NOTE_ONLY'));assert.ok(noteIndex>0&&noteIndex<cardIndex);}}else if(region!=='tools'){assert.ok(JSON.stringify(next).includes(region==='persona'?'NEW_PERSONA_ONLY':'NEW_CORE_NOTE_ONLY'));assert.ok(!JSON.stringify(next).includes(region==='persona'?'OLD_PERSONA_ONLY':'OLD_CORE_NOTE_ONLY'));}else{assert.ok(!JSON.stringify(next).includes('NEW_TOOL_PROMPT_ONLY'));assert.equal(ctx.config.toolPrompts.dsh,'NEW_TOOL_PROMPT_ONLY');}
  const newMarker=cardsRegion?'NEW_CARD_ONLY':region==='persona'?'NEW_PERSONA_ONLY':region==='tools'?'NEW_TOOL_PROMPT_ONLY':'NEW_CORE_NOTE_ONLY';
  assert.equal(JSON.stringify(next).split(newMarker).length-1,region==='tools'?0:1,'edited base information has one Wire owner');
  assert.ok(!JSON.stringify(next).includes('\"user_edit\":'),'program-only edit provenance never enters the model');
  console.info('base_wire_metrics',JSON.stringify({region,mode,beforeMessages:first.messages.length,afterMessages:next.messages.length,baseCopies:JSON.stringify(next).split(newMarker).length-1,workerRestarted:after?.pid!==before?.pid}));
  if(process.env.FISHBOT_WIRE_EVIDENCE)await writeFile(process.env.FISHBOT_WIRE_EVIDENCE+'.'+region+'.'+mode+'.json',JSON.stringify({before:first,after:next}));
  const beforeTail=first.messages.slice(2);
  if(!cardsRegion){
  for(let i=0;i<beforeTail.length;i++){
   if(cardsRegion&&JSON.stringify(beforeTail[i]).includes('OLD_CARD_ONLY'))continue;
   if(cognition&&JSON.stringify(beforeTail[i]).includes('OLD_CORE_NOTE_ONLY'))continue;
   assert.deepEqual(next.messages[2+i],beforeTail[i],'unrelated Journal message is unchanged');
  }
  assert.deepEqual(next.messages[0],first.messages[0],'platform System is unchanged');
  }
  assert.equal(seen.filter(body=>JSON.stringify(body).includes('SECOND_REQUEST')).length,1);
  if(cardsRegion){
   const owned=(m:any)=>typeof m.content==='string'&&(m.content.startsWith('[FishBot 角色卡] ')||m.content.startsWith('[FishBot 网页 Persona 参考 v1]')||m.content.startsWith('[FishBot 上下文固定基础区 v1]')&&JSON.parse(m.content.split('\n').slice(1).join('\n')).region==='core_note');
   const toolIndex=next.messages.findIndex((m:any)=>m.role==='tool'&&m.tool_call_id==='fixture-context-read');assert.ok(toolIndex>0);assert.equal(next.messages[toolIndex-1].role,'assistant');assert.equal(next.messages[toolIndex-1].tool_calls[0].id,'fixture-context-read');
   const dynamic=first.messages.filter((m:any)=>!owned(m));assert.deepEqual(next.messages.filter((m:any)=>!owned(m)).slice(0,dynamic.length),dynamic);
   let previous=next,sequence=0;
   const original={id:'one',name:'示例',kind:'speech',content:'NEW_CARD_ONLY',enabled:true};
    const five=Array.from({length:5},(_,i)=>({...original,id:String(i),content:('CARD_CONTENT_'+i+'\u0001'.repeat(4000)).slice(0,4000)}));
   const variants=[five,[...five].reverse(),five.filter((_,i)=>i!==2),five.map(c=>({...c,enabled:false})),[],[original]];
   for(const variant of variants){
    await wait(async()=>{const statuses:string[]=JSON.parse(execFileSync(python,['-c',`import sqlite3,json,sys; d=sqlite3.connect('file:'+sys.argv[1]+'?mode=ro',uri=True); print(json.dumps([r[0] for r in d.execute('SELECT status FROM pipeline_work_inputs')]))`,path.join(root,'bots',id,'behavior.sqlite')],{windowsHide:true,encoding:'utf8'}));return statuses.every(s=>s.startsWith('settled'));},'work settled before next edit');
    const current=(await x.app.inject({url:`/v1/bots/${id}`,cookies})).json();
    const response=await x.app.inject({method:'PUT',url:`/v1/bots/${id}/character-cards`,cookies,payload:{expectedRevision:current.bot.revision,characterCards:variant}});assert.equal(response.statusCode,200,response.body);assert.equal(response.json().applied,true);
    ctx=await x.agent.context((await x.db.query('SELECT * FROM bots WHERE id=$1',[id]))[0]);
    const marker='CARD_CYCLE_'+(++sequence);await mock.call(ctx,'/message',{text:marker,sender:'12345678'});
    await wait(async()=>seen.some(body=>JSON.stringify(body).includes(marker)),'card cycle Wire');
    const wire=seen.find(body=>JSON.stringify(body).includes(marker));
    assert.deepEqual(wire.messages.filter((m:any)=>m.role==='system'&&m.content.startsWith('[FishBot 角色卡] ')).map((m:any)=>m.content),variant.filter(c=>c.enabled).map(c=>'[FishBot 角色卡] '+c.name+'\n'+c.content));
    const preserved=previous.messages.filter((m:any)=>!owned(m));assert.deepEqual(wire.messages.filter((m:any)=>!owned(m)).slice(0,preserved.length),preserved,'all unrelated messages retain bytes, role, order and boundaries');
    assert.deepEqual(wire.messages[0],first.messages[0]);previous=wire;
   }
   assert.equal((await x.agent.host.inspect(group.id,'ai'))?.pid,before?.pid);
   if(region==='cards_note'){
    await wait(async()=>{const statuses:string[]=JSON.parse(execFileSync(python,['-c',`import sqlite3,json,sys; d=sqlite3.connect('file:'+sys.argv[1]+'?mode=ro',uri=True); print(json.dumps([r[0] for r in d.execute('SELECT status FROM pipeline_work_inputs')]))`,path.join(root,'bots',id,'behavior.sqlite')],{windowsHide:true,encoding:'utf8'}));return statuses.every(s=>s.startsWith('settled'));},'settled before independent Persona edit');
    const current=(await x.app.inject({url:`/v1/bots/${id}`,cookies})).json();const personaSave=await x.app.inject({method:'PUT',url:`/v1/bots/${id}/config`,cookies,payload:{expectedRevision:current.bot.revision,name:current.bot.name,expectedUin:'91000001',config:{...current.config,systemPrompt:'AFTER_CARD_PERSONA'}}});assert.equal(personaSave.statusCode,200,personaSave.body);assert.equal(personaSave.json().applied,true);
    ctx=await x.agent.context((await x.db.query('SELECT * FROM bots WHERE id=$1',[id]))[0]);await mock.call(ctx,'/message',{text:'PERSONA_AFTER_CARDS',sender:'12345678'});await wait(async()=>seen.some(body=>JSON.stringify(body).includes('PERSONA_AFTER_CARDS')),'independent Persona Wire');const last=seen.find(body=>JSON.stringify(body).includes('PERSONA_AFTER_CARDS'));assert.equal(JSON.stringify(last).split('NEW_CARD_ONLY').length-1,1);assert.equal(JSON.stringify(last).split('OLD_CORE_NOTE_ONLY').length-1,1);assert.equal(JSON.stringify(last).split('AFTER_CARD_PERSONA').length-1,1);
   }
   if(peer){const health=await x.agent.shared.probe(peer);assert.equal(health.ready,true,'unrelated shared member remains at its own revision');assert.equal(health.pid,before?.pid);await mock.stop(peer.botId);await x.agent.shared.stop(peer.botId)}
   if(region==='cards'){
    await wait(async()=>{const statuses:string[]=JSON.parse(execFileSync(python,['-c',`import sqlite3,json,sys; d=sqlite3.connect('file:'+sys.argv[1]+'?mode=ro',uri=True); print(json.dumps([r[0] for r in d.execute('SELECT status FROM pipeline_work_inputs')]))`,path.join(root,'bots',id,'behavior.sqlite')],{windowsHide:true,encoding:'utf8'}));return statuses.every(s=>s.startsWith('settled'));},'settled before recovery');
    await x.agent.aiFor({runtime_mode:mode}).stop(id);await x.agent.aiFor({runtime_mode:mode}).start(ctx);
    await wait(async()=>{try{await mock.call(ctx,'/message',{text:'RESTORED_CARD_REQUEST',sender:'12345678'});return true}catch{return false}},'restored connection');
    await wait(async()=>seen.some(body=>JSON.stringify(body).includes('RESTORED_CARD_REQUEST')),'restored Journal Wire');
    const restored=seen.find(body=>JSON.stringify(body).includes('RESTORED_CARD_REQUEST'));assert.equal(restored.messages.filter((m:any)=>m.role==='system'&&m.content.includes('NEW_CARD_ONLY')).length,1);
    const preserved=previous.messages.filter((m:any)=>!owned(m));assert.deepEqual(restored.messages.filter((m:any)=>!owned(m)).slice(0,preserved.length),preserved,'recovered Journal preserves the prior prefix');
   }
   console.info('card_cycle_wire',JSON.stringify({mode,region,variants:variants.length,historyPreserved:true,hotUpdateRestarted:false,recoveryTest:region==='cards'}));
  }
 }catch(error){await writeFile(path.join(root,'wire-fixture.json'),JSON.stringify(seen,null,2));console.error('Runtime evidence:',root,error);throw error;}finally{unblock();if(fixtureBotId)await x.agent.protocols.mock.stop(fixtureBotId);for(const key of [...x.agent.host.children.keys()]){const split=key.lastIndexOf(':');await x.agent.host.stop(key.slice(0,split),key.slice(split+1));}await x.app.close();server.closeAllConnections();await new Promise<void>(resolve=>server.close(()=>resolve()));}
});
