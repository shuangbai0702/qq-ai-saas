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

for(const region of ['persona','core_note','tools'] as const)for(const mode of ['shared','dedicated'] as const)test(`real ${mode} Worker applies only ${region} during an in-flight SDK request`, {timeout:150000},async()=>{
 const seen:any[]=[];let unblock=()=>{};let hold=false;
 const server=http.createServer(async(req,res)=>{
  if(req.method==='GET'){res.setHeader('Content-Type','application/json');res.end(JSON.stringify({data:[{id:'base-fixture'}]}));return;}
  let raw='';for await(const part of req)raw+=part;const body=JSON.parse(raw);seen.push(body);
  if(hold&&body.model==='base-fixture'){hold=false;await new Promise<void>(resolve=>{unblock=resolve;});}
  res.setHeader('Content-Type','application/json');res.end(JSON.stringify({id:'fixture-response',object:'chat.completion',created:1,model:body.model,choices:[{index:0,message:{role:'assistant',content:'测试收到。'},finish_reason:'stop'}],usage:{prompt_tokens:10,completion_tokens:3,total_tokens:13}}));
 });
 await new Promise<void>(resolve=>server.listen(0,'127.0.0.1',resolve));permitModelFixture(server);const port=(server.address() as any).port;
 const root=await mkdtemp(path.resolve('.local/web-base-runtime-'));const x=await createApp({root,agent:false});
 const wait=async(fn:()=>Promise<boolean>,label:string,timeout=60000)=>{const deadline=Date.now()+timeout;while(Date.now()<deadline){if(await fn())return;await new Promise(r=>setTimeout(r,100));}throw Error(label+' timed out; evidence root: '+root);};
 try{
  const registration=await x.app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(x.db),email:'runtime-base@example.test',name:'test',password:'runtime-base-123'}});assert.equal(registration.statusCode,200,registration.body);const cookies={fish_session:registration.cookies[0].value};
  const config={...defaultConfig,platformModel:false,baseUrl:`http://127.0.0.1:${port}/v1`,model:'base-fixture',systemPrompt:region==='persona'?'OLD_PERSONA_ONLY':'STATIC_PERSONA',toolPrompts:{dsh:'OLD_TOOL_PROMPT_ONLY'},proactive:false,behavior:{...defaultConfig.behavior,memory:region==='core_note',memorySummary:false,burstDebounceMs:0,typingSimulation:false,sendIntervalMs:0,groupParticipationEnabled:false}};
  const created=await x.app.inject({method:'POST',url:'/v1/bots',cookies,payload:{name:'runtime base',protocol:'mock',runtimeMode:mode,expectedUin:'91000001',config,apiKey:'synthetic-runtime-base-key'}});assert.equal(created.statusCode,201,created.body);const id=created.json().id;
  if(region==='core_note'){
   const python=path.resolve(process.env.ASTRBOT_PYTHON||'references/astrbot/.venv/Scripts/python.exe');
   execFileSync(python,['-X','utf8','-c',`import sqlite3,sys; db=sqlite3.connect(sys.argv[1]); db.execute("CREATE TABLE IF NOT EXISTS memory_core_notes(slot INTEGER PRIMARY KEY,text TEXT,scopes TEXT,people TEXT,sources TEXT,updated REAL)"); db.execute("INSERT INTO memory_core_notes VALUES(1,'OLD_CORE_NOTE_ONLY','[\\\"private:12345678\\\"]','[\\\"12345678\\\"]','[]',1)"); db.commit(); db.close()`,path.join(root,'bots',id,'behavior.sqlite')],{windowsHide:true});
  }
  await x.db.query("UPDATE bots SET desired_state='running',generation=1 WHERE id=$1",[id]);
  let ctx=await x.agent.context((await x.db.query('SELECT * FROM bots WHERE id=$1',[id]))[0]);const mock=x.agent.protocols.mock as MockProtocolAdapter;
  await mock.beginLogin(ctx);await wait(async()=>{try{await mock.call(ctx,'/transition',{state:'online',uin:ctx.expectedUin});return true;}catch{return false;}},'mock');
  await x.agent.aiFor({runtime_mode:mode}).start(ctx);const group=mode==='shared'?await x.agent.shared.group(id):{id};const before=await x.agent.host.inspect(group.id,'ai');assert.ok(before);
  const beforeRequests=seen.length;hold=true;
  await wait(async()=>{try{await mock.call(ctx,'/message',{text:'FIRST_REQUEST',sender:'12345678',messageId:1001});return true;}catch{return false;}},'native OneBot connection');
  await wait(async()=>seen.length>beforeRequests,'first SDK HTTP request');const first=structuredClone(seen.at(-1));if(region!=='tools')assert.ok(JSON.stringify(first).includes(region==='persona'?'OLD_PERSONA_ONLY':'OLD_CORE_NOTE_ONLY'));else assert.ok(!JSON.stringify(first).includes('OLD_TOOL_PROMPT_ONLY'));
  const detail=(await x.app.inject({url:`/v1/bots/${id}`,cookies})).json();
  if(region==='core_note')await x.db.query("UPDATE bots SET applied_revision=revision,observed_state='running' WHERE id=$1",[id]);
  const coreResponse=region==='core_note'?await x.app.inject({url:`/v1/bots/${id}/behavior-data`,cookies}):null;
  if(coreResponse)assert.equal(coreResponse.statusCode,200,coreResponse.body);
  const core=coreResponse?.json().persona_core_note;
  const saved=await x.app.inject(region!=='core_note'?{method:'PUT',url:`/v1/bots/${id}/config`,cookies,payload:{expectedRevision:detail.bot.revision,name:'runtime base',expectedUin:'91000001',config:{...detail.config,...(region==='persona'?{systemPrompt:'NEW_PERSONA_ONLY'}:{toolPrompts:{dsh:'NEW_TOOL_PROMPT_ONLY'}})}}}:{method:'POST',url:`/v1/bots/${id}/behavior-data`,cookies,payload:{action:'edit_core_note',text:'NEW_CORE_NOTE_ONLY',expectedVersion:core.version}});assert.equal(saved.statusCode,200,saved.body);
  ctx=await x.agent.context((await x.db.query('SELECT * FROM bots WHERE id=$1',[id]))[0]);
  if(region!=='core_note')assert.equal(saved.json().applied,true,'normal authenticated web save applies without a separate test attach');
  else assert.equal(saved.json().updatedRegion,'core_note');
  const after=await x.agent.host.inspect(group.id,'ai');assert.equal(after?.pid,before?.pid);
  unblock();
  await wait(async()=>((await mock.call(ctx,'/messages')) as any[]).some(row=>row.action==='send_private_msg'),'in-flight reply survives edit');
  assert.deepEqual(seen[beforeRequests],first,'in-flight Wire stays unchanged');
  await mock.call(ctx,'/message',{text:'SECOND_REQUEST',sender:'12345678'});
  await wait(async()=>seen.some(body=>JSON.stringify(body).includes('SECOND_REQUEST')),'next request');
  const next=seen.find(body=>JSON.stringify(body).includes('SECOND_REQUEST'));
  if(region!=='tools'){assert.ok(JSON.stringify(next).includes(region==='persona'?'NEW_PERSONA_ONLY':'NEW_CORE_NOTE_ONLY'));assert.ok(!JSON.stringify(next).includes(region==='persona'?'OLD_PERSONA_ONLY':'OLD_CORE_NOTE_ONLY'));}else{assert.ok(!JSON.stringify(next).includes('NEW_TOOL_PROMPT_ONLY'));assert.equal(ctx.config.toolPrompts.dsh,'NEW_TOOL_PROMPT_ONLY');}
  const newMarker=region==='persona'?'NEW_PERSONA_ONLY':region==='tools'?'NEW_TOOL_PROMPT_ONLY':'NEW_CORE_NOTE_ONLY';
  assert.equal(JSON.stringify(next).split(newMarker).length-1,region==='tools'?0:1,'edited base information has one Wire owner');
  assert.ok(!JSON.stringify(next).includes('\"user_edit\":'),'program-only edit provenance never enters the model');
  console.info('base_wire_metrics',JSON.stringify({region,mode,beforeMessages:first.messages.length,afterMessages:next.messages.length,baseCopies:JSON.stringify(next).split(newMarker).length-1,workerRestarted:after?.pid!==before?.pid}));
  if(process.env.FISHBOT_WIRE_EVIDENCE)await writeFile(process.env.FISHBOT_WIRE_EVIDENCE+'.'+region+'.'+mode+'.json',JSON.stringify({before:first,after:next}));
  const beforeTail=first.messages.slice(2);
  for(let i=0;i<beforeTail.length;i++){
   if(region==='core_note'&&JSON.stringify(beforeTail[i]).includes('OLD_CORE_NOTE_ONLY'))continue;
   assert.deepEqual(next.messages[2+i],beforeTail[i],'unrelated Journal message is unchanged');
  }
  assert.deepEqual(next.messages[0],first.messages[0],'platform System is unchanged');
  assert.equal(seen.filter(body=>JSON.stringify(body).includes('SECOND_REQUEST')).length,1);
 }catch(error){await writeFile(path.join(root,'wire-fixture.json'),JSON.stringify(seen,null,2));console.error('Runtime evidence:',root,error);throw error;}finally{unblock();for(const key of [...x.agent.host.children.keys()]){const split=key.lastIndexOf(':');await x.agent.host.stop(key.slice(0,split),key.slice(split+1));}await x.app.close();server.closeAllConnections();await new Promise<void>(resolve=>server.close(()=>resolve()));}
});
