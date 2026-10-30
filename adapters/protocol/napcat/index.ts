import {createHash,randomBytes} from 'node:crypto';import path from 'node:path';import {readFile,writeFile,mkdir,access} from 'node:fs/promises';
import type {ProtocolAdapter,RuntimeContext,LoginSnapshot} from '../../../packages/contracts/index.js';import {ProcessHost} from '../../../apps/node-agent/src/process-host.js';
interface Endpoint {url:string;webuiToken:string;launcher?:string;args?:string[];runtimeDir?:string}
export class NapCatAdapter implements ProtocolAdapter{
 resumeAttempts=new Map<string,number>();
 credentials=new Map<string,{credential:string;until:number}>();
 constructor(private host:ProcessHost,private settingsRoot:string){}
 async settings(ctx:RuntimeContext):Promise<Endpoint>{
  let s:Endpoint;const file=path.join(this.settingsRoot,`${ctx.botId}.json`);
  try{s=JSON.parse(await readFile(file,'utf8'))}catch{
   const runtimeDir=path.resolve(process.env.NAPCAT_RUNTIME_DIR||(process.platform==='win32'?'references/napcat-windows-node':'references/napcat-linux-node'));
   try{await access(path.join(runtimeDir,'fishbot-upstream.json'))}catch{throw new Error('NapCat便携运行环境未安装：运行 python scripts/napcat-setup.py；不会使用已有桌面QQ')}
   s={url:`http://127.0.0.1:${ctx.protocolPort}/api`,webuiToken:randomBytes(32).toString('hex'),runtimeDir,launcher:path.join(runtimeDir,process.platform==='win32'?'node.exe':'node'),args:[path.resolve('adapters/protocol/napcat/runner.mjs')]};
   await mkdir(this.settingsRoot,{recursive:true});await writeFile(file,JSON.stringify(s,null,2),{mode:0o600,flag:'wx'});
  }
  const u=new URL(s.url);if(u.hostname!=='127.0.0.1'||u.protocol!=='http:')throw new Error('NapCat管理端点仅允许本机HTTP');return s
 }
 async request(ctx:RuntimeContext,action:string,body:unknown={},retry=true):Promise<any>{const s=await this.settings(ctx);let credential=this.credentials.get(ctx.botId);if(!credential||credential.until<Date.now()){const r=await fetch(s.url+'/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({hash:createHash('sha256').update(s.webuiToken+'.napcat').digest('hex')}),signal:AbortSignal.timeout(4000)});const data:any=await r.json();if(!data.data?.Credential)throw new Error('NapCat管理认证失败');credential={credential:data.data.Credential,until:Date.now()+3000000};this.credentials.set(ctx.botId,credential)}
 const r=await fetch(s.url+action,{method:'POST',headers:{'Content-Type':'application/json',Authorization:`Bearer ${credential.credential}`},body:JSON.stringify(body),signal:AbortSignal.timeout(4000)});const data:any=await r.json();if(data.code!==0){if(retry&&String(data.message).includes('Unauthorized')){this.credentials.delete(ctx.botId);return this.request(ctx,action,body,false)}throw new Error('NapCat管理请求失败：'+(data.message||'unknown'))}return data.data}
 async provision(ctx:RuntimeContext){const s=await this.settings(ctx);await mkdir(path.join(ctx.root,'qq'),{recursive:true});const u=new URL(s.url);await writeFile(path.join(ctx.root,'napcat-platform.json'),JSON.stringify({botId:ctx.botId,resume:ctx.loginIntent!=='qr',expectedUin:ctx.expectedUin,dataDir:path.join(ctx.root,'qq'),runtimeDir:s.runtimeDir,reverseWs:{url:`ws://127.0.0.1:${ctx.aiPort}/ws`,token:ctx.token},management:{host:'127.0.0.1',port:Number(u.port),token:s.webuiToken}}),{mode:0o600})}
 async beginLogin(ctx:RuntimeContext){await this.provision(ctx);const s=await this.settings(ctx);if(!s.launcher)throw new Error('NapCat需配置独立受控launcher后才能在网页登录，避免无法撤销外部会话');this.credentials.delete(ctx.botId);await this.host.start(ctx.botId,'protocol',s.launcher,[...(s.args||[]),path.join(ctx.root,'napcat-platform.json')],ctx.root,{...process.env,NAPCAT_PLATFORM_MANIFEST:path.join(ctx.root,'napcat-platform.json'),LD_LIBRARY_PATH:s.runtimeDir},[s.webuiToken,ctx.token]);}

 async getLoginState(ctx:RuntimeContext):Promise<LoginSnapshot>{
  const s=await this.request(ctx,'/QQLogin/CheckLoginStatus');
  if(s.isLogin&&s.coreReady){const info=await this.request(ctx,'/QQLogin/GetQQLoginInfo');const uin=String(info.uin||info.selfInfo?.uin||'');if(/^\d{5,20}$/.test(uin)&&(!ctx.expectedUin||ctx.expectedUin===uin)){await mkdir(path.join(ctx.root,'qq'),{recursive:true});await writeFile(path.join(ctx.root,'qq/identity.json'),JSON.stringify({uin}),{mode:0o600})}return{state:'online',uin}}
  // The native quick login can race network readiness. Once per process, retry
  // the saved account after QR readiness; explicit QR refresh never uses this.
  if(s.loginPhase==='waiting_qrcode')try{const manifest=JSON.parse(await readFile(path.join(ctx.root,'napcat-platform.json'),'utf8'));const p=await this.host.inspect(ctx.botId,'protocol');if(manifest.resume&&p&&this.resumeAttempts.get(ctx.botId)!==p.pid){this.resumeAttempts.set(ctx.botId,p.pid);const saved=JSON.parse(await readFile(path.join(ctx.root,'qq/identity.json'),'utf8'));if(/^\d{5,20}$/.test(String(saved.uin))&&(!ctx.expectedUin||ctx.expectedUin===String(saved.uin))){const list=await this.request(ctx,'/QQLogin/GetQuickLoginList');if(JSON.stringify(list).includes(String(saved.uin))){await this.request(ctx,'/QQLogin/SetQuickLogin',{uin:String(saved.uin)});return{state:'authenticating',message:'正在恢复已保存的QQ会话'};}}}}catch{/* No retry loop; QR remains available to the user. */}
  if(s.loginError){const message=typeof s.loginError==='string'?s.loginError:'QQ需要额外验证';if(/过期|expired/i.test(message))return{state:'expired',message};if(/登录失败|failed|无法重复登录/i.test(message))return{state:'failed',message};return{state:'verification_required',message}}
  // Core starts offline and may retain that flag while a new QR is generated.
  // Only a terminal phase (or legacy response without progress) is a failure.
  const progressing=['generating_qrcode','waiting_qrcode','qrcode_scanned','initializing','reconnecting'].includes(s.loginPhase);
  if(s.loginPhase==='offline'||(s.isOffline&&!progressing))return{state:'failed',message:'QQ已掉线'};
  const state=s.qrLoginAccepted||s.loginPhase==='initializing'||s.isLogin?'authenticating':s.loginPhase==='qrcode_scanned'?'waiting_confirm':s.qrcodeurl?'waiting_scan':'starting';
  return{state,qr:s.qrcodeurl||undefined}
 }
 async cancelLogin(ctx:RuntimeContext){const s=await this.settings(ctx);if(!s.launcher)throw new Error('外部NapCat实例不受进程管理；无法保证撤销二维码，请管理员配置launcher');await this.stop(ctx.botId)}
 async queryIdentity(ctx:RuntimeContext){const s=await this.getLoginState(ctx);return s.state==='online'?s.uin||null:null}
 getCapabilities(){return ['qr','text','image','record','onebot-reverse-ws','private-management-api']}
 async getHealth(ctx:RuntimeContext){const p=await this.host.inspect(ctx.botId,'protocol');try{const s=await this.getLoginState(ctx);return{alive:!!p,ready:!!p,online:!!p&&s.state==='online',pid:p?.pid,rssBytes:p?.rssBytes,memoryPolicy:p?.memoryPolicy,mode:'real',loginState:s.state,detail:s.message}}catch(e){return{alive:!!p,ready:false,online:false,pid:p?.pid,rssBytes:p?.rssBytes,memoryPolicy:p?.memoryPolicy,mode:'real',detail:(e as Error).message}}}
 async stop(id:string){this.credentials.delete(id);this.resumeAttempts.delete(id);await this.host.stop(id,'protocol')}
 async exportSessionRef(ctx:RuntimeContext){return path.join(ctx.root,'qq')}
}
