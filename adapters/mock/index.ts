import path from 'node:path';import {mkdir,writeFile} from 'node:fs/promises';import type {ProtocolAdapter,ProtocolContext,LoginSnapshot,ProcessHostLike} from '../../packages/contracts/index.js';
export class MockProtocolAdapter implements ProtocolAdapter{
 constructor(private host:ProcessHostLike){}
 async provision(ctx:ProtocolContext){await mkdir(ctx.root,{recursive:true});await writeFile(path.join(ctx.root,'protocol.json'),JSON.stringify({botId:ctx.botId,aiPort:ctx.aiPort,protocolPort:ctx.protocolPort,token:ctx.token,expectedUin:ctx.expectedUin}),{mode:0o600})}
 async beginLogin(ctx:ProtocolContext){await this.provision(ctx);const env:NodeJS.ProcessEnv={...process.env};delete env.MASTER_KEY;delete env.DATABASE_URL;delete env.EULER_SIGNER_TOKEN;await this.host.start(ctx.botId,'protocol',process.execPath,[path.resolve('adapters/mock/protocol-worker.mjs'),ctx.root],ctx.root,env,[ctx.token])}
 async call(ctx:ProtocolContext,route:string,body?:unknown){const r=await fetch(`http://127.0.0.1:${ctx.protocolPort}${route}`,{method:body?'POST':'GET',headers:{Authorization:`Bearer ${ctx.token}`,'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined,signal:AbortSignal.timeout(2000)});if(!r.ok)throw new Error('模拟协议尚未就绪');return r.json()}
 async getLoginState(ctx:ProtocolContext):Promise<LoginSnapshot>{return await this.call(ctx,'/state') as LoginSnapshot}
 async cancelLogin(ctx:ProtocolContext){await this.stop(ctx.botId)}
 async queryIdentity(ctx:ProtocolContext){const s=await this.getLoginState(ctx);return s.state==='online'?s.uin||null:null}
 getCapabilities(){return ['mock','qr','cancel','text','onebot-reverse-ws']}
 async getHealth(ctx:ProtocolContext){const p=await this.host.inspect(ctx.botId,'protocol');if(!p)return{alive:false,ready:false,online:false,mode:'mock'};let online=false,ready=false;let loginState:LoginSnapshot['state']|undefined;try{loginState=(await this.getLoginState(ctx)).state;online=loginState==='online';ready=true}catch{}return{alive:!!p,ready,online,loginState,pid:p?.pid,rssBytes:p?.rssBytes,mode:'mock'}}
 async stop(id:string){await this.host.stop(id,'protocol')}
}
