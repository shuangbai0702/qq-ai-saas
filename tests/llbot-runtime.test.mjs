import test from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter,once} from 'node:events';
import {createServer} from 'node:http';
import {mkdtemp,mkdir,writeFile,readFile,access} from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import vm from 'node:vm';
import {createHash} from 'node:crypto';
import {createRequire} from 'node:module';
import {WebSocket,WebSocketServer} from 'ws';
import {LLBotRelay} from '../adapters/protocol/llbot/relay.mjs';
import {hardenPmhq,hardenReverse} from '../adapters/protocol/llbot/lifecycle.mjs';
import {resourceDecision} from '../adapters/protocol/llbot/resource-policy.mjs';
import {installActions,qzoneForm,qzoneReceipt} from '../adapters/protocol/llbot/actions.mjs';
import {prepareRuntime,startRuntime,validateManifest} from '../adapters/protocol/llbot/runner.mjs';
import {patchRuntime} from '../adapters/protocol/llbot/patch-runtime.mjs';
import {privateQqShell} from '../adapters/protocol/llbot/qq-assets.mjs';

const hash=x=>createHash('sha256').update(x).digest('hex');
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return{promise,resolve}};
async function listen(server){await new Promise(r=>server.listen(0,'127.0.0.1',r));return server.address().port}
const deadline=promise=>Promise.race([promise,new Promise((_,reject)=>{const timer=setTimeout(()=>reject(Error('local wire deadline')),4000);timer.unref()})]);
function shellFixture(){const bytes=Buffer.alloc(160);bytes.write('MZ');bytes.writeUInt32LE(64,60);bytes.write('PE\0\0',64);bytes.writeUInt16LE(0x8664,68);Buffer.from('4889ce488b114c8b41084929d0488b4918e801020304','hex').copy(bytes,96);return bytes}

test('private QQ shell changes only the unique x64 verification call and never mutates shared bytes',()=>{
 const original=shellFixture(),before=Buffer.from(original),patched=privateQqShell(original);
 assert.deepEqual(original,before);assert.deepEqual(patched.subarray(113,118),Buffer.from([0xb8,1,0,0,0]));
 assert.deepEqual(patched.subarray(0,113),original.subarray(0,113));assert.deepEqual(patched.subarray(118),original.subarray(118));
 assert.throws(()=>privateQqShell(Buffer.alloc(10)),/Unsupported/);
 const duplicate=Buffer.concat([original,original]);assert.throws(()=>privateQqShell(duplicate),/not unique/);
 assert.throws(()=>privateQqShell(patched),/not unique/);
 const arm=Buffer.from(original);arm.writeUInt16LE(0xaa64,68);assert.throws(()=>privateQqShell(arm),/Unsupported/);
});

async function fixture(){
 const root=await mkdtemp(path.join(os.tmpdir(),'fishbot-llbot-')),runtime=path.join(root,'runtime');
 await mkdir(path.join(runtime,'bin/llbot/node_modules'),{recursive:true});await mkdir(path.join(runtime,'bin/llbot/webui'),{recursive:true});await mkdir(path.join(runtime,'bin/pmhq'),{recursive:true});
 for(const name of ['llbot.js','fishbot-actions.mjs','fishbot-lifecycle.mjs','package.json','sign-proxy.win-x64.node','sign-proxy.package.json','ffmpeg.exe','ffprobe.exe'])await writeFile(path.join(runtime,'bin/llbot',name),name==='package.json'?'{}':'// inert fixture');
 await writeFile(path.join(runtime,'bin/pmhq/pmhq_loader.js'),'// inert fixture');
 const qq=path.join(root,'qq-assets'),app=path.join(qq,'versions/9.9.37-54395/resources/app');await mkdir(path.join(app,'assets'),{recursive:true});
 await writeFile(path.join(qq,'QQ.exe'),'inert native fixture');await writeFile(path.join(qq,'versions/config.json'),JSON.stringify({curVersion:'9.9.37-54395'}));await writeFile(path.join(app,'package.json'),JSON.stringify({main:'./application.asar/app_launcher/index.js'}));await writeFile(path.join(app,'application.asar'),'inert QQ archive');
 await writeFile(path.join(qq,'versions/9.9.37-54395/QQNT.dll'),shellFixture());
 await writeFile(path.join(runtime,'fishbot-upstream.json'),JSON.stringify({version:'8.3.2-test',platform:process.platform,sha256:{'bin/llbot/llbot.js':hash('// inert fixture')}}));
 const socketServer=createServer(),port=await listen(socketServer);await new Promise(r=>socketServer.close(r));
 const bot=path.join(root,'bot');await mkdir(bot);
 const m={botId:'a',tenantId:'t',root:bot,generation:2,expectedUin:'90000',authMode:'token',authToken:'synthetic-pmhq',runtimeDir:runtime,qqPath:path.join(qq,'QQ.exe'),egress:{route:'direct'},management:{host:'127.0.0.1',port,token:'a'.repeat(64)},reverseWs:{url:`ws://127.0.0.1:${port-3}/ws`,token:'b'.repeat(64)},pmhqPort:port-2,webuiPort:port-1};
 const file=path.join(bot,'llbot-platform.json');await writeFile(file,JSON.stringify(m));return{root,runtime,m,file};
}

