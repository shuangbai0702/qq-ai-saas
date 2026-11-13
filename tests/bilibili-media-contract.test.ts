import test from 'node:test';import assert from 'node:assert/strict';
import {biliOperations} from '../packages/contracts/bilibili.js';
import {BiliWeb} from '../apps/node-agent/src/bilibili/web-adapter.js';
import {mediaReadiness} from '../apps/node-agent/src/bilibili/video-evidence.js';
import {mkdtemp,rm} from 'node:fs/promises';import os from 'node:os';import path from 'node:path';
const bvid='BV1xx411c7mD';
test('frame positions derive count only when absent and reject conflicts rather than silently change evidence',()=>{
 const base={bvid,question:'test',start:10,seconds:20};
 assert.equal(biliOperations.analyze.args.parse(base).frameCount,undefined);
 assert.deepEqual(biliOperations.analyze.args.parse({...base,crop:{x:0.5,y:0,width:0.5,height:1}}).crop,{x:0.5,y:0,width:0.5,height:1});
 for(const crop of [{x:0.9,y:0,width:0.2,height:1},{x:0,y:0,width:0,height:1},{x:-0.1,y:0,width:0.5,height:1}])assert.throws(()=>biliOperations.analyze.args.parse({...base,crop}));
 assert.equal(biliOperations.analyze.args.parse({...base,timestamps:[10,11,12,13,14,15,16,17]}).timestamps?.length,8);
 for(const extra of [{frameCount:3},{frameCount:13},{frameCount:4,timestamps:[10,11,12,13,14]},{timestamps:[9,10,11,12]},{timestamps:[10,11,12,30]},{timestamps:[10,10,11,12]}])assert.throws(()=>biliOperations.analyze.args.parse({...base,...extra}));
});
test('large subtitles paginate beyond old first 1000 with time windows, full-search cursor and bounded output',async()=>{
 const rows=Array.from({length:2500},(_,i)=>({from:i,to:i+0.8,content:'字幕'+i+(i%100===0?'needle':'')}));
 const web=new BiliWeb(async()=>new Response(JSON.stringify({body:rows})));
 web.video=async()=>({bvid,aid:170001,pages:[{page:1,cid:123,duration:2501}]});
 web.signed=async()=>({bvid,aid:170001,cid:123,subtitle:{subtitles:[{lan:'zh-CN',id:1,subtitle_url:'https://subtitle.hdslb.com/fixture.json'}]}});
 const first=await web.transcript(bvid,1,'zh-CN');assert.equal(first.segments.length,50);assert.equal(first.nextOffset,50);assert.equal(first.totalSegments,2500);
 const later=await web.transcript(bvid,1,'zh-CN',undefined,undefined,{offset:2400,segmentLimit:100});assert.equal(later.segments[0].from,2400);assert.equal(later.nextOffset,null);
 const search=await web.transcript(bvid,1,'zh-CN',undefined,undefined,{query:'needle',offset:20,segmentLimit:3});assert.deepEqual(search.segments.map((v:any)=>v.from),[2000,2100,2200]);assert.equal(search.matchedSegments,25);assert.equal(search.nextOffset,23);
 const window=await web.transcript(bvid,1,'zh-CN',undefined,undefined,{start:1200,end:1202});assert.deepEqual(window.segments.map((v:any)=>v.from),[1200,1201]);
});
test('media readiness fails closed for missing real Guard and dependencies',async t=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'bili-unready-'));t.after(()=>rm(root,{recursive:true,force:true}));
 const state=await mediaReadiness(root);assert.equal(state.ready,false);assert.ok(['guard_unavailable','media_dependency_unavailable'].includes(state.code));
});
