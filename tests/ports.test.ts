import {issueTestInvite} from './helpers/invitations.js';
import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp} from 'node:fs/promises';import path from 'node:path';import {createApp} from '../apps/control-api/src/app.js';
test('repeated port candidates allocate distinct four-port blocks without random creation failures',async t=>{
 let clock=Date.now();t.mock.method(Date,'now',()=>clock);const current=await createApp({root:await mkdtemp(path.resolve('.local/ports-')),agent:false});const random=Math.random;
 try{
  const r=await current.app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(current.db,20),email:'ports@test.invalid',name:'ports',password:'ports-test-123'}});const cookies={fish_session:r.cookies[0].value};
  Math.random=()=>0;for(let n=0;n<20;n++){clock+=61000;const created=await current.app.inject({method:'POST',url:'/v1/bots',cookies,payload:{name:'p'+n}});assert.equal(created.statusCode,201,created.body)}
  const ports=await current.db.query('SELECT ai_port,protocol_port FROM instances');assert.equal(new Set(ports.map(p=>p.ai_port)).size,20);assert.ok(ports.every(p=>p.protocol_port===p.ai_port+2));
  clock+=61000;assert.equal((await current.app.inject({method:'POST',url:'/v1/bots',cookies,payload:{name:'quota'}})).statusCode,409,'explicit invitation quota retained');
 }finally{Math.random=random;await current.app.close()}
});
