import {permitModelFixture} from './helpers/model-fixture.js';
import {issueTestInvite} from './helpers/invitations.js';
// Real AstrBot SDK + OneBot WS; deterministic HTTP model barriers, no real QQ.
import test from 'node:test';import assert from 'node:assert/strict';import http from 'node:http';import path from 'node:path';import {mkdtemp} from 'node:fs/promises';
import {createApp} from '../apps/control-api/src/app.js';import {defaultConfig} from '../packages/contracts/index.js';import {MockProtocolAdapter} from '../adapters/mock/index.js';
const sleep=(ms:number)=>new Promise(r=>setTimeout(r,ms));async function wait(fn:()=>Promise<boolean>,label:string){for(let i=0;i<180;i++){if(await fn())return;await sleep(100)}throw new Error(label)}
test('shared native requests held across apply, stop and delete never send stale responses or disrupt peer',{timeout:100000},async()=>{
 let hold:http.ServerResponse|undefined;let requests=0;
 const server=http.createServer(async(req,res)=>{let raw='';for await(const b of req)raw+=b;const body=JSON.parse(raw);requests++;if(JSON.stringify(body.messages.at(-1)).includes('HOLD')){hold=res;return}reply(res)});
 function reply(res:http.ServerResponse){if(!res.destroyed)res.end(JSON.stringify({id:'barrier',model:'fixture',choices:[{index:0,message:{role:'assistant',content:'正常回复'},finish_reason:'stop'}]}))}
 await new Promise<void>(r=>server.listen(0,'127.0.0.1',r));permitModelFixture(server);const current=await createApp({root:await mkdtemp(path.resolve('.local/shared-fence-'))});const {app,db,agent}=current;
 try{
  const auth=await app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(db,2),email:'shared-fence@test.invalid',name:'barrier',password:'shared-fence-123'}});const cookies={fish_session:auth.cookies[0].value};const call=(method:any,url:string,payload?:any)=>app.inject({method,url,payload,cookies});const ids:string[]=[];
  for(let n=0;n<2;n++){const id=(await call('POST','/v1/bots',{name:'barrier'+n,runtimeMode:'shared',protocol:'mock',expectedUin:'9000877'+n,apiKey:'barrier-key-'+n,config:{...defaultConfig,model:'fixture',baseUrl:`http://127.0.0.1:${(server.address()as any).port}/v1`}})).json().id;ids.push(id);await call('POST',`/v1/bots/${id}/actions`,{action:'start',idempotencyKey:'start'})}
  await wait(async()=>(await db.query('SELECT * FROM bots')).every(b=>b.health?.ai?.ready&&b.health?.protocol?.ready),'ready');
  const contexts=await Promise.all(ids.map(async id=>agent.context((await db.query('SELECT * FROM bots WHERE id=$1',[id]))[0])));const mock=agent.protocols.mock as MockProtocolAdapter;
  const connect=async(n:number)=>{await mock.call(contexts[n],'/transition',{state:'online',uin:contexts[n].expectedUin});await wait(async()=>!!(await mock.call(contexts[n],'/state')as any).connected,'connected')};await connect(0);await connect(1);
  const actions=async(n:number)=>(await mock.call(contexts[n],'/messages')as any[]).filter(m=>m.action.startsWith('send_'));
  await mock.call(contexts[0],'/message',{text:'HOLD apply'});await wait(async()=>!!hold,'held model');const old=(await call('GET','/v1/bots/'+ids[0])).json();
  await call('PUT',`/v1/bots/${ids[0]}/config`,{name:'updated',expectedRevision:old.bot.revision,config:{...old.config,systemPrompt:'新配置'},apiKey:'barrier-key-updated'});
  await wait(async()=>(await call('GET','/v1/bots/'+ids[0])).json().bot.applied_revision===2,'applied config');reply(hold!);hold=undefined;await connect(0);await sleep(300);assert.equal((await actions(0)).length,0,'old model response after apply');
  await mock.call(contexts[1],'/message',{text:'peer remains healthy'});await wait(async()=>(await actions(1)).length>0,'peer response');
  await mock.call(contexts[0],'/message',{text:'HOLD stop'});await wait(async()=>!!hold,'held before stop');await call('POST',`/v1/bots/${ids[0]}/actions`,{action:'stop',idempotencyKey:'stop'});
  await wait(async()=>(await call('GET','/v1/bots/'+ids[0])).json().bot.observed_state==='stopped','stop ack');reply(hold!);hold=undefined;
  await call('POST',`/v1/bots/${ids[0]}/actions`,{action:'start',idempotencyKey:'resume'});await wait(async()=>(await call('GET','/v1/bots/'+ids[0])).json().bot.health?.ai?.ready,'resume');await connect(0);
  // Mock process is new after stop, so its action log must also be empty.
  assert.equal((await actions(0)).length,0);
  await mock.call(contexts[0],'/message',{text:'HOLD delete'});await wait(async()=>!!hold,'held before delete');await call('DELETE','/v1/bots/'+ids[0]);await wait(async()=>(await call('GET','/v1/bots/'+ids[0])).statusCode===404,'delete ack');reply(hold!);hold=undefined;
  const count=(await actions(1)).length;await mock.call(contexts[1],'/message',{text:'peer after delete'});await wait(async()=>(await actions(1)).length>count,'peer survived delete');assert.ok(requests>=5);
 }finally{if(hold)reply(hold);await app.close();server.closeAllConnections();await new Promise<void>(r=>server.close(()=>r()))}
});
