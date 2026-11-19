import test from 'node:test';import assert from 'node:assert/strict';
import {mkdtemp,rm,writeFile} from 'node:fs/promises';import path from 'node:path';import os from 'node:os';
import {createApp} from '../apps/control-api/src/app.js';import {defaultConfig} from '../packages/contracts/index.js';
import {loadKey,encrypt} from '../packages/shared/security.js';import {platformSecretPurpose} from '../apps/control-api/src/platform-model.js';
import {platformModelCredential} from '../packages/shared/platform-model.js';import {issueTestInvite} from './helpers/invitations.js';

test('platform budget falls back before dispatch to this Bot personal connection without replay or credit changes',async()=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'fish-personal-fallback-')),key=await loadKey(root);
 await writeFile(path.join(root,'platform-model-secret'),encrypt(key,platformSecretPurpose,JSON.stringify({primary:'platform-primary-fixture',fallback:'platform-secondary-fixture'})));
 const calls:{url:string;body:any;auth:string|null}[]=[];let interrupt=false,block=false,entered!:()=>void,release!:()=>void;
 const barrier=new Promise<void>(r=>release=r),started=new Promise<void>(r=>entered=r);
 const {app,db}=await createApp({root,databaseUrl:'',agent:false,platformModelTransport:async(url,init)=>{
  const body=JSON.parse(String(init?.body)),auth=new Headers(init?.headers).get('Authorization');calls.push({url:String(url),body,auth});
  if(String(url).startsWith('https://crab-ai.net/')){if(interrupt)throw Error('unknown after dispatch');if(block){entered();await barrier}}
  if(body.stream)return new Response('data: '+JSON.stringify({choices:[{delta:{content:'OK'},finish_reason:'stop'}]})+'\n\ndata: [DONE]\n\n');
  return Response.json({choices:[{message:{role:'assistant',content:'OK'}}],usage:{prompt_tokens:2,completion_tokens:1,total_tokens:3}});
 }});
 try{
  async function setup(email:string,personalKey?:string){
   const invite=await issueTestInvite(db),registered=await app.inject({method:'POST',url:'/v1/auth/register',payload:{email,name:email,password:'fixture-password',inviteCode:invite}});assert.equal(registered.statusCode,200,registered.body);
   const cookie=registered.cookies.map(c=>c.name+'='+c.value).join(';'),[member]=await db.query('SELECT tenant_id FROM memberships WHERE user_id=$1',[registered.json().user.id]);
   await db.query('INSERT INTO platform_model_credits(tenant_id,granted) VALUES($1,100000)',[member.tenant_id]);
   const created=await app.inject({method:'POST',url:'/v1/bots',headers:{cookie},payload:{name:'controlled fixture',protocol:'mock',config:{...defaultConfig,platformModel:true,baseUrl:'https://personal.example/v1',model:'personal-fixture',reasoningEffort:'low'},apiKey:personalKey}});assert.equal(created.statusCode,201,created.body);
   const [bot]=await db.query('SELECT * FROM bots WHERE id=$1',[created.json().id]);
   return{bot,auth:platformModelCredential(key,bot)};
  }
  const first=await setup('first@fallback.test','first-personal-fixture'),other=await setup('other@fallback.test','other-personal-fixture'),missing=await setup('missing@fallback.test');
  await db.query('UPDATE platform_model_credits SET spent=granted');
  const payload={model:'platform',max_tokens:50,messages:[{role:'system',content:'Stable prefix'},{role:'user',content:'Synthetic input'}],tools:[{type:'function',function:{name:'echo',parameters:{type:'object'}}}],prompt_cache_key:'fixture-prefix'};
  const call=(auth=first.auth,stream=false)=>app.inject({method:'POST',url:'/v1/platform-model/chat/completions',headers:{authorization:'Bearer '+auth},payload:{...payload,stream}});
  const ledger=()=>db.query('SELECT tenant_id,granted,spent,reserved FROM platform_model_credits ORDER BY tenant_id');
  const before=await ledger(),states=await db.query('SELECT id,last_status,last_checked_at FROM platform_model_providers ORDER BY id');
  const result=await call();assert.equal(result.statusCode,200,result.body);assert.equal(result.json().model,'platform');assert.equal(calls.length,1);
  assert.equal(calls[0].url,'https://personal.example/v1/chat/completions');assert.equal(calls[0].auth,'Bearer first-personal-fixture');assert.equal(calls[0].body.model,'personal-fixture');assert.equal(calls[0].body.reasoning_effort,'low');
  assert.deepEqual(calls[0].body.messages,payload.messages);assert.deepEqual(calls[0].body.tools,payload.tools);assert.equal(calls[0].body.prompt_cache_key,payload.prompt_cache_key);assert.ok(!result.body.includes('first-personal-fixture'));
  const stream=await call(first.auth,true);assert.equal(stream.statusCode,200);assert.match(stream.body,/\[DONE\]/);
  assert.equal((await call(other.auth)).statusCode,200);assert.equal(calls.at(-1)!.auth,'Bearer other-personal-fixture');
  const count=calls.length,absent=await call(missing.auth);assert.equal(absent.statusCode,402);assert.match(absent.body,/自备模型/);assert.equal(calls.length,count);
  assert.deepEqual(await ledger(),before);assert.deepEqual(await db.query('SELECT id,last_status,last_checked_at FROM platform_model_providers ORDER BY id'),states);assert.equal((await db.query('SELECT * FROM platform_model_reservations')).length,0);
  // Enough credit for exactly one concurrent platform hold. The next genuine
  // request uses personal credit, without charging platform credit twice.
  await db.query('UPDATE platform_model_credits SET spent=0,granted=4600 WHERE tenant_id=$1',[first.bot.tenant_id]);block=true;
  const pending=call();await started;
  try{const concurrent=await call();assert.equal(concurrent.statusCode,200);assert.equal(calls.at(-1)!.auth,'Bearer first-personal-fixture')}finally{release()}
  assert.equal((await pending).statusCode,200);block=false;
  const [settled]=await db.query('SELECT spent,reserved FROM platform_model_credits WHERE tenant_id=$1',[first.bot.tenant_id]);assert.equal(Number(settled.spent),3);assert.equal(Number(settled.reserved),0);
  // A timeout after platform dispatch must NOT spend personal credit as well.
  interrupt=true;const prior=calls.length;assert.equal((await call()).statusCode,502);assert.equal(calls.length,prior+1);assert.equal(calls.at(-1)!.auth,'Bearer platform-primary-fixture');
  assert.equal((await db.query('SELECT * FROM platform_model_reservations')).length,1);
  await db.query('UPDATE bots SET revision=revision+1 WHERE id=$1',[other.bot.id]);const n=calls.length;assert.equal((await call(other.auth)).statusCode,401);assert.equal(calls.length,n);
 }finally{release();await app.close();await rm(root,{recursive:true,force:true})}
});
