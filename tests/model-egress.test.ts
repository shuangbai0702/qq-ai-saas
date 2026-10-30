import test from 'node:test';import assert from 'node:assert/strict';import http from 'node:http';import {execFile} from 'node:child_process';import path from 'node:path';
import {modelHttp,modelUrl,publicModelIp} from '../packages/shared/model-http.js';import {discoverModels} from '../packages/shared/model-catalog.js';import {probeModel} from '../packages/shared/model-probe.js';import {permitModelFixture} from './helpers/model-fixture.js';
test('model egress rejects private/encoded/reserved destinations, credentials and redirects before sending a Key',async()=>{
 for(const ip of ['0.0.0.0','127.0.0.1','10.0.0.1','100.64.0.1','169.254.169.254','192.0.0.9','192.168.1.1','198.18.0.1','::1','::ffff:8.8.8.8','fc00::1','2001:db8::1','2002:7f00:1::'])assert.equal(publicModelIp(ip),false,ip);assert.ok(publicModelIp('8.8.8.8'));assert.ok(publicModelIp('2606:4700:4700::1111'));
 for(const url of ['file:///etc/passwd','https://user:secret@models.example/v1','https://example.com/?key=x'])assert.throws(()=>modelUrl(url));
 let calls=0;const server=http.createServer((req,res)=>{calls++;if(req.url==='/redirect/models')res.writeHead(302,{location:'http://169.254.169.254/'});res.end('{"data":[{"id":"safe"}]}')});await new Promise<void>(r=>server.listen(0,'127.0.0.1',r));
 try{const port=(server.address()as any).port,base='http://127.0.0.1:'+port;
  for(const host of ['127.0.0.1','127.1','2130706433','[::1]'])await assert.rejects(()=>modelHttp('http://'+host+':'+port),/内网|保留/);assert.equal(calls,0);
  assert.equal((await discoverModels(base,'synthetic-egress-key')).ok,false);assert.equal((await probeModel(base,'fixture','synthetic-egress-key')).results.every(x=>!x.ok),true);assert.equal(calls,0);
  permitModelFixture(server);assert.equal((await discoverModels(base,'synthetic-egress-key')).ok,true);assert.equal((await discoverModels(base+'/redirect','synthetic-egress-key')).ok,false);assert.equal(calls,2,'no request follows the redirect');
 }finally{await new Promise<void>(r=>server.close(()=>r()))}
});
test('AstrBot SDK checks actual socket destinations and pins validated DNS answers',async()=>{
 const result=await new Promise<string>((resolve,reject)=>execFile(path.resolve(`references/astrbot/.venv/${process.platform==='win32'?'Scripts/python.exe':'bin/python'}`),['experiments/docker-audit-20261029/model_egress_test.py'],{windowsHide:true},(error,out,err)=>error?reject(new Error(err)):resolve(out)));assert.match(result,/PASS actual SDK backend/);
});
