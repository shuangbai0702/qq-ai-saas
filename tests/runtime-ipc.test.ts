import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import {runtimeIpc} from '../packages/shared/runtime-ipc.js';
import {AstrBotAdapter} from '../adapters/astrbot/index.js';
import {runtimeClient} from '../packages/shared/runtime-client.js';

test('autonomous IPC errors retain their operation and original HTTP or timeout classification',async t=>{
 const requests:string[]=[];
 const server=http.createServer(async(req,res)=>{for await(const _ of req){}requests.push(req.url!);if(req.url!.endsWith('/policy'))res.writeHead(503).end()});
 await new Promise<void>(resolve=>server.listen(0,'127.0.0.1',resolve));
 t.after(async()=>{server.closeAllConnections();await new Promise<void>(resolve=>server.close(()=>resolve()))});
 const port=(server.address() as import('node:net').AddressInfo).port;
 const client=runtimeClient({query:async(sql:string)=>sql.includes('FROM bots')?[{runtime_mode:'standalone',generation:1,revision:2}]:[{ai_port:port-1,token:'fixture-token'}]} as any);
 const bot={bot_id:'fixture',tenant_id:'tenant',generation:1,revision:2};
 await assert.rejects(()=>client(bot,'policy',{},1000),(error:any)=>error.message==='autonomous_ipc_503'&&error.runtimeOperation==='policy');
 await assert.rejects(()=>client(bot,'snapshot',{},200),(error:any)=>error.name==='TimeoutError'&&error.runtimeOperation==='snapshot');
 assert.deepEqual(requests,['/autonomous/policy','/autonomous/snapshot']);
});

test('runtime IPC never forwards configuration bodies or trusts health across redirects',async t=>{
 let forwarded=0;
 const sink=http.createServer(async(req,res)=>{for await(const _ of req){}forwarded++;res.end(JSON.stringify({generation:1,revision:2,target:'cards'}))});
 await new Promise<void>(resolve=>sink.listen(0,'127.0.0.1',resolve));
 const target=`http://127.0.0.1:${(sink.address() as import('node:net').AddressInfo).port}/foreign`;
 const source=http.createServer((req,res)=>{res.writeHead(307,{Location:target});res.end()});
 await new Promise<void>(resolve=>source.listen(0,'127.0.0.1',resolve));
 t.after(async()=>{for(const server of [source,sink]){server.closeAllConnections();await new Promise<void>(resolve=>server.close(()=>resolve()))}});
 const port=(source.address() as import('node:net').AddressInfo).port;
 await assert.rejects(()=>runtimeIpc(port,'/members/fixture',{method:'POST',headers:{Authorization:'Bearer fixture'},body:{apiKey:'synthetic-secret'},timeoutMs:1000}));
 const adapter=new AstrBotAdapter({inspect:async()=>({pid:1,rssBytes:0})} as any,'','');
 const ctx:any={botId:'fixture',tenantId:'tenant',aiPort:port-1,generation:1,revision:2,token:'fixture',apiKey:'synthetic-secret',config:{},characterCardsEdit:{target:'cards'},toolsEdit:{},personaEdit:{}};
 for(const apply of [()=>adapter.applyCards(ctx),()=>adapter.applyTools(ctx),()=>adapter.applyPersona(ctx)])await assert.rejects(apply);
 assert.equal((await adapter.probe(ctx)).ready,false);
 assert.equal(forwarded,0,'even cross-port loopback redirects must not forward a model key or forge runtime readiness');
});
