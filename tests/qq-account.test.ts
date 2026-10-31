import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp} from 'node:fs/promises';import path from 'node:path';
import {createApp} from '../apps/control-api/src/app.js';import {issueTestInvite} from './helpers/invitations.js';
test('QQ account edits stop old ownership, clear binding, invalidate QR and preserve config/secret',async()=>{
 const current=await createApp({root:await mkdtemp(path.resolve('.local/qq-edit-')),agent:false});
 try{
  const cookies=[];for(const name of ['a','b']){const r=await current.app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(current.db),email:name+'@qqedit.test',password:'qq-edit-test-123',name}});cookies.push({fish_session:r.cookies[0].value})}
  const id=(await current.app.inject({method:'POST',url:'/v1/bots',cookies:cookies[0],payload:{name:'可换QQ',expectedUin:'90000123',apiKey:'synthetic-edit-key'}})).json().id,url='/v1/bots/'+id;
  const old=(await current.app.inject({url,cookies:cookies[0]})).json();await current.db.query("INSERT INTO qq_bindings(bot_id,qq_uin,binding_kind) VALUES($1,'90000123','mock')",[id]);
  const login=(await current.app.inject({method:'POST',url:url+'/login-sessions',cookies:cookies[0],payload:{}})).json();
  const beforeEdit=(await current.app.inject({url,cookies:cookies[0]})).json();assert.equal(beforeEdit.bot.generation,old.bot.generation+1,'a new login fences the prior runtime lifecycle');
  const body={expectedRevision:1,expectedUin:'90000456'};
  assert.equal((await current.app.inject({method:'PUT',url:url+'/qq-account',cookies:cookies[1],payload:body})).statusCode,404);
  const changed=await current.app.inject({method:'PUT',url:url+'/qq-account',cookies:cookies[0],payload:body});assert.equal(changed.statusCode,200,changed.body);
  const d=(await current.app.inject({url,cookies:cookies[0]})).json();assert.equal(d.bot.expected_uin,'90000456');assert.equal(d.bot.desired_state,'stopped');assert.equal(d.bot.generation,beforeEdit.bot.generation+1);assert.equal(d.hasKey,true);assert.deepEqual(d.config,old.config);assert.equal((await current.agent.context(d.bot)).expectedUin,'90000456');
  assert.equal((await current.db.query('SELECT * FROM qq_bindings WHERE bot_id=$1',[id])).length,0);assert.equal((await current.app.inject({url:'/v1/login-sessions/'+login.id,cookies:cookies[0]})).json().state,'canceled');
  assert.equal((await current.app.inject({method:'PUT',url:url+'/qq-account',cookies:cookies[0],payload:body})).statusCode,409);
  assert.equal((await current.app.inject({method:'PUT',url:url+'/qq-account',cookies:cookies[0],payload:{expectedRevision:2,expectedUin:''}})).statusCode,200);
  const cleared=(await current.app.inject({url,cookies:cookies[0]})).json();assert.equal((await current.agent.context(cleared.bot)).expectedUin,'');
  // A saved binding is an observed identity, not a hidden prefilled restriction.
  await current.db.query("INSERT INTO qq_bindings(bot_id,qq_uin,binding_kind) VALUES($1,'90000123','mock')",[id]);assert.equal((await current.agent.context(cleared.bot)).expectedUin,'','blank must not fall back to old QQ binding');
  assert.equal((await current.app.inject({method:'DELETE',url,cookies:cookies[1]})).statusCode,404);
  const del=await current.app.inject({method:'DELETE',url,cookies:cookies[0]});assert.equal(del.statusCode,200);
  const [command]=await current.db.query('SELECT * FROM commands WHERE id=$1',[del.json().id]);await current.agent.execute(command);
  assert.equal((await current.app.inject({url,cookies:cookies[0]})).statusCode,404);assert.equal((await current.db.query('SELECT * FROM secrets WHERE bot_id=$1',[id])).length,0);
 }finally{await current.app.close()}
});
test('late identity probe cannot restore old binding or stop the changed account',async()=>{
 const current=await createApp({root:await mkdtemp(path.resolve('.local/qq-edit-race-')),agent:false});
 try{
  const auth=await current.app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(current.db),email:'race@qqedit.test',password:'qq-edit-test-123',name:'race'}}),cookies={fish_session:auth.cookies[0].value};
  const id=(await current.app.inject({method:'POST',url:'/v1/bots',cookies,payload:{name:'身份迟到',expectedUin:'90000123',protocol:'mock'}})).json().id;
  await current.db.query("UPDATE bots SET desired_state='running' WHERE id=$1",[id]);let entered!:()=>void,release!:(v:string)=>void;const boundary=new Promise<void>(r=>entered=r),identity=new Promise<string>(r=>release=r);let stopped=0;
  (current.agent as any).aiFor=()=>({probe:async()=>({alive:true,ready:true})});
  (current.agent.protocols as any).mock={getHealth:async()=>({alive:true,ready:true,online:true}),queryIdentity:async()=>{entered();return identity},stop:async()=>{stopped++}};
  const pending=current.agent.health();await boundary;
  assert.equal((await current.app.inject({method:'PUT',url:'/v1/bots/'+id+'/qq-account',cookies,payload:{expectedRevision:1,expectedUin:'90000456'}})).statusCode,200);
  release('90000999');await pending;assert.equal(stopped,0);assert.equal((await current.db.query('SELECT * FROM qq_bindings WHERE bot_id=$1',[id])).length,0);
 }finally{await current.app.close()}
});