test('managed runtime validates private ports, version, hashes and per-Bot data without running upstream native assets',async()=>{
 const f=await fixture(),a=await prepareRuntime(f.file);
 assert.notEqual(a.work,path.join(f.runtime,'bin/llbot'));
 assert.equal(JSON.parse(await readFile(path.join(a.work,'default_config.json'),'utf8')).ob11.connect[0].url,`ws://127.0.0.1:${f.m.management.port}/upstream`);
 assert.throws(()=>validateManifest({...f.m,webuiPort:f.m.pmhqPort},f.file));
 assert.throws(()=>validateManifest({...f.m,root:f.root},f.file));
 assert.throws(()=>validateManifest({...f.m,expectedUin:'other'},f.file));
 const bot2=path.join(f.root,'bot2');await mkdir(bot2);const file2=path.join(bot2,'llbot-platform.json');await writeFile(file2,JSON.stringify({...f.m,root:bot2,botId:'b',management:{...f.m.management,token:'c'.repeat(64)}}));
 const b=await prepareRuntime(file2);assert.notEqual(a.home,b.home);assert.notEqual(a.work,b.work);
 assert.notEqual(a.qqPath,b.qqPath);const app='versions/9.9.37-54395/resources/app';
 assert.equal(JSON.parse(await readFile(path.join(path.dirname(f.m.qqPath),app,'package.json'),'utf8')).main,'./application.asar/app_launcher/index.js','shared QQ metadata stays immutable');
 assert.equal(JSON.parse(await readFile(path.join(path.dirname(a.qqPath),app,'package.json'),'utf8')).main,'./fishbot-entry.cjs');
 assert.ok((await readFile(path.join(path.dirname(a.qqPath),app,'fishbot-entry.cjs'),'utf8')).includes(JSON.stringify(a.bootstrap)));assert.equal((await prepareRuntime(f.file)).qqPath,a.qqPath,'same Bot reuses only its own launcher');
 const sourceDll=path.join(path.dirname(f.m.qqPath),'versions/9.9.37-54395/QQNT.dll'),privateDll=path.join(path.dirname(a.qqPath),'versions/9.9.37-54395/QQNT.dll');
 assert.deepEqual(await readFile(sourceDll),shellFixture());assert.deepEqual(await readFile(privateDll),privateQqShell(shellFixture()));
 await writeFile(privateDll,'unexpected native change');await assert.rejects(prepareRuntime(f.file),/shell ownership/);
 await writeFile(path.join(f.runtime,'bin/llbot/llbot.js'),'changed');await assert.rejects(prepareRuntime(f.file),/integrity/);
});

