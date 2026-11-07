import test from 'node:test';import assert from 'node:assert/strict';import {mkdtempSync,readFileSync,rmSync} from 'node:fs';import os from 'node:os';import path from 'node:path';import {observeModel} from '../packages/shared/model-diagnostics.js';
test('Node model observation preserves request, usage, disposition and isolation without bodies',async()=>{
 const root=mkdtempSync(path.join(os.tmpdir(),'fish-observation-'));try{
 const owner={root,tenantId:'tenant',botId:'bot-1'};
 await observeModel(owner,'favorite_caption','fixture',async trace=>{const response=await trace.request(async()=>new Response(JSON.stringify({id:'id',usage:{prompt_tokens:9,completion_tokens:2,prompt_cache_hit_tokens:7},secret:'never-record-this'})));trace.response(await response.json());trace.adopted('saved')});
 await assert.rejects(observeModel({...owner,botId:'bot-2'},'video_evidence','fixture',async trace=>trace.request(async()=>{throw new TypeError('never-record-this')})));
 const text=readFileSync(path.join(root,'bots/bot-1/model-diagnostics.jsonl'),'utf8');assert.ok(!text.includes('never-record-this'));const rows=text.trim().split('\n').map(x=>JSON.parse(x));assert.equal(new Set(rows.map(x=>x.attemptId)).size,1);assert.equal(rows.find(x=>x.kind==='request_result').cachedTokens,7);assert.equal(rows.at(-1).adoption,'adopted');
 const failed=readFileSync(path.join(root,'bots/bot-2/model-diagnostics.jsonl'),'utf8').trim().split('\n').map(x=>JSON.parse(x));assert.equal(failed.find(x=>x.kind==='request_result').diagnosticDetail,'request_submission_unknown');assert.equal(failed.at(-1).adoption,'not_adopted');
 }finally{rmSync(root,{recursive:true,force:true})}
});

test('HTTP rejection remains failed even when probe returns a structured report',async()=>{const root=mkdtempSync(path.join(os.tmpdir(),'fish-observation-'));try{await observeModel({root,tenantId:'t',botId:'b'},'model_probe:chat','fixture',async trace=>{await trace.request(async()=>new Response('{}',{status:401}));trace.adopted('probe_reported')});const rows=readFileSync(path.join(root,'bots/b/model-diagnostics.jsonl'),'utf8').trim().split('\n').map(x=>JSON.parse(x));assert.equal(rows.find(x=>x.kind==='request_result').status,'failed');assert.equal(rows.at(-1).adoption,'adopted')}finally{rmSync(root,{recursive:true,force:true})}});
