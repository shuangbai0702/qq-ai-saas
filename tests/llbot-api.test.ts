import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import os from 'node:os';
import {mkdtemp,readFile} from 'node:fs/promises';
import {createApp} from '../apps/control-api/src/app.js';
import {NodeAgent} from '../apps/node-agent/src/agent.js';
import {issueTestInvite} from './helpers/invitations.js';
import {DatabaseSync} from 'node:sqlite';

test('LLBot and NapCat coexist; switching is owned, CAS/generation fenced, durably executed and preserves Bot config/Journal',async t=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'fishbot-dual-api-')),{app,db}=await createApp({root,agent:false});t.after(()=>app.close());
 const register=async(email:string)=>(await app.inject({method:'POST',url:'/v1/auth/register',payload:{email,password:'test-password-123',name:'fixture',inviteCode:await issueTestInvite(db,3)}})).cookies[0].value;
 const a=await register('dual-a@example.com'),b=await register('dual-b@example.com');
 const call=(cookie:string,method:any,url:string,payload?:any)=>app.inject({method,url,payload,cookies:{fish_session:cookie}});
 const n=await call(a,'POST','/v1/bots',{name:'existing NapCat',runtimeMode:'dedicated'}),l=await call(a,'POST','/v1/bots',{name:'new LLBot',protocol:'llbot'});
 assert.equal(n.statusCode,201,n.body);assert.equal(l.statusCode,201,l.body);
 const id=l.json().id,nap=n.json().id,url='/v1/bots/'+id;
 assert.equal((await call(a,'GET','/v1/bots/'+nap)).json().bot.protocol,'napcat');assert.equal((await call(a,'GET',url)).json().bot.protocol,'llbot');
 const [before]=await db.query('SELECT * FROM bots WHERE id=$1',[id]);
 const journalFile=path.join(root,'bots',id,'behavior.sqlite');
 const journal=()=>{const j=new DatabaseSync(journalFile,{readOnly:true});try{return JSON.stringify(j.prepare('SELECT * FROM fishbot_readonly_journals ORDER BY rowid').all())}finally{j.close()}};
 const journalBefore=journal(),configBefore=await db.query('SELECT * FROM bot_config_revisions WHERE bot_id=$1 ORDER BY revision',[id]);
 const payload={protocol:'napcat',expectedRevision:before.revision,expectedGeneration:before.generation};
 assert.equal((await call(b,'PUT',url+'/protocol',payload)).statusCode,404);
 assert.equal((await call(a,'PUT',url+'/protocol',{...payload,expectedRevision:99})).statusCode,409);
 assert.equal((await call(a,'PUT',url+'/protocol',{...payload,config:{}})).statusCode,400);
 await db.query("UPDATE bots SET desired_state='running',observed_state='running' WHERE id=$1",[id]);assert.equal((await call(a,'PUT',url+'/protocol',payload)).statusCode,409);
 await db.query("UPDATE bots SET desired_state='stopped',observed_state='stopped' WHERE id=$1",[id]);
 const results=await Promise.all([call(a,'PUT',url+'/protocol',payload),call(a,'PUT',url+'/protocol',payload)]);assert.deepEqual(results.map(r=>r.statusCode).sort(),[200,409]);
 const [queued]=await db.query("SELECT * FROM commands WHERE bot_id=$1 AND kind='switch-protocol'",[id]);assert.ok(queued);
 assert.equal((await call(a,'GET',url)).json().bot.protocol,'llbot','API success is queued, not applied');
 assert.equal((await call(a,'POST',url+'/actions',{action:'start',idempotencyKey:'while-switch'})).statusCode,409);
 assert.equal((await call(a,'POST',url+'/login-sessions',{})).statusCode,409);
 const agent:any=new NodeAgent(db,root,Buffer.alloc(32)),stopped:string[]=[];
 agent.protocols.llbot.stop=async(bot:string)=>{stopped.push('llbot:'+bot)};agent.ai.stop=async(bot:string)=>{stopped.push('ai:'+bot)};agent.shared.stop=async(bot:string)=>{stopped.push('shared:'+bot)};agent.host.inspect=async()=>null;
 await agent.execute(queued);
 const [after]=await db.query('SELECT * FROM bots WHERE id=$1',[id]);assert.equal(after.protocol,'napcat');assert.equal(after.id,before.id);assert.equal(after.revision,before.revision);assert.equal(after.applied_revision,before.applied_revision);assert.equal(after.expected_uin,before.expected_uin);assert.deepEqual(await db.query('SELECT * FROM bot_config_revisions WHERE bot_id=$1 ORDER BY revision',[id]),configBefore);assert.equal(after.observed_state,'stopped');assert.equal(journal(),journalBefore);
 assert.deepEqual(stopped,[before.runtime_mode==='shared'?'shared:'+id:'ai:'+id,'llbot:'+id]);assert.equal((await db.query('SELECT status FROM commands WHERE id=$1',[queued.id]))[0].status,'completed');
 assert.equal((await db.query("SELECT count(*) AS n FROM audit_events WHERE bot_id=$1 AND event='protocol.switched'",[id]))[0].n,1);
 // A late old-generation command cannot stop or switch the current protocol.
 const current={protocol:'llbot',expectedRevision:after.revision,expectedGeneration:after.generation};
 const next=await call(a,'PUT',url+'/protocol',current);assert.equal(next.statusCode,200,next.body);await db.query('UPDATE bots SET generation=generation+1 WHERE id=$1',[id]);
 const [late]=await db.query('SELECT * FROM commands WHERE id=$1',[next.json().command]);await agent.execute(late);assert.equal(stopped.length,2);assert.equal((await db.query('SELECT status FROM commands WHERE id=$1',[late.id]))[0].status,'superseded');assert.equal((await db.query('SELECT protocol FROM bots WHERE id=$1',[id]))[0].protocol,'napcat');
 // Dedicated mode uses its AI owner, without touching the other shared Bot.
 agent.protocols.napcat.stop=async(bot:string)=>{stopped.push('napcat:'+bot)};
 const napBefore=(await db.query('SELECT * FROM bots WHERE id=$1',[nap]))[0];
 const dedicated=await call(a,'PUT','/v1/bots/'+nap+'/protocol',{protocol:'llbot',expectedRevision:napBefore.revision,expectedGeneration:napBefore.generation});assert.equal(dedicated.statusCode,200,dedicated.body);
 await agent.execute((await db.query('SELECT * FROM commands WHERE id=$1',[dedicated.json().command]))[0]);assert.deepEqual(stopped.slice(-2),['ai:'+nap,'napcat:'+nap]);assert.equal((await db.query('SELECT protocol FROM bots WHERE id=$1',[nap]))[0].protocol,'llbot');

});