test('actual reviewed bundle patch is exact, removes profile side effect, installs extensions and preserves packet framing correction',async t=>{
 const source=path.resolve('../framework-91-8.3.2-research/inspected/test/LLBot-Local/bin/llbot/llbot.js');
 try{await access(source)}catch{t.skip('reviewed upstream archive not present');return}
 const bytes=await readFile(source),patched=patchRuntime(bytes);assert.throws(()=>patchRuntime(Buffer.from('changed')),/hash/);
 assert.match(patched,/installActions\(actionMap, adapter.ctx, selfInfo/);assert.doesNotMatch(patched,/key: 20032,\s*value: Buffer.alloc\(12\)/);
 assert.match(patched,/server.push\(`\$\{uint32ToIPV4Addr\(ip\)\}:\$\{ports\[index\]\}`\)/);
 assert.match(patched,/readUInt32BE\(0\)/);assert.match(patched,/hardenPmhq\(PmhqQQProtocol/);
});

test('PMHQ stale socket close cannot clear new connection; disposed/throw paths release response listeners and HTTP preserves requested deadline',async()=>{
 class Socket {static OPEN=1;constructor(){this.readyState=1;Socket.all.push(this)}close(){}send(){if(this.throwSend)throw Error('closed')}}Socket.all=[];
 const events=new EventEmitter(),self={online:true};
 class Protocol {constructor(){this.ctx=events;this.resListeners=new Map();this.logger={error(){}};this.pmhqProbeToken=0}resetPmhqState(){this.reset=(this.reset||0)+1}startPmhqLoginProbe(){}addResListener(fn){const id=String(this.resListeners.size);this.resListeners.set(id,fn);return id}removeResListener(id){this.resListeners.delete(id)}unwrapPmhqRes(value){if(value.code)throw Error('rejected');return value.data}}
 hardenPmhq(Protocol,Socket,self);const p=new Protocol();await p.connectWebSocket();const old=p.ws;await p.connectWebSocket();const current=p.ws;old.onclose();assert.equal(p.ws,current);assert.equal(self.online,true);assert.equal(p.reset,undefined);
 current.throwSend=true;await assert.rejects(p.wsSend({type:'send',data:{}},100),/unknown/);assert.equal(p.resListeners.size,0);
 p.ws=undefined;let timeout; p.httpSend=async(data,t)=>{timeout=t;return{code:0,data:data.data}};
 assert.deepEqual(await p.sendPB('command',Buffer.from([1,2]),23),{cmd:'command',pb:'0102'});assert.equal(timeout,23);
 events.emit('dispose');await assert.rejects(p.sendPB('write','00'),/disposed/);assert.equal(p.ws,undefined);
});

test('late reverse action result never replies into a replacement socket',async()=>{
 const gate=deferred();class Reverse{reply(socket,data){socket.replies.push(data)}}
 hardenReverse(Reverse,{},{});const reverse=new Reverse(),a={replies:[],close(){}},b={replies:[],close(){}};
 reverse.activated=true;reverse.wsClient=a;reverse.config={actionMap:new Map([['send',{websocketHandle:()=>gate.promise}]])};
 const pending=reverse.handleAction(JSON.stringify({action:'send',echo:1}),a);reverse.wsClient=b;gate.resolve({status:'ok'});await pending;
 assert.deepEqual(a.replies,[]);assert.deepEqual(b.replies,[]);reverse.stop();assert.equal(reverse.activated,false);
});

test('real local WebSocket wire preserves events/actions/echo, rejects identity mismatch and does not replay unknown writes',async t=>{
 const aiHttp=createServer(),ai=new WebSocketServer({server:aiHttp}),aiPort=await listen(aiHttp),protocolHttp=createServer(),up=new WebSocketServer({server:protocolHttp}),port=await listen(protocolHttp);
 const identity=deferred(),aiConnection=once(ai,'connection');let accepted;
 const relay=new LLBotRelay({expectedUin:'90000',url:`ws://127.0.0.1:${aiPort}/ws`,token:'ai-only',onIdentity:uin=>{assert.equal(uin,'90000');identity.resolve()}});
 up.on('connection',(socket,req)=>{accepted=socket;relay.attach(socket,String(req.headers['x-self-id']||''))});
 t.after(async()=>{relay.close();for(const s of ai.clients)s.terminate();for(const s of up.clients)s.terminate();ai.close();up.close();await Promise.all([new Promise(r=>aiHttp.close(r)),new Promise(r=>protocolHttp.close(r))])});
 const client=new WebSocket(`ws://127.0.0.1:${port}`,{headers:{'X-Self-ID':'90000'}});let writes=0;const received=deferred();
 client.on('message',raw=>{const request=JSON.parse(raw);if(request.action==='get_login_info')client.send(JSON.stringify({status:'ok',retcode:0,data:{user_id:90000},echo:request.echo}));else if(request.action==='send_group_msg'){writes++;client.send(JSON.stringify({status:'ok',retcode:0,data:{message_id:19},echo:request.echo}))}else received.resolve(request)});
 await deadline(identity.promise);const [aiSocket,req]=await deadline(aiConnection);assert.equal(req.headers.authorization,'Bearer ai-only');assert.equal(req.headers['x-self-id'],'90000');
 const event={post_type:'message',message_type:'group',self_id:90000,message:[{type:'image',data:{file:'https://example.invalid/image'}}]};
 const eventWire=once(aiSocket,'message');client.send(JSON.stringify(event));assert.deepEqual(JSON.parse((await deadline(eventWire))[0]),event);
 const response=once(aiSocket,'message');aiSocket.send(JSON.stringify({action:'send_group_msg',params:{group_id:11111,message:'local fixture'},echo:{id:'original'}}));
 const reply=JSON.parse((await deadline(response))[0]);assert.deepEqual(reply.echo,{id:'original'});assert.equal(reply.data.message_id,19);assert.equal(writes,1);
 const pending=relay.dispatch('write_unknown',{},5000),unknown=assert.rejects(pending,/unknown/);await deadline(received.promise);relay.disconnectUpstream();await unknown;assert.equal(relay.pending.size,0);assert.equal(writes,1);
 const wrong=new WebSocket(`ws://127.0.0.1:${port}`,{headers:{'X-Self-ID':'80000'}});await deadline(once(wrong,'close'));assert.equal(relay.online,false);client.terminate();
});

test('protocol extensions use actual favorite bytes, enforce identity/path and require a real Qzone receipt',async()=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'llbot-actions-')),file=path.join(root,'image.gif'),bytes=Buffer.from('GIF89a-test-bytes');await writeFile(file,bytes);
 const self={uin:'90000',online:true},actions=new Map(),calls=[];
 const ctx={ntMsgApi:{getCustomFaceList:async()=>({retCode:0,emojiInfoList:[{emojiId:'one',url:'https://p.qpic.cn/favorite'}]}),addCustomFace:async image=>{assert.equal(image,file);return{retCode:0,emojiId:'one'}}},ntUserApi:{modifySelfProfile:async value=>{calls.push(value);return{errorCode:0}},getPSkey:async()=>new Map([['qzone.qq.com','private-pskey']])},ntWebApi:{getCookies:async()=>({skey:'private-skey'}),genBkn:()=>23}};
 let published=0;installActions(actions,ctx,self,{root,expectedUin:'90000'},{download:async()=>bytes,fetcher:async(url,options)=>{assert.equal(new URL(url).hostname,'user.qzone.qq.com');assert.equal(options.redirect,'error');assert.equal(options.body.get('con'),'local text');published++;return new Response(JSON.stringify({code:0,tid:'real-tid-fixture'}))}});
 const run=(action,params)=>actions.get(action).websocketHandle(params,'echo');
 assert.equal((await run('set_self_longnick',{longNick:'hello'})).retcode,0);assert.deepEqual(calls,[{bio:'hello'}]);
 assert.equal((await run('add_custom_face',{file})).retcode,0);const details=await run('fetch_custom_face_detail',{});assert.equal(details.data[0].md5,createHash('md5').update(bytes).digest('hex'));assert.equal(details.data[0].url,'https://p.qpic.cn/favorite');
 assert.equal((await run('add_custom_face',{file:path.join(root,'../outside')})).retcode,1200);
 assert.equal((await run('send_qzone_msg',{content:'local text'})).data.tid,'real-tid-fixture');assert.equal(published,1);
 assert.throws(()=>qzoneReceipt({code:0}),/回执/);
 assert.throws(()=>qzoneReceipt({code:-1,subcode:0,tid:'invalid'}),/拒绝/);
 assert.throws(()=>qzoneReceipt({code:0,subcode:-1,tid:'invalid'}),/拒绝/);
 assert.throws(()=>qzoneForm('90000','x',16),/targets/);
 self.uin='80000';assert.equal((await run('set_self_longnick',{longNick:'bad'})).retcode,1200);assert.equal(calls.length,1);
});

