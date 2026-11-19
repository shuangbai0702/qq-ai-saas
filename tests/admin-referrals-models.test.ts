import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm,writeFile} from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {createApp} from '../apps/control-api/src/app.js';
import {loadKey,encrypt} from '../packages/shared/security.js';
import {platformSecretPurpose} from '../apps/control-api/src/platform-model.js';
import {issueTestInvite} from './helpers/invitations.js';
import {defaultConfig} from '../packages/contracts/index.js';

test('admin model configuration controls routing, fences stale probes and survives restart',async()=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'fish-admin-models-')),key=await loadKey(root);
 const secret='fixture-new-provider-secret',legacy='fixture-old-provider-secret';
 await writeFile(path.join(root,'platform-model-secret'),encrypt(key,platformSecretPurpose,JSON.stringify({primary:legacy,fallback:legacy})));
 let release!:()=>void,started!:()=>void,wait=false;const barrier=new Promise<void>(r=>release=r),entered=new Promise<void>(r=>started=r);
 const calls:{model:string;url:string;credential:string|null}[]=[];
 let instance=await createApp({root,databaseUrl:'',agent:false,platformModelTransport:async(url,init)=>{
  const body=JSON.parse(String(init?.body));calls.push({model:body.model,url:String(url),credential:new Headers(init?.headers).get('Authorization')});
  if(wait){started();await barrier;}
  return Response.json({model:body.model,choices:[{message:{role:'assistant',content:'OK'}}],usage:{prompt_tokens:2,completion_tokens:1,total_tokens:3}});
 }});
 try{
  const invite=await issueTestInvite(instance.db),register=await instance.app.inject({method:'POST',url:'/v1/auth/register',payload:{email:'admin@models.test',name:'管理员',password:'fixture-pass',inviteCode:invite}});assert.equal(register.statusCode,200,register.body);
  const cookie=register.cookies.map(c=>c.name+'='+c.value).join(';');
  const call=(method:any,url:string,payload?:any)=>instance.app.inject({method,url,payload,headers:{cookie}});
  assert.equal((await call('GET','/v1/admin/platform-models')).statusCode,403);
  await instance.db.query('UPDATE users SET is_admin=true');
  const seeded=(await call('GET','/v1/admin/platform-models')).json();assert.equal(seeded.items.length,2);assert.ok(!JSON.stringify(seeded).includes(legacy));
  const created=await call('POST','/v1/admin/platform-models',{name:'新优先模型',baseUrl:'https://model.example/v1/',model:'fixture-priority-model',apiKey:secret,priority:1,enabled:true});assert.equal(created.statusCode,200,created.body);
  const item=created.json().item;assert.equal(item.base_url,'https://model.example/v1');assert.ok(!created.body.includes(secret));
  const [user]=await instance.db.query('SELECT m.tenant_id FROM memberships m JOIN users u ON u.id=m.user_id WHERE u.email=$1',['admin@models.test']);
  await instance.db.query('INSERT INTO platform_model_credits(tenant_id,granted) VALUES($1,100000)',[user.tenant_id]);
  const botResponse=await call('POST','/v1/bots',{name:'可控测试',protocol:'mock',config:{...defaultConfig,platformModel:true}});assert.equal(botResponse.statusCode,201,botResponse.body);
  const [bot]=await instance.db.query('SELECT * FROM bots WHERE id=$1',[botResponse.json().id]);const context=await instance.agent.context(bot);
  const actual=await instance.app.inject({method:'POST',url:'/v1/platform-model/chat/completions',headers:{authorization:'Bearer '+context.apiKey},payload:{model:'platform',messages:[{role:'user',content:'fixture'}],max_tokens:16}});
  assert.equal(actual.statusCode,200,actual.body);assert.equal(actual.json().model,'platform');assert.equal(calls[0].model,'fixture-priority-model');assert.equal(calls[0].credential,'Bearer '+secret);
  const body={name:item.name,baseUrl:item.base_url,model:item.model,priority:2,enabled:true,expectedRevision:item.revision};
  const saves=await Promise.all([call('PUT','/v1/admin/platform-models/'+item.id,body),call('PUT','/v1/admin/platform-models/'+item.id,{...body,priority:3})]);assert.deepEqual(saves.map(s=>s.statusCode).sort(),[200,409]);
  const revision=saves.find(s=>s.statusCode===200)!.json().item.revision;
  wait=true;const probe=call('POST','/v1/admin/platform-models/'+item.id+'/probe',{expectedRevision:revision});await entered;
  const edited=await call('PUT','/v1/admin/platform-models/'+item.id,{...body,model:'fixture-changed',expectedRevision:revision});assert.equal(edited.statusCode,200);
  release();const receipt=await probe;assert.equal(receipt.json().applied,false);
  const current=(await call('GET','/v1/admin/platform-models')).json().items.find((p:any)=>p.id===item.id);assert.equal(current.last_status,'unchecked');
  assert.equal((await call('PUT','/v1/admin/platform-models/'+item.id,{...body,baseUrl:'https://different.example/v1',expectedRevision:current.revision})).statusCode,400);
  await instance.app.close();
  instance=await createApp({root,databaseUrl:'',agent:false});
  const persisted=(await call('GET','/v1/admin/platform-models')).json();assert.equal(persisted.items.length,3);assert.equal(persisted.items.find((p:any)=>p.id===item.id).model,'fixture-changed');
 }finally{release();await instance.app.close();await rm(root,{recursive:true,force:true})}
});

