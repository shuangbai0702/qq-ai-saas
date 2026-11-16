import test from 'node:test';import assert from 'node:assert/strict';import http from 'node:http';
import {installedDsh} from '../packages/dsh/kernel.mjs';
import {nativePublicWeb} from '../scripts/dsh-prototype/native-web.mjs';

async function server(t,handler){const s=http.createServer(handler);await new Promise(r=>s.listen(0,'127.0.0.1',r));t.after(()=>new Promise(r=>s.close(r)));return 'http://127.0.0.1:'+s.address().port+'/v1';}
const config=baseURL=>({baseURL,apiKey:'synthetic-test-only',model:'fixture',maxTokens:1024,maxUses:1});

test('installed native search registers real schema and joins cited sources behind reservation',async t=>{
 let requests=0,reserves=0,settlement;
 const baseURL=await server(t,async(req,res)=>{requests++;assert.equal(req.url,'/v1/messages');let body='';for await(const chunk of req)body+=chunk;const v=JSON.parse(body);assert.equal(v.tools[0].type,'web_search_20250305');assert.equal(v.tools[0].max_uses,1);assert.equal(v.max_tokens,1024);res.setHeader('content-type','application/json');res.end(JSON.stringify({content:[{type:'web_search_tool_result',content:[{type:'web_search_result',url:'https://example.com/source',title:'public source'}]},{type:'text',text:'found',citations:[{url:'https://example.com/source',cited_text:'public evidence'}]}]}));});
 const web=await nativePublicWeb(await installedDsh(),{search:config(baseURL),reserveSearch:async()=>{reserves++;return{settle:async v=>settlement=v};}});t.after(()=>web.close());
 const item=(await web.discover()).find(v=>v.id==='web_search');assert(item);assert(item.definition.parameters.properties.queries);
 const result=await web.execute(item,{queries:['public evidence']},{callId:'search',signal:new AbortController().signal});
 assert.equal(requests,1);assert.equal(reserves,1);assert.equal(result.sources[0].snippet,'public evidence');assert.equal(settlement.state,'completed');assert.equal(settlement.usage,undefined);
});

test('native search cannot bypass absent/denied budgets or canceled input',async t=>{
 let requests=0;const baseURL=await server(t,(_req,res)=>{requests++;res.end('{}');}),native=await installedDsh();
 await assert.rejects(nativePublicWeb(native,{search:config(baseURL)}),/budget reservation/);
 const web=await nativePublicWeb(native,{search:config(baseURL),reserveSearch:async()=>undefined});t.after(()=>web.close());const item=(await web.discover()).find(v=>v.id==='web_search');
 await assert.rejects(web.execute(item,{queries:['public']},{callId:'denied',signal:new AbortController().signal}),/reserved/);
 const c=new AbortController();c.abort();await assert.rejects(web.execute(item,{queries:['public']},{callId:'cancel',signal:c.signal}));assert.equal(requests,0);
});

test('provider errors and absent structured search blocks are failures, never fake search results',async t=>{
 for(const mode of ['http','prose']){
  let settlement;const baseURL=await server(t,(_req,res)=>{res.statusCode=mode==='http'?404:200;res.setHeader('content-type','application/json');res.end(JSON.stringify(mode==='http'?{error:{message:'not available'}}:{content:[{type:'text',text:'plausible but unverified answer'}]}));});
  const web=await nativePublicWeb(await installedDsh(),{search:config(baseURL),reserveSearch:async()=>({settle:async v=>settlement=v})});
  try{const item=(await web.discover()).find(v=>v.id==='web_search');await assert.rejects(web.execute(item,{queries:['public']},{callId:mode,signal:new AbortController().signal}),mode==='http'?/404/:/no web_search_tool_result/);assert.equal(settlement.state,'unknown');}finally{await web.close();}
 }
});
