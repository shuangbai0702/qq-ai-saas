import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {createApp} from '../apps/control-api/src/app.js';

test('healthy polling is quiet; slow/failed health and ordinary requests remain observable',async t=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'fishbot-health-log-')),records:any[]=[];
 const {app,db}=await createApp({root,externalAgent:true,logger:{stream:{write(line:string){records.push(JSON.parse(line))}}}});
 t.after(async()=>{await app.close();await rm(root,{recursive:true,force:true})});
 app.addHook('onRequest',async(req,reply)=>{
  if(req.headers['x-fixture-slow'])Object.defineProperty(reply,'elapsedTime',{get:()=>2001});
 });
 assert.equal((await app.inject('/v1/health')).statusCode,200);
 assert.equal(records.length,0);
 assert.equal((await app.inject({url:'/v1/health',headers:{'x-fixture-slow':'1'}})).statusCode,200);
 assert.deepEqual(records.map(r=>[r.level,r.msg,r.statusCode]),[[40,'Health probe degraded',200]]);
 const query=db.query.bind(db);
 db.query=(async(sql:string,...args:any[])=>{if(sql==='SELECT 1')throw Error('fixture database unavailable');return query(sql,...args)}) as typeof db.query;
 assert.equal((await app.inject('/v1/health')).statusCode,500);
 assert.ok(records.some(r=>r.msg==='Health probe degraded'&&r.statusCode===500));
 assert.ok(records.some(r=>r.level>=50&&r.msg==='request failed'&&r.message==='fixture database unavailable'));
 db.query=query;
 await app.inject('/v1/auth/me');
 assert.ok(records.some(r=>r.msg==='incoming request'&&r.req.url==='/v1/auth/me'));
 assert.ok(records.some(r=>r.msg==='request completed'));
});