test('admin referrals distinguish generated links, client copies and committed registrations',async()=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'fish-admin-referrals-')),key=await loadKey(root);
 await writeFile(path.join(root,'platform-model-secret'),encrypt(key,platformSecretPurpose,'fixture-secret'));
 const {app,db}=await createApp({root,databaseUrl:'',agent:false});
 try{
  const register=async(email:string,referral?:{code:string;activation:string})=>{
   const invite=referral?undefined:await issueTestInvite(db);
   const r=await app.inject({method:'POST',url:'/v1/auth/register',payload:{email,name:email,password:'fixture-pass',inviteCode:referral?.activation||invite!,...(referral?{referralCode:referral.code}:{})}});assert.equal(r.statusCode,200,r.body);return r.cookies.map(c=>c.name+'='+c.value).join(';');
  };
  const cookie=await register('inviter@referral.test'),ordinary=await register('ordinary@referral.test');
  const call=(method:any,url:string,payload?:any,session=cookie)=>app.inject({method,url,payload,headers:{cookie:session}});
  assert.equal((await call('GET','/v1/admin/referrals')).statusCode,403);
  await db.query("UPDATE users SET is_admin=true WHERE email='inviter@referral.test'");
  const invite=await issueTestInvite(db),link=(await call('POST','/v1/referrals',{inviteCode:invite})).json();assert.ok(link.code);
  const item=(await call('GET','/v1/admin/referrals?q=inviter')).json().items[0];assert.equal(item.generated,true);assert.equal(item.invited,0);assert.equal(item.copies,0);assert.ok(item.last_generated_at);
  assert.equal((await call('POST','/v1/referrals/copied',{code:link.code},ordinary)).statusCode,403);
  assert.equal((await call('POST','/v1/referrals/copied',{code:link.code})).statusCode,200);
  await register('invitee@referral.test',link);
  const after=(await call('GET','/v1/admin/referrals?q=inviter')).json().items[0];assert.equal(after.invited,1);assert.equal(after.copies,1);assert.ok(after.last_success_at);
  const detail=await call('GET','/v1/admin/referrals/'+item.id);assert.equal(detail.json().items[0].email,'invitee@referral.test');assert.ok(!detail.body.includes(link.activation));
  assert.equal((await call('GET','/v1/admin/referrals/'+item.id,undefined,ordinary)).statusCode,403);
  assert.equal((await call('GET','/v1/admin/referrals?page=0')).statusCode,400);
 }finally{await app.close();await rm(root,{recursive:true,force:true})}
});
