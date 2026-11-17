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
 const key=await loadKey(root);
 await writeFile(path.join(root,'platform-model-secret'),encrypt(key,platformSecretPurpose,secret));
 let requests=0,release!:()=>void,started!:()=>void;
 const barrier=new Promise<void>(resolve=>{release=resolve});
 const firstRequest=new Promise<void>(resolve=>{started=resolve});
 const receipt={prompt_tokens:10,completion_tokens:5,total_tokens:15};
 const {app,db,agent}=await createApp({root,databaseUrl:'',agent:false,platformModelTransport:async(_,init)=>{
  assert.equal(new Headers(init?.headers).get('Authorization'),'Bearer '+secret);
  requests++;
  if(requests===1){started();await barrier;}
  const body=JSON.parse(init!.body as string);
  assert.notEqual(body.model,'platform');
  if(body.messages[0].content==='unknown')throw new Error('connection was lost');
  if(body.messages[0].content==='rejected')return new Response(null,{status:429});
  if(body.stream){assert.deepEqual(body.stream_options,{include_usage:true});return new Response('data: '+JSON.stringify({model:body.model,choices:[],usage:receipt})+'\n\ndata: [DONE]\n\n');}
  return Response.json({model:body.model,choices:[],usage:receipt});
 }});
 const request=(url:string,payload?:any,cookie?:string)=>app.inject({method:payload===undefined?'GET':'POST',url,headers:cookie?{cookie}:{},payload});
 try{
  const activation='fixture-invitation';
  await db.query("INSERT INTO invitations(id,code_hash,code_prefix,max_uses,expires_at,bot_limit) VALUES($1,$2,'fixture',5,now()+interval '1 day',1)",[randomUUID(),digest(activation)]);
  async function register(name:string,referralCode?:string){return request('/v1/auth/register',{name,email:name+'@example.com',password:'test-password',inviteCode:activation,referralCode})}
  const owner=await register('inviter');assert.equal(owner.statusCode,200);
  const cookie=(response:typeof owner)=>String(response.headers['set-cookie']).split(';')[0];
  const inviter=cookie(owner);
  const invitation=await request('/v1/referrals',{},inviter);assert.equal(invitation.statusCode,200);
  const {code}=invitation.json();
  assert.equal((await request('/v1/referrals',{},inviter)).json().code,code);
  assert.equal((await register('will-rollback','T'.repeat(24))).statusCode,400);
  const invited=await register('invitee',code);assert.equal(invited.statusCode,200);
  assert.equal((await register('invitee',code)).statusCode,409);
  const invitee=cookie(invited);
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
  const call=(content='normal',stream=false,credential=context.apiKey)=>app.inject({method:'POST',url:'/v1/platform-model/chat/completions',headers:{authorization:'Bearer '+credential},payload:{model:'platform',max_tokens:50,stream,messages:[{role:'user',content}]}});
  assert.equal((await call('normal',false,'untrusted')).statusCode,401);
  // Keep just enough credit for one concurrent reservation.
  await db.query('UPDATE platform_model_credits SET spent=granted-4500 WHERE tenant_id=$1',[bot.tenant_id]);
  const first=call();await firstRequest;
  try{assert.equal((await call()).statusCode,402);assert.equal(requests,1);}finally{release()}
  const done=await first;assert.equal(done.statusCode,200);assert.equal(done.json().model,'platform');
  assert.equal((await request('/v1/platform-model/balance',undefined,invitee)).json().available,4485);
  const stream=await call('stream',true);assert.equal(stream.statusCode,200);assert.match(stream.body,/\[DONE\]/);assert.match(stream.body,/"model":"platform"/);
  assert.equal((await request('/v1/platform-model/balance',undefined,invitee)).json().available,4470);
  assert.equal((await call('rejected')).statusCode,429);
  assert.equal((await request('/v1/platform-model/balance',undefined,invitee)).json().available,4470);
  assert.equal((await call('unknown')).statusCode,502);
  assert.equal(Number((await db.query('SELECT count(*) AS count FROM platform_model_reservations'))[0].count),1);
  await db.query('UPDATE bots SET generation=generation+1 WHERE id=$1',[bot.id]);
  assert.equal((await call()).statusCode,401);
 }finally{release();await app.close();await rm(root,{recursive:true,force:true});}
});
