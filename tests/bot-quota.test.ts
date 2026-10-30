import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp,readFile} from 'node:fs/promises';import path from 'node:path';
import {createApp} from '../apps/control-api/src/app.js';import {issueTestInvite} from './helpers/invitations.js';import {defaultConfig} from '../packages/contracts/index.js';

test('default activation quota is one; forged limits and concurrent creates cannot bypass it',async t=>{
 const {app,db}=await createApp({root:await mkdtemp(path.resolve('.local/quota-race-')),agent:false});t.after(()=>app.close());
 const auth=await app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(db),name:'quota',email:'quota-race@test.invalid',password:'quota-race-123',botLimit:20}});assert.equal(auth.statusCode,200);
 const cookies={fish_session:auth.cookies[0].value};const list=async()=>(await app.inject({method:'GET',url:'/v1/bots',cookies})).json();
 assert.deepEqual((await list()).quota,{limit:1,used:0,available:1,canCreate:true});
 const results=await Promise.all([0,1,2].map(n=>app.inject({method:'POST',url:'/v1/bots',cookies,payload:{name:'race'+n,botLimit:20,config:defaultConfig,apiKey:'synthetic-quota-key'}})));
 assert.deepEqual(results.map(r=>r.statusCode).sort(),[201,409,409]);const id=results.find(r=>r.statusCode===201)!.json().id;
 assert.deepEqual((await list()).quota,{limit:1,used:1,available:0,canCreate:false});
 for(const table of ['bots','instances','bot_config_revisions','secrets'])assert.equal((await db.query('SELECT count(*)::int AS n FROM '+table))[0].n,1,'no partial writes to '+table);
 assert.equal((await db.query("SELECT * FROM audit_events WHERE event='bot.created'")).length,1);assert.equal((await db.query('SELECT * FROM runtime_groups')).length,0,'creation does not start an AI runtime');
 const create=()=>app.inject({method:'POST',url:'/v1/bots',cookies,payload:{name:'blocked',runtimeMode:'dedicated',botLimit:20}});
 await app.inject({method:'POST',url:`/v1/bots/${id}/actions`,cookies,payload:{action:'stop',idempotencyKey:'quota-stop'}});assert.equal((await create()).statusCode,409,'stop never releases a deployment slot');
 await db.query("UPDATE bots SET observed_state='failed' WHERE id=$1",[id]);assert.equal((await create()).statusCode,409,'failed bot still owns its slot');
});

test('delete keeps the slot until real Agent cleanup; failure is retryable, ownership stays enforced',async t=>{
 const {app,db,agent}=await createApp({root:await mkdtemp(path.resolve('.local/quota-delete-')),agent:false});t.after(()=>app.close());
 const register=async(name:string)=>(await app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(db),name,email:name+'@quota.test',password:'quota-delete-123'}})).cookies[0].value;
 const a=await register('owner'),b=await register('other');const call=(token:string,method:any,url:string,payload?:any)=>app.inject({method,url,payload,cookies:{fish_session:token}});
 const id=(await call(a,'POST','/v1/bots',{name:'old',runtimeMode:'dedicated',expectedUin:'90003221',apiKey:'synthetic-old-key'})).json().id;
 const other=(await call(b,'POST','/v1/bots',{name:'other'})).json().id;await db.query('INSERT INTO qq_bindings(bot_id,qq_uin) VALUES($1,$2)',[id,'90003221']);
 assert.equal((await call(b,'DELETE','/v1/bots/'+id)).statusCode,404);assert.equal((await call(b,'POST','/v1/bots',{name:'other-over'})).statusCode,409);
 const deletion=await call(a,'DELETE','/v1/bots/'+id);assert.equal(deletion.statusCode,200);assert.equal((await call(a,'DELETE','/v1/bots/'+id)).statusCode,409,'pending delete not duplicated');
 const create=()=>call(a,'POST','/v1/bots',{name:'replacement',expectedUin:'90003222'});assert.equal((await create()).statusCode,409,'queued delete is not completed cleanup');
 let release!:()=>void,enter!:()=>void;const barrier=new Promise<void>(r=>release=r),entered=new Promise<void>(r=>enter=r);let fail=true,stops=0;
 agent.ai.stop=async()=>{stops++;if(fail)throw new Error('controlled stop failure');enter();await barrier};agent.protocols.mock.stop=async()=>{};
 const [command]=await db.query('SELECT * FROM commands WHERE id=$1',[deletion.json().id]);await agent.execute(command);
 assert.equal((await db.query('SELECT status FROM commands WHERE id=$1',[command.id]))[0].status,'failed');assert.equal((await create()).statusCode,409,'failure must not release the slot');
 assert.equal((await db.query('SELECT * FROM secrets WHERE bot_id=$1',[id])).length,1);assert.equal((await db.query('SELECT * FROM qq_bindings WHERE bot_id=$1',[id])).length,1);
 const retry=await call(a,'DELETE','/v1/bots/'+id);assert.equal(retry.statusCode,200);assert.equal(retry.json().id,command.id,'retry keeps original command and generation');
 assert.equal((await call(a,'DELETE','/v1/bots/'+id)).statusCode,409);fail=false;const execution=agent.execute(command);try{await entered;assert.equal((await create()).statusCode,409,'in-flight stop still consumes the slot')}finally{release();await execution}
 assert.equal(stops,2);assert.equal((await call(a,'GET','/v1/bots/'+id)).statusCode,404);assert.equal((await db.query('SELECT * FROM secrets WHERE bot_id=$1',[id])).length,0);assert.equal((await db.query('SELECT * FROM qq_bindings WHERE bot_id=$1',[id])).length,0);
 assert.deepEqual((await call(a,'GET','/v1/bots')).json().quota,{limit:1,used:0,available:1,canCreate:true});assert.equal((await create()).statusCode,201);assert.equal((await create()).statusCode,409);
 assert.equal((await call(b,'GET','/v1/bots/'+other)).statusCode,200,'peer remains intact');assert.deepEqual((await call(b,'GET','/v1/bots')).json().quota,{limit:1,used:1,available:0,canCreate:false});
});