test('unverified favorite catalogs fail instead of retiring items from an incomplete list',async()=>{
 const actions=new Map();installActions(actions,{ntMsgApi:{getCustomFaceList:async()=>({retCode:0,emojiInfoList:[{emojiId:'unread',url:'https://p.qpic.cn/f'}]})}},{uin:'90000',online:true},{expectedUin:'90000'},{download:async()=>{throw Error('network')}});
 const ret=await actions.get('fetch_custom_face_detail').websocketHandle({},1);assert.equal(ret.status,'failed');assert.equal(ret.data,null);
});

test('LLBot memory policy protects active work, reclaims only JS, resets for replacement and requests bounded idle recovery',()=>{
 const state={},sample={at:1000000,pid:1,rssBytes:900*1048576,heapUsed:400*1048576,active:1,lastActivity:0,available:100,total:1000};
 assert.equal(resourceDecision(state,sample).decision,'busy');assert.equal(resourceDecision(state,{...sample,active:0}).decision,'reclaim-js');
 assert.equal(resourceDecision(state,{...sample,at:1300001,active:0}).decision,'reclaim-js');assert.equal(resourceDecision(state,{...sample,at:1300010,active:0}).decision,'restart-llbot');
 assert.equal(resourceDecision(state,{...sample,pid:2,rssBytes:1,heapUsed:1}).decision,'healthy');assert.equal(state.highSince,undefined);
});

