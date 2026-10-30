import {issueTestInvite} from './helpers/invitations.js';
import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp} from 'node:fs/promises';import path from 'node:path';import {createApp} from '../apps/control-api/src/app.js';

test('start superseded during AI startup cannot bring QQ online; deletion is terminal',{timeout:30000},async()=>{
 const current=await createApp({root:await mkdtemp(path.resolve('.local/command-fence-')),agent:false});const {app,agent,db}=current;
 try{
  const auth=await app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(db),email:'fence@test.invalid',name:'fence',password:'barrier-test-123'}});const cookies={fish_session:auth.cookies[0].value};
  const call=(method:any,url:string,payload?:any)=>app.inject({method,url,payload,cookies});
  let started=0,stopped=0,protocol=0;let enter!:()=>void,release!:()=>void;
  const entered=new Promise<void>(r=>enter=r),barrier=new Promise<void>(r=>release=r);
  agent.ai.start=async()=>{started++;enter();await barrier};agent.ai.stop=async()=>{stopped++};agent.protocols.mock.beginLogin=async()=>{protocol++};
  const id=(await call('POST','/v1/bots',{name:'fence',protocol:'mock',runtimeMode:'dedicated'})).json().id;
  await call('POST',`/v1/bots/${id}/actions`,{action:'start',idempotencyKey:'start'});
  const [command]=await db.query("SELECT * FROM commands WHERE bot_id=$1 AND kind='start'",[id]);const execution=agent.execute(command);await entered;
  await call('POST',`/v1/bots/${id}/actions`,{action:'stop',idempotencyKey:'stop'});release();await execution;
  assert.equal(started,1);assert.equal(protocol,0,'stale start must not begin login');assert.equal(stopped,2,'stop before start + cleanup of stale AI');
  assert.equal((await db.query('SELECT status FROM commands WHERE id=$1',[command.id]))[0].status,'superseded');
  const result=await call('DELETE',`/v1/bots/${id}`);assert.equal(result.statusCode,200);
  assert.equal((await call('POST',`/v1/bots/${id}/actions`,{action:'start',idempotencyKey:'resurrect'})).statusCode,409);
  assert.equal((await call('DELETE',`/v1/bots/${id}`)).statusCode,409);
  const [bot]=await db.query('SELECT * FROM bots WHERE id=$1',[id]);const [deletion]=await db.query("SELECT * FROM commands WHERE bot_id=$1 AND kind='delete'",[id]);assert.equal(bot.generation,deletion.generation);
 }finally{await app.close()}
});
