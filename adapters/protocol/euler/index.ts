import path from 'node:path';import {mkdir,writeFile,readFile,rm} from 'node:fs/promises';import type {ProtocolAdapter,RuntimeContext,LoginSnapshot} from '../../../packages/contracts/index.js';import {ProcessHost} from '../../../apps/node-agent/src/process-host.js';
export class EulerAdapter implements ProtocolAdapter{
 constructor(private host:ProcessHost,private source:string,private python:string){}
 async provision(ctx:RuntimeContext){if(!ctx.expectedUin)throw new Error('Euler需要先填写计划登录的QQ号');if(!process.env.EULER_SIGNER_URL)throw new Error('Euler尚未配置签名服务：设置EULER_SIGNER_URL / EULER_SIGNER_TOKEN；可改用NapCat或明确标注的模拟模式');const root=path.join(ctx.root,'qq');await mkdir(root,{recursive:true});
 await writeFile(path.join(root,'appconfig.json'),JSON.stringify({log_level:'INFO',access_token:ctx.token,connections:[{type:'ReverseWebSocket',url:`ws://127.0.0.1:${ctx.aiPort}/ws`,use_universal_client:true,reconnect_interval:3000}],login:{uin:Number(ctx.expectedUin),signer_url:process.env.EULER_SIGNER_URL,signer_token:process.env.EULER_SIGNER_TOKEN||'',setup_watchdog:false},heartbeat:{enabled:true,interval:15000}}),{mode:0o600})}
 async beginLogin(ctx:RuntimeContext){await this.provision(ctx);const root=path.join(ctx.root,'qq');await rm(path.join(root,'login-state.json'),{force:true});await this.host.start(ctx.botId,'protocol',this.python,[path.resolve('adapters/protocol/euler/bridge.py'),this.source,root],root,{...process.env,PYTHONIOENCODING:'utf-8',PYTHONUNBUFFERED:'1'},[process.env.EULER_SIGNER_TOKEN||'',ctx.token])}
 async getLoginState(ctx:RuntimeContext):Promise<LoginSnapshot>{try{const state=JSON.parse(await readFile(path.join(ctx.root,'qq/login-state.json'),'utf8'));if(!['failed','expired','canceled','verification_required'].includes(state.state)&&!(await this.host.inspect(ctx.botId,'protocol')))return{state:'failed',message:'Euler进程已退出，请检查签名服务与运行日志'};return state}catch{return{state:'starting'}}}
 async cancelLogin(ctx:RuntimeContext){await this.stop(ctx.botId)}
 async queryIdentity(ctx:RuntimeContext){const s=await this.getLoginState(ctx);return s.state==='online'?s.uin||null:null}
 getCapabilities(){return ['qr','cancel','requires-uin','requires-signer','onebot-reverse-ws','text','image','record']}
 async getHealth(ctx:RuntimeContext){const p=await this.host.inspect(ctx.botId,'protocol');const s=await this.getLoginState(ctx);return{alive:!!p,ready:!!p,online:!!p&&s.state==='online',loginState:s.state,pid:p?.pid,rssBytes:p?.rssBytes,mode:'real'}}
 async stop(id:string){await this.host.stop(id,'protocol')}
 async exportSessionRef(ctx:RuntimeContext){return path.join(ctx.root,'qq')}
}
