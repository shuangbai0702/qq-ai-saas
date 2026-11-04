import {permitModelFixture} from './helpers/model-fixture.js';
import test from 'node:test';import assert from 'node:assert/strict';import http from 'node:http';import {mkdtemp} from 'node:fs/promises';import path from 'node:path';
import {discoverModels} from '../packages/shared/model-catalog.js';import {createApp} from '../apps/control-api/src/app.js';import {issueTestInvite} from './helpers/invitations.js';
test('model discovery makes only a bounded models GET, filters duplicates and never returns credentials',async()=>{
 const key='synthetic-catalog-private-key';const seen:any[]=[];const server=http.createServer((req,res)=>{seen.push({url:req.url,auth:req.headers.authorization,method:req.method});res.setHeader('Content-Type','application/json');res.end(JSON.stringify({data:[{id:'gpt-5.6-luna'},{id:'gpt-5.6-luna'},{id:key},{id:'another-model'},{id:''},{}],secret:key}))});await new Promise<void>(r=>server.listen(0,'127.0.0.1',r));permitModelFixture(server);
 try{const base=`http://127.0.0.1:${(server.address()as any).port}/v1/`;const result=await discoverModels(base,key);assert.equal(result.ok,true);assert.deepEqual(result.models,[{id:'gpt-5.6-luna'},{id:'another-model'}]);assert.equal(result.capabilitiesVerified,false);assert.ok(!JSON.stringify(result).includes(key));assert.deepEqual(seen,[{url:'/v1/models',auth:'Bearer '+key,method:'GET'}]);await assert.rejects(()=>discoverModels(base+'?key=private',key),/Base URL/)}finally{await new Promise<void>(r=>server.close(()=>r()))}
});
test('discovery handles unsupported, oversized and redirected upstreams without leaking error bodies',async()=>{
 const key='synthetic-upstream-credential';const server=http.createServer((req,res)=>{if(req.url?.startsWith('/large')){res.end(JSON.stringify({data:[],padding:'x'.repeat(270000)}));return}if(req.url?.startsWith('/redirect')){res.writeHead(302,{Location:'/other/models'});res.end();return}res.writeHead(401,{'Content-Type':'application/json'});res.end(JSON.stringify({error:{message:key}}))});await new Promise<void>(r=>server.listen(0,'127.0.0.1',r));permitModelFixture(server);
 try{for(const route of ['auth','large','redirect']){const result=await discoverModels(`http://127.0.0.1:${(server.address()as any).port}/${route}`,key);assert.equal(result.ok,false);assert.deepEqual(result.models,[]);assert.ok(!JSON.stringify(result).includes(key))}}finally{await new Promise<void>(r=>server.close(()=>r()))}
});
test('saved-key model discovery checks bot ownership and keeps all user model configuration isolated',async t=>{
 const seen:string[]=[];const server=http.createServer((req,res)=>{seen.push(req.headers.authorization||'');res.end(JSON.stringify({data:[{id:'model-for-'+(req.headers.authorization?.endsWith('A')?'A':'B')}]}))});await new Promise<void>(r=>server.listen(0,'127.0.0.1',r));permitModelFixture(server);t.after(()=>new Promise<void>(r=>server.close(()=>r())));
 const {app,db}=await createApp({root:await mkdtemp(path.resolve('.local/catalog-api-')),agent:false});t.after(()=>app.close());const cookies:Array<{fish_session:string}>=[];
 for(const name of ['a','b']){const registered=await app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(db),email:name+'@catalog.test',name,password:'catalog-pass-123'}});assert.equal(registered.statusCode,200);cookies.push({fish_session:registered.cookies[0].value})}
 const baseUrl=`http://127.0.0.1:${(server.address()as any).port}/v1`;const ids:string[]=[];for(let i=0;i<2;i++){const c=await app.inject({method:'POST',url:'/v1/bots',cookies:cookies[i],payload:{name:'catalog-'+i,apiKey:'synthetic-Key-'+(i?'B':'A')}});ids.push(c.json().id)}
 assert.equal((await app.inject({method:'POST',url:'/v1/model-catalog',payload:{baseUrl,apiKey:'any'}})).statusCode,401);
 const other=await app.inject({method:'POST',url:'/v1/model-catalog',cookies:cookies[1],payload:{baseUrl,botId:ids[0],apiKey:'supplied-by-B'}});assert.equal(other.statusCode,404);assert.equal(seen.length,0,'ownership is checked before contacting an upstream even with a supplied Key');
 for(let i=0;i<2;i++){const result=await app.inject({method:'POST',url:'/v1/model-catalog',cookies:cookies[i],payload:{baseUrl,botId:ids[i]}});assert.equal(result.statusCode,200);assert.equal(result.json().models[0].id,'model-for-'+(i?'B':'A'));assert.ok(!result.body.includes('synthetic-Key-'))}assert.deepEqual(seen,['Bearer synthetic-Key-A','Bearer synthetic-Key-B']);assert.equal((await db.query('SELECT * FROM runtime_groups')).length,0,'configuration and model discovery start no AstrBot process');
});

test('reasoning metadata exposes only declared supported values and never guesses by model ID',async()=>{
 const key='synthetic-reasoning-key';const server=http.createServer((_req,res)=>{res.end(JSON.stringify({data:[
  {id:'declared',supported_reasoning_efforts:['low','medium','high','not-a-supported-value',key]},
  {id:'nested',capabilities:{reasoning_efforts:['medium','max']}},
  {id:'unsupported',supports_reasoning:false},
  {id:'boolean-only',supported_parameters:['reasoning']},
  {id:'gpt-reasoning-max-no-declaration'},
 ]}))});await new Promise<void>(r=>server.listen(0,'127.0.0.1',r));permitModelFixture(server);
 try{const result=await discoverModels(`http://127.0.0.1:${(server.address()as any).port}/v1`,key);assert.deepEqual(result.models,[{id:'declared',reasoningEfforts:['low','medium','high'],reasoningSupported:true},{id:'nested',reasoningEfforts:['medium','max'],reasoningSupported:true},{id:'unsupported',reasoningSupported:false},{id:'boolean-only',reasoningSupported:true},{id:'gpt-reasoning-max-no-declaration'}]);assert.equal(result.capabilitiesVerified,false);assert.ok(!JSON.stringify(result).includes(key))}finally{await new Promise<void>(r=>server.close(()=>r()))}
});