test('runtime HTTP auth, profile/native proof, worker-only restart and PMHQ failure are isolated without executing QQ',async t=>{
 const f=await fixture(),children=[];let at=Date.now(),online=true,reachable=true;
 const spawnChild=(exe,args,options)=>{const child=new EventEmitter();Object.assign(child,{pid:500+children.length,connected:true,kill(){child.emit('exit',0)},send(){}});children.push({child,exe,args,options});return child};
 const fetcher=async url=>{if(String(url).endsWith('/health')&&!reachable)throw Error('unreachable');return new Response(JSON.stringify(String(url).endsWith('/health')?{uin:online?'90000':'',uid:online?'u_fixture':'',qq_pid:501,memory:{qq_using:12345,total:99999},cpu:{qq_using:1,total:100}}:String(url).endsWith('/api/login-info')?{success:true,data:{online,uin:'90000'}}:{success:true,data:{qrcodeUrl:'https://fixture.invalid/qr'}}))};
 const run=await startRuntime(f.file,{spawnChild,clock:()=>at,fetcher});t.after(()=>run.close());
 const health=()=>fetch(`http://127.0.0.1:${f.m.management.port}/api/health`,{headers:{Authorization:'Bearer '+f.m.management.token}}).then(r=>r.json());
 assert.equal((await fetch(`http://127.0.0.1:${f.m.management.port}/api/health`)).status,401);
 assert.equal(children.length,2);assert.ok(children[0].args.some(a=>a.includes('--work-dir='+path.join(f.m.root,'llbot/qq'))));assert.ok(children.every(c=>c.options.windowsHide));
 assert.ok(children.every(c=>c.options.stdio[1]==='pipe'&&c.options.stdio[2]==='pipe'),'native child output must reach the managed runner log instead of Windows NUL handles');
 assert.equal(new URL(children[1].args[children[1].args.indexOf('--import')+1]).protocol,'file:','Node --import requires a file URL for Windows drive paths');
 // Login cannot be accepted from the upstream WebUI alone.
 await run.sample();assert.notEqual((await health()).login.state,'online');
 await writeFile(path.join(f.m.root,'llbot/qq/profile-proof.json'),JSON.stringify({botId:'a',generation:2,pid:501,profile:path.join(f.m.root,'llbot/qq/profile'),network:true,route:'direct'}));
 run.relay.uin='90000';run.relay.upstream={close(){}};await run.sample();assert.equal((await health()).login.state,'online');
 children[1].child.emit('exit',1);at+=3001;await run.sample();assert.equal(children.length,3);assert.equal(children[0].child.listenerCount('exit'),1);assert.equal((await health()).metrics.restartRequested,false);
 online=false;const beforeDisconnect=children.length;await run.sample();at+=16000;await run.sample();
 assert.equal((await health()).login.state,'failed','a stale login QR cannot turn a transient established outage into manual reauthentication');
 assert.equal(children.length,beforeDisconnect,'account recovery must not repeatedly recycle the OneBot-only worker');
 reachable=false;at+=60001;await run.sample();assert.equal((await health()).metrics.restartRequested,true,'hung PMHQ requests normal controlled recovery');
 children[0].child.emit('exit',1);await run.sample();assert.equal((await health()).metrics.restartRequested,true);
});

