import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer,request as httpRequest} from 'node:http';
import {mkdtemp,readFile,rm} from 'node:fs/promises';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import os from 'node:os';import path from 'node:path';
import {rangeSource} from '../apps/node-agent/src/media/range-source.js';
const ffmpeg='C:/Users/76868/AppData/Local/Programs/Python/Python313/Lib/site-packages/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe';
test('real FFmpeg seeks a late clip through bounded DNS-pinned Range bridge without complete source download',{timeout:40000},async t=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'fishbot-range-'));t.after(()=>rm(root,{recursive:true,force:true}));
 const exe=process.env.FISHBOT_TEST_FFMPEG||ffmpeg,source=path.join(root,'long.mp4');
 await promisify(execFile)(exe,['-hide_banner','-nostdin','-loglevel','error','-threads','1','-filter_threads','1','-f','lavfi','-i','testsrc2=size=640x360:rate=15','-t','60','-c:v','mpeg4','-q:v','2','-threads','1',source],{windowsHide:true,timeout:20000});
 const bytes=await readFile(source);assert.ok(bytes.length>4*1048576);let requests=0;
 const server=createServer((req,res)=>{requests++;assert.equal(req.headers.cookie,undefined);const m=/^bytes=(\d+)-(\d+)$/.exec(req.headers.range||'');assert.ok(m);const start=Number(m[1]),end=Math.min(Number(m[2]),bytes.length-1);res.writeHead(206,{'Content-Range':`bytes ${start}-${end}/${bytes.length}`,'Content-Length':end-start+1,'Accept-Ranges':'bytes'});res.end(bytes.subarray(start,end+1))});
 await new Promise<void>(r=>server.listen(0,'127.0.0.1',r));t.after(()=>{server.closeAllConnections();server.close()});
 const io:any={lookup:async()=>[{address:'8.8.8.8',family:4}],request:(_url:any,options:any,callback:any)=>httpRequest({host:'127.0.0.1',port:(server.address() as any).port,path:'/',headers:options.headers,signal:options.signal},callback)};
 const budget={remaining:4*1048576,received:0};
 await rangeSource('https://test.bilivideo.com/source.mp4',budget,new AbortController().signal,async url=>{
  const output=await promisify(execFile)(exe,['-hide_banner','-nostdin','-loglevel','info','-threads','1','-ss','45','-copyts','-i',url,'-vf','showinfo','-frames:v','1','-f','null','-'],{windowsHide:true,timeout:15000,maxBuffer:131072});
  assert.ok(/pts_time:45(?:[.\s])/.test(output.stderr));
 },io);
 assert.ok(requests>1);assert.ok(budget.received<bytes.length);assert.ok(budget.received<=4*1048576);
 console.log(JSON.stringify({kind:'range-fixture',sourceBytes:bytes.length,downloadBytes:budget.received,requests,paidModelCalls:0}));
 await assert.rejects(()=>rangeSource('https://test.bilivideo.com/video.mp4',{remaining:100,received:0},new AbortController().signal,url=>fetch(url).then(r=>r.text()),{...io,lookup:async()=>[{address:'127.0.0.1',family:4}]}),(e:any)=>e.code==='private_media_address');
});
