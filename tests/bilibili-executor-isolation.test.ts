import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import {mkdtemp,readFile,rm} from 'node:fs/promises';
import {BilibiliExecutor} from '../apps/node-agent/src/bilibili/executor.js';

test('one unreachable Worker does not starve other bridges or the executor queue',async t=>{
 const root=await mkdtemp(path.resolve('.local/bilibili-bridge-isolation-'));
 const bots=['failed','healthy'].map(id=>({id,tenant_id:'tenant',generation:1,revision:1,desired_state:'running',health:{sampledAt:new Date().toISOString(),protocol:{online:true},ai:{ready:true}}}));
 const db={query:async(sql:string)=>sql.includes('FROM bots b')?bots:[]} as any;
 const executor=new BilibiliExecutor(db,root,Buffer.alloc(32,1));
 t.after(async()=>{await executor.stop();await rm(root,{recursive:true,force:true})});
 (executor as any).mediaCheckAt=Date.now();(executor as any).mediaStatus={ready:true};
 let claimed=0,accountTicks=0;const reconciled:string[]=[],logs:string[]=[];
 executor.repo.pending=async(b:any)=>{if(b.id==='failed')throw Object.assign(new Error('SECRET_CREDENTIAL_DO_NOT_LOG'),{code:'ECONNREFUSED'});return[]};
 executor.repo.reconcileBudgets=async(b:any)=>{reconciled.push(b.id)};
 executor.repo.claim=async()=>{claimed++;return undefined};executor.accounts.tick=async()=>{accountTicks++};
 t.mock.method(console,'error',(...args:unknown[])=>{logs.push(args.join(' '))});
 await executor.run(new AbortController().signal);
 assert.deepEqual(reconciled,['healthy']);assert.equal(claimed,1);assert.equal(accountTicks,1);
 const bridge=JSON.parse(await readFile(path.join(root,'bots','healthy','ai','bilibili-bridge.json'),'utf8'));
 assert.equal(bridge.botId,'healthy');assert.equal(bridge.generation,1);
 assert.equal(logs.length,1);assert.match(logs[0],/ECONNREFUSED/);assert.match(logs[0],/"stage":"pending"/);assert.match(logs[0],/"botId":"failed"/);
 assert.ok(!logs[0].includes('SECRET_CREDENTIAL'));
});
