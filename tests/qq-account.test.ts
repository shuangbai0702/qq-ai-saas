import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp} from 'node:fs/promises';import path from 'node:path';
import {createApp} from '../apps/control-api/src/app.js';import {issueTestInvite} from './helpers/invitations.js';
test('bound QQ refuses account changes and preserves same-account login and configuration',async()=>{
 const current=await createApp({root:await mkdtemp(path.resolve('.local/qq-fixed-')),agent:false});
 try{
  const cookies:{fish_session:string}[]=[];for(const name of ['a','b']){const r=await current.app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(current.db),email:name+'@qqfixed.test',password:'qq-fixed-test-123',name}});cookies.push({fish_session:r.cookies[0].value})}
  const make=()=>current.app.inject({method:'POST',url:'/v1/bots',cookies:cookies[0],payload:{name:'账号固定',protocol:'mock',expectedUin:'',apiKey:'synthetic-fixed-key'}});
  const id=(await make()).json().id,url='/v1/bots/'+id;
  await current.db.query("INSERT INTO qq_bindings(bot_id,qq_uin,binding_kind) VALUES($1,'90000123','mock')",[id]);
  const old=(await current.app.inject({url,cookies:cookies[0]})).json();
  for(const uin of ['90000456','90000123'])assert.equal((await current.app.inject({method:'PUT',url:url+'/qq-account',cookies:cookies[0],payload:{expectedRevision:1,expectedUin:uin}})).statusCode,409);
  assert.equal((await current.app.inject({method:'PUT',url:url+'/qq-account',cookies:cookies[1],payload:{expectedRevision:1,expectedUin:'90000456'}})).statusCode,404);
  const config={expectedRevision:1,name:old.bot.name,expectedUin:'90000456',config:old.config};
  assert.equal((await current.app.inject({method:'PUT',url:url+'/config',cookies:cookies[0],payload:config})).statusCode,409);
  assert.equal((await current.app.inject({method:'PUT',url:url+'/config',cookies:cookies[0],payload:{...config,expectedUin:''}})).statusCode,200);
  const fresh=(await current.app.inject({url,cookies:cookies[0]})).json();assert.equal(fresh.hasKey,true);assert.deepEqual(fresh.config,old.config);assert.equal(fresh.bot.generation,old.bot.generation);assert.equal((await current.agent.context(fresh.bot)).expectedUin,'90000123');
  const login=await current.app.inject({method:'POST',url:url+'/login-sessions',cookies:cookies[0],payload:{}});assert.equal(login.statusCode,200,login.body);
  let stopped=0;current.agent.protocols.mock.getLoginState=async()=>({state:'online',uin:'90000456'});current.agent.protocols.mock.stop=async()=>{stopped++};
  const [session]=await current.db.query('SELECT * FROM login_sessions WHERE id=$1',[login.json().id]);await current.agent.pollLogin(session);
  assert.equal(stopped,1);assert.equal((await current.db.query('SELECT state FROM login_sessions WHERE id=$1',[session.id]))[0].state,'failed');assert.equal((await current.db.query('SELECT qq_uin FROM qq_bindings WHERE bot_id=$1',[id]))[0].qq_uin,'90000123');
  const del=await current.app.inject({method:'DELETE',url,cookies:cookies[0]});assert.equal(del.statusCode,200,del.body);const [command]=await current.db.query('SELECT * FROM commands WHERE id=$1',[del.json().id]);await current.agent.execute(command);assert.equal((await current.app.inject({url,cookies:cookies[0]})).statusCode,404);
  const next=await make();assert.equal(next.statusCode,201,next.body);assert.notEqual(next.json().id,id);
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

test('binding established during login awaits cannot be overwritten by another scanned identity',async()=>{
 const current=await createApp({root:await mkdtemp(path.resolve('.local/qq-bind-race-')),agent:false});
 try{
  const auth=await current.app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(current.db),email:'bindrace@qq.test',password:'qq-bind-race-123',name:'race'}}),cookies={fish_session:auth.cookies[0].value};
  const id=(await current.app.inject({method:'POST',url:'/v1/bots',cookies,payload:{name:'绑定竞态',protocol:'mock'}})).json().id;
  const login=(await current.app.inject({method:'POST',url:'/v1/bots/'+id+'/login-sessions',cookies,payload:{}})).json();
  let entered!:()=>void,release!:()=>void,stopped=0;const boundary=new Promise<void>(r=>entered=r),resume=new Promise<void>(r=>release=r);
  current.agent.protocols.mock.getLoginState=async()=>({state:'online',uin:'90000456'});current.agent.protocols.mock.getHealth=async()=>{entered();await resume;return{online:true,alive:true,ready:true}};current.agent.protocols.mock.stop=async()=>{stopped++};
  const [session]=await current.db.query('SELECT * FROM login_sessions WHERE id=$1',[login.id]);const pending=current.agent.pollLogin(session);await boundary;
  await current.db.query("INSERT INTO qq_bindings(bot_id,qq_uin,binding_kind) VALUES($1,'90000123','mock')",[id]);release();await pending;
  assert.equal(stopped,1);assert.equal((await current.db.query('SELECT qq_uin FROM qq_bindings WHERE bot_id=$1',[id]))[0].qq_uin,'90000123');assert.equal((await current.db.query('SELECT state FROM login_sessions WHERE id=$1',[login.id]))[0].state,'failed');
 }finally{await current.app.close()}
});
