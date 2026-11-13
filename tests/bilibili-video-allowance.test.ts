import test from 'node:test';import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm}from'node:fs/promises';import path from'node:path';import os from'node:os';import{randomBytes}from'node:crypto';
import{VideoEvidence,VIDEO_ANALYSIS_OUTPUT_TOKENS}from'../apps/node-agent/src/bilibili/video-evidence.js';
import{BilibiliConfigSchema}from'../packages/contracts/bilibili.js';import{encrypt}from'../packages/shared/security.js';
test('video output allowance is sent and fully reserved before model dispatch',async t=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'video-allowance-'));t.after(()=>rm(root,{recursive:true,force:true}));
 await writeFile(path.join(root,'media-resources.json'),JSON.stringify({ffmpeg:'fixture-ffmpeg',ffprobe:'fixture-ffprobe',guardRoot:root}));
 const key=randomBytes(32);const db:any={query:async(sql:string)=>sql.includes('bot_config_revisions')?[{config:{baseUrl:'https://provider.invalid/v1',model:'fixture'}}]:[{ciphertext:encrypt(key,'tenant:bot','fixture')} ]};
 const web:any={video:async()=>({pages:[{page:1,duration:8,cid:1}]}),get:async()=>({dash:{video:[{baseUrl:'https://x.bilivideo.com/video'}]}})};
 let reserved=0,wire:any;
 const service=new VideoEvidence(db,root,key,web,async(_url:any,init:any)=>{
  wire=JSON.parse(init.body);assert.ok(reserved>0,'model dispatch preceded reservation');
  return new Response(JSON.stringify({choices:[{message:{content:'fixture complete'},finish_reason:'stop'}],usage:{total_tokens:10}}));
 },{admit:async()=>{},download:async(_url,file)=>{await writeFile(file,'fixture')},process:async(exe,args)=>{
  if(exe==='fixture-ffprobe')return{pid:undefined,stdout:JSON.stringify({streams:[{codec_type:'video',width:640,height:360}]}),stderr:''};
  await writeFile(args[args.length-1],'fixture-jpeg');return{pid:undefined,stdout:'',stderr:'pts_time:'+args[args.indexOf('-ss')+1]};
 }});
 const settings={config:BilibiliConfigSchema.parse({enabled:true,model:{provider:'bot'}})};
 const result=await service.analyze({id:'fixture',bot_id:'bot',tenant_id:'tenant',config_revision:1,args:{bvid:'BV1eFs96uE3K',part:1,start:0,seconds:8,frameCount:4,audio:'none'}},settings,undefined,new AbortController().signal,async n=>{reserved=n});
 assert.equal(wire.max_tokens,4096);assert.equal(wire.max_tokens,VIDEO_ANALYSIS_OUTPUT_TOKENS);
 assert.equal(reserved,Buffer.byteLength(JSON.stringify(wire.messages[1].content.filter((p:any)=>p.type!=='image_url')))+4*4096+wire.max_tokens+512);
 assert.equal(result.analysisComplete,true);assert.equal(result.frameCount,4);
});
