import test from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {mkdtemp,rm,writeFile} from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {createApp} from '../apps/control-api/src/app.js';
import {platformSecretPurpose} from '../apps/control-api/src/platform-model.js';
import {encrypt,digest,loadKey} from '../packages/shared/security.js';
import {defaultConfig} from '../packages/contracts/index.js';

test('referral registration, scoped credentials, concurrency, usage and unknown',async()=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'fish-referral-'));
 const secret='fixture-upstream-credential';
 const primary='fixture-primary-credential';
 const key=await loadKey(root);
 await writeFile(path.join(root,'platform-model-secret'),encrypt(key,platformSecretPurpose,JSON.stringify({primary,fallback:secret})));
 const routes:string[]=[];
 let requests=0,oversizedCancelled=false,release!:()=>void,started!:()=>void;
 const barrier=new Promise<void>(resolve=>{release=resolve});
 const firstRequest=new Promise<void>(resolve=>{started=resolve});
 const receipt={prompt_tokens:10,completion_tokens:5,total_tokens:15,prompt_tokens_details:{cached_tokens:8}};
 const {app,db,agent}=await createApp({root,databaseUrl:'',agent:false,platformModelTransport:async(url,init)=>{
  const isPrimary=new URL(String(url)).hostname==='crab-ai.net';
  assert.equal(new Headers(init?.headers).get('Authorization'),'Bearer '+(isPrimary?primary:secret));
  requests++;
  if(requests===1){started();await barrier;}
  const body=JSON.parse(init!.body as string);
  assert.equal(body.model,isPrimary?'gpt-6.1-sol':'gpt-5.6-luna');
  routes.push(body.model);
  if(body.messages[0].content==='quota'&&isPrimary)return Response.json({error:{code:'insufficient_user_quota'}},{status:403});
  if(body.messages[0].content==='quota_both')return new Response(null,{status:402});
  if(body.messages[0].content==='unknown_after_quota'&&isPrimary)return Response.json({error:{type:'insufficient_quota'}},{status:429});
  if(body.messages[0].content==='unknown_after_quota')throw new Error('fallback connection was lost');
  if(body.messages[0].content==='unknown')throw new Error('connection was lost');
  if(body.messages[0].content==='oversized')return new Response(new ReadableStream({
   pull(controller){controller.enqueue(new Uint8Array(1024*1024));},
   cancel(){oversizedCancelled=true;},
  }));
  if(body.messages[0].content==='rejected')return new Response(null,{status:429});
  if(body.messages[0].content==='invalid_contract')return Response.json({error:{type:'invalid_request_error',code:'unsupported_parameter'}},{status:400});
  if(body.stream){
   assert.deepEqual(body.stream_options,{include_usage:true});
   if(body.messages[0].content==='inline_usage'||body.messages[0].content==='interrupted_usage'){
    const frame=(usage:any,finish_reason:string|null)=>'data: '+JSON.stringify({model:body.model,choices:[{delta:{content:'OK'},finish_reason}],usage})+'\n\n';
    const partial=frame({prompt_tokens:1,completion_tokens:1,total_tokens:2},null);
    return new Response(partial+(body.messages[0].content==='interrupted_usage'?'':frame(receipt,'stop')+frame(receipt,'stop')+'data: [DONE]\n\n'));
   }
   return new Response('data: '+JSON.stringify({model:body.model,choices:[],usage:receipt})+'\n\ndata: [DONE]\n\n');
  }
  return Response.json({model:body.model,choices:[],usage:receipt});
 }});
 const request=(url:string,payload?:any,cookie?:string)=>app.inject({method:payload===undefined?'GET':'POST',url,headers:cookie?{cookie}:{},payload});
 try{
  const activation='fixture-invitation';
  await db.query("INSERT INTO invitations(id,code_hash,code_prefix,max_uses,expires_at,bot_limit) VALUES($1,$2,'fixture',5,now()+interval '1 day',1)",[randomUUID(),digest(activation)]);
  async function register(name:string,referralCode?:string,inviteCode=activation){return request('/v1/auth/register',{name,email:name+'@example.com',password:'test-password',inviteCode,referralCode})}
  const owner=await register('inviter');assert.equal(owner.statusCode,200);
  const cookie=(response:typeof owner)=>String(response.headers['set-cookie']).split(';')[0];
  const inviter=cookie(owner);
  assert.equal((await request('/v1/referrals',undefined,inviter)).json().canInvite,true);
  const invitation=await request('/v1/referrals',{inviteCode:activation},inviter);assert.equal(invitation.statusCode,200);
  const {code,activation:linkActivation}=invitation.json();
  assert.notEqual(linkActivation,activation);
  assert.equal((await request('/v1/referrals',{inviteCode:activation},inviter)).json().code,code);
  assert.equal((await register('unbound','T'.repeat(24),linkActivation)).statusCode,400);
  assert.equal((await register('stripped',undefined,linkActivation)).statusCode,400);
  assert.equal((await register('legacy',code)).statusCode,400);
  const invited=await register('invitee',code,linkActivation);assert.equal(invited.statusCode,200);
  assert.equal((await register('invitee',code,linkActivation)).statusCode,409);
  const invitee=cookie(invited);
  const [inviteeMembership]=await db.query('SELECT tenant_id FROM memberships WHERE user_id=$1',[invited.json().user.id]);
  const oldReferral='R'.repeat(24);
  await db.query('INSERT INTO referral_codes(user_id,tenant_id,code) VALUES($1,$2,$3)',[invited.json().user.id,inviteeMembership.tenant_id,oldReferral]);
  const restricted=(await request('/v1/referrals',undefined,invitee)).json();
  assert.equal(restricted.canInvite,false);
  assert.equal(restricted.code,null);
  assert.equal((await request('/v1/referrals',{inviteCode:activation},invitee)).statusCode,403);
  const [originalInvite]=await db.query('SELECT id FROM invitations WHERE code_hash=$1',[digest(activation)]);
  const denied=await register('next-generation',oldReferral,encrypt(key,`referral-activation:${oldReferral}`,originalInvite.id));
  assert.equal(denied.statusCode,403);
  assert.equal((await db.query('SELECT id FROM users WHERE email=$1',['next-generation@example.com'])).length,0);
  assert.equal((await request('/v1/referrals',undefined,inviter)).json().invited,1);
  assert.equal((await request('/v1/platform-model/balance',undefined,inviter)).json().available,30_000_000);
  assert.equal((await request('/v1/platform-model/balance',undefined,invitee)).json().available,20_000_000);
  assert.equal((await db.query('SELECT used_count FROM invitations'))[0].used_count,2);
  const botResult=await request('/v1/bots',{name:'platform bot',protocol:'mock',config:{...defaultConfig,platformModel:true},apiKey:'test-personal-key'},invitee);
  assert.equal(botResult.statusCode,201);
  const [bot]=await db.query('SELECT * FROM bots WHERE id=$1',[botResult.json().id]);
  const context=await agent.context(bot);
  assert.equal(context.config.model,'platform');
  assert.equal(await agent.personalModelKey(bot),'test-personal-key');
  assert.equal((await request('/v1/bots/'+bot.id,undefined,inviter)).statusCode,404);
  // This fixture exercises insufficient platform credit without a personal
  // fallback. Personal-key retention was checked above; automatic fallback is
  // covered separately with independent tenants and concurrent reservations.
  await db.query('DELETE FROM secrets WHERE bot_id=$1',[bot.id]);
  const call=(content='normal',stream=false,credential=context.apiKey)=>app.inject({method:'POST',url:'/v1/platform-model/chat/completions',headers:{authorization:'Bearer '+credential},payload:{model:'platform',max_tokens:50,stream,messages:[{role:'user',content}]}});
  assert.equal((await call('normal',false,'untrusted')).statusCode,401);
  // Keep just enough credit for one concurrent reservation.
  await db.query('UPDATE platform_model_credits SET spent=granted-4500 WHERE tenant_id=$1',[bot.tenant_id]);
  const first=call();await firstRequest;
  try{assert.equal((await call()).statusCode,402);assert.equal(requests,1);}finally{release()}
  const done=await first;assert.equal(done.statusCode,200);assert.equal(done.json().model,'platform');
  assert.equal(done.json().usage.prompt_tokens_details.cached_tokens,8);
  assert.equal((await request('/v1/platform-model/balance',undefined,invitee)).json().available,4485);
  const stream=await call('stream',true);assert.equal(stream.statusCode,200);assert.match(stream.body,/\[DONE\]/);assert.match(stream.body,/"model":"platform"/);
  assert.equal((await request('/v1/platform-model/balance',undefined,invitee)).json().available,4470);
  assert.equal((await call('rejected')).statusCode,429);
  assert.deepEqual(routes.slice(-2),['gpt-6.1-sol','gpt-5.6-luna']); // Explicit rate rejection now tries each configured priority.
  assert.equal((await request('/v1/platform-model/balance',undefined,invitee)).json().available,4470);
  const beforeInvalid=requests;
  assert.equal((await call('invalid_contract')).statusCode,502);
  assert.equal(requests,beforeInvalid+1,'a validation refusal must not replay against another provider');
  assert.equal((await request('/v1/platform-model/balance',undefined,invitee)).json().available,4470);
  assert.equal((await call('unknown')).statusCode,502);
  assert.equal(Number((await db.query('SELECT count(*) AS count FROM platform_model_reservations'))[0].count),1);
  await db.query('UPDATE platform_model_credits SET granted=granted+50000 WHERE tenant_id=$1',[bot.tenant_id]);
  const balance=async()=> (await request('/v1/platform-model/balance',undefined,invitee)).json().available;
  const beforeFallback=await balance();
  const quota=await call('quota',true);
  assert.equal(quota.statusCode,200);assert.match(quota.body,/\[DONE\]/);assert.match(quota.body,/"model":"platform"/);
  assert.deepEqual(routes.slice(-2),['gpt-6.1-sol','gpt-5.6-luna']);
  assert.equal(await balance(),beforeFallback-15);
  assert.equal((await call('quota_both')).statusCode,503);
  assert.deepEqual(routes.slice(-2),['gpt-6.1-sol','gpt-5.6-luna']);
  assert.equal(await balance(),beforeFallback-15);
  assert.equal((await call('unknown_after_quota')).statusCode,502);
  assert.deepEqual(routes.slice(-2),['gpt-6.1-sol','gpt-5.6-luna']);
  assert.equal((await call('oversized')).statusCode,502);
  assert.equal(oversizedCancelled,true);
  assert.equal(Number((await db.query('SELECT count(*) AS count FROM platform_model_reservations'))[0].count),3);
  const ledger=async()=> (await db.query('SELECT spent,reserved FROM platform_model_credits WHERE tenant_id=$1',[bot.tenant_id]))[0];
  const beforeInline=await ledger();
  assert.equal((await call('inline_usage',true)).statusCode,200);
  const afterInline=await ledger();assert.equal(Number(afterInline.spent),Number(beforeInline.spent)+15);assert.equal(Number(afterInline.reserved),Number(beforeInline.reserved));
  assert.equal((await call('interrupted_usage',true)).statusCode,200);
  const afterInterrupted=await ledger();assert.equal(Number(afterInterrupted.spent),Number(afterInline.spent));assert.ok(Number(afterInterrupted.reserved)>Number(afterInline.reserved));
  assert.equal((await request('/v1/health')).statusCode,200);
  await db.query('UPDATE bots SET generation=generation+1 WHERE id=$1',[bot.id]);
  assert.equal((await call()).statusCode,401);
 }finally{release();await app.close();await rm(root,{recursive:true,force:true});}
});

