import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,readFile,writeFile,mkdir,readdir,rename} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {pathToFileURL} from 'node:url';
import {atomicJson,renameAtomic} from '../packages/shared/fs-atomic.js';
import {NodeAgent,HeartbeatWriter} from '../apps/node-agent/src/agent.js';
import {installResilienceRuntime} from '../apps/node-agent/src/maintenance.js';
import {createApp} from '../apps/control-api/src/app.js';
import {issueTestInvite} from './helpers/invitations.js';
import {randomUUID} from 'node:crypto';

test('delete releases only the owned instance and credentials, and generation CAS rejects a late delete',async t=>{
 const {app,db,agent}=await createApp({root:await mkdtemp(path.join(os.tmpdir(),'fish-resilience-delete-')),agent:false});t.after(()=>app.close());
 const registration=await app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(db,4),email:'delete-resilience@example.test',name:'fixture',password:'fixture-password-123'}});
 const cookies={fish_session:registration.cookies[0].value};
 const create=async()=>{const r=await app.inject({method:'POST',url:'/v1/bots',cookies,payload:{name:'fixture',protocol:'mock',runtimeMode:'dedicated'}});assert.equal(r.statusCode,201);return r.json().id};
 const a=await create(),b=await create();
 agent.ai.stop=async()=>{};agent.protocols.mock.stop=async()=>{};
 for(const id of [a,b]){
  const [bot]=await db.query('SELECT * FROM bots WHERE id=$1',[id]);await agent.context(bot);
  await db.query("INSERT INTO bilibili_settings(bot_id,tenant_id,config,model_secret) VALUES($1,$2,'{}','private-fixture')",[id,bot.tenant_id]);
  await db.query("INSERT INTO bilibili_accounts(bot_id,tenant_id,uid,label,secret) VALUES($1,$2,$1,'fixture','private-fixture')",[id,bot.tenant_id]);
 }
 const [bot]=await db.query('SELECT * FROM bots WHERE id=$1',[a]);
 const [command]=await db.query("INSERT INTO commands(id,bot_id,idempotency_key,generation,kind) VALUES($1,$2,$1,$3,'delete') RETURNING *",[randomUUID(),a,bot.generation]);
 await agent.execute(command);
 assert.equal((await db.query('SELECT deleted_at FROM bots WHERE id=$1',[a]))[0].deleted_at!==null,true);
 for(const table of ['instances','bilibili_settings','bilibili_accounts']){
  assert.equal((await db.query('SELECT * FROM '+table+' WHERE bot_id=$1',[a])).length,0);
  assert.equal((await db.query('SELECT * FROM '+table+' WHERE bot_id=$1',[b])).length,1);
 }
 const [other]=await db.query('SELECT * FROM bots WHERE id=$1',[b]);
 const [stale]=await db.query("INSERT INTO commands(id,bot_id,idempotency_key,generation,kind) VALUES($1,$2,$1,$3,'delete') RETURNING *",[randomUUID(),b,other.generation]);
 agent.protocols.mock.stop=async()=>{await db.query('UPDATE bots SET generation=generation+1 WHERE id=$1',[b])};
 await agent.execute(stale);
 assert.equal((await db.query('SELECT deleted_at FROM bots WHERE id=$1',[b]))[0].deleted_at,null);
 assert.equal((await db.query('SELECT * FROM instances WHERE bot_id=$1',[b])).length,1);
 assert.equal((await db.query('SELECT status FROM commands WHERE id=$1',[stale.id]))[0].status,'superseded');
});

test('atomic JSON retries only Windows sharing failures and leaves no temporary files',async()=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'fish-resilience-atomic-')),file=path.join(root,'state.json');
 await atomicJson(file,{version:1});await atomicJson(file,{version:2});
 assert.deepEqual(JSON.parse(await readFile(file,'utf8')),{version:2});
 assert.deepEqual(await readdir(root),['state.json']);
 let attempts=0;
 await assert.rejects(renameAtomic('a','b',async()=>{attempts++;throw Object.assign(new Error('sharing'),{code:'EPERM'})}),/sharing/);
 assert.equal(attempts,process.platform==='win32'?5:1);
 attempts=0;
 await assert.rejects(renameAtomic('a','b',async()=>{attempts++;throw Object.assign(new Error('missing'),{code:'ENOENT'})}),/missing/);
 assert.equal(attempts,1);
 if(process.platform==='win32'){
  await writeFile(path.join(root,'new'),'ready');attempts=0;
  await renameAtomic(path.join(root,'new'),file,async(a,b)=>{if(++attempts<3)throw Object.assign(new Error('sharing'),{code:'EBUSY'});await rename(a,b)});
  assert.equal(await readFile(file,'utf8'),'ready');assert.equal(attempts,3);
 }
});

test('release admission checks compiled artifacts and Python without requiring Docker TS sources',async()=>{
 const {verifyProduction}=await import(pathToFileURL(path.resolve('scripts/verify-production.mjs')).href);
 const root=await mkdtemp(path.join(os.tmpdir(),'fish-resilience-manifest-'));
 await mkdir(path.join(root,'dist-server'),{recursive:true});
 const digest=(text:string)=>createHash('sha256').update(text).digest('hex');
 await writeFile(path.join(root,'runtime.py'),'original');await writeFile(path.join(root,'dist-server/main.js'),'compiled');
 const manifest={gitDirty:false,pythonSources:{'runtime.py':digest('original')},artifactHashes:{'dist-server/main.js':digest('compiled')}};
 const save=()=>writeFile(path.join(root,'dist-server/build.json'),JSON.stringify(manifest));await save();
 await verifyProduction(root);
 await writeFile(path.join(root,'runtime.py'),'drift');await assert.rejects(verifyProduction(root),/drift/);
 await writeFile(path.join(root,'runtime.py'),'original');await writeFile(path.join(root,'dist-server/main.js'),'drift');await assert.rejects(verifyProduction(root),/drift/);
 manifest.gitDirty=true;await save();await assert.rejects(verifyProduction(root),/uncommitted/);
});

test('resilience maintenance retains Agent, children, heartbeat queue and refuses active control',()=>{
 const agent:any=new NodeAgent({} as any,'unused',Buffer.alloc(32));
 const children=agent.host.children,prototype=Object.getPrototypeOf(agent),old=prototype.execute;
 const heartbeatPrototype={writeOnce(){}};
 try{
  agent.busy=true;assert.throws(()=>installResilienceRuntime(agent,'a'.repeat(64),heartbeatPrototype),/boundary/);
  agent.busy=false;const result=installResilienceRuntime(agent,'a'.repeat(64),heartbeatPrototype);
  assert.equal(result.heartbeatQueuePreserved,true);assert.equal(agent.host.children,children);
  assert.equal(Object.getOwnPropertyDescriptor(heartbeatPrototype,'writeOnce')?.value,Object.getOwnPropertyDescriptor(HeartbeatWriter.prototype,'writeOnce')?.value);
 }finally{prototype.execute=old}
});
