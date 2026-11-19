import test from 'node:test';import assert from 'node:assert/strict';
import {modelBaseCandidates,normalizeModelBaseUrl} from '../packages/shared/model-url.js';
import {discoverModels,resolveModelBaseUrl} from '../packages/shared/model-catalog.js';
import {modelRequestHeaders} from '../packages/shared/model-http.js';
import {BotConfigSchema,defaultConfig} from '../packages/contracts/index.js';

test('model URL paste cleanup handles punctuation, duplicated addresses, endpoint tails and missing schemes',()=>{
 for(const raw of [' “https://api.example.com / v1 / chat / completions/” ','api.example.com/v1','https//api.example.com/v1','https:/api.example.com/v1','接口地址：https：／／api.example.com/v1/models','https://api.example.com/v1/v1/responses','https://api.example.com/v1 https://api.example.com/v1','api.example.com/v1 api.example.com/v1','[API](https://api.example.com/v1)','https://api.example.com/v1?utm_source=docs#example'])assert.equal(normalizeModelBaseUrl(raw),'https://api.example.com/v1',raw);
 assert.equal(BotConfigSchema.parse({...defaultConfig,baseUrl:'api.example.com/v1/chat/completions'}).baseUrl,'https://api.example.com/v1');
 for(const raw of ['https://name:password@api.example.com/v1','https://api.example.com/v1?api_key=private','file:///etc/passwd'])assert.throws(()=>normalizeModelBaseUrl(raw));
 assert.deepEqual(modelBaseCandidates('https://api.example.com'),['https://api.example.com','https://api.example.com/v1','https://api.example.com/api/v1']);
 assert.deepEqual(modelBaseCandidates('https://api.example.com/api/v1/models'),['https://api.example.com/api/v1']);
});

test('URL candidates really run concurrently; only verified model lists select a base, never a billed completion',async()=>{
 let release!:()=>void;const gate=new Promise<void>(r=>release=r),calls:string[]=[];
 const transport=async(input:string|URL,init?:RequestInit)=>{calls.push(String(input));assert.ok(!init?.body);await gate;return String(input).includes('/api/v1/')?Response.json({data:[{id:'fixture-model'}]}):new Response('<html>not models</html>')};
 const pending=discoverModels('https://api.example.com','fixture-secret',undefined,transport);
 assert.equal(calls.length,3,'all candidates started before any response completes');release();
 const result=await pending;assert.equal(result.ok,true);assert.ok('baseUrl' in result);assert.equal(result.baseUrl,'https://api.example.com/api/v1');assert.equal(result.candidatesChecked,3);
 assert.equal(await resolveModelBaseUrl('https://api.example.com','fixture-secret',undefined,transport),'https://api.example.com/api/v1');
});

test('different pasted URLs auto-select the working explicit address and canceled discovery releases readers',async()=>{
 const transport=async(input:string|URL)=>String(input).includes('good.example')?Response.json({data:[{id:'fixture'}]}):new Response(null,{status:404});
 const r=await discoverModels('https://bad.example/v1 https://good.example/v1','fixture-key',undefined,transport);assert.ok('baseUrl' in r);assert.equal(r.baseUrl,'https://good.example/v1');
 let canceled=0;const controller=new AbortController();
 const pending=discoverModels('https://api.example/v1','fixture-key',controller.signal,async(_input,init)=>new Response(new ReadableStream({start(c){init!.signal!.addEventListener('abort',()=>c.error(Error('aborted')),{once:true})},cancel(){canceled++}})));
 controller.abort();assert.equal((await pending).ok,false);
 // A failed read still releases its lock; bounded/oversized streams are canceled.
 let released=false;
 const stream=new ReadableStream<Uint8Array>({pull(c){c.enqueue(new Uint8Array(260000))},cancel(){released=true}});
 assert.equal((await discoverModels('https://api.example/v1','fixture-key',undefined,async()=>new Response(stream))).ok,false);assert.equal(released,true);
});

test('Loomy token adaptation is constrained to its exact HTTPS API origin',()=>{
 const headers={Authorization:'Bearer fixture-secret'};
 assert.equal(modelRequestHeaders(new URL('https://loomyad.xunfei.cn/api/v1/chat/completions'),headers).get('token'),'fixture-secret');
 for(const url of ['https://loomyad.xunfei.cn.evil.example/api/v1/models','http://loomyad.xunfei.cn/api/v1/models','https://loomyad.xunfei.cn/other','https://other.example/v1/models'])assert.equal(modelRequestHeaders(new URL(url),headers).has('token'),false);
});
