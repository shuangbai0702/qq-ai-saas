import {issueTestInvite} from './helpers/invitations.js';
import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp} from 'node:fs/promises';import path from 'node:path';import {createApp} from '../apps/control-api/src/app.js';

test('live SSE closes on API shutdown without retaining a connection or timer',{timeout:15000},async()=>{
 const current=await createApp({root:await mkdtemp(path.resolve('.local/sse-shutdown-')),agent:false});const app=current.app;
 try{
  const auth=await app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(current.db),email:'stream@test.invalid',name:'stream',password:'stream-test-123'}});const cookies={fish_session:auth.cookies[0].value};
  const id=(await app.inject({method:'POST',url:'/v1/bots',cookies,payload:{name:'stream'}})).json().id;
  const session=(await app.inject({method:'POST',url:`/v1/bots/${id}/login-sessions`,cookies,payload:{}})).json().id;
  await app.listen({host:'127.0.0.1',port:0});const port=(app.server.address()as any).port;
  const response=await fetch(`http://127.0.0.1:${port}/v1/login-sessions/${session}/events`,{headers:{Cookie:'fish_session='+cookies.fish_session}});assert.equal(response.status,200);
  const reader=response.body!.getReader();const data=await reader.read();assert.ok(new TextDecoder().decode(data.value).includes('created'));
  await app.close();assert.equal((await reader.read()).done,true);
 }finally{await app.close()}
});
