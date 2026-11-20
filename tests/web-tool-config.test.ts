import test from 'node:test';import assert from 'node:assert/strict';import path from 'node:path';import os from 'node:os';import {mkdtemp} from 'node:fs/promises';
import {createApp} from '../apps/control-api/src/app.js';import {issueTestInvite} from './helpers/invitations.js';import {defaultConfig} from '../packages/contracts/index.js';
test('authenticated tool edits preserve legacy defaults, CAS, tenancy and isolated live apply',async t=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'fish-tool-edit-')), {app,db,agent}=await createApp({root,agent:false});t.after(()=>app.close());
 const register=async(email:string)=>{const r=await app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(db),email,password:'tool-test-123',name:'test'}});assert.equal(r.statusCode,200,r.body);return{fish_session:r.cookies[0].value}};
 const cookies=await register('tool-owner@example.test'),other=await register('tool-other@example.test');
 const made=await app.inject({method:'POST',url:'/v1/bots',cookies,payload:{name:'tool edit',protocol:'mock',config:defaultConfig}});assert.equal(made.statusCode,201,made.body);const id=made.json().id;
 const [row]=await db.query('SELECT config FROM bot_config_revisions WHERE bot_id=$1 AND revision=1',[id]);delete row.config.toolPrompts;
 await db.query('UPDATE bot_config_revisions SET config=$2 WHERE bot_id=$1 AND revision=1',[id,JSON.stringify(row.config)]);
 let tools=0,persona=0;agent.applyUserTools=async(bot,tenant)=>{assert.equal(bot,id);assert.ok(tenant);tools++;return true};agent.applyUserPersona=async()=>{persona++;return true};
 const url='/v1/bots/'+id+'/config',payload={expectedRevision:1,name:'tool edit',config:{...defaultConfig,toolPrompts:{dsh:'tool-only instructions'}}};
 assert.equal((await app.inject({method:'PUT',url,cookies:other,payload})).statusCode,404);
 const saved=await app.inject({method:'PUT',url,cookies,payload});assert.equal(saved.statusCode,200,saved.body);assert.equal(saved.json().applied,true);assert.equal(tools,1);assert.equal(persona,0);
 assert.equal((await app.inject({method:'PUT',url,cookies,payload})).statusCode,409);assert.equal(tools,1);
 const [audit]=await db.query("SELECT detail FROM audit_events WHERE bot_id=$1 AND event='config.saved'",[id]);assert.equal(audit.detail.source,'authenticated_web_config');assert.ok(audit.detail.actorId);assert.equal(audit.detail.credentialChanged,false);
 const personaSaved=await app.inject({method:'PUT',url,cookies,payload:{...payload,expectedRevision:2,config:{...payload.config,systemPrompt:'new persona'}}});assert.equal(personaSaved.statusCode,200,personaSaved.body);assert.equal(persona,1);assert.equal(tools,1);
});
