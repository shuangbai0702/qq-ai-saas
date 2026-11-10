import test from 'node:test';import assert from 'node:assert/strict';
import net from 'node:net';import http from 'node:http';import path from 'node:path';
import {mkdtemp,readFile,writeFile,mkdir} from 'node:fs/promises';
import {createApp} from '../apps/control-api/src/app.js';
import {SharedAstrBotAdapter} from '../adapters/astrbot/shared.js';
import {issueTestInvite} from './helpers/invitations.js';

async function listen(server:net.Server,port=0){await new Promise<void>((resolve,reject)=>{server.once('error',reject);server.listen({host:'127.0.0.1',port,exclusive:true},()=>{server.removeListener('error',reject);resolve()})});return(server.address() as net.AddressInfo).port}
async function close(server:net.Server){if(server.listening)await new Promise<void>((resolve,reject)=>server.close(error=>error?reject(error):resolve()))}
async function fixture(t:any,count=1){
 const root=await mkdtemp(path.resolve('.local/shared-health-port-')),{app,db,agent}=await createApp({root,agent:false});t.after(()=>app.close());
 const auth=await app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(db,count),email:'health-port@test.invalid',name:'ports',password:'health-port-pass-123'}});assert.equal(auth.statusCode,200,auth.body);const cookies={fish_session:auth.cookies[0].value},ids:string[]=[];
 for(let i=0;i<count;i++){const created=await app.inject({method:'POST',url:'/v1/bots',cookies,payload:{name:'port '+i,protocol:'mock',runtimeMode:'shared',expectedUin:String(90001000+i)}});assert.equal(created.statusCode,201,created.body);ids.push(created.json().id)}
 return{root,app,db,agent,ids};
}

test('shared allocation rejects DB-reserved group and complete instance ports, and real occupied sockets',async t=>{
 const old=process.env.SHARED_GROUP_CAPACITY;process.env.SHARED_GROUP_CAPACITY='1';t.after(()=>{if(old===undefined)delete process.env.SHARED_GROUP_CAPACITY;else process.env.SHARED_GROUP_CAPACITY=old});
 const{db,agent,ids}=await fixture(t,7),first=await agent.shared.assign(ids[0]);
 const[instance]=await db.query('SELECT ai_port,protocol_port FROM instances WHERE bot_id=$1',[ids[0]]);
 const occupied=net.createServer(),occupiedPort=await listen(occupied);t.after(()=>close(occupied));
 const candidates=[first.health_port,instance.ai_port,instance.ai_port+1,instance.protocol_port,instance.protocol_port+1,occupiedPort];
 for(let i=0;i<candidates.length;i++){
  // Force only the OS's first port-0 answer; the socket still performs a real
  // bind. Remaining fallback candidates use the unmodified operating system.
  const original=net.Server.prototype.listen;let forced=false;
  const patch=t.mock.method(net.Server.prototype,'listen',function(this:net.Server,...args:any[]){if(!forced&&args[0]?.port===0){forced=true;args[0]={...args[0],port:candidates[i]}}return Reflect.apply(original,this,args)});
  let group:any;try{group=await agent.shared.assign(ids[i+1])}finally{patch.mock.restore()}
  assert.equal(forced,true);assert.notEqual(group.health_port,candidates[i]);
  const groups=await db.query('SELECT health_port FROM runtime_groups WHERE id<>$1',[group.id]);assert.ok(!groups.some(g=>g.health_port===group.health_port));
  const instances=await db.query('SELECT ai_port,protocol_port FROM instances');assert.ok(instances.every(v=>![v.ai_port,v.ai_port+1,v.protocol_port,v.protocol_port+1].includes(group.health_port)));
  const probe=net.createServer();try{await listen(probe,group.health_port)}finally{await close(probe)}
 }
});