test('explicit invitation allowance is durable and cannot be raised by ordinary account data',async()=>{
 const root=await mkdtemp(path.resolve('.local/quota-durable-'));let current=await createApp({root,agent:false});
 try{
  const code=await issueTestInvite(current.db,2);const auth=await current.app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:code,name:'special',email:'durable@quota.test',password:'quota-durable-123',botLimit:20}});assert.equal(auth.statusCode,200);const cookies={fish_session:auth.cookies[0].value};
  const make=()=>current.app.inject({method:'POST',url:'/v1/bots',cookies,payload:{name:'explicit-grant',quota:20,botLimit:20}});
  assert.equal((await make()).statusCode,201);await current.app.close();current=await createApp({root,agent:false});assert.equal((await current.app.inject({method:'GET',url:'/v1/bots',cookies})).json().quota.limit,2);assert.equal((await make()).statusCode,201);assert.equal((await make()).statusCode,409);
  assert.equal((await current.app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:code,name:'reuse',email:'reuse@quota.test',password:'quota-durable-123'}})).statusCode,400);
  assert.equal((await current.app.inject({method:'POST',url:'/v1/admin/invitations',cookies,payload:{botLimit:20}})).statusCode,403);
 }finally{await current.app.close()}
});

test('migration gives legacy accounts limit one without altering existing bots, keys or QQ bindings',async()=>{
 const root=await mkdtemp(path.resolve('.local/quota-upgrade-'));let current=await createApp({root,agent:false});
 try{
  const auth=await current.app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(current.db,2),name:'legacy',email:'legacy@quota.test',password:'quota-legacy-123'}});const cookies={fish_session:auth.cookies[0].value};const ids:string[]=[];
  for(let n=0;n<2;n++){const created=await current.app.inject({method:'POST',url:'/v1/bots',cookies,payload:{name:'legacy'+n,config:{...defaultConfig,systemPrompt:'legacy-persona-'+n},apiKey:'synthetic-legacy-key-'+n}});assert.equal(created.statusCode,201);ids.push(created.json().id)}
  await current.db.query('INSERT INTO qq_bindings(bot_id,qq_uin) VALUES($1,$2)',[ids[0],'90004444']);const master=await readFile(path.join(root,'master.key'));
  const tables=['bots','bot_config_revisions','secrets','instances','qq_bindings'];const before=await Promise.all(tables.map(table=>current.db.query('SELECT * FROM '+table+' ORDER BY 1')));
  // Reconstruct the previous schema in this isolated fixture, then run the real upgrade entry point.
  await current.db.transaction(async tx=>{await tx.query('ALTER TABLE tenants DROP COLUMN bot_limit');await tx.query('ALTER TABLE invitations DROP COLUMN bot_limit');await tx.query("DELETE FROM schema_migrations WHERE version='004'")});
  await current.app.close();current=await createApp({root,agent:false});assert.deepEqual(await readFile(path.join(root,'master.key')),master);
  assert.deepEqual(await Promise.all(tables.map(table=>current.db.query('SELECT * FROM '+table+' ORDER BY 1'))),before);assert.deepEqual((await current.app.inject({method:'GET',url:'/v1/bots',cookies})).json().quota,{limit:1,used:2,available:0,canCreate:false});
  assert.equal((await current.app.inject({method:'POST',url:'/v1/bots',cookies,payload:{name:'over-quota'}})).statusCode,409);
  for(const id of ids){const detail=await current.app.inject({method:'GET',url:'/v1/bots/'+id,cookies});assert.equal(detail.statusCode,200);assert.equal(detail.json().hasKey,true);assert.match(detail.json().config.systemPrompt,/legacy-persona/)}
 }finally{await current.app.close()}
});