test('QQ bootstrap isolates native profile and configures only its own MSF proxy, proving both before admission',async()=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'llbot-native-')),file=path.join(root,'manifest.json');await writeFile(file,JSON.stringify({botId:'one',generation:3,root,egress:{route:'huawei',proxyPort:17897}}));
 const calls=[],paths=[];class Util{};Object.defineProperty(Util,'getNTUserDataInfoConfig',{value:()=>'/shared',configurable:false});
 class Session{static getNTWrapperSession(){return{getMSFService:()=>({setNetworkProxy:config=>calls.push(config)})}}};
 const PMHQ={windowsDocumentPath:'unrelated-desktop',windowsDownloadPath:'unrelated-download'};
 const fakeProcess={env:{},pid:123,dlopen(){}};const require=createRequire(import.meta.url),module={exports:{}};
 vm.runInNewContext(await readFile('adapters/protocol/llbot/qq-bootstrap.cjs','utf8'),{module,require:name=>name==='electron'?{app:{setPath:(...args)=>paths.push(args)}}:require(name),process:fakeProcess,Buffer,PMHQ});
 module.exports(file);assert.equal(PMHQ.windowsDocumentPath,path.join(root,'llbot/qq/Documents'));assert.equal(PMHQ.windowsDownloadPath,path.join(root,'llbot/qq/Downloads'));const native={exports:{NodeQQNTWrapperUtil:Util,NodeIQQNTWrapperSession:Session}};fakeProcess.dlopen(native,'wrapper.node');assert.equal(native.exports.NodeQQNTWrapperUtil.getNTUserDataInfoConfig(),path.join(root,'llbot/qq/profile'));
 native.exports.NodeIQQNTWrapperSession.getNTWrapperSession('nt_1');await Promise.resolve();await Promise.resolve();
 const proof=JSON.parse(await readFile(path.join(root,'llbot/qq/profile-proof.json'),'utf8'));assert.equal(proof.network,true);assert.equal(proof.botId,'one');assert.equal(calls[0].proxyType,2);assert.equal(calls[0].port,17897);assert.equal(paths.length,2);
});


