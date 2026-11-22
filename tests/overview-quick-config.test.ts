import test from 'node:test';import assert from'node:assert/strict';import{mkdtemp}from'node:fs/promises';import path from'node:path';import{createApp}from'../apps/control-api/src/app.js';import{issueTestInvite}from'./helpers/invitations.js';import{defaultReasoningEffort}from'../packages/shared/model-catalog-options.js';
test('reasoning defaults to medium only with advertised support',()=>{assert.equal(defaultReasoningEffort(),undefined);assert.equal(defaultReasoningEffort({reasoningSupported:false}),undefined);assert.equal(defaultReasoningEffort({reasoningSupported:true}),'medium');assert.equal(defaultReasoningEffort({reasoningEfforts:['low','high']}),undefined);assert.equal(defaultReasoningEffort({reasoningEfforts:['medium','high']}),'medium');assert.equal(defaultReasoningEffort({reasoningSupported:false,reasoningEfforts:['medium']}),undefined)});
test('real execution is default, only administrators can configure shadow, ownership and epoch remain enforced',async()=>{const x=await createApp({root:await mkdtemp(path.resolve('.local/autonomy-ui-default-')),databaseUrl:'',agent:false});try{const r=await x.app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(x.db),email:'default@fixture.test',password:'fixture-password-123',name:'测试'}});assert.equal(r.statusCode,200);const cookies={fish_session:r.cookies[0].value};const b=await x.app.inject({method:'POST',url:'/v1/bots',cookies,payload:{name:'默认测试',protocol:'mock'}});const url='/v1/bots/'+b.json().id+'/autonomous';const view=(await x.app.inject({url,cookies})).json();assert.equal(view.settings.config.mode,'active');assert.equal(view.settings.config.rollout,'live');assert.equal(view.canConfigureShadow,false);const body={expectedEpoch:view.settings.epoch,config:{...view.settings.config,mode:'active',rollout:'shadow'}};assert.equal((await x.app.inject({url,method:'PUT',cookies,payload:body})).statusCode,403);assert.equal((await x.app.inject({url,method:'PUT',cookies,payload:{...body,config:{...body.config,rollout:'live'}}})).statusCode,200);assert.equal((await x.app.inject({url,method:'PUT',cookies,payload:{...body,config:{...body.config,rollout:'live'}}})).statusCode,409);await x.db.query('UPDATE users SET is_admin=true WHERE email=$1',['default@fixture.test']);const adminView=(await x.app.inject({url,cookies})).json();assert.equal(adminView.canConfigureShadow,true);assert.equal((await x.app.inject({url,method:'PUT',cookies,payload:{...body,expectedEpoch:adminView.settings.epoch}})).statusCode,200);const foreign=(await x.app.inject({url:url.replace(b.json().id,'00000000-0000-4000-8000-000000000001'),cookies}));assert.equal(foreign.statusCode,404)}finally{await x.app.close()}});

test('group participation follows the service whitelist on create and save',async()=>{
 const {defaultConfig}=await import('../packages/contracts/index.js');
 const x=await createApp({root:await mkdtemp(path.resolve('.local/group-whitelist-')),databaseUrl:'',agent:false});
 try{
  const registration=await x.app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(x.db),email:'groups@fixture.test',password:'fixture-password-123',name:'测试'}});
  assert.equal(registration.statusCode,200);
  const cookies={fish_session:registration.cookies[0].value};
  const created=await x.app.inject({method:'POST',url:'/v1/bots',cookies,payload:{name:'群白名单',protocol:'mock',config:{...defaultConfig,groups:['742437724'],behavior:{...defaultConfig.behavior,groupParticipationGroups:['742437724','1136818758']}}}});
  assert.equal(created.statusCode,201);
  const url='/v1/bots/'+created.json().id;
  const saved=(await x.app.inject({url,cookies})).json();
  assert.deepEqual(saved.config.behavior.groupParticipationGroups,['742437724']);
  const update=await x.app.inject({url:url+'/config',method:'PUT',cookies,payload:{name:'群白名单',expectedRevision:saved.bot.revision,config:{...saved.config,groups:[]}}});
  assert.equal(update.statusCode,200,update.body);
  const after=(await x.app.inject({url,cookies})).json();
  assert.deepEqual(after.config.behavior.groupParticipationGroups,[]);
  assert.equal(after.config.behavior.groupParticipationEnabled,false);
 }finally{await x.app.close()}
});
