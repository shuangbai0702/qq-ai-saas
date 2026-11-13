import test from 'node:test';import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,readFile,copyFile,readdir,rm} from 'node:fs/promises';import os from 'node:os';import path from 'node:path';
import {execFile} from 'node:child_process';import {promisify} from 'node:util';import {createServer} from 'node:http';import {randomBytes} from 'node:crypto';
import {modelImageTargetEdge} from '../packages/shared/model-media.js';
import {VideoEvidence,recoverMedia} from '../apps/node-agent/src/bilibili/video-evidence.js';import {BiliWeb} from '../apps/node-agent/src/bilibili/web-adapter.js';import {managedProcess} from '../apps/node-agent/src/media/lifecycle.js';
import {encrypt} from '../packages/shared/security.js';import {modelHttp} from '../packages/shared/model-http.js';import {BilibiliConfigSchema} from '../packages/contracts/bilibili.js';
function jpegSize(data:Buffer){
 let at=2;while(at+8<data.length){if(data[at++]!==0xff)continue;const marker=data[at++];if(marker===0xd8||marker===0xd9)continue;const size=data.readUInt16BE(at);if([0xc0,0xc1,0xc2].includes(marker))return{width:data.readUInt16BE(at+5),height:data.readUInt16BE(at+3)};if(size<2)break;at+=size}throw new Error('JPEG dimensions missing');
}
const winFfmpeg='C:/Users/76868/AppData/Local/Programs/Python/Python313/Lib/site-packages/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe';

