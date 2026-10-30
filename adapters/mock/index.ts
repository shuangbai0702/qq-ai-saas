import path from 'node:path';import {mkdir,writeFile} from 'node:fs/promises';import type {ProtocolAdapter,RuntimeContext,LoginSnapshot} from '../../packages/contracts/index.js';import {ProcessHost} from '../../apps/node-agent/src/process-host.js';
export class MockProtocolAdapter implements ProtocolAdapter{
 constructor(private host:ProcessHost){}
 async provision(ctx:RuntimeContext){await mkdir(ctx.root,{recursive:true});await writeFile(path.join(ctx.root,'protocol.json'),JSON.stringify({botId:ctx.botId,aiPort:ctx.aiPort,protocolPort:ctx.protocolPort,token:ctx.token,expectedUin:ctx.expectedUin}),{mode:0o600})}
 async beginLogin(ctx:RuntimeContext){await this.provision(ctx);await this.host.start(ctx.botId,'protocol',process.execPath,[path.resolve('adapters/mock/protocol-worker.mjs'),ctx.root],ctx.root,{...process.env},[ctx.token])}
 async call(ctx:RuntimeContext,route:string,body?:unknown){const r=await fetch(`http://127.0.0.1:${ctx.protocolPort}${route}`,{method:body?'POST':'GET',headers:{Authorization:`Bearer ${ctx.token}`,'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined,signal:AbortSignal.timeout(2000)});if(!r.ok)throw new Error('模拟协议尚未就绪');return r.json()}
 async getLoginState(ctx:RuntimeContext):Promise<LoginSnapshot>{return await this.call(ctx,'/state') as LoginSnapshot}
 async cancelLogin(ctx:RuntimeContext){await this.stop(ctx.botId)}
 async queryIdentity(ctx:RuntimeContext){const s=await this.getLoginState(ctx);return s.state==='online'?s.uin||null:null}
 getCapabilities(){return ['mock','qr','cancel','text','onebot-reverse-ws']}
 async getHealth(ctx:RuntimeContext){const p=await this.host.inspect(ctx.botId,'protocol');if(!p)return{alive:false,ready:false,online:false,mode:'mock'};let online=false,ready=false;try{online=(await this.getLoginState(ctx)).state==='online';ready=true}catch{}return{alive:!!p,ready,online,pid:p?.pid,rssBytes:p?.rssBytes,mode:'mock'}}
 async stop(id:string){await this.host.stop(id,'protocol')}
 async exportSessionRef(ctx:RuntimeContext){return path.join(ctx.root,'mock-session.json')}
}
