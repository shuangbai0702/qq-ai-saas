import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp,readFile} from 'node:fs/promises';import path from 'node:path';
import {createApp} from '../apps/control-api/src/app.js';import {issueTestInvite} from './helpers/invitations.js';
test('a simulated identity cannot reserve a real QQ, while NapCat and Euler still share real account uniqueness',async()=>{
 const current=await createApp({root:await mkdtemp(path.resolve('.local/binding-security-')),agent:false});const {app,db,agent}=current;
 try{
  const make=async(protocol:string)=>{const auth=await app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(db),email:protocol+'@binding.test',name:protocol,password:'binding-test-123'}});const cookies={fish_session:auth.cookies[0].value};const made=await app.inject({method:'POST',url:'/v1/bots',cookies,payload:{name:protocol,protocol,binding_kind:'real'}});assert.equal(made.statusCode,201);const id=made.json().id;const login=await app.inject({method:'POST',url:`/v1/bots/${id}/login-sessions`,cookies,payload:{}});return{id,sid:login.json().id}};
  const mock=await make('mock'),real=await make('napcat'),duplicate=await make('euler');let rejectedStops=0;
  for(const protocol of ['mock','napcat','euler']){agent.protocols[protocol].getHealth=async()=>({alive:true,ready:true,online:true});agent.protocols[protocol].getLoginState=async()=>({state:'online',uin:'90111222'});agent.protocols[protocol].stop=async()=>{rejectedStops++}}
  const session=async(id:string)=>(await db.query('SELECT * FROM login_sessions WHERE id=$1',[id]))[0];
  await agent.pollLogin(await session(mock.sid));assert.equal((await session(mock.sid)).state,'online');await agent.pollLogin(await session(real.sid));assert.equal((await session(real.sid)).state,'online','synthetic arbitrary QQ does not block trusted real login');
  const bindings=await db.query('SELECT bot_id,binding_kind,qq_uin FROM qq_bindings ORDER BY binding_kind');assert.equal(bindings.length,2);assert.deepEqual(bindings.map(x=>x.binding_kind),['mock','real']);
  await agent.pollLogin(await session(duplicate.sid));assert.equal((await session(duplicate.sid)).state,'failed');assert.equal(rejectedStops,1);assert.equal((await db.query('SELECT * FROM qq_bindings')).length,2,'two actual protocols cannot start the same QQ twice');
 }finally{await app.close()}
});
test('binding namespace migration preserves legacy identities, secrets and master key',async()=>{
 const root=await mkdtemp(path.resolve('.local/binding-migration-'));let current=await createApp({root,agent:false});
 try{
  const auth=await current.app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(current.db,2),email:'legacy@binding.test',name:'legacy',password:'legacy-binding-123'}});const cookies={fish_session:auth.cookies[0].value},ids:string[]=[];
  for(const protocol of ['mock','napcat']){const made=await current.app.inject({method:'POST',url:'/v1/bots',cookies,payload:{name:protocol,protocol,apiKey:'synthetic-legacy-binding-key'}});assert.equal(made.statusCode,201);ids.push(made.json().id)}
  await current.db.transaction(async tx=>{await tx.query('DROP INDEX qq_bindings_kind_uin_unique');await tx.query('ALTER TABLE qq_bindings DROP COLUMN binding_kind');await tx.query('ALTER TABLE qq_bindings ADD CONSTRAINT qq_bindings_qq_uin_key UNIQUE(qq_uin)');await tx.query("DELETE FROM schema_migrations WHERE version='005'");for(let i=0;i<2;i++)await tx.query('INSERT INTO qq_bindings(bot_id,qq_uin) VALUES($1,$2)',[ids[i],'9011220'+i])});
  const before=await current.db.query('SELECT bot_id,qq_uin,bound_at FROM qq_bindings ORDER BY bot_id'),secrets=await current.db.query('SELECT ciphertext FROM secrets ORDER BY bot_id'),key=await readFile(path.join(root,'master.key'));await current.app.close();current=await createApp({root,agent:false});
  assert.deepEqual(await current.db.query('SELECT bot_id,qq_uin,bound_at FROM qq_bindings ORDER BY bot_id'),before);assert.deepEqual(await current.db.query('SELECT ciphertext FROM secrets ORDER BY bot_id'),secrets);assert.deepEqual(await readFile(path.join(root,'master.key')),key);assert.equal((await current.db.query('SELECT binding_kind FROM qq_bindings WHERE bot_id=$1',[ids[0]]))[0].binding_kind,'mock');assert.equal((await current.db.query('SELECT binding_kind FROM qq_bindings WHERE bot_id=$1',[ids[1]]))[0].binding_kind,'real');
 }finally{await current.app.close()}
});
