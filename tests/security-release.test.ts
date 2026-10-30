import test from 'node:test';import assert from 'node:assert/strict';import path from 'node:path';import {mkdtemp,writeFile,readFile,rename} from 'node:fs/promises';
import {createApp} from '../apps/control-api/src/app.js';import {issueTestInvite} from './helpers/invitations.js';import {defaultConfig} from '../packages/contracts/index.js';import {loadKey,decrypt} from '../packages/shared/security.js';
import {acquireLease} from '../packages/database/lease.js';
test('release headers, exact auth exemptions and credential URL validation keep tenant boundaries',async t=>{
 const {app,db}=await createApp({root:await mkdtemp(path.resolve('.local/security-release-')),agent:false});t.after(()=>app.close());
 const health=await app.inject('/v1/health');assert.match(health.headers['content-security-policy'] as string,/frame-ancestors 'none'/);assert.equal(health.headers['x-content-type-options'],'nosniff');assert.equal(health.headers['cache-control'],'no-store');assert.equal((await app.inject('/v1/auth/login-extra')).statusCode,401);
 const auth=await app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(db),email:'release@security.test',name:'release',password:'security-release-123',is_admin:true,botLimit:20}});assert.equal(auth.statusCode,200);assert.match(auth.headers['set-cookie'] as string,/HttpOnly/);assert.match(auth.headers['set-cookie'] as string,/SameSite=Lax/);const cookies={fish_session:auth.cookies[0].value};
 assert.equal((await app.inject({method:'POST',url:'/v1/bots',headers:{origin:'null'},cookies,payload:{name:'forged'}})).statusCode,403);
 for(const baseUrl of ['https://user:password@example.com/v1','https://example.com/v1?key=private','file:///etc/passwd'])assert.equal((await app.inject({method:'POST',url:'/v1/bots',cookies,payload:{name:'invalid',config:{...defaultConfig,baseUrl}}})).statusCode,400);
 assert.equal((await app.inject({method:'POST',url:'/v1/admin/invitations',cookies,payload:{botLimit:20}})).statusCode,403);assert.equal((await db.query('SELECT * FROM bots')).length,0,'rejected config has no side effects');
 const catalog=await app.inject({method:'POST',url:'/v1/model-catalog',cookies,payload:{baseUrl:'http://169.254.169.254',apiKey:'synthetic-security-key'}});assert.equal(catalog.json().ok,false);assert.ok(!catalog.body.includes('synthetic-security-key'));
});
test('SSE cap is owned by account and releases after the client closes',{timeout:20000},async()=>{
 const {app,db}=await createApp({root:await mkdtemp(path.resolve('.local/security-sse-')),agent:false});const readers:ReadableStreamDefaultReader<Uint8Array>[]=[];
 try{
  const auth=await app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(db),email:'sse@security.test',name:'sse',password:'security-stream-123'}});const cookies={fish_session:auth.cookies[0].value},headers={Cookie:'fish_session='+cookies.fish_session};const id=(await app.inject({method:'POST',url:'/v1/bots',cookies,payload:{name:'stream'}})).json().id;const session=(await app.inject({method:'POST',url:`/v1/bots/${id}/login-sessions`,cookies,payload:{}})).json().id;
  let closed!:()=>void,requests=0;const firstClosed=new Promise<void>(r=>closed=r);app.server.on('request',(req,res)=>{if(req.url?.endsWith('/events')&&++requests===1)res.once('close',closed)});const base=await app.listen({host:'127.0.0.1',port:0}),url=base+`/v1/login-sessions/${session}/events`;
  for(let n=0;n<4;n++){const response=await fetch(url,{headers});assert.equal(response.status,200);const reader=response.body!.getReader();await reader.read();readers.push(reader)}
  assert.equal((await fetch(url,{headers})).status,429);await readers.shift()!.cancel();await firstClosed;const renewed=await fetch(url,{headers});assert.equal(renewed.status,200);readers.push(renewed.body!.getReader());await readers.at(-1)!.read();
 }finally{for(const reader of readers)await reader.cancel().catch(()=>{});await app.close()}
});
test('malformed master key fails without replacing or accepting truncated credentials',async()=>{
 const root=await mkdtemp(path.resolve('.local/security-key-')),file=path.join(root,'master.key'),before=process.env.MASTER_KEY;
 try{delete process.env.MASTER_KEY;await writeFile(file,'corrupted-key');await assert.rejects(()=>loadKey(root),/master.key/);await assert.rejects(()=>createApp({root,agent:false}),/master.key/);const release=await acquireLease(root,'database');await release();assert.equal(await readFile(file,'utf8'),'corrupted-key');process.env.MASTER_KEY='ab'.repeat(32)+'NOT_HEX';await assert.rejects(()=>loadKey(root),/32-byte hex/)}finally{if(before===undefined)delete process.env.MASTER_KEY;else process.env.MASTER_KEY=before}
});

test('existing encrypted credentials refuse a missing master key and recover with the original file',async()=>{
 const root=await mkdtemp(path.resolve('.local/security-missing-key-')),file=path.join(root,'master.key'),before=process.env.MASTER_KEY;let current:Awaited<ReturnType<typeof createApp>>|undefined;
 try{
  delete process.env.MASTER_KEY;current=await createApp({root,agent:false});
  const auth=await current.app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(current.db),email:'restore@security.test',name:'restore',password:'security-restore-123'}});assert.equal(auth.statusCode,200);
  const created=await current.app.inject({method:'POST',url:'/v1/bots',cookies:{fish_session:auth.cookies[0].value},payload:{name:'restore-test',apiKey:'synthetic-restore-credential'}});assert.equal(created.statusCode,201);
  const [secret]=await current.db.query('SELECT tenant_id,bot_id,ciphertext FROM secrets WHERE bot_id=$1',[created.json().id]);const original=await readFile(file,'utf8');assert.ok(!secret.ciphertext.includes('synthetic-restore-credential'));
  await current.app.close();current=undefined;await rename(file,file+'.original');
  await assert.rejects(async()=>{current=await createApp({root,agent:false});await current.app.close();current=undefined},/master\.key.*缺失/);
  await assert.rejects(()=>readFile(file),{code:'ENOENT'});const release=await acquireLease(root,'database');await release();
  await rename(file+'.original',file);current=await createApp({root,agent:false});assert.equal(await readFile(file,'utf8'),original);
  const [restored]=await current.db.query('SELECT ciphertext FROM secrets WHERE bot_id=$1',[secret.bot_id]);assert.equal(restored.ciphertext,secret.ciphertext);assert.equal(decrypt(await loadKey(root),`${secret.tenant_id}:${secret.bot_id}`,restored.ciphertext),'synthetic-restore-credential');
 }finally{await current?.app.close();if(before===undefined)delete process.env.MASTER_KEY;else process.env.MASTER_KEY=before}
});
