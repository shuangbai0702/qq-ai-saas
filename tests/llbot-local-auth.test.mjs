import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import tls from 'node:tls';
import path from 'node:path';
import {access} from 'node:fs/promises';
import {EventEmitter,once} from 'node:events';
import {localAuth} from '../adapters/protocol/llbot/local-auth.mjs';

test('local PMHQ bridge rejects other destinations and tokens, accepts lowercase Basic and absolute HTTPS wire, and closes owned helpers',async t=>{
 const runtimeDir=path.resolve('references/llbot-8.3.2');
 try{await access(path.join(runtimeDir,'bin/pmhq/auth-local/leaf.pem'))}catch{t.skip('reviewed local auth assets not installed');return}
 const server=http.createServer();await new Promise(r=>server.listen(0,'127.0.0.1',r));
 const children=[],m={runtimeDir,python:'owned-python',management:{port:server.address().port,token:'a'.repeat(64)}};
 const spawnChild=(exe,args,options)=>{
  assert.equal(exe,args[0]==='-c'?m.python:path.resolve('base-python.exe'));assert.equal(options.windowsHide,true);
  const child=new EventEmitter();child.stdout=new EventEmitter();child.stdin=new EventEmitter();child.kill=()=>child.emit('exit',0);
  child.stdin.end=()=>{child.ended=true;child.emit('exit',0)};
  child.stdin.write=line=>{assert.deepEqual(JSON.parse(line),{op:'patch'});queueMicrotask(()=>child.stdout.emit('data',Buffer.from('{"ok":true}\n')))};
  children.push(child);
  if(args[0]==='-c'){assert.equal(args.at(-1),'900');queueMicrotask(()=>{child.stdout.emit('data',JSON.stringify({created:123,python:path.resolve('base-python.exe'),paths:[path.resolve('venv/site-packages')]}));child.emit('exit',0)})}
  else{assert.deepEqual(args.slice(0,2),['-B','-c']);assert.ok(args[2].includes('venv'));const proof=JSON.parse(args.at(-1));assert.equal(proof.pid,900);assert.equal(proof.parent,process.pid);assert.equal(proof.created,123);assert.ok(proof.executable.startsWith(runtimeDir));}
  return child;
 };
 let failures=0;const auth=await localAuth(server,{manifest:m,spawnChild,getProcess:()=>({pid:900}),onFailure:()=>failures++});
 t.after(async()=>{auth.close();await new Promise(r=>server.close(r))});
 const request=(target,token)=>http.request({host:'127.0.0.1',port:m.management.port,method:'CONNECT',path:target,headers:{'Proxy-Authorization':token}});
 for(const [target,token]of [['other.example:443',''],['api-auth.luckylillia.com:443','Basic d3Jvbmc=']]){
  const req=request(target,token);const error=once(req,'error');req.end();await error;
 }
 assert.equal(children.length,0);
 const req=request('api-auth.luckylillia.com:443','basic '+Buffer.from(m.management.token+':local').toString('base64'));
 const connected=once(req,'connect');req.end();const [response,socket,head]=await connected;assert.equal(response.statusCode,200);assert.equal(head.length,0);
 const secure=tls.connect({socket,servername:'api-auth.luckylillia.com',rejectUnauthorized:false});await once(secure,'secureConnect');
 const chunks=[];secure.on('data',raw=>chunks.push(raw));secure.write('GET https://api-auth.luckylillia.com/api/sign/info HTTP/1.1\r\nHost: api-auth.luckylillia.com\r\nConnection: close\r\n\r\n');await once(secure,'end');secure.destroy();
 const wire=Buffer.concat(chunks).toString();assert.match(wire,/HTTP\/1.1 200/);assert.match(wire,/"code":0/);assert.equal(failures,0);assert.equal(children.length,2);
 auth.close();assert.equal(children[1].ended,true);
});
