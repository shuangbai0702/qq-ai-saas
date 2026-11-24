import path from 'node:path';
import {mkdir,readFile,access} from 'node:fs/promises';
import {randomBytes} from 'node:crypto';
import lockfile from 'proper-lockfile';
import type {ProtocolAdapter,ProtocolContext,ProcessHostLike,Health,LoginSnapshot} from '../../../packages/contracts/index.js';
import {EgressSchema,initialEgress,nextEgress,observeEgress,proxyPort,type QQEgress} from '../egress.js';
import {atomicJson} from '../../../packages/shared/fs-atomic.js';

type Settings={runtimeDir:string;qqPath:string;token:string;authToken:string;egress?:QQEgress};

export class LLBotAdapter implements ProtocolAdapter {
 constructor(private host:ProcessHostLike,private settingsRoot:string,private python=path.resolve(process.env.ASTRBOT_PYTHON||`references/astrbot/.venv/${process.platform==='win32'?'Scripts/python.exe':'bin/python'}`)){}
 private async withSettings<T>(ctx:ProtocolContext,edit:(s:Settings,save:()=>Promise<void>)=>Promise<T>){
  await mkdir(this.settingsRoot,{recursive:true});
  const file=path.join(this.settingsRoot,ctx.botId+'.json');
  const release=await lockfile.lock(file,{realpath:false,retries:{retries:30,minTimeout:20,maxTimeout:100}});
  try{
   let settings:Settings;
   try{settings=JSON.parse(await readFile(file,'utf8'))}catch(error){if((error as NodeJS.ErrnoException).code!=='ENOENT')throw error;
    settings={runtimeDir:path.resolve(process.env.LLBOT_RUNTIME_DIR||'references/llbot-8.3.2'),qqPath:path.resolve(process.env.LLBOT_QQ_PATH||'references/llbot-qq/QQ.exe'),token:randomBytes(32).toString('hex'),authToken:process.env.LLBOT_AUTH_TOKEN||''};
    await atomicJson(file,settings);
   }
   const override={runtimeDir:process.env.LLBOT_RUNTIME_DIR?path.resolve(process.env.LLBOT_RUNTIME_DIR):settings.runtimeDir,qqPath:process.env.LLBOT_QQ_PATH?path.resolve(process.env.LLBOT_QQ_PATH):settings.qqPath,authToken:process.env.LLBOT_AUTH_TOKEN||settings.authToken};
   if(Object.entries(override).some(([key,value])=>settings[key as keyof Settings]!==value)){Object.assign(settings,override);await atomicJson(file,settings)}
   if(settings.egress)settings.egress=EgressSchema.parse(settings.egress);
   return await edit(settings,()=>atomicJson(file,settings));
  }finally{await release()}
 }
 private settings(ctx:ProtocolContext){return this.withSettings(ctx,async s=>s)}
 async provision(ctx:ProtocolContext){return this.withSettings(ctx,async s=>this.writeManifest(ctx,s))}
 private async writeManifest(ctx:ProtocolContext,settings:Settings){
  await access(path.join(settings.runtimeDir,'fishbot-upstream.json')).catch(()=>{throw new Error('LLBot运行环境未安装，请运行 npm run setup:llbot -- --archive <8.3.2测试版ZIP>')});
  await access(settings.qqPath).catch(()=>{throw new Error('LLBot独立QQNT运行环境未配置，请设置LLBOT_QQ_PATH')});
  const authMode=settings.authToken?'token':'local';
  if(authMode==='local')await access(path.join(settings.runtimeDir,'bin/pmhq/auth-local/sign-info.json')).catch(()=>{throw new Error('LLBot本地启动组件未安装，请重新运行 setup:llbot')});
  await mkdir(path.join(ctx.root,'llbot'),{recursive:true});
  await atomicJson(path.join(ctx.root,'llbot-platform.json'),{botId:ctx.botId,tenantId:ctx.tenantId,generation:ctx.generation,expectedUin:ctx.expectedUin,resume:ctx.loginIntent!=='qr',runtimeDir:settings.runtimeDir,qqPath:settings.qqPath,authMode,authToken:settings.authToken||'local-no-token',python:this.python,root:ctx.root,egress:{route:settings.egress?.route||'direct',proxyPort:settings.egress?.route==='huawei'?proxyPort():undefined},management:{host:'127.0.0.1',port:ctx.protocolPort,token:settings.token},pmhqPort:ctx.aiPort+1,webuiPort:ctx.protocolPort+1,reverseWs:{url:`ws://127.0.0.1:${ctx.aiPort}/ws`,token:ctx.token}});
 }
 async beginLogin(ctx:ProtocolContext){
  return this.withSettings(ctx,async(s,save)=>{
   if(await this.host.inspect(ctx.botId,'protocol'))return;
   if(proxyPort()||s.egress)s.egress=nextEgress(s.egress||initialEgress());
   if(s.egress)delete s.egress.process;
   await save();await this.writeManifest(ctx,s);
   await this.host.start(ctx.botId,'protocol',path.join(s.runtimeDir,'bin/llbot',process.platform==='win32'?'node.exe':'node'),[path.resolve('adapters/protocol/llbot/runner.mjs'),path.join(ctx.root,'llbot-platform.json')],ctx.root,process.env,[s.token,s.authToken,ctx.token]);
  });
 }
 private async request(ctx:ProtocolContext,route:string){
  const s=await this.settings(ctx);
  const response=await fetch(`http://127.0.0.1:${ctx.protocolPort}/api/${route}`,{headers:{Authorization:'Bearer '+s.token},signal:AbortSignal.timeout(5000)});
  const data=await response.json() as any;
  if(!response.ok||data.botId!==ctx.botId)throw new Error('LLBot实例未就绪');
  // An already running QQ survives Agent/AI generation changes. Its process
  // lifetime is verified by ProcessHost; the protocol has no AI/config authority.
  return data;
 }
 async getLoginState(ctx:ProtocolContext):Promise<LoginSnapshot>{
  const process=await this.host.inspect(ctx.botId,'protocol');if(!process)return{state:'failed',message:'LLBot协议进程未运行'};
  const {login}=await this.request(ctx,'health');const current=await this.host.record(ctx.botId,'protocol');
  if(current?.pid!==process.pid||current?.created!==process.created)throw new Error('LLBot进程已换代，请刷新状态');
  if(login?.state==='online'&&ctx.expectedUin&&login.uin!==ctx.expectedUin)throw new Error('LLBot实际QQ与绑定账号不一致');
  return login||{state:'starting'};
 }
 async queryIdentity(ctx:ProtocolContext){const s=await this.getLoginState(ctx);return s.state==='online'?s.uin||null:null}
 async cancelLogin(ctx:ProtocolContext){await this.stop(ctx.botId)}
 async getHealth(ctx:ProtocolContext):Promise<Health>{
  const process=await this.host.inspect(ctx.botId,'protocol');
  if(!process)return{alive:false,ready:false,online:false,mode:'real',detail:'LLBot未运行'};
  try{const data=await this.request(ctx,'health');const login=data.login as LoginSnapshot;
   const identityValid=login?.state!=='online'||!ctx.expectedUin||login.uin===ctx.expectedUin;
   if(!identityValid)throw new Error('LLBot实际QQ与绑定账号不一致');
   const record=await this.host.record(ctx.botId,'protocol');if(record?.pid!==process.pid||record?.created!==process.created)throw new Error('LLBot进程已换代，请刷新状态');
   let egress:{route:string;switches:number;failures:number;pending:boolean}|undefined;
   await this.withSettings(ctx,async(s,save)=>{
    const current=await this.host.record(ctx.botId,'protocol');
    if(s.egress&&current?.pid===process.pid&&current.created===process.created){
     if(!s.egress.process||s.egress.process.pid!==current.pid||s.egress.process.created!==current.created)s.egress.process={pid:current.pid,created:current.created!,generation:ctx.generation,online:false,counted:false};
     s.egress=observeEgress(s.egress,{pid:current.pid,created:current.created!,generation:s.egress.process.generation},login?.state==='online',data.metrics?.qqDisconnected===true);
     await save();egress={route:s.egress.route,switches:s.egress.switches,failures:s.egress.failures,pending:s.egress.pending};
    }
   });
   return{alive:true,ready:data.ready===true,online:login?.state==='online',loginState:login?.state,pid:process.pid,rssBytes:process.rssBytes,memoryPolicy:data.resources,metrics:{...data.metrics,egress,components:data.components,restartRequested:data.metrics?.restartRequested===true||egress?.pending===true&&data.metrics?.qqDisconnected===true},mode:'real',detail:data.detail};
  }catch(error){return{alive:true,ready:false,online:false,pid:process.pid,rssBytes:process.rssBytes,mode:'real',detail:(error as Error).message}}
 }
 getCapabilities(){return['qr','cancel','text','image','record','onebot-reverse-ws','private-management-api','qq-profile','qq-favorites','qzone']}
 async stop(botId:string){await this.host.stop(botId,'protocol')}
}