test('real FFmpeg frames remain disk-backed, reach image provider with coverage, and clean after success/cancel',{timeout:90000},async t=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'fishbot-video-test-'));t.after(()=>rm(root,{recursive:true,force:true}));
 const configFile=process.env.FISHBOT_TEST_MEDIA_RESOURCES_FILE,resources=configFile?JSON.parse(await readFile(configFile,'utf8')):{ffmpeg:winFfmpeg,ffprobe:'fixture-probe'};
 if(process.platform==='win32'){resources.guardRoot=path.join(root,'guard');await mkdir(resources.guardRoot);await writeFile(path.join(resources.guardRoot,'guard-heartbeat'),'');await writeFile(path.join(resources.guardRoot,'armed'),'');const heartbeat=setInterval(()=>void writeFile(path.join(resources.guardRoot,'guard-heartbeat'),'').catch(()=>{}),1000);heartbeat.unref();t.after(()=>clearInterval(heartbeat))}
 await writeFile(path.join(root,'media-resources.json'),JSON.stringify(resources));
 const source=process.env.FISHBOT_TEST_MEDIA_FIXTURE||path.join(root,'test.mp4');
 if(!process.env.FISHBOT_TEST_MEDIA_FIXTURE)await promisify(execFile)(resources.ffmpeg,['-hide_banner','-nostdin','-loglevel','error','-threads','1','-filter_threads','1','-f','lavfi','-i','testsrc2=size=320x240:rate=5,drawbox=x=160:y=0:w=160:h=240:color=blue:t=fill','-f','lavfi','-i','sine=frequency=440:sample_rate=8000','-t','4','-c:v','libx264','-threads','1','-pix_fmt','yuv420p','-c:a','aac','-y',source],{windowsHide:true,timeout:20000});
 const key=randomBytes(32),secret=encrypt(key,'tenant:bot','fixture-only-key');let expectedDimensions={width:192,height:144},capturedImage:Buffer|undefined,truncated=false,expectedFrames=4,uploads=0,hold=false,empty=false,expectedModel='fixture-image',expectedKey='fixture-only-key',seen!:()=>void;let entered=new Promise<void>(r=>seen=r);
 const server=createServer(async(req,res)=>{let raw='';for await(const c of req)raw+=c;const body=JSON.parse(raw);uploads++;assert.ok(!raw.includes('https://cdn'));assert.equal(body.model,expectedModel);assert.equal(req.headers.authorization,'Bearer '+expectedKey);const parts=body.messages[1].content,images=parts.filter((p:any)=>p.type==='image_url');assert.ok(images.length>0&&images.length<=expectedFrames);assert.ok(images.every((p:any)=>p.image_url.url.startsWith('data:image/jpeg;base64,/9j/')));for(const image of images){capturedImage=Buffer.from(image.image_url.url.split(',')[1],'base64');const dimensions=jpegSize(capturedImage);assert.deepEqual(dimensions,expectedDimensions,'cropped source dimensions select the existing shared image tier');}assert.ok(Buffer.byteLength(raw)<1200000);seen();if(hold)return;res.setHeader('Content-Type','application/json');res.end(JSON.stringify({choices:[{message:{content:empty?null:'测试图片模型收到真实采样帧。声音未知。'},finish_reason:empty||truncated?'length':'stop'}],usage:{prompt_tokens:12,completion_tokens:8,total_tokens:20}}))});
 await new Promise<void>(r=>server.listen(0,'127.0.0.1',r));t.after(()=>server.close());const base='http://127.0.0.1:'+(server.address() as any).port;
 const trusted=process.env.FISHBOT_MODEL_ALLOWED_PRIVATE_ORIGINS;process.env.FISHBOT_MODEL_ALLOWED_PRIVATE_ORIGINS=[trusted,base].filter(Boolean).join(',');t.after(()=>{if(trusted===undefined)delete process.env.FISHBOT_MODEL_ALLOWED_PRIVATE_ORIGINS;else process.env.FISHBOT_MODEL_ALLOWED_PRIVATE_ORIGINS=trusted});
 const db:any={query:async(sql:string)=>sql.includes('bot_config_revisions')?[{config:{baseUrl:base,model:'fixture-image'}}]:[{ciphertext:secret}]};
 const web=new BiliWeb();web.video=async()=>({title:'测试源',pages:[{page:1,cid:123,duration:4}]});web.get=async()=>({dash:{video:[{baseUrl:'https://cdn.bilivideo.com/video.mp4',bandwidth:100}],audio:[{baseUrl:'https://cdn.bilivideo.com/audio.mp4',bandwidth:50}]}});
 const io:any={download:async(_url:string,dest:string,budget:any,signal:AbortSignal)=>{signal.throwIfAborted();const bytes=(await readFile(source)).length;budget.received+=bytes;budget.remaining-=bytes;await copyFile(source,dest)}};
 if(process.platform==='win32')io.process=async(exe:string,args:string[],opts:any)=>exe==='fixture-probe'?{stdout:JSON.stringify({streams:[{codec_type:'video',width:320,height:240},{codec_type:'audio'}]})}:managedProcess(exe,args,opts);
 const service=new VideoEvidence(db,root,key,web,modelHttp,io),settings={config:BilibiliConfigSchema.parse({enabled:true,scopes:['media.analyze'],model:{provider:'bot'}})};
 const job={id:'fixture-media-1',bot_id:'bot',tenant_id:'tenant',config_revision:1,args:{bvid:'BV1xx411c7mD',part:1,start:0,seconds:4,question:'画面是什么'}};
 const result=await service.analyze(job,settings,undefined,new AbortController().signal);assert.equal(result.modality,'frames');assert.equal(result.audio.analyzed,false);assert.deepEqual(result.clip,[0,4]);assert.ok(result.frameTimes.length>0);assert.equal(result.usage.providerUsage.total_tokens,20);assert.deepEqual(await readdir(path.join(root,'media-tasks')),[]);
 expectedFrames=12;
 const twelve=await service.analyze({...job,id:'fixture-media-12',args:{...job.args,frameCount:12,timestamps:Array.from({length:12},(_,i)=>i/3)}},settings,undefined,new AbortController().signal);
 assert.equal(twelve.requestedFrameCount,12);assert.equal(twelve.frameCount,12);assert.equal(new Set(twelve.frameTimes).size,12);assert.ok(twelve.frameOffsets.every(offset=>offset>=-0.001&&offset<=0.201));assert.equal(twelve.missingFrames.length,0);
 await assert.rejects(()=>service.analyze({...job,args:{...job.args,audio:'transcribe'}},settings,undefined,new AbortController().signal),(e:any)=>e.code==='asr_not_configured');
 expectedFrames=4;
 expectedDimensions={width:85,height:128};
 const cropped=await service.analyze({...job,id:'fixture-media-crop',args:{...job.args,crop:{x:0.5,y:0,width:0.5,height:1}}},settings,undefined,new AbortController().signal);
 assert.deepEqual(cropped.crop,{x:160,y:0,width:160,height:240});assert.deepEqual(cropped.sourceSize,{width:320,height:240});assert.equal(cropped.frameImageMaxEdge,128);assert.deepEqual(cropped.frameTimes,result.frameTimes);assert.equal(cropped.analysisComplete,true);
 if(!process.env.FISHBOT_TEST_MEDIA_FIXTURE){
  const image=path.join(root,'cropped.jpg');await writeFile(image,capturedImage!);
  const pixel=await promisify(execFile)(resources.ffmpeg,['-hide_banner','-nostdin','-loglevel','error','-i',image,'-vf','scale=1:1','-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','-'],{windowsHide:true,encoding:'buffer'});
  assert.ok(pixel.stdout[2]>200&&pixel.stdout[0]<30&&pixel.stdout[1]<30,'right-half ROI is the blue source region, not the full picture');
 }
 expectedDimensions={width:8,height:6};
 const tiny=await service.analyze({...job,id:'fixture-media-small-crop',args:{...job.args,crop:{x:0.75,y:0.25,width:0.025,height:0.025}}},settings,undefined,new AbortController().signal);
 assert.equal(tiny.frameImageMaxEdge,8,'small source ROI is never enlarged');assert.deepEqual(tiny.crop,{x:240,y:60,width:8,height:6});
 for(const crop of [{x:0.9,y:0,width:0.2,height:1},{x:0,y:0,width:0.00001,height:1}])await assert.rejects(()=>service.analyze({...job,args:{...job.args,crop}},settings,undefined,new AbortController().signal),(e:any)=>e.code==='crop_invalid');
 expectedDimensions={width:192,height:144};truncated=true;
 const partial=await service.analyze({...job,id:'fixture-media-length'},settings,undefined,new AbortController().signal);
 assert.equal(partial.analysisComplete,false);assert.equal(partial.finishReason,'length');assert.match(partial.analysis,/真实采样帧/);assert.ok(partial.limitations.some(s=>s.includes('仅保留已返回部分')));truncated=false;
 // A protocol fixture validates owned audio extraction and timestamp mapping;
 // it performs no speech recognition and is not evidence of ASR quality.
 resources.asr={executable:process.execPath,args:['-e',"const fs=require('fs');if(fs.readFileSync(process.argv[1]).subarray(0,4).toString()!=='RIFF')throw Error('not WAV');fs.writeFileSync(process.argv[2],JSON.stringify({segments:[{start:0.25,end:0.75,text:'fixture transcription, not recognition'}]}))",'{audio}','{output}']};
 await writeFile(path.join(root,'media-resources.json'),JSON.stringify(resources));
 const joint=await service.analyze({...job,id:'fixture-media-asr-protocol',args:{...job.args,start:1,audio:'transcribe'}},settings,undefined,new AbortController().signal);
 assert.equal(joint.modality,'frames+asr');assert.equal(joint.audio.trackDownloaded,true);assert.deepEqual(joint.audio.segments,[{from:1.25,to:1.75,text:'fixture transcription, not recognition'}]);assert.equal(joint.audio.source.cid,123);assert.ok(joint.frameTimes.every(t=>t>=1));
 resources.asr.args[1]="require('fs').writeFileSync(process.argv[2],JSON.stringify({segments:[{start:0,end:99,text:'invalid'}]}))";
 await writeFile(path.join(root,'media-resources.json'),JSON.stringify(resources));
 await assert.rejects(()=>service.analyze({...job,id:'fixture-media-asr-invalid',args:{...job.args,audio:'transcribe'}},settings,undefined,new AbortController().signal),(e:any)=>e.code==='asr_invalid');
 assert.deepEqual(await readdir(path.join(root,'media-tasks')),[]);delete resources.asr;await writeFile(path.join(root,'media-resources.json'),JSON.stringify(resources));
 expectedModel='independent-image';expectedKey='independent-key';
 const independent={config:BilibiliConfigSchema.parse({enabled:true,scopes:['media.analyze'],model:{provider:'openai',baseUrl:base,model:expectedModel}}),model_secret:encrypt(key,'bilibili:model:tenant:bot',expectedKey)};
 const isolated=await service.analyze({...job,id:'fixture-media-independent'},independent,undefined,new AbortController().signal);assert.equal(isolated.usage.providerUsage.total_tokens,20);
 await assert.rejects(()=>service.analyze({...job,id:'fixture-media-no-key'},{...independent,model_secret:undefined},undefined,new AbortController().signal),/没有可用/);
 expectedModel='fixture-image';expectedKey='fixture-only-key';
 empty=true;
 await assert.rejects(()=>service.analyze({...job,id:'fixture-media-empty'},settings,undefined,new AbortController().signal),(e:any)=>e.code==='vision_response_invalid'&&e.message.includes('length')&&e.usage.providerUsage.total_tokens===20);
 assert.deepEqual(await readdir(path.join(root,'media-tasks')),[]);empty=false;
 const beforeCancelUploads=uploads,beforeDispatchCancel=new AbortController();
 await assert.rejects(()=>service.analyze({...job,id:'fixture-media-cancel-after-reserve'},settings,undefined,beforeDispatchCancel.signal,async()=>{beforeDispatchCancel.abort()}),(e:any)=>e.code==='media_canceled'&&e.usage.modelRequestStatus==='released');
 assert.equal(uploads,beforeCancelUploads,'proven pre-dispatch cancellation never reaches the provider');assert.deepEqual(await readdir(path.join(root,'media-tasks')),[]);
 hold=true;entered=new Promise<void>(r=>seen=r);const abort=new AbortController(),pending=service.analyze({...job,id:'fixture-media-2'},settings,undefined,abort.signal);await entered;abort.abort();await assert.rejects(()=>pending);assert.deepEqual(await readdir(path.join(root,'media-tasks')),[]);assert.equal(uploads,9);await mkdir(path.join(root,'media-tasks','job-crash123'));await writeFile(path.join(root,'media-tasks','job-crash123','fragment.mp4'),'orphan');await writeFile(path.join(root,'media-tasks','keep-policy.txt'),'not-owned');await recoverMedia(root,db);assert.deepEqual(await readdir(path.join(root,'media-tasks')),['keep-policy.txt']);console.log(JSON.stringify({kind:'video-evidence-fixture',platform:process.platform,memory:process.memoryUsage(),usage:process.resourceUsage(),realModelCalls:0,realBilibiliWrites:0}));
});

test('model image source-longest tiers retain the exact 512 exception and never upscale small inputs',()=>{
 for(const [source,target] of [[2048,512],[1025,512],[1024,256],[513,256],[512,256],[511,192],[257,192],[256,128],[1,1]])assert.equal(modelImageTargetEdge(source),target);
 for(const source of [0,-1,NaN,Infinity,1.5])assert.throws(()=>modelImageTargetEdge(source),RangeError);
});