test('reviewed upstream registry and reverse class execute an installed signature action over real local HTTP/WS',async t=>{
 const source=path.resolve('../framework-91-8.3.2-research/inspected/test/LLBot-Local/bin/llbot/llbot.js');try{await access(source)}catch{t.skip('reviewed distribution absent');return}
 const patched=patchRuntime(await readFile(source));
 const classStart=patched.indexOf('var OB11WebSocketReverse ='),classEnd=patched.indexOf('\n};',classStart)+3;
 const registryStart=patched.indexOf('function initActionMap('),registryEnd=patched.indexOf('\n}',registryStart)+2,registry=patched.slice(registryStart,registryEnd);
 const selfInfo={uin:'90000',online:true},logger={info(){},error(){}},writes=[];
 const ctx={logger,ntUserApi:{modifySelfProfile:async value=>{writes.push(value);return{errorCode:0}}},interval:()=>()=>{},timeout:()=>()=>{}};
 const sandbox={WebSocket$1:WebSocket,matchEventFilter:()=>true,installActions,selfInfo,fishManifest:{expectedUin:'90000'},Map};
 // Native QQ services are fixture-only. The registry and transport execute the
 // exact reviewed distribution code; no original entry point/native code runs.
 for(const [,name] of registry.matchAll(/new ([\w$]+)\(adapter\)/g))sandbox[name]=class {constructor(){this.actionName='fixture-'+name}};
 vm.createContext(sandbox);vm.runInContext(patched.slice(classStart,classEnd)+'\n'+registry,sandbox);
 const actions=sandbox.initActionMap({ctx});assert.ok(actions.has('set_self_longnick'));
 actions.set('get_login_info',{websocketHandle:async(_params,echo)=>({status:'ok',retcode:0,data:{user_id:90000},echo})});
 hardenReverse(sandbox.OB11WebSocketReverse,WebSocket,{headers:()=>({'X-Self-ID':'90000',Authorization:'Bearer private'}),event:()=>({post_type:'meta_event',self_id:90000}),heartbeat:()=>({post_type:'meta_event',self_id:90000})});
 const server=createServer(),ws=new WebSocketServer({server}),ready=deferred();let relay;
 server.on('request',async(req,res)=>{try{const result=await relay.dispatch('set_self_longnick',{longNick:'wire fixture'});res.setHeader('Content-Type','application/json');res.end(JSON.stringify(result))}catch{res.writeHead(503).end()}});
 const port=await listen(server);relay=new LLBotRelay({expectedUin:'90000',url:'ws://127.0.0.1:1/ws',token:'none',onIdentity:()=>ready.resolve()});relay.connectAI=()=>{};
 ws.on('connection',(socket,req)=>{assert.equal(req.headers.authorization,'Bearer private');relay.attach(socket,req.headers['x-self-id'])});
 const reverse=new sandbox.OB11WebSocketReverse(ctx,{enable:true,url:`ws://127.0.0.1:${port}`,actionMap:actions,heartInterval:30000});
 t.after(async()=>{reverse.stop();relay.close();for(const socket of ws.clients)socket.terminate();ws.close();await new Promise(r=>server.close(r))});reverse.start();await deadline(ready.promise);
 const response=await fetch(`http://127.0.0.1:${port}`),result=await response.json();assert.equal(result.retcode,0);assert.equal(writes.length,1);assert.equal(writes[0].bio,'wire fixture');assert.ok(Object.keys(writes[0]).length===1);
});


test('Qzone image wire uses verified Bot files, exact richval and fixed credential destinations',async()=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'llbot-qzone-')),file=path.join(root,'picture.gif');await writeFile(file,Buffer.from('GIF89a-fixture'));
 const ctx={ntUserApi:{getPSkey:async()=>new Map([['qzone.qq.com','fixture-private']])},ntWebApi:{getCookies:async()=>({skey:'fixture-skey'}),genBkn:()=>55}},map=new Map();let requests=0;
 installActions(map,ctx,{uin:'90000',online:true},{root,expectedUin:'90000'},{fetcher:async(url,options)=>{
  requests++;assert.equal(options.redirect,'error');assert.ok(options.headers.Cookie.includes('fixture-private'));
  if(new URL(url).hostname==='up.qzone.qq.com'){assert.equal(options.body.get('base64'),'1');assert.equal(options.body.get('albumtype'),'7');assert.ok(options.body.get('backUrls').includes('up.qzone.qq.com'));return new Response('frameElement.callback('+JSON.stringify({code:0,data:{albumid:'album',lloc:'location',type:1,width:20,height:10}})+');</script>')}
  assert.equal(new URL(url).hostname,'user.qzone.qq.com');assert.equal(options.body.get('richval'),',album,location,location,1,10,20,,10,20');assert.equal(options.body.get('ugc_right'),'64');return new Response(JSON.stringify({subcode:0,t1_tid:'image-tid'}));
 }});
 const action=map.get('send_qzone_msg'),result=await action.websocketHandle({content:'local image fixture',images:[file],ugc_right:64},'image-echo');assert.equal(result.retcode,0,result.message);assert.equal(result.data.tid,'image-tid');assert.equal(requests,2);
 assert.equal((await action.websocketHandle({content:'invalid',images:{}},1)).retcode,1200);assert.equal(requests,2,'invalid input never submits');
});

