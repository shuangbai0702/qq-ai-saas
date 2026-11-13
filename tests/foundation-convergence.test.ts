import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {SingleFlight} from '../packages/shared/single-flight.js';
import {runtimeIpc} from '../packages/shared/runtime-ipc.js';
import {runtimeClient} from '../apps/node-agent/src/autonomous.js';
import {lockOwnedBot} from '../apps/control-api/src/bot-access.js';
import {FavoriteMaintenance} from '../apps/node-agent/src/favorite-maintenance.js';
import {BilibiliExecutor} from '../apps/node-agent/src/bilibili/executor.js';

test('single flight publishes owner before reentry, joins, clears rejection and retries',async()=>{
 const flight=new SingleFlight();let release!:()=>void,calls=0;
 const first=flight.run(()=>{calls++;assert.equal(flight.run(async()=>{calls++}),flight.pending);return new Promise<void>(r=>release=r)});
 assert.equal(flight.run(async()=>{calls++}),first);assert.equal(calls,1);release();await first;assert.equal(flight.pending,undefined);
 await assert.rejects(flight.run(async()=>{throw Error('failed')}),/failed/);assert.equal(flight.pending,undefined);
 await flight.run(async()=>{calls++});assert.equal(calls,2);
});
test('maintenance stop aborts and joins the existing owner, overlapping ticks do not dispatch',async()=>{
 for(const owner of [new FavoriteMaintenance({} as any,'.',Buffer.alloc(32)),new BilibiliExecutor({} as any,'.',Buffer.alloc(32))]){
  let calls=0,release!:()=>void,signal!:AbortSignal;
  owner.run=async(s)=>{calls++;signal=s;await new Promise<void>(r=>release=r)};
  owner.tick();owner.tick();assert.equal(calls,1);
  let stopped=false;const stop=owner.stop().then(()=>{stopped=true});assert.equal(signal.aborted,true);await Promise.resolve();assert.equal(stopped,false);
  release();await stop;owner.tick();assert.equal(calls,1);
 }
});
test('ownership is read again after the row lock; deletion and authorization errors propagate',async()=>{
 const events:string[]=[];const tx={query:async()=>{events.push('lock');return[]}} as any;
 let n=0;const owned=async()=>{events.push('owned');return{id:'b',revision:++n}};
 assert.equal((await lockOwnedBot(tx,owned,{},'b')).revision,2);assert.deepEqual(events,['owned','lock','owned']);
 await assert.rejects(lockOwnedBot(tx,async()=>{throw Object.assign(Error('missing'),{statusCode:404})},{},'b'),{statusCode:404});
});
test('IPC keeps runtime identity headers/body and client-specific status errors',async()=>{
 let received:any;const server=createServer(async(req,res)=>{let raw='';for await(const b of req)raw+=b;received={url:req.url,headers:req.headers,body:raw};res.writeHead(req.url?.includes('reject')?409:200,{'Content-Type':'application/json'});res.end('{"ok":true}')});
 await new Promise<void>(r=>server.listen(0,'127.0.0.1',r));const port=(server.address() as any).port;
 try{
  const db={query:async(sql:string)=>sql.includes('FROM bots')?[{runtime_mode:'shared',group_id:'g',generation:3,revision:7}]:[{health_port:port,token:'synthetic',generation:9}]} as any;
  const call=runtimeClient(db),bot={bot_id:'b',tenant_id:'t',generation:3,revision:7};
  assert.deepEqual(await call(bot,'observe',{x:1},1000),{ok:true});assert.equal(received.url,'/members/b/autonomous/observe');
  assert.equal(received.headers['x-group-generation'],'9');assert.equal(received.headers['x-member-generation'],'3');assert.equal(received.headers['x-member-revision'],'7');assert.equal(received.body,'{"x":1}');
  await assert.rejects(call(bot,'reject',{},1000),/autonomous_ipc_409/);
  const response=await runtimeIpc(port,'/reject',{method:'POST',headers:{},body:false,timeoutMs:1000});assert.equal(response.status,409);assert.equal(received.body,'false');
  await assert.rejects(call({...bot,revision:8},'observe',{},1000),/runtime_identity_changed/);
 }finally{await new Promise<void>(r=>server.close(()=>r()))}
});