test('workerless shared restart repairs an occupied health port and preserves group identity and durable files',async t=>{
 const{db,agent,ids,root}=await fixture(t),group=await agent.shared.assign(ids[0]);
 const occupied=net.createServer(),port=await listen(occupied);t.after(()=>close(occupied));
 await db.query('UPDATE runtime_groups SET health_port=$2,generation=9 WHERE id=$1',[group.id,port]);
 const[bot]=await db.query('SELECT * FROM bots WHERE id=$1',[ids[0]]),ctx=await agent.context(bot);
 await mkdir(ctx.root,{recursive:true});const journal=path.join(ctx.root,'journal-preservation-fixture');await writeFile(journal,'durable journal fixture');
 let starts=0;const worker=http.createServer((req,res)=>{res.setHeader('Content-Type','application/json');res.end(JSON.stringify({ready:true,generation:10}))});t.after(()=>close(worker));
 const host:any={root,endInspection:()=>{},inspect:async()=>null,start:async(id:string,kind:string,python:string,args:string[],cwd:string)=>{starts++;assert.equal(id,group.id);const manifest=JSON.parse(await readFile(path.join(cwd,'group.json'),'utf8'));assert.equal(manifest.token,group.token);assert.equal(manifest.generation,10);assert.notEqual(manifest.healthPort,port);await listen(worker,manifest.healthPort)}};
 const adapter=new SharedAstrBotAdapter(db,host,{} as any,'unused','unused',async()=>ctx);let attached=0;adapter.attach=async()=>{attached++};
 await adapter.start(ctx);const[current]=await db.query('SELECT * FROM runtime_groups WHERE id=$1',[group.id]);
 assert.equal(starts,1);assert.equal(attached,1);assert.equal(current.id,group.id);assert.equal(current.token,group.token);assert.equal(current.generation,10);assert.notEqual(current.health_port,port);assert.equal((worker.address() as net.AddressInfo).port,current.health_port);
 assert.equal((await db.query('SELECT group_id FROM bots WHERE id=$1',[bot.id]))[0].group_id,group.id);assert.equal(await readFile(journal,'utf8'),'durable journal fixture');
});

test('fresh owned worker truth preserves its occupied port and generation despite a stale inspection cache',async t=>{
 const{db,agent,ids,root}=await fixture(t),group=await agent.shared.assign(ids[0]);
 const worker=net.createServer(),port=await listen(worker);t.after(()=>close(worker));await db.query('UPDATE runtime_groups SET health_port=$2,generation=9 WHERE id=$1',[group.id,port]);
 const[bot]=await db.query('SELECT * FROM bots WHERE id=$1',[ids[0]]),ctx=await agent.context(bot);let cached=true,starts=0,attached=0;
 const host:any={root,endInspection:()=>{cached=false},inspect:async()=>cached?null:{pid:123,created:456},start:async()=>{starts++}};
 const adapter=new SharedAstrBotAdapter(db,host,{} as any,'unused','unused',async()=>ctx);adapter.attach=async()=>{attached++};await adapter.start(ctx);
 const[current]=await db.query('SELECT * FROM runtime_groups WHERE id=$1',[group.id]);assert.equal(starts,0);assert.equal(attached,1);assert.equal(current.health_port,port);assert.equal(current.generation,9);assert.equal(current.token,group.token);assert.equal(worker.listening,true);
});

for(const arrival of [2,3])test(`owned worker appearing at inspection ${arrival} prevents port replacement and duplicate spawn`,async t=>{
 const{db,agent,ids,root}=await fixture(t),group=await agent.shared.assign(ids[0]);const[bot]=await db.query('SELECT * FROM bots WHERE id=$1',[ids[0]]),ctx=await agent.context(bot);let inspections=0,starts=0;
 const host:any={root,endInspection:()=>{},inspect:async()=>++inspections<arrival?null:{pid:123,created:456},start:async()=>{starts++}};
 const adapter=new SharedAstrBotAdapter(db,host,{} as any,'unused','unused',async()=>ctx);adapter.attach=async()=>{};await adapter.start(ctx);
 const[current]=await db.query('SELECT * FROM runtime_groups WHERE id=$1',[group.id]);assert.equal(inspections,arrival);assert.equal(starts,0);assert.equal(current.health_port,group.health_port);assert.equal(current.generation,group.generation);assert.equal(current.token,group.token);
});