test('Qzone accepts real ret-only upload receipts including type zero; upload failure never publishes',async()=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'llbot-qzone-ret-')),file=path.join(root,'picture.gif');await writeFile(file,Buffer.from('GIF89a-fixture'));
 const ctx={ntUserApi:{getPSkey:async()=>new Map([['qzone.qq.com','secret-pskey']])},ntWebApi:{getCookies:async()=>({skey:'secret-skey'}),genBkn:()=>55}};
 for(const upload of [{ret:0,type:22},{ret:0,type:0},{code:0,type:1},{ret:-1,type:22},{ret:0,code:-1,type:22},{ret:0,type:null}]){
  const map=new Map();let publishes=0;
  installActions(map,ctx,{uin:'90000',online:true},{root,expectedUin:'90000'},{fetcher:async(url,options)=>{
   if(new URL(url).hostname==='up.qzone.qq.com')return new Response('<script>frameElement.callback('+JSON.stringify({...upload,data:{albumid:'album',lloc:'location',type:upload.type,width:20,height:10}})+');</script>');
   publishes++;assert.equal(options.body.get('richval'),`,album,location,location,${upload.type},10,20,,10,20`);return new Response(JSON.stringify({code:0,tid:'receipt'}));
  }});
  const r=await map.get('send_qzone_msg').websocketHandle({content:'fixture',images:[file]},1),ok=upload.ret!==-1&&upload.code!==-1&&upload.type!==null;
  assert.equal(r.retcode,ok?0:1200);assert.equal(publishes,ok?1:0);
  if(!ok){assert.equal(r.data.receipt.status,'rejected');assert.equal(r.data.stage,'upload');assert.doesNotMatch(JSON.stringify(r),/secret-/)}
 }
});

test('Qzone distinguishes explicit publish rejection from an unknown submitted request',async()=>{
 const ctx={ntUserApi:{getPSkey:async()=>new Map([['qzone.qq.com','secret-pskey']])},ntWebApi:{getCookies:async()=>({skey:'secret-skey'}),genBkn:()=>55}};
 for(const result of [{code:-1,tid:'not-valid'},{code:0},'network']){
  const map=new Map();installActions(map,ctx,{uin:'90000',online:true},{expectedUin:'90000'},{fetcher:async()=>{if(result==='network')throw Error('secret-cookie');return new Response(JSON.stringify(result))}});
  const r=await map.get('send_qzone_msg').websocketHandle({content:'fixture'},1);
  assert.equal(r.retcode,1200);assert.equal(r.data.stage,'publish');assert.equal(r.data.receipt.status,result.code===-1?'rejected':'unknown');assert.doesNotMatch(JSON.stringify(r),/secret-/);
 }
});

test('late credentials after the operation deadline cannot start a Qzone publish',async t=>{
 const controller=new AbortController(),timeout=AbortSignal.timeout.bind(AbortSignal);t.mock.method(AbortSignal,'timeout',ms=>ms===45000?controller.signal:timeout(ms));
 const gate=deferred(),map=new Map();let writes=0;
 installActions(map,{ntUserApi:{getPSkey:async()=>new Map([['qzone.qq.com','secret']])},ntWebApi:{getCookies:()=>gate.promise,genBkn:()=>55}},{uin:'90000',online:true},{expectedUin:'90000'},{fetcher:async()=>{writes++;return new Response('{}')}});
 const flight=map.get('send_qzone_msg').websocketHandle({content:'fixture'},1);controller.abort();gate.resolve({skey:'secret'});
 const r=await flight;assert.equal(writes,0);assert.equal(r.data.receipt.status,'rejected');assert.equal(r.data.stage,'prepare');
});


test('LLBot explicit QR never quick-logins while resume passes the pinned account to both owned components',async t=>{
 for(const resume of [false,true]){
  const f=await fixture();await writeFile(f.file,JSON.stringify({...f.m,resume}));const children=[];
  const spawnChild=(exe,args)=>{const child=new EventEmitter();Object.assign(child,{pid:700+children.length,kill(){child.emit('exit',0)}});children.push({exe,args});return child};
  const run=await startRuntime(f.file,{spawnChild});
  try{assert.equal(children.length,2);for(const c of children)assert.equal(c.args.includes('--qq=90000'),resume,'the QQ login intent must be identical in PMHQ and LLBot')}
  finally{await run.close()}
 }
});
