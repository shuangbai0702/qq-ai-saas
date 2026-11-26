import path from 'node:path';
import os from 'node:os';
import http from 'node:http';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {spawn} from 'node:child_process';
import {mkdir,readFile,writeFile,copyFile,symlink,realpath,rm} from 'node:fs/promises';
import {createHash,timingSafeEqual} from 'node:crypto';
import {WebSocketServer} from 'ws';
import {LLBotRelay} from './relay.mjs';
import {resourceDecision} from './resource-policy.mjs';
import {localAuth} from './local-auth.mjs';
import {prepareQqAssets} from './qq-assets.mjs';

const directory=path.dirname(fileURLToPath(import.meta.url));
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
export function validateManifest(m,file){
 const root=path.dirname(path.resolve(file));
 const ai=/^ws:\/\/127\.0\.0\.1:([1-9]\d{0,4})\/ws$/.exec(m.reverseWs?.url||'');
 const ports=[Number(ai?.[1]),m.pmhqPort,m.management?.port,m.webuiPort];
 if(!['local','token'].includes(m.authMode)||m.authMode==='local'&&(!path.isAbsolute(m.python||'')||process.platform!=='win32'))throw Error('Invalid private LLBot auth mode');
 if(m.root!==root||!m.botId||!Number.isInteger(m.generation)||!ai||!ports.every(p=>Number.isInteger(p)&&p>1024&&p<65536)||new Set(ports).size!==4||m.management?.host!=='127.0.0.1'||!/^\d{5,20}$|^$/.test(m.expectedUin)||!m.authToken||typeof m.runtimeDir!=='string'||typeof m.qqPath!=='string'||!['direct','huawei'].includes(m.egress?.route)||m.egress.route==='huawei'&&(!Number.isInteger(m.egress.proxyPort)||m.egress.proxyPort<1||m.egress.proxyPort>65535)||!/^[a-f0-9]{64}$/i.test(m.management?.token||'')||!/^[a-f0-9]{64}$/i.test(m.reverseWs?.token||''))throw Error('Invalid private LLBot manifest');
 return m;
}
export async function prepareRuntime(file){
 const m=validateManifest(JSON.parse(await readFile(file,'utf8')),file);
 const upstream=JSON.parse(await readFile(path.join(m.runtimeDir,'fishbot-upstream.json'),'utf8'));
 if(!upstream.sha256||!Object.keys(upstream.sha256).length)throw Error('LLBot integrity manifest missing');
 if(upstream.version!=='8.3.2-test'||upstream.platform!==process.platform)throw Error('LLBot runtime platform/version mismatch');
 for(const [name,digest] of Object.entries(upstream.sha256)){if(!/^[a-f0-9]{64}$/.test(digest)||path.isAbsolute(name)||name.split(/[\\/]/).includes('..')||hash(await readFile(path.join(m.runtimeDir,name)))!==digest)throw Error('LLBot runtime integrity check failed')}
 const work=path.join(m.root,'llbot','client'),home=path.join(m.root,'llbot','qq');
 await mkdir(path.join(work,'data'),{recursive:true});await mkdir(home,{recursive:true});
 for(const name of ['llbot.js','fishbot-actions.mjs','fishbot-lifecycle.mjs','package.json'])await copyFile(path.join(m.runtimeDir,'bin/llbot',name),path.join(work,name));
 for(const name of ['node_modules','webui','sign-proxy.win-x64.node','sign-proxy.package.json','ffmpeg.exe','ffprobe.exe']){
  const src=path.join(m.runtimeDir,'bin/llbot',name),dest=path.join(work,name);
  if(['node_modules','webui'].includes(name)){
   try{if(await realpath(dest)!==await realpath(src))throw Error('LLBot shared asset ownership mismatch')}catch(error){if(error.code!=='ENOENT')throw error;await symlink(src,dest,process.platform==='win32'?'junction':'dir')}
  }else await copyFile(src,dest);
 }
 const config={webui:{enable:true,host:'127.0.0.1',port:m.webuiPort},milky:{enable:false},satori:{enable:false},ob11:{enable:true,connect:[{type:'ws-reverse',enable:true,url:`ws://127.0.0.1:${m.management.port}/upstream`,token:m.management.token,heartInterval:30000,messageFormat:'array',reportSelfMessage:false,reportOfflineMessage:false}]},enableLocalFile2Url:false,log:false,logLevel:'warn',autoDeleteFile:false,autoDeleteFileSecond:300,msgCacheExpire:120,ffmpeg:path.join(work,'ffmpeg.exe')};
 await writeFile(path.join(work,'default_config.json'),JSON.stringify(config),{mode:0o600});
 if(m.expectedUin)await writeFile(path.join(work,'data',`config_${m.expectedUin}.json`),JSON.stringify(config),{mode:0o600});
 await writeFile(path.join(work,'data','webui_token.txt'),m.management.token,{mode:0o600});
 const bootstrap=path.join(m.root,'llbot','qq-bootstrap.cjs');
 await writeFile(bootstrap,`require(${JSON.stringify(path.join(directory,'qq-bootstrap.cjs'))})(${JSON.stringify(path.resolve(file))});\n`,{mode:0o600});
 // PMHQ 9 no longer executes --js-path. Install the profile hook through this
 // Bot's private Electron main metadata, before the original QQ entry runs.
 const qqPath=await prepareQqAssets(m.qqPath,home,bootstrap);
 await writeFile(path.join(home,'pmhq_config.json'),JSON.stringify({default_host:'127.0.0.1',default_port:m.pmhqPort,debug:false,debug_pb:false,qq_console:false,headless:false}),{mode:0o600});
 await rm(path.join(home,'profile-proof.json'),{force:true});
 return{manifest:m,work,home,bootstrap,qqPath};
}
const authorized=(request,token)=>{const received=Buffer.from(request.headers.authorization||''),wanted=Buffer.from('Bearer '+token);return received.length===wanted.length&&timingSafeEqual(received,wanted)};
async function jsonBody(request){const chunks=[];let size=0;for await(const chunk of request){size+=chunk.length;if(size>1048576)throw Error('Request too large');chunks.push(chunk)}return JSON.parse(Buffer.concat(chunks).toString('utf8')||'{}')}
export async function startRuntime(file,{spawnChild=spawn,clock=()=>Date.now(),fetcher=fetch}={}){
 const {manifest:m,work,home,qqPath}=await prepareRuntime(file);
 let timer,auth,sampleTask,closeTask;let closing=false,worker,pmhq,epoch=0,workerMemory,lastMemory=0,workerStarted=0,workerRestartAt=0,workerRestarts=[],pmhqExited=false,detail='LLBot正在启动',qqResources={},qqDisconnected=false,wasOnline=false,nativeOnline=false,offlineAt=0;
 let lastNativeHealth=clock(),nativeSeen=false;
 const lifetime=new AbortController(),requestSignal=ms=>AbortSignal.any([lifetime.signal,AbortSignal.timeout(ms)]);
 const policy={};let resourceStatus={decision:'starting'};let login={state:'starting'};
 const relay=new LLBotRelay({expectedUin:m.expectedUin,url:m.reverseWs.url,token:m.reverseWs.token,onIdentity:async uin=>{
  const native=await nativeHealth();
  if(closing||String(native.uin)!==uin||!native.uid||m.expectedUin&&uin!==m.expectedUin)throw Error('Native QQ identity mismatch');
  await profileProof(native.qq_pid);lifetime.signal.throwIfAborted();
 }});
 async function profileProof(pid){const proof=JSON.parse(await readFile(path.join(home,'profile-proof.json'),'utf8'));if(proof.botId!==m.botId||proof.generation!==m.generation||proof.profile!==path.join(home,'profile')||!Number.isInteger(proof.pid)||proof.pid!==pid||proof.network!==true||proof.route!==m.egress.route)throw Error('Private QQ profile proof unavailable');return proof}
 async function nativeHealth(){
  const response=await fetcher(`http://127.0.0.1:${m.pmhqPort}/health`,{signal:requestSignal(3000)});
  if(!response.ok)throw Error('PMHQ health unavailable');const value=await response.json();
  if(!value||typeof value!=='object'||Array.isArray(value))throw Error('Invalid PMHQ health');
  return value;
 }
 const env={...process.env,HOME:home,USERPROFILE:home,APPDATA:path.join(home,'AppData','Roaming'),LOCALAPPDATA:path.join(home,'AppData','Local')};
 // Windows ProcessHost captures the runner's JS writes; inherited native
 // handles point at NUL. Pipe each owned child's output through that capture.
 function forwardOutput(child){child.stdout?.on('data',chunk=>process.stdout.write(chunk));child.stderr?.on('data',chunk=>process.stderr.write(chunk))}
 function launchWorker(){
  if(closing||pmhqExited)return;
  relay.disconnectUpstream();const currentEpoch=++epoch;
  login={state:'starting'};resourceStatus={decision:'starting'};for(const key of Object.keys(policy))delete policy[key];
  workerMemory=undefined;lastMemory=clock();workerStarted=clock();
  const child=spawnChild(process.execPath,['--expose-gc','--max-old-space-size=640','--import',pathToFileURL(path.join(directory,'worker-preload.mjs')).href,path.join(work,'llbot.js'),'--',`--pmhq-port=${m.pmhqPort}`,...(m.resume&&m.expectedUin?['--qq='+m.expectedUin]:[])],{cwd:work,env:{...env,FISHBOT_LLBOT_MANIFEST:path.resolve(file)},windowsHide:true,stdio:['ignore','pipe','pipe','ipc']});worker=child;forwardOutput(child);
  const ended=reason=>{
   if(epoch!==currentEpoch||worker!==child||closing)return;
   worker=undefined;workerMemory=undefined;login={state:'starting'};relay.disconnectUpstream();workerRestartAt=clock()+3000;detail=reason;
  };
  child.on('error',()=>{
   if(epoch!==currentEpoch||worker!==child||closing)return;
   detail='LLBot子进程启动或通信失败';
   // A failed spawn emits error/close without exit and has no PID. A running
   // child's IPC error does not prove death; retain its owner until exit/close.
   if(!child.pid)ended('LLBot子进程启动失败，QQ会话保留，等待局部恢复');
  });
  child.on('message',value=>{if(epoch===currentEpoch&&worker===child&&!closing&&value?.type==='llbot-memory'&&value.pid===child.pid&&[value.rss,value.heapUsed].every(Number.isFinite)){workerMemory=value;lastMemory=clock()}});
  child.once('exit',()=>ended('LLBot子进程退出，QQ会话保留，等待局部恢复'));
  child.once('close',()=>ended('LLBot子进程已关闭，QQ会话保留，等待局部恢复'));
 }
 function recycleWorker(reason){
  if(!worker||relay.active||closing)return false;
  const now=clock();workerRestarts=workerRestarts.filter(at=>at>now-3600000);if(workerRestarts.length>=3){detail='LLBot子进程恢复已达每小时上限，请检查日志';return false}
  detail=reason;worker.kill();return true;
 }
 const server=http.createServer(async(request,response)=>{
  response.setHeader('Content-Type','application/json');response.setHeader('Cache-Control','no-store');
  const send=(status,value)=>{if(!response.destroyed&&!response.writableEnded){response.writeHead(status);response.end(JSON.stringify(value))}};
  if(!authorized(request,m.management.token))return send(401,{error:'Unauthorized'});
  try{
   if(request.method==='GET'&&request.url==='/api/health'){
    const ready=!closing&&!pmhqExited&&!!worker&&relay.online&&nativeOnline&&login.state==='online';
    return send(200,{botId:m.botId,generation:m.generation,ready,login:login.state==='online'&&!ready?{state:'starting'}:login,detail,resources:{strategy:'llbot-owned-components',...resourceStatus,worker:workerMemory?{pid:workerMemory.pid,rssBytes:workerMemory.rss,heapUsed:workerMemory.heapUsed}:null,workerRestarts:workerRestarts.length},components:{pmhq:{pid:pmhq?.pid,alive:!pmhqExited},llbot:{pid:worker?.pid,alive:!!worker},qqnt:{profileIsolated:login.state==='online',nativeOnline,...qqResources}},metrics:{restartRequested:pmhqExited||clock()-lastNativeHealth>(nativeSeen?60000:180000),qqDisconnected,transport:relay.transport}});
   }
   if(request.method==='GET'&&request.url==='/api/login-qrcode'){
    const upstream=await fetcher(`http://127.0.0.1:${m.webuiPort}/api/login-qrcode`,{headers:{'X-Webui-Token':hash(m.management.token)},signal:requestSignal(4000)});const data=await upstream.json();
    if(!upstream.ok||!data.success)throw Error('QR unavailable');return send(200,{botId:m.botId,...data});
   }
   if(request.method==='POST'&&request.url?.startsWith('/actions/')){
    if(pmhqExited||closing)throw Error('Protocol unavailable');
    const action=request.url.slice('/actions/'.length);if(!/^[a-z_]{1,64}$/.test(action))return send(400,{error:'Invalid action'});
    const data=await relay.dispatch(action,await jsonBody(request),action==='fetch_custom_face_detail'?600000:60000);return send(200,data);
   }
   return send(404,{error:'Not found'});
  }catch{return send(503,{botId:m.botId,error:'Protocol request failed or outcome unknown'})}
 });
 const ws=new WebSocketServer({noServer:true,maxPayload:16*1024*1024,perMessageDeflate:false});
 server.on('upgrade',(request,socket,head)=>{
  if(request.url!=='/upstream'||!authorized(request,m.management.token)||closing){socket.destroy();return}
  ws.handleUpgrade(request,socket,head,client=>relay.attach(client,String(request.headers['x-self-id']||'')));
 });
 await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(m.management.port,'127.0.0.1',resolve)});
 try{
  if(m.authMode==='local')auth=await localAuth(server,{manifest:m,spawnChild,getProcess:()=>pmhq,onFailure:()=>{pmhqExited=true;detail='LLBot私有本地启动组件失败，等待受控恢复'}});
  const pmhqEnv=auth?{...env,HTTPS_PROXY:auth.proxy,HTTP_PROXY:auth.proxy,ALL_PROXY:auth.proxy,https_proxy:auth.proxy,http_proxy:auth.proxy,all_proxy:auth.proxy,NO_PROXY:'127.0.0.1,localhost',no_proxy:'127.0.0.1,localhost'}:env;
  pmhq=spawnChild(path.join(m.runtimeDir,'bin/pmhq',process.platform==='win32'?'pmhq.exe':'pmhq'),['--mode=inline',`--config=${path.join(home,'pmhq_config.json')}`,'--host=127.0.0.1',`--port=${m.pmhqPort}`,`--auth-token=${m.authToken}`,`--qq-path=${qqPath}`,`--work-dir=${home}`,`--qq-args=--multi-instance --user-data-dir="${path.join(home,'profile')}"`,...(m.resume&&m.expectedUin?[`--qq=${m.expectedUin}`]:[]),'--exit-with-qq-delay=15'],{cwd:home,env:pmhqEnv,windowsHide:true,stdio:['ignore','pipe','pipe']});forwardOutput(pmhq);
  const nativeEnded=()=>{if(closing)return;pmhqExited=true;nativeOnline=false;login={state:'failed',message:'PMHQ已退出，等待平台受控恢复'};relay.disconnectUpstream();detail=login.message};
  pmhq.on('error',()=>{if(!pmhq.pid)nativeEnded()});pmhq.once('exit',nativeEnded);pmhq.once('close',nativeEnded);launchWorker();
 }catch{await close();throw Error('LLBot component launch failed')}
 timer=setInterval(()=>{void sample()},5000);timer.unref();
 function sample(){if(closing)return Promise.resolve();return sampleTask??=sampleOnce().finally(()=>{sampleTask=undefined})}
 async function sampleOnce(){
  let current=()=>!closing;
  try{
   if(pmhqExited)return;
   const now=clock();workerRestarts=workerRestarts.filter(at=>at>now-3600000);
   if(!worker&&now>=workerRestartAt&&workerRestarts.length<3){workerRestarts.push(now);launchWorker()}
   if(worker&&now-lastMemory>60000&&now-workerStarted>60000)recycleWorker('LLBot无心跳，局部恢复，不重启QQ');
   if(workerMemory){resourceStatus=resourceDecision(policy,{at:now,pid:workerMemory.pid,rssBytes:workerMemory.rss,heapUsed:workerMemory.heapUsed,active:relay.active,lastActivity:relay.lastActivity,available:os.freemem(),total:os.totalmem()});
    if(resourceStatus.decision==='reclaim-js'&&worker?.connected){const child=worker;try{child.send({type:'reclaim-js'},error=>{if(error&&!closing&&worker===child)detail='LLBot内存回收通信失败，等待健康检查'})}catch{detail='LLBot内存回收通信失败，等待健康检查'}}
    if(resourceStatus.decision==='restart-llbot')recycleWorker('LLBot持续内存压力，局部恢复，不重启QQ');
   }
   const observedEpoch=epoch,observedWorker=worker;current=()=>!closing&&!pmhqExited&&epoch===observedEpoch&&worker===observedWorker;
   const native=await nativeHealth().catch(()=>null);if(!current())return;
   nativeOnline=!!native?.uin&&!!native.uid&&(!m.expectedUin||String(native.uin)===m.expectedUin);
   if(native){lastNativeHealth=clock();nativeSeen=true;qqResources={rssBytes:Number.isFinite(native.memory?.qq_using)?native.memory.qq_using:undefined,cpuPercent:native.cpu?.total>0?Math.max(0,Math.min(100,native.cpu.qq_using/native.cpu.total*100)):undefined}}
   if(wasOnline&&!nativeOnline){offlineAt||=now;qqDisconnected=now-offlineAt>=15000;if(qqDisconnected)relay.disconnectUpstream()}else{offlineAt=0;qqDisconnected=false}
   const response=await fetcher(`http://127.0.0.1:${m.webuiPort}/api/login-info`,{headers:{'X-Webui-Token':hash(m.management.token)},signal:requestSignal(3000)});const state=await response.json();
   if(!current())return;
   if(!response.ok||!state.success)throw Error('LLBot login probe unavailable');
   if(state.data.online&&nativeOnline){
    const uin=String(state.data.uin||'');const proof=await profileProof(native.qq_pid);if(!current())return;
    if(String(native.uin)!==uin)throw Error('Native QQ identity mismatch');
    if(proof.botId!==m.botId||proof.profile!==path.join(home,'profile')||!Number.isInteger(proof.pid)||!relay.online||relay.uin!==uin||m.expectedUin&&uin!==m.expectedUin)throw Error('LLBot profile/identity proof missing');
    login={state:'online',uin};wasOnline=true;detail='';
   }else if(wasOnline&&!nativeOnline){
    // An established account's disconnect belongs to Agent account recovery;
    // replacing only OneBot cannot restore QQ or prove a fresh login QR.
    login={state:'failed',message:'QQ会话离线，等待自动恢复'};
   }else{
    const q=await fetcher(`http://127.0.0.1:${m.webuiPort}/api/login-qrcode`,{headers:{'X-Webui-Token':hash(m.management.token)},signal:requestSignal(3000)});const qr=await q.json();if(!current())return;
    const data=qr.success&&qr.data;
    login=data?.qrcodeUrl?{state:'waiting_scan',qr:data.qrcodeUrl}:{state:'starting'};
   }
  }catch{if(current()){login=qqDisconnected?{state:'failed',message:'QQ已掉线'}:{state:'starting'};detail||='LLBot正在等待可验证的QQ登录、私有目录和协议连接'}}
 }
 function close(){
  if(closeTask)return closeTask;closing=true;lifetime.abort();clearInterval(timer);
  return closeTask=(async()=>{
   relay.close();for(const client of ws.clients)client.terminate();ws.close();auth?.close();
   const errors=[];for(const child of [worker,pmhq])try{child?.kill()}catch(error){errors.push(error)}
   await new Promise(resolve=>{server.close(resolve);server.closeAllConnections()});
   if(errors.length)throw new AggregateError(errors,'LLBot owned component cleanup failed');
  })();
 }
 return{server,relay,close,sample};
}
if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url))startRuntime(process.argv[2]).then(runtime=>{for(const signal of ['SIGTERM','SIGINT'])process.once(signal,()=>{void runtime.close().finally(()=>process.exit(0))})}).catch(error=>{console.error('LLBot managed startup failed:',error?.stack||String(error));process.exit(1)});
