import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {createApp} from '../apps/control-api/src/app.js';
import {issueTestInvite} from './helpers/invitations.js';

test('Persona authorization comes from authenticated CAS saves and survives unrelated later revisions',async t=>{
 const {app,db,agent}=await createApp({root:await mkdtemp(path.join(os.tmpdir(),'fish-web-base-')),agent:false});t.after(()=>app.close());
 const registration=await app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(db),email:'web-base@example.test',name:'test',password:'web-base-test-123'}});
 assert.equal(registration.statusCode,200,registration.body);const cookies={fish_session:registration.cookies[0].value};
 const created=await app.inject({method:'POST',url:'/v1/bots',cookies,payload:{name:'base edit',protocol:'mock'}});assert.equal(created.statusCode,201,created.body);const id=created.json().id;
 const context=async()=>agent.context((await db.query('SELECT * FROM bots WHERE id=$1',[id]))[0]);
 assert.equal((await context()).personaEdit,undefined);
 let detail=(await app.inject({method:'GET',url:`/v1/bots/${id}`,cookies})).json();
 const save=(expectedRevision:number,config:any)=>app.inject({method:'PUT',url:`/v1/bots/${id}/config`,cookies,payload:{expectedRevision,name:'base edit',expectedUin:'',config}});
 const old=detail.config.systemPrompt;
 const next={...detail.config,systemPrompt:'A distinct user-saved Persona'};
 const result=await save(detail.bot.revision,next);assert.equal(result.statusCode,200,result.body);
 const adopted=await context();assert.ok(adopted.systemChangePermitId);
 assert.equal(adopted.personaEdit?.target,createHash('sha256').update(next.systemPrompt).digest('hex'));
 assert.ok(adopted.personaEdit?.origins.includes(createHash('sha256').update(old).digest('hex')));
 const stale=await save(detail.bot.revision,{...next,systemPrompt:'stale overwritten Persona'});assert.equal(stale.statusCode,409,stale.body);
 const unrelated=await save(result.json().revision,{...next,historyTurns:next.historyTurns===20?21:20});assert.equal(unrelated.statusCode,200,unrelated.body);
 const later=await context();assert.equal(later.systemChangePermitId,adopted.systemChangePermitId);assert.deepEqual(later.personaEdit,adopted.personaEdit);
 // No request-body permit or arbitrary bot identity is accepted by this boundary.
 const registration2=await app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(db),email:'other-base@example.test',name:'other',password:'other-base-test-123'}});assert.equal(registration2.statusCode,200,registration2.body);
 const otherCookies={fish_session:registration2.cookies[0].value};
 const denied=await app.inject({method:'PUT',url:`/v1/bots/${id}/config`,cookies:otherCookies,payload:{expectedRevision:unrelated.json().revision,name:'not owner',expectedUin:'',config:next}});assert.equal(denied.statusCode,404,denied.body);
 const other=await app.inject({method:'POST',url:'/v1/bots',cookies:otherCookies,payload:{name:'other bot',protocol:'mock'}});assert.equal(other.statusCode,201,other.body);
 const otherContext=await agent.context((await db.query('SELECT * FROM bots WHERE id=$1',[other.json().id]))[0]);
 assert.equal(otherContext.systemChangePermitId,undefined);assert.equal(otherContext.personaEdit,undefined);
});
