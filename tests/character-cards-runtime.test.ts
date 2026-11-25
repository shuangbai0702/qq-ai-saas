import test from 'node:test';import assert from 'node:assert/strict';import http from 'node:http';import path from 'node:path';import {mkdtemp,mkdir,writeFile,readFile} from 'node:fs/promises';
import {AstrBotAdapter} from '../adapters/astrbot/index.js';import {defaultConfig,type AiRuntimeContext} from '../packages/contracts/index.js';
import {SharedAstrBotAdapter} from '../adapters/astrbot/shared.js';
test('shared card acknowledgement requires the exact target before advancing applied state',async()=>{
 const root=await mkdtemp(path.resolve('.local/cards-ack-'));await mkdir(path.join(root,'ai/data'),{recursive:true});await writeFile(path.join(root,'ai/data/cmd_config.json'),JSON.stringify({provider:[],platform:[{id:'qq'}]}));
 let updates=0,persists=0;const db={query:async()=>{updates++;return[{bot_id:'fixture'}]}} as any;
 const shared=new SharedAstrBotAdapter(db,{inspect:async()=>({pid:1})} as any,{persistApplied:async()=>{persists++;return true}} as any,'unused','unused');
 shared.group=async()=>({id:'group'});let ack:any={generation:1,revision:2,unchanged:true};shared.request=async()=>ack;
 const ctx:AiRuntimeContext={botId:'fixture',tenantId:'tenant',root,generation:1,revision:2,aiPort:1,protocolPort:2,token:'synthetic',apiKey:'',expectedUin:'91000001',config:defaultConfig,characterCardsEdit:{permitId:'2',target:'expected',origins:[]}};
 await assert.rejects(shared.applyCards(ctx),/acknowledgement mismatch/);ack.target='foreign';await assert.rejects(shared.applyCards(ctx),/acknowledgement mismatch/);assert.equal(updates,0);assert.equal(persists,0);
 ack.target='expected';assert.equal(await shared.applyCards(ctx),true);assert.equal(updates,1);assert.equal(persists,1);
});
test('concurrent runtime replies cannot regress persisted configuration or cross generations',async()=>{
 const root=await mkdtemp(path.resolve('.local/cards-order-'));await mkdir(path.join(root,'ai'));const file=path.join(root,'ai/runtime.json');await writeFile(file,JSON.stringify({generation:1,revision:1,config:defaultConfig}));
 let arrived!:()=>void,release!:()=>void;const arrival=new Promise<void>(r=>arrived=r),barrier=new Promise<void>(r=>release=r);const seen:number[]=[];let live=1;
 const server=http.createServer(async(req,res)=>{let raw='';for await(const part of req)raw+=part;const body=JSON.parse(raw);seen.push(body.revision);if(body.revision===2&&live===1){arrived();await barrier}if(body.revision<live){res.writeHead(409);res.end('{}');return}live=body.revision;res.setHeader('Content-Type','application/json');res.end(JSON.stringify({generation:body.generation,revision:body.revision,target:body.characterCardsEdit.target}))});await new Promise<void>(r=>server.listen(0,'127.0.0.1',r));
 const ctx=(revision:number):AiRuntimeContext=>({botId:'fixture',tenantId:'tenant',root,generation:1,revision,aiPort:(server.address() as any).port-1,protocolPort:0,token:'fixture',apiKey:'',expectedUin:'91000001',config:defaultConfig,characterCardsEdit:{permitId:String(revision),target:'fixture',origins:[]}});
 const adapter=new AstrBotAdapter({inspect:async()=>({pid:1})} as any,'unused','unused');
 try{const a=adapter.applyCards(ctx(2));await arrival;const b=adapter.applyCards(ctx(3));assert.deepEqual(seen,[2]);release();assert.deepEqual(await Promise.all([a,b]),[true,true]);assert.deepEqual(seen,[2,3]);assert.equal(JSON.parse(await readFile(file,'utf8')).revision,3);assert.equal(await adapter.applyCards(ctx(2)),false);assert.equal(JSON.parse(await readFile(file,'utf8')).revision,3);
 await writeFile(file,JSON.stringify({generation:2,revision:4,config:defaultConfig}));assert.equal(await adapter.persistApplied(ctx(5)),false);assert.equal(JSON.parse(await readFile(file,'utf8')).generation,2);
 await assert.rejects(adapter.applyConfig(ctx(5)),/superseded/);assert.equal(JSON.parse(await readFile(file,'utf8')).generation,2);
 }finally{release();server.closeAllConnections();await new Promise<void>(r=>server.close(()=>r()))}
});
