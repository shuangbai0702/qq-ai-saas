import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,readFile} from 'node:fs/promises';
import path from 'node:path';import os from 'node:os';import http from 'node:http';
import {LLBotAdapter} from '../adapters/protocol/llbot/index.js';
import type {ProtocolContext} from '../packages/contracts/index.js';
test('LLBot admission serializes a Bot, omits model credentials, adopts existing protocol and rejects late process/identity health',async t=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'llbot-adapter-')),runtime=path.join(root,'runtime'),qq=path.join(root,'QQ.exe'),botRoot=path.join(root,'bots/a'),settings=path.join(root,'settings');await mkdir(runtime);await mkdir(botRoot,{recursive:true});await writeFile(path.join(runtime,'fishbot-upstream.json'),'{}');await writeFile(qq,'inert');await mkdir(settings);
 await writeFile(path.join(settings,'a.json'),JSON.stringify({runtimeDir:runtime,qqPath:qq,token:'a'.repeat(64),authToken:'synthetic-auth'}));
 let record:any=null,starts=0,waiting:()=>void=()=>{},send:()=>void=()=>{},hold=false,uin='90000';
 const server=http.createServer((request,response)=>{assert.equal(request.headers.authorization,'Bearer '+'a'.repeat(64));const reply=()=>response.end(JSON.stringify({botId:'a',ready:true,login:{state:'online',uin}}));if(hold){send=reply;waiting()}else reply()});await new Promise<void>(resolve=>server.listen(0,'127.0.0.1',resolve));t.after(()=>new Promise<void>(resolve=>server.close(()=>resolve())));
 const host:any={root,inspect:async()=>record,record:async()=>record,start:async()=>{starts++;record={pid:100,created:200,rssBytes:1024}},stop:async()=>{record=null}};
 const adapter=new LLBotAdapter(host,settings),ctx:ProtocolContext={botId:'a',tenantId:'t',root:botRoot,generation:1,aiPort:26000,protocolPort:(server.address() as any).port,expectedUin:'90000',token:'b'.repeat(64)};
 await Promise.all([adapter.beginLogin(ctx),adapter.beginLogin(ctx)]);assert.equal(starts,1);const manifest=JSON.parse(await readFile(path.join(botRoot,'llbot-platform.json'),'utf8'));assert.equal(manifest.botId,'a');assert.equal('config' in manifest,false);assert.equal('apiKey' in manifest,false);
 assert.equal((await adapter.getHealth(ctx)).online,true);await adapter.beginLogin({...ctx,generation:2});assert.equal(starts,1,'Agent generation change adopts QQ');
 hold=true;const entered=new Promise<void>(resolve=>waiting=resolve),pending=adapter.getHealth(ctx);await entered;record={pid:101,created:201,rssBytes:1024};send();assert.equal((await pending).online,false,'late old process health cannot become online');hold=false;
 uin='80000';await assert.rejects(adapter.queryIdentity(ctx),/绑定账号不一致/);await adapter.stop('a');assert.equal(record,null);
 await writeFile(path.join(settings,'a.json'),JSON.stringify({runtimeDir:runtime,qqPath:qq,token:'a'.repeat(64),authToken:''}));await mkdir(path.join(runtime,'bin/pmhq/auth-local'),{recursive:true});await writeFile(path.join(runtime,'bin/pmhq/auth-local/sign-info.json'),'{}');await adapter.provision(ctx);const local=JSON.parse(await readFile(path.join(botRoot,'llbot-platform.json'),'utf8'));assert.equal(local.authMode,'local');assert.equal(local.authToken,'local-no-token');assert.equal('apiKey' in local,false);
});
