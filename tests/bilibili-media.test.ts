import test from 'node:test';import assert from 'node:assert/strict';
import {mkdtemp,rm,readFile,readdir,access} from 'node:fs/promises';import path from 'node:path';import os from 'node:os';
import {Readable} from 'node:stream';
import {streamToFile,mediaUrl,publicAddress,managedProcess,mediaWorkspace} from '../apps/node-agent/src/media/lifecycle.js';

test('media stream bounds bytes, removes partial files and releases workspace on failure',async t=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'bili-media-'));t.after(()=>rm(root,{recursive:true,force:true}));
 await assert.rejects(()=>mediaWorkspace(root,async dir=>{await streamToFile(Readable.from([Buffer.alloc(32),Buffer.alloc(32)]),path.join(dir,'part.mp4'),50,new AbortController().signal)}),/超过额度/);
 assert.deepEqual(await readdir(root),[]);
 const result=await mediaWorkspace(root,async dir=>{const file=path.join(dir,'part.mp4');assert.equal(await streamToFile(Readable.from([Buffer.alloc(32),Buffer.alloc(32)]),file,64,new AbortController().signal),64);return(await readFile(file)).length});assert.equal(result,64);assert.deepEqual(await readdir(root),[]);
});
test('media refuses arbitrary destinations, private IP and credentials on CDN URLs',()=>{
 for(const url of ['http://x.bilivideo.com/a','https://localhost/a','https://api.bilibili.com/a','https://user:secret@x.hdslb.com/a','https://x.hdslb.com:8787/a','https://hdslb.com.attacker.example/a'])assert.throws(()=>mediaUrl(url));
 for(const ip of ['127.0.0.1','10.0.0.1','192.168.1.1','172.17.0.1','169.254.169.254','100.64.0.1','::1','::ffff:127.0.0.1','fe80::1','2001:db8::1'])assert.equal(publicAddress(ip),false,ip);
 assert.equal(publicAddress('8.8.8.8'),true);assert.equal(mediaUrl('https://x.bilivideo.com/video.mp4').protocol,'https:');
});
test('media process is joined after cancellation; canceled caller cannot leave an active child',{skip:process.platform!=='win32'},async t=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'bili-child-'));t.after(()=>rm(root,{recursive:true,force:true}));const signal=new AbortController();let pid=0;
 // A real child writes its PID before the test cancels, rather than depending
 // on process startup speed or a fixed sleep.
 const marker=path.join(root,'pid');
 const running=managedProcess(process.execPath,['-e',`require('fs').writeFileSync(process.argv[1],String(process.pid));setInterval(()=>{},1000)`,marker],{cwd:root,signal:signal.signal,timeoutMs:10000});
 const end=Date.now()+5000;while(Date.now()<end){try{pid=Number(await readFile(marker,'utf8'));break}catch{await new Promise(r=>setTimeout(r,10))}}
 assert.ok(pid);signal.abort();await assert.rejects(()=>running);assert.throws(()=>process.kill(pid,0));
});
test('Linux media startup fails closed before spawn without explicit delegation',{skip:process.platform!=='linux'},async()=>{
 await assert.rejects(()=>managedProcess('/usr/bin/ffmpeg',[],{cwd:os.tmpdir(),signal:new AbortController().signal,timeoutMs:1000}),/专属进程归属/);
});

test('playurl selects a safe backup without permitting PCDN ports or arbitrary hosts',async()=>{
 const{playableMediaSource}=await import('../apps/node-agent/src/bilibili/video-evidence.js');
 const unsafe='https://vu5bt87a.edge.mountaintoys.cn:4483/video';
 const safe='https://upos-sz-estgoss.bilivideo.com/video?signature=kept';
 assert.equal(playableMediaSource([{bandwidth:98,baseUrl:unsafe,backupUrl:[safe]},{bandwidth:138,baseUrl:'https://x.bilivideo.com/other'}]),safe);
 assert.equal(playableMediaSource([{base_url:unsafe,backup_url:['http://x.bilivideo.com/video',safe]}]),safe);
 assert.equal(playableMediaSource([{url:unsafe,backup_url:['https://bilivideo.com.attacker.example/a','https://u:p@x.bilivideo.com/a']}]),undefined);
 assert.equal(playableMediaSource([{url:safe}]),safe);
});