test('an unreadable platform credential leaves normal platform services available',async()=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'fish-platform-secret-'));
 await writeFile(path.join(root,'platform-model-secret'),'invalid encrypted fixture');
 let instance:Awaited<ReturnType<typeof createApp>>|undefined;
 try{
  instance=await createApp({root,databaseUrl:'',agent:false});
  assert.equal((await instance.app.inject('/v1/health')).statusCode,200);
  assert.equal((await instance.app.inject({method:'POST',url:'/v1/platform-model/chat/completions',payload:{}})).statusCode,503);
 }finally{try{await instance?.app.close()}finally{await rm(root,{recursive:true,force:true})}}
});


test('unused registration entitlement regenerates encoded links and only committed signups consume it',async()=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'fish-referral-entitlement-')),key=await loadKey(root);
 await writeFile(path.join(root,'platform-model-secret'),encrypt(key,platformSecretPurpose,JSON.stringify({primary:'fixture',fallback:'fixture'})));
 const {app,db}=await createApp({root,databaseUrl:'',agent:false});
 try{
  const activation='fixture-original-entitlement',id=randomUUID();
  await db.query("INSERT INTO invitations(id,code_hash,code_prefix,max_uses,expires_at) VALUES($1,$2,'fixture',3,now()+interval '1 day')",[id,digest(activation)]);
  const signup=(email:string,code=activation,referralCode?:string)=>app.inject({method:'POST',url:'/v1/auth/register',payload:{email,name:'fixture',password:'fixture-pass',inviteCode:code,referralCode}});
  const owner=await signup('owner@entitlement.test');assert.equal(owner.statusCode,200);
  const cookie=owner.cookies.map(c=>c.name+'='+c.value).join(';');
  const call=(method:any,url:string,payload?:any)=>app.inject({method,url,payload,headers:{cookie}});
  const used=async()=>Number((await db.query('SELECT used_count FROM invitations WHERE id=$1',[id]))[0].used_count);
  assert.equal((await call('GET','/v1/referrals')).json().remainingUses,2);
  const first=await call('POST','/v1/referrals',{});assert.equal(first.statusCode,200,first.body);const a=first.json();
  assert.equal((await call('POST','/v1/referrals/copied',{code:a.code})).statusCode,200);
  const second=await call('POST','/v1/referrals',{});assert.equal(second.statusCode,200,second.body);const b=second.json();
  assert.equal(a.code,b.code);assert.notEqual(a.activation,b.activation);assert.equal(await used(),1);
  const link=new URL('https://fixture.test/#/invite/'+b.code+'?'+new URLSearchParams({activation:b.activation}));
  assert.equal(new URLSearchParams(link.hash.split('?')[1]).get('activation'),b.activation);
  assert.equal((await signup('bad@entitlement.test','19650831',b.code)).statusCode,400);assert.equal(await used(),1);
  assert.equal((await signup('friend@entitlement.test',a.activation,a.code)).statusCode,200);assert.equal(await used(),2);
  assert.equal((await signup('friend@entitlement.test',b.activation,b.code)).statusCode,409);assert.equal(await used(),2,'failed registration rolls back the invitation use');
  const regenerated=await call('POST','/v1/referrals',{});assert.equal(regenerated.statusCode,200);assert.equal(regenerated.json().remainingUses,1);
  const results=await Promise.all([signup('last-a@entitlement.test',b.activation,b.code),signup('last-b@entitlement.test',b.activation,b.code)]);
  assert.deepEqual(results.map(r=>r.statusCode).sort(),[200,400]);assert.equal(await used(),3);
  assert.equal((await call('GET','/v1/referrals')).json().remainingUses,0);assert.equal((await call('POST','/v1/referrals',{})).statusCode,400);
  assert.equal((await db.query('SELECT count(*)::int AS count FROM referral_rewards WHERE inviter_id=$1',[owner.json().user.id]))[0].count,2);
  for(const change of ["expires_at=now()-interval '1 second',revoked_at=NULL","expires_at=now()+interval '1 day',revoked_at=now()"]){
   await db.query('UPDATE invitations SET max_uses=4,'+change+' WHERE id=$1',[id]);
   assert.equal((await call('POST','/v1/referrals',{})).statusCode,400);assert.equal((await call('GET','/v1/referrals')).json().remainingUses,0);
  }
 }finally{await app.close();await rm(root,{recursive:true,force:true})}
});
