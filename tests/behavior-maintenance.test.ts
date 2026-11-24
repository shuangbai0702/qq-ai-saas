import test from 'node:test';
import assert from 'node:assert/strict';
import Fastify from 'fastify';
import childProcess from 'node:child_process';
import {syncBuiltinESMExports} from 'node:module';
import {EventEmitter} from 'node:events';
import {registerBehaviorData} from '../apps/control-api/src/behavior-data.js';
import type {Sql} from '../packages/database/index.js';

test('stopped maintenance holds lifecycle ownership until the helper settles, including failure',async t=>{
 const bot={id:'fixture',tenant_id:'tenant',desired_state:'stopped',observed_state:'stopped'};
 let tail=Promise.resolve();
 const serial=<T>(fn:()=>Promise<T>)=>{const result=tail.then(fn);tail=result.then(()=>{},()=>{});return result};
 const tx:Sql={query:async()=>[{...bot,starting:false}] as any,transaction:fn=>fn(tx),close:async()=>{}};
 const db:Sql={...tx,query:(sql,params)=>serial(()=>tx.query(sql,params)),transaction:fn=>serial(()=>fn(tx))};
 const app=Fastify();
 app.addHook('preHandler',async req=>{(req as any).user={id:'owner'}});
 registerBehaviorData(app,'.local',async()=>({...bot}),{isAiRunning:async()=>false,aiIpcTarget:async()=>{throw Error('no Worker IPC during maintenance')},pythonExecutable:'fixture-python'},db);
 const original=childProcess.execFile;let complete!: (error:Error|null,out:string)=>void;let spawned!:()=>void;
 (childProcess as any).execFile=(...args:any[])=>{complete=args.at(-1);spawned();return{stdin:Object.assign(new EventEmitter(),{end:()=>{}})}};
 syncBuiltinESMExports();
 t.after(async()=>{childProcess.execFile=original;syncBuiltinESMExports();await app.close()});
 for(const fail of [false,true]){
  let announce!:()=>void;const ready=new Promise<void>(resolve=>announce=resolve);spawned=announce;
  const request=app.inject({method:'POST',url:'/v1/bots/fixture/behavior-data',payload:{action:'forget_fact',scope:'group:12345',subject:'12345',name:'drink',expectedVersion:1}});
  await ready;
  let started=false;const start=db.transaction(async()=>{started=true});
  // Drain the in-memory SQL scheduler, with no clock delay or live QQ process.
  await new Promise<void>(resolve=>setImmediate(resolve));
  assert.equal(started,false,'a lifecycle transition cannot pass an unfinished maintenance helper');
  complete(fail?Error('helper failed'):null,JSON.stringify({ok:true}));
  const response=await request;await start;
  assert.equal(response.statusCode,fail?503:200);assert.equal(started,true,'failure releases lifecycle ownership too');
 }
});
